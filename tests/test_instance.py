from fleetplanning.model import DAYS, Area, DaySchedule, Mower, Scenario, ServiceEvent
from fleetplanning.solver.completion_table import CompletionRow
from fleetplanning.solver.instance import render_instance


def test_instance_matches_golden(toy_scenario, check_golden):
    """The emitted facts for the toy scenario are byte-stable.

    If this fails after an intentional change, regenerate the golden with:
        uv run pytest tests/test_instance.py --update-golden
    """
    check_golden(render_instance(toy_scenario), "toy_course_instance.lp")


def test_instance_has_the_expected_predicates(toy_scenario):
    text = render_instance(toy_scenario)
    for predicate in (
        "area(",
        "priority(",
        "min_interval(",
        "max_interval(",
        "mower(",
        "capable(",
        "last(",
        "min_starts(",
        "max_starts(",
        "completion(",
    ):
        assert predicate in text, f"missing {predicate}"


def _one_area_scenario(history_completion: int) -> Scenario:
    area = Area(
        name="A",
        type="semirough_A",
        hole=1,
        priority=2,
        min_interval=10,
        max_interval=20,
        schedule={d: DaySchedule() for d in DAYS},
    )
    return Scenario(
        name="tiny",
        areas=[area],
        mowers=[Mower(name="M", can_mow=["A"])],
        history=[ServiceEvent(area="A", mower="M", start=-5, completion=history_completion)],
        horizon_hours=12,
        horizon_start_hour=0,
    )


def test_completion_rows_before_an_in_progress_service_are_dropped():
    """A service still running at t=0 (completion > 0) censors earlier start options."""
    rows = [CompletionRow(area="A", mower="M", start=s, completion=s + 3, avoid_hours=0)
            for s in range(12)]

    finished = render_instance(_one_area_scenario(history_completion=-1), completion_rows=rows)
    in_progress = render_instance(_one_area_scenario(history_completion=6), completion_rows=rows)

    assert finished.count("completion(") == 12
    # starts 0..5 fall before the in-progress service finishes at hour 6 -> dropped
    assert in_progress.count("completion(") == 6
    assert "completion(\"A\",\"M\",5," not in in_progress
    assert "completion(\"A\",\"M\",6," in in_progress


def _two_area_one_mower_scenario(a1_completion: int) -> Scenario:
    """One mower serves both areas; its last service on A1 finishes at ``a1_completion``."""
    areas = [
        Area(
            name=name,
            type="semirough_A",
            hole=1,
            priority=2,
            min_interval=10,
            max_interval=20,
            schedule={d: DaySchedule() for d in DAYS},
        )
        for name in ("A1", "A2")
    ]
    return Scenario(
        name="tiny",
        areas=areas,
        mowers=[Mower(name="M", can_mow=["A1", "A2"])],
        history=[
            ServiceEvent(area="A1", mower="M", start=-2, completion=a1_completion),
            ServiceEvent(area="A2", mower="M", start=-30, completion=-25),
        ],
        horizon_hours=12,
        horizon_start_hour=0,
    )


def test_a_mower_busy_on_one_area_cannot_start_another_before_it_is_free():
    """A mower still finishing A1 at t=+5 is not offered a start on A2 before hour 5
    (docs/known-hazards.md: the encoding does not model this on its own)."""
    rows = [
        CompletionRow(area=area, mower="M", start=s, completion=s + 3, avoid_hours=0)
        for area in ("A1", "A2")
        for s in range(12)
    ]

    free = render_instance(_two_area_one_mower_scenario(a1_completion=-1), completion_rows=rows)
    busy = render_instance(_two_area_one_mower_scenario(a1_completion=5), completion_rows=rows)

    assert free.count('completion("A2","M",') == 12
    # M is busy on A1 until hour 5, so starts 0..4 on A2 are censored too.
    assert 'completion("A2","M",4,' not in busy
    assert 'completion("A2","M",5,' in busy
    assert busy.count('completion("A2","M",') == 7
