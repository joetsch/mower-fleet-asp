"""Thin wrapper around clingo + the clingcon theory — ported from the notebook (cell 28).

clingcon is a *theory* plugged into clingo: programs may contain ``&sum`` / ``&dom``
constraint atoms over integers, which clingo alone does not understand. Using it from
Python means:

1. parse each program through ``clingcon.rewrite_ast`` so the theory atoms are recognised,
2. ground the ``base`` part, then ``theory.prepare`` the constraint solver,
3. solve; for optimisation, keep the last (best) model the solver reports.

Solve status (ADR-0010)
-----------------------
The clingo ``SolveResult`` + async handle + timeout combination is easy to misread — e.g.
``.satisfiable`` is ``None`` (not ``False``) when the search is cut short before an
answer, and ``.exhausted`` means "optimum proven" only for an optimisation program. We
collapse it to one of four states; ``tests/test_solver_status.py`` pins the raw behaviour.

===============  ================================================================
``optimal``      a model was found and the search finished (optimum proven)
``satisfiable``  a model was found but the search was cut short (best-so-far)
``unsatisfiable``  no model and UNSAT was proven
``unknown``      no model and the search was cut short (timed out)
===============  ================================================================

Study instrumentation (ADR-0016)
--------------------------------
For the performance study the runner also records, per solve:

- ``ground_time_s`` — grounding wall-clock, timed separately from the search (the two
  answer different questions in the scaling analysis);
- ``incumbents`` — ``(elapsed_s, cost)`` for every improved model, ``elapsed_s`` measured
  from the *start of the search* (after grounding). The anytime profile is reconstructed
  from this without re-solving;
- ``proof_time_s`` — search elapsed at the moment the optimum was proven, else ``None``;
- ``stats`` — a small curated slice of ``clingo.Control.statistics`` (ground size, choices,
  conflicts, restarts, and — under ``--stats=2`` — ``domain_choices``, the mechanism
  study's inert-``#heuristic`` guard, ADR-0033). The nested-dict layout is a clingo API
  assumption pinned in ``tests/test_solver_contract.py``.

The mechanism study (ADR-0033) additionally passes ``trace_models=True`` to
:meth:`ClingconSolver.solve`, which records each incumbent's atoms alongside the
``(elapsed, cost)`` trace so a schedule can be read off any budget without re-solving.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

import clingo
from clingcon import ClingconTheory
from clingo.ast import ProgramBuilder, parse_string

SolveStatus = Literal["optimal", "satisfiable", "unsatisfiable", "unknown"]


@dataclass
class ClingconResult:
    status: SolveStatus = "unknown"
    atoms: list[clingo.Symbol] = field(default_factory=list)
    cost: list[int] = field(default_factory=list)
    solve_time_s: float = 0.0
    ground_time_s: float = 0.0
    proof_time_s: float | None = None
    incumbents: list[tuple[float, list[int]]] = field(default_factory=list)
    incumbent_atoms: list[list[clingo.Symbol]] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)

    @property
    def solved(self) -> bool:
        """True when there is a schedule to read (proven optimal or best-so-far)."""
        return self.status in ("optimal", "satisfiable")

    @property
    def exhausted(self) -> bool:
        """True when the search finished — optimum proven, or UNSAT proven."""
        return self.status in ("optimal", "unsatisfiable")


FeasibilityStatus = Literal["satisfiable", "unsatisfiable", "unknown"]


@dataclass
class FeasibilityResult:
    """The outcome of one :meth:`ClingconSolver.solve_under` call (Iteration 6, the
    explainability counterfactual) — a yes/no feasibility question, not an optimisation.
    """

    status: FeasibilityStatus
    elapsed_s: float
    #: Raw assumption literals clasp's conflict analysis reports when ``unsatisfiable`` —
    #: a *valid* conflict, not necessarily a *minimal* one (Stage 0 measured a 2x gap on
    #: the smallest possible example; minimising it is the caller's job). ``None`` unless
    #: unsatisfiable.
    core: list[int] | None = None


def _feasibility_status(solve_result: clingo.SolveResult) -> FeasibilityStatus:
    """The ADR-0030 interrupted-vetoes-unsatisfiable rule, applied to a plain feasibility
    question. Pulled out as its own pure function (mirroring :func:`_finalize`) so the
    rule can be pinned directly against a stand-in result — the raw combination this
    guards (``interrupted`` *and* ``unsatisfiable`` both truthy at once) is exactly the
    documented clasp quirk :func:`_finalize` already warns about, and it does not occur
    reliably enough on any real instance to drive a test off solver timing.
    """
    if solve_result.interrupted:
        return "unknown"
    if solve_result.unsatisfiable:
        return "unsatisfiable"
    if solve_result.satisfiable:
        return "satisfiable"
    return "unknown"


# Curated slice of ``clingo.Control.statistics`` (a deeply nested dict). Kept small and
# flat on purpose — these are the covariates the scaling analysis actually plots. The
# paths are a clingo API assumption; ``tests/test_solver_contract.py`` pins them.
_STAT_PATHS: dict[str, tuple[str, ...]] = {
    "ground_rules": ("problem", "lp", "rules"),
    "ground_atoms": ("problem", "lp", "atoms"),
    "choices": ("solving", "solvers", "choices"),
    "conflicts": ("solving", "solvers", "conflicts"),
    "restarts": ("solving", "solvers", "restarts"),
    # Only populated under ``--stats=2`` and only non-zero when a ``#heuristic`` directive
    # actually fires — the mechanism study's inert-heuristic guard (ADR-0033). The walk in
    # ``_extract_stats`` silently yields nothing when the ``extra`` sub-dict is absent, so
    # adding this path is safe for every solve that does not ask for it.
    "domain_choices": ("solving", "solvers", "extra", "domain_choices"),
}


def _extract_stats(statistics: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, path in _STAT_PATHS.items():
        node: object = statistics
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, (int, float)):
            out[name] = float(node)
    return out


def _finalize(
    solve_result: clingo.SolveResult,
    best: ClingconResult | None,
    elapsed: float,
    ground_time: float,
    stats: dict[str, float],
) -> ClingconResult:
    """Collapse a finished (or cancelled) clingo ``SolveResult`` plus the last-seen model
    into one :class:`ClingconResult` — the ADR-0010 four-state status. Shared by the
    blocking :meth:`ClingconSolver.solve` and the anytime :class:`AsyncSolve`, so both
    read the handle the same way.
    """
    if best is not None:
        best.status = "optimal" if solve_result.exhausted else "satisfiable"
        best.solve_time_s = elapsed
        best.ground_time_s = ground_time
        best.proof_time_s = elapsed if solve_result.exhausted else None
        best.stats = stats
        return best
    # No model. `.unsatisfiable` is *usually* truthy only when UNSAT was actually proven,
    # and a timeout before any answer leaves it None — but not reliably: cancelling a
    # search that has not got going can hand back `unsatisfiable` and `exhausted` both
    # set, on an instance whose real refutation takes 90+ seconds. Those flags do not
    # describe a completed proof.
    #
    # So `interrupted` vetoes the claim. Saying "unsatisfiable" means "this course cannot
    # be serviced" — the UI words it distinctly from a timeout (ADR-0024) — and we only
    # say it about a search that ran to completion. The cost is that a genuine refutation
    # finishing in the same instant as a cancel is under-reported as "unknown"; that is
    # the safe direction, and it is what makes the Stop button honest.
    proven_unsat = bool(solve_result.unsatisfiable) and not solve_result.interrupted
    status: SolveStatus = "unsatisfiable" if proven_unsat else "unknown"
    return ClingconResult(
        status=status, solve_time_s=elapsed, ground_time_s=ground_time, stats=stats
    )


class AsyncSolve:
    """A live, cancellable, pollable solve — the anytime-solving / stop-button primitive.

    Wraps a clingo ``SolveHandle`` obtained with ``async_=True``: the search runs on
    clingo's own thread, ``on_model`` fires on that thread as better models are found,
    and this object exposes thread-safe, non-blocking access to "the best model right
    now" plus a way to end the search early. Construct via
    :meth:`ClingconSolver.solve_async`, not directly.

    ``poll()`` and ``cancel()`` are safe to call from any thread (a FastAPI request
    handler, say), including concurrently with clingo's own ``on_model`` callback and
    with each other.

    Locking is deliberately split in two, to avoid a self-deadlock a single lock caused
    here (found by hand: a hung ``pytest`` run, reproduced with a stack sample landing in
    clingo's async-solve setup, blocked forever on a Python lock acquire):
    ``self._best`` is written by ``_on_model`` with **no lock at all** — CPython makes a
    single attribute assignment atomic, so this is safe, and it means ``on_model`` can
    never block. Finalizing (calling the blocking ``handle.get()``) uses
    ``self._finalize_lock``, held only around the bookkeeping — **never** across
    ``handle.get()`` itself. The bug this replaced held one lock across that blocking
    call; if a model was mid-flight, ``on_model`` blocked trying to acquire the very lock
    ``get()``'s caller was holding while waiting for that same ``on_model`` call to
    finish — a real, if intermittent, hang under concurrent access.
    """

    def __init__(
        self, ctl: clingo.Control, ground_time: float, time_limit_s: float | None
    ) -> None:
        self._ctl = ctl
        self._ground_time = ground_time
        self._started = time.perf_counter()
        self._best: ClingconResult | None = None  # written only by _on_model, lock-free
        self._done = False
        self._final: ClingconResult | None = None
        self._finalize_lock = threading.Lock()
        self._finalizing = False
        self._finalized = threading.Event()
        # Everything above must exist before this — on_model can fire as soon as
        # solve() is called, possibly before this constructor returns.
        self._handle = ctl.solve(async_=True, on_model=self._on_model)
        self._timer: threading.Timer | None = None
        if time_limit_s is not None:
            self._timer = threading.Timer(time_limit_s, self._handle.cancel)
            self._timer.daemon = True
            self._timer.start()

    def _on_model(self, model: clingo.Model) -> None:
        # No lock: a plain attribute assignment is atomic under the GIL, and this must
        # never be able to block (see the class docstring on why).
        self._best = ClingconResult(
            status="satisfiable",  # provisional — corrected to "optimal" on finalize
            atoms=list(model.symbols(shown=True)),
            cost=list(model.cost),
            solve_time_s=time.perf_counter() - self._started,
        )

    def poll(self) -> tuple[bool, ClingconResult | None]:
        """Non-blocking check-in. Returns ``(done, result)``: while running, ``result`` is
        the best model seen so far (``None`` if none yet); once ``done`` it is the final
        result — this call finalizes the search the first time it notices completion.
        """
        if not self._done and self._handle.wait(0):
            self._finalize()
        return self._done, (self._final if self._done else self._best)

    def cancel(self) -> ClingconResult:
        """Stop the search early and finalize, keeping whatever best-so-far model exists.
        Idempotent — calling it again, or after the search already finished on its own,
        just returns the same final result.
        """
        if not self._done:
            self._handle.cancel()
            self._finalize()
        assert self._final is not None
        return self._final

    def _finalize(self) -> None:
        """Collapse the handle into ``self._final``, exactly once, however many callers
        (poll/cancel, from different threads) race to trigger it — the rest wait for the
        one that "won" rather than each calling ``handle.get()`` themselves.
        """
        with self._finalize_lock:
            if self._done:
                return
            if self._finalizing:
                won = False
            else:
                self._finalizing = won = True
        if not won:
            self._finalized.wait()
            return
        if self._timer is not None:
            self._timer.cancel()
        # Deliberately outside any lock — this blocks until the search actually stops,
        # during which on_model may still fire (lock-free, so that's fine).
        solve_result = self._handle.get()
        elapsed = time.perf_counter() - self._started
        stats = _extract_stats(self._ctl.statistics)
        self._final = _finalize(solve_result, self._best, elapsed, self._ground_time, stats)
        self._done = True
        self._finalized.set()


class ClingconSolver:
    """Accumulate program text, ground once, then solve."""

    def __init__(self, control_args: list[str] | None = None) -> None:
        self._ctl = clingo.Control(control_args or ["-t1"])
        self._theory = ClingconTheory()
        self._theory.register(self._ctl)
        self._programs: list[str] = []
        self._grounded = False

    def add(self, program: str) -> None:
        self._programs.append(program)

    def ground(self) -> None:
        with ProgramBuilder(self._ctl) as builder:
            for program in self._programs:
                parse_string(
                    program,
                    lambda ast: self._theory.rewrite_ast(ast, builder.add),
                )
        self._ctl.ground([("base", [])])
        self._theory.prepare(self._ctl)
        self._grounded = True

    def solve(
        self, time_limit_s: float | None = None, *, trace_models: bool = False
    ) -> ClingconResult:
        """Ground (once) and solve, keeping the best model.

        ``trace_models`` (ADR-0033 decision 6) additionally records each incumbent's shown
        atoms in ``ClingconResult.incumbent_atoms``, index-aligned with ``incumbents``, so
        the mechanism study can reconstruct the schedule at any budget off a single run.
        Off by default — the product path (``solve_scenario``, the job machine) is
        behaviourally unchanged.
        """
        ground_started = time.perf_counter()
        if not self._grounded:
            self.ground()
        ground_time = time.perf_counter() - ground_started

        best: ClingconResult | None = None
        incumbents: list[tuple[float, list[int]]] = []
        incumbent_atoms: list[list[clingo.Symbol]] = []
        started = time.perf_counter()

        def on_model(model: clingo.Model) -> None:
            nonlocal best
            cost = list(model.cost)
            atoms = list(model.symbols(shown=True))
            incumbents.append((time.perf_counter() - started, cost))
            if trace_models:
                incumbent_atoms.append(atoms)
            best = ClingconResult(atoms=atoms, cost=cost)

        handle = self._ctl.solve(async_=True, on_model=on_model)
        if time_limit_s is None:
            handle.wait()
        elif not handle.wait(time_limit_s):
            handle.cancel()
        solve_result = handle.get()
        elapsed = time.perf_counter() - started
        stats = _extract_stats(self._ctl.statistics)

        result = _finalize(solve_result, best, elapsed, ground_time, stats)
        result.incumbents = incumbents
        result.incumbent_atoms = incumbent_atoms
        return result

    def solve_async(self, time_limit_s: float | None = None) -> AsyncSolve:
        """Start a cancellable, pollable solve and return immediately (the anytime-solving
        / stop-button primitive) — unlike :meth:`solve`, this does not block until the
        search finishes. Check in via :meth:`AsyncSolve.poll`, end it early via
        :meth:`AsyncSolve.cancel`.
        """
        ground_started = time.perf_counter()
        if not self._grounded:
            self.ground()
        ground_time = time.perf_counter() - ground_started
        return AsyncSolve(self._ctl, ground_time, time_limit_s)

    def literal_for(self, symbol: clingo.Symbol) -> int | None:
        """The program literal for one ground atom, or ``None`` if it never grounded.

        Grounds first if needed. An atom clasp can simplify away — proved false or
        absent in every possible model — has no literal to assume over; ``None`` is the
        signal for that (Stage 0's go/no-go check, `docs/explainability-literature.md`
        §5.1 — an edit naming an individually-illegal hour is exactly this case, and
        callers should treat it as statically impossible, never as "undetermined").
        """
        if not self._grounded:
            self.ground()
        atom = self._ctl.symbolic_atoms[symbol]
        return atom.literal if atom is not None else None

    def solve_under(
        self, assumptions: list[int], time_limit_s: float | None = None
    ) -> FeasibilityResult:
        """Does any answer set exist that also satisfies every literal in
        ``assumptions``? (Iteration 6's explainability counterfactual.)

        This is a plain feasibility question, never an optimisation — any model is a
        sufficient witness. **Callers must pass ``--opt-mode=ignore`` in the
        ``control_args`` this solver was constructed with.** Without it, clasp keeps
        searching for a *provably optimal* model under the assumptions, which is exactly
        as expensive as a full re-solve — found the hard way in the Stage 1 pilot
        (`docs/study/explain_pilot_v1/README.md` §4b): it turned every hard scenario's
        check into a multi-minute hang until this flag was added.

        Follows the same interrupted-vetoes-unsatisfiable rule as :func:`_finalize`
        (ADR-0030): a cancelled/timed-out search is never reported ``"unsatisfiable"``,
        whatever ``SolveResult.unsatisfiable`` happens to read at that instant.
        """
        if not self._grounded:
            self.ground()
        started = time.perf_counter()
        handle = self._ctl.solve(assumptions=assumptions, async_=True)
        if time_limit_s is None:
            handle.wait()
        elif not handle.wait(time_limit_s):
            handle.cancel()
        solve_result = handle.get()
        elapsed = time.perf_counter() - started
        status = _feasibility_status(solve_result)
        core = list(handle.core()) if status == "unsatisfiable" else None
        return FeasibilityResult(status, elapsed, core=core)
