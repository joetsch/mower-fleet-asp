"""The minimal from-scratch scenario template (ADR-0027, ``scenarios/template.py``)."""

from __future__ import annotations

from fleetplanning import derived
from fleetplanning.duration import base_duration
from fleetplanning.model import Scenario
from fleetplanning.scenarios import template
from fleetplanning.scenarios.template import FIRST_AREA, FIRST_MOWER, minimal_scenario


def test_minimal_scenario_is_a_valid_one_area_one_mower_problem():
    s = minimal_scenario("my course")  # construction = pydantic validation
    assert s.name == "my course"
    assert [a.name for a in s.areas] == [FIRST_AREA]
    assert [m.name for m in s.mowers] == [FIRST_MOWER]
    assert len(s.history) == 1 and s.history[0].area == FIRST_AREA
    # the history event's mower is capable of the area (model.py requires it)
    assert FIRST_AREA in next(m for m in s.mowers if m.name == FIRST_MOWER).can_mow


def test_base_durations_cover_exactly_the_capable_pair_and_reuse_the_formula():
    s = minimal_scenario("x")
    area, mower = s.areas[0], s.mowers[0]
    assert s.base_durations is not None
    assert [(d.area, d.mower) for d in s.base_durations] == [(FIRST_AREA, FIRST_MOWER)]
    expected = base_duration(area.size_m2, "OPEN", mower.area_capacity_m2_per_day)
    assert s.base_durations[0].hours == expected
    # history "just serviced at t = 0"
    assert s.history[0].completion == 0
    assert s.history[0].start == -expected


def test_schedule_is_seven_free_weekdays():
    s = minimal_scenario("x")
    sched = s.areas[0].schedule
    assert set(sched) == {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }
    assert all(not d.no_go and not d.avoid for d in sched.values())


def test_derived_figures_are_finite_and_sensible():
    s = minimal_scenario("x")
    bounds = derived.service_bounds(s)
    assert set(bounds) == {FIRST_AREA}
    lo, hi = bounds[FIRST_AREA]
    assert 1 <= lo <= hi
    lf = derived.load_factor(s)
    assert lf is not None and lf > 0


def test_template_round_trips_through_json():
    s = minimal_scenario("Weird Name 123")
    assert Scenario.model_validate_json(s.model_dump_json()) == s


def test_free_week_helper_is_uniform():
    week = template.free_week()
    assert len(week) == 7
    assert len({d.model_dump_json() for d in week.values()}) == 1
