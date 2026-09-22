"""Tests for ``solver/greedy.py`` — the priority-tier + EDF baseline (ADR-0016)."""

from __future__ import annotations

import json

import pytest

from fleetplanning.model import (
    DAYS,
    Area,
    BaseDuration,
    DaySchedule,
    Mower,
    Scenario,
    ServiceEvent,
)
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.solver.greedy import greedy_schedule

from .conftest import GOLDEN_DIR


def _free_day() -> dict[str, DaySchedule]:
    return {d: DaySchedule(no_go=[], avoid=[]) for d in DAYS}


def _two_priority_scenario() -> Scenario:
    """A P1 area and a P3 area, one shared mower, both overdue at t = 0."""
    areas = [
        Area(
            name=name,
            type="fairway" if name == "hi" else "semirough",
            hole=1,
            priority=prio,
            min_interval=18,
            max_interval=24,
            schedule=_free_day(),
        )
        for name, prio in (("hi", 1), ("lo", 3))
    ]
    return Scenario(
        name="tiers",
        areas=areas,
        mowers=[Mower(name="M", can_mow=["hi", "lo"])],
        history=[
            ServiceEvent(area="hi", mower="M", start=-30, completion=-24),
            ServiceEvent(area="lo", mower="M", start=-30, completion=-24),
        ],
        horizon_hours=168,
        horizon_start_hour=0,
        base_durations=[
            BaseDuration(area="hi", mower="M", hours=6),
            BaseDuration(area="lo", mower="M", hours=6),
        ],
    )


def test_a_mower_mid_service_is_not_free_until_it_finishes():
    """A mower still finishing area X at t = +5 cannot be dispatched to area Y before
    then (ADR-0040) — the greedy witness must respect the same cutoff the solver does."""
    areas = [
        Area(name=name, type="semirough", hole=1, priority=2, min_interval=18,
             max_interval=24, schedule=_free_day())
        for name in ("x", "y")
    ]
    scenario = Scenario(
        name="busy",
        areas=areas,
        mowers=[Mower(name="M", can_mow=["x", "y"])],
        history=[
            ServiceEvent(area="x", mower="M", start=-1, completion=5),  # in progress
            ServiceEvent(area="y", mower="M", start=-30, completion=-24),
        ],
        horizon_hours=168,
        horizon_start_hour=0,
        base_durations=[
            BaseDuration(area="x", mower="M", hours=6),
            BaseDuration(area="y", mower="M", hours=6),
        ],
    )
    sched = greedy_schedule(scenario)
    assert min(t.start for t in sched.tasks) >= 5


def test_deterministic():
    s = toy_course()
    a = greedy_schedule(s)
    b = greedy_schedule(s)
    assert [t.model_dump() for t in a.tasks] == [t.model_dump() for t in b.tasks]
    assert a.cost == b.cost


def test_priority_tier_is_served_first():
    sched = greedy_schedule(_two_priority_scenario())
    first_hi = min(t.start for t in sched.tasks if t.area == "hi")
    first_lo = min(t.start for t in sched.tasks if t.area == "lo")
    assert first_hi < first_lo


def test_respects_mower_non_overlap_and_intra_area_order():
    sched = greedy_schedule(toy_course())

    by_mower: dict[str, list] = {}
    for t in sched.tasks:
        by_mower.setdefault(t.mower, []).append(t)
    for tasks in by_mower.values():
        tasks.sort(key=lambda t: t.start)
        for a, b in zip(tasks, tasks[1:], strict=False):
            assert b.start >= a.end, "a mower may not run two tasks at once"

    by_area: dict[str, list] = {}
    for t in sched.tasks:
        by_area.setdefault(t.area, []).append(t)
    for tasks in by_area.values():
        tasks.sort(key=lambda t: t.task)
        for a, b in zip(tasks, tasks[1:], strict=False):
            assert b.start >= a.end, "an area's next task starts after the previous completes"


@pytest.mark.slow
def test_greedy_never_beats_the_proven_optimum_on_the_toy_instance():
    optimum = json.loads((GOLDEN_DIR / "toy_course_schedule_t1.json").read_text())["cost"]
    greedy = greedy_schedule(toy_course()).cost
    assert greedy >= optimum, f"greedy {greedy} is lexicographically below the optimum {optimum}"
