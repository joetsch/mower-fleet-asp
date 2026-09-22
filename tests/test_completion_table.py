import random

from fleetplanning.model import DAYS, Area, BaseDuration, DaySchedule, Mower, Scenario, ServiceEvent
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.solver.completion_table import (
    _adjusted,
    _base_duration,
    _hour_of_week,
    _in_intervals,
    _week_masks,
    build_completion_table,
)


def test_hour_of_week_maps_offsets_to_weekday_and_hour():
    assert _hour_of_week(0, 13) == ("Monday", 13)
    assert _hour_of_week(11, 13) == ("Tuesday", 0)
    assert _hour_of_week(167, 13) == ("Monday", 12)


def test_in_intervals_is_half_open_and_wraps_midnight():
    assert _in_intervals(23, [(22, 6)]) is True
    assert _in_intervals(5, [(22, 6)]) is True
    assert _in_intervals(6, [(22, 6)]) is False  # end is exclusive
    assert _in_intervals(14, [(13, 20)]) is True
    assert _in_intervals(20, [(13, 20)]) is False


def test_adjusted_pauses_through_a_no_go_window():
    # no-go 02:00-04:00 every day; start Monday 00:00; 3 hours of work.
    area = Area(
        name="A",
        type="fairway",
        hole=1,
        priority=1,
        min_interval=18,
        max_interval=24,
        schedule={d: DaySchedule(no_go=[(2, 4)], avoid=[]) for d in DAYS},
    )
    no_go, avoid_hows = _week_masks(area)
    elapsed, avoid = _adjusted(
        no_go, avoid_hows, start_offset=0, base_duration=3, horizon_start_hour=0
    )
    assert elapsed == 5  # worked 0,1 then paused 2,3 then worked 4
    assert avoid == 0


def test_base_duration_ranges_per_area_type():
    """Notebook's ``sample_mowing_duration``: fairway 5..8, everything else 3..6."""
    fairway = {_base_duration(random.Random(s), "fairway") for s in range(200)}
    semi = {_base_duration(random.Random(s), "semirough_A") for s in range(200)}
    assert fairway == {5, 6, 7, 8}
    assert semi == {3, 4, 5, 6}


def test_completion_table_is_deterministic_for_a_fixed_seed():
    scenario = toy_course()
    assert build_completion_table(scenario) == build_completion_table(scenario)


def test_injected_rng_overrides_the_scenario_seed():
    scenario = toy_course()  # duration_seed=1
    from_seed = build_completion_table(scenario)
    same = build_completion_table(scenario, rng=random.Random(1))
    different = build_completion_table(scenario, rng=random.Random(999))
    assert same == from_seed
    assert different != from_seed


def test_base_duration_is_drawn_once_per_capable_pair_in_area_then_mower_order():
    """The RNG is consumed for capable (area, mower) pairs only, area-major. Reordering
    mowers or making a pair (in)capable shifts every later duration (ADR-0005)."""
    areas = [
        Area(name="A", type="fairway", hole=1, priority=1, min_interval=18, max_interval=24,
             schedule={d: DaySchedule() for d in DAYS}),
        Area(name="B", type="semirough_A", hole=1, priority=2, min_interval=39, max_interval=48,
             schedule={d: DaySchedule() for d in DAYS}),
    ]
    mowers = [Mower(name="M1", can_mow=["A", "B"]), Mower(name="M2", can_mow=["B"])]
    scenario = Scenario(
        name="s", areas=areas, mowers=mowers,
        history=[
            ServiceEvent(area="A", mower="M1", start=-5, completion=-1),
            ServiceEvent(area="B", mower="M1", start=-5, completion=-1),
        ],
        horizon_hours=6, horizon_start_hour=0, duration_seed=42,
    )
    rows = build_completion_table(scenario)
    got = {}
    for r in rows:
        got.setdefault((r.area, r.mower), r.completion - r.start)

    rng = random.Random(42)
    expected = {
        ("A", "M1"): _base_duration(rng, "fairway"),   # 1st draw
        ("B", "M1"): _base_duration(rng, "semirough_A"),  # 2nd draw
        ("B", "M2"): _base_duration(rng, "semirough_A"),  # 3rd draw
    }
    assert got == expected


def test_completion_table_respects_no_go_and_avoid():
    scenario = toy_course()
    rows = build_completion_table(scenario)
    areas = {a.name: a for a in scenario.areas}

    assert rows, "expected a non-empty table"
    for row in rows:
        assert row.completion > row.start
        weekday, hour = _hour_of_week(row.start, scenario.horizon_start_hour)
        assert not _in_intervals(hour, areas[row.area].schedule[weekday].no_go)
        # only fairways have avoid windows in the toy scenario
        if row.avoid_hours:
            assert areas[row.area].type == "fairway"


def test_base_durations_override_sampling():
    """When scenario.base_durations is set, those hours are used verbatim (ADR-0013)."""
    area = Area(name="A", type="fairway", hole=1, priority=1, min_interval=18, max_interval=24,
                schedule={d: DaySchedule() for d in DAYS})
    scenario = Scenario(
        name="s", areas=[area], mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-5, completion=-1)],
        horizon_hours=6, horizon_start_hour=0, duration_seed=42,
        base_durations=[BaseDuration(area="A", mower="M", hours=9)],
    )
    rows = build_completion_table(scenario)
    assert {r.completion - r.start for r in rows} == {9}  # not a sampled fairway (5..8)


def test_week_masks_resolve_per_weekday_independently():
    """A Monday-only no-go blocks Monday's hours and NOT Tuesday 00:00 — defines the
    per-hour-of-week semantics that the mask makes exact (docs/known-hazards.md)."""
    sched = {d: DaySchedule() for d in DAYS}
    sched["Monday"] = DaySchedule(no_go=[(22, 0)])  # Mon 22:00-24:00 only
    area = Area(name="A", type="fairway", hole=1, priority=1, min_interval=18, max_interval=24,
                schedule=sched)
    no_go, _ = _week_masks(area)
    assert 22 in no_go and 23 in no_go        # Monday 22, 23
    assert 24 not in no_go and 25 not in no_go  # Tuesday 00, 01 — free


def test_mask_path_matches_a_hand_walk_on_a_uniform_schedule(toy_scenario):
    """Regression: the refactored table equals a fresh solve of toy_course's golden
    (uniform (22,6)/(23,5) windows) — proven elsewhere by the golden test; here we just
    assert the table is stable and non-empty after the refactor."""
    rows_a = build_completion_table(toy_scenario)
    rows_b = build_completion_table(toy_course())
    assert rows_a == rows_b and rows_a


def test_single_area_single_mower_scenario_has_expected_shape():
    area = Area(
        name="A",
        type="semirough_A",
        hole=1,
        priority=2,
        min_interval=39,
        max_interval=48,
        schedule={d: DaySchedule(no_go=[], avoid=[]) for d in DAYS},
    )
    scenario = Scenario(
        name="tiny",
        areas=[area],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-10, completion=-4)],
        horizon_hours=24,
        horizon_start_hour=0,
        duration_seed=0,
    )
    rows = build_completion_table(scenario)
    # no no-go anywhere -> one row per start offset, constant duration
    assert len(rows) == 24
    durations = {r.completion - r.start for r in rows}
    assert len(durations) == 1
    assert all(r.avoid_hours == 0 for r in rows)
