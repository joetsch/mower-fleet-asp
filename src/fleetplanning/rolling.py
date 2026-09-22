"""Advance "now" for the moving-horizon loop (ADR-0042).

In production the planner is re-run continuously — every 24 h by default, or on a trigger
(a greenkeeper request, a mower down). :func:`advance` is one step of that loop: it slides
the planning window forward by a fixed number of hours, folds the part of the plan that is
now in the past into the service history, and hands back the still-future part as
preferences for the re-solve.

There is **no execution simulation** (deliberately out of scope): what a roll "executed"
is just the previous plan's tasks, passed in as ``executed``. The signature takes them as
an argument rather than reading them off the plan so that a future perturbation layer can
replace one call site and nothing else.

The window is fixed-length and slides along the same weekly availability calendar, so the
arithmetic is exact: offset ``S`` becomes ``S - hours`` and ``horizon_start_hour`` becomes
``(H + hours) mod 168``. A carried task's absolute hour-of-week
``(H + S) mod 168 = (H + hours + (S - hours)) mod 168`` is therefore **unchanged** — same
weekday, same availability window, same base duration. The only reason a carried
preference is ever dropped by ``solver/preferences.py`` afterwards is an in-progress
service censoring its hour (ADR-0040).
"""

from __future__ import annotations

from collections.abc import Sequence

from fleetplanning.model import PreferredTask, RollResult, Scenario, ScheduledTask, ServiceEvent

HOURS_PER_WEEK = 24 * 7


def advance(scenario: Scenario, executed: Sequence[ScheduledTask], hours: int) -> RollResult:
    """Roll ``scenario`` forward by ``hours``, given the tasks that were ``executed``.

    ``1 <= hours <= scenario.horizon_hours``. At the upper bound a whole window has
    elapsed: nothing carries and every area's history is refreshed.
    """
    if not 1 <= hours <= scenario.horizon_hours:
        raise ValueError(
            f"hours must be in 1..{scenario.horizon_hours} (the horizon length), got {hours}"
        )

    # A task that has already started belongs to the past even if it runs past the new
    # now; a task that has not started yet is carried forward.
    past = [t for t in executed if t.start < hours]
    future = sorted(
        (t for t in executed if t.start >= hours), key=lambda t: (t.area, t.start)
    )

    latest_past: dict[str, ScheduledTask] = {}
    for t in past:
        current = latest_past.get(t.area)
        if current is None or t.start > current.start:
            latest_past[t.area] = t

    old_history = {ev.area: ev for ev in scenario.history}
    new_history: list[ServiceEvent] = []
    consumed: list[ServiceEvent] = []
    notes: list[str] = []

    for area in scenario.areas:
        task = latest_past.get(area.name)
        if task is not None:
            event = ServiceEvent(
                area=area.name,
                mower=task.mower,
                start=task.start - hours,
                completion=task.end - hours,
            )
            consumed.append(event)
            if event.completion > 0:
                notes.append(
                    f"{area.name}: {task.mower} is still running at the new now "
                    f"(finishes at +{event.completion} h)"
                )
        else:
            old = old_history[area.name]
            event = ServiceEvent(
                area=area.name,
                mower=old.mower,
                start=old.start - hours,
                completion=old.completion - hours,
            )
            notes.append(f"{area.name}: not serviced in the last {hours} h")
        new_history.append(event)

    carried = [
        PreferredTask(area=t.area, start=t.start - hours, mower=t.mower, origin="frozen")
        for t in future
    ]

    rolled = Scenario.model_validate(
        {
            **scenario.model_dump(),
            "history": [ev.model_dump() for ev in new_history],
            "horizon_start_hour": (scenario.horizon_start_hour + hours) % HOURS_PER_WEEK,
        }
    )
    return RollResult(scenario=rolled, consumed=consumed, carried=carried, notes=notes)
