"""The greedy baseline scheduler (ADR-0016).

A deliberately simple constructive heuristic, to answer "what does the ASP optimisation
actually buy over a sensible dispatch rule?" in the performance study. It also serves as
the **feasibility witness** for ``bounds.service_count_bounds`` (ADR-0017), so it must not
depend on that module.

**Priority-tier-first, then earliest-deadline-first (EDF).** One monotonic forward pass
over ``now``, no backtracking. It reuses ``build_completion_table`` — the same feasible
``start -> completion`` options the ASP model grounds — so no-go pauses and avoid-hour
counting are identical, and the two schedules are scored on one scale (``solver/score.py``).

Per step at time ``now``:

1. an area is **capped at its cadence** (``ceil`` of the horizon over ``max_interval``) —
   a sensible dispatcher does not over-service, and the cap stops a high-priority area
   monopolising a shared mower;
2. candidates are the areas that are *due* (``prev_start + max_interval <= now``), or, if
   none are due, the areas that are *ready* (past their ``min_interval`` and with a mower
   free now);
3. pick the smallest ``(priority, deadline, -longest_job, name)`` and give it the capable
   mower that *completes* it earliest;
4. when nothing is due or ready, advance ``now`` to the next moment something can happen.
"""

from __future__ import annotations

from bisect import bisect_left

from fleetplanning.model import Scenario, Schedule, ScheduledTask
from fleetplanning.solver.completion_table import (
    CompletionRow,
    build_completion_table,
    mower_busy_until,
)
from fleetplanning.solver.score import score_schedule


def _count(first_start: int, step: int, horizon: int) -> int:
    """Number of ``step``-spaced starts from ``first_start`` (clamped to 0) below ``horizon``."""
    cursor = max(0, first_start)
    n = 0
    while cursor < horizon:
        n += 1
        cursor += step
    return n


def greedy_schedule(
    scenario: Scenario, *, completion_rows: list[CompletionRow] | None = None
) -> Schedule:
    """Build a baseline weekly schedule for ``scenario`` — deterministic, no RNG.

    Used both as the study baseline and as the feasibility witness for
    ``bounds.service_count_bounds`` (ADR-0017), so it must not depend on that module.
    """
    rows = completion_rows if completion_rows is not None else build_completion_table(scenario)
    horizon = scenario.horizon_hours
    history = {ev.area: ev for ev in scenario.history}
    # Cadence cap: the number of starts at max_interval spacing from the first deadline —
    # the natural service target. A sensible dispatcher serves each area this often, no
    # more; capping here also keeps a high-priority area from starving a shared mower.
    cap = {
        a.name: _count(history[a.name].start + a.max_interval, a.max_interval, horizon)
        for a in scenario.areas
    }

    # Per (area, mower): starts sorted ascending, and the matching rows.
    pair_rows: dict[tuple[str, str], tuple[list[int], list[CompletionRow]]] = {}
    for r in rows:
        starts, rlist = pair_rows.setdefault((r.area, r.mower), ([], []))
        starts.append(r.start)
        rlist.append(r)
    for key, (starts, rlist) in pair_rows.items():
        order = sorted(range(len(starts)), key=starts.__getitem__)
        pair_rows[key] = ([starts[i] for i in order], [rlist[i] for i in order])

    def first_row_from(area: str, mower: str, earliest: int) -> CompletionRow | None:
        entry = pair_rows.get((area, mower))
        if entry is None:
            return None
        starts, rlist = entry
        i = bisect_left(starts, earliest)
        return rlist[i] if i < len(starts) else None

    areas_by_name = {a.name: a for a in scenario.areas}
    capable = {
        a.name: [m.name for m in scenario.mowers if a.name in m.can_mow] for a in scenario.areas
    }
    # Unpaused job length per pair (min elapsed over its rows) — a "longest job" proxy for
    # the tie-break, and independent of scenario.base_durations being set.
    pair_base = {
        key: min(r.completion - r.start for r in rlist) for key, (_s, rlist) in pair_rows.items()
    }
    longest = {
        name: max((pair_base[(name, m)] for m in ms if (name, m) in pair_base), default=0)
        for name, ms in capable.items()
    }

    # A mower mid-service at t = 0 is not free until it finishes (ADR-0040) — the same
    # cutoff the instance emitter censors the completion rows against.
    free_at = {m.name: 0 for m in scenario.mowers} | mower_busy_until(scenario)
    state: dict[str, dict | None] = {
        a.name: {
            "prev_start": history[a.name].start,
            "prev_completion": history[a.name].completion,
            "idx": 1,
        }
        for a in scenario.areas
    }

    def earliest_start(name: str) -> int:
        st = state[name]
        assert st is not None
        area = areas_by_name[name]
        return max(st["prev_start"] + area.min_interval, st["prev_completion"], 0)

    def deadline(name: str) -> int:
        st = state[name]
        assert st is not None
        return st["prev_start"] + areas_by_name[name].max_interval

    def schedulable(name: str) -> bool:
        st = state[name]
        if st is None or st["idx"] > cap[name]:
            return False
        return earliest_start(name) < horizon

    def free_mower_available(name: str, at: int) -> bool:
        return any(free_at[m] <= at for m in capable[name])

    def next_time(name: str) -> int:
        """The earliest ``now`` at which this area could next be served."""
        mower_free = min((free_at[m] for m in capable[name]), default=horizon)
        return max(earliest_start(name), mower_free)

    def sort_key(name: str) -> tuple[int, int, int, str]:
        return (areas_by_name[name].priority, deadline(name), -longest[name], name)

    # Monotonic forward pass. At each `now`: serve the due areas (priority tier, then EDF);
    # if none are due, serve the areas that are merely *ready* (past min_interval, mower
    # free); if neither, jump `now` forward to the next moment something can happen.
    scheduled: list[ScheduledTask] = []
    now = 0
    while now < horizon:
        active = [a.name for a in scenario.areas if schedulable(a.name)]
        if not active:
            break

        due = [n for n in active if deadline(n) <= now and free_mower_available(n, now)]
        ready = due or [
            n for n in active if earliest_start(n) <= now and free_mower_available(n, now)
        ]
        if not ready:
            ahead = [t for t in (next_time(n) for n in active) if t > now]
            if not ahead:
                break
            now = min(ahead)
            continue

        name = min(ready, key=sort_key)
        st = state[name]
        assert st is not None

        best: tuple[str, CompletionRow] | None = None
        for mower in capable[name]:
            row = first_row_from(name, mower, max(free_at[mower], earliest_start(name)))
            if row is not None and (best is None or row.completion < best[1].completion):
                best = (mower, row)
        if best is None:
            state[name] = None
            continue

        mower, row = best
        scheduled.append(
            ScheduledTask(
                area=name, task=st["idx"], mower=mower, start=row.start, end=row.completion
            )
        )
        free_at[mower] = row.completion
        st["prev_start"] = row.start
        st["prev_completion"] = row.completion
        st["idx"] += 1

    scheduled.sort(key=lambda t: (t.area, t.task))
    score = score_schedule(scenario, scheduled, completion_rows=rows)
    return Schedule(tasks=scheduled, violations=score.violations, cost=score.slots)
