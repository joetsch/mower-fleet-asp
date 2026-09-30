import pytest

from fleetplanning.model import DAYS, Area, BaseDuration, DaySchedule, Mower, Scenario, ServiceEvent
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.solver.bounds import _count, service_count_bounds
from fleetplanning.solver.greedy import greedy_schedule


def _area(name: str, min_interval: int, max_interval: int) -> Area:
    return Area(
        name=name,
        type="fairway",
        hole=1,
        priority=1,
        min_interval=min_interval,
        max_interval=max_interval,
        schedule={d: DaySchedule() for d in DAYS},
    )


def test_count_counts_starts_strictly_below_the_horizon():
    assert _count(first_start=0, step=24, horizon=168) == 7  # 0,24,48,72,96,120,144
    assert _count(first_start=5, step=10, horizon=24) == 2  # 5,15
    assert _count(first_start=-3, step=10, horizon=24) == 3  # clamped to 0: 0,10,20
    assert _count(first_start=200, step=10, horizon=168) == 0  # first start past the horizon


def test_area_whose_first_start_is_past_the_horizon_gets_zero_bounds():
    scenario = Scenario(
        name="s",
        areas=[_area("A", min_interval=100, max_interval=100)],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=90, completion=95)],
        horizon_hours=168,
    )
    assert service_count_bounds(scenario)["A"] == (0, 0)  # 90 + 100 = 190 > 168


def test_known_bounds_for_the_toy_scenario():
    bounds = service_count_bounds(toy_course())
    # The floor is now 1 (see test_min_starts_is_never_more_than_one below): the objective,
    # not the floor, decides how densely a contended area gets served. Only the ceiling
    # still matches the old cadence-based max.
    assert bounds["Hole1_Fairway"] == (1, 9)
    assert bounds["Hole2_SemiroughC"] == (1, 4)


def test_min_starts_is_never_more_than_one():
    # An area whose cadence (and greedy witness) both call for several services a week
    # used to get min_starts == cadence, forcing that many tasks in even when the
    # optimum doesn't want them (owner report, compact-single-mower/H2_SR2: a forced
    # 4th task there produced an awkward min-interval undercut for no cost benefit).
    # The floor now only ensures the area is *represented* at all -- one task is enough
    # to switch on its max-interval objective terms -- and leaves how many more to add
    # to the optimizer.
    scenario = Scenario(
        name="s",
        areas=[_area("A", min_interval=39, max_interval=48)],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-45, completion=-41)],
        horizon_hours=168,
    )
    bounds = service_count_bounds(scenario)
    assert bounds["A"][0] == 1  # cadence here is 4 -- the old floor would have been 4


def test_derived_bounds_bracket_the_greedy_witness_for_every_area():
    scenario = toy_course()
    bounds = service_count_bounds(scenario)
    counts: dict[str, int] = {}
    for t in greedy_schedule(scenario).tasks:
        counts[t.area] = counts.get(t.area, 0) + 1
    for name, (lo, hi) in bounds.items():
        assert 0 <= lo <= hi
        assert lo <= counts.get(name, 0) <= hi  # the witness is inside the bounds


def test_recently_serviced_area_with_long_intervals_gets_zero_bounds():
    # last start 90, intervals 100: the first feasible start (190) is past the horizon,
    # so cadence == physical == 0 and there is no meaningful lower bound to impose.
    scenario = Scenario(
        name="s",
        areas=[_area("A", min_interval=100, max_interval=100), _area("B", 18, 24)],
        mowers=[Mower(name="M", can_mow=["A", "B"])],
        history=[
            ServiceEvent(area="A", mower="M", start=90, completion=95),
            ServiceEvent(area="B", mower="M", start=-10, completion=-4),
        ],
        horizon_hours=168,
        base_durations=[
            BaseDuration(area="A", mower="M", hours=6),
            BaseDuration(area="B", mower="M", hours=6),
        ],
    )
    bounds = service_count_bounds(scenario)
    assert bounds["A"] == (0, 0)
    assert bounds["B"][0] >= 1


def test_tight_false_widens_max_starts_to_the_physical_ceiling():
    scenario = toy_course()
    tight = service_count_bounds(scenario, tight=True)
    loose = service_count_bounds(scenario, tight=False)
    for name in tight:
        assert loose[name][0] == tight[name][0]  # same floor
        assert loose[name][1] >= tight[name][1]  # never tighter on top


def test_missing_history_is_rejected():
    scenario = toy_course()
    scenario.history = [ev for ev in scenario.history if ev.area != "Hole1_Fairway"]
    with pytest.raises(ValueError, match="Hole1_Fairway"):
        service_count_bounds(scenario)
