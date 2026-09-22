"""Tests for the orchestration in ``service.py``.

The pure helpers (``control_args``, ``solve_result_from``) are tested here without the
solver. One ``slow`` test checks the reproducibility guarantee the docstring makes for
``threads=1``.
"""

from __future__ import annotations

import json

import clingo
import pytest

from fleetplanning.model import PreferredTask, SolvePreferences
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.service import (
    control_args,
    prepare_preferences,
    solve_result_from,
    solve_scenario,
    solver_info,
)
from fleetplanning.solver.completion_table import build_completion_table
from fleetplanning.solver.runner import ClingconResult

_INFO = solver_info(threads=4, time_limit_s=20.0)


def test_control_args_single_threaded_uses_the_deterministic_config(toy_scenario):
    # threads=1 runs under --configuration=jumpy, not clingo's fragile tweety default
    # (ADR-0041) — tweety can go modelless on this encoding.
    args = control_args(toy_scenario, threads=1)
    assert args == [
        "-t1",
        "--configuration=jumpy",
        "-c",
        f"horizon={toy_scenario.horizon_hours}",
    ]
    assert "--configuration=many" not in args


def test_control_args_multi_threaded_adds_the_portfolio(toy_scenario):
    args = control_args(toy_scenario, threads=4)
    assert "-t4" in args
    assert "--configuration=many" in args
    assert "-c" in args and f"horizon={toy_scenario.horizon_hours}" in args


def test_solver_info_single_threaded_reports_the_deterministic_config():
    info = solver_info(threads=1, time_limit_s=30.0)
    assert info.name == "clingcon"
    assert info.threads == 1
    assert info.config == "jumpy"  # ADR-0041
    assert info.args == ["-t1", "--configuration=jumpy"]
    assert info.time_limit_s == 30.0


def test_solver_info_multi_threaded_reports_many():
    info = solver_info(threads=4, time_limit_s=20.0)
    assert info.config == "many"
    assert info.args == ["-t4", "--configuration=many"]


# --- clingo_args override (ADR-0023: expert-mode editable command line) -----------------


def test_control_args_with_clingo_args_replaces_the_default_portfolio(toy_scenario):
    args = control_args(toy_scenario, threads=4, clingo_args=["-t1", "--stats"])
    assert args == ["-t1", "--stats", "-c", f"horizon={toy_scenario.horizon_hours}"]
    # threads=4 is ignored — the override wins wholesale, not merged with the default.
    assert "--configuration=many" not in args


def test_control_args_empty_clingo_args_list_is_still_an_override(toy_scenario):
    # An empty list is a deliberate "no portfolio flags at all", distinct from None
    # ("use the default"). The HTTP layer (api/schemas.py) rejects an empty list from the
    # client — this just pins that the two are not conflated here.
    args = control_args(toy_scenario, threads=4, clingo_args=[])
    assert args == ["-c", f"horizon={toy_scenario.horizon_hours}"]


def test_solver_info_with_clingo_args_parses_threads_and_config_from_the_args():
    info = solver_info(threads=4, time_limit_s=20.0, clingo_args=["-t2", "--stats"])
    assert info.threads == 2  # recovered from -t2, not the (ignored) threads=4 param
    assert info.config is None  # no --configuration= present
    assert info.args == ["-t2", "--stats"]


def test_solver_info_with_clingo_args_defaults_threads_to_one_when_absent():
    # Matches clingo's own default: -t omitted means single-threaded.
    info = solver_info(threads=4, time_limit_s=20.0, clingo_args=["--stats"])
    assert info.threads == 1
    assert info.config is None


def test_solver_info_with_clingo_args_recovers_a_custom_configuration():
    info = solver_info(threads=1, time_limit_s=20.0, clingo_args=["-t3", "--configuration=jumpy"])
    assert info.threads == 3
    assert info.config == "jumpy"


def test_solve_result_from_unsolved_has_no_schedule(toy_scenario):
    out = solve_result_from(
        toy_scenario, ClingconResult(status="unsatisfiable", solve_time_s=1.5), _INFO
    )
    assert out.solved is False
    assert out.schedule is None
    assert out.quality is None
    assert out.optimal is False
    assert out.status == "unsatisfiable"
    assert out.scenario_name == toy_scenario.name
    assert out.horizon_hours == toy_scenario.horizon_hours
    assert out.solve_time_s == 1.5
    assert out.solver == _INFO


def test_solve_result_from_optimal_maps_status_and_schedule(toy_scenario):
    raw = ClingconResult(
        status="optimal",
        atoms=[clingo.parse_term('schedule("Hole1_Fairway",1,"Mower A",5,9)')],
        cost=[0, 0, 0, 0, 1],
        solve_time_s=2.0,
    )
    out = solve_result_from(toy_scenario, raw, _INFO)
    assert out.solved is True
    assert out.optimal is True
    assert out.status == "optimal"
    assert out.schedule is not None
    assert out.schedule.tasks[0].area == "Hole1_Fairway"
    assert out.schedule.cost == [0, 0, 0, 0, 1]
    assert out.solver == _INFO


def test_solve_result_carries_the_fixed_5_slot_quality_vector(toy_scenario):
    """`quality` is score.py's instance-independent vector, not the raw clingcon cost —
    it is what a rolling horizon compares across rolls (ADR-0042)."""
    from fleetplanning.solver.score import score_schedule

    raw = ClingconResult(
        status="optimal",
        atoms=[clingo.parse_term('schedule("Hole1_Fairway",1,"Mower A",5,9)')],
        cost=[3, 1],  # a short, differently-shaped raw vector
        solve_time_s=2.0,
    )
    out = solve_result_from(toy_scenario, raw, _INFO)
    assert out.quality == score_schedule(toy_scenario, out.schedule.tasks).slots
    assert len(out.quality) == 5


def test_solve_result_from_best_so_far_is_not_optimal(toy_scenario):
    raw = ClingconResult(status="satisfiable", atoms=[], cost=[1])
    out = solve_result_from(toy_scenario, raw, _INFO)
    assert out.solved is True
    assert out.optimal is False
    assert out.status == "satisfiable"


@pytest.mark.slow
def test_single_threaded_solve_is_reproducible_and_matches_golden(check_golden):
    """``threads=1`` is deterministic (ADR-0007): it proves optimality and returns the
    exact schedule captured in the golden. A change here means either the encoding, the
    instance emitter, or the solver's behaviour moved — all worth a deliberate look.

    Regenerate on purpose with ``uv run pytest tests/test_service.py --update-golden``.
    """
    result = solve_scenario(toy_course(), time_limit_s=60.0, threads=1)
    assert result.solved
    assert result.optimal, "single-threaded solve should prove optimality within the budget"
    assert result.solver is not None
    assert result.solver.threads == 1 and result.solver.config == "jumpy"  # ADR-0041
    dumped = json.dumps(result.schedule.model_dump(), indent=2, sort_keys=True) + "\n"
    check_golden(dumped, "toy_course_schedule_t1.json")


@pytest.mark.slow
def test_clingo_args_override_with_the_default_portfolio_tokens_matches_threads_path(
    check_golden,
):
    """The expert-mode override (ADR-0023) is not a separate code path with its own
    behaviour — passing the same tokens the ``threads=1`` default would build must solve
    identically, down to the exact schedule. Reuses the ``threads=1`` golden rather than a
    new one: if this ever diverges from it, the override plumbing (not the solver) moved.

    The deterministic default is ``-t1 --configuration=jumpy`` (ADR-0041), so those are
    the tokens to pass — bare ``-t1`` would inherit clingo's ``tweety`` and can go
    modelless on this encoding.
    """
    result = solve_scenario(
        toy_course(), time_limit_s=60.0, clingo_args=["-t1", "--configuration=jumpy"]
    )
    assert result.solved and result.optimal
    assert result.solver is not None
    assert result.solver.threads == 1 and result.solver.config == "jumpy"
    # echoes exactly what was sent, not the default's shape
    assert result.solver.args == ["-t1", "--configuration=jumpy"]
    dumped = json.dumps(result.schedule.model_dump(), indent=2, sort_keys=True) + "\n"
    check_golden(dumped, "toy_course_schedule_t1.json")


def test_solve_scenario_raises_runtime_error_on_an_invalid_clingo_flag(toy_scenario):
    """clingo validates its own args at ``Control()`` construction time (confirmed by
    hand: no grounding/solving needed to trigger it), so a bad expert-mode override fails
    fast. ``api/app.py`` maps this ``RuntimeError`` to an HTTP 400.
    """
    with pytest.raises(RuntimeError):
        solve_scenario(toy_scenario, time_limit_s=5.0, clingo_args=["--definitely-not-a-flag"])


# --- the preference layer (ADR-0031, ADR-0032) ------------------------------------------


def _prefs(*tasks, mode="weak", level="top"):
    return SolvePreferences(tasks=list(tasks), mode=mode, level=level)


def _first_area_start(scenario):
    """A legal (area, mower, start) triple for the toy scenario."""
    rows = build_completion_table(scenario)
    row = next(r for r in rows if r.start >= 12)
    return row.area, row.mower, row.start


@pytest.mark.parametrize(
    "preferences",
    [
        None,
        SolvePreferences(),  # mode "off"
        SolvePreferences(mode="weak"),  # active, but no tasks
    ],
)
def test_inactive_preferences_leave_the_program_and_args_untouched(toy_scenario, preferences):
    """The ADR-0031 invariant, at the seam that matters.

    Every golden and contract test in the repo pins a property of the program built
    without preferences. If any of them silently gained a rule or a flag, they would all
    be asserting something else.
    """
    setup = prepare_preferences(toy_scenario, preferences)
    assert setup.programs == []
    assert setup.args == []
    assert control_args(toy_scenario, threads=1, extra_args=setup.args) == control_args(
        toy_scenario, threads=1
    )


def test_a_preference_whose_halves_are_all_dropped_stays_inactive(toy_scenario):
    """Nothing expressible means nothing added — not an empty overlay with no facts."""
    setup = prepare_preferences(
        toy_scenario, _prefs(PreferredTask(area="no-such-area", start=3, mower="Mower A"))
    )
    assert setup.programs == []
    assert setup.args == []
    assert setup.dropped != []  # ... but the user is still told why


def test_weak_mode_adds_the_weak_overlay_and_the_level_constant(toy_scenario):
    area, mower, start = _first_area_start(toy_scenario)
    task = PreferredTask(area=area, start=start, mower=mower)

    top = prepare_preferences(toy_scenario, _prefs(task, level="top"))
    tie = prepare_preferences(toy_scenario, _prefs(task, level="tiebreak"))

    assert any(":~ pref_time(" in p for p in top.programs)
    assert any(f'pref_time("{area}",{start}).' in p for p in top.programs)
    assert top.args == ["-c", "pref_level=6"]
    assert tie.args == ["-c", "pref_level=-1"]
    assert "--heuristic=Domain" not in top.args


def test_heuristic_mode_adds_the_heuristic_overlay_and_the_domain_flag(toy_scenario):
    """Without --heuristic=Domain clingo ignores #heuristic entirely, so the flag is not
    optional — the arm would silently degrade to a cold solve.
    """
    area, mower, start = _first_area_start(toy_scenario)
    setup = prepare_preferences(
        toy_scenario, _prefs(PreferredTask(area=area, start=start, mower=mower), mode="heuristic")
    )
    assert any("#heuristic" in p for p in setup.programs)
    assert "--heuristic=Domain" in setup.args
    assert not any(":~" in p for p in setup.programs)  # the objective stays untouched
    assert "pref_level" not in " ".join(setup.args)


def test_every_named_preference_level_maps_to_its_priority_constant(toy_scenario):
    """The stability settings are all ``-c pref_level=N`` read against the forward model's
    own weak-constraint levels — max-interval at ``5-P`` in {4,3,2}, avoid-zone at 1,
    min-interval at 0. So one churned task costs the same as one missed High-priority
    service at ``high``, as a Low-priority one at ``low``, as an hour worked in an avoid
    window at ``avoid``; ``top`` (6) outranks every one of them and ``tiebreak`` (-1) is
    below all of them. No ``.lp`` edit is involved: ``pref_level`` is a command-line
    constant the overlay already declares.
    """
    area, mower, start = _first_area_start(toy_scenario)
    task = PreferredTask(area=area, start=start, mower=mower)

    for name, constant in {"top": 6, "high": 4, "low": 2, "avoid": 1, "tiebreak": -1}.items():
        setup = prepare_preferences(toy_scenario, _prefs(task, level=name))
        assert setup.args == ["-c", f"pref_level={constant}"], name


def test_heuristic_mode_runs_at_the_bias_weight_the_pilot_chose(toy_scenario):
    """ADR-0034 decision 4 picked ``heur[1,true]``, but the shipped mode passed only
    ``--heuristic=Domain`` and so inherited ``preferences_heuristic.lp``'s own
    ``#const pref_w=10`` — i.e. ``heur[10,true]``, the *worse* pilot arm (0.839 vs 0.919
    agreement). The weight is pinned on the command line so the mode that ships is the
    mode that was measured.
    """
    area, mower, start = _first_area_start(toy_scenario)
    setup = prepare_preferences(
        toy_scenario, _prefs(PreferredTask(area=area, start=start, mower=mower), mode="heuristic")
    )
    assert setup.args == ["--heuristic=Domain", "-c", "pref_w=1"]


def test_the_preference_report_echoes_the_level_the_solve_ran_at(toy_scenario):
    """``schedule.cost``'s shape depends on where the preference weak constraints sit: at
    ``top`` they are a separate leading slot; at ``high``/``low``/``avoid`` they land on an
    existing service-quality level and are *summed into* that slot. A client cannot split
    the vector without knowing which, and the setting currently selected in the UI need not
    be the one the result on screen came from — so the result carries it.
    """
    raw = ClingconResult(
        status="optimal",
        atoms=[clingo.parse_term('schedule("Hole1_Fairway",1,"Mower A",5,9)')],
        cost=[0, 0, 0, 0, 1],
        solve_time_s=2.0,
    )
    area, mower, start = _first_area_start(toy_scenario)
    task = PreferredTask(area=area, start=start, mower=mower)

    weak = solve_result_from(toy_scenario, raw, _INFO, preferences=_prefs(task, level="high"))
    assert weak.preferences.level == "high"
    assert weak.preferences.pref_level == 4

    # The heuristic overlay leaves the objective alone, so there is no level to report.
    heur = solve_result_from(
        toy_scenario, raw, _INFO, preferences=_prefs(task, mode="heuristic", level="top")
    )
    assert heur.preferences.level is None
    assert heur.preferences.pref_level is None


def test_control_args_appends_preference_flags_before_the_horizon_constant(toy_scenario):
    """clingo errors on a redefined constant, so ordering around `-c horizon=` matters."""
    args = control_args(toy_scenario, threads=4, extra_args=["--heuristic=Domain"])
    assert args == [
        "-t4",
        "--configuration=many",
        "--heuristic=Domain",
        "-c",
        f"horizon={toy_scenario.horizon_hours}",
    ]
