"""Reference catalog for the demonstrator: mower models, area-type service policy, and
the few defaults the scenario editor suggests.

``api/app.py`` serves it as ``GET /api/catalog``; ``scenarios/template.py`` and
``duration.py`` read from it. Pydantic-only, no ``numpy``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# --- Catalog-row shapes ---------------------------------------------------------------


class MowerModelSpec(BaseModel):
    """One mower model. Only what capability + duration need."""

    name: str
    area_capacity_m2_per_day: int = Field(gt=0)
    min_cut_height_mm: int = Field(gt=0)
    max_cut_height_mm: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> MowerModelSpec:
        if self.min_cut_height_mm > self.max_cut_height_mm:
            raise ValueError(f"{self.name}: min cut height > max")
        return self


class AreaTypeSpec(BaseModel):
    """Service policy for one area type."""

    name: str
    cut_height_mm: int = Field(gt=0)
    priority: int = Field(ge=1, le=3)
    min_interval_h: int = Field(gt=0)
    max_interval_h: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> AreaTypeSpec:
        if self.min_interval_h > self.max_interval_h:
            raise ValueError(f"{self.name}: min_interval > max_interval")
        return self


class AvailabilityArchetype(BaseModel):
    """One availability pattern: a 24-hour AP/ANP/NA row per area type (AP = free,
    ANP = avoid, NA = no-go). Only the shape ships here — ``generator/source.py``
    validates against it; the per-area windows live in each scenario."""

    name: str
    grid: dict[str, list[str]]

    @model_validator(mode="after")
    def _check(self) -> AvailabilityArchetype:
        for area_type, row in self.grid.items():
            if len(row) != 24:
                raise ValueError(f"{self.name}/{area_type}: row must be 24 hours, got {len(row)}")
            bad = set(row) - {"AP", "ANP", "NA"}
            if bad:
                raise ValueError(f"{self.name}/{area_type}: unknown hour states {sorted(bad)}")
        return self


# --- Mower models (public product specifications: capacity / min / max cut height) ------

MOWER_MODELS: tuple[MowerModelSpec, ...] = (
    MowerModelSpec(
        name="AM_520 EPOS",
        area_capacity_m2_per_day=2000,
        min_cut_height_mm=20,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="AM_540 EPOS",
        area_capacity_m2_per_day=4000,
        min_cut_height_mm=20,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="AM_560 EPOS",
        area_capacity_m2_per_day=6000,
        min_cut_height_mm=20,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="AM_580 EPOS",
        area_capacity_m2_per_day=8000,
        min_cut_height_mm=20,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="AM_580L EPOS",
        area_capacity_m2_per_day=8000,
        min_cut_height_mm=10,
        max_cut_height_mm=50,
    ),
    MowerModelSpec(
        name="AM_535 EPOS",
        area_capacity_m2_per_day=3000,
        min_cut_height_mm=30,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="Ceora_544 EPOS",
        area_capacity_m2_per_day=15000,
        min_cut_height_mm=20,
        max_cut_height_mm=70,
    ),
    MowerModelSpec(
        name="Ceora_544L EPOS",
        area_capacity_m2_per_day=13000,
        min_cut_height_mm=10,
        max_cut_height_mm=60,
    ),
    MowerModelSpec(
        name="Ceora_546 EPOS",
        area_capacity_m2_per_day=25000,
        min_cut_height_mm=20,
        max_cut_height_mm=70,
    ),
    MowerModelSpec(
        name="Ceora_546L EPOS",
        area_capacity_m2_per_day=22000,
        min_cut_height_mm=10,
        max_cut_height_mm=60,
    ),
)
MOWER_MODELS_BY_NAME: dict[str, MowerModelSpec] = {m.name: m for m in MOWER_MODELS}

# --- Area-type service policy -----------------------------------------------------------
# Priority 1 is the most important of three tiers; SEMIROUGH_C shares tier 3 with
# SEMIROUGH_B and differs by max_interval.

AREA_TYPES: tuple[AreaTypeSpec, ...] = (
    AreaTypeSpec(
        name="FAIRWAY", cut_height_mm=14, priority=1, min_interval_h=18, max_interval_h=24
    ),
    AreaTypeSpec(
        name="SEMIROUGH_A", cut_height_mm=45, priority=2, min_interval_h=39, max_interval_h=48
    ),
    AreaTypeSpec(
        name="SEMIROUGH_B", cut_height_mm=45, priority=3, min_interval_h=39, max_interval_h=48
    ),
    AreaTypeSpec(
        name="SEMIROUGH_C", cut_height_mm=45, priority=3, min_interval_h=39, max_interval_h=72
    ),
)
AREA_TYPES_BY_NAME: dict[str, AreaTypeSpec] = {t.name: t for t in AREA_TYPES}
SEMIROUGH_TYPES: tuple[str, ...] = ("SEMIROUGH_A", "SEMIROUGH_B", "SEMIROUGH_C")


# --- Editor defaults -------------------------------------------------------------------


class Distributions(BaseModel):
    """Defaults the scenario editor suggests."""

    # The size (m²) suggested for a newly added area of each type.
    area_geo_mean_m2: dict[str, float] = Field(
        default_factory=lambda: {
            "FAIRWAY": 6000.0,
            "SEMIROUGH_A": 5500.0,
            "SEMIROUGH_B": 3350.0,
            "SEMIROUGH_C": 6000.0,
        }
    )


# Duration complexity multiplier: an area's path-complexity tier scales its base mowing
# time (``duration.base_duration``). A single global factor per tier.
COMPLEXITY_MULT: dict[str, float] = {"OPEN": 1.0, "MODERATE": 1.12, "SEVERE": 1.25}
COMPLEXITIES: tuple[str, ...] = ("OPEN", "MODERATE", "SEVERE")

DEFAULTS = Distributions()
