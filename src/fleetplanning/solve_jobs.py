"""In-memory registry for anytime, cancellable solves (ADR-0022).

This is a single-operator demonstrator, not a multi-tenant service (consistent with "no
result caching", ``docs/design-choices.md`` D2) — so there is **at most one active job at
a time**. Starting a new solve cancels and discards whatever job was running; a ``job_id``
is still handed out (not a bare global) so a poll from a superseded job gets a clear 404
instead of silently reading the new job's state — the same identity-check discipline
``App.tsx`` already applies to its ``AbortController`` ref.

The actual clingo mechanics (thread-safety, the cancel/finalize idiom) live in
:class:`fleetplanning.solver.runner.AsyncSolve` — this module only tracks *which* job is
current and maps its raw :class:`~fleetplanning.solver.runner.ClingconResult` onto the
API-facing :class:`~fleetplanning.model.SolveResult` via ``service.solve_result_from``,
the same mapping the blocking ``/api/solve`` path used before this feature (and still
uses, for callers that want a single blocking call — the offline study, the CLI).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from fleetplanning.model import (
    DroppedPreference,
    Scenario,
    SolvePreferences,
    SolveResult,
    SolverInfo,
)
from fleetplanning.service import (
    control_args,
    encoding_text,
    prepare_preferences,
    solve_result_from,
    solver_info,
)
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.runner import AsyncSolve, ClingconSolver


class JobNotFound(Exception):
    """Raised for an unknown or superseded ``job_id`` — the API maps this to a 404."""


@dataclass
class SolveJobStatus:
    job_id: str
    done: bool
    result: SolveResult | None = None


@dataclass
class _Job:
    id: str
    scenario: Scenario
    solver_info: SolverInfo
    async_solve: AsyncSolve
    # Held for the life of the job so every poll can re-score the best-so-far schedule
    # against what the user asked for (ADR-0031).
    preferences: SolvePreferences | None = None
    dropped: list[DroppedPreference] = field(default_factory=list)


_lock = threading.Lock()
_current: _Job | None = None


def start(
    scenario: Scenario,
    *,
    time_limit_s: float = 20.0,
    threads: int = 4,
    clingo_args: list[str] | None = None,
    preferences: SolvePreferences | None = None,
) -> str:
    """Cancel any job already running, start a new one, return its id.

    Grounding happens synchronously here (fast for the toy-scale instances this
    demonstrator targets — see ``docs/roadmap.md`` on scaling); only the search itself
    runs asynchronously.

    ``clingo_args`` is the expert-mode command-line override (ADR-0023) — see
    ``service.solve_scenario`` for the same parameter. Constructing/grounding with a bad
    clingo arg list raises ``RuntimeError``; the caller (``api/app.py``) maps that to a
    400 rather than letting it surface as an unhandled 500.

    ``preferences`` carries the user's schedule edits (ADR-0031/0032). The job holds on to
    them so every ``poll`` can report agreement against the best-so-far schedule — the
    anytime UI shows "6 of 9 edits kept" improving alongside the Gantt, not only at the end.
    """
    # Re-validate before grounding — mirrors service.solve_scenario (ADR-0010). A scenario
    # reconstructed from an API request body (ADR-0024) is already validated by pydantic,
    # but a mutated-in-place one would not be; this is the single guard for both entry
    # points into the async path.
    scenario = Scenario.model_validate(scenario.model_dump())

    global _current
    with _lock:
        if _current is not None:
            _current.async_solve.cancel()  # best-effort, returns promptly

        setup = prepare_preferences(scenario, preferences)
        solver = ClingconSolver(
            control_args(scenario, threads, clingo_args=clingo_args, extra_args=setup.args)
        )
        solver.add(encoding_text())
        solver.add(render_instance(scenario))
        for program in setup.programs:
            solver.add(program)
        async_solve = solver.solve_async(time_limit_s)

        job_id = uuid.uuid4().hex
        _current = _Job(
            id=job_id,
            scenario=scenario,
            solver_info=solver_info(threads, time_limit_s, clingo_args=clingo_args),
            async_solve=async_solve,
            preferences=preferences,
            dropped=setup.dropped,
        )
        return job_id


def _get(job_id: str) -> _Job:
    with _lock:
        job = _current
    if job is None or job.id != job_id:
        raise JobNotFound(job_id)
    return job


def poll(job_id: str) -> SolveJobStatus:
    """Non-blocking check-in — the ~2s poll from the frontend."""
    job = _get(job_id)
    done, raw = job.async_solve.poll()
    result = (
        solve_result_from(
            job.scenario,
            raw,
            job.solver_info,
            preferences=job.preferences,
            dropped=job.dropped,
        )
        if raw is not None
        else None
    )
    return SolveJobStatus(job_id=job_id, done=done, result=result)


def cancel(job_id: str) -> SolveJobStatus:
    """The stop-button endpoint: end the search early, keep the best model found."""
    job = _get(job_id)
    raw = job.async_solve.cancel()
    result = solve_result_from(
        job.scenario, raw, job.solver_info, preferences=job.preferences, dropped=job.dropped
    )
    return SolveJobStatus(job_id=job_id, done=True, result=result)
