"""Bounds on the number of service starts per area (notebook cell 21; overhauled ADR-0017).

The forward encoding does ``{ num_starts(A,N) : N = Min..Max } = 1`` — it must pick a
count in ``[Min, Max]`` for every area. That makes these bounds load-bearing in two ways:

- **``Min`` defines the required service level.** An area with ``num_starts = 0`` derives
  no ``task/2`` atoms and so incurs *zero* objective cost — without a positive ``Min`` the
  empty schedule is "optimal". ``Min`` is therefore not a bound on a pre-existing optimum;
  it is the floor that makes the problem meaningful. It must be **jointly feasible** across
  the shared fleet, or the whole program is UNSAT — never an acceptable outcome.
- **``Max`` caps the ground-program size** (``task``/``asg``/``start``/``completion`` per
  task, and the quadratic no-overlap constraint). It must still *contain the optimum*.

Approach
--------
``cadence(A)``    — services at ``max_interval`` spacing from the first deadline: the
                   fewest starts that give area A zero max-interval violations *in
                   isolation*.
``greedy(A)``     — how often the greedy baseline (``solver/greedy.py``) actually served A.
                   The greedy schedule is a concrete, always-feasible witness.

``min_starts(A) = min(1, cadence(A), greedy(A))``
    The floor is **not** the zero-violation cadence (ADR-0017 amendment): with a shared
    fleet, a contended area's optimum often can't afford full cadence, and forcing that
    many starts in anyway bought nothing on the max-interval objective (one gap over the
    limit costs the same whether or not a later, now-redundant task follows) while
    manufacturing awkward min-interval undercuts as the solver parks the forced task
    wherever is cheapest. One start is the real floor: it is enough to switch on the
    area's max-interval boundary terms (``first_over``/``last_under`` in the encoding)
    so it isn't invisible to the objective, and the optimizer decides from there how many
    *more* are worth adding. Still feasible by construction (a single start is never
    denser than the old, already-feasible floor) and never UNSAT: an area with cadence or
    greedy count 0 (not due, or no feasible start) still gets ``min_starts = 0``.

``max_starts(A) = min(physical_max(A), n_feasible_starts(A), max(cadence, greedy) + SLACK)``
    Contains the optimum: beyond ``cadence`` a denser schedule only adds avoid hours and
    min-interval violations (both strictly dominated) — the optimum never wants more than
    the cadence plus a few "bridging" services across no-go gaps, which ``SLACK`` covers.
    ``n_feasible_starts`` also forces ``(0, 0)`` for a structurally impossible area
    (no capable mower / no feasible start), so the solver cannot pick ``num_starts > 0``
    there and hit UNSAT.

Pass ``tight=False`` for the provably-safe-but-loose ``max_starts = physical_max`` — used
by the study to check the tight bounds do not move the proven optimum.
"""

from __future__ import annotations

from fleetplanning.model import Scenario
from fleetplanning.solver.completion_table import CompletionRow, build_completion_table
from fleetplanning.solver.greedy import _count, greedy_schedule

__all__ = ["service_count_bounds", "SLACK"]

# Headroom above the zero-violation cadence, for "bridging" services across no-go gaps
# (see the module docstring / ADR-0017). Small: the optimum is dominated beyond this.
SLACK = 3


def service_count_bounds(
    scenario: Scenario,
    *,
    completion_rows: list[CompletionRow] | None = None,
    greedy_counts: dict[str, int] | None = None,
    tight: bool = True,
) -> dict[str, tuple[int, int]]:
    """Return ``{area_name: (min_starts, max_starts)}`` (see the module docstring)."""
    last_start = {ev.area: ev.start for ev in scenario.history}
    missing = [a.name for a in scenario.areas if a.name not in last_start]
    if missing:
        raise ValueError(f"service history missing for areas: {missing}")

    rows = completion_rows if completion_rows is not None else build_completion_table(scenario)
    starts_by_area: dict[str, set[int]] = {}
    for r in rows:
        starts_by_area.setdefault(r.area, set()).add(r.start)
    n_feasible = {a: len(s) for a, s in starts_by_area.items()}

    if greedy_counts is None:
        greedy_counts = {}
        for t in greedy_schedule(scenario, completion_rows=rows).tasks:
            greedy_counts[t.area] = greedy_counts.get(t.area, 0) + 1

    horizon = scenario.horizon_hours
    bounds: dict[str, tuple[int, int]] = {}
    for area in scenario.areas:
        s = last_start[area.name]
        cadence = _count(s + area.max_interval, area.max_interval, horizon)
        physical = _count(s + area.min_interval, area.min_interval, horizon)
        feasible = n_feasible.get(area.name, 0)
        greedy = greedy_counts.get(area.name, 0)

        min_starts = min(1, cadence, greedy)
        if tight:
            max_starts = min(physical, feasible, max(cadence, greedy) + SLACK)
        else:
            max_starts = min(physical, feasible)
        bounds[area.name] = (min_starts, max(min_starts, max_starts))
    return bounds
