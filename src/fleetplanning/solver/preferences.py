"""User schedule edits -> clingcon preference facts (ADR-0031, ADR-0032).

The greenkeeper edits a solved schedule and presses re-solve. Their edits reach the solver
as two fact predicates, consumed by whichever preference overlay encoding is active:

======================  =========================================================
``pref_time/2``         ``pref_time(A, S)`` — area A should have a task starting at S
``pref_mower/3``        ``pref_mower(A, S, M)`` — and that task should be on mower M
======================  =========================================================

**Two facts, not one.** The hour and the mower are scored independently, so keeping the
hour while swapping the mower is a partial success rather than a total loss (ADR-0031).
A ``PreferredTask`` with no mower — "keep the time, any mower" — simply emits no
``pref_mower`` fact.

**No task index appears.** The encoding numbers an area's tasks 1..N in time order and
``num_starts(A,N)`` is a choice, so an index-based preference would silently re-target
exactly when a re-solve changes the count. A preference names a position, not a task.

Nothing here can make an instance UNSAT: an unsatisfiable preference is simply unmet, and
costs its unit. What this module *does* do is drop preferences the solver could never
satisfy — an hour with no supporting ``completion/5`` fact, or a mower that cannot service
the area — and say why, so the UI can tell the user rather than leaving them wondering
why their edit vanished.

When there is nothing to express (no preferences, ``mode="off"``, or everything dropped)
:func:`render_preferences` returns the **empty string**, not a comment banner. Adding even
a comment would change the program text of every preference-free solve, which is the one
thing ADR-0031 promises it does not do.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from fleetplanning.model import (
    AgreementCounts,
    DroppedPreference,
    PreferenceAgreement,
    Scenario,
    ScheduledTask,
    SolvePreferences,
)
from fleetplanning.solver.completion_table import (
    CompletionRow,
    build_completion_table,
    mower_busy_until,
)
from fleetplanning.solver.instance import earliest_starts


def _q(name: str) -> str:
    return f'"{name}"'


@dataclass(frozen=True)
class RenderedPreferences:
    """The fact program, plus what had to be left out of it."""

    text: str
    dropped: list[DroppedPreference] = field(default_factory=list)


def legal_starts(
    scenario: Scenario, completion_rows: list[CompletionRow] | None = None
) -> tuple[dict[str, set[int]], dict[tuple[str, str], set[int]]]:
    """The hours the encoding can actually place a task on — per area, and per (area, mower).

    Exactly the encoding's ``&dom`` domain: a ``completion/5`` row that survives both
    in-progress-service filters —
    :func:`~fleetplanning.solver.instance.earliest_starts` (the area is mid-service) and
    :func:`~fleetplanning.solver.completion_table.mower_busy_until` (its mower is
    mid-service elsewhere). :func:`render_preferences` judges a preference legal against
    these sets, and the mechanism-study runner (ADR-0033) draws its perturbed starts from
    them, so a sampled edit is legal by construction and is never dropped before it
    reaches an arm.

    Both returned mappings are ``defaultdict(set)`` — a missing area or (area, mower) key
    reads as "no legal start", which is what both callers want.
    """
    rows = build_completion_table(scenario) if completion_rows is None else completion_rows
    earliest = earliest_starts(scenario)
    mower_free = mower_busy_until(scenario)
    starts_by_area: dict[str, set[int]] = defaultdict(set)
    starts_by_pair: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in rows:
        if row.start < max(earliest.get(row.area, 0), mower_free.get(row.mower, 0)):
            continue
        starts_by_area[row.area].add(row.start)
        starts_by_pair[(row.area, row.mower)].add(row.start)
    return starts_by_area, starts_by_pair


def render_preferences(
    preferences: SolvePreferences | None,
    scenario: Scenario,
    completion_rows: list[CompletionRow] | None = None,
) -> RenderedPreferences:
    """Render ``preferences`` as a clingcon fact program for ``scenario``.

    ``completion_rows`` is the same table :func:`~fleetplanning.solver.instance.
    render_instance` emits from; pass it in to avoid rebuilding it. Legality is judged
    against exactly the rows that reach the solver — including the in-progress-service
    censoring — so a preference is dropped here if and only if the encoding would have
    had no ``completion/5`` fact to support it.
    """
    if preferences is None or preferences.mode == "off" or not preferences.tasks:
        return RenderedPreferences(text="")

    rows = build_completion_table(scenario) if completion_rows is None else completion_rows
    starts_by_area, starts_by_pair = legal_starts(scenario, rows)

    known_areas = {a.name for a in scenario.areas}

    times: list[tuple[str, int]] = []
    mowers: list[tuple[str, int, str]] = []
    dropped: list[DroppedPreference] = []

    for task in preferences.tasks:
        if task.area not in known_areas:
            for half in ("time", "mower"):
                if half == "mower" and task.mower is None:
                    continue
                dropped.append(
                    DroppedPreference(
                        area=task.area,
                        start=task.start,
                        mower=task.mower,
                        half=half,
                        reason=f"no area named {task.area!r} in this scenario",
                    )
                )
            continue

        time_ok = task.start in starts_by_area[task.area]
        if time_ok:
            times.append((task.area, task.start))
        else:
            dropped.append(
                DroppedPreference(
                    area=task.area,
                    start=task.start,
                    mower=task.mower,
                    half="time",
                    reason=(
                        f"no mower can start on {task.area} at hour {task.start} "
                        "(a no-go window, or a service still running at t = 0)"
                    ),
                )
            )

        if task.mower is None:
            continue
        if task.start in starts_by_pair[(task.area, task.mower)]:
            mowers.append((task.area, task.start, task.mower))
        else:
            capable = any(
                task.mower == m.name and task.area in m.can_mow for m in scenario.mowers
            )
            reason = (
                f"{task.mower!r} cannot start on {task.area} at hour {task.start}"
                if capable
                else f"{task.mower!r} cannot service {task.area}"
            )
            dropped.append(
                DroppedPreference(
                    area=task.area,
                    start=task.start,
                    mower=task.mower,
                    half="mower",
                    reason=reason,
                )
            )

    if not times and not mowers:
        return RenderedPreferences(text="", dropped=dropped)

    lines = ["% --- user preferences (ADR-0031) ---"]
    for area, start in sorted(set(times)):
        lines.append(f"pref_time({_q(area)},{start}).")
    for area, start, mower in sorted(set(mowers)):
        lines.append(f"pref_mower({_q(area)},{start},{_q(mower)}).")
    lines.append("")
    return RenderedPreferences(text="\n".join(lines), dropped=dropped)


# --- agreement: how much of what the user asked for survived ----------------------------


def preference_agreement(
    preferences: SolvePreferences | None, tasks: Sequence[ScheduledTask]
) -> PreferenceAgreement:
    """Score ``tasks`` against what the user asked for.

    A preference's **time** is kept when some task of that area starts at that hour; its
    **mower** is kept when that task is also on the named mower. Task indices are never
    consulted — that is the ADR-0031 guarantee, and this is where it has to hold.
    """
    agreement = PreferenceAgreement()
    if preferences is None or not preferences.tasks:
        return agreement

    starts: set[tuple[str, int]] = {(t.area, t.start) for t in tasks}
    with_mower: set[tuple[str, int, str]] = {(t.area, t.start, t.mower) for t in tasks}

    for task in preferences.tasks:
        bucket = agreement.by_origin.setdefault(task.origin, AgreementCounts())
        time_kept = (task.area, task.start) in starts
        for counts in (agreement, bucket):
            counts.total += 1
            counts.time_kept += int(time_kept)
        if task.mower is None:
            continue
        mower_kept = (task.area, task.start, task.mower) in with_mower
        for counts in (agreement, bucket):
            counts.mower_total += 1
            counts.mower_kept += int(mower_kept)

    return agreement
