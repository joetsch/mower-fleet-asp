"""Solver contract — every assumption our Python makes about the clingo / clingcon API.

Why this file exists
--------------------
We drive clingo and clingcon through their Python bindings (no external binary). Our code
(`solver/runner.py`, `solver/parse.py`, `service.py`) hard-codes the *shape* of what those
bindings return: attribute names, argument types, which flag proves optimality, and so on.
Exact version pins in ``pyproject.toml`` are the only thing stopping a new release from
silently changing one of those.

When you upgrade ``clingo`` / ``clingcon``: run ``uv run pytest -m contract``. If something
here fails, a real assumption moved — read the failure, fix the affected code, then update
the expected versions in ``test_pinned_versions`` deliberately.

See also ``tests/test_solver_status.py`` for the full solve-result / timeout state table.

Run with:  uv run pytest -m contract
"""

from __future__ import annotations

import importlib.metadata

import clingo
import pytest
from clingcon import ClingconTheory
from clingo.ast import ProgramBuilder, parse_string

pytestmark = [pytest.mark.contract, pytest.mark.slow]

# The versions every assumption below was last verified against. Bump these together with
# the pins in pyproject.toml, and only after this file still passes.
EXPECTED_CLINGO = "5.8.2"
EXPECTED_CLINGCON = "5.2.1.post2"


def test_pinned_versions_match_what_the_contract_was_verified_against():
    clingcon_version = importlib.metadata.version("clingcon")
    assert clingo.__version__ == EXPECTED_CLINGO and clingcon_version == EXPECTED_CLINGCON, (
        f"Solver versions changed (clingo {clingo.__version__}, clingcon {clingcon_version}). "
        "Re-run `uv run pytest -m contract`, fix anything that breaks, then update "
        "EXPECTED_CLINGO / EXPECTED_CLINGCON here and the pins in pyproject.toml."
    )


def _solve(program: str, args: list[str] | None = None):
    """Run a program through the same clingcon pipeline as ClingconSolver; return
    (solve_result, models) where models is a list of (shown_symbols, cost)."""
    ctl = clingo.Control(args or ["-t1"])
    theory = ClingconTheory()
    theory.register(ctl)
    with ProgramBuilder(ctl) as builder:
        parse_string(program, lambda ast: theory.rewrite_ast(ast, builder.add))
    ctl.ground([("base", [])])
    theory.prepare(ctl)

    models: list[tuple[list[clingo.Symbol], list[int]]] = []

    def on_model(m: clingo.Model) -> None:
        models.append((list(m.symbols(shown=True)), list(m.cost)))

    handle = ctl.solve(async_=True, on_model=on_model)
    assert handle.wait(30.0) is True  # returns a bool; True = finished in time
    return handle.get(), models


def test_control_accepts_the_flags_service_passes():
    result, _ = _solve("a. #show a/0.", args=["-t2", "-c", "horizon=168", "--configuration=many"])
    assert result.satisfiable


def test_solve_result_exposes_exhausted_and_satisfiability():
    # `exhausted` is how service.py decides `SolveResult.optimal`: for an optimisation
    # program it is True once the optimum has been proven.
    optimised, _ = _solve("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    assert optimised.satisfiable is True
    assert optimised.unsatisfiable is False
    assert optimised.exhausted is True

    unsat, _ = _solve("a. :- a.")
    assert unsat.satisfiable is False
    assert unsat.unsatisfiable is True
    assert unsat.exhausted is True  # the (empty) search space was fully explored


def test_solve_handle_supports_timed_wait_and_cancel():
    ctl = clingo.Control(["-t1"])
    ClingconTheory().register(ctl)
    ctl.add("base", [], "{a; b; c}.")
    ctl.ground([("base", [])])
    handle = ctl.solve(async_=True, on_model=lambda _m: None)
    finished = handle.wait(0.0)  # non-blocking poll -> bool
    assert isinstance(finished, bool)
    handle.cancel()  # must not raise
    handle.get()


def test_model_cost_is_a_list_and_improves_to_the_optimum():
    _result, models = _solve("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    assert models, "expected at least one model"
    _shown, best_cost = models[-1]  # runner.py keeps the LAST model as the best
    assert isinstance(best_cost, list) and best_cost == [1]

    # Model exposes the accessors runner.py / parse.py use, plus optimality_proven.
    attrs: dict[str, bool] = {}
    ctl = clingo.Control(["-t1"])
    ctl.add("base", [], "a.")
    ctl.ground([("base", [])])
    def probe(m: clingo.Model) -> None:
        attrs["cost_is_list"] = isinstance(m.cost, list)
        attrs["symbols_iterable"] = isinstance(list(m.symbols(shown=True)), list)  # runner wraps
        attrs["has_optimality_proven"] = hasattr(m, "optimality_proven")

    ctl.solve(on_model=probe)
    assert attrs == {"cost_is_list": True, "symbols_iterable": True, "has_optimality_proven": True}


def test_symbol_accessors_used_by_parse():
    _, models = _solve('schedule("Area X", 3, "Mower A", 10, 18). #show schedule/5.')
    shown, _cost = models[-1]
    atom = shown[0]
    assert atom.type == clingo.SymbolType.Function
    assert atom.name == "schedule"
    area, task, mower, start, end = atom.arguments
    assert area.string == "Area X" and mower.string == "Mower A"
    assert (task.number, start.number, end.number) == (3, 10, 18)


def test_statistics_expose_the_nested_paths_the_runner_reads():
    """``runner._extract_stats`` walks ``Control.statistics`` by these exact paths
    (ADR-0016). If clingo reorganises the dict this fails and the paths need updating."""
    ctl = clingo.Control(["-t1"])
    ClingconTheory().register(ctl)
    ctl.add("base", [], "{a}. :~ a. [1@0] #minimize { 1@0 : a }.")
    ctl.ground([("base", [])])
    ctl.solve(on_model=lambda _m: None)
    stats = ctl.statistics
    assert isinstance(stats["problem"]["lp"]["rules"], float)
    assert isinstance(stats["problem"]["lp"]["atoms"], float)
    for key in ("choices", "conflicts", "restarts"):
        assert isinstance(stats["solving"]["solvers"][key], float)


def test_domain_choices_stat_is_the_inert_heuristic_guard():
    """The mechanism study (ADR-0033) reads ``solving.solvers.extra.domain_choices`` to
    tell a firing ``#heuristic`` from a silently inert one — an inert heuristic arm would
    quietly become ``cold`` vs ``cold``. Two API facts are pinned here:

    1. the ``extra`` sub-dict only materialises under ``--stats=2`` (so the study runner,
       and ``runner._STAT_PATHS``'s new path, need that flag), and
    2. ``domain_choices`` is non-zero with ``--heuristic=Domain`` over a program that
       carries a ``#heuristic`` directive, and zero without it.
    """
    prog = (
        "val(1..30). { pick(X) : val(X) }. "
        ":~ pick(X). [X@0,X] "
        "#heuristic pick(X) : val(X). [10,true] "
        "#show pick/1."
    )

    def stats(args: list[str]) -> dict:
        ctl = clingo.Control(args)
        theory = ClingconTheory()
        theory.register(ctl)
        with ProgramBuilder(ctl) as builder:
            parse_string(prog, lambda ast: theory.rewrite_ast(ast, builder.add))
        ctl.ground([("base", [])])
        theory.prepare(ctl)
        ctl.solve(on_model=lambda _m: None)
        return ctl.statistics

    fired = stats(["-t1", "--stats=2", "--heuristic=Domain"])
    assert fired["solving"]["solvers"]["extra"]["domain_choices"] > 0

    inert = stats(["-t1", "--stats=2"])
    assert inert["solving"]["solvers"]["extra"]["domain_choices"] == 0

    # ...and without --stats=2 the sub-dict is absent entirely, which is why the study
    # runner passes --stats=2 and _extract_stats must tolerate a missing path.
    assert "extra" not in stats(["-t1", "--heuristic=Domain"])["solving"]["solvers"]


def test_clingcon_sum_and_dom_theory_atoms_are_solved():
    result, models = _solve("&dom {1..20} = x. &sum { x } = 12. #show.")
    assert result.satisfiable
    # The concrete value lives in the theory assignment, not in shown symbols; the
    # contract we rely on is just that a &sum/&dom program grounds + solves without error.


# --------------------------------------------------------------------------- Iteration 6:
# assumption-based solving, under a registered clingcon theory — the explainability
# counterfactual's whole mechanism (`solver/runner.py::ClingconSolver.solve_under`).


def _ground(program: str, args: list[str] | None = None) -> clingo.Control:
    """Ground (not solve) — the shared setup for the assumption tests below, which each
    need to solve the *same* grounded program more than once."""
    ctl = clingo.Control(args or ["-t1"])
    theory = ClingconTheory()
    theory.register(ctl)
    with ProgramBuilder(ctl) as builder:
        parse_string(program, lambda ast: theory.rewrite_ast(ast, builder.add))
    ctl.ground([("base", [])])
    theory.prepare(ctl)
    return ctl


def test_solve_accepts_assumptions_as_symbol_bool_tuples():
    ctl = _ground("{a; b}. #show a/0. #show b/0.")
    result = ctl.solve(assumptions=[(clingo.Function("a"), True)])
    assert result.satisfiable


def test_symbolic_atoms_literal_is_a_valid_assumption_and_a_missing_atom_is_none():
    ctl = _ground("a(1). #show a/1.")
    present = ctl.symbolic_atoms[clingo.Function("a", [clingo.Number(1)])]
    assert present is not None
    result = ctl.solve(assumptions=[present.literal])
    assert result.satisfiable
    assert ctl.symbolic_atoms[clingo.Function("a", [clingo.Number(2)])] is None


def test_a_conflicting_assumption_pair_is_unsat_with_a_nonempty_core():
    ctl = _ground("{a; b}. :- a, b. #show a/0. #show b/0.")
    lit_a = ctl.symbolic_atoms[clingo.Function("a")].literal
    lit_b = ctl.symbolic_atoms[clingo.Function("b")].literal
    handle = ctl.solve(assumptions=[lit_a, lit_b], async_=True)
    handle.wait(30.0)
    result = handle.get()
    assert result.unsatisfiable
    assert set(handle.core()) == {lit_a, lit_b}


def test_repeated_solve_with_different_assumptions_on_one_control():
    """Multi-shot solving: the same grounded, theory-registered Control answers more than
    one assumption-solve — the "one grounding, many solves" shape the whole counterfactual
    mechanism depends on for its cost profile."""
    ctl = _ground("{a; b}. :- a, b. #show a/0. #show b/0.")
    lit_a = ctl.symbolic_atoms[clingo.Function("a")].literal
    lit_b = ctl.symbolic_atoms[clingo.Function("b")].literal
    both = ctl.solve(assumptions=[lit_a, lit_b])
    just_a = ctl.solve(assumptions=[lit_a])
    assert both.unsatisfiable
    assert just_a.satisfiable


def test_opt_mode_ignore_stops_at_the_first_model_no_cost_computed():
    """The single most important operational fact the Stage 1 pilot found
    (`docs/study/explain_pilot_v1/README.md` §4b): without this flag, an assumption-only
    feasibility solve on a program that still carries weak constraints silently
    re-optimises — exactly as expensive as a full solve. `--opt-mode=ignore` disables
    that: any model is accepted immediately and ``Model.cost`` is never computed."""
    ctl = clingo.Control(["-t1", "--opt-mode=ignore"])
    ClingconTheory().register(ctl)
    ctl.add("base", [], "{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    ctl.ground([("base", [])])
    costs = []
    ctl.solve(on_model=lambda m: costs.append(list(m.cost)))
    assert costs == [[]]  # a model was found; the weak constraints were never scored
