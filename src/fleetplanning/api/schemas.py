"""Request/response bodies for the HTTP API.

The solve *result* body is the domain model :class:`fleetplanning.model.SolveResult` —
FastAPI turns its pydantic definition into the OpenAPI schema automatically. The request
bodies and the scenario-library wrappers (picker list + the source-derived detail the
runtime ``Scenario`` drops) live here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from fleetplanning.model import (
    ExplanationReport,
    PreferredTask,
    Scenario,
    Schedule,
    ScheduledTask,
    ServiceEvent,
    SolvePreferences,
    SolveResult,
)

# Expert-mode command-line override (ADR-0023): a generous but finite cap, just to reject
# obvious nonsense before it reaches clingo's own arg parser.
_MAX_CLINGO_ARGS = 20


class SolveRequest(BaseModel):
    scenario_id: str | None = Field(
        default=None,
        description="Slug of a curated scenario (see GET /api/scenarios). Provide this or "
        "`scenario`; `scenario` wins when both are set.",
    )
    scenario: Scenario | None = Field(
        default=None,
        description="A full (edited) problem instance, solved directly (ADR-0024). Every "
        "`model.py` rule runs during request parsing, so a malformed body is a 422 with a "
        "field path. Takes precedence over `scenario_id`.",
    )
    time_limit_s: float = Field(
        default=20.0,
        gt=0,
        le=14400,
        description="Solver wall-clock budget in seconds; on timeout the best schedule so far "
        "is returned. Capped at 4 hours purely as a sanity limit against runaway jobs.",
    )
    preferences: SolvePreferences | None = Field(
        default=None,
        description="User edits to preserve on a re-solve (ADR-0031). Omit for a cold "
        "solve; omitting it and sending `mode: off` are equivalent, and both leave the "
        "solver program exactly as it would be without this feature.",
    )
    clingo_args: list[str] | None = Field(
        default=None,
        description="Expert-mode override for the clingo command line, replacing the "
        "default -t4 --configuration=many portfolio (ADR-0007, ADR-0023). Omit for the "
        "default. Must not set constants (-c/--const) — the horizon constant is appended "
        "automatically; clingo itself rejects most other invalid flags/values, surfaced "
        "as a 400.",
    )

    @model_validator(mode="after")
    def _check_target(self) -> SolveRequest:
        if self.scenario_id is None and self.scenario is None:
            raise ValueError("provide either scenario_id or a scenario body")
        return self

    @field_validator("clingo_args")
    @classmethod
    def _check_clingo_args(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("clingo_args, if given, must be a non-empty list")
        if len(v) > _MAX_CLINGO_ARGS:
            raise ValueError(f"clingo_args: too many tokens (max {_MAX_CLINGO_ARGS})")
        for tok in v:
            if not tok.strip():
                raise ValueError("clingo_args: tokens must not be blank")
            if tok in ("-c", "--const") or tok.startswith("--const="):
                raise ValueError(
                    "clingo_args must not set constants (-c/--const) — the horizon "
                    "constant is appended automatically"
                )
        return v


class SolveJobStarted(BaseModel):
    """``POST /api/solve`` returns this immediately — the solve keeps running in the
    background (ADR-0022, anytime solving). Poll ``GET /api/solve/{job_id}`` for progress.
    """

    job_id: str


class SolveJobStatus(BaseModel):
    """Response for both the poll and cancel endpoints.

    ``result`` is ``None`` only while running with no model found yet; while running with
    a model it is the best-so-far (``solved=True, optimal=False``); once ``done`` it is
    the final result (possibly still ``solved=False`` — no feasible schedule was found).
    """

    job_id: str
    done: bool
    result: SolveResult | None = None


class ExplainRequest(BaseModel):
    """Body for ``POST /api/explain`` (Iteration 6). ``preferences`` and ``schedule`` are
    the payload and the result of the solve being explained — the same
    ``SolvePreferences`` sent to ``POST /api/solve`` and the ``SolveResult.schedule`` it
    returned. Never re-solved: a second solve under the ``-t4`` portfolio could return a
    different equally-optimal plan (ADR-0007), which would explain a schedule the user is
    not looking at.
    """

    scenario_id: str | None = Field(
        default=None,
        description="Slug of a curated scenario. Provide this or `scenario`; `scenario` "
        "wins when both are set.",
    )
    scenario: Scenario | None = Field(default=None, description="A full problem instance.")
    preferences: SolvePreferences = Field(
        description="The edits submitted to the solve being explained."
    )
    schedule: Schedule = Field(description="The schedule that solve returned.")
    budget_s: float | None = Field(
        default=None,
        gt=0,
        le=60,
        description="Overall wall-clock deadline for this explanation (expert-mode "
        "setting, mirroring `SolveRequest.time_limit_s`). Omit for the default "
        "(`explain.DEFAULT_EXPLAIN_BUDGET_S`). Capped at 60s — this is a feasibility "
        "check, not a search, and the Stage 1 pilot's worst observed batch was 7.6s.",
    )

    @model_validator(mode="after")
    def _check_target(self) -> ExplainRequest:
        if self.scenario_id is None and self.scenario is None:
            raise ValueError("provide either scenario_id or a scenario body")
        return self


class ExplainResponse(BaseModel):
    report: ExplanationReport


class ScenarioSummary(BaseModel):
    """The facts the picker shows next to a scenario name — all computed, never stored."""

    holes: int
    areas: int
    mowers: int
    load_factor: float | None = Field(
        default=None,
        description="Demand/capacity proxy, recomputed from the scenario (ADR-0024); None "
        "when size or mower-rate data is missing.",
    )


class ScenarioListEntry(BaseModel):
    id: str
    summary: ScenarioSummary


class ScenarioDerived(BaseModel):
    """Figures computed from the ``Scenario`` — never stored, recomputed on every edit
    (ADR-0024, ``fleetplanning.derived``). Replaces the old source-derived ``detail``."""

    bounds: dict[str, tuple[int, int]] = Field(
        description="Per-area (min, max) service starts for the week — the user-owned "
        "requirement when set, else the ADR-0017 derivation."
    )
    load_factor: float | None = Field(
        default=None, description="Demand/capacity proxy; None without size/rate data."
    )

    @classmethod
    def of(cls, scenario: Scenario) -> ScenarioDerived:
        from fleetplanning import derived

        return cls(
            bounds=derived.service_bounds(scenario),
            load_factor=derived.load_factor(scenario),
        )


class ScenarioBundle(BaseModel):
    scenario: Scenario
    derived: ScenarioDerived


class RollRequest(BaseModel):
    """Body for ``POST /api/scenario/advance`` — the moving-horizon step (ADR-0042).

    Advance "now" by ``hours``, given the plan ``tasks`` that were executed. There is no
    execution simulation: ``tasks`` is just the current plan. ``hours`` out of the
    ``1..horizon_hours`` range is a 400.
    """

    scenario: Scenario
    tasks: list[ScheduledTask] = Field(
        default_factory=list, description="The plan being executed over the elapsed window."
    )
    hours: int = Field(default=24, ge=1, description="How far to advance now, in whole hours.")


class RollResponse(BaseModel):
    """What ``POST /api/scenario/advance`` returns: the rolled scenario (with its derived
    figures recomputed, like every other scenario response) plus what the roll did."""

    scenario: Scenario
    derived: ScenarioDerived
    consumed: list[ServiceEvent] = Field(
        description="New history events from now-past tasks — one per area serviced in the window."
    )
    carried: list[PreferredTask] = Field(
        description="Still-future tasks rebased to the new now, as frozen re-solve preferences."
    )
    notes: list[str] = Field(
        description="Remarks about this roll (areas idle, services straddling now)."
    )


class ScenarioCreateRequest(BaseModel):
    """Body for ``POST /api/scenarios`` — save the edited scenario under a *new* slug
    (ADR-0026). ``id`` is the target slug; a clash is a 409. ``PUT /api/scenario/{id}``
    is the overwrite path and takes the bare ``Scenario`` instead."""

    id: str = Field(description="Target slug (lowercase, digits, internal hyphens).")
    scenario: Scenario


class ScenarioTemplateRequest(BaseModel):
    """Body for ``POST /api/scenario/template`` (ADR-0027). ``name`` seeds the editable
    scenario-name field only — it is not a slug and nothing is written; Save derives the
    slug from it later."""

    name: str = Field(default="new-scenario", min_length=1, max_length=100)


class CatalogMower(BaseModel):
    """One mower model from ``reference.py`` — the editor's defaults source
    (ADR-0024). ``capable_area_types`` applies the cut-height rule so the frontend never
    re-implements it. Suggestions only — the stored scenario value is always the truth."""

    name: str
    area_capacity_m2_per_day: int
    min_cut_height_mm: int
    max_cut_height_mm: int
    capable_area_types: list[str]


class CatalogAreaType(BaseModel):
    name: str
    cut_height_mm: int
    priority: int
    min_interval_h: int
    max_interval_h: int
    default_size_m2: int = Field(
        gt=0,
        description="Typical size for this type (generator's Distributions.area_geo_mean_m2) "
        "— the editor's default when adding an area (ADR-0027). A suggestion, never a "
        "constraint.",
    )


class Catalog(BaseModel):
    mower_models: list[CatalogMower]
    area_types: list[CatalogAreaType]
