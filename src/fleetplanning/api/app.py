"""FastAPI service wrapping the solver core.

- ``GET  /api/health``                  -> liveness check
- ``GET  /api/scenarios``                -> the curated scenario library (picker list)
- ``GET  /api/scenario/{id}``            -> one scenario + its source-derived detail
- ``POST /api/scenario/advance``         -> roll "now" forward, fold history (ADR-0042)
- ``POST /api/solve``                    -> start solving a scenario by id, return a job id
- ``GET  /api/solve/{job_id}``           -> poll: best-so-far or final result
- ``POST /api/solve/{job_id}/cancel``    -> stop button: end the search, keep the best model

Solving is anytime and cancellable (ADR-0022): ``POST /api/solve`` returns as soon as the
search starts, not when it finishes. There is at most one live job — see
``fleetplanning.solve_jobs`` — so starting a new solve implicitly cancels the previous
one. The handlers are plain ``def`` so FastAPI runs them in a worker thread; grounding
(synchronous, fast at this demonstrator's scale) happens on that thread inside
``solve_jobs.start``, the search itself runs on clingo's own thread.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from fleetplanning import __version__, derived, solve_jobs
from fleetplanning.api.schemas import (
    Catalog,
    CatalogAreaType,
    CatalogMower,
    ExplainRequest,
    ExplainResponse,
    RollRequest,
    RollResponse,
    ScenarioBundle,
    ScenarioCreateRequest,
    ScenarioDerived,
    ScenarioListEntry,
    ScenarioSummary,
    ScenarioTemplateRequest,
    SolveJobStarted,
    SolveJobStatus,
    SolveRequest,
)
from fleetplanning.explain import explain_scenario
from fleetplanning.model import Scenario
from fleetplanning.reference import AREA_TYPES, DEFAULTS, MOWER_MODELS
from fleetplanning.rolling import advance
from fleetplanning.scenarios import registry, template

app = FastAPI(title="Fleet-Planning Demonstrator API", version=__version__)

# The Vite dev server runs on 5173 and proxies /api here, but allow direct calls too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _summary(scenario: Scenario) -> ScenarioSummary:
    lf = derived.load_factor(scenario)
    return ScenarioSummary(
        holes=len({a.hole for a in scenario.areas}),
        areas=len(scenario.areas),
        mowers=len(scenario.mowers),
        load_factor=None if lf is None else round(lf, 2),
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/scenarios", response_model=list[ScenarioListEntry])
def list_scenarios() -> list[ScenarioListEntry]:
    entries = []
    for slug in registry.scenario_ids():
        scenario, _ = registry.load(slug)
        entries.append(ScenarioListEntry(id=slug, summary=_summary(scenario)))
    return entries


@app.get("/api/scenario/{scenario_id}", response_model=ScenarioBundle)
def get_scenario(scenario_id: str) -> ScenarioBundle:
    try:
        scenario, _ = registry.load(scenario_id)
    except registry.ScenarioNotFound:
        raise HTTPException(status_code=404, detail=f"unknown scenario {scenario_id!r}") from None
    return ScenarioBundle(scenario=scenario, derived=ScenarioDerived.of(scenario))


@app.post("/api/scenario/derived", response_model=ScenarioDerived)
def post_scenario_derived(scenario: Scenario) -> ScenarioDerived:
    """Recompute the derived figures for an edited scenario (ADR-0024). The editor calls
    this as the user types; a malformed body is a 422 from request parsing."""
    return ScenarioDerived.of(scenario)


@app.post("/api/scenario/advance", response_model=RollResponse)
def post_scenario_advance(request: RollRequest) -> RollResponse:
    """Advance "now" for the moving horizon (ADR-0042): slide the window forward, fold the
    now-past plan into history, hand back the still-future plan as frozen preferences. The
    caller then re-solves the returned scenario carrying ``carried``. ``hours`` outside
    ``1..horizon_hours`` is a 400."""
    try:
        result = advance(request.scenario, request.tasks, request.hours)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RollResponse(
        scenario=result.scenario,
        derived=ScenarioDerived.of(result.scenario),
        consumed=result.consumed,
        carried=result.carried,
        notes=result.notes,
    )


@app.post("/api/scenario/template", response_model=ScenarioBundle)
def post_scenario_template(request: ScenarioTemplateRequest) -> ScenarioBundle:
    """A minimal, immediately-solvable scenario for "New scenario…" (ADR-0027).

    Nothing is written: this is a *draft* the editor opens. It reaches disk only via
    ``PUT /api/scenario/{id}`` or ``POST /api/scenarios``. 200, not 201 — no resource
    is created here.
    """
    scenario = template.minimal_scenario(request.name)
    return ScenarioBundle(scenario=scenario, derived=ScenarioDerived.of(scenario))


@app.put("/api/scenario/{scenario_id}", response_model=ScenarioBundle)
def put_scenario(scenario_id: str, scenario: Scenario) -> ScenarioBundle:
    """Save the edited scenario over the slug ``scenario_id`` — create or overwrite
    (ADR-0026). ``scenario.name`` is forced to the slug. A malformed slug is a 400; a
    malformed scenario is a 422 from request parsing."""
    try:
        registry.save(scenario_id, scenario, overwrite=True)
    except registry.InvalidSlug:
        raise HTTPException(
            status_code=400, detail=f"invalid scenario id {scenario_id!r}"
        ) from None
    saved, _ = registry.load(scenario_id)
    return ScenarioBundle(scenario=saved, derived=ScenarioDerived.of(saved))


@app.post("/api/scenarios", response_model=ScenarioBundle, status_code=201)
def create_scenario(request: ScenarioCreateRequest) -> ScenarioBundle:
    """Save the edited scenario under a *new* slug (the "Save as…" path, ADR-0026). A
    slug that is already taken is a 409 — use ``PUT`` to overwrite."""
    try:
        registry.save(request.id, request.scenario, overwrite=False)
    except registry.InvalidSlug:
        raise HTTPException(status_code=400, detail=f"invalid scenario id {request.id!r}") from None
    except registry.ScenarioExists:
        raise HTTPException(
            status_code=409, detail=f"scenario {request.id!r} already exists"
        ) from None
    saved, _ = registry.load(request.id)
    return ScenarioBundle(scenario=saved, derived=ScenarioDerived.of(saved))


@app.delete("/api/scenario/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str) -> None:
    """Remove a scenario from the library (ADR-0026). 404 if the slug is unknown."""
    try:
        registry.delete(scenario_id)
    except registry.InvalidSlug:
        raise HTTPException(
            status_code=400, detail=f"invalid scenario id {scenario_id!r}"
        ) from None
    except registry.ScenarioNotFound:
        raise HTTPException(status_code=404, detail=f"unknown scenario {scenario_id!r}") from None


@app.get("/api/catalog", response_model=Catalog)
def get_catalog() -> Catalog:
    """The mower model + area-type catalogue (``reference.py``), read-only,
    the editor's defaults source (ADR-0024)."""
    return Catalog(
        mower_models=[
            CatalogMower(
                name=m.name,
                area_capacity_m2_per_day=m.area_capacity_m2_per_day,
                min_cut_height_mm=m.min_cut_height_mm,
                max_cut_height_mm=m.max_cut_height_mm,
                capable_area_types=[
                    t.name
                    for t in AREA_TYPES
                    if m.min_cut_height_mm <= t.cut_height_mm <= m.max_cut_height_mm
                ],
            )
            for m in MOWER_MODELS
        ],
        area_types=[
            CatalogAreaType(
                name=t.name,
                cut_height_mm=t.cut_height_mm,
                priority=t.priority,
                min_interval_h=t.min_interval_h,
                max_interval_h=t.max_interval_h,
                default_size_m2=round(DEFAULTS.area_geo_mean_m2[t.name]),
            )
            for t in AREA_TYPES
        ],
    )


@app.post("/api/solve", response_model=SolveJobStarted)
def post_solve(request: SolveRequest) -> SolveJobStarted:
    if request.scenario is not None:
        scenario = request.scenario
    else:
        try:
            scenario, _ = registry.load(request.scenario_id)
        except registry.ScenarioNotFound:
            raise HTTPException(
                status_code=404, detail=f"unknown scenario {request.scenario_id!r}"
            ) from None
    try:
        job_id = solve_jobs.start(
            scenario,
            time_limit_s=request.time_limit_s,
            clingo_args=request.clingo_args,
            preferences=request.preferences,
        )
    except RuntimeError as e:
        # clingo rejects the expert-mode command-line override (ADR-0023) — a bad flag or
        # value, not a server error. schemas.py already blocks -c/--const; this is the
        # backstop for everything else clingo itself validates (unknown options, an
        # out-of-range -t, ...).
        raise HTTPException(status_code=400, detail=f"invalid clingo arguments: {e}") from None
    return SolveJobStarted(job_id=job_id)


@app.get("/api/solve/{job_id}", response_model=SolveJobStatus)
def get_solve(job_id: str) -> SolveJobStatus:
    try:
        return solve_jobs.poll(job_id)
    except solve_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail=f"unknown solve job {job_id!r}") from None


@app.post("/api/solve/{job_id}/cancel", response_model=SolveJobStatus)
def post_cancel_solve(job_id: str) -> SolveJobStatus:
    try:
        return solve_jobs.cancel(job_id)
    except solve_jobs.JobNotFound:
        raise HTTPException(status_code=404, detail=f"unknown solve job {job_id!r}") from None


@app.post("/api/explain", response_model=ExplainResponse)
def post_explain(request: ExplainRequest) -> ExplainResponse:
    """Explain why the edits in `request.preferences` that are missing from
    `request.schedule` were not kept (Iteration 6).

    A plain synchronous call, not a job like `/api/solve`: the explain budget (a few
    seconds, capped at 60s by `schemas.ExplainRequest`) bounds it directly, so there is
    nothing to poll or cancel. FastAPI runs this `def` handler in a worker thread, the
    same as every other handler here — it does not block the server's event loop, only
    this one request.
    """
    if request.scenario is not None:
        scenario = request.scenario
    else:
        try:
            scenario, _ = registry.load(request.scenario_id)
        except registry.ScenarioNotFound:
            raise HTTPException(
                status_code=404, detail=f"unknown scenario {request.scenario_id!r}"
            ) from None
    kwargs = {} if request.budget_s is None else {"budget_s": request.budget_s}
    report = explain_scenario(
        scenario, request.preferences, request.schedule, released=request.released, **kwargs
    )
    return ExplainResponse(report=report)


def run() -> None:
    """Entry point for ``uv run fleetplanning-api``."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
