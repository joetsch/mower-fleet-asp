"""Integration tests for the orchestrator (`fleetplanning.explain.explain_scenario`):
tier-1-before-tier-2 ordering, and reinstated/ripple/edits combined in one report.
"""

from __future__ import annotations

import pytest

from fleetplanning.explain import explain_scenario
from fleetplanning.model import (
    Area,
    BaseDuration,
    DaySchedule,
    Mower,
    PreferredTask,
    Scenario,
    Schedule,
    ScheduledTask,
    SolvePreferences,
    history_event,
)

pytestmark = pytest.mark.slow

_WEEK = {
    day: DaySchedule()
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
}


def _area(name: str, *, min_services: int = 1, max_services: int = 1) -> Area:
    return Area(
        name=name, type="test", hole=1, priority=1, min_interval=1, max_interval=200,
        schedule=_WEEK, min_services=min_services, max_services=max_services,
    )


def _scenario(areas: list[Area], can_mow: dict[str, list[str]]) -> Scenario:
    mowers = [Mower(name=m, can_mow=areas_) for m, areas_ in can_mow.items()]

    def _mower_for(area_name: str) -> str:
        return next(m for m, xs in can_mow.items() if area_name in xs)

    history = [history_event(a.name, _mower_for(a.name), 10, completion=-1000) for a in areas]
    base_durations = [
        BaseDuration(area=a, mower=m, hours=10) for m, xs in can_mow.items() for a in xs
    ]
    return Scenario(
        name="orchestrator-test", areas=areas, mowers=mowers, history=history,
        horizon_hours=100, base_durations=base_durations,
    )


def _task(area: str, start: int, mower: str) -> ScheduledTask:
    return ScheduledTask(area=area, task=1, mower=mower, start=start, end=start + 10)


def test_tier_1_conflict_never_reaches_the_solver():
    """A tier-1-catchable edit must never carry a solve_time_s -- proof it never touched
    tier 2 (`explain/static.py`'s own tests cover the check itself)."""
    scenario = _scenario([_area("A"), _area("D")], {"M": ["A", "D"]})
    schedule = Schedule(tasks=[_task("A", 0, "M")], violations=[], cost=[])
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=0, mower="M", origin="frozen"),
            PreferredTask(area="D", start=5, mower="M", origin="edited"),  # overlaps A@[0,10)
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=8.0)
    assert len(report.edits) == 1
    assert report.edits[0].outcome == "blocked_statically"
    assert report.edits[0].solve_time_s is None


def test_tier_2_runs_only_for_what_tier_1_could_not_explain():
    scenario = _scenario([_area("A"), _area("D")], {"M": ["A", "D"]})
    schedule = Schedule(tasks=[_task("A", 0, "M")], violations=[], cost=[])
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=0, mower="M", origin="frozen"),
            PreferredTask(area="D", start=20, mower="M", origin="edited"),  # no overlap
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=8.0)
    assert len(report.edits) == 1
    assert report.edits[0].outcome == "not_yet_found"
    assert report.edits[0].solve_time_s is not None


def test_reinstated_and_ripple_and_edits_all_appear_together():
    scenario = _scenario(
        [_area("A"), _area("D"), _area("B", min_services=1, max_services=2)],
        {"M": ["A", "D", "B"]},
    )
    # B ends up with 2 tasks though only 1 was submitted for it -- reinstated.
    # A's frozen slot is gone (churned) but shares mower M with the D edit -- ripple.
    # D's edit itself does not appear in the schedule at all -- explained.
    schedule = Schedule(
        tasks=[_task("B", 0, "M"), _task("B", 30, "M"), _task("D", 60, "M")],
        violations=[],
        cost=[],
    )
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=10, mower="M", origin="frozen"),
            PreferredTask(area="B", start=0, mower="M", origin="frozen"),
            PreferredTask(area="D", start=90, mower="M", origin="edited"),
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=8.0)

    assert len(report.reinstated) == 1
    assert report.reinstated[0].area == "B"
    assert report.reinstated[0].submitted_count == 1
    assert report.reinstated[0].actual_count == 2

    assert len(report.ripple) == 1
    assert report.ripple[0].area == "A"
    assert report.ripple[0].shares_mower_with == "D"

    assert len(report.edits) == 1
    assert report.edits[0].area == "D"


def test_budget_exhausted_flag_and_not_determined_when_the_budget_is_effectively_zero():
    scenario = _scenario([_area("A"), _area("D")], {"M": ["A", "D"]})
    schedule = Schedule(tasks=[_task("A", 0, "M")], violations=[], cost=[])
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=0, mower="M", origin="frozen"),
            PreferredTask(area="D", start=20, mower="M", origin="edited"),
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=1e-9)
    assert report.budget_exhausted is True
    assert report.edits[0].outcome == "not_determined"


def test_no_dropped_edits_means_an_empty_report_besides_reinstated_and_ripple():
    scenario = _scenario([_area("A")], {"M": ["A"]})
    schedule = Schedule(tasks=[_task("A", 0, "M")], violations=[], cost=[])
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[PreferredTask(area="A", start=0, mower="M", origin="frozen")],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=8.0)
    assert report.edits == []
    assert report.reinstated == []
    assert report.ripple == []
    assert report.budget_exhausted is False
