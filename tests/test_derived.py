"""Figures recomputed from a ``Scenario`` (ADR-0024, ``fleetplanning.derived``)."""

from __future__ import annotations

import pytest

from fleetplanning import derived
from fleetplanning.scenarios import registry
from fleetplanning.scenarios.toy_course import toy_course


@pytest.mark.parametrize("slug", registry.scenario_ids())
def test_load_factor_matches_the_generator_source(slug):
    """The re-derivation on the compiled scenario must reproduce the generator's own
    ``load_factor`` (from ``<slug>.source.json``) — the guard that the promoted format is
    faithful. This is the regression the study depends on staying stable."""
    scenario, source = registry.load(slug)
    if source is None:
        pytest.skip("hand-authored or UI-saved scenario (ADR-0026) — no generator source")
    assert derived.load_factor(scenario) == pytest.approx(source.load_factor, abs=5e-4)


def test_load_factor_is_none_without_size_or_rate():
    # toy-course is hand-authored: no size_m2, no mower rate.
    assert derived.load_factor(toy_course()) is None


def test_service_bounds_fall_back_to_the_derivation():
    scenario = toy_course()
    bounds = derived.service_bounds(scenario)
    assert set(bounds) == {a.name for a in scenario.areas}
    assert all(1 <= lo <= hi for lo, hi in bounds.values())


def test_service_bounds_honour_an_explicit_override():
    scenario = toy_course()
    data = scenario.model_dump()
    target = scenario.areas[0].name
    data["areas"][0]["min_services"] = 2
    data["areas"][0]["max_services"] = 99
    edited = type(scenario).model_validate(data)
    lo, hi = derived.service_bounds(edited)[target]
    assert (lo, hi) == (2, 99)
    # the other areas still come from the derivation
    other = scenario.areas[1].name
    assert derived.service_bounds(edited)[other] == derived.service_bounds(scenario)[other]
