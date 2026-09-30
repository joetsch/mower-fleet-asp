"""Serving the built frontend from FastAPI (packaged / local-run mode).

Day to day the demonstrator is two dev servers: Vite on :5173 proxying ``/api`` to FastAPI
on :8000 (see ``frontend/vite.config.ts``). A domain expert running the packaged zip
(``scripts/package_demo.py``, ADR-0058) has no Node and no Vite, so FastAPI itself serves
``frontend/dist`` and ``uv run fleetplanning-api`` alone is the whole app.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleetplanning.api.app import _mount_static, _resolve_static_dir


def _write_dist(dist: Path) -> None:
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>demo</title>")


def test_resolve_static_dir_prefers_the_env_var(tmp_path, monkeypatch):
    dist = tmp_path / "somewhere" / "dist"
    _write_dist(dist)
    monkeypatch.setenv("FLEETPLANNING_STATIC_DIR", str(dist))
    assert _resolve_static_dir() == dist


def test_resolve_static_dir_falls_back_to_cwd_frontend_dist(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEETPLANNING_STATIC_DIR", raising=False)
    dist = tmp_path / "frontend" / "dist"
    _write_dist(dist)
    monkeypatch.chdir(tmp_path)
    assert _resolve_static_dir() == dist


def test_resolve_static_dir_is_none_without_a_build(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEETPLANNING_STATIC_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # no frontend/dist here, no env var either
    assert _resolve_static_dir() is None


def test_mount_static_serves_the_built_frontend_without_shadowing_the_api(tmp_path):
    dist = tmp_path / "dist"
    _write_dist(dist)
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    _mount_static(app, dist)
    client = TestClient(app)

    # /api routes, registered before the mount, still win...
    assert client.get("/api/health").json() == {"status": "ok"}
    # ...and everything else falls through to the built frontend's index.html.
    r = client.get("/")
    assert r.status_code == 200
    assert "demo" in r.text
