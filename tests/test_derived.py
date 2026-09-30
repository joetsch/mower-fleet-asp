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


def test_explicit_min_alone_above_the_derived_maximum_wins():
    # "Drop a service" (lib/scenarioEdits.ts's dropOneService) sets only max_services, one
    # below the plan's current count. If the derivation's own minimum is higher, an explicit
    # cap must still win -- otherwise the dropped service silently comes back (owner report,
    # 2026-09-24: "Drop to 1" on an area derived at (2, 4) produced (2, 2), not (1, 1)).
    #
    # The derived floor is now always <= 1 (bounds.py's min_starts relaxation), and
    # min_services/max_services are both floored at 1 by the model (ge=1), so that
    # particular direction -- an explicit max alone landing below the derived min -- can no
    # longer arise: the derived min it would have to undercut is at most 1, same as the
    # smallest max_services allowed. What's still live is the symmetric case this same
    # "whichever bound the caller did NOT set explicitly yields" logic also has to handle:
    # an explicit min alone landing *above* the derived max.
    scenario = toy_course()
    target = scenario.areas[0].name
    _, derived_hi = derived.service_bounds(scenario)[target]
    data = scenario.model_dump()
    data["areas"][0]["min_services"] = derived_hi + 5
    edited = type(scenario).model_validate(data)
    assert derived.service_bounds(edited)[target] == (derived_hi + 5, derived_hi + 5)
