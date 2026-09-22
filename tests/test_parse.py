"""Unit tests for ``parse_schedule`` — the pure transform from clingo's shown atoms to a
:class:`Schedule`.

No solver run needed: ``clingo.parse_term`` builds the exact ``Symbol`` objects the real
solver would hand us, so we can feed ``parse_schedule`` hand-written atom sets.
"""

from __future__ import annotations

import clingo

from fleetplanning.solver.parse import parse_schedule


def _parse(*atom_strings: str, cost: list[int] | None = None):
    atoms = [clingo.parse_term(s) for s in atom_strings]
    return parse_schedule(atoms, cost or [])


def test_schedule_atom_maps_to_a_scheduled_task():
    schedule = _parse('schedule("Hole1_Fairway",2,"Mower A",30,38)')
    assert len(schedule.tasks) == 1
    task = schedule.tasks[0]
    assert (task.area, task.task, task.mower, task.start, task.end) == (
        "Hole1_Fairway",
        2,
        "Mower A",
        30,
        38,
    )
    assert isinstance(task.task, int) and isinstance(task.start, int)


def test_each_violation_predicate_maps_to_its_kind():
    schedule = _parse(
        'max_interval_violation("A",1)',
        'min_interval_violation("B",2)',
        'avoid_zone_violation("C",3)',
    )
    assert {(v.kind, v.area, v.task) for v in schedule.violations} == {
        ("max_interval", "A", 1),
        ("min_interval", "B", 2),
        ("avoid_zone", "C", 3),
    }


def test_area_level_boundary_violations_fold_into_min_max_interval():
    """The forward encoding (ADR-0015) emits arity-1 area-level atoms. ``parse_schedule``
    maps them onto the existing kinds and pins them to the area's first / last task."""
    schedule = _parse(
        'schedule("A",1,"M",0,4)',
        'schedule("A",2,"M",20,24)',
        'schedule("A",3,"M",40,44)',
        'first_task_too_late("A")',
        'first_task_too_early("A")',
        'last_task_too_early("A")',
    )
    assert {(v.kind, v.area, v.task) for v in schedule.violations} == {
        ("max_interval", "A", 1),  # first_task_too_late  -> first task
        ("min_interval", "A", 1),  # first_task_too_early -> first task
        ("max_interval", "A", 3),  # last_task_too_early  -> last task
    }


def test_area_level_violation_for_area_with_no_scheduled_task_is_dropped():
    schedule = _parse('schedule("A",1,"M",0,4)', 'first_task_too_late("B")')
    assert schedule.violations == []


def test_unrecognised_and_wrong_arity_atoms_are_ignored():
    """Documents current behaviour: anything that is not a known atom shape is dropped
    silently (see docs/known-hazards.md — no schema check yet)."""
    schedule = _parse(
        'schedule("A",1,"M",0,4)',
        'schedule("A",1,"M",0)',  # wrong arity
        'some_other_atom("A",1)',
        "42",  # not a function symbol
    )
    assert len(schedule.tasks) == 1
    assert schedule.violations == []


def test_tasks_and_violations_are_sorted_deterministically():
    schedule = _parse(
        'schedule("B",1,"M",0,4)',
        'schedule("A",2,"M",5,9)',
        'schedule("A",1,"M",0,4)',
        'min_interval_violation("B",1)',
        'avoid_zone_violation("A",1)',
    )
    assert [(t.area, t.task) for t in schedule.tasks] == [("A", 1), ("A", 2), ("B", 1)]
    assert [(v.kind, v.area) for v in schedule.violations] == [
        ("avoid_zone", "A"),
        ("min_interval", "B"),
    ]


def test_cost_vector_is_passed_through_verbatim():
    schedule = _parse('schedule("A",1,"M",0,4)', cost=[3, 0, 1, 0, 2])
    assert schedule.cost == [3, 0, 1, 0, 2]


def test_empty_atom_set_yields_an_empty_schedule():
    schedule = parse_schedule([], [])
    assert schedule.tasks == []
    assert schedule.violations == []
    assert schedule.cost == []
