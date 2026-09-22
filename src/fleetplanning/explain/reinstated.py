"""A released task reappearing — the explanation class the kept/dropped count cannot see.

Release means "out of the payload", not "never do this" (ADR-0035 decision 3) — there is
no negative preference, so a released task can come straight back the moment the area's
own service-count minimum demands it. Because the release itself is never sent as a
preference, no solver output ever marks it "dropped" — the agreement banner can say "all
your edits kept" while a job the user just deleted sits back on the Gantt.

Detected without a solver call: an area whose schedule carries more tasks than the user's
submitted plan named for it. This needs no knowledge of *which* task was released — the
count mismatch alone is the signal, and :func:`fleetplanning.derived.service_bounds` says
why.
"""

from __future__ import annotations

from collections import Counter

from fleetplanning.derived import service_bounds
from fleetplanning.model import PreferredTask, ReinstatedService, Scenario, Schedule


def reinstated_services(
    scenario: Scenario, preferences: list[PreferredTask], schedule: Schedule
) -> list[ReinstatedService]:
    if not preferences:
        return []
    submitted = Counter(p.area for p in preferences)
    actual = Counter(t.area for t in schedule.tasks)
    bounds = service_bounds(scenario)

    out = [
        ReinstatedService(
            area=area,
            min_services=bounds.get(area, (0, 0))[0],
            submitted_count=submitted.get(area, 0),
            actual_count=count,
        )
        for area, count in actual.items()
        if count > submitted.get(area, 0)
    ]
    return sorted(out, key=lambda r: r.area)
