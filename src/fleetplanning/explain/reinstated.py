"""A service the payload does not explain — the explanation class the kept/dropped count
cannot see.

Release means "out of the payload", not "never do this" (ADR-0035 decision 3) — there is
no negative preference, so a released task can come straight back the moment the area's
own service-count minimum demands it. Because the release itself is never sent as a
preference, no solver output ever marks it "dropped" — the agreement banner can say "all
your edits kept" while a job the user just deleted sits back on the Gantt.

Detected without a solver call: an area whose schedule carries more tasks than its
*baseline* named for it. The baseline is the plan before the re-solve — the submitted
payload plus whatever the caller released from that area (`released`, keyed by area name;
the backend never sees a release itself, only its count, passed in by the frontend, which
is the only side that knows what "released" means). Comparing against the payload alone
(pre-2026-09-24) always read a release as "you asked for fewer" — the opposite of what
releasing means — and reported it as though the service had been deleted and come back.
Folding the release into the baseline instead means a service the minimum brings back
after a release is not reported at all: that is exactly what release says may happen.
"""

from __future__ import annotations

from collections import Counter

from fleetplanning.derived import service_bounds
from fleetplanning.model import PreferredTask, ReinstatedService, Scenario, Schedule


def reinstated_services(
    scenario: Scenario,
    preferences: list[PreferredTask],
    schedule: Schedule,
    *,
    released: dict[str, int] | None = None,
) -> list[ReinstatedService]:
    if not preferences:
        return []
    submitted = Counter(p.area for p in preferences)
    actual = Counter(t.area for t in schedule.tasks)
    bounds = service_bounds(scenario)
    released = released or {}

    out = [
        ReinstatedService(
            area=area,
            min_services=(minimum := bounds.get(area, (0, 0))[0]),
            # The plan before the re-solve: what was submitted, plus what was released from
            # this area. A service the minimum brings back after a release is exactly what
            # release means and is not reported (the `count > baseline` filter below).
            submitted_count=(baseline := submitted.get(area, 0) + released.get(area, 0)),
            actual_count=count,
            # Only the minimum can be *named* as the cause, and only when it really bound.
            # An area already at or above its minimum gets extra services for the
            # max-interval objective's sake, not because the floor demanded them.
            forced_by_minimum=baseline < minimum,
        )
        for area, count in actual.items()
        if count > submitted.get(area, 0) + released.get(area, 0)
    ]
    return sorted(out, key=lambda r: r.area)
