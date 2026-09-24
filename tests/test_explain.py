"""Integration tests for the orchestrator (`fleetplanning.explain.explain_scenario`):
tier-1-before-tier-2 ordering, and reinstated/ripple/edits combined in one report.
"""

from __future__ import annotations

import pytest

from fleetplanning.explain import explain_scenario, plan_consistent_kept
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


def test_every_kept_assumption_is_one_the_plan_itself_satisfies():
    """The load-bearing property of the whole mechanism, pinned directly.

    The explanation asks whether a dropped edit can hold *alongside the kept set*. That
    question is only meaningful because the kept set is satisfiable, and it is satisfiable
    for a reason that needs no solver: **the plan on screen satisfies it**. Every core of
    ``{edit} ∪ kept`` therefore contains the edit.

    Lose this and the failure is silent and total: an unsatisfiable kept set makes every
    dropped edit "conflict", and minimisation (which pins the edit) returns a contradiction
    among the kept services instead. That is what shipped, and what produced every one of
    the paper's 37 conflict findings.

    So: every preference this returns must match a scheduled task exactly — area, hour
    *and* mower — and nothing kept may be dropped along the way.
    """
    schedule = Schedule(
        tasks=[_task("A", 0, "M"), _task("D", 5, "N"), _task("C", 50, "N")],
        violations=[], cost=[],
    )
    submitted = [
        PreferredTask(area="A", start=0, mower="M", origin="frozen"),   # mower honoured
        PreferredTask(area="D", start=5, mower="M", origin="frozen"),   # hour only
        PreferredTask(area="C", start=50, mower=None, origin="frozen"), # no mower asked
        PreferredTask(area="C", start=30, mower="N", origin="edited"),  # not kept at all
    ]

    kept = plan_consistent_kept(submitted, schedule.tasks)

    scheduled = {(t.area, t.start, t.mower) for t in schedule.tasks}
    assert [(k.area, k.start, k.mower) for k in kept] == [
        ("A", 0, "M"), ("D", 5, "N"), ("C", 50, "N")
    ]
    for k in kept:
        assert (k.area, k.start, k.mower) in scheduled, f"{k} is not in the plan"


def test_a_kept_service_on_another_mower_does_not_invent_a_conflict():
    """The kept set must be assumed as the *plan* realises it, not as it was requested.

    ``weak@top`` routinely keeps a preference's hour while putting the service on a
    different mower — that is partial success, not failure. If the counterfactual assumes
    the *requested* mower for such a preference, it reasons about a week that does not
    exist, and the assumed set can be unsatisfiable on its own; every dropped edit is then
    UNSAT with it, and minimisation (which pins the edit) converges on a contradiction
    among the kept services that has nothing to do with the edit.

    Here ``D@5`` was requested on ``M`` but the plan puts it on ``N``. Assumed as
    requested, ``A@0`` and ``D@5`` both sit on ``M`` over ``[0,10)`` and ``[5,15)`` — which
    is impossible. The edit ``C@30`` on ``N`` genuinely fits (``N`` is free between
    ``D@5``'s real slot and ``C@50``), so the honest answer is that a schedule exists.

    Found by auditing the recorded pilot: all three ``six-hole-course`` cells had an
    unsatisfiable assumed kept set, and produced every one of the paper's 37 "conflicts".
    """
    scenario = _scenario(
        [_area("A"), _area("D"), _area("C", max_services=2)],
        {"M": ["A", "D"], "N": ["A", "D", "C"]},
    )
    # The plan: D is on N, not on the M it was asked for.
    schedule = Schedule(
        tasks=[_task("A", 0, "M"), _task("D", 5, "N"), _task("C", 50, "N")],
        violations=[], cost=[],
    )
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=0, mower="M", origin="frozen"),
            PreferredTask(area="D", start=5, mower="M", origin="frozen"),
            PreferredTask(area="C", start=50, mower="N", origin="frozen"),
            PreferredTask(area="C", start=30, mower="N", origin="edited"),
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=20.0)

    (edit,) = report.edits
    assert edit.area == "C" and edit.start == 30
    assert edit.outcome == "not_yet_found", (
        f"expected a satisfiable counterfactual, got {edit.outcome} "
        f"with conflicts {[(c.area, c.start, c.mower) for c in edit.conflicts]}"
    )
    assert edit.conflicts == []


def test_an_impossible_hour_is_reported_as_impossible_not_as_a_conflict():
    """An edit with no completion row at all cannot "conflict" with anything.

    Tier 1 used to answer this case itself, with an empty conflict list, which stopped
    tier 2 ever running and rendered on screen as the nonsense "conflicts with 0 kept
    tasks" — while the banner's own pre-solve row, which carried the true reason, was
    suppressed as already-explained. ADR-0048's taxonomy says this is
    ``individually_impossible``, which is what tier 2 returns unaided.
    """
    scenario = _scenario([_area("A"), _area("D")], {"M": ["A", "D"]})
    schedule = Schedule(tasks=[_task("A", 0, "M")], violations=[], cost=[])
    preferences = SolvePreferences(
        mode="weak", level="top",
        tasks=[
            PreferredTask(area="A", start=0, mower="M", origin="frozen"),
            # Past the horizon: no completion row exists for this (area, mower, hour).
            PreferredTask(area="D", start=99999, mower="M", origin="edited"),
        ],
    )
    report = explain_scenario(scenario, preferences, schedule, budget_s=20.0)

    (edit,) = report.edits
    assert edit.outcome == "individually_impossible"
    assert edit.conflicts == []


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
