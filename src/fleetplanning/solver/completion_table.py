"""Precomputed completion table — ported from the notebook (cells 9-15).

For every (area, mower it can service, start hour offset) we compute how long the job
actually takes, given that a mower *pauses* during the area's no-go hours and resumes
afterwards. We also count how many worked hours fall in "avoid" windows.

Doing this arithmetic here, in Python, keeps the clingcon model simple: the solver only
picks an integer start time from a precomputed set of ``(start -> completion)`` pairs and
never reasons about calendars.

Base duration
-------------
- If ``scenario.base_durations`` is set (the scenario generator, ADR-0013), those
  data-driven per-(area, mower) hours are used directly.
- Otherwise the notebook's seeded *sampling* applies (ADR-0005): stable across runs
  because it is driven by ``scenario.duration_seed``. This is the ``toy_course`` /
  notebook path.

Availability
------------
Each area's weekly no-go / avoid schedule is resolved **once**, up front, into a pair of
``frozenset``s of hour-of-week indices (0..167). Hour-of-week ``h`` has weekday
``DAYS[(h // 24) % 7]`` and hour-of-day ``h % 24``, checked against *that weekday's own*
intervals — exact for any per-weekday schedule, with no cross-day wrap ambiguity (see
``docs/known-hazards.md``). The inner walk is then O(1) set membership.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from fleetplanning.model import DAYS, Area, Interval, Scenario

HOURS_PER_WEEK = 24 * 7


def mower_busy_until(scenario: Scenario) -> dict[str, int]:
    """Per mower, the first hour it is free to start work on *any* area.

    A mower mid-service at t = 0 (a history event with ``completion > 0``) cannot begin
    work elsewhere until that service finishes. The forward encoding does not model this
    on its own — its only hard history rule (``:- last(A,M,S,C), asg(A,T,M), ...``) binds
    the area identically in both atoms, so it covers the same-area case only, which the
    per-area ``instance.earliest_starts`` filter already handles. Censoring the completion
    rows against this closes the cross-area gap (ADR-0040, docs/known-hazards.md).

    Lives here rather than beside ``earliest_starts`` because ``solver/greedy.py`` needs
    it too and must not import ``solver/instance`` (circular via ``bounds`` → ``derived``).
    """
    out: dict[str, int] = {}
    for ev in scenario.history:
        out[ev.mower] = max(out.get(ev.mower, 0), ev.completion, 0)
    return out


@dataclass(frozen=True)
class CompletionRow:
    """One feasible ``start -> completion`` option for an (area, mower) pair.

    Times are hour offsets from ``t = 0``. ``avoid_hours`` is how many of the worked
    hours land in an "avoid" window.
    """

    area: str
    mower: str
    start: int
    completion: int
    avoid_hours: int


def _hour_of_week(offset: int, horizon_start_hour: int) -> tuple[str, int]:
    """Map a t=0 offset to (weekday name, hour-of-day). Hour 0 = Monday 00:00."""
    absolute = horizon_start_hour + offset
    return DAYS[(absolute // 24) % 7], absolute % 24


def _in_intervals(hour_of_day: int, intervals: list[Interval]) -> bool:
    for start, end in intervals:
        if start < end:
            if start <= hour_of_day < end:
                return True
        elif hour_of_day >= start or hour_of_day < end:  # wraps past midnight
            return True
    return False


def _week_masks(area: Area) -> tuple[frozenset[int], frozenset[int]]:
    """``(no_go_hours_of_week, avoid_hours_of_week)`` for one area, each a subset of 0..167."""
    no_go: set[int] = set()
    avoid: set[int] = set()
    for how in range(HOURS_PER_WEEK):
        day = area.schedule[DAYS[(how // 24) % 7]]
        hour_of_day = how % 24
        if _in_intervals(hour_of_day, day.no_go):
            no_go.add(how)
        if _in_intervals(hour_of_day, day.avoid):
            avoid.add(how)
    return frozenset(no_go), frozenset(avoid)


def _base_duration(rng: random.Random, area_type: str) -> int:
    """Notebook's ``sample_mowing_duration``: fairways take longer than semiroughs."""
    if area_type == "fairway":
        return rng.randint(5, 8)  # np.random.randint(5, 9)
    return rng.randint(3, 6)  # np.random.randint(3, 7)


def _adjusted(
    no_go: frozenset[int],
    avoid: frozenset[int],
    start_offset: int,
    base_duration: int,
    horizon_start_hour: int,
) -> tuple[int, int]:
    """Walk hour by hour from the start, pausing in no-go windows.

    Returns (elapsed_hours, avoid_hours): elapsed spans start..completion; avoid_hours
    counts productive hours worked inside an "avoid" window.
    """
    remaining = base_duration
    elapsed = 0
    avoid_hours = 0
    cursor = start_offset
    while remaining > 0:
        how = (horizon_start_hour + cursor) % HOURS_PER_WEEK
        if how not in no_go:
            remaining -= 1
            if how in avoid:
                avoid_hours += 1
        elapsed += 1
        cursor += 1
    return elapsed, avoid_hours


def build_completion_table(
    scenario: Scenario, *, rng: random.Random | None = None
) -> list[CompletionRow]:
    """All feasible ``start -> completion`` options across the horizon.

    A start hour that is itself inside a no-go window is skipped (a mower cannot begin
    then). The base duration comes from ``scenario.base_durations`` when set, else from
    sampling once per (area, mower) pair in scenario order.

    ``rng`` defaults to ``random.Random(scenario.duration_seed)``; it is only consulted
    on the sampling path (``base_durations is None``).
    """
    rng = rng or random.Random(scenario.duration_seed)
    given = (
        {(bd.area, bd.mower): bd.hours for bd in scenario.base_durations}
        if scenario.base_durations is not None
        else None
    )

    rows: list[CompletionRow] = []
    for area in scenario.areas:
        no_go, avoid = _week_masks(area)
        for mower in scenario.mowers:
            if area.name not in mower.can_mow:
                continue
            if given is not None:
                base_duration = given[(area.name, mower.name)]
            else:
                base_duration = _base_duration(rng, area.type)
            for offset in range(scenario.horizon_hours):
                how = (scenario.horizon_start_hour + offset) % HOURS_PER_WEEK
                if how in no_go:
                    continue
                elapsed, avoid_hours = _adjusted(
                    no_go, avoid, offset, base_duration, scenario.horizon_start_hour
                )
                rows.append(
                    CompletionRow(
                        area=area.name,
                        mower=mower.name,
                        start=offset,
                        completion=offset + elapsed,
                        avoid_hours=avoid_hours,
                    )
                )
    return rows
