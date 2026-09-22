"""Encoding contract — what the packaged ``.lp`` produces and how ``parse.py`` reads it.

``parse.py`` hard-codes the shown-atom names/arities, and ``test_end_to_end.py`` assumes
the optimisation cost vector has exactly 5 entries for the toy (one per weak-constraint
level in the encoding — design-choices B3). If the forward-planning encoding (ADR-0015) is
edited these break with a pointer to what to update.

Run with:  uv run pytest -m contract
"""

from __future__ import annotations

import clingo
import pytest

from fleetplanning.model import DAYS, Area, DaySchedule, Mower, Scenario, ServiceEvent
from fleetplanning.service import encoding_text
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.runner import ClingconSolver

pytestmark = [pytest.mark.contract, pytest.mark.slow]

SHOWN_PREDICATES = {
    ("schedule", 5),
    ("max_interval_violation", 2),
    ("min_interval_violation", 2),
    ("avoid_zone_violation", 2),
    # forward-planning encoding (ADR-0015): area-level start-boundary violations
    ("first_task_too_late", 1),
    ("first_task_too_early", 1),
    ("last_task_too_early", 1),
}


def _minimal_scenario() -> Scenario:
    area = Area(
        name="A",
        type="fairway",
        hole=1,
        priority=1,
        min_interval=10,
        max_interval=20,
        schedule={d: DaySchedule(avoid=[(13, 20)]) for d in DAYS},
    )
    return Scenario(
        name="minimal",
        areas=[area],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-5, completion=-1)],
        horizon_hours=48,
        horizon_start_hour=0,
    )


@pytest.fixture(scope="module")
def solved():
    scenario = _minimal_scenario()
    # The portfolio config (ADR-0007), as production uses. Single-threaded search does not
    # prove this instance's optimum quickly under the forward-planning encoding (ADR-0015);
    # the contract here is about the *shown atoms* and cost-vector shape, not determinism.
    solver = ClingconSolver(
        ["-t4", "--configuration=many", "-c", f"horizon={scenario.horizon_hours}"]
    )
    solver.add(encoding_text())
    solver.add(render_instance(scenario))
    return solver.solve(time_limit_s=30.0)


def test_encoding_text_is_found_in_the_installed_package():
    text = encoding_text()
    assert "#show schedule" in text
    # The forward-planning encoding (ADR-0015) does not reference the ``horizon`` constant;
    # only the Python completion table / bounds do. ``-c horizon=`` is still passed.
    assert "first_task_too_late" in text


def test_minimal_instance_solves(solved):
    assert solved.solved
    assert solved.status == "optimal"  # tiny instance: optimum proven


def test_shown_atoms_are_exactly_the_predicates_parse_expects(solved):
    seen = {
        (a.name, len(a.arguments))
        for a in solved.atoms
        if a.type == clingo.SymbolType.Function
    }
    assert seen <= SHOWN_PREDICATES, f"unexpected shown atoms: {seen - SHOWN_PREDICATES}"
    assert ("schedule", 5) in seen


def test_schedule_atom_argument_types_match_parse(solved):
    schedule_atoms = [a for a in solved.atoms if a.name == "schedule" and len(a.arguments) == 5]
    assert schedule_atoms
    area, task, mower, start, end = schedule_atoms[0].arguments
    assert area.type == clingo.SymbolType.String
    assert mower.type == clingo.SymbolType.String
    assert all(x.type == clingo.SymbolType.Number for x in (task, start, end))


def test_cost_vector_length_follows_the_priority_levels_present(solved):
    """AUDIT FINDING: the cost-vector length is NOT a fixed 5 — it is the number of
    distinct weak-constraint levels that actually ground. The encoding's levels are
    ``5-P`` for each area priority P (max-interval + first/last-task boundary), ``1`` for
    avoid-zone and ``0`` for min-interval (+ first-task-too-early).

    This minimal instance has one priority-1 area, so the levels are {4, 1, 0} -> length 3.
    The toy scenario has priorities 1..3 (ADR-0012), giving {4, 3, 2, 1, 0} -> length 5,
    which is what ``test_end_to_end.test_cost_vector_shape`` asserts. Both facts are pinned
    so an objective change (design-choices B3 / roadmap Iteration 3) surfaces here.
    """
    assert [isinstance(c, int) for c in solved.cost] == [True] * len(solved.cost)
    assert len(solved.cost) == 3
