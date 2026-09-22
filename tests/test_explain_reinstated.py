"""Unit tests for the reinstated-service check — a released task the area's own
service-count minimum brought back. Invisible to the kept/dropped count (ADR-0035
decision 3: release means "out of the payload", not "never do this"), so this needs no
dropped edit to trigger — just a count mismatch."""

from __future__ import annotations

from fleetplanning.explain.reinstated import reinstated_services
from fleetplanning.model import PreferredTask, Schedule, ScheduledTask
from fleetplanning.scenarios.toy_course import toy_course


def _task(area: str, start: int) -> ScheduledTask:
    return ScheduledTask(area=area, task=1, mower="Mower A", start=start, end=start + 4)


def test_empty_preferences_means_nothing_to_compare():
    scenario = toy_course()
    schedule = Schedule(tasks=[_task("Hole1_Fairway", 10)], violations=[], cost=[])
    assert reinstated_services(scenario, [], schedule) == []


def test_actual_count_matching_submitted_is_not_reported():
    scenario = toy_course()
    submitted = [PreferredTask(area="Hole1_Fairway", start=10, mower="Mower A", origin="frozen")]
    schedule = Schedule(tasks=[_task("Hole1_Fairway", 10)], violations=[], cost=[])
    assert reinstated_services(scenario, submitted, schedule) == []


def test_an_extra_task_beyond_what_was_submitted_is_reported():
    """The user released this area down to one task; the schedule still carries two --
    the second is the reinstated one, with no need to know which specific release did it.
    """
    scenario = toy_course()
    submitted = [PreferredTask(area="Hole1_Fairway", start=10, mower="Mower A", origin="frozen")]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", 10), _task("Hole1_Fairway", 50)],
        violations=[],
        cost=[],
    )

    result = reinstated_services(scenario, submitted, schedule)
    assert len(result) == 1
    assert result[0].area == "Hole1_Fairway"
    assert result[0].submitted_count == 1
    assert result[0].actual_count == 2
    assert result[0].min_services >= 1  # the ADR-0017 derivation for this toy area


def test_an_area_with_no_submitted_preferences_at_all_can_still_be_reported():
    """The whole area was released -- zero submitted preferences for it -- and the
    solver still had to service it at least once."""
    scenario = toy_course()
    submitted = [PreferredTask(area="Hole2_Fairway", start=10, mower="Mower A", origin="frozen")]
    schedule = Schedule(
        tasks=[_task("Hole2_Fairway", 10), _task("Hole1_Fairway", 30)],
        violations=[],
        cost=[],
    )

    result = reinstated_services(scenario, submitted, schedule)
    assert len(result) == 1
    assert result[0].area == "Hole1_Fairway"
    assert result[0].submitted_count == 0
    assert result[0].actual_count == 1
