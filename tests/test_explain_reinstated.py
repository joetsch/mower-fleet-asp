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


def test_it_says_whether_the_minimum_actually_forced_the_extra_service():
    """The row must carry *why*, because the UI states a cause.

    ``Hole1_Fairway``'s derived minimum is 7 (ADR-0017). Submitting 8 and getting 9 back
    is not the minimum's doing — the extra service is the optimiser's own choice, driven
    by the max-interval objective. The sentence on screen said "needs at least 7 …, so the
    solver added 1 back" either way, asserting a cause nobody checked.
    """
    scenario = toy_course()
    submitted = [
        PreferredTask(area="Hole1_Fairway", start=s, mower="Mower A", origin="frozen")
        for s in (10, 30, 50, 70, 90, 110, 130, 150)
    ]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", s) for s in (10, 30, 50, 70, 90, 110, 130, 150, 160)],
        violations=[], cost=[],
    )

    (row,) = reinstated_services(scenario, submitted, schedule)
    assert row.submitted_count == 8
    assert row.actual_count == 9
    assert row.min_services == 7
    assert row.forced_by_minimum is False, "8 submitted already meets the minimum of 7"


def test_the_minimum_is_reported_as_the_cause_when_it_really_is():
    scenario = toy_course()
    submitted = [
        PreferredTask(area="Hole1_Fairway", start=s, mower="Mower A", origin="frozen")
        for s in (10, 30)
    ]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", s) for s in (10, 30, 50)], violations=[], cost=[]
    )

    (row,) = reinstated_services(scenario, submitted, schedule)
    assert row.submitted_count == 2
    assert row.min_services == 7
    assert row.forced_by_minimum is True


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


# Owner report, pre-workshop review 2026-09-24: releasing a service (dropping it out of
# the payload, ADR-0035 decision 3 -- "out of the payload", never "never do this") always
# read as "you asked for N" in the reinstated sentence, when releasing is the opposite of
# asking. The fix: the baseline this check compares against is the plan *before* the
# re-solve -- what was submitted, plus what was released -- not the payload alone.


def test_a_released_service_the_minimum_brings_back_is_not_reported():
    """The user released exactly the services that brought the count under the minimum.
    That is what release *means* (it may come back) -- not a mismatch to explain."""
    scenario = toy_course()
    # Hole1_Fairway's derived minimum is 7. Submit 5 (having released 2 of the 7 that were
    # on screen); the solver brings the count back to 7, exactly matching submitted+released.
    submitted = [
        PreferredTask(area="Hole1_Fairway", start=s, mower="Mower A", origin="frozen")
        for s in (10, 30, 50, 70, 90)
    ]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", s) for s in (10, 30, 50, 70, 90, 110, 130)],
        violations=[],
        cost=[],
    )

    result = reinstated_services(
        scenario, submitted, schedule, released={"Hole1_Fairway": 2}
    )
    assert result == []


def test_a_service_beyond_what_was_released_is_still_reported_against_the_wider_baseline():
    """The solver added one more than even submitted+released explains -- the released
    count does not swallow every extra service, only the ones it actually accounts for."""
    scenario = toy_course()
    submitted = [
        PreferredTask(area="Hole1_Fairway", start=s, mower="Mower A", origin="frozen")
        for s in (10, 30, 50, 70, 90)
    ]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", s) for s in (10, 30, 50, 70, 90, 110, 130, 150)],
        violations=[],
        cost=[],
    )

    (row,) = reinstated_services(scenario, submitted, schedule, released={"Hole1_Fairway": 2})
    assert row.submitted_count == 7  # 5 submitted + 2 released -- the plan before the re-solve
    assert row.actual_count == 8
    assert row.forced_by_minimum is False  # 7 already meets the minimum of 7


def test_released_count_also_feeds_the_forced_by_minimum_check():
    """Without folding the release back in, this would read as "the minimum forced it" --
    it did not; the plan before the re-solve (submitted + released) already met it. One
    extra service beyond that baseline is still reported, just not blamed on the minimum."""
    scenario = toy_course()
    submitted = [
        PreferredTask(area="Hole1_Fairway", start=s, mower="Mower A", origin="frozen")
        for s in (10, 30, 50)
    ]
    schedule = Schedule(
        tasks=[_task("Hole1_Fairway", s) for s in (10, 30, 50, 70, 90, 110, 130, 150)],
        violations=[],
        cost=[],
    )

    (row,) = reinstated_services(scenario, submitted, schedule, released={"Hole1_Fairway": 4})
    assert row.submitted_count == 7  # 3 submitted + 4 released == the minimum, not below it
    assert row.actual_count == 8
    assert row.forced_by_minimum is False
