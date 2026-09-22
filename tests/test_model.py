"""Model-level tests.

Two kinds:

* **characterization** — pin what the pydantic models accept and default;
* **validation** — the sanity checks ported + extended from the notebook's cell 19
  (ADR-0010). Each builds an invalid scenario and expects a ``ValidationError``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from fleetplanning.model import (
    DAYS,
    Area,
    DaySchedule,
    Mower,
    Scenario,
    ServiceEvent,
    _covered_hours,
)

from .conftest import GOLDEN_DIR


def _area(**overrides) -> dict:
    base = dict(
        name="A",
        type="fairway",
        hole=1,
        priority=1,
        min_interval=18,
        max_interval=24,
        schedule={d: DaySchedule() for d in DAYS},
    )
    base.update(overrides)
    return base


def _scenario(**overrides) -> dict:
    base = dict(
        name="s",
        areas=[Area(**_area())],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-5, completion=-1)],
    )
    base.update(overrides)
    return base


# --- characterization -------------------------------------------------------------------


def test_scenario_defaults():
    s = Scenario(**_scenario())
    assert s.horizon_hours == 168
    assert s.horizon_start_hour == 13
    assert s.duration_seed == 0
    assert s.areas[0].schedule["Monday"].no_go == []


def test_scenario_round_trips_through_dump_and_validate():
    s = Scenario(**_scenario(name="rt"))
    assert Scenario.model_validate(s.model_dump()) == s


def test_an_empty_scenario_is_valid():
    """The base case the from-scratch editor rests on (ADR-0027): all three lists empty."""
    s = Scenario(name="blank", areas=[], mowers=[], history=[])
    assert s.areas == [] and s.mowers == [] and s.history == []


def test_a_mower_with_no_areas_is_valid():
    """Reachable via the editor's deleteArea (ADR-0027) — an idle mower is legal."""
    scen = _scenario(mowers=[Mower(name="M", can_mow=["A"]), Mower(name="Idle", can_mow=[])])
    assert any(m.can_mow == [] for m in Scenario(**scen).mowers)


def test_unknown_fields_are_ignored_on_parse():
    """Current behaviour: no ``model_config`` -> pydantic silently drops extras."""
    m = Mower.model_validate({"name": "M", "can_mow": ["A"], "speed_kmh": 4})
    assert not hasattr(m, "speed_kmh")


def test_covered_hours_expands_intervals_and_wraps_midnight():
    assert _covered_hours([(13, 20)]) == set(range(13, 20))
    assert _covered_hours([(22, 6)]) == {22, 23, 0, 1, 2, 3, 4, 5}
    assert _covered_hours([(0, 12), (12, 23)]) == set(range(0, 23))


# --- validation (ADR-0010) ------------------------------------------------------------


def test_interval_min_must_not_exceed_max():
    with pytest.raises(ValidationError, match="min_interval"):
        Area(**_area(min_interval=30, max_interval=24))


def test_intervals_must_be_positive():
    with pytest.raises(ValidationError):
        Area(**_area(min_interval=0))


def test_priority_must_be_in_range_1_to_3():
    # Capped at 3 (ADR-0012): level 5-P must stay above the avoid (@1) and min (@0) levels.
    with pytest.raises(ValidationError):
        Area(**_area(priority=9))
    with pytest.raises(ValidationError):
        Area(**_area(priority=4))
    with pytest.raises(ValidationError):
        Area(**_area(priority=0))


def test_schedule_must_cover_all_seven_weekdays():
    with pytest.raises(ValidationError, match="weekday keys"):
        Area(**_area(schedule={d: DaySchedule() for d in DAYS[:5]}))


def test_schedule_weekday_keys_must_be_spelled_exactly():
    bad = {d: DaySchedule() for d in DAYS[1:]}
    bad["monday"] = DaySchedule()  # wrong case
    with pytest.raises(ValidationError):
        Area(**_area(schedule=bad))


def test_no_go_may_not_cover_a_whole_day():
    full_day = {d: DaySchedule() for d in DAYS}
    full_day["Wednesday"] = DaySchedule(no_go=[(0, 12), (12, 23), (23, 0)])
    with pytest.raises(ValidationError, match="whole day"):
        Area(**_area(schedule=full_day))


def test_zero_width_interval_is_rejected():
    with pytest.raises(ValidationError, match="zero-width"):
        DaySchedule(no_go=[(5, 5)])


def test_interval_hours_must_be_within_0_23():
    with pytest.raises(ValidationError, match="outside 0..23"):
        DaySchedule(avoid=[(10, 25)])


def test_service_event_completion_must_not_precede_start():
    with pytest.raises(ValidationError, match="precedes start"):
        ServiceEvent(area="A", mower="M", start=0, completion=-3)


def test_horizon_hours_must_be_positive():
    with pytest.raises(ValidationError):
        Scenario(**_scenario(horizon_hours=0))


def test_area_names_must_be_unique():
    dup = [Area(**_area(name="A")), Area(**_area(name="A"))]
    with pytest.raises(ValidationError, match="area names must be unique"):
        Scenario(**_scenario(areas=dup, mowers=[Mower(name="M", can_mow=["A"])]))


def test_mower_names_must_be_unique():
    dup = [Mower(name="M", can_mow=["A"]), Mower(name="M", can_mow=["A"])]
    with pytest.raises(ValidationError, match="mower names must be unique"):
        Scenario(**_scenario(mowers=dup))


def test_can_mow_must_reference_existing_areas():
    with pytest.raises(ValidationError, match="unknown areas"):
        Scenario(**_scenario(mowers=[Mower(name="M", can_mow=["A", "Ghost"])]))


def test_history_must_reference_an_existing_mower():
    bad = [ServiceEvent(area="A", mower="Ghost", start=-5, completion=-1)]
    with pytest.raises(ValidationError, match="unknown mower"):
        Scenario(**_scenario(history=bad))


def test_history_must_reference_an_existing_area():
    bad = [ServiceEvent(area="Ghost", mower="M", start=-5, completion=-1)]
    with pytest.raises(ValidationError, match="unknown area"):
        Scenario(**_scenario(history=bad))


def test_history_mower_must_be_capable_of_the_area():
    scen = _scenario(
        areas=[Area(**_area(name="A")), Area(**_area(name="B"))],
        mowers=[Mower(name="M", can_mow=["A"])],  # M cannot mow B
        history=[
            ServiceEvent(area="A", mower="M", start=-5, completion=-1),
            ServiceEvent(area="B", mower="M", start=-5, completion=-1),
        ],
    )
    with pytest.raises(ValidationError, match="cannot service area"):
        Scenario(**scen)


def test_history_must_have_at_most_one_event_per_area():
    dup = [
        ServiceEvent(area="A", mower="M", start=-10, completion=-6),
        ServiceEvent(area="A", mower="M", start=-5, completion=-1),
    ]
    with pytest.raises(ValidationError, match="more than one event"):
        Scenario(**_scenario(history=dup))


def test_every_area_must_have_a_history_event():
    scen = _scenario(
        areas=[Area(**_area(name="A")), Area(**_area(name="B"))],
        mowers=[Mower(name="M", can_mow=["A", "B"])],
        history=[ServiceEvent(area="A", mower="M", start=-5, completion=-1)],
    )
    with pytest.raises(ValidationError, match="missing for areas"):
        Scenario(**scen)


# --- frontend <-> backend mirror (docs/known-hazards.md, "Refactor before Iteration 5" #4)


def _is_valid_scenario(payload: dict) -> bool:
    try:
        Scenario.model_validate(payload)
    except ValidationError:
        return False
    return True


def test_edit_helper_outputs_are_valid_scenarios():
    """Every scenario the frontend's ``scenarioEdits.ts`` structural-edit helpers build
    must satisfy the pydantic ``Scenario`` ruleset — otherwise the editor constructs
    drafts the server rejects on Save.

    The golden is emitted from the *real* TS helpers by
    ``frontend/src/lib/mirror.golden.test.ts``. Regenerate on purpose with
    ``cd frontend && UPDATE_GOLDEN=1 npm run test`` and review the diff.
    """
    golden = json.loads((GOLDEN_DIR / "edit_helper_outputs.json").read_text())
    cases = golden["cases"]
    assert cases, "golden is empty — regenerate it"
    bad = [c["call"] for c in cases if not _is_valid_scenario(c["output"])]
    assert not bad, f"scenarioEdits.ts built scenarios pydantic rejects: {bad}"


def test_client_validation_verdicts_match_pydantic():
    """``scenarioValidation.ts`` is a hand mirror of ``model.py``. This pins that the two
    agree on accept/reject over a table of scenarios exercising the shared rules — a
    rule that moves server-side without the mirror following trips this.

    Regenerate with ``cd frontend && UPDATE_GOLDEN=1 npm run test``.
    """
    golden = json.loads((GOLDEN_DIR / "edit_validation_table.json").read_text())
    cases = golden["cases"]
    assert cases, "golden is empty — regenerate it"
    disagreements = [
        c["note"]
        for c in cases
        if (c["verdict"] == "accept") != _is_valid_scenario(c["scenario"])
    ]
    assert not disagreements, (
        "scenarioValidation.ts and pydantic disagree on: " + ", ".join(disagreements)
    )
