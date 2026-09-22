"""Tests for ``solver/score.py`` — the Python re-implementation of the forward-model
objective (ADR-0016).

The slow test is the important one: it proves ``score_schedule`` reproduces clingcon's
own cost vector on a proven-optimal instance, so the study's fixed 5-slot quality metric
is the same number the solver optimised.
"""

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
    Schedule,
    ScheduledTask,
    ServiceEvent,
)
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.solver.score import score_schedule

from .conftest import GOLDEN_DIR


def _one_area_scenario(
    *, priority: int = 1, avoid: list[tuple[int, int]] | None = None
) -> Scenario:
    """One area 'F', one mower 'M', 6-hour job, history finishing at t = 0."""
    return Scenario(
        name="hand",
        areas=[
            Area(
                name="F",
                type="fairway",
                hole=1,
                priority=priority,
                min_interval=18,
                max_interval=24,
                schedule={d: DaySchedule(no_go=[], avoid=avoid or []) for d in DAYS},
            )
        ],
        mowers=[Mower(name="M", can_mow=["F"])],
        history=[ServiceEvent(area="F", mower="M", start=-6, completion=0)],
        horizon_hours=168,
        horizon_start_hour=0,
        base_durations=[BaseDuration(area="F", mower="M", hours=6)],
    )


def test_empty_schedule_scores_all_zero():
    sv = score_schedule(_one_area_scenario(), [])
    assert sv.slots == [0, 0, 0, 0, 0]
    assert sv.violations == []


def test_gap_history_and_horizon_violations_land_in_the_right_slots():
    # task1 @10, task2 @40: gap 30 > max_interval 24 -> one max@P1; start 10 < -6+18 ->
    # first_task_too_early -> min; last start 40 < 167-24 -> last_task_too_early -> max@P1.
    tasks = [
        ScheduledTask(area="F", task=1, mower="M", start=10, end=16),
        ScheduledTask(area="F", task=2, mower="M", start=40, end=46),
    ]
    sv = score_schedule(_one_area_scenario(), tasks)
    assert sv.slots == [2, 0, 0, 0, 1]
    assert sv.detail["max_interval"] == 1
    assert sv.detail["last_task_too_early"] == 1
    assert sv.detail["first_task_too_early"] == 1


def test_priority_routes_to_its_own_max_slot():
    # single task @100: > history deadline (first_task_too_late) AND < 167-24
    # (last_task_too_early) -> two max-interval units, both charged to P3's slot.
    tasks = [ScheduledTask(area="F", task=1, mower="M", start=100, end=106)]
    sv = score_schedule(_one_area_scenario(priority=3), tasks)
    assert sv.max_by_priority == (0, 0, 2)  # P3 -> slot index 2


def test_avoid_hours_count_once_per_task():
    # avoid 08:00-20:00; a task starting at 08:00 works entirely inside it.
    scen = _one_area_scenario(avoid=[(8, 20)])
    tasks = [ScheduledTask(area="F", task=1, mower="M", start=8, end=14)]
    sv = score_schedule(scen, tasks)
    assert sv.avoid == 1
    assert any(v.kind == "avoid_zone" for v in sv.violations)


@pytest.mark.slow
def test_recomputed_score_matches_clingcon_cost_on_the_optimal_toy_schedule():
    golden = json.loads((GOLDEN_DIR / "toy_course_schedule_t1.json").read_text())
    sched = Schedule.model_validate(golden)
    sv = score_schedule(toy_course(), sched.tasks)
    assert sv.slots == sched.cost  # the golden is proven optimal -> the two must agree
    assert len(sv.violations) == len(sched.violations)
