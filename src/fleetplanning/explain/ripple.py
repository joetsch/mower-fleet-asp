""""I moved one thing and others shifted" — attributing untouched churn to an edit.

A *frozen* preference (a task the user did not touch) that still does not appear in the
resulting schedule churned for some reason. This module does not try to guess where it
ended up — task identity does not survive a re-solve any more than an edit's does
(ADR-0031) — it only answers the cheaper, honest question: does this churned task share a
mower or an area with one of the user's own edits? If so, that is a plausible, reportable
cause. If not, the churn is real but outside this mechanism's reach (most likely a
min-interval/max-interval knock-on elsewhere in the same area's own schedule) and is left
unattributed rather than guessed at.

No solver call; pure comparison of what was submitted against what came back.
"""

from __future__ import annotations

from collections.abc import Sequence

from fleetplanning.model import PreferredTask, RippleMove, Schedule


def ripple_moves(preferences: Sequence[PreferredTask], schedule: Schedule) -> list[RippleMove]:
    if not preferences:
        return []
    kept_starts = {(t.area, t.start) for t in schedule.tasks}
    edited = [p for p in preferences if p.origin in ("edited", "added")]
    edited_areas = {p.area for p in edited}
    # One representative edited area per mower, for the attribution text — several edits
    # could share a mower; naming one is enough to make the sentence concrete.
    area_by_mower: dict[str, str] = {}
    for p in edited:
        if p.mower is not None:
            area_by_mower.setdefault(p.mower, p.area)

    out: list[RippleMove] = []
    for p in preferences:
        if p.origin != "frozen" or (p.area, p.start) in kept_starts:
            continue
        shares_mower_with = area_by_mower.get(p.mower) if p.mower else None
        shares_area_with = p.area if p.area in edited_areas else None
        if shares_mower_with is None and shares_area_with is None:
            continue
        out.append(
            RippleMove(
                area=p.area,
                start=p.start,
                mower=p.mower,
                shares_mower_with=shares_mower_with,
                shares_area_with=shares_area_with,
            )
        )
    return out
