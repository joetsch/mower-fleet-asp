"""`explain-study` — Iteration 6 Stage 1 pilot: does assumption-based counterfactual
refutation, over the real forward model, answer "why wasn't my edit kept" affordably?

Mirrors ``generator/pref_study.py`` deliberately: same curated-scenario + seeded-RNG
shape, same "reuse the product's own primitives, add only what does not exist yet"
posture. What does not exist yet is assumption solving itself — no production code
touches ``clingo.Control(assumptions=...)`` — so this module builds it standalone,
Stage-0-spike-style, rather than reaching into ``solver/runner.py``. That is
deliberate: which primitive Stage 2 adds to ``ClingconSolver`` should be decided by
what this pilot measures, not guessed at first.

One pilot cell is one scenario × one seed:

1. Solve cold (product default: ``-t4 --configuration=many``, 20 s) to get the
   **reference plan** — what the greenkeeper is looking at before editing anything.
2. **perturb-k** (ADR-0033's dropped edit set, revived here — it is "the only shape
   that puts a user edit in genuine conflict with the objective", which is exactly
   what an explanation feature needs to have something to explain): move
   ``max(1, round(f * n))`` sampled tasks to a different legal ``(start, mower)``,
   origin ``"edited"``; every other task travels as origin ``"frozen"`` — precisely
   what ``toPreferences(planTasks, "top")`` sends on a plain Re-solve.
3. Re-solve at ``weak@top`` (the shipping default) via ``service.solve_scenario`` —
   the actual product path, not a hand-rolled equivalent.
4. Classify every submitted preference kept/dropped by the same set-membership test
   ``solver/preferences.py::preference_agreement`` uses.
5. For every **dropped edited task**, explain it:
   a. **Tier 1** (static, no solver): does its ``(mower, [start, end))`` interval
      overlap a *kept* task's interval on the same mower or the same area? If so,
      that is the whole explanation, and it is free.
   b. **Tier 2** (assumption-based counterfactual): ground once per scenario with
      ``pref_time``/``pref_mower`` facts for *every* submitted preference (kept and
      dropped alike — one grounding serves every dropped task's explanation), then
      per dropped task assume it plus every kept preference true and refute under
      ``-t1 --configuration=jumpy`` (Stage 0 measured this deterministic; the
      portfolio is not). If UNSAT, minimise the core by deletion, budgeted per step
      with the keep-on-timeout rule, then repeat the refutation a few times to
      confirm the minimised core is stable.

Writes ``docs/study/explain_pilot_v1/{summary.csv,meta.json}``; this file's own
docstring plus the README next to it *is* the write-up, per the roll/pref-pilot
precedent — there is no separate ``analyse.py`` because every reportable number is
already a column.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import clingo
from clingcon import ClingconTheory
from clingo.ast import ProgramBuilder, parse_string
from pydantic import BaseModel

from fleetplanning.generator.runinfo import tool_versions as _tool_versions
from fleetplanning.model import PreferredTask, Scenario, ScheduledTask, SolvePreferences
from fleetplanning.scenarios import registry
from fleetplanning.service import DETERMINISTIC_CONFIG, encoding_text, solve_scenario
from fleetplanning.solver.completion_table import CompletionRow, build_completion_table
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.preferences import legal_starts, render_preferences

# --------------------------------------------------------------------------- perturb-k


def _rng(base_seed: int, scenario_id: str, seed_tag: str) -> random.Random:
    return random.Random(f"{base_seed}:{scenario_id}:{seed_tag}")


def perturb_edit_set(
    reference: Sequence[ScheduledTask],
    fraction: float,
    rng: random.Random,
    starts_by_area: dict[str, set[int]],
    starts_by_pair: dict[tuple[str, str], set[int]],
) -> list[PreferredTask]:
    """ADR-0033's dropped **perturb-k**: sample ``max(1, round(f·n))`` reference tasks and
    move each to a different legal start (preferred) or, failing that, a different legal
    mower at the same start. Origin ``"edited"`` for the moved ones, ``"frozen"`` for
    everyone else — the whole plan, exactly what a plain Re-solve sends.
    """
    n = len(reference)
    k = max(1, round(fraction * n))
    moved_idx = set(rng.sample(range(n), min(k, n)))

    out: list[PreferredTask] = []
    for i, task in enumerate(reference):
        if i not in moved_idx:
            out.append(
                PreferredTask(area=task.area, start=task.start, mower=task.mower, origin="frozen")
            )
            continue

        alt_starts = sorted(starts_by_area.get(task.area, set()) - {task.start})
        if alt_starts:
            new_start = rng.choice(alt_starts)
            capable_here = sorted(
                m
                for (a, m), starts in starts_by_pair.items()
                if a == task.area and new_start in starts
            )
            new_mower = rng.choice(capable_here) if capable_here else task.mower
        else:
            # No alternative hour at all for this area — fall back to a mower swap at
            # the same hour, so the sampled task is still an edit, not a no-op.
            new_start = task.start
            capable_here = sorted(
                m
                for (a, m), starts in starts_by_pair.items()
                if a == task.area and new_start in starts and m != task.mower
            )
            new_mower = rng.choice(capable_here) if capable_here else task.mower

        out.append(PreferredTask(area=task.area, start=new_start, mower=new_mower, origin="edited"))
    return out


# --------------------------------------------------------------------------- tier 1 (static)


def static_conflict(
    edit: PreferredTask,
    kept: Sequence[PreferredTask],
    rows: Sequence[CompletionRow],
) -> str | None:
    """A free, solver-free reason, or ``None`` if this edit needs the counterfactual.

    Checks the edit's ``[start, end)`` interval (looked up in the completion table —
    the exact ``&dom`` domain the encoding grounds) against every *kept* preference's own
    interval, on the same mower (the encoding's cross-task no-overlap constraint) or the
    same area (the encoding's same-area precedence constraint). Both are exactly what the
    forward model's four hard constraints check (``forward_model.lp:26-35``) — this
    reproduces two of the four in Python, over the preference set rather than the solved
    schedule, so it costs nothing and needs no solver.
    """
    if edit.mower is None:
        return None  # a time-only edit's mower is the solver's choice; nothing to check yet

    def _row(area: str, mower: str, start: int) -> CompletionRow | None:
        return next(
            (r for r in rows if r.area == area and r.mower == mower and r.start == start), None
        )

    edit_row = _row(edit.area, edit.mower, edit.start)
    if edit_row is None:
        return "no legal completion row for this (area, mower, start)"
    edit_end = edit_row.completion

    for other in kept:
        if other.mower is None:
            continue
        same_mower = other.mower == edit.mower
        same_area = other.area == edit.area
        if not (same_mower or same_area):
            continue
        other_row = _row(other.area, other.mower, other.start)
        if other_row is None:
            continue
        overlap = edit.start < other_row.completion and other.start < edit_end
        if overlap and same_mower:
            return f"mower {edit.mower!r} already serving {other.area!r} at that hour"
        if overlap and same_area:
            return f"overlaps this area's own kept task at hour {other.start}"
    return None


# --------------------------------------------------------------------------- tier 2


@dataclass
class GroundedExplain:
    ctl: clingo.Control
    time_lit: dict[tuple[str, int], int]
    mower_lit: dict[tuple[str, int, str], int]


def ground_for_explanation(
    scenario: Scenario,
    rows: list[CompletionRow],
    preferences: SolvePreferences,
    opt_mode: str = "ignore",
) -> GroundedExplain:
    """One grounding per scenario, covering every submitted preference (kept and dropped
    alike) — Stage 0's "one grounding, many solves" design, applied to a whole pilot cell
    rather than one hand-built example.

    Solved deterministically (``-t1 --configuration=jumpy``, ``service.DETERMINISTIC_CONFIG``,
    ADR-0041) — Stage 0 measured the ``-t4`` portfolio's raw core as unstable across fresh
    solves and the deterministic path as stable every time (§5.4).

    ``--opt-mode=ignore`` is load-bearing, not an optimisation: the forward model's own
    weak constraints (service quality) are still in this program, and a plain ``solve()``
    would keep searching for a *provably optimal* model under the assumptions — exactly as
    expensive as the original solve, and on the two scenarios that do not prove optimality
    within budget in the first place, exactly as slow. The counterfactual only ever asks a
    yes/no feasibility question; any model is a sufficient witness, so optimisation is
    switched off entirely. Found by measurement, not foresight — see the pilot README.
    ``opt_mode`` exists only to re-measure that (``"opt"`` is clingo's default).
    """
    rendered = render_preferences(preferences, scenario, rows)
    programs = [
        encoding_text(),
        encoding_text("preferences_weak.lp"),
        render_instance(scenario, completion_rows=rows),
        rendered.text,
    ]
    ctl = clingo.Control(
        [
            "-t1",
            f"--configuration={DETERMINISTIC_CONFIG}",
            f"--opt-mode={opt_mode}",
            "-c",
            f"horizon={scenario.horizon_hours}",
            "-c",
            "pref_level=6",
        ]
    )
    theory = ClingconTheory()
    theory.register(ctl)
    with ProgramBuilder(ctl) as b:
        for p in programs:
            parse_string(p, lambda ast: theory.rewrite_ast(ast, b.add))
    ctl.ground([("base", [])])
    theory.prepare(ctl)

    time_lit: dict[tuple[str, int], int] = {}
    mower_lit: dict[tuple[str, int, str], int] = {}
    for task in preferences.tasks:
        key_t = (task.area, task.start)
        if key_t not in time_lit:
            sym = clingo.Function(
                "pref_time_met", [clingo.String(task.area), clingo.Number(task.start)]
            )
            atom = ctl.symbolic_atoms[sym]
            if atom is not None:
                time_lit[key_t] = atom.literal
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
                atom = ctl.symbolic_atoms[sym]
                if atom is not None:
                    mower_lit[key_m] = atom.literal
    return GroundedExplain(ctl=ctl, time_lit=time_lit, mower_lit=mower_lit)


def _literals_for(pref: PreferredTask, g: GroundedExplain) -> list[int] | None:
    """The assumption literals standing for one preference, or ``None`` if it did not
    survive grounding (tier-1's static reciprocal: an individually-illegal hour/mower —
    Stage 0 §5.1's near-miss, generalised into a real guard here)."""
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


def refute(
    ctl: clingo.Control, assumption_lits: list[int], time_limit_s: float
) -> tuple[str, float]:
    """One assumption-solve. Returns ``(status, elapsed_s)`` — ``status`` is
    ``"unsat" | "sat" | "unknown"``."""
    started = time.perf_counter()
    handle = ctl.solve(assumptions=assumption_lits, async_=True)
    completed = handle.wait(time_limit_s)
    if not completed:
        handle.cancel()
        handle.get()
        return "unknown", time.perf_counter() - started
    res = handle.get()
    elapsed = time.perf_counter() - started
    if res.unsatisfiable:
        return "unsat", elapsed
    if res.satisfiable:
        return "sat", elapsed
    return "unknown", elapsed


def minimise_core(
    ctl: clingo.Control,
    edit_lits: list[int],
    kept_by_name: dict[str, list[int]],
    step_budget_s: float,
    max_total_s: float,
) -> tuple[set[str], int, float, bool]:
    """Deletion-based minimisation of the "kept" side, holding the edit fixed.

    An assumption is only removed once UNSAT is *proven* without it, so a per-step
    timeout can only leave the core larger, never wrong (the keep-on-timeout rule).
    ``max_total_s`` is the same rule applied to the whole loop, not just one step: on a
    kept set too large to fully minimise in budget, stop and report what is left as the
    (non-minimal, but still valid) core — never claim minimality that was not proven.
    Returns ``(minimal_kept_names, steps_taken, total_time_s, hit_budget)``.
    """
    current = dict(kept_by_name)
    steps = 0
    total = 0.0
    hit_budget = False
    changed = True
    while changed and total < max_total_s:
        changed = False
        for name in list(current):
            trial_lits = edit_lits + [
                lit for n, lits in current.items() if n != name for lit in lits
            ]
            status, elapsed = refute(ctl, trial_lits, step_budget_s)
            steps += 1
            total += elapsed
            if status == "unknown":
                hit_budget = True
                continue  # keep it — not proven redundant
            if status == "unsat":
                del current[name]
                changed = True
                break
            if total >= max_total_s:
                hit_budget = True
                break
    return set(current), steps, total, hit_budget


# --------------------------------------------------------------------------- one edit's record


@dataclass
class ExplainRecord:
    scenario: str
    seed_tag: str
    area: str
    requested_start: int
    requested_mower: str | None
    outcome: str
    detail: str
    n_kept_total: int
    # Was the *parent* re-solve (the one that produced this dropped edit) proven optimal?
    # Load-bearing for reading `free_but_untaken`: at optimal, a free-but-untaken edit is a
    # genuine tie between equally-good schedules; short of optimal, it may just mean the
    # search never got there — "give it more time", not "the choice was arbitrary".
    parent_optimal: bool = False
    n_kept_relevant: int = 0
    refute_status: str | None = None
    refute_time_s: float | None = None
    raw_core_size: int | None = None
    min_core_size: int | None = None
    min_steps: int = 0
    min_time_s: float = 0.0
    min_hit_budget: bool = False
    stability_repeats: int = 0
    stable: bool | None = None
    # The local (pruned) check was SAT but the check against every kept preference was
    # UNSAT: a conflict only a pruned-away preference completes (ADR-0047 amendment).
    pruning_missed: bool = False


def _relevant(edit: PreferredTask, kept: Sequence[PreferredTask]) -> list[PreferredTask]:
    """Kept preferences sharing a mower or an area with the edit — a fast *first* check,
    not a sound pruning. The forward model's hard constraints relate two tasks sharing a
    mower or an area, but a conflict can run through a task that carries no preference,
    so it can involve a kept preference sharing neither with the edit (ADR-0047
    amendment, 2026-09-15). An UNSAT answer over this subset is still genuine; a SAT one
    is confirmed against every kept preference in :func:`explain_dropped_edit`. Keeping
    the first check local is what bounds minimisation's cost by local density rather
    than the whole plan's size (see the README's "what went wrong first" note).
    """
    return [k for k in kept if k.mower == edit.mower or k.area == edit.area]


def explain_dropped_edit(
    edit: PreferredTask,
    kept: Sequence[PreferredTask],
    g: GroundedExplain,
    *,
    refute_time_limit_s: float,
    step_budget_s: float,
    max_minimise_total_s: float,
    stability_repeats: int,
) -> ExplainRecord:
    relevant = _relevant(edit, kept)
    base = dict(
        scenario="",  # filled by caller
        seed_tag="",
        area=edit.area,
        requested_start=edit.start,
        requested_mower=edit.mower,
        n_kept_total=len(kept),
        n_kept_relevant=len(relevant),
    )

    edit_lits = _literals_for(edit, g)
    if edit_lits is None:
        return ExplainRecord(
            outcome="individually_impossible", detail="no legal (area,mower,start)", **base
        )

    # individually possible on its own?
    status, elapsed = refute(g.ctl, edit_lits, refute_time_limit_s)
    if status == "unsat":
        return ExplainRecord(
            outcome="individually_impossible", detail="UNSAT even alone",
            refute_status=status, refute_time_s=elapsed, **base,
        )
    if status == "unknown":
        return ExplainRecord(
            outcome="not_determined", detail="budget exhausted on the individual check",
            refute_status=status, refute_time_s=elapsed, **base,
        )

    # assume the edit plus every *relevant* kept preference (see _relevant)
    kept_lits_by_name: dict[str, list[int]] = {}
    for i, k in enumerate(relevant):
        lits = _literals_for(k, g)
        if lits is not None:
            kept_lits_by_name[f"kept{i}:{k.area}@{k.start}"] = lits
    all_lits = edit_lits + [lit for lits in kept_lits_by_name.values() for lit in lits]

    status, elapsed = refute(g.ctl, all_lits, refute_time_limit_s)
    pruning_missed = False
    if status == "sat" and len(relevant) < len(kept):
        # The local check alone cannot claim "could have been kept" (see _relevant):
        # confirm against every kept preference, and explain over all of them if needed.
        every_kept: dict[str, list[int]] = {}
        for i, k in enumerate(kept):
            lits = _literals_for(k, g)
            if lits is not None:
                every_kept[f"kept{i}:{k.area}@{k.start}"] = lits
        status, confirm_elapsed = refute(
            g.ctl, edit_lits + [lit for lits in every_kept.values() for lit in lits],
            refute_time_limit_s,
        )
        elapsed += confirm_elapsed
        if status == "unsat":
            pruning_missed = True
            kept_lits_by_name = every_kept
    if status == "sat":
        return ExplainRecord(
            outcome="free_but_untaken", detail="could have been kept alongside everything else",
            refute_status=status, refute_time_s=elapsed, **base,
        )
    if status == "unknown":
        return ExplainRecord(
            outcome="not_determined", detail="budget exhausted on the full refutation",
            refute_status=status, refute_time_s=elapsed, **base,
        )

    raw_core_size = len(kept_lits_by_name) + 1  # not read from handle.core() here: we already
    # know it's UNSAT and want the *minimised* core's size, which subsumes it; recorded as the
    # theoretical raw maximum for the "raw vs minimised" comparison the README reports.

    min_kept, steps, min_time, hit_budget = minimise_core(
        g.ctl, edit_lits, kept_lits_by_name, step_budget_s, max_minimise_total_s
    )

    stable = None
    if stability_repeats > 0:
        cores_seen = set()
        for _ in range(stability_repeats):
            trial_lits = edit_lits + [
                lit for n, lits in kept_lits_by_name.items() if n in min_kept for lit in lits
            ]
            s, _ = refute(g.ctl, trial_lits, refute_time_limit_s)
            cores_seen.add(s)
        stable = cores_seen == {"unsat"}

    return ExplainRecord(
        outcome="conflicts_with",
        detail=f"conflicts with {len(min_kept)} kept task(s)",
        refute_status="unsat",
        refute_time_s=elapsed,
        raw_core_size=raw_core_size,
        min_core_size=len(min_kept),
        min_steps=steps,
        min_time_s=min_time,
        min_hit_budget=hit_budget,
        stability_repeats=stability_repeats,
        stable=stable,
        pruning_missed=pruning_missed,
        **base,
    )


# --------------------------------------------------------------------------- cells


class Cell(BaseModel):
    """One (scenario, seed): what the explanation phase needs, recorded so it can be replayed.

    The two product solves that produce it run on the ``-t4`` portfolio under a time limit
    and are not reproducible; which edits end up dropped differs run to run. Everything
    after them — grounding, checks, minimisation — is single-threaded and deterministic
    up to its time budgets, so replaying a recorded cell reproduces its explanations.
    """

    scenario: str
    scenario_sha256: str
    seed_tag: str
    preferences: SolvePreferences
    replan_tasks: list[ScheduledTask]
    replan_optimal: bool


class CellFile(BaseModel):
    """``cells.json``: every cell with a re-plan, plus the (scenario, seed) pairs that had none."""

    cells: list[Cell]
    skipped: list[str]


def _scenario_sha256(scenario: Scenario) -> str:
    return hashlib.sha256(scenario.model_dump_json().encode("utf-8")).hexdigest()


def plan_cells(
    slug: str, *, base_seed: int, fraction: float, n_seeds: int, time_limit_s: float
) -> tuple[list[Cell], list[str]]:
    """Reference solve, ``n_seeds`` perturbed edit sets, one ``weak@top`` re-plan each."""
    scenario, _ = registry.load(slug)
    rows = build_completion_table(scenario)
    starts_by_area, starts_by_pair = legal_starts(scenario, rows)

    reference = solve_scenario(scenario, time_limit_s=time_limit_s, threads=4)
    if not reference.solved or reference.schedule is None:
        return [], [f"{slug}: no reference plan within {time_limit_s:g} s"]

    cells: list[Cell] = []
    skipped: list[str] = []
    for seed_i in range(n_seeds):
        tag = f"seed{seed_i}"
        rng = _rng(base_seed, slug, tag)
        edits = perturb_edit_set(
            reference.schedule.tasks, fraction, rng, starts_by_area, starts_by_pair
        )
        preferences = SolvePreferences(tasks=edits, mode="weak", level="top")
        result = solve_scenario(
            scenario, time_limit_s=time_limit_s, threads=4, preferences=preferences
        )
        if not result.solved or result.schedule is None:
            skipped.append(f"{slug}/{tag}: no re-plan within {time_limit_s:g} s")
            continue
        cells.append(
            Cell(
                scenario=slug, scenario_sha256=_scenario_sha256(scenario), seed_tag=tag,
                preferences=preferences, replan_tasks=result.schedule.tasks,
                replan_optimal=result.optimal,
            )
        )
    return cells, skipped


def explain_cell(
    cell: Cell,
    *,
    refute_time_limit_s: float,
    step_budget_s: float,
    max_minimise_total_s: float,
    stability_repeats: int,
    opt_mode: str = "ignore",
) -> tuple[list[ExplainRecord], float]:
    """Explain every dropped edited task of one cell. Returns the records and the wall
    time of the explanation phase (grounding + checks)."""
    scenario, _ = registry.load(cell.scenario)
    if _scenario_sha256(scenario) != cell.scenario_sha256:
        raise ValueError(
            f"scenario {cell.scenario!r} changed since this cell was recorded — "
            "a replay would explain a different problem"
        )
    rows = build_completion_table(scenario)
    preferences = cell.preferences
    kept_starts = {(t.area, t.start) for t in cell.replan_tasks}
    kept = [p for p in preferences.tasks if (p.area, p.start) in kept_starts]
    dropped_edited = [
        p
        for p in preferences.tasks
        if p.origin == "edited" and (p.area, p.start) not in kept_starts
    ]
    if not dropped_edited:
        return [], 0.0

    records: list[ExplainRecord] = []
    started = time.perf_counter()
    g = ground_for_explanation(scenario, rows, preferences, opt_mode)
    for edit in dropped_edited:
        tier1 = static_conflict(edit, kept, rows)
        if tier1 is not None:
            records.append(
                ExplainRecord(
                    scenario=cell.scenario, seed_tag=cell.seed_tag, area=edit.area,
                    requested_start=edit.start, requested_mower=edit.mower,
                    outcome="blocked_statically", detail=tier1, n_kept_total=len(kept),
                    parent_optimal=cell.replan_optimal,
                )
            )
            continue
        rec = explain_dropped_edit(
            edit, kept, g,
            refute_time_limit_s=refute_time_limit_s,
            step_budget_s=step_budget_s,
            max_minimise_total_s=max_minimise_total_s,
            stability_repeats=stability_repeats,
        )
        rec.scenario = cell.scenario
        rec.seed_tag = cell.seed_tag
        rec.parent_optimal = cell.replan_optimal
        records.append(rec)
    return records, time.perf_counter() - started


# --------------------------------------------------------------------------- top-level


def run_explain_pilot(
    scenarios: Sequence[str],
    out: Path,
    *,
    base_seed: int = 1,
    fraction: float = 0.4,
    n_seeds: int = 3,
    time_limit_s: float = 20.0,
    refute_time_limit_s: float = 10.0,
    step_budget_s: float = 5.0,
    max_minimise_total_s: float = 30.0,
    stability_repeats: int = 5,
    opt_mode: str = "ignore",
    replay: Path | None = None,
) -> Path:
    """Plan fresh cells (and record them to ``out/cells.json``), or ``replay`` a recorded
    ``cells.json`` — then explain every cell of the requested ``scenarios``."""
    out.mkdir(parents=True, exist_ok=True)
    if replay is not None:
        recorded = CellFile.model_validate_json(replay.read_text(encoding="utf-8"))
        cells = [c for c in recorded.cells if c.scenario in scenarios]
        skipped = [s for s in recorded.skipped if s.split("/")[0].split(":")[0] in scenarios]
    else:
        cells, skipped = [], []
        for slug in scenarios:
            planned, missed = plan_cells(
                slug, base_seed=base_seed, fraction=fraction, n_seeds=n_seeds,
                time_limit_s=time_limit_s,
            )
            cells += planned
            skipped += missed
        (out / "cells.json").write_text(
            CellFile(cells=cells, skipped=skipped).model_dump_json(indent=1) + "\n",
            encoding="utf-8",
        )

    all_records: list[ExplainRecord] = []
    explain_s: dict[str, float] = {}
    for cell in cells:
        records, elapsed = explain_cell(
            cell,
            refute_time_limit_s=refute_time_limit_s,
            step_budget_s=step_budget_s,
            max_minimise_total_s=max_minimise_total_s,
            stability_repeats=stability_repeats,
            opt_mode=opt_mode,
        )
        all_records += records
        explain_s[cell.scenario] = round(explain_s.get(cell.scenario, 0.0) + elapsed, 1)

    fieldnames = list(ExplainRecord.__dataclass_fields__.keys())
    with (out / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_records:
            writer.writerow({k: getattr(r, k) for k in fieldnames})

    meta = {
        "scenarios": list(scenarios),
        "cells": "replayed from " + replay.name if replay is not None else "planned (cells.json)",
        "base_seed": base_seed,
        "fraction": fraction,
        "n_seeds": n_seeds,
        "time_limit_s": time_limit_s,
        "refute_time_limit_s": refute_time_limit_s,
        "step_budget_s": step_budget_s,
        "max_minimise_total_s": max_minimise_total_s,
        "stability_repeats": stability_repeats,
        "opt_mode": opt_mode,
        "skipped": skipped,
        # Grounding + checks only, i.e. without the reference and re-plan solves.
        "explain_s_by_scenario": explain_s,
        "n_records": len(all_records),
        "generated_at": datetime.now(UTC).isoformat(),
        "host": platform.platform(),
        "tool_versions": _tool_versions(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return out
