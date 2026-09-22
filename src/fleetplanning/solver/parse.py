"""Turn clingcon's shown atoms into a :class:`Schedule` — notebook cell 33.

The forward-planning encoding (``golf_schedule_forward_model.lp``, ADR-0015) shows:

- ``schedule(Area, Task, Mower, Start, Completion)``
- ``max_interval_violation(Area, Task)`` / ``min_interval_violation(Area, Task)``
- ``avoid_zone_violation(Area, Task)``
- ``first_task_too_late(Area)`` / ``first_task_too_early(Area)`` /
  ``last_task_too_early(Area)`` — area-level start-boundary violations against the service
  history and the horizon end. These carry no task index; we fold them into the existing
  ``max_interval`` / ``min_interval`` kinds (owner's call — no separate colour/symbol in
  the UI) and attach them to that area's first or last task, so downstream code that keys
  violations by ``(area, task)`` needs no change.

This is a pure transform over a list of ``clingo.Symbol`` — it does not depend on the
solver runner.
"""

from __future__ import annotations

import clingo

from fleetplanning.model import Schedule, ScheduledTask, Violation

# task-level violation atoms: name -> kind
_VIOLATION_KIND = {
    "max_interval_violation": "max_interval",
    "min_interval_violation": "min_interval",
    "avoid_zone_violation": "avoid_zone",
}

# area-level boundary atoms (arity 1): name -> (kind, which task of the area it attaches to)
_AREA_VIOLATION = {
    "first_task_too_late": ("max_interval", "first"),
    "first_task_too_early": ("min_interval", "first"),
    "last_task_too_early": ("max_interval", "last"),
}


def parse_schedule(atoms: list[clingo.Symbol], cost: list[int]) -> Schedule:
    tasks: list[ScheduledTask] = []
    violations: list[Violation] = []
    area_violations: list[tuple[str, str, str]] = []  # (kind, area, "first" | "last")

    for atom in atoms:
        if atom.type != clingo.SymbolType.Function:
            continue
        args = atom.arguments
        if atom.name == "schedule" and len(args) == 5:
            area, task, mower, start, end = args
            tasks.append(
                ScheduledTask(
                    area=area.string,
                    task=task.number,
                    mower=mower.string,
                    start=start.number,
                    end=end.number,
                )
            )
        elif atom.name in _VIOLATION_KIND and len(args) == 2:
            area, task = args
            violations.append(
                Violation(
                    kind=_VIOLATION_KIND[atom.name],
                    area=area.string,
                    task=task.number,
                )
            )
        elif atom.name in _AREA_VIOLATION and len(args) == 1:
            kind, which = _AREA_VIOLATION[atom.name]
            area_violations.append((kind, args[0].string, which))

    # Resolve area-level boundary violations to a concrete task once every schedule atom
    # is in: "first" -> lowest task index for that area, "last" -> highest.
    tasks_by_area: dict[str, list[int]] = {}
    for t in tasks:
        tasks_by_area.setdefault(t.area, []).append(t.task)
    for kind, area, which in area_violations:
        indices = tasks_by_area.get(area)
        if not indices:
            continue
        violations.append(
            Violation(kind=kind, area=area, task=min(indices) if which == "first" else max(indices))
        )

    # A folded area-level violation can coincide exactly with a task-level one of the same
    # kind (e.g. first_task_too_early and min_interval_violation both landing on task 1) —
    # the UI draws them identically, so collapse exact duplicates.
    seen: set[tuple[str, str, int | None]] = set()
    unique: list[Violation] = []
    for v in violations:
        key = (v.kind, v.area, v.task)
        if key not in seen:
            seen.add(key)
            unique.append(v)

    tasks.sort(key=lambda t: (t.area, t.task))
    unique.sort(key=lambda v: (v.kind, v.area, v.task or 0))
    return Schedule(tasks=tasks, violations=unique, cost=list(cost))
