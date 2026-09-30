"""Regression for the relaxed service-count floor (min_starts, bounds.py).

Marked ``slow`` -- it runs the real solver. Before the floor was relaxed to 1, a
contended area's derived min_starts == cadence, which could force in a service the
optimum didn't want. On compact-single-mower this produced an awkward min-interval
undercut on H2_SR2 with no benefit to the objective (owner report, confirmed by hand:
the forced task cost 0 on the max-interval level and 1 on the min-interval level, purely
because min_starts required it). Run just this with:
    uv run pytest -m slow tests/test_min_service_floor.py
"""

from __future__ import annotations

import pytest

from fleetplanning.scenarios import registry
from fleetplanning.service import solve_scenario

pytestmark = pytest.mark.slow


def test_compact_single_mower_has_no_min_interval_violations():
    scenario, _ = registry.load("compact-single-mower")
    # threads=1 for a reproducible schedule (ADR-0041) rather than the many-portfolio's
    # per-run choice among equal optima.
    result = solve_scenario(scenario, time_limit_s=30.0, threads=1)
    assert result.solved
    assert result.schedule is not None
    kinds = {v.kind for v in result.schedule.violations}
    assert "min_interval" not in kinds
