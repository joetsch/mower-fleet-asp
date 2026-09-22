"""Unit tests for ripple attribution — an untouched task that still moved."""

from __future__ import annotations

from fleetplanning.explain.ripple import ripple_moves
from fleetplanning.model import PreferredTask, Schedule, ScheduledTask


def _task(area: str, start: int, mower: str) -> ScheduledTask:
    return ScheduledTask(area=area, task=1, mower=mower, start=start, end=start + 4)


def test_empty_preferences_means_nothing_to_report():
    schedule = Schedule(tasks=[], violations=[], cost=[])
    assert ripple_moves([], schedule) == []


def test_a_frozen_task_still_present_is_not_churn():
    frozen = PreferredTask(area="A", start=10, mower="M1", origin="frozen")
    schedule = Schedule(tasks=[_task("A", 10, "M1")], violations=[], cost=[])
    assert ripple_moves([frozen], schedule) == []


def test_churn_sharing_a_mower_with_an_edit_is_attributed():
    frozen = PreferredTask(area="A", start=10, mower="M1", origin="frozen")
    edit = PreferredTask(area="B", start=50, mower="M1", origin="edited")
    # The frozen task's slot is gone from the schedule -- it churned.
    schedule = Schedule(tasks=[_task("B", 50, "M1")], violations=[], cost=[])

    moves = ripple_moves([frozen, edit], schedule)
    assert len(moves) == 1
    assert moves[0].area == "A" and moves[0].start == 10
    assert moves[0].shares_mower_with == "B"
    assert moves[0].shares_area_with is None


def test_churn_sharing_only_an_area_with_an_edit_is_attributed():
    # Same area edited (different task/mower), frozen task's own slot gone.
    frozen = PreferredTask(area="A", start=10, mower="M1", origin="frozen")
    edit = PreferredTask(area="A", start=90, mower="M2", origin="edited")
    schedule = Schedule(tasks=[_task("A", 90, "M2")], violations=[], cost=[])

    moves = ripple_moves([frozen, edit], schedule)
    assert len(moves) == 1
    assert moves[0].shares_area_with == "A"
    assert moves[0].shares_mower_with is None


def test_unattributed_churn_is_left_out_rather_than_guessed_at():
    """Churn that shares neither a mower nor an area with any edit is real but outside
    this mechanism's reach (most likely a min/max-interval knock-on elsewhere)."""
    frozen = PreferredTask(area="A", start=10, mower="M1", origin="frozen")
    edit = PreferredTask(area="B", start=50, mower="M2", origin="edited")
    schedule = Schedule(tasks=[_task("B", 50, "M2")], violations=[], cost=[])
    assert ripple_moves([frozen, edit], schedule) == []


def test_added_origin_counts_as_an_edit_for_attribution():
    frozen = PreferredTask(area="A", start=10, mower="M1", origin="frozen")
    added = PreferredTask(area="C", start=70, mower="M1", origin="added")
    schedule = Schedule(tasks=[_task("C", 70, "M1")], violations=[], cost=[])

    moves = ripple_moves([frozen, added], schedule)
    assert len(moves) == 1
    assert moves[0].shares_mower_with == "C"
