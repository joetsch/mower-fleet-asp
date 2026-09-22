"""Tier 2 — assumption-based counterfactual refutation, tested directly (not through tier
1, which is `test_explain_static.py`'s job) against a small, fully hand-built scenario so
every conflict shape is known in advance rather than sampled.

One mower ``M`` services areas ``A`` and ``D``; a second, unrelated mower ``N`` services
``C``. Every area needs exactly one service this week (``min_services = max_services =
1``), each taking exactly 10 hours (``base_durations``, no no-go/avoid windows), so a
task's interval is simply ``[start, start + 10)`` — arithmetic anyone can check by hand,
and the scenario's *only* possible source of UNSAT is the no-overlap-per-mower
constraint, which is exactly what this module exists to explain.
"""

from __future__ import annotations

import time

import clingo
import pytest

from fleetplanning.explain.counterfactual import (
    GroundedExplain,
    _minimise,
    explain_edit,
    ground_for_explanation,
)
from fleetplanning.model import (
    Area,
    BaseDuration,
    DaySchedule,
    Mower,
    PreferredTask,
    Scenario,
    SolvePreferences,
    history_event,
)
from fleetplanning.service import DETERMINISTIC_CONFIG, encoding_text
from fleetplanning.solver.completion_table import build_completion_table
from fleetplanning.solver.runner import ClingconSolver

pytestmark = pytest.mark.slow


def _tiny_scenario() -> Scenario:
    week = {day: DaySchedule() for day in ("Monday", "Tuesday", "Wednesday", "Thursday",
                                            "Friday", "Saturday", "Sunday")}

    def area(name: str) -> Area:
        return Area(
            name=name, type="test", hole=1, priority=1,
            min_interval=1, max_interval=200, schedule=week,
            min_services=1, max_services=1,
        )

    areas = [area("A"), area("D"), area("C")]
    mowers = [Mower(name="M", can_mow=["A", "D"]), Mower(name="N", can_mow=["C"])]
    history = [
        history_event("A", "M", 10, completion=-1000),
        history_event("D", "M", 10, completion=-1000),
        history_event("C", "N", 10, completion=-1000),
    ]
    base_durations = [
        BaseDuration(area="A", mower="M", hours=10),
        BaseDuration(area="D", mower="M", hours=10),
        BaseDuration(area="C", mower="N", hours=10),
    ]
    return Scenario(
        name="tiny-explain-test", areas=areas, mowers=mowers, history=history,
        horizon_hours=100, base_durations=base_durations,
    )


def _ground(scenario, tasks):
    """Ground for every atom named in ``tasks`` -- kept *and* the edit alike, matching
    how ``explain_scenario`` grounds against the full submitted preference set."""
    rows = build_completion_table(scenario)
    preferences = SolvePreferences(mode="weak", level="top", tasks=list(tasks))
    return ground_for_explanation(scenario, rows, preferences)


def test_individually_impossible_when_the_atom_never_grounds():
    """No completion row exists past the horizon -- the atom simply never grounds
    (Stage 0's go/no-go case), which is the cheap, common form of "impossible"."""
    scenario = _tiny_scenario()
    kept = [PreferredTask(area="A", start=0, mower="M", origin="frozen")]
    edit = PreferredTask(area="D", start=99999, mower="M", origin="edited")
    g = _ground(scenario, [*kept, edit])
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() + 10.0)
    assert result.outcome == "individually_impossible"
    assert result.solve_time_s is None


def test_not_yet_found_when_the_edit_does_not_actually_overlap_anything_kept():
    scenario = _tiny_scenario()
    kept = [PreferredTask(area="A", start=0, mower="M", origin="frozen")]
    edit = PreferredTask(area="D", start=20, mower="M", origin="edited")  # [20,30) vs [0,10)
    g = _ground(scenario, [*kept, edit])
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() + 10.0)
    assert result.outcome == "not_yet_found"
    assert result.conflicts == []
    assert result.solve_time_s is not None


def test_conflicts_with_a_genuinely_overlapping_kept_task():
    scenario = _tiny_scenario()
    kept = [PreferredTask(area="A", start=0, mower="M", origin="frozen")]
    edit = PreferredTask(area="D", start=5, mower="M", origin="edited")  # [5,15) vs [0,10): overlap
    g = _ground(scenario, [*kept, edit])
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() + 10.0)
    assert result.outcome == "conflicts_with"
    assert result.minimal is True
    assert [(c.area, c.start, c.mower) for c in result.conflicts] == [("A", 0, "M")]


def test_minimisation_drops_a_kept_task_that_is_not_actually_the_cause():
    """kept = {A@0 (conflicts), C@1000 (irrelevant: different mower entirely)} -- the raw
    assumption set names both, minimisation must reduce to just A."""
    scenario = _tiny_scenario()
    kept = [
        PreferredTask(area="A", start=0, mower="M", origin="frozen"),
        PreferredTask(area="C", start=0, mower="N", origin="frozen"),
    ]
    edit = PreferredTask(area="D", start=5, mower="M", origin="edited")
    g = _ground(scenario, [*kept, edit])
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() + 10.0)
    assert result.outcome == "conflicts_with"
    assert result.minimal is True
    assert [(c.area, c.mower) for c in result.conflicts] == [("A", "M")]


def test_a_deadline_already_past_returns_not_determined_without_solving():
    scenario = _tiny_scenario()
    kept = [PreferredTask(area="A", start=0, mower="M", origin="frozen")]
    edit = PreferredTask(area="D", start=5, mower="M", origin="edited")
    g = _ground(scenario, [*kept, edit])
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() - 1.0)
    assert result.outcome == "not_determined"


def test_minimise_reports_minimal_false_when_its_own_deadline_is_already_past():
    """The keep-on-timeout rule, isolated: an already-expired deadline must make
    minimisation stop immediately and report the *unreduced* set as non-minimal --
    correct-but-not-smallest, never wrong."""
    scenario = _tiny_scenario()
    kept = [
        PreferredTask(area="A", start=0, mower="M", origin="frozen"),
        PreferredTask(area="C", start=0, mower="N", origin="frozen"),
    ]
    edit = PreferredTask(area="D", start=5, mower="M", origin="edited")
    g = _ground(scenario, [*kept, edit])
    edit_lits = g.time_lit[("D", 5)], g.mower_lit[("D", 5, "M")]
    kept_by_name = {
        "A": [g.time_lit[("A", 0)], g.mower_lit[("A", 0, "M")]],
        "C": [g.time_lit[("C", 0)], g.mower_lit[("C", 0, "N")]],
    }
    remaining, hit_budget, _elapsed = _minimise(
        g, list(edit_lits), kept_by_name, step_budget_s=2.0, deadline=time.perf_counter() - 1.0
    )
    assert hit_budget is True
    assert remaining == {"A", "C"}  # nothing removed -- never claims minimality it didn't prove


# Two areas, two mowers, the completion table spelled out row by row. Area a needs two
# services; on m it can only start at 0, 10 or 40, on n only at 70. Area b (n only) can
# start at 0 or 50.
_MEDIATED_FACTS = """
area("a"). area("b"). mower("m"). mower("n").
priority("a",1). priority("b",1).
min_interval("a",1). max_interval("a",200). min_interval("b",1). max_interval("b",200).
capable("m","a"). capable("n","a"). capable("n","b").
min_starts("a",2). max_starts("a",2). min_starts("b",1). max_starts("b",1).
completion("a","m",0,30,0). completion("a","m",10,60,0). completion("a","m",40,70,0).
completion("a","n",70,100,0).
completion("b","n",0,20,0). completion("b","n",50,80,0).
pref_time("a",10). pref_mower("a",10,"m").
pref_time("b",50). pref_mower("b",50,"n").
"""


def test_a_conflict_through_an_unpinned_task_is_not_missed_by_the_local_pruning():
    """Regression (2026-09-15, ADR-0047 amendment). The edit e = a@10 on m and the kept
    edit k = b@50 on n share neither area nor mower, yet cannot both hold: once e runs
    10-60 on m, a's second (unpinned) service can only go on n at 70-100, across k's
    50-80. The one-hop pruning drops k, so a satisfiable pruned check alone must not be
    reported as "could have been kept" -- before the fix it was (``not_yet_found``)."""
    solver = ClingconSolver(
        ["-t1", f"--configuration={DETERMINISTIC_CONFIG}", "--opt-mode=ignore",
         "-c", "pref_level=6"]
    )
    solver.add(encoding_text())
    solver.add(encoding_text("preferences_weak.lp"))
    solver.add(_MEDIATED_FACTS)
    solver.ground()

    def lit(name: str, *args: str | int) -> int:
        terms = [clingo.String(a) if isinstance(a, str) else clingo.Number(a) for a in args]
        found = solver.literal_for(clingo.Function(name, terms))
        assert found is not None
        return found

    g = GroundedExplain(
        solver=solver,
        time_lit={("a", 10): lit("pref_time_met", "a", 10),
                  ("b", 50): lit("pref_time_met", "b", 50)},
        mower_lit={("a", 10, "m"): lit("pref_mower_met", "a", 10, "m"),
                   ("b", 50, "n"): lit("pref_mower_met", "b", 50, "n")},
    )
    edit = PreferredTask(area="a", start=10, mower="m", origin="edited")
    kept = [PreferredTask(area="b", start=50, mower="n", origin="frozen")]
    result = explain_edit(edit, kept, g, deadline=time.perf_counter() + 10.0)
    assert result.outcome == "conflicts_with"
    assert result.minimal is True
    assert [(c.area, c.start, c.mower) for c in result.conflicts] == [("b", 50, "n")]
