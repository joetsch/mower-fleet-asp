"""Turn a :class:`Scenario` into clingcon input facts — notebook cell 25.

Emitted predicates (consumed by ``golf_schedule_forward_model.lp``; the dormant
``golf_schedule_wrap_around_model.lp`` consumes the same facts):

======================  =========================================================
``area/1``              an area exists
``priority/2``          area priority (1 = highest)
``min_interval/2``      minimum hours between service starts
``max_interval/2``      maximum hours between service starts
``mower/1``             a mower exists
``capable/2``           ``capable(M, A)`` — mower M can service area A
``last/4``              ``last(A, M, S, C)`` — last service of A by M, started S, done C
``min_starts/2``        lower bound on services for A in the horizon
``max_starts/2``        upper bound on services for A in the horizon
``completion/5``        ``completion(A, M, S, C, AH)`` — feasible start S, completion C,
                        AH worked hours in "avoid" windows
======================  =========================================================
"""

from __future__ import annotations

from fleetplanning.derived import service_bounds
from fleetplanning.model import Scenario
from fleetplanning.solver.completion_table import (
    CompletionRow,
    build_completion_table,
    mower_busy_until,
)


def _q(name: str) -> str:
    return f'"{name}"'


def earliest_starts(scenario: Scenario) -> dict[str, int]:
    """Per area, the first start hour the solver may consider.

    A service still running at t = 0 censors every earlier start option, so those
    ``completion/5`` rows are never emitted. Shared with ``solver/preferences.py`` so
    that "an hour the solver can actually use" means the same thing in both places —
    a preference must be judged against the rows the encoding really has.
    """
    return {ev.area: max(0, ev.completion) for ev in scenario.history}


def render_instance(scenario: Scenario, completion_rows: list[CompletionRow] | None = None) -> str:
    """Render the scenario as a clingcon fact program (a ``.lp`` string)."""
    rows = build_completion_table(scenario) if completion_rows is None else completion_rows
    bounds = service_bounds(scenario, completion_rows=rows)
    # Skip a start before the area's own in-progress service finishes, or before the
    # assigned mower is free from an in-progress service elsewhere.
    earliest_start = earliest_starts(scenario)
    mower_free = mower_busy_until(scenario)

    lines: list[str] = [f"% instance: {scenario.name}", ""]

    lines.append("% --- areas ---")
    for area in scenario.areas:
        a = _q(area.name)
        lines += [
            f"area({a}).",
            f"priority({a},{area.priority}).",
            f"min_interval({a},{area.min_interval}).",
            f"max_interval({a},{area.max_interval}).",
        ]
    lines.append("")

    lines.append("% --- mowers and capabilities ---")
    for mower in scenario.mowers:
        m = _q(mower.name)
        lines.append(f"mower({m}).")
        for area_name in mower.can_mow:
            lines.append(f"capable({m},{_q(area_name)}).")
    lines.append("")

    lines.append("% --- service history ---")
    for ev in scenario.history:
        lines.append(f"last({_q(ev.area)},{_q(ev.mower)},{ev.start},{ev.completion}).")
    lines.append("")

    lines.append("% --- service-count bounds ---")
    for area in scenario.areas:
        min_starts, max_starts = bounds[area.name]
        a = _q(area.name)
        lines += [f"min_starts({a},{min_starts}).", f"max_starts({a},{max_starts})."]
    lines.append("")

    lines.append("% --- completion table ---")
    for row in rows:
        if row.start < max(earliest_start.get(row.area, 0), mower_free.get(row.mower, 0)):
            continue
        lines.append(
            f"completion({_q(row.area)},{_q(row.mower)},{row.start},{row.completion},{row.avoid_hours})."
        )
    lines.append("")

    return "\n".join(lines)
