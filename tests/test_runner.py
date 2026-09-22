"""Unit tests for ``ClingconSolver`` — the thin wrapper around clingo + the clingcon
theory. Uses tiny throwaway programs, not the real encoding, so each test is fast.

Marked ``slow`` only because it constructs a real ``clingo.Control`` and grounds/solves;
in practice each test is well under a second.
"""

from __future__ import annotations

import clingo
import pytest

from fleetplanning.solver.runner import ClingconResult, ClingconSolver

pytestmark = pytest.mark.slow

#: Genuinely UNSAT (12 pigeons, 11 holes) but slow enough to refute at n=11 that a tight
#: time limit reliably cuts the search short before a proof — reused from
#: ``test_solver_status.py``'s own fixture for exactly this property.
_PIGEONHOLE = """
#const n=11.
pigeon(1..n+1). hole(1..n).
{ assign(P,H) : hole(H) } = 1 :- pigeon(P).
:- assign(P1,H), assign(P2,H), P1 < P2.
"""


def test_satisfiable_program_returns_shown_atoms():
    solver = ClingconSolver(["-t1"])
    solver.add("a. b. #show a/0.")
    result = solver.solve()
    assert result.solved
    assert [str(sym) for sym in result.atoms] == ["a"]


def test_unsatisfiable_program_reports_not_solved():
    solver = ClingconSolver(["-t1"])
    solver.add("a. :- a.")
    result = solver.solve()
    assert result.status == "unsatisfiable"
    assert result.solved is False
    assert result.atoms == []


def test_optimisation_reports_cost_and_proves_optimum():
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve()
    assert result.status == "optimal"
    assert result.cost == [1]  # picking `a` (penalty 1) beats not-a (penalty 2)
    assert result.exhausted is True


def test_optimisation_records_the_anytime_trace_and_grounding_split():
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve()
    assert result.incumbents, "expected at least one improved incumbent"
    elapsed, cost = result.incumbents[-1]
    assert cost == [1] and elapsed >= 0.0
    assert result.ground_time_s >= 0.0
    assert result.proof_time_s is not None  # optimum was proven
    # curated statistics slice is populated (see runner._STAT_PATHS)
    assert {"ground_rules", "choices", "conflicts"} <= set(result.stats)


def test_no_model_still_reports_grounding_and_stats_but_no_proof_time():
    solver = ClingconSolver(["-t1"])
    solver.add("a. :- a.")
    result = solver.solve()
    assert result.status == "unsatisfiable"
    assert result.proof_time_s is None
    assert result.incumbents == []
    assert "ground_rules" in result.stats


def test_trace_models_carries_each_incumbent_atom_tuple():
    """``trace_models=True`` makes each incumbent additionally carry its shown atoms, so
    the mechanism study (ADR-0033 decision 6) can read the schedule at any budget off one
    run. Off by default, so the product path is unchanged."""
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve(trace_models=True)
    assert len(result.incumbent_atoms) == len(result.incumbents)
    assert result.incumbent_atoms, "expected at least one traced incumbent"
    assert [str(s) for s in result.incumbent_atoms[-1]] == ["a"]  # final incumbent picks `a`


def test_trace_models_is_off_by_default():
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve()
    assert result.incumbents  # the (elapsed, cost) trace is always recorded
    assert result.incumbent_atoms == []  # the atom tuples are not


def test_clingcon_sum_constraint_is_enforced():
    solver = ClingconSolver(["-t1"])
    solver.add("&dom {1..10} = x. &sum { x } = 7. #show.")
    result = solver.solve()
    assert result.solved  # the theory found x = 7; no clingo error


def test_tiny_time_limit_returns_a_result_without_raising():
    solver = ClingconSolver(["-t1"])
    solver.add("&dom {1..1000000} = x. &sum { x } >= 1. #minimize { x }. #show.")
    result = solver.solve(time_limit_s=0.001)
    assert isinstance(result, ClingconResult)  # best-so-far or unsolved, never an exception


def test_add_after_solve_is_silently_ignored():
    """Documents current behaviour (see docs/known-hazards.md): programs added after the
    first solve are never grounded."""
    solver = ClingconSolver(["-t1"])
    solver.add("a. #show a/0.")
    solver.solve()
    solver.add("b. #show b/0.")
    result = solver.solve()
    assert [str(sym) for sym in result.atoms] == ["a"]


# --------------------------------------------------------------------------- Iteration 6:
# solve_under / literal_for — the explainability counterfactual's primitives.


def test_literal_for_returns_none_for_an_atom_that_never_grounds():
    """Stage 0's go/no-go case (`docs/explainability-literature.md` §5.1): an atom with
    no supporting rule instance simply never exists in the ground program."""
    solver = ClingconSolver(["-t1"])
    solver.add("a(1). #show a/1.")
    assert solver.literal_for(clingo.Function("a", [clingo.Number(1)])) is not None
    assert solver.literal_for(clingo.Function("a", [clingo.Number(99)])) is None


def test_solve_under_no_assumptions_matches_plain_satisfiability():
    solver = ClingconSolver(["-t1"])
    solver.add("{a}.")
    result = solver.solve_under([])
    assert result.status == "satisfiable"
    assert result.core is None


def test_solve_under_assumption_that_conflicts_with_a_hard_constraint_is_unsat_with_a_core():
    solver = ClingconSolver(["-t1"])
    solver.add("{a; b}. :- a, b. #show a/0. #show b/0.")
    lit_a = solver.literal_for(clingo.Function("a"))
    lit_b = solver.literal_for(clingo.Function("b"))
    assert lit_a is not None and lit_b is not None

    both = solver.solve_under([lit_a, lit_b])
    assert both.status == "unsatisfiable"
    assert both.core is not None and set(both.core) == {lit_a, lit_b}

    # multi-shot: a second, different assumption set on the SAME grounded Control
    just_a = solver.solve_under([lit_a])
    assert just_a.status == "satisfiable"


def test_opt_mode_ignore_disables_optimisation_entirely():
    """The single most important operational finding from the Stage 1 pilot
    (`docs/study/explain_pilot_v1/README.md` §4b): without ``--opt-mode=ignore`` a
    feasibility-only assumption solve silently re-optimises the whole schedule, as
    expensive as a full re-solve. This pins the flag's effect at the Control level: an
    ordinary ``solve()`` on a program with weak constraints normally reports a nonzero
    ``cost`` and proves the optimum; with the flag, optimisation never runs at all.
    """
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve()
    assert result.cost == [1]  # the ordinary path proves an optimum, as elsewhere in this file

    ignoring = ClingconSolver(["-t1", "--opt-mode=ignore"])
    ignoring.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    ignored_result = ignoring.solve_under([])
    assert ignored_result.status == "satisfiable"  # still answers the yes/no question


def test_solve_under_a_realistic_timeout_never_reports_unsatisfiable():
    """Reuses ``test_solver_status.py``'s PIGEONHOLE fixture — genuinely UNSAT, but slow
    enough to refute at n=11 that a 0.05s budget reliably cuts it short before a proof.
    At this scale ``SolveResult.unsatisfiable`` itself is already ``None`` when cut short
    (matching ``test_solver_status.py``'s own finding) — this only confirms
    :meth:`ClingconSolver.solve_under` does not somehow report "unsatisfiable" anyway.
    The *documented* clasp quirk this guards — ``interrupted`` and ``unsatisfiable`` both
    truthy at once — does not reproduce reliably enough on any real instance to drive a
    test off solver timing, so it is pinned directly below instead
    (``test_feasibility_status_vetoes_an_interrupted_unsatisfiable_claim``).
    """
    solver = ClingconSolver(["-t1"])
    solver.add(_PIGEONHOLE)
    result = solver.solve_under([], time_limit_s=0.05)
    assert result.status != "unsatisfiable"


class _FakeSolveResult:
    """A duck-typed stand-in for ``clingo.SolveResult`` — just the three flags
    ``_feasibility_status`` reads."""

    def __init__(self, *, interrupted: bool, unsatisfiable: bool | None, satisfiable: bool | None):
        self.interrupted = interrupted
        self.unsatisfiable = unsatisfiable
        self.satisfiable = satisfiable


def test_feasibility_status_vetoes_an_interrupted_unsatisfiable_claim():
    """ADR-0030's rule, pinned directly against the documented clasp quirk
    ``_finalize`` already warns about: cancelling a search that has not truly finished
    can hand back ``unsatisfiable=True`` *and* ``interrupted=True`` together. Real solver
    timing cannot reliably reproduce that combination (see the test above), so this
    drives ``_feasibility_status`` with a stand-in that states it directly."""
    from fleetplanning.solver.runner import _feasibility_status

    quirky = _FakeSolveResult(interrupted=True, unsatisfiable=True, satisfiable=None)
    assert _feasibility_status(quirky) == "unknown"  # never "unsatisfiable"

    genuine = _FakeSolveResult(interrupted=False, unsatisfiable=True, satisfiable=None)
    assert _feasibility_status(genuine) == "unsatisfiable"
