"""A minimal, immediately-solvable :class:`Scenario` for "New scenario…" (ADR-0027).

Never written to disk — the API hands it to the editor as a starting *draft*; it reaches
``curated/`` only when the user presses Save. Built here rather than in the frontend so
"a valid scenario from nothing" has one definition, beside ``model.py`` and validated by
it. The frontend's structural edit helpers still construct entries client-side, but that
is *editing an already-valid scenario* — a far smaller surface than constructing one.
"""

from __future__ import annotations

from fleetplanning.duration import base_duration
from fleetplanning.model import (
    DAYS,
    Area,
    BaseDuration,
    DaySchedule,
    Mower,
    Scenario,
    history_event,
)
from fleetplanning.reference import AREA_TYPES, DEFAULTS, MOWER_MODELS_BY_NAME

FIRST_AREA = "Area 1"
FIRST_MOWER = "Mower 1"
# The smallest-capacity model that reaches a FAIRWAY's 14 mm cut height (min_cut 10 mm).
_TEMPLATE_MODEL = "AM_580L EPOS"


def free_week() -> dict[str, DaySchedule]:
    """Seven identical, entirely-free weekdays — the least-constraining schedule."""
    return {day: DaySchedule() for day in DAYS}


def minimal_scenario(name: str) -> Scenario:
    """One fairway, one capable mower, one history event: valid and solvable on the first
    click. Everything the editor then adds is an edit to this."""
    area_type = AREA_TYPES[0]  # FAIRWAY
    size_m2 = round(DEFAULTS.area_geo_mean_m2[area_type.name])
    model = MOWER_MODELS_BY_NAME[_TEMPLATE_MODEL]
    hours = base_duration(size_m2, "OPEN", model.area_capacity_m2_per_day)
    return Scenario(
        name=name,
        areas=[
            Area(
                name=FIRST_AREA,
                type=area_type.name,
                hole=1,
                priority=area_type.priority,
                min_interval=area_type.min_interval_h,
                max_interval=area_type.max_interval_h,
                schedule=free_week(),
                size_m2=size_m2,
            )
        ],
        mowers=[
            Mower(
                name=FIRST_MOWER,
                can_mow=[FIRST_AREA],
                model=model.name,
                area_capacity_m2_per_day=model.area_capacity_m2_per_day,
            )
        ],
        # "just serviced at t = 0" — the RNG-free counterpart of
        # generator/params.py::_synthesise_history, which draws the completion offset.
        history=[history_event(FIRST_AREA, FIRST_MOWER, hours)],
        # duration_seed is irrelevant while base_durations is set (model.py).
        base_durations=[BaseDuration(area=FIRST_AREA, mower=FIRST_MOWER, hours=hours)],
    )
