"""Explainability (Iteration 6) — why a schedule edit was not kept.

Three independent, cheapest-first mechanisms, orchestrated by :func:`explain_scenario`
below:

- :mod:`.static` — tier 1. A plain interval-overlap check against the plan already on
  screen. No solver call; resolves close to half of real dropped edits in the Stage 1
  pilot (`docs/study/explain_pilot_v1/README.md`).
- :mod:`.counterfactual` — tier 2. Assumption-based refutation over the real forward
  model, only for edits tier 1 could not already explain.
- :mod:`.reinstated` — a released task the area's own service-count minimum brought
  back. Needs no dropped edit at all — it is invisible to the kept/dropped count, since
  there is no negative preference (ADR-0035 decision 3).
- :mod:`.ripple` — an untouched task that still moved, attributed to sharing a mower or
  area with one of the user's edits.

No module here ever re-solves. Everything is computed from the submitted
``SolvePreferences`` and the resulting ``Schedule`` a caller already has in hand — a
second solve under the ``-t4`` portfolio could return a different equally-optimal plan
(ADR-0007), which would make an explanation describe a schedule the user is not looking
at. :func:`explain_scenario` is the orchestrator; ``api/app.py`` calls it directly (this
package depends on ``service.py`` for :data:`~fleetplanning.service.DETERMINISTIC_CONFIG`
and :func:`~fleetplanning.service.encoding_text`, so the orchestrator lives here rather
than there, to avoid a circular import).
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from fleetplanning.explain.counterfactual import explain_edit, ground_for_explanation
from fleetplanning.explain.reinstated import reinstated_services
from fleetplanning.explain.ripple import ripple_moves
from fleetplanning.explain.static import static_conflict
from fleetplanning.model import (
    EditExplanation,
    ExplanationReport,
    PreferredTask,
    Scenario,
    Schedule,
    ScheduledTask,
    SolvePreferences,
)
from fleetplanning.solver.completion_table import build_completion_table

#: The default overall explain deadline (expert-mode-adjustable, mirroring
#: ``SolveRequest.time_limit_s``). Comfortable headroom over the Stage 1 pilot's observed
#: worst case (7.6s to explain every dropped edit from one re-solve in a single batch,
#: `docs/study/explain_pilot_v1/README.md` §5) — past it, any edit not yet explained comes
#: back ``not_determined`` rather than blocking further.
DEFAULT_EXPLAIN_BUDGET_S = 8.0


def plan_consistent_kept(
    tasks: Sequence[PreferredTask], scheduled: Sequence[ScheduledTask]
) -> list[PreferredTask]:
    """The kept preferences, re-expressed as the plan actually realises them.

    Kept = the plan has a service for that area at that hour (a preference names a
    position, not a task — ADR-0031). The mower is the separately-scored second half and
    may differ from the requested one; this returns the *scheduled* mower, so every
    assumption built from the result is one the plan itself witnesses.

    Shared with ``generator/explain_study.py`` on purpose: that module is otherwise a
    standalone copy of this logic, and the audit found the same defect living in both
    copies because only one had been looked at.
    """
    by_position = {(t.area, t.start): t.mower for t in scheduled}
    out = []
    for pref in tasks:
        mower = by_position.get((pref.area, pref.start))
        if mower is None:
            continue
        out.append(pref if pref.mower == mower else pref.model_copy(update={"mower": mower}))
    return out


def explain_scenario(
    scenario: Scenario,
    preferences: SolvePreferences,
    schedule: Schedule,
    *,
    budget_s: float = DEFAULT_EXPLAIN_BUDGET_S,
    released: dict[str, int] | None = None,
) -> ExplanationReport:
    """Explain one solve result relative to what the user asked for.

    ``preferences`` is the payload that produced ``schedule`` — the same
    ``SolvePreferences`` sent to ``POST /api/solve``. Tier 1 (free) runs for every dropped
    edit first; tier 2 (the counterfactual) only for what tier 1 could not already
    explain, sharing one grounding across all of them. ``budget_s`` is a single overall
    wall-clock deadline enforced across every edit, not a per-edit one — the whole call
    degrades honestly to ``not_determined`` rather than summing per-edit budgets past what
    the caller asked to wait.

    ``released`` (area name -> count) is never derivable from ``preferences`` alone — a
    release is the *absence* of a task from the payload, and the backend has no other
    record of it. It comes from the frontend, which is the only side that knows what was
    released, and it is what lets :func:`.reinstated.reinstated_services` compare against
    the plan before the re-solve rather than the payload alone (2026-09-24 amendment).
    """
    deadline = time.perf_counter() + budget_s

    reinstated = reinstated_services(scenario, preferences.tasks, schedule, released=released)
    ripple = ripple_moves(preferences.tasks, schedule)

    kept_starts = {(t.area, t.start) for t in schedule.tasks}
    kept = plan_consistent_kept(preferences.tasks, schedule.tasks)
    dropped = [
        p
        for p in preferences.tasks
        if p.origin in ("edited", "added") and (p.area, p.start) not in kept_starts
    ]

    edits: list[EditExplanation] = []
    if dropped:
        rows = build_completion_table(scenario)
        # Ground against the kept set *as the plan realises it* plus the edits as
        # requested, so every kept assumption is one the plan itself satisfies.
        grounding_payload = preferences.model_copy(update={"tasks": [*kept, *dropped]})
        needs_tier2 = []
        for edit in dropped:
            tier1 = static_conflict(edit, kept, rows)
            if tier1 is None:
                needs_tier2.append(edit)
                continue
            edits.append(
                EditExplanation(
                    area=edit.area,
                    start=edit.start,
                    mower=edit.mower,
                    outcome="blocked_statically",
                    detail=tier1.detail,
                    conflicts=tier1.conflicts,
                )
            )

        if needs_tier2:
            g = ground_for_explanation(scenario, rows, grounding_payload)
            for edit in needs_tier2:
                edits.append(explain_edit(edit, kept, g, deadline=deadline))

    return ExplanationReport(
        edits=edits,
        reinstated=reinstated,
        ripple=ripple,
        budget_s=budget_s,
        budget_exhausted=any(e.outcome == "not_determined" for e in edits),
    )
