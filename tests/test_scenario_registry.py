"""The curated demo scenario library (ADR-0019)."""

from __future__ import annotations

import pytest

from fleetplanning import derived
from fleetplanning.generator.source import ScenarioSource
from fleetplanning.model import Scenario
from fleetplanning.scenarios import registry


def test_library_is_non_empty_and_sorted():
    ids = registry.scenario_ids()
    assert len(ids) >= 5
    assert ids == sorted(ids)


@pytest.mark.parametrize("slug", registry.scenario_ids())
def test_every_curated_file_parses_as_a_valid_scenario(slug):
    scenario, source = registry.load(slug)
    assert isinstance(scenario, Scenario)
    # the inner name matches the filename slug (set when the library was built)
    assert scenario.name == slug
    assert source is None or isinstance(source, ScenarioSource)


@pytest.mark.parametrize("slug", registry.scenario_ids())
def test_curated_scenarios_are_self_describing(slug):
    """Since ADR-0024 every library scenario carries size_m2 + mower model/rate (the
    `promote()` step), so the app never needs the source."""
    scenario, _ = registry.load(slug)
    assert all(a.size_m2 is not None and a.size_m2 > 0 for a in scenario.areas)
    assert all(m.model and m.area_capacity_m2_per_day for m in scenario.mowers)


def test_generated_scenarios_carry_a_source_and_base_durations():
    for slug in registry.scenario_ids():
        scenario, source = registry.load(slug)
        if source is None:
            continue  # hand-authored or UI-saved (ADR-0026) — no generator provenance
        assert scenario.base_durations is not None  # data-driven durations (ADR-0013)
        # source areas/mowers line up with the compiled scenario
        assert {a.name for a in source.areas} == {a.name for a in scenario.areas}
        assert {m.name for m in source.mowers} == {m.name for m in scenario.mowers}


def test_unknown_slug_raises():
    with pytest.raises(registry.ScenarioNotFound):
        registry.load("no-such-scenario")


@pytest.mark.parametrize("slug", registry.scenario_ids())
def test_service_bounds_cover_every_area(slug):
    scenario, _ = registry.load(slug)
    bounds = derived.service_bounds(scenario)
    assert set(bounds) == {a.name for a in scenario.areas}
    assert all(0 <= lo <= hi for lo, hi in bounds.values())
