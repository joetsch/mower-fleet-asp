import pytest
from fastapi.testclient import TestClient

import fleetplanning.solve_jobs as solve_jobs_module
from fleetplanning import __version__
from fleetplanning.api.app import app
from fleetplanning.api.schemas import SolveRequest
from fleetplanning.model import (
    DroppedPreference,
    PreferenceAgreement,
    PreferenceReport,
    PreferredTask,
    Scenario,
    Schedule,
    ScheduledTask,
    SolvePreferences,
    SolveResult,
    SolverInfo,
)
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.solve_jobs import SolveJobStatus
from fleetplanning.solver.completion_table import build_completion_table

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_scenarios_endpoint_lists_the_curated_library():
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    ids = [e["id"] for e in body]
    assert len(ids) >= 5
    assert ids == sorted(ids)  # stable order
    # every library scenario is self-describing, so the load factor is always computed
    assert all(e["summary"]["load_factor"] is not None for e in body)


def test_scenario_endpoint_returns_bundle_with_derived_figures():
    r = client.get("/api/scenario/understaffed-fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"]["name"] == "understaffed-fleet"
    d = body["derived"]
    assert d["load_factor"] > 0
    # per-area service bounds cover every area, min <= max
    assert set(d["bounds"]) == {a["name"] for a in body["scenario"]["areas"]}
    assert all(lo <= hi for lo, hi in d["bounds"].values())
    # the scenario carries its own size/model data now
    assert all(a["size_m2"] > 0 for a in body["scenario"]["areas"])


def test_scenario_endpoint_404s_on_an_unknown_id():
    assert client.get("/api/scenario/does-not-exist").status_code == 404


def test_catalog_endpoint_serves_the_reference_catalogue():
    r = client.get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    assert len(body["mower_models"]) == 10
    assert len(body["area_types"]) == 4
    fairway = next(t for t in body["area_types"] if t["name"] == "FAIRWAY")
    assert fairway["priority"] == 1
    # the cut-height rule: only the low-cut "L" models reach FAIRWAY
    for m in body["mower_models"]:
        reaches = m["min_cut_height_mm"] <= fairway["cut_height_mm"] <= m["max_cut_height_mm"]
        assert ("FAIRWAY" in m["capable_area_types"]) == reaches


def test_catalog_area_types_carry_a_default_size():
    """ADR-0027: every area type suggests a size for the "add area" editor, from the
    generator's ``Distributions.area_geo_mean_m2``. Guards the dict lookup in get_catalog."""
    body = client.get("/api/catalog").json()
    assert all(t["default_size_m2"] > 0 for t in body["area_types"])
    fairway = next(t for t in body["area_types"] if t["name"] == "FAIRWAY")
    assert fairway["default_size_m2"] == 6000


def test_scenario_derived_endpoint_recomputes_for_an_edited_scenario():
    base = client.get("/api/scenario/understaffed-fleet").json()["scenario"]
    before = client.get("/api/scenario/understaffed-fleet").json()["derived"]["load_factor"]
    base["areas"][0]["size_m2"] *= 4  # much bigger area -> heavier load
    r = client.post("/api/scenario/derived", json=base)
    assert r.status_code == 200
    assert r.json()["load_factor"] > before


def test_scenario_derived_endpoint_422s_on_a_bad_scenario():
    base = client.get("/api/scenario/well-resourced").json()["scenario"]
    base["areas"][0]["priority"] = 9
    assert client.post("/api/scenario/derived", json=base).status_code == 422


def test_advance_endpoint_rolls_now_and_recomputes_derived():
    bundle = client.get("/api/scenario/well-resourced").json()
    scenario = bundle["scenario"]
    tasks = [
        {
            "area": a["name"],
            "task": 1,
            "mower": scenario["mowers"][0]["name"],
            "start": 5,
            "end": 11,
        }
        for a in scenario["areas"]
        if a["name"] in scenario["mowers"][0]["can_mow"]
    ]

    r = client.post(
        "/api/scenario/advance", json={"scenario": scenario, "tasks": tasks, "hours": 24}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"]["horizon_start_hour"] == (scenario["horizon_start_hour"] + 24) % 168
    assert "derived" in body and "bounds" in body["derived"]
    # every "start=5" task is now in the past -> a consumed history event
    assert {e["area"] for e in body["consumed"]} == {t["area"] for t in tasks}
    assert body["carried"] == []


def test_advance_endpoint_400s_on_hours_past_the_horizon():
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    r = client.post(
        "/api/scenario/advance", json={"scenario": scenario, "tasks": [], "hours": 9999}
    )
    assert r.status_code == 400


def test_solve_request_validation():
    # neither scenario_id nor scenario body
    assert client.post("/api/solve", json={"time_limit_s": 5}).status_code == 422
    # bad time limit
    assert (
        client.post(
            "/api/solve", json={"scenario_id": "well-resourced", "time_limit_s": 0}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/solve", json={"scenario_id": "well-resourced", "time_limit_s": 99999}
        ).status_code
        == 422
    )
    # unknown scenario
    assert (
        client.post("/api/solve", json={"scenario_id": "nope", "time_limit_s": 5}).status_code
        == 404
    )


def test_solve_endpoint_starts_a_job_and_poll_returns_it(monkeypatch):
    """Happy path without the real solver: stub ``solve_jobs.start``/``.poll`` and check
    the routes wire the request through and return bodies matching the schemas — the
    solve is anytime/cancellable (ADR-0022), so ``POST /api/solve`` only returns a job id.
    """
    canned = SolveResult(
        scenario_name="toy-course",
        horizon_hours=168,
        solved=True,
        optimal=True,
        solve_time_s=0.01,
        solver=SolverInfo(
            threads=4, config="many", args=["-t4", "--configuration=many"], time_limit_s=5.0
        ),
        schedule=Schedule(
            tasks=[ScheduledTask(area="Hole1_Fairway", task=1, mower="Mower A", start=0, end=6)],
            violations=[],
            cost=[0, 0, 0, 0, 0],
        ),
    )
    started: list[tuple[str, float]] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        started.append((scenario.name, time_limit_s))
        return "job-1"

    def fake_poll(job_id):
        assert job_id == "job-1"
        return SolveJobStatus(job_id=job_id, done=True, result=canned)

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)
    monkeypatch.setattr(solve_jobs_module, "poll", fake_poll)

    r = client.post("/api/solve", json={"scenario_id": "well-resourced", "time_limit_s": 5.0})
    assert r.status_code == 200
    assert started == [("well-resourced", 5.0)]
    job_id = r.json()["job_id"]
    assert job_id == "job-1"

    r = client.get(f"/api/solve/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["done"] is True
    assert body["result"]["solved"] is True and body["result"]["optimal"] is True
    assert body["result"]["schedule"]["tasks"][0]["area"] == "Hole1_Fairway"
    SolveResult.model_validate(body["result"])


def test_solve_endpoint_accepts_an_edited_scenario_body(monkeypatch):
    """ADR-0024: a full `scenario` body is solved directly, and wins over `scenario_id`."""
    seen: list[Scenario] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        seen.append(scenario)
        return "job-e"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)

    edited = client.get("/api/scenario/well-resourced").json()["scenario"]
    edited["areas"][0]["priority"] = 3 if edited["areas"][0]["priority"] != 3 else 1
    want_priority = edited["areas"][0]["priority"]
    r = client.post(
        "/api/solve",
        json={"scenario_id": "does-not-exist", "scenario": edited, "time_limit_s": 5.0},
    )
    assert r.status_code == 200  # no 404 — the body wins, the bogus id is ignored
    assert seen[-1].areas[0].priority == want_priority


def test_solve_endpoint_422s_on_an_invalid_scenario_body():
    edited = client.get("/api/scenario/well-resourced").json()["scenario"]
    edited["areas"][0]["min_interval"] = edited["areas"][0]["max_interval"] + 100
    r = client.post("/api/solve", json={"scenario": edited})
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"][:4] == ["body", "scenario", "areas", 0]
    assert "min_interval" in err["msg"]


def test_solve_endpoint_uses_the_default_budget_when_omitted(monkeypatch):
    seen: list[float] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        seen.append(time_limit_s)
        return "job-2"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)
    assert client.post("/api/solve", json={"scenario_id": "well-resourced"}).status_code == 200
    assert seen == [20.0]  # SolveRequest default


# --- clingo_args: the expert-mode command-line override (ADR-0023) ----------------------


def test_solve_endpoint_passes_clingo_args_through_to_solve_jobs(monkeypatch):
    seen: list[list[str] | None] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        seen.append(clingo_args)
        return "job-4"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)
    r = client.post(
        "/api/solve",
        json={"scenario_id": "well-resourced", "clingo_args": ["-t2", "--stats"]},
    )
    assert r.status_code == 200
    assert seen == [["-t2", "--stats"]]

    # Omitted entirely -> None reaches solve_jobs.start, same as before this feature.
    assert client.post("/api/solve", json={"scenario_id": "well-resourced"}).status_code == 200
    assert seen[-1] is None


@pytest.mark.parametrize(
    "clingo_args",
    [
        [],  # empty list is rejected — None ("omit") is the way to ask for the default
        ["-t4", ""],  # blank token
        ["-t4", "   "],  # whitespace-only token
        ["-c", "horizon=5"],  # would collide with the auto-appended horizon constant
        ["--const=horizon=5"],
        [f"-t{n}" for n in range(21)],  # over the token cap
    ],
)
def test_solve_request_rejects_invalid_clingo_args(clingo_args):
    r = client.post(
        "/api/solve", json={"scenario_id": "well-resourced", "clingo_args": clingo_args}
    )
    assert r.status_code == 422


def test_solve_endpoint_maps_a_bad_clingo_flag_to_400():
    """No monkeypatching here — a genuinely bad flag hits the real ``solve_jobs.start`` /
    ``clingo.Control`` and must come back as a client error (400), not an unhandled 500.
    clingo validates its args at ``Control()`` construction, before any grounding, so this
    is fast even without stubbing the solver.
    """
    r = client.post(
        "/api/solve",
        json={"scenario_id": "well-resourced", "clingo_args": ["--definitely-not-a-flag"]},
    )
    assert r.status_code == 400
    assert "invalid clingo arguments" in r.json()["detail"]


def test_solve_status_endpoint_404s_on_an_unknown_job():
    assert client.get("/api/solve/does-not-exist").status_code == 404


# --- scenario persistence (ADR-0026) — all against an isolated copy of the library ---


def test_put_scenario_overwrites_and_returns_the_fresh_bundle(isolated_library):
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    scenario["horizon_start_hour"] = 9
    r = client.put("/api/scenario/well-resourced", json=scenario)
    assert r.status_code == 200
    assert r.json()["scenario"]["horizon_start_hour"] == 9
    # persisted: a fresh GET sees it
    assert client.get("/api/scenario/well-resourced").json()["scenario"]["horizon_start_hour"] == 9


def test_put_scenario_creates_a_new_slug_and_forces_the_name(isolated_library):
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    scenario["name"] = "whatever"
    r = client.put("/api/scenario/my-saved-copy", json=scenario)
    assert r.status_code == 200
    assert r.json()["scenario"]["name"] == "my-saved-copy"
    assert "my-saved-copy" in [e["id"] for e in client.get("/api/scenarios").json()]


def test_put_scenario_400s_on_a_bad_slug(isolated_library):
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    assert client.put("/api/scenario/Bad Slug", json=scenario).status_code == 400


def test_post_scenarios_creates_only_and_409s_on_a_clash(isolated_library):
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    r = client.post("/api/scenarios", json={"id": "brand-new", "scenario": scenario})
    assert r.status_code == 201
    assert r.json()["scenario"]["name"] == "brand-new"
    clash = client.post("/api/scenarios", json={"id": "well-resourced", "scenario": scenario})
    assert clash.status_code == 409


def test_delete_scenario_removes_it(isolated_library):
    assert client.delete("/api/scenario/well-resourced").status_code == 204
    assert "well-resourced" not in [e["id"] for e in client.get("/api/scenarios").json()]
    assert client.get("/api/scenario/well-resourced").status_code == 404


def test_delete_scenario_404s_on_an_unknown_id(isolated_library):
    assert client.delete("/api/scenario/does-not-exist").status_code == 404


def test_saved_scenario_can_then_be_solved_by_id(isolated_library, monkeypatch):
    started: list[tuple[str, float]] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        started.append((scenario.name, time_limit_s))
        return "job-saved"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)
    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    client.post("/api/scenarios", json={"id": "solve-me", "scenario": scenario})
    r = client.post("/api/solve", json={"scenario_id": "solve-me", "time_limit_s": 5.0})
    assert r.status_code == 200
    assert started == [("solve-me", 5.0)]


# --- from-scratch template (ADR-0027) ---


def test_scenario_template_endpoint_returns_an_unsaved_bundle():
    r = client.post("/api/scenario/template", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"]["name"] == "new-scenario"
    assert len(body["scenario"]["areas"]) == 1
    assert set(body["derived"]["bounds"]) == {body["scenario"]["areas"][0]["name"]}
    # nothing was written
    assert "new-scenario" not in [e["id"] for e in client.get("/api/scenarios").json()]

    named = client.post("/api/scenario/template", json={"name": "Pinehurst No. 2"})
    assert named.json()["scenario"]["name"] == "Pinehurst No. 2"


def test_template_can_be_saved_and_then_solved(isolated_library, monkeypatch):
    started: list[str] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        started.append(scenario.name)
        return "job-scratch"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)
    scenario = client.post("/api/scenario/template", json={}).json()["scenario"]
    created = client.post("/api/scenarios", json={"id": "from-scratch", "scenario": scenario})
    assert created.status_code == 201
    r = client.post("/api/solve", json={"scenario_id": "from-scratch", "time_limit_s": 5.0})
    assert r.status_code == 200
    assert started == ["from-scratch"]


def test_derived_endpoint_handles_an_empty_scenario():
    """An area-less scenario is valid; load_factor is inf -> serialised as JSON null,
    which the frontend's finite-check relies on (ADR-0027)."""
    empty = {"name": "blank", "areas": [], "mowers": [], "history": []}
    r = client.post("/api/scenario/derived", json=empty)
    assert r.status_code == 200
    assert r.json() == {"bounds": {}, "load_factor": None}


def test_solve_cancel_endpoint_stops_the_job(monkeypatch):
    """The stop button: cancel returns the final (possibly best-so-far, possibly
    unsolved) result directly — no further polling needed."""
    canned = SolveResult(scenario_name="x", horizon_hours=168, solved=False, solve_time_s=0.0)

    def fake_cancel(job_id):
        assert job_id == "job-3"
        return SolveJobStatus(job_id=job_id, done=True, result=canned)

    monkeypatch.setattr(solve_jobs_module, "cancel", fake_cancel)
    r = client.post("/api/solve/job-3/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["done"] is True
    assert body["result"]["solved"] is False


def test_solve_cancel_endpoint_404s_on_an_unknown_job():
    assert client.post("/api/solve/does-not-exist/cancel").status_code == 404


def test_a_per_weekday_schedule_round_trips_and_solves(isolated_library, monkeypatch):
    """ADR-0029: the editor can now write a non-uniform weekly schedule, so the save →
    reload → solve path has to carry one intact.

    The model has always allowed it (``Area.schedule`` is keyed by weekday and
    ``completion_table._week_masks`` resolves each day against its own intervals — see
    ``test_completion_table.py::test_week_masks_resolve_per_weekday_independently``).
    What is new is that a user can reach it, so it is worth a guard end to end.
    """
    seen: list[Scenario] = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        seen.append(scenario)
        return "job-w"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)

    scenario = client.get("/api/scenario/well-resourced").json()["scenario"]
    # A no-go the area only has at the weekend, and one weekday left deliberately free.
    scenario["areas"][0]["schedule"]["Saturday"]["no_go"] = [[22, 6]]
    scenario["areas"][0]["schedule"]["Sunday"]["no_go"] = [[22, 6]]

    assert client.put("/api/scenario/well-resourced", json=scenario).status_code == 200

    reloaded = client.get("/api/scenario/well-resourced").json()["scenario"]
    assert reloaded["areas"][0]["schedule"]["Saturday"]["no_go"] == [[22, 6]]
    assert reloaded["areas"][0]["schedule"]["Sunday"]["no_go"] == [[22, 6]]
    assert reloaded["areas"][0]["schedule"]["Wednesday"]["no_go"] == []

    assert client.post("/api/solve", json={"scenario": reloaded}).status_code == 200
    solved = seen[-1].areas[0].schedule
    assert solved["Saturday"].no_go == [(22, 6)]
    assert solved["Wednesday"].no_go == []


# --- the schedule-edit preference payload (ADR-0031) ------------------------------------
#
# Contract only at this point: the request carries the payload and it parses. Nothing in
# the solver reads it yet — that is branch A.


def test_solve_request_carries_a_preference_payload():
    req = SolveRequest.model_validate(
        {
            "scenario_id": "well-resourced",
            "preferences": {
                "mode": "weak",
                "level": "tiebreak",
                "tasks": [
                    {"area": "H1_FW", "start": 49, "mower": "Ceora 546", "origin": "frozen"},
                    {"area": "H2_SR", "start": 7},
                ],
            },
        }
    )
    assert req.preferences is not None
    assert req.preferences.mode == "weak"
    assert req.preferences.level == "tiebreak"
    assert [t.area for t in req.preferences.tasks] == ["H1_FW", "H2_SR"]
    # A preference with no mower is a *time-only* preference: keep the hour, any mower.
    assert req.preferences.tasks[1].mower is None
    assert req.preferences.tasks[1].origin == "edited"  # the default


def test_solve_request_without_preferences_leaves_them_none():
    """The ADR-0031 invariant, at the API edge: omitting the field is not the same as
    sending an empty one — `None` is what tells the solver path to change nothing at all.
    """
    req = SolveRequest.model_validate({"scenario_id": "well-resourced"})
    assert req.preferences is None


@pytest.mark.parametrize(
    "preferences",
    [
        {"tasks": [{"area": "H1_FW", "start": -1, "mower": "m"}]},  # start before t = 0
        {"tasks": [{"start": 3, "mower": "m"}]},  # no area
        {"tasks": [], "mode": "hard"},  # not one of the three mechanisms
        {"tasks": [], "level": "middle"},  # not one of the two levels
        {"tasks": [{"area": "H1_FW", "start": 3, "origin": "invented"}]},
    ],
)
def test_solve_request_rejects_a_malformed_preference_payload(preferences):
    r = client.post(
        "/api/solve", json={"scenario_id": "well-resourced", "preferences": preferences}
    )
    assert r.status_code == 422


def test_solve_endpoint_passes_preferences_through_to_solve_jobs(monkeypatch):
    """The payload has to reach the job machine, not just parse (ADR-0031)."""
    seen = []

    def fake_start(scenario, *, time_limit_s, clingo_args=None, preferences=None):
        seen.append(preferences)
        return "job-p"

    monkeypatch.setattr(solve_jobs_module, "start", fake_start)

    r = client.post(
        "/api/solve",
        json={
            "scenario_id": "well-resourced",
            "preferences": {
                "mode": "weak",
                "level": "tiebreak",
                "tasks": [{"area": "A", "start": 12, "mower": "M", "origin": "frozen"}],
            },
        },
    )
    assert r.status_code == 200
    assert seen[-1] is not None
    assert seen[-1].mode == "weak"
    assert seen[-1].level == "tiebreak"
    assert [t.area for t in seen[-1].tasks] == ["A"]

    # Omitted entirely -> None reaches solve_jobs.start, same as before this feature.
    assert client.post("/api/solve", json={"scenario_id": "well-resourced"}).status_code == 200
    assert seen[-1] is None


def test_solve_status_reports_what_became_of_the_preferences(monkeypatch):
    """`preference_report` rides back out on the poll, so the anytime UI can show
    "6 of 9 edits kept" improving alongside the Gantt."""
    report = PreferenceReport(
        agreement=PreferenceAgreement(total=2, time_kept=1, mower_total=2, mower_kept=1),
        dropped=[
            DroppedPreference(
                area="A", start=3, mower="M", half="mower", reason="'M' cannot service A"
            )
        ],
    )
    monkeypatch.setattr(
        solve_jobs_module,
        "poll",
        lambda job_id: SolveJobStatus(
            job_id=job_id,
            done=True,
            result=SolveResult(
                scenario_name="x",
                horizon_hours=168,
                solved=True,
                status="optimal",
                solve_time_s=1.0,
                preferences=report,
            ),
        ),
    )

    body = client.get("/api/solve/job-p").json()
    assert body["result"]["preferences"]["agreement"]["time_kept"] == 1
    assert body["result"]["preferences"]["agreement"]["mower_total"] == 2
    assert body["result"]["preferences"]["dropped"][0]["half"] == "mower"


# --------------------------------------------------------------------------- /api/explain
# Iteration 6. `explain_scenario` never re-solves, so these build a hand-consistent
# (scenario, preferences, schedule) trio directly from the completion table rather than
# running a real solve — fast, and exercises the endpoint's routing/validation, not the
# solver (that is `tests/test_explain_*.py`'s job).


def _conflicting_edit_fixture() -> tuple[Scenario, SolvePreferences, Schedule]:
    """One kept task and one dropped edit that statically overlaps it on the same mower
    — a `blocked_statically` case, so it needs no solver call at all."""
    scenario = toy_course()
    rows = build_completion_table(scenario)
    for kept_row in rows:
        for edit_row in rows:
            if (
                edit_row.mower == kept_row.mower
                and edit_row.area != kept_row.area
                and edit_row.start < kept_row.completion
                and kept_row.start < edit_row.completion
            ):
                schedule = Schedule(
                    tasks=[
                        ScheduledTask(
                            area=kept_row.area,
                            task=1,
                            mower=kept_row.mower,
                            start=kept_row.start,
                            end=kept_row.completion,
                        )
                    ],
                    violations=[],
                    cost=[],
                )
                preferences = SolvePreferences(
                    mode="weak",
                    level="top",
                    tasks=[
                        PreferredTask(
                            area=kept_row.area,
                            start=kept_row.start,
                            mower=kept_row.mower,
                            origin="frozen",
                        ),
                        PreferredTask(
                            area=edit_row.area,
                            start=edit_row.start,
                            mower=edit_row.mower,
                            origin="edited",
                        ),
                    ],
                )
                return scenario, preferences, schedule
    raise AssertionError("no overlapping (mower-sharing) row pair found in toy_course")


def test_explain_endpoint_validation():
    assert client.post("/api/explain", json={}).status_code == 422  # neither target
    scenario, preferences, schedule = _conflicting_edit_fixture()
    assert (
        client.post(
            "/api/explain",
            json={
                "scenario_id": "does-not-exist",
                "preferences": preferences.model_dump(mode="json"),
                "schedule": schedule.model_dump(mode="json"),
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/explain",
            json={
                "scenario": scenario.model_dump(mode="json"),
                "preferences": preferences.model_dump(mode="json"),
                "schedule": schedule.model_dump(mode="json"),
                "budget_s": 0,
            },
        ).status_code
        == 422
    )


def test_explain_endpoint_finds_the_static_conflict():
    scenario, preferences, schedule = _conflicting_edit_fixture()
    resp = client.post(
        "/api/explain",
        json={
            "scenario": scenario.model_dump(mode="json"),
            "preferences": preferences.model_dump(mode="json"),
            "schedule": schedule.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["budget_exhausted"] is False
    assert len(report["edits"]) == 1
    edit = report["edits"][0]
    assert edit["outcome"] == "blocked_statically"
    assert edit["conflicts"][0]["area"] == preferences.tasks[0].area
    assert edit["solve_time_s"] is None  # tier 1 makes no solver call


def test_explain_endpoint_reports_nothing_when_every_edit_was_kept():
    scenario, preferences, schedule = _conflicting_edit_fixture()
    # Drop the conflicting edit, keep only the frozen task -- everything submitted is
    # already in the schedule, so there is nothing to explain.
    preferences = SolvePreferences(mode="weak", level="top", tasks=preferences.tasks[:1])
    resp = client.post(
        "/api/explain",
        json={
            "scenario": scenario.model_dump(mode="json"),
            "preferences": preferences.model_dump(mode="json"),
            "schedule": schedule.model_dump(mode="json"),
        },
    )
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["edits"] == []
    assert report["reinstated"] == []
    assert report["ripple"] == []
