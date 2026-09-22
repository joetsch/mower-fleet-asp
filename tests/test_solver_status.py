"""Solve-status semantics — the tricky part.

The clingo ``SolveResult`` + async handle + timeout combination is historically easy to
misread, and how it is *described* has shifted across releases. So we pin it hard:

* one ``contract`` test on the **raw** ``clingo.SolveResult`` flags (the early-warning net
  for an upstream change), and
* one test per state on our ``ClingconResult.status`` mapping.

The timeout cases are built to have a wide margin, not a race:

* "best-so-far": under ``--configuration=jumpy`` (the ``threads=1`` default, ADR-0041) the
  toy encoding reaches its first incumbent in ~1 s but takes ~10 s to prove optimality —
  a 3 s budget reliably lands mid-search.
* "unknown": pigeonhole (12 pigeons, 11 holes) is UNSAT and slow to refute, so there is
  *never* a model and a 0.05 s budget *never* finishes the proof.
"""

from __future__ import annotations

import threading
import time

import clingo
import pytest
from clingcon import ClingconTheory

from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.service import control_args, encoding_text, solve_scenario
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.runner import ClingconSolver, _finalize

pytestmark = pytest.mark.slow

PIGEONHOLE = """
#const n=11.
pigeon(1..n+1). hole(1..n).
{ assign(P,H) : hole(H) } = 1 :- pigeon(P).
:- assign(P1,H), assign(P2,H), P1 < P2.
"""


def _raw_solve(program: str, limit: float | None = None) -> clingo.SolveResult:
    ctl = clingo.Control(["-t1"])
    ClingconTheory().register(ctl)
    ctl.add("base", [], program)
    ctl.ground([("base", [])])
    handle = ctl.solve(async_=True, on_model=lambda _m: None)
    if limit is None:
        handle.wait()
    elif not handle.wait(limit):
        handle.cancel()
    return handle.get()  # must not raise


@pytest.mark.contract
def test_raw_clingo_solve_result_flags():
    """Pins clingo's own flags for each case. If this breaks after a solver upgrade, the
    mapping in ``runner.solve()`` needs re-checking (see ADR-0010)."""
    unsat = _raw_solve("a. :- a.")
    assert (unsat.satisfiable, unsat.unsatisfiable, unsat.exhausted, unsat.interrupted) == (
        False, True, True, False,
    )

    sat_nonopt = _raw_solve("a.")
    assert sat_nonopt.satisfiable is True
    assert sat_nonopt.exhausted is False  # one model, search not exhausted
    assert sat_nonopt.interrupted is False

    opt = _raw_solve("{a}. :~ a. [1@0] :~ not a. [2@0]")
    assert opt.satisfiable is True
    assert opt.exhausted is True  # optimum proven
    assert opt.interrupted is False

    cut_short = _raw_solve(PIGEONHOLE, limit=0.05)
    # the dangerous case: no answer yet -> satisfiable is None, NOT False
    assert cut_short.satisfiable is None
    assert cut_short.unsatisfiable is None
    assert cut_short.unknown is True
    assert cut_short.interrupted is True


def test_status_unsatisfiable():
    solver = ClingconSolver(["-t1"])
    solver.add("a. :- a.")
    result = solver.solve()
    assert result.status == "unsatisfiable"
    assert result.solved is False
    assert result.exhausted is True


def test_status_satisfiable_without_proven_optimum():
    """A satisfiable non-optimisation program: a model, but the search is not exhausted,
    so the status is 'satisfiable', not 'optimal'."""
    solver = ClingconSolver(["-t1"])
    solver.add("a. #show a/0.")
    result = solver.solve()
    assert result.status == "satisfiable"
    assert result.solved is True
    assert result.exhausted is False


def test_status_optimal():
    solver = ClingconSolver(["-t1"])
    solver.add("{a}. :~ a. [1@0] :~ not a. [2@0] #show a/0.")
    result = solver.solve()
    assert result.status == "optimal"
    assert result.solved is True


def test_status_satisfiable_on_timeout_with_a_best_so_far():
    out = solve_scenario(toy_course(), time_limit_s=3.0, threads=1)
    assert out.status == "satisfiable"
    assert out.solved is True
    assert out.optimal is False
    assert out.schedule is not None and out.schedule.tasks


def test_status_unknown_on_timeout_before_any_model():
    solver = ClingconSolver(["-t1"])
    solver.add(PIGEONHOLE)
    result = solver.solve(time_limit_s=0.05)  # returns without raising
    assert result.status == "unknown"
    assert result.solved is False
    assert result.atoms == []


# --- ClingconSolver.solve_async (ADR-0022: anytime solving / the stop-button primitive) ---
# Same fixtures, same wide-margin philosophy as the tests above, but driven through the
# non-blocking poll()/cancel() API instead of the blocking solve(time_limit_s=...).


def _poll_until(async_solve, *, timeout_s: float = 5.0):
    """Poll until either a model appears or the search finishes, whichever first —
    mirrors how the frontend's ~2s poll loop and ``solve_jobs.poll`` consume this."""
    deadline = time.perf_counter() + timeout_s
    done, result = async_solve.poll()
    while time.perf_counter() < deadline and not done and result is None:
        time.sleep(0.02)
        done, result = async_solve.poll()
    return done, result


def test_async_poll_before_any_model_is_not_done():
    solver = ClingconSolver(["-t1"])
    solver.add(PIGEONHOLE)
    async_solve = solver.solve_async()
    # Called effectively instantly — pigeonhole(11) never finishes this fast (see the
    # module docstring's margin note), so this is not a race.
    done, result = async_solve.poll()
    assert (done, result) == (False, None)
    async_solve.cancel()  # tidy up rather than leaving the search running


def test_async_poll_returns_a_best_so_far_while_still_running():
    """Mirrors test_status_satisfiable_on_timeout_with_a_best_so_far, through poll()
    instead of a blocking time limit: under jumpy (ADR-0041) the toy encoding reaches its
    first incumbent in ~1s but takes ~10s to prove optimality, so polling within a 15s
    window reliably lands mid-search."""
    scenario = toy_course()
    solver = ClingconSolver(control_args(scenario, threads=1))
    solver.add(encoding_text())
    solver.add(render_instance(scenario))
    async_solve = solver.solve_async()

    done, result = _poll_until(async_solve, timeout_s=15.0)
    assert done is False, "should still be proving optimality at this point"
    assert result is not None
    assert result.status == "satisfiable"  # provisional best-so-far, not yet "optimal"
    assert result.solved is True
    async_solve.cancel()


def test_async_cancel_before_any_model_finalizes_to_unknown():
    solver = ClingconSolver(["-t1"])
    solver.add(PIGEONHOLE)
    async_solve = solver.solve_async()
    result = async_solve.cancel()  # called effectively instantly, same margin as above
    assert result.status == "unknown"
    assert result.solved is False


class _FakeSolveResult:
    """Just the three flags ``_finalize`` reads. A real ``clingo.SolveResult`` cannot be
    constructed by hand, and the state below cannot be produced on demand — it is a race."""

    def __init__(self, *, unsatisfiable: bool | None, exhausted: bool, interrupted: bool):
        self.unsatisfiable = unsatisfiable
        self.exhausted = exhausted
        self.interrupted = interrupted


def test_an_interrupted_solve_with_no_model_is_never_called_unsatisfiable():
    """Cancelling a search that has not got going can come back with ``unsatisfiable`` and
    ``exhausted`` both set, on an instance whose real refutation takes 90+ seconds — the
    flags do not describe a completed proof. Reporting that as "unsatisfiable" would tell
    the user their course is infeasible when the solver simply never looked (the UI words
    proven-UNSAT distinctly from a timeout, ADR-0024).

    So ``interrupted`` vetoes the claim: with no model in hand, an interrupted solve is
    "unknown". This is also what made
    ``test_async_cancel_before_any_model_finalizes_to_unknown`` flaky (~15% of runs).
    """
    result = _finalize(
        _FakeSolveResult(unsatisfiable=True, exhausted=True, interrupted=True),
        None,
        elapsed=0.0003,
        ground_time=0.0,
        stats={},
    )
    assert result.status == "unknown"
    assert result.solved is False


def test_a_completed_refutation_is_still_reported_unsatisfiable():
    """The veto must not cost us real UNSAT detection: a proof that ran to completion is
    never ``interrupted``, so it still reports ``unsatisfiable``."""
    result = _finalize(
        _FakeSolveResult(unsatisfiable=True, exhausted=True, interrupted=False),
        None,
        elapsed=0.5,
        ground_time=0.0,
        stats={},
    )
    assert result.status == "unsatisfiable"
    assert result.solved is False


def test_async_cancel_after_a_model_finalizes_to_satisfiable():
    scenario = toy_course()
    solver = ClingconSolver(control_args(scenario, threads=1))
    solver.add(encoding_text())
    solver.add(render_instance(scenario))
    async_solve = solver.solve_async()

    _, best_so_far = _poll_until(async_solve, timeout_s=15.0)
    assert best_so_far is not None

    final = async_solve.cancel()
    assert final.status == "satisfiable"
    assert final.solved is True
    assert final.atoms  # the best-so-far model, not thrown away


def test_async_timer_enforces_the_time_limit_like_the_blocking_path():
    solver = ClingconSolver(["-t1"])
    solver.add(PIGEONHOLE)
    async_solve = solver.solve_async(time_limit_s=0.05)
    done, result = _poll_until(async_solve, timeout_s=5.0)
    assert done is True
    assert result is not None
    assert result.status == "unknown"


def test_async_cancel_does_not_deadlock_with_concurrent_on_model_calls():
    """Regression for a real deadlock, found via a hung ``pytest`` run in practice (a
    stack sample landed in clingo's async-solve setup, blocked forever on a Python lock
    acquire) rather than by inspection: an earlier ``AsyncSolve.cancel()`` held a lock
    across the blocking ``handle.get()`` call, which ``on_model`` also needed to acquire
    — if a model was reported mid-flight exactly when ``cancel()`` ran, ``on_model``
    blocked on that lock while ``get()`` blocked waiting for that very ``on_model`` call
    to return. Hammers ``poll()``/``cancel()`` from a second thread while the search is
    actively finding models (the toy_course encoding's first few seconds, per the module
    docstring's margin note), with a bounded ``join(timeout=...)`` so a reintroduced
    deadlock fails this test instead of hanging the whole suite again.
    """
    scenario = toy_course()
    solver = ClingconSolver(control_args(scenario, threads=1))
    solver.add(encoding_text())
    solver.add(render_instance(scenario))
    async_solve = solver.solve_async()

    # A separate thread keeps calling poll() the whole time, so on_model is genuinely
    # firing concurrently with cancel() below rather than the two merely being called
    # from the same thread in sequence.
    stop_polling = threading.Event()

    def poll_loop() -> None:
        while not stop_polling.is_set():
            async_solve.poll()

    poller = threading.Thread(target=poll_loop, daemon=True)
    poller.start()

    outcome: list[object] = []

    def canceller() -> None:
        time.sleep(1.5)  # comfortably past the ~1s first-incumbent mark (module docstring)
        outcome.append(async_solve.cancel())

    canceller_thread = threading.Thread(target=canceller, daemon=True)
    canceller_thread.start()
    canceller_thread.join(timeout=15.0)
    stop_polling.set()
    poller.join(timeout=5.0)

    assert not canceller_thread.is_alive(), "cancel() deadlocked under concurrency"
    assert not poller.is_alive(), "poll() deadlocked under concurrency"
    assert outcome and outcome[0].solved is True


def test_async_cancel_after_natural_completion_is_idempotent():
    """Once poll() has already finalized the job (natural completion), cancel() must not
    try to re-drive the handle — it should just hand back the cached result."""
    solver = ClingconSolver(["-t1"])
    solver.add("a. #show a/0.")
    async_solve = solver.solve_async()
    done, _ = _poll_until(async_solve, timeout_s=5.0)
    assert done is True

    first = async_solve.cancel()
    second = async_solve.cancel()
    assert first is second
