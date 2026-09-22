"""Unit tests for tier 1 — the free, solver-free conflict check."""

from __future__ import annotations

from fleetplanning.explain.static import static_conflict
from fleetplanning.model import ConflictingTask, PreferredTask
from fleetplanning.solver.completion_table import build_completion_table


def _rows(toy_scenario):
    return build_completion_table(toy_scenario)


def _overlaps(a, b) -> bool:
    return a.start < b.completion and b.start < a.completion


def _pref(row, *, origin: str) -> PreferredTask:
    return PreferredTask(area=row.area, start=row.start, mower=row.mower, origin=origin)


def _pair_sharing_mower(rows):
    """Two rows, same mower, different area, overlapping in time."""
    for a in rows:
        for b in rows:
            if a.mower == b.mower and a.area != b.area and _overlaps(a, b):
                return a, b
    raise AssertionError("no overlapping same-mower pair found in the toy scenario")


def test_no_conflict_when_kept_is_empty(toy_scenario):
    rows = _rows(toy_scenario)
    edit = _pref(rows[0], origin="edited")
    assert static_conflict(edit, [], rows) is None


def test_time_only_edit_always_falls_through_to_tier_2(toy_scenario):
    """No mower named -> nothing concrete to check against a specific kept interval."""
    rows = _rows(toy_scenario)
    edit = PreferredTask(area=rows[0].area, start=rows[0].start, mower=None, origin="edited")
    kept = [_pref(rows[0], origin="frozen")]
    assert static_conflict(edit, kept, rows) is None


def test_same_mower_overlap_is_caught(toy_scenario):
    rows = _rows(toy_scenario)
    kept_row, edit_row = _pair_sharing_mower(rows)
    edit = _pref(edit_row, origin="edited")
    kept = [_pref(kept_row, origin="frozen")]

    result = static_conflict(edit, kept, rows)
    assert result is not None
    assert "already serving" in result.detail
    assert result.conflicts == [
        ConflictingTask(area=kept_row.area, start=kept_row.start, mower=kept_row.mower)
    ]


def test_same_area_overlap_is_caught_even_on_a_different_mower(toy_scenario):
    """Same area, *different* mower — precedence is mower-blind, unlike the no-overlap
    constraint, so this must not be mistaken for the same-mower case above."""
    rows = _rows(toy_scenario)
    kept_row, edit_row = next(
        (a, b)
        for a in rows
        for b in rows
        if a.area == b.area and a.mower != b.mower and _overlaps(a, b)
    )

    edit = _pref(edit_row, origin="edited")
    kept = [_pref(kept_row, origin="frozen")]

    result = static_conflict(edit, kept, rows)
    assert result is not None
    assert "overlaps this area's own kept task" in result.detail


def test_non_overlapping_kept_task_on_the_same_mower_is_not_a_conflict(toy_scenario):
    rows = _rows(toy_scenario)
    a = rows[0]
    b = next(r for r in rows if r.mower == a.mower and r.area != a.area and not _overlaps(r, a))

    edit = _pref(b, origin="edited")
    kept = [_pref(a, origin="frozen")]
    assert static_conflict(edit, kept, rows) is None
