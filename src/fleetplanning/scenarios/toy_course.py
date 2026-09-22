"""The notebook's fixed synthetic example, ported verbatim.

Source: ``notebooks/area-schedule-demonstrator.ipynb`` cells 2 and 18. Three holes,
nine areas, four mowers, plus a service history. This is the Phase 1 reference
scenario (ADR-0004) — its solved schedule is what we check the new pipeline against.
"""

from __future__ import annotations

from fleetplanning.model import (
    DAYS,
    Area,
    DaySchedule,
    Interval,
    Mower,
    Scenario,
    ServiceEvent,
)

# Service policy per area type: (priority, min_interval, max_interval)
# semirough_C shares priority 3 with semirough_B (ADR-0012 caps priority at 3); the two
# still differ by max_interval (72 vs 48). The notebook used priority 4 for semirough_C,
# which put its max-interval violations on the same weak-constraint level as avoid-zone
# violations — see docs/design-choices.md B4.
_POLICY: dict[str, tuple[int, int, int]] = {
    "fairway": (1, 18, 24),
    "semirough_A": (2, 39, 48),
    "semirough_B": (3, 39, 48),
    "semirough_C": (3, 39, 72),
}


def _weekly(no_go: list[Interval], avoid: list[Interval]) -> dict[str, DaySchedule]:
    """Same availability every weekday (as in the notebook's ``make_schedule``)."""
    return {day: DaySchedule(no_go=list(no_go), avoid=list(avoid)) for day in DAYS}


def _area(name: str, area_type: str, hole: int, no_go, avoid) -> Area:
    priority, min_interval, max_interval = _POLICY[area_type]
    return Area(
        name=name,
        type=area_type,
        hole=hole,
        priority=priority,
        min_interval=min_interval,
        max_interval=max_interval,
        schedule=_weekly(no_go, avoid),
    )


_AREAS: list[Area] = [
    # Hole 1
    _area("Hole1_Fairway", "fairway", 1, [(22, 6)], [(13, 20)]),
    _area("Hole1_SemiroughA", "semirough_A", 1, [(23, 5)], []),
    _area("Hole1_SemiroughB", "semirough_B", 1, [(22, 6)], []),
    # Hole 2
    _area("Hole2_Fairway", "fairway", 2, [(22, 6)], [(13, 20)]),
    _area("Hole2_SemiroughA", "semirough_A", 2, [(23, 5)], []),
    _area("Hole2_SemiroughC", "semirough_C", 2, [(22, 6)], []),
    # Hole 3
    _area("Hole3_Fairway", "fairway", 3, [(22, 6)], [(13, 20)]),
    _area("Hole3_SemiroughB", "semirough_B", 3, [(23, 5)], []),
    _area("Hole3_SemiroughC", "semirough_C", 3, [(22, 6)], []),
]

_MOWERS: list[Mower] = [
    Mower(
        name="Mower A",
        can_mow=["Hole1_Fairway", "Hole1_SemiroughA", "Hole2_Fairway", "Hole2_SemiroughA"],
    ),
    Mower(name="Mower B", can_mow=["Hole1_SemiroughB"]),
    Mower(
        name="Mower C",
        can_mow=["Hole2_Fairway", "Hole2_SemiroughC", "Hole3_Fairway", "Hole3_SemiroughC"],
    ),
    Mower(name="Mower D", can_mow=["Hole3_SemiroughB"]),
]

_HISTORY: list[ServiceEvent] = [
    ServiceEvent(area="Hole1_Fairway", mower="Mower A", start=-10, completion=-6),
    ServiceEvent(area="Hole1_SemiroughA", mower="Mower A", start=-6, completion=2),
    ServiceEvent(area="Hole1_SemiroughB", mower="Mower B", start=-20, completion=-14),
    ServiceEvent(area="Hole2_Fairway", mower="Mower A", start=-30, completion=-22),
    ServiceEvent(area="Hole2_SemiroughA", mower="Mower A", start=-12, completion=-6),
    ServiceEvent(area="Hole2_SemiroughC", mower="Mower C", start=-3, completion=5),
    ServiceEvent(area="Hole3_Fairway", mower="Mower C", start=-15, completion=-7),
    ServiceEvent(area="Hole3_SemiroughB", mower="Mower D", start=-8, completion=-2),
    ServiceEvent(area="Hole3_SemiroughC", mower="Mower C", start=-25, completion=-18),
]


def toy_course() -> Scenario:
    """Return a fresh copy of the toy scenario."""
    return Scenario(
        name="toy-course",
        areas=[a.model_copy(deep=True) for a in _AREAS],
        mowers=[m.model_copy(deep=True) for m in _MOWERS],
        history=[h.model_copy(deep=True) for h in _HISTORY],
        horizon_hours=168,
        horizon_start_hour=13,  # notebook: current time 12:45 -> next full hour, Monday 13:00
        duration_seed=1,  # instance that proves optimal in a few seconds (ADR-0007)
    )
