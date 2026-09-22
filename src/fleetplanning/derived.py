"""Figures computed from a :class:`Scenario` — never stored, always recomputed (ADR-0024).

The editor lets the user change any input value and must show honest numbers back
immediately, so the figures the read-only library view used to take from the generator
source (``ScenarioBundle.detail``) are re-derived here from the compiled scenario instead:

- ``service_bounds`` — per-area ``(min, max)`` service starts for the week. The user-owned
  ``Area.min_services`` / ``max_services`` when set, else the ADR-0017 derivation.
- ``load_factor`` — the generator's demand/capacity proxy (``generator/loadfactor.py``)
  re-expressed on the compiled scenario. Needs ``size_m2`` + mower rate; ``None`` without
  them.

Both are pure functions of the scenario; measured at 1.8-27 ms across the curated library,
so the API recomputes them on every edit rather than caching. The generator's
``capability_density`` / ``structurally_unsat`` are *not* re-derived: the first is defined
against group + cut-height eligibility (recipe concepts the compiled model drops), and the
second is always ``False`` for a valid ``Scenario`` (the one-history-event-per-area rule
guarantees every area has a capable mower).
"""

from __future__ import annotations

from statistics import fmean

from fleetplanning.model import DAYS, Scenario, _covered_hours
from fleetplanning.solver.bounds import service_count_bounds
from fleetplanning.solver.completion_table import CompletionRow


def service_bounds(
    scenario: Scenario, *, completion_rows: list[CompletionRow] | None = None
) -> dict[str, tuple[int, int]]:
    """``{area_name: (min_starts, max_starts)}``.

    An area's explicit ``min_services`` / ``max_services`` override the derived value;
    either may be set alone, with the other falling back to the derivation. Pass
    ``completion_rows`` to reuse an already-built table (the instance emitter does).
    """
    explicit_min = {a.name: a.min_services for a in scenario.areas if a.min_services is not None}
    explicit_max = {a.name: a.max_services for a in scenario.areas if a.max_services is not None}
    if len(explicit_min) == len(scenario.areas) and len(explicit_max) == len(scenario.areas):
        return {a.name: (explicit_min[a.name], explicit_max[a.name]) for a in scenario.areas}

    derived = service_count_bounds(scenario, completion_rows=completion_rows)
    out: dict[str, tuple[int, int]] = {}
    for area in scenario.areas:
        lo, hi = derived[area.name]
        lo = explicit_min.get(area.name, lo)
        hi = explicit_max.get(area.name, hi)
        out[area.name] = (lo, max(lo, hi))
    return out


def _available_fraction(scenario: Scenario) -> dict[str, float]:
    """Share of the planning week an area is usable (not in a ``no_go`` window),
    averaged over the 7 weekdays. Matches the generator's AP+ANP fraction for a
    weekday-uniform schedule."""
    out: dict[str, float] = {}
    for area in scenario.areas:
        blocked = sum(len(_covered_hours(area.schedule[d].no_go)) for d in DAYS)
        out[area.name] = (7 * 24 - blocked) / (7 * 24)
    return out


def load_factor(scenario: Scenario) -> float | None:
    """Demand / capacity (``generator/loadfactor.py``), re-expressed on the compiled
    scenario. ``None`` if any ``size_m2`` or mower rate is missing; ``inf`` if capacity
    is zero."""
    if any(a.size_m2 is None for a in scenario.areas):
        return None
    working = [m for m in scenario.mowers if m.can_mow]
    if any(m.area_capacity_m2_per_day is None for m in working):
        return None

    avail = _available_fraction(scenario)
    demand = sum(a.size_m2 * 24 / a.max_interval for a in scenario.areas)
    capacity = sum(
        m.area_capacity_m2_per_day * fmean(avail[a] for a in m.can_mow) for m in working
    )
    if capacity <= 0:
        return float("inf")
    return demand / capacity
