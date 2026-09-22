"""`ScenarioSource` — the rich, self-contained recipe the generator writes to disk.

A ``source.json`` fully describes one sub-problem: the (subset of) mower model
specs, area-type policy and availability archetype it uses, the areas and mowers with
their **materialised** capability, the synthesised history, and the provenance needed to
regenerate it. ``compile_source`` (``generator/compile.py``) turns it into the runtime
``fleetplanning.model.Scenario`` the solver consumes.

See ``docs/scenario-generator-spec.md`` §5.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from fleetplanning.model import DAYS, ServiceEvent
from fleetplanning.reference import AreaTypeSpec, AvailabilityArchetype, MowerModelSpec

HOUR_STATES = frozenset({"AP", "ANP", "NA"})
COMPLEXITY_VALUES = frozenset({"OPEN", "MODERATE", "SEVERE"})


class SourceArea(BaseModel):
    name: str
    hole: int = Field(ge=1)
    type: str
    size_m2: int = Field(gt=0)
    complexity: str
    group: str

    @model_validator(mode="after")
    def _check(self) -> SourceArea:
        if self.complexity not in COMPLEXITY_VALUES:
            raise ValueError(
                f"{self.name}: complexity {self.complexity!r} not in {COMPLEXITY_VALUES}"
            )
        return self


class SourceMower(BaseModel):
    name: str
    model: str
    group: str
    can_mow: list[str] = Field(
        description="Materialised capability — area names (§6.2), so source.json is complete."
    )


class ScenarioSource(BaseModel):
    """A complete, auditable recipe for one sub-problem scenario."""

    name: str
    course_ref: str | None = None  # e.g. "synthetic"

    # Embedded catalogs (the subset actually referenced).
    area_types: list[AreaTypeSpec]
    mower_models: list[MowerModelSpec]
    availability: list[AvailabilityArchetype]
    archetype: str  # the one drawn for this sub-problem

    areas: list[SourceArea]
    mowers: list[SourceMower]
    history: list[ServiceEvent]  # one per area

    horizon_hours: int = Field(default=168, gt=0)
    horizon_start_hour: int = Field(default=13, ge=0, le=167)

    # Provenance.
    generator_version: str
    seed_spec: dict
    drawn: dict = Field(
        default_factory=dict,
        description="The few non-materialised choices: k/hole, achieved load, capability density.",
    )

    # Difficulty bookkeeping (predicted, not solved).
    load_factor: float
    capability_density: float
    structurally_unsat: bool = False

    @model_validator(mode="after")
    def _check(self) -> ScenarioSource:
        type_names = {t.name for t in self.area_types}
        model_names = {m.name for m in self.mower_models}
        archetype_names = {a.name for a in self.availability}
        area_names = [a.name for a in self.areas]
        mower_names = [m.name for m in self.mowers]
        groups = {a.group for a in self.areas} | {m.group for m in self.mowers}

        if len(set(area_names)) != len(area_names):
            raise ValueError("area names must be unique")
        if len(set(mower_names)) != len(mower_names):
            raise ValueError("mower names must be unique")
        if self.archetype not in archetype_names:
            raise ValueError(f"archetype {self.archetype!r} not among embedded availability")

        for area in self.areas:
            if area.type not in type_names:
                raise ValueError(f"area {area.name!r} references unknown type {area.type!r}")
            row = _archetype_row(self, area.type)
            if area.type != "FAIRWAY" and "ANP" in row:
                # keeps the 5-P weak-constraint levels clear of the avoid level (ADR-0012)
                raise ValueError(
                    f"non-FAIRWAY type {area.type!r} has ANP hours in {self.archetype!r}"
                )

        for mower in self.mowers:
            if mower.model not in model_names:
                raise ValueError(f"mower {mower.name!r} references unknown model {mower.model!r}")
            unknown = sorted(set(mower.can_mow) - set(area_names))
            if unknown:
                raise ValueError(
                    f"mower {mower.name!r} can_mow references unknown areas: {unknown}"
                )

        for g in groups:
            if not any(m.group == g for m in self.mowers):
                raise ValueError(f"group {g!r} has areas but no mower")

        # history: exactly one per area, capable mower, ordered
        capable = {(a, m.name) for m in self.mowers for a in m.can_mow}
        seen: set[str] = set()
        for ev in self.history:
            if ev.area not in set(area_names):
                raise ValueError(f"history references unknown area {ev.area!r}")
            if ev.mower not in set(mower_names):
                raise ValueError(f"history references unknown mower {ev.mower!r}")
            if (ev.area, ev.mower) not in capable:
                raise ValueError(f"history: mower {ev.mower!r} cannot service area {ev.area!r}")
            if ev.area in seen:
                raise ValueError(f"history has more than one event for area {ev.area!r}")
            seen.add(ev.area)
        missing = sorted(set(area_names) - seen)
        if missing:
            raise ValueError(f"history missing for areas: {missing}")

        return self


def _archetype_row(source: ScenarioSource, area_type: str) -> list[str]:
    arch = next(a for a in source.availability if a.name == source.archetype)
    return arch.grid[area_type]


def archetype_row(source: ScenarioSource, area_type: str) -> list[str]:
    """The 24-hour AP/ANP/NA row for ``area_type`` under this source's archetype."""
    return _archetype_row(source, area_type)


# Re-export so callers can `from ...source import DAYS` alongside the models.
__all__ = [
    "DAYS",
    "AreaTypeSpec",
    "AvailabilityArchetype",
    "MowerModelSpec",
    "ScenarioSource",
    "SourceArea",
    "SourceMower",
    "archetype_row",
]
