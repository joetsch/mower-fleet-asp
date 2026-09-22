"""Score a schedule against the objective of ``golf_schedule_forward_model.lp``, in Python.

Why this exists (ADR-0016)
--------------------------
The clingcon encoding optimises a lexicographic cost vector, but its raw length is
*instance-dependent* — one slot per weak-constraint level that actually grounds
(``docs/known-hazards.md``). For the performance study we need a **fixed, comparable**
quality number for every schedule, including ones the ASP solver never produced (the
greedy baseline). So we re-implement the objective here and score any task list on the
same five slots::

    [ max-interval @ P1 , max-interval @ P2 , max-interval @ P3 , avoid , min-interval ]

"max-interval" folds the three violation sources the encoding charges to level ``5-P``
(a too-large gap between consecutive starts, ``first_task_too_late`` against the service
history, ``last_task_too_early`` against the horizon end). "min-interval" folds the two
sources at level ``0`` (a too-small gap, ``first_task_too_early``). "avoid" is level ``1``
— one unit per task that works any hour inside an avoid window.

The study always *recomputes* quality with this function from the returned schedule; it
never tries to reconstruct the padded form from clingcon's variable-length vector. The
two are cross-checked on a solved instance in ``tests/test_solver_score.py``.

The ``violations`` list uses the same three kinds and the same (area, task) folding as
``solver/parse.py``, so a greedy :class:`~fleetplanning.model.Schedule` and an ASP one
are directly comparable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from fleetplanning.model import Scenario, ScheduledTask, Violation
from fleetplanning.solver.completion_table import CompletionRow, build_completion_table


@dataclass
class ScoreVector:
    """The fixed 5-slot quality vector, a per-source breakdown, and the violation list."""

    max_by_priority: tuple[int, int, int]  # index i -> priority i + 1
    avoid: int
    min_interval: int
    detail: dict[str, int] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)

    @property
    def slots(self) -> list[int]:
        """``[max@P1, max@P2, max@P3, avoid, min]`` — lexicographic, most significant first."""
        return [*self.max_by_priority, self.avoid, self.min_interval]

    @property
    def total(self) -> int:
        return sum(self.slots)


def score_schedule(
    scenario: Scenario,
    tasks: Sequence[ScheduledTask],
    *,
    completion_rows: list[CompletionRow] | None = None,
) -> ScoreVector:
    """Score ``tasks`` (an ASP or greedy schedule) on the forward-model objective.

    ``completion_rows`` supplies the per-task avoid-hour count and each area's latest
    feasible start; it is rebuilt from the scenario when not passed in.
    """
    rows = completion_rows if completion_rows is not None else build_completion_table(scenario)

    latest_start: dict[str, int] = {}
    avoid_lookup: dict[tuple[str, str, int], int] = {}
    for r in rows:
        if r.start > latest_start.get(r.area, -1):
            latest_start[r.area] = r.start
        avoid_lookup[(r.area, r.mower, r.start)] = r.avoid_hours

    hist_start = {ev.area: ev.start for ev in scenario.history}

    by_area: dict[str, list[ScheduledTask]] = defaultdict(list)
    for t in tasks:
        by_area[t.area].append(t)

    max_counts = [0, 0, 0]
    min_count = 0
    avoid_count = 0
    detail: dict[str, int] = {
        k: 0
        for k in (
            "max_interval",
            "first_task_too_late",
            "last_task_too_early",
            "min_interval",
            "first_task_too_early",
            "avoid_zone",
        )
    }
    seen: set[tuple[str, str, int]] = set()
    violations: list[Violation] = []

    def record(kind: str, area: str, task: int) -> None:
        key = (kind, area, task)
        if key not in seen:
            seen.add(key)
            violations.append(Violation(kind=kind, area=area, task=task))

    for area in scenario.areas:
        ts = sorted(by_area.get(area.name, []), key=lambda t: t.task)
        if not ts:
            continue
        slot = area.priority - 1

        for a, b in zip(ts, ts[1:], strict=False):
            gap = b.start - a.start
            if gap > area.max_interval:
                max_counts[slot] += 1
                detail["max_interval"] += 1
                record("max_interval", area.name, a.task)
            if gap < area.min_interval:
                min_count += 1
                detail["min_interval"] += 1
                record("min_interval", area.name, a.task)

        first, last = ts[0], ts[-1]
        h = hist_start[area.name]
        if first.start > h + area.max_interval:
            max_counts[slot] += 1
            detail["first_task_too_late"] += 1
            record("max_interval", area.name, first.task)
        if first.start < h + area.min_interval:
            min_count += 1
            detail["first_task_too_early"] += 1
            record("min_interval", area.name, first.task)

        latest = latest_start.get(area.name)
        if latest is not None and last.start < latest - area.max_interval:
            max_counts[slot] += 1
            detail["last_task_too_early"] += 1
            record("max_interval", area.name, last.task)

        for t in ts:
            if avoid_lookup.get((t.area, t.mower, t.start), 0) > 0:
                avoid_count += 1
                detail["avoid_zone"] += 1
                record("avoid_zone", area.name, t.task)

    violations.sort(key=lambda v: (v.kind, v.area, v.task or 0))
    return ScoreVector(
        max_by_priority=(max_counts[0], max_counts[1], max_counts[2]),
        avoid=avoid_count,
        min_interval=min_count,
        detail=detail,
        violations=violations,
    )
