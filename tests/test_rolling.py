"""Tests for ``fleetplanning.rolling`` — advancing "now" for the moving horizon (ADR-0042)."""

from __future__ import annotations

import pytest

from fleetplanning.model import (
    DAYS,
    Area,
    DaySchedule,
    Mower,
    Scenario,
    ScheduledTask,
    ServiceEvent,
    SolvePreferences,
)
from fleetplanning.rolling import HOURS_PER_WEEK, advance
from fleetplanning.scenarios.registry import load, scenario_ids
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.service import solve_scenario
from fleetplanning.solver.preferences import render_preferences


def _tiny_scenario() -> Scenario:
    areas = [
        Area(
            name=name,
            type="semirough_A",
            hole=1,
            priority=2,
            min_interval=10,
            max_interval=40,
            schedule={d: DaySchedule() for d in DAYS},
        )
        for name in ("A", "B")
    ]
    return Scenario(
        name="tiny",
        areas=areas,
        mowers=[Mower(name="M", can_mow=["A", "B"])],
        history=[
            ServiceEvent(area="A", mower="M", start=-8, completion=-2),
            ServiceEvent(area="B", mower="M", start=-30, completion=-24),
        ],
        horizon_hours=168,
        horizon_start_hour=13,
    )


def _plan(*triples: tuple[str, str, int, int]) -> list[ScheduledTask]:
    return [
        ScheduledTask(area=a, task=i + 1, mower=m, start=s, end=e)
        for i, (a, m, s, e) in enumerate(triples)
    ]


def test_hours_must_be_within_the_horizon():
    s = _tiny_scenario()
    with pytest.raises(ValueError):
        advance(s, [], 0)
    with pytest.raises(ValueError):
        advance(s, [], s.horizon_hours + 1)


def test_now_and_every_offset_slide_by_the_same_amount():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 4, 10), ("A", "M", 100, 106), ("B", "M", 50, 56))
    result = advance(s, plan, hours=24)

    assert result.scenario.horizon_start_hour == (13 + 24) % HOURS_PER_WEEK
    assert result.scenario.horizon_hours == s.horizon_hours  # fixed-length window


def test_a_carried_task_keeps_its_exact_wall_clock_hour():
    """The invariant that makes a roll auditable: (H + S) mod 168 is unchanged, so a
    carried preference lands in the same availability window with the same duration."""
    s = _tiny_scenario()
    plan = _plan(("A", "M", 30, 36), ("B", "M", 120, 126))
    hours = 24
    result = advance(s, plan, hours)

    old_abs = {(t.area, (s.horizon_start_hour + t.start) % HOURS_PER_WEEK) for t in plan}
    new_abs = {
        (p.area, (result.scenario.horizon_start_hour + p.start) % HOURS_PER_WEEK)
        for p in result.carried
    }
    assert new_abs == old_abs


def test_past_tasks_fold_into_history_one_event_per_area():
    s = _tiny_scenario()
    # A serviced twice before the new now, B once; latest A task wins.
    plan = _plan(
        ("A", "M", 2, 8),
        ("A", "M", 15, 21),
        ("B", "M", 5, 11),
        ("A", "M", 60, 66),  # future
    )
    result = advance(s, plan, hours=24)

    hist = {ev.area: ev for ev in result.scenario.history}
    assert set(hist) == {"A", "B"}
    assert hist["A"].start == 15 - 24 and hist["A"].completion == 21 - 24
    assert hist["B"].start == 5 - 24 and hist["B"].completion == 11 - 24
    assert {ev.area for ev in result.consumed} == {"A", "B"}


def test_an_area_not_serviced_in_the_window_keeps_its_old_event_rebased():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 4, 10))  # only A serviced
    result = advance(s, plan, hours=24)

    hist = {ev.area: ev for ev in result.scenario.history}
    # B's old event was start=-30, completion=-24 -> rebased by -24
    assert hist["B"].start == -54 and hist["B"].completion == -48
    assert "B" not in {ev.area for ev in result.consumed}
    assert any("B" in note for note in result.notes)


def test_a_task_straddling_the_new_now_becomes_an_in_progress_event():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 20, 30))  # starts before hour 24, ends after
    result = advance(s, plan, hours=24)

    ev = next(e for e in result.scenario.history if e.area == "A")
    assert ev.start == 20 - 24  # -4
    assert ev.completion == 30 - 24  # +6, still running at the new now
    assert ev.completion > 0
    assert any("running" in note for note in result.notes)


def test_carried_preferences_are_all_non_negative_and_frozen():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 4, 10), ("A", "M", 40, 46), ("B", "M", 100, 106))
    result = advance(s, plan, hours=24)

    assert [(p.area, p.start) for p in result.carried] == [("A", 16), ("B", 76)]
    assert all(p.start >= 0 for p in result.carried)
    assert all(p.origin == "frozen" for p in result.carried)


def test_a_full_horizon_roll_carries_nothing():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 4, 10), ("B", "M", 150, 156))
    result = advance(s, plan, hours=s.horizon_hours)

    assert result.carried == []
    assert result.scenario.horizon_start_hour == s.horizon_start_hour  # a whole week
    assert {ev.area for ev in result.scenario.history} == {"A", "B"}


def test_rolled_scenario_is_a_valid_scenario():
    s = _tiny_scenario()
    plan = _plan(("A", "M", 20, 30), ("B", "M", 5, 11), ("A", "M", 90, 96))
    result = advance(s, plan, hours=24)
    # Round-trips through full pydantic validation without raising.
    Scenario.model_validate(result.scenario.model_dump())


@pytest.mark.parametrize("slug", sorted(scenario_ids()))
def test_every_curated_scenario_rolls_and_keeps_one_history_event_per_area(slug):
    scenario, _ = load(slug)
    # A cheap synthetic "executed" plan: one task per area at a legal-looking hour.
    plan = [
        ScheduledTask(area=a.name, task=1, mower=_capable(scenario, a.name), start=10, end=16)
        for a in scenario.areas
    ]
    result = advance(scenario, plan, hours=24)

    assert len(result.scenario.history) == len(scenario.areas)
    assert {ev.area for ev in result.scenario.history} == {a.name for a in scenario.areas}
    Scenario.model_validate(result.scenario.model_dump())


def _capable(scenario: Scenario, area: str) -> str:
    return next(m.name for m in scenario.mowers if area in m.can_mow)


@pytest.mark.slow
def test_a_rolled_scenario_solves_and_carried_preferences_are_honoured():
    """End to end: solve toy_course, roll a day, re-solve carrying the remaining plan.
    Nothing should be dropped except where an in-progress service censors an hour."""
    scenario = toy_course()
    base = solve_scenario(scenario, time_limit_s=30.0, threads=1)
    assert base.schedule is not None

    result = advance(scenario, base.schedule.tasks, hours=24)
    prefs = SolvePreferences(mode="weak", level="top", tasks=result.carried)
    rendered = render_preferences(prefs, result.scenario)

    for dropped in rendered.dropped:
        assert "still running" in dropped.reason or "no-go" in dropped.reason


@pytest.mark.slow
def test_no_mower_starts_an_area_while_still_busy_elsewhere_after_a_roll():
    """ADR-0040's invariant, asserted on a *solved rolled schedule* rather than on the
    instance.

    ``test_instance.py`` pins that no ``completion`` row is emitted for a mower that is
    mid-service somewhere else; this pins that the property survives grounding, solving and
    parsing — the thing an observer would actually check, and the one walkthrough item that
    cannot be read off the chart (past bars carry no mower colour, only hover text).

    A roll is what makes this worth testing: it is what manufactures straddling services,
    where a fixed ``t = 0`` scenario normally has none. The roll point is taken from the
    solved plan — *inside* a running service — rather than fixed at 24 h, because a plain
    day-long roll of this instance happens to land in a gap and the assertion would then
    be vacuous. The guard below is what caught that.
    """
    scenario = toy_course()
    base = solve_scenario(scenario, time_limit_s=30.0, threads=1)
    assert base.schedule is not None

    running = next(t for t in base.schedule.tasks if t.end - t.start >= 2 and t.start >= 1)
    mid_service = (running.start + running.end) // 2
    rolled = advance(scenario, base.schedule.tasks, hours=mid_service).scenario
    # Computed here rather than through `mower_busy_until` on purpose: a test that reuses
    # the helper it is checking can only ever agree with it.
    busy_until: dict[str, int] = {}
    for ev in rolled.history:
        busy_until[ev.mower] = max(busy_until.get(ev.mower, 0), ev.completion, 0)
    # Without a mower actually busy across the new t = 0 the assertion below is vacuous —
    # which is exactly how this test would rot into a green no-op.
    assert any(h > 0 for h in busy_until.values()), "the roll produced no in-progress service"

    replanned = solve_scenario(rolled, time_limit_s=30.0, threads=1)
    assert replanned.schedule is not None
    for task in replanned.schedule.tasks:
        assert task.start >= busy_until.get(task.mower, 0), (
            f"{task.mower} starts {task.area} at {task.start} while still finishing "
            f"elsewhere until {busy_until.get(task.mower)}"
        )
