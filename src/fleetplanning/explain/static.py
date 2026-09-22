"""Tier 1 — a free, solver-free reason for a dropped edit.

Checks a dropped edit's ``[start, end)`` interval against every *kept* preference's own
interval, on the same mower (the forward model's cross-task no-overlap constraint,
``forward_model.lp:26-35``) or the same area (its precedence/history constraint). Both are
two of the encoding's only four hard constraints, reproduced here in Python over the
*preference set* rather than the solved schedule — a kept preference and a dropped one
can conflict even though the schedule that resulted never had to say so explicitly.

Sound as far as it goes, not complete: it only sees conflicts one hop away. A conflict
mediated through a third task, an area's ``min_starts``/``max_starts`` count, or the
history boundary needs the counterfactual (tier 2). In the Stage 1 pilot
(`docs/study/explain_pilot_v1/README.md`) this tier alone resolved 44% of dropped edits,
for free, and its share was highest on exactly the lightly-loaded scenarios where a
synchronous UI response matters most.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from fleetplanning.model import ConflictingTask, PreferredTask
from fleetplanning.solver.completion_table import CompletionRow


@dataclass(frozen=True)
class StaticConflict:
    detail: str
    conflicts: list[ConflictingTask] = field(default_factory=list)


def _row(
    rows: Sequence[CompletionRow], area: str, mower: str, start: int
) -> CompletionRow | None:
    return next(
        (r for r in rows if r.area == area and r.mower == mower and r.start == start), None
    )


def static_conflict(
    edit: PreferredTask,
    kept: Sequence[PreferredTask],
    rows: Sequence[CompletionRow],
) -> StaticConflict | None:
    """A free reason the edit was dropped, or ``None`` if tier 2 is needed.

    ``edit`` must name a mower — a time-only preference (``mower=None``) leaves the mower
    to the solver's choice, so there is nothing concrete to check yet against a specific
    kept interval; it always falls through to tier 2.
    """
    if edit.mower is None:
        return None
    edit_row = _row(rows, edit.area, edit.mower, edit.start)
    if edit_row is None:
        return StaticConflict(detail="no legal completion row for this (area, mower, start)")
    edit_end = edit_row.completion

    for other in kept:
        if other.mower is None:
            continue
        same_mower = other.mower == edit.mower
        same_area = other.area == edit.area
        if not (same_mower or same_area):
            continue
        other_row = _row(rows, other.area, other.mower, other.start)
        if other_row is None:
            continue
        overlaps = edit.start < other_row.completion and other.start < edit_end
        if not overlaps:
            continue
        conflict = ConflictingTask(area=other.area, start=other.start, mower=other.mower)
        if same_mower:
            return StaticConflict(
                detail=f"mower {edit.mower!r} is already serving {other.area!r} at that hour",
                conflicts=[conflict],
            )
        return StaticConflict(
            detail=f"overlaps this area's own kept task at hour {other.start}",
            conflicts=[conflict],
        )
    return None
