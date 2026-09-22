"""User schedule edits -> clingcon preference facts (ADR-0031, ADR-0032).

Pure: no solver runs here. The facts these produce are consumed by the overlay
encodings; whether the *solver* honours them is ``tests/test_encoding_contract.py``
and ``tests/test_end_to_end.py``.
"""

import pytest

from fleetplanning.model import (
    DAYS,
    Area,
    DaySchedule,
    Mower,
    PreferredTask,
    Scenario,
    ScheduledTask,
    ServiceEvent,
    SolvePreferences,
)
from fleetplanning.solver.preferences import preference_agreement, render_preferences


def _scenario(*, no_go=None, history_completion=-1) -> Scenario:
    """Two areas, two mowers, one of which cannot service area B."""
    day = DaySchedule(no_go=no_go or [])
    return Scenario(
        name="tiny",
        areas=[
            Area(
                name="A",
                type="fairway",
                hole=1,
                priority=1,
                min_interval=10,
                max_interval=20,
                schedule={d: day for d in DAYS},
            ),
            Area(
                name="B",
                type="semirough_A",
                hole=1,
                priority=2,
                min_interval=10,
                max_interval=20,
                schedule={d: DaySchedule() for d in DAYS},
            ),
        ],
        mowers=[Mower(name="M1", can_mow=["A", "B"]), Mower(name="M2", can_mow=["A"])],
        history=[
            ServiceEvent(area="A", mower="M1", start=-5, completion=history_completion),
            ServiceEvent(area="B", mower="M1", start=-5, completion=-1),
        ],
        horizon_hours=24,
        horizon_start_hour=0,
    )


def _prefs(*tasks, mode="weak", level="top") -> SolvePreferences:
    return SolvePreferences(tasks=list(tasks), mode=mode, level=level)


# --- the "changes nothing" cases -------------------------------------------------------


@pytest.mark.parametrize(
    "prefs",
    [
        None,
        SolvePreferences(),  # mode defaults to "off"
        _prefs(PreferredTask(area="A", start=3, mower="M1"), mode="off"),
        _prefs(mode="weak"),  # active mode, but nothing to say
    ],
)
def test_nothing_to_express_renders_an_empty_program(prefs):
    """The ADR-0031 invariant at the emitter: no facts at all, not a comment banner.

    A non-empty string here would be added to the solver and would change the
    program (and so the goldens) for every preference-free solve.
    """
    rendered = render_preferences(prefs, _scenario())
    assert rendered.text == ""
    assert rendered.dropped == []


# --- fact rendering ---------------------------------------------------------------------


def test_a_preference_renders_both_halves_as_quoted_facts():
    rendered = render_preferences(
        _prefs(PreferredTask(area="A", start=3, mower="M2")), _scenario()
    )
    assert 'pref_time("A",3).' in rendered.text
    assert 'pref_mower("A",3,"M2").' in rendered.text
    assert rendered.dropped == []


def test_a_time_only_preference_renders_no_mower_fact():
    """`mower=None` is 'keep the hour, any mower' (ADR-0031)."""
    rendered = render_preferences(_prefs(PreferredTask(area="A", start=3)), _scenario())
    assert 'pref_time("A",3).' in rendered.text
    assert "pref_mower(" not in rendered.text


def test_duplicate_preferences_are_emitted_once():
    rendered = render_preferences(
        _prefs(
            PreferredTask(area="A", start=3, mower="M1"),
            PreferredTask(area="A", start=3, mower="M1", origin="frozen"),
        ),
        _scenario(),
    )
    assert rendered.text.count('pref_time("A",3).') == 1
    assert rendered.text.count('pref_mower("A",3,"M1").') == 1


# --- dropping what the solver could never satisfy ---------------------------------------


def test_an_incapable_mower_drops_only_the_mower_half():
    """M2 cannot service B. The hour is still a perfectly good preference."""
    rendered = render_preferences(
        _prefs(PreferredTask(area="B", start=3, mower="M2")), _scenario()
    )
    assert 'pref_time("B",3).' in rendered.text
    assert "pref_mower(" not in rendered.text
    assert [(d.half, d.area, d.start) for d in rendered.dropped] == [("mower", "B", 3)]
    assert "M2" in rendered.dropped[0].reason


def test_a_start_inside_a_no_go_window_drops_both_halves():
    """No `completion/5` row supports that hour, so no task can ever start there."""
    scenario = _scenario(no_go=[(0, 6)])  # hours 0..5 of every day
    rendered = render_preferences(
        _prefs(PreferredTask(area="A", start=2, mower="M1")), scenario
    )
    assert rendered.text == ""
    assert sorted(d.half for d in rendered.dropped) == ["mower", "time"]


def test_a_start_censored_by_an_in_progress_service_is_dropped():
    """`instance.py` omits rows before an in-progress service finishes, so a preference
    there has no support in the program the solver actually sees.
    """
    scenario = _scenario(history_completion=8)
    early = render_preferences(_prefs(PreferredTask(area="A", start=3)), scenario)
    late = render_preferences(_prefs(PreferredTask(area="A", start=9)), scenario)
    assert early.text == ""
    assert [d.reason for d in early.dropped] != []
    assert 'pref_time("A",9).' in late.text


def test_a_start_before_the_mower_is_free_elsewhere_is_dropped():
    """M1 is still finishing area A at hour 8, so it cannot start area B before then —
    even though area B itself has no in-progress service (ADR-0040)."""
    scenario = _scenario(history_completion=8)  # M1 busy until 8
    early = render_preferences(_prefs(PreferredTask(area="B", start=3, mower="M1")), scenario)
    late = render_preferences(_prefs(PreferredTask(area="B", start=9, mower="M1")), scenario)
    assert early.text == ""
    assert sorted(d.half for d in early.dropped) == ["mower", "time"]
    assert 'pref_time("B",9).' in late.text
    assert 'pref_mower("B",9,"M1").' in late.text


def test_an_unknown_area_is_dropped_rather_than_raising():
    rendered = render_preferences(
        _prefs(PreferredTask(area="nope", start=3, mower="M1")), _scenario()
    )
    assert rendered.text == ""
    assert all(d.area == "nope" for d in rendered.dropped)


# --- agreement --------------------------------------------------------------------------


def _task(area, index, mower, start):
    return ScheduledTask(area=area, task=index, mower=mower, start=start, end=start + 3)


def test_agreement_counts_the_two_halves_separately():
    prefs = _prefs(
        PreferredTask(area="A", start=3, mower="M1"),  # kept whole
        PreferredTask(area="A", start=9, mower="M2"),  # right hour, wrong mower
        PreferredTask(area="B", start=4, mower="M1"),  # gone entirely
    )
    tasks = [_task("A", 1, "M1", 3), _task("A", 2, "M1", 9), _task("B", 1, "M1", 20)]

    agreement = preference_agreement(prefs, tasks)
    assert agreement.total == 3
    assert agreement.time_kept == 2
    assert agreement.mower_kept == 1


def test_agreement_ignores_the_task_index_entirely():
    """The whole point of ADR-0031: renumbering must not change the verdict."""
    prefs = _prefs(PreferredTask(area="A", start=9, mower="M1"))
    assert preference_agreement(prefs, [_task("A", 1, "M1", 9)]).mower_kept == 1
    assert preference_agreement(prefs, [_task("A", 7, "M1", 9)]).mower_kept == 1


def test_agreement_splits_by_origin():
    prefs = _prefs(
        PreferredTask(area="A", start=3, mower="M1", origin="frozen"),
        PreferredTask(area="A", start=9, mower="M1", origin="frozen"),
        PreferredTask(area="B", start=4, mower="M1", origin="edited"),
    )
    tasks = [_task("A", 1, "M1", 3), _task("B", 1, "M1", 4)]

    agreement = preference_agreement(prefs, tasks)
    assert agreement.by_origin["frozen"].total == 2
    assert agreement.by_origin["frozen"].mower_kept == 1
    assert agreement.by_origin["edited"].total == 1
    assert agreement.by_origin["edited"].mower_kept == 1


def test_a_time_only_preference_never_counts_toward_the_mower_half():
    prefs = _prefs(PreferredTask(area="A", start=3))
    agreement = preference_agreement(prefs, [_task("A", 1, "M1", 3)])
    assert agreement.time_kept == 1
    assert agreement.mower_kept == 0
    assert agreement.mower_total == 0  # nothing was asked of the mower
