"""Saving and deleting curated scenarios (ADR-0026, ``registry.save`` / ``registry.delete``).

Every test runs against ``isolated_library`` (conftest) — a throwaway copy of ``curated/``
— so nothing here touches the scenarios that ship in git.
"""

from __future__ import annotations

import pytest

from fleetplanning.model import Scenario
from fleetplanning.scenarios import registry


@pytest.fixture
def sample_scenario(isolated_library) -> Scenario:
    scenario, _ = registry.load("well-resourced")
    return scenario


@pytest.mark.parametrize(
    "text,expected",
    [
        ("My New Course", "my-new-course"),
        ("  spaced  out  ", "spaced-out"),
        ("Hole #3 (back nine)", "hole-3-back-nine"),
        ("already-a-slug", "already-a-slug"),
        ("UPPER_case__mix", "upper-case-mix"),
    ],
)
def test_slugify(text, expected):
    assert registry.slugify(text) == expected


@pytest.mark.parametrize("text", ["", "  ", "!!!", "---", "..", "/"])
def test_slugify_rejects_empty_result(text):
    with pytest.raises(registry.InvalidSlug):
        registry.slugify(text)


@pytest.mark.parametrize(
    "slug", ["Bad", "with space", "../escape", "a/b", "-lead", "trail-", "x" * 65]
)
def test_save_rejects_a_bad_slug(sample_scenario, slug):
    with pytest.raises(registry.InvalidSlug):
        registry.save(slug, sample_scenario, overwrite=False)


def test_save_new_scenario_appears_in_the_library(sample_scenario, isolated_library):
    assert "my-copy" not in registry.scenario_ids()
    registry.save("my-copy", sample_scenario, overwrite=False)
    assert "my-copy" in registry.scenario_ids()
    loaded, source = registry.load("my-copy")
    assert loaded.name == "my-copy"  # forced to the slug
    assert source is None  # no generator provenance for a saved scenario
    assert (isolated_library / "my-copy.json").read_text().endswith("\n")


def test_save_without_overwrite_refuses_an_existing_slug(sample_scenario):
    with pytest.raises(registry.ScenarioExists):
        registry.save("well-resourced", sample_scenario, overwrite=False)


def test_save_with_overwrite_replaces_the_file(sample_scenario):
    edited = sample_scenario.model_copy(update={"horizon_start_hour": 7})
    registry.save("well-resourced", edited, overwrite=True)
    reloaded, _ = registry.load("well-resourced")
    assert reloaded.horizon_start_hour == 7
    assert reloaded.name == "well-resourced"


def test_delete_removes_the_scenario_and_its_source(isolated_library):
    assert (isolated_library / "well-resourced.source.json").is_file()
    registry.delete("well-resourced")
    assert "well-resourced" not in registry.scenario_ids()
    assert not (isolated_library / "well-resourced.json").exists()
    assert not (isolated_library / "well-resourced.source.json").exists()


def test_delete_unknown_slug_raises(isolated_library):
    with pytest.raises(registry.ScenarioNotFound):
        registry.delete("no-such-scenario")


def test_delete_bad_slug_raises_invalid(isolated_library):
    with pytest.raises(registry.InvalidSlug):
        registry.delete("../etc/passwd")


def test_resaving_an_unchanged_scenario_is_a_no_op_diff(isolated_library):
    """``save`` writes the same ``exclude_none`` layout scripts/promote_curated.py used,
    so re-saving a loaded scenario byte-for-byte reproduces the file."""
    before = (isolated_library / "well-resourced.json").read_text()
    scenario, _ = registry.load("well-resourced")
    registry.save("well-resourced", scenario, overwrite=True)
    assert (isolated_library / "well-resourced.json").read_text() == before


def test_round_trip_save_then_load(sample_scenario):
    registry.save("round-trip", sample_scenario, overwrite=False)
    reloaded, _ = registry.load("round-trip")
    # everything the solver reads survives the JSON round-trip
    assert [a.name for a in reloaded.areas] == [a.name for a in sample_scenario.areas]
    assert reloaded.base_durations == sample_scenario.base_durations
