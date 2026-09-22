"""Tier 2 — assumption-based counterfactual refutation over the real forward model.

Answers "why wasn't this edit kept" by turning it into a hard constraint and asking the
solver directly: *does a schedule exist that also honours this edit?* — without touching
any ``.lp`` file. ``preferences_weak.lp`` already derives ``pref_time_met(A,S)`` /
``pref_mower_met(A,S,M)`` as ordinary Boolean atoms from the ``pref_time``/``pref_mower``
facts a re-solve already sends; assuming one **true** is exactly the compiled-hard-
constraint form of "this edit holds", over atoms that already exist
(`docs/explainability-literature.md` §5.1, the Stage 0 go/no-go).

Three things the Stage 0 spike and the Stage 1 pilot found the hard way, all load-bearing
here:

1. **``--opt-mode=ignore`` is not optional.** The grounded program still carries the
   forward model's own service-quality weak constraints; without this flag a plain
   feasibility check silently re-optimises the whole schedule — exactly as expensive as
   the original solve, and on a hard scenario exactly as slow
   (`docs/study/explain_pilot_v1/README.md` §4b).
2. **Assume the local kept preferences first** (:func:`_is_local` — sharing a mower or an
   area with the edit). It keeps minimisation's cost bounded by local density rather than
   by the whole plan's size. It is **not** a sound pruning (ADR-0047 amendment,
   2026-09-15): an UNSAT answer over the local set is a genuine conflict, but a SAT one is
   confirmed against every kept preference before the edit is called keepable.
3. **Solve deterministically** (`service.DETERMINISTIC_CONFIG`, ADR-0041), never the
   ``-t4`` portfolio: Stage 0 measured the portfolio's raw core as unstable across fresh
   solves and the deterministic path as stable every time, and the Stage 1 pilot
   replicated that at real scale (100% stable across 5 repeats on every genuine conflict
   found).

One ``Control`` is grounded per explain call, covering every submitted preference (kept
and dropped alike); every dropped edit is then a handful of assumption-solves on that same
hot control — the "one grounding, many solves" shape multi-shot solving is for (Gebser
et al. 2019, cited in the literature note).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import clingo

from fleetplanning.model import (
    ConflictingTask,
    EditExplanation,
    PreferredTask,
    Scenario,
    SolvePreferences,
)
from fleetplanning.service import DETERMINISTIC_CONFIG, encoding_text
from fleetplanning.solver.completion_table import CompletionRow
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.preferences import render_preferences
from fleetplanning.solver.runner import ClingconSolver

#: Per-solve-call budget inside one edit's checks. Small on purpose: Stage 1 measured a
#: median refutation cost of 65ms and a worst case of 3.2s across 92 real checks once
#: --opt-mode=ignore was in place, so this is generous headroom, not a tight limit. The
#: *overall* explain deadline (passed in by the caller) is what actually protects against
#: a harder-than-piloted production scenario.
DEFAULT_STEP_BUDGET_S = 2.0


def _is_local(edit: PreferredTask, k: PreferredTask) -> bool:
    """Does kept preference ``k`` share a mower or an area with ``edit``?

    The local kept preferences are assumed *first* — a fast check, **not a sound
    pruning** (ADR-0047 amendment, 2026-09-15). The forward model's hard constraints do
    relate only two tasks sharing a mower or an area (``forward_model.lp:26-35``), but a
    conflict can run through a task that carries no preference at all: pinning the edit
    can force one of its area's *unpinned* services onto another mower, where it collides
    with a kept preference that shares nothing with the edit. So an UNSAT answer over the
    local set is a genuine conflict, while a SAT one must be confirmed against every kept
    preference before the edit may be called keepable (:func:`explain_edit`).
    """
    return k.mower == edit.mower or k.area == edit.area


@dataclass
class GroundedExplain:
    """One grounding, reused across every dropped edit in one explain call."""

    solver: ClingconSolver
    time_lit: dict[tuple[str, int], int]
    mower_lit: dict[tuple[str, int, str], int]


def ground_for_explanation(
    scenario: Scenario, rows: list[CompletionRow], preferences: SolvePreferences
) -> GroundedExplain:
    rendered = render_preferences(preferences, scenario, rows)
    solver = ClingconSolver(
        [
            "-t1",
            f"--configuration={DETERMINISTIC_CONFIG}",
            "--opt-mode=ignore",
            "-c",
            f"horizon={scenario.horizon_hours}",
            "-c",
            "pref_level=6",
        ]
    )
    solver.add(encoding_text())
    solver.add(encoding_text("preferences_weak.lp"))
    solver.add(render_instance(scenario, completion_rows=rows))
    solver.add(rendered.text)
    solver.ground()

    time_lit: dict[tuple[str, int], int] = {}
    mower_lit: dict[tuple[str, int, str], int] = {}
    for task in preferences.tasks:
        key_t = (task.area, task.start)
        if key_t not in time_lit:
            sym = clingo.Function(
                "pref_time_met", [clingo.String(task.area), clingo.Number(task.start)]
            )
            lit = solver.literal_for(sym)
            if lit is not None:
                time_lit[key_t] = lit
        if task.mower is not None:
            key_m = (task.area, task.start, task.mower)
            if key_m not in mower_lit:
                sym = clingo.Function(
                    "pref_mower_met",
                    [
                        clingo.String(task.area),
                        clingo.Number(task.start),
                        clingo.String(task.mower),
                    ],
                )
                lit = solver.literal_for(sym)
                if lit is not None:
                    mower_lit[key_m] = lit
    return GroundedExplain(solver=solver, time_lit=time_lit, mower_lit=mower_lit)


def _literals_for(pref: PreferredTask, g: GroundedExplain) -> list[int] | None:
    """Assumption literals for one preference, or ``None`` if it never grounded — an
    individually-illegal hour/mower (Stage 0 §5.1's near-miss)."""
    lits = []
    t = g.time_lit.get((pref.area, pref.start))
    if t is None:
        return None
    lits.append(t)
    if pref.mower is not None:
        m = g.mower_lit.get((pref.area, pref.start, pref.mower))
        if m is None:
            return None
        lits.append(m)
    return lits


def _minimise(
    g: GroundedExplain,
    edit_lits: list[int],
    kept_by_name: dict[str, list[int]],
    *,
    step_budget_s: float,
    deadline: float,
) -> tuple[set[str], bool, float]:
    """Deletion-based minimisation, holding the edit fixed. An assumption is only removed
    once UNSAT is *proven* without it, so running out of ``deadline`` partway through can
    only leave the returned set larger than the true minimum, never wrong — the
    keep-on-timeout rule.
    """
    current = dict(kept_by_name)
    hit_budget = False
    total = 0.0
    changed = True
    while changed:
        changed = False
        for name in list(current):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return set(current), True, total
            trial = edit_lits + [lit for n, lits in current.items() if n != name for lit in lits]
            result = g.solver.solve_under(trial, time_limit_s=min(step_budget_s, remaining))
            total += result.elapsed_s
            if result.status == "unknown":
                hit_budget = True
                continue
            if result.status == "unsatisfiable":
                del current[name]
                changed = True
                break
    return set(current), hit_budget, total


def explain_edit(
    edit: PreferredTask,
    kept: Sequence[PreferredTask],
    g: GroundedExplain,
    *,
    deadline: float,
    step_budget_s: float = DEFAULT_STEP_BUDGET_S,
) -> EditExplanation:
    """Explain one dropped edit against the plan on screen. Never re-solves; only ever
    asks feasibility questions on the shared ``GroundedExplain`` control.
    """
    base = dict(area=edit.area, start=edit.start, mower=edit.mower)

    edit_lits = _literals_for(edit, g)
    if edit_lits is None:
        return EditExplanation(
            outcome="individually_impossible",
            detail="no legal (area, mower, start) for this edit",
            **base,
        )

    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return EditExplanation(
            outcome="not_determined", detail="explain budget exhausted", **base
        )
    alone = g.solver.solve_under(edit_lits, time_limit_s=min(step_budget_s, remaining))
    if alone.status == "unsatisfiable":
        return EditExplanation(
            outcome="individually_impossible",
            detail="no schedule could ever place this edit",
            solve_time_s=alone.elapsed_s,
            **base,
        )
    if alone.status == "unknown":
        return EditExplanation(
            outcome="not_determined",
            detail="budget exhausted checking this edit alone",
            solve_time_s=alone.elapsed_s,
            **base,
        )

    kept_lits_by_name: dict[str, list[int]] = {}
    kept_by_name: dict[str, PreferredTask] = {}
    for i, k in enumerate(kept):
        lits = _literals_for(k, g)
        if lits is not None:
            name = f"kept{i}"
            kept_lits_by_name[name] = lits
            kept_by_name[name] = k
    local = [n for n, k in kept_by_name.items() if _is_local(edit, k)]

    def assumptions(names: Sequence[str]) -> list[int]:
        return edit_lits + [lit for n in names for lit in kept_lits_by_name[n]]

    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return EditExplanation(
            outcome="not_determined",
            detail="explain budget exhausted",
            solve_time_s=alone.elapsed_s,
            **base,
        )
    full = g.solver.solve_under(assumptions(local), time_limit_s=min(step_budget_s, remaining))
    total_so_far = alone.elapsed_s + full.elapsed_s
    scope = local
    if full.status == "satisfiable" and len(local) < len(kept_lits_by_name):
        # The local check alone cannot call the edit keepable: a conflict may run through
        # an unpinned task to a kept preference outside it (_is_local). Confirm against
        # every kept preference; if that is UNSAT, explain over all of them.
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return EditExplanation(
                outcome="not_determined",
                detail="explain budget exhausted",
                solve_time_s=total_so_far,
                **base,
            )
        scope = list(kept_lits_by_name)
        full = g.solver.solve_under(
            assumptions(scope), time_limit_s=min(step_budget_s, remaining)
        )
        total_so_far += full.elapsed_s
    if full.status == "satisfiable":
        # Provably only reachable when the parent solve was not proven optimal — see
        # EditExplanation's docstring for why "optimal parent + SAT here" is a
        # contradiction, not merely rare.
        return EditExplanation(
            outcome="not_yet_found",
            detail="a schedule keeping this edit alongside everything you kept does "
            "exist — the earlier search had not found it",
            solve_time_s=total_so_far,
            **base,
        )
    if full.status == "unknown":
        return EditExplanation(
            outcome="not_determined",
            detail="budget exhausted on the full check",
            solve_time_s=total_so_far,
            **base,
        )

    minimal_names, hit_budget, min_time = _minimise(
        g,
        edit_lits,
        {n: kept_lits_by_name[n] for n in scope},
        step_budget_s=step_budget_s,
        deadline=deadline,
    )
    conflicts = [
        ConflictingTask(
            area=kept_by_name[n].area, start=kept_by_name[n].start, mower=kept_by_name[n].mower
        )
        for n in sorted(minimal_names)
    ]
    return EditExplanation(
        outcome="conflicts_with",
        detail=f"conflicts with {len(conflicts)} kept task(s)",
        conflicts=conflicts,
        minimal=not hit_budget,
        solve_time_s=total_so_far + min_time,
        **base,
    )
