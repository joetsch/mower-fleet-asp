"""End-to-end: solve the toy scenario with clingcon and sanity-check the schedule.

Marked ``slow`` — it runs the real solver (seconds). Run just this with:
    uv run pytest -m slow
or skip it with:
    uv run pytest -m "not slow"
"""

import pytest

from fleetplanning.model import PreferredTask, SolvePreferences
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.service import solve_scenario
from fleetplanning.solver.bounds import service_count_bounds
from fleetplanning.solver.completion_table import build_completion_table

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def result():
    return solve_scenario(toy_course(), time_limit_s=25.0)


def test_it_finds_a_schedule(result):
    assert result.solved
    assert result.schedule is not None


def test_task_counts_are_within_the_interval_bounds(result):
    scenario = toy_course()
    bounds = service_count_bounds(scenario)
    per_area: dict[str, int] = {}
    for task in result.schedule.tasks:
        per_area[task.area] = per_area.get(task.area, 0) + 1

    for area in scenario.areas:
        lo, hi = bounds[area.name]
        assert lo <= per_area.get(area.name, 0) <= hi, area.name


def test_tasks_are_well_formed(result):
    scenario = toy_course()
    capable = {(m.name, a) for m in scenario.mowers for a in m.can_mow}
    for task in result.schedule.tasks:
        assert task.start >= 0
        assert task.end > task.start
        assert task.start < scenario.horizon_hours
        assert (task.mower, task.area) in capable
        assert task.task >= 1


def test_per_area_tasks_do_not_overlap_and_are_ordered(result):
    by_area: dict[str, list] = {}
    for task in result.schedule.tasks:
        by_area.setdefault(task.area, []).append(task)
    for tasks in by_area.values():
        tasks.sort(key=lambda t: t.task)
        for earlier, later in zip(tasks, tasks[1:], strict=False):
            assert later.start >= earlier.end


def test_cost_vector_shape(result):
    # 5 = distinct weak-constraint levels that ground for THIS instance (priorities 1..3
    # -> levels 4..2, plus avoid-zone level 1 and min-interval level 0; ADR-0012). Not a
    # fixed property of the encoding — see tests/test_encoding_contract.py for the general
    # rule.
    assert len(result.schedule.cost) == 5
    assert all(isinstance(c, int) and c >= 0 for c in result.schedule.cost)


# --- the preference layer, end to end (ADR-0031, ADR-0032) ------------------------------


def _movable_task(schedule, scenario):
    """A task from the solved schedule, plus a legal start for it that the optimum did
    *not* choose — the whole point being to ask for something the solver would not have
    picked on its own.
    """
    rows = build_completion_table(scenario)
    taken = {(t.area, t.start) for t in schedule.tasks}
    for task in schedule.tasks:
        legal = sorted(
            r.start
            for r in rows
            if r.area == task.area and r.mower == task.mower and r.start >= 24
        )
        for start in legal:
            if (task.area, start) not in taken and abs(start - task.start) >= 12:
                return task, start
    raise AssertionError("no movable task found in the toy schedule")


def test_a_preference_is_honoured_at_the_top_level():
    """The spec for ADR-0032's weak overlay: asking for a start the optimum did not pick,
    at ``level="top"``, moves a task there.

    ``threads=1`` throughout — with the portfolio, "the optimum did not pick it" is not a
    stable statement between runs (ADR-0007).
    """
    scenario = toy_course()
    base = solve_scenario(scenario, time_limit_s=45.0, threads=1)
    task, wanted = _movable_task(base.schedule, scenario)

    prefs = SolvePreferences(
        mode="weak",
        level="top",
        tasks=[PreferredTask(area=task.area, start=wanted, mower=task.mower)],
    )
    result = solve_scenario(scenario, time_limit_s=45.0, threads=1, preferences=prefs)

    assert result.solved
    assert (task.area, wanted) in {(t.area, t.start) for t in result.schedule.tasks}
    assert result.preferences is not None
    assert result.preferences.agreement.time_kept == 1
    assert result.preferences.agreement.mower_kept == 1


def test_the_weak_overlay_adds_exactly_one_cost_slot():
    """The heuristic overlay must add none: it changes the search, not the problem."""
    scenario = toy_course()
    base = solve_scenario(scenario, time_limit_s=45.0, threads=1)
    task, wanted = _movable_task(base.schedule, scenario)
    task_pref = [PreferredTask(area=task.area, start=wanted, mower=task.mower)]

    weak = solve_scenario(
        scenario,
        time_limit_s=45.0,
        threads=1,
        preferences=SolvePreferences(mode="weak", tasks=task_pref),
    )
    heuristic = solve_scenario(
        scenario,
        time_limit_s=45.0,
        threads=1,
        preferences=SolvePreferences(mode="heuristic", tasks=task_pref),
    )

    assert len(weak.schedule.cost) == len(base.schedule.cost) + 1
    assert len(heuristic.schedule.cost) == len(base.schedule.cost)


def test_conflicting_preferences_are_soft_not_refuting():
    """Every preference is soft by construction (ADR-0031).

    Hole1_Fairway and Hole2_SemiroughA are both serviceable *only* by Mower A, so asking
    for both at the same hour asks one machine to be in two places. A hard constraint
    would make the course unschedulable; a weak one just leaves one edit unmet.

    Hour 2, not 0: Mower A is mid-service on Hole1_SemiroughA until hour 2 (ADR-0040), so
    hour 0 is no longer a legal start for it at all and both edits would be *dropped*
    rather than left unmet — a different code path from the one this test guards.
    """
    scenario = toy_course()
    prefs = SolvePreferences(
        mode="weak",
        level="top",
        tasks=[
            PreferredTask(area="Hole1_Fairway", start=2, mower="Mower A"),
            PreferredTask(area="Hole2_SemiroughA", start=2, mower="Mower A"),
        ],
    )
    result = solve_scenario(scenario, time_limit_s=45.0, threads=1, preferences=prefs)

    assert result.status != "unsatisfiable"
    assert result.solved
    assert result.schedule is not None
    # Both cannot be honoured, and nothing was dropped before the solver saw it.
    assert result.preferences.agreement.total == 2
    assert result.preferences.agreement.time_kept <= 1
    assert result.preferences.dropped == []
