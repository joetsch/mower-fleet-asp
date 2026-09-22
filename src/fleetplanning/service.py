"""Orchestration: :class:`Scenario` in, :class:`SolveResult` out.

    scenario
      -> completion table + bounds  (Python)
      -> instance facts (.lp)
      -> clingcon(forward-planning encoding)
      -> Schedule

Solver settings (ADR-0007): the multi-threaded portfolio (``-t4 --configuration=many``,
as in the notebook) proves optimality on well-behaved toy instances in a few seconds, so
that is the default. Among equally-optimal schedules it may return a different one from
run to run, so the demo scenario's seed is chosen to give a clean, quick-to-prove
instance. Pass ``threads=1`` for a fully reproducible single schedule (slower) — it runs
under ``--configuration=jumpy`` (:data:`DETERMINISTIC_CONFIG`, ADR-0041), not clingo's
fragile ``tweety`` default. Scaling to the 18-hole instances is a later iteration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files

from fleetplanning.model import (
    DroppedPreference,
    PreferenceReport,
    Scenario,
    SolvePreferences,
    SolveResult,
    SolverInfo,
)
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.parse import parse_schedule
from fleetplanning.solver.preferences import preference_agreement, render_preferences
from fleetplanning.solver.runner import ClingconResult, ClingconSolver
from fleetplanning.solver.score import score_schedule

ENCODING = "golf_schedule_forward_model.lp"

#: The preference overlays (ADR-0032), keyed by ``SolvePreferences.mode``. Additive
#: programs loaded *beside* the forward model, never edits to it.
PREFERENCE_ENCODINGS = {
    "weak": "preferences_weak.lp",
    "heuristic": "preferences_heuristic.lp",
}

#: ``-c pref_level=N`` for the weak overlay, keyed by :data:`model.PreferenceLevel`.
#: Read against the forward model's own weak-constraint levels — max-interval at ``5-P``
#: in {4,3,2}, avoid-zone at 1, min-interval at 0 — so the number *is* the price of one
#: churned task: 6 outranks every service violation; 4 ties it with one missed
#: High-priority service, 2 with a Low-priority one, 1 with an hour worked in an avoid
#: window; -1 sits below all of them, so preferences only choose among otherwise
#: equally-good schedules. None collides with ADR-0012.
#:
#: The intermediate settings deliberately share a level with the service objective, which
#: means their costs are *summed* into one slot rather than kept apart — see
#: :attr:`model.PreferenceReport.level`. Changing the price needs no ``.lp`` edit:
#: ``pref_level`` is a ``#const`` the overlay already declares.
PREFERENCE_LEVELS = {"top": 6, "high": 4, "low": 2, "avoid": 1, "tiebreak": -1}

#: ``-c pref_w=N``, the ``#heuristic`` bias weight. ADR-0034 decision 4 chose
#: ``heur[1,true]``; the overlay's own ``#const pref_w=10`` is the *other*, worse pilot arm
#: (0.839 vs 0.919 agreement), so the weight is pinned here rather than inherited.
HEURISTIC_WEIGHT = 1


def encoding_text(name: str = ENCODING) -> str:
    """Read one packaged ``.lp``. Defaults to the active forward model (ADR-0015); the
    preference overlays are read through the same door."""
    return files("fleetplanning.solver.encodings").joinpath(name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class PreferenceSetup:
    """What the active preference mechanism contributes to one solve (ADR-0032).

    All three fields are empty when preferences are inactive — absent, ``mode="off"``, no
    tasks, or every half dropped as unsatisfiable. That is the ADR-0031 invariant in its
    operational form: nothing to add means the program and the command line are exactly
    what they would have been without this feature.
    """

    programs: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    dropped: list[DroppedPreference] = field(default_factory=list)


def prepare_preferences(
    scenario: Scenario,
    preferences: SolvePreferences | None,
    completion_rows: list | None = None,
) -> PreferenceSetup:
    """Turn a preference payload into the extra programs and flags one solve needs.

    Returns an empty setup whenever there is nothing the solver could act on, so callers
    can splat it unconditionally.
    """
    if preferences is None or preferences.mode == "off" or not preferences.tasks:
        return PreferenceSetup()

    rendered = render_preferences(preferences, scenario, completion_rows)
    if not rendered.text:
        # Everything was dropped: no facts to give the overlay, so do not load it. The
        # dropped list still travels, so the UI can say why the edits went nowhere.
        return PreferenceSetup(dropped=rendered.dropped)

    if preferences.mode == "weak":
        args = ["-c", f"pref_level={PREFERENCE_LEVELS[preferences.level]}"]
    else:
        # Without --heuristic=Domain clingo ignores #heuristic directives outright and the
        # arm would silently degrade to a cold solve (docs/preference-resolve-literature.md
        # §5.2); without -c pref_w it would run at the overlay's default 10, not the weight
        # the pilot chose.
        args = ["--heuristic=Domain", "-c", f"pref_w={HEURISTIC_WEIGHT}"]

    return PreferenceSetup(
        programs=[encoding_text(PREFERENCE_ENCODINGS[preferences.mode]), rendered.text],
        args=args,
        dropped=rendered.dropped,
    )


#: The ``--configuration`` the deterministic ``threads=1`` path runs under (ADR-0041).
#: Clingo's default at one thread is ``tweety``, which is pathological on this encoding —
#: it can spend the whole budget without a first model on instances the portfolio proves
#: in seconds (surfaced by ADR-0040's completion-table censoring, which tips ``toy_course``
#: over that edge). ``jumpy`` is the winner of the ADR-0033 bake-off (18/18 anchored vs
#: tweety's 9/18; first incumbent ~1.15 s) and is what the study runner already uses for
#: its own ``threads=1`` runs (``generator/pref_study.T1_CONFIG``).
DETERMINISTIC_CONFIG = "jumpy"


def _portfolio_args(threads: int) -> list[str]:
    """The thread + search-portfolio flags (ADR-0007, ADR-0041).

    Built once and reused by :func:`control_args` (what clingo actually runs) and
    :func:`solver_info` (what the client is told), so the two cannot drift apart —
    which is exactly how the old "single-threaded" UI text went stale (ADR-0009).

    ``threads > 1`` gets the ``many`` portfolio; ``threads == 1`` gets
    :data:`DETERMINISTIC_CONFIG` rather than clingo's fragile ``tweety`` default.
    """
    config = "many" if threads > 1 else DETERMINISTIC_CONFIG
    return [f"-t{threads}", f"--configuration={config}"]


_THREADS_FLAG = re.compile(r"-t(\d+)")


def _parse_threads(args: list[str]) -> int:
    """Best-effort thread count for expert-mode display when the caller (the expert-mode
    command-line override, ADR-0023) supplied a raw arg list instead of a ``threads`` int.
    clingo itself defaults to 1 thread when ``-tN`` is absent.
    """
    for arg in args:
        m = _THREADS_FLAG.fullmatch(arg)
        if m:
            return int(m.group(1))
    return 1


def _parse_config(args: list[str]) -> str | None:
    """Best-effort ``--configuration`` value for expert-mode display, same caveat as
    :func:`_parse_threads`."""
    for arg in args:
        if arg.startswith("--configuration="):
            return arg.split("=", 1)[1]
    return None


def control_args(
    scenario: Scenario,
    threads: int,
    *,
    clingo_args: list[str] | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the clingo command-line arguments for one solve.

    Public (with :func:`encoding_text`, :func:`solver_info`, :func:`solve_result_from`)
    because the anytime-solve job machine (``solve_jobs.py``) and the study runner
    (``generator/evaluate.py``) both drive the solver step-by-step rather than through
    :func:`solve_scenario`, and need the same argument/encoding/result plumbing.

    ``clingo_args``, when given, *replaces* the ADR-0007 default portfolio wholesale —
    the expert-mode command-line override (ADR-0023). ``threads`` is then unused for the
    portfolio itself but still selects the default a caller falls back to.

    ``horizon`` is passed as a solver constant so the horizon length lives in exactly one
    place — :class:`Scenario`. The forward-planning encoding does not reference it (only the
    Python-side completion table and bounds do), but it is still passed for ADR-0008
    consistency and for the dormant wrap-around encoding. It is appended *after* the
    caller's args, which is also why ``clingo_args`` must not itself set constants
    (validated in ``api/schemas.py``) — clingo errors on a redefinition.

    ``extra_args`` are flags the active preference mechanism needs (ADR-0032):
    ``--heuristic=Domain``, or ``-c pref_level=N``. They come from
    :func:`prepare_preferences`, not from the user, so they are appended to the portfolio
    rather than replacing it — an expert command line and a preference-aware re-solve are
    independent choices.

    Pure and solver-free so it can be unit-tested directly (see ``tests/test_service.py``).
    """
    portfolio = clingo_args if clingo_args is not None else _portfolio_args(threads)
    return [*portfolio, *(extra_args or []), "-c", f"horizon={scenario.horizon_hours}"]


def solver_info(
    threads: int, time_limit_s: float, *, clingo_args: list[str] | None = None
) -> SolverInfo:
    """The solver configuration echoed to the client for expert-mode display.

    For a custom command line (``clingo_args``), ``threads``/``config`` are recovered from
    the args themselves on a best-effort basis (:func:`_parse_threads` / :func:`_parse_config`)
    rather than trusted from the caller — the whole point of the override is that the
    caller no longer necessarily matches the ADR-0007 portfolio shape.
    """
    if clingo_args is not None:
        return SolverInfo(
            threads=_parse_threads(clingo_args),
            config=_parse_config(clingo_args),
            args=clingo_args,
            time_limit_s=time_limit_s,
        )
    return SolverInfo(
        threads=threads,
        config="many" if threads > 1 else DETERMINISTIC_CONFIG,
        args=_portfolio_args(threads),
        time_limit_s=time_limit_s,
    )


def solve_result_from(
    scenario: Scenario,
    result: ClingconResult,
    info: SolverInfo,
    *,
    preferences: SolvePreferences | None = None,
    dropped: list[DroppedPreference] | None = None,
) -> SolveResult:
    """Map a raw :class:`ClingconResult` onto the API-facing :class:`SolveResult`.

    When the solve carried preferences, the agreement is **recomputed here** from the
    returned tasks rather than shown out of the encoding (ADR-0032): the overlays add no
    ``#show``, so ``parse.py`` and the pinned ``SHOWN_PREDICATES`` contract stay untouched.

    Pure (given the parsed atoms) and solver-free — unit-tested in ``tests/test_service.py``.
    """

    def report(tasks: list) -> PreferenceReport | None:
        if preferences is None or not preferences.tasks:
            return None
        # The level travels with the result because it decides how `Schedule.cost` may be
        # read (a leading slot at "top", folded into a service slot otherwise), and the
        # client's current selection need not be the one this result came from.
        weak = preferences.mode == "weak"
        return PreferenceReport(
            agreement=preference_agreement(preferences, tasks),
            dropped=list(dropped or []),
            level=preferences.level if weak else None,
            pref_level=PREFERENCE_LEVELS[preferences.level] if weak else None,
        )

    if not result.solved:
        # No schedule, so nothing was kept — but the dropped list still explains which
        # edits could never have been honoured, which is the useful half on a timeout.
        return SolveResult(
            scenario_name=scenario.name,
            horizon_hours=scenario.horizon_hours,
            solved=False,
            status=result.status,
            solve_time_s=result.solve_time_s,
            solver=info,
            preferences=report([]),
        )

    schedule = parse_schedule(result.atoms, result.cost)
    return SolveResult(
        scenario_name=scenario.name,
        horizon_hours=scenario.horizon_hours,
        solved=True,
        optimal=result.status == "optimal",
        status=result.status,
        solve_time_s=result.solve_time_s,
        solver=info,
        schedule=schedule,
        # The instance-independent quality axis (ADR-0016) — comparable across rolls where
        # the raw `schedule.cost` is not (its length tracks the priorities present, and
        # `weak@top` prepends a slot).
        quality=score_schedule(scenario, schedule.tasks).slots,
        preferences=report(schedule.tasks),
    )


def solve_scenario(
    scenario: Scenario,
    *,
    time_limit_s: float = 20.0,
    threads: int = 4,
    clingo_args: list[str] | None = None,
    preferences: SolvePreferences | None = None,
) -> SolveResult:
    """Solve one scenario with the forward-planning clingcon encoding (ADR-0015).

    ``threads > 1`` uses the ``--configuration=many`` portfolio (fast, proves optimum on
    good instances, but the exact schedule among equal optima can vary between runs).
    ``threads=1`` is fully reproducible but slower, and runs under
    ``--configuration=jumpy`` (:data:`DETERMINISTIC_CONFIG`, ADR-0041).

    ``clingo_args``, when given, overrides the whole portfolio with a caller-supplied
    clingo command line (the expert-mode override, ADR-0023); ``threads`` is then ignored
    for the portfolio itself. May raise ``RuntimeError`` if clingo rejects the args.

    ``preferences`` carries the user's schedule edits into the solve (ADR-0031/0032).
    Omitting it — or passing ``mode="off"`` — leaves the program and the command line
    byte-identical to a solve without this feature.
    """
    # Re-validate: a scenario mutated after construction, or built by a future generator,
    # is checked before it reaches the solver (ADR-0010). Cheap — the scenario is small.
    Scenario.model_validate(scenario.model_dump())

    setup = prepare_preferences(scenario, preferences)
    solver = ClingconSolver(
        control_args(scenario, threads, clingo_args=clingo_args, extra_args=setup.args)
    )
    solver.add(encoding_text())
    solver.add(render_instance(scenario))
    for program in setup.programs:
        solver.add(program)
    result = solver.solve(time_limit_s=time_limit_s)
    return solve_result_from(
        scenario,
        result,
        solver_info(threads, time_limit_s, clingo_args=clingo_args),
        preferences=preferences,
        dropped=setup.dropped,
    )
