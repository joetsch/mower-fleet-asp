"""Tests for the single-active-job registry (ADR-0022: anytime solving / stop button).

The clingo-facing mechanics (thread safety, the poll/cancel idiom) are pinned in
``test_solver_status.py`` against :class:`~fleetplanning.solver.runner.AsyncSolve`
directly. These tests cover what this module adds on top: the ``job_id`` handle, "at
most one job at a time" (a new solve supersedes the old one), and the 404-shaped
``JobNotFound`` for a stale or unknown id.
"""

from __future__ import annotations

import time

import pytest

from fleetplanning import solve_jobs
from fleetplanning.scenarios.toy_course import toy_course

pytestmark = pytest.mark.slow


def _poll_until(job_id: str, *, timeout_s: float = 15.0):
    deadline = time.perf_counter() + timeout_s
    status = solve_jobs.poll(job_id)
    while time.perf_counter() < deadline and not status.done and status.result is None:
        time.sleep(0.02)
        status = solve_jobs.poll(job_id)
    return status


def test_start_returns_a_job_id_and_poll_reports_a_best_so_far():
    # threads=1 + no early proof (under jumpy, ADR-0041, toy_course reaches its first
    # incumbent in ~1s but takes ~10s to prove, same margin note as test_solver_status.py)
    # so a poll within the 15s budget reliably lands mid-search.
    job_id = solve_jobs.start(toy_course(), time_limit_s=15.0, threads=1)
    status = _poll_until(job_id)
    assert status.job_id == job_id
    assert status.done is False
    assert status.result is not None
    assert status.result.solved is True
    assert status.result.optimal is False

    solve_jobs.cancel(job_id)  # tidy up rather than leaving the search running


def test_starting_a_new_job_cancels_and_supersedes_the_previous_one():
    first_id = solve_jobs.start(toy_course(), time_limit_s=15.0, threads=1)
    second_id = solve_jobs.start(toy_course(), time_limit_s=1.0, threads=1)

    assert first_id != second_id
    with pytest.raises(solve_jobs.JobNotFound):
        solve_jobs.poll(first_id)

    # The second job is unaffected and behaves normally.
    status = _poll_until(second_id)
    assert status.job_id == second_id
    solve_jobs.cancel(second_id)


def test_cancel_stops_the_job_and_returns_the_best_so_far():
    job_id = solve_jobs.start(toy_course(), time_limit_s=15.0, threads=1)
    _poll_until(job_id)  # make sure a model has been found before stopping

    status = solve_jobs.cancel(job_id)
    assert status.done is True
    assert status.result is not None
    assert status.result.solved is True

    # Cancel is the stop button — a further poll on the same job reports the same
    # finished result rather than erroring.
    again = solve_jobs.poll(job_id)
    assert again.done is True
    assert again.result == status.result


def test_start_with_clingo_args_reflects_the_override_in_solver_info():
    """The expert-mode command-line override (ADR-0023) end to end through the job
    registry: the echoed ``SolverInfo`` matches the override, not the ``threads=`` default
    (which is passed here too, to confirm it's ignored when ``clingo_args`` is given).
    """
    job_id = solve_jobs.start(
        toy_course(), time_limit_s=1.0, threads=4, clingo_args=["-t1"]
    )
    status = _poll_until(job_id)
    assert status.result is not None
    assert status.result.solver is not None
    assert status.result.solver.threads == 1
    assert status.result.solver.config is None
    assert status.result.solver.args == ["-t1"]

    solve_jobs.cancel(job_id)  # tidy up rather than leaving the search running


def test_poll_unknown_job_raises_job_not_found():
    with pytest.raises(solve_jobs.JobNotFound):
        solve_jobs.poll("does-not-exist")


def test_cancel_unknown_job_raises_job_not_found():
    with pytest.raises(solve_jobs.JobNotFound):
        solve_jobs.cancel("does-not-exist")
