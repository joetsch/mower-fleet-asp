"""Shared test fixtures and helpers.

pytest auto-discovers this file — anything defined here is available to every test in
``tests/`` without an import. Two things live here:

* the ``toy_scenario`` fixture (a fresh copy of the reference scenario per test);
* golden-file plumbing: the ``--update-golden`` flag and a ``check_golden`` helper that
  rewrites the golden when that flag is passed and otherwise asserts with a short diff.
"""

from __future__ import annotations

import difflib
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from fleetplanning.model import Scenario
from fleetplanning.scenarios import registry
from fleetplanning.scenarios.toy_course import toy_course

GOLDEN_DIR = Path(__file__).parent / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite golden files instead of asserting against them.",
    )


@pytest.fixture
def toy_scenario() -> Scenario:
    """A fresh, independently-mutable copy of the reference scenario."""
    return toy_course()


@pytest.fixture
def isolated_library(tmp_path: Path) -> Iterator[Path]:
    """Point the scenario registry at a throwaway copy of ``curated/`` (ADR-0026).

    Save/delete tests mutate the library on disk; this keeps them off the real curated
    set. Restores the packaged directory and clears the parse cache on the way out.
    """
    lib = tmp_path / "curated"
    shutil.copytree(registry.curated_dir(), lib)
    registry._dir_override = lib
    registry._invalidate()
    try:
        yield lib
    finally:
        registry._dir_override = None
        registry._invalidate()


@pytest.fixture
def check_golden(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    """Return ``check(actual_text, golden_filename)``.

    With ``--update-golden`` it writes ``actual_text`` to ``tests/golden/<filename>`` and
    passes. Without it, it asserts equality and, on mismatch, fails with a compact
    unified diff instead of dumping the whole file.
    """
    updating: bool = request.config.getoption("--update-golden")

    def check(actual: str, filename: str) -> None:
        path = GOLDEN_DIR / filename
        if updating:
            path.write_text(actual, encoding="utf-8")
            return
        expected = path.read_text(encoding="utf-8")
        if actual == expected:
            return
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"{filename} (golden)",
                tofile=f"{filename} (actual)",
            )
        )
        head = "\n".join(diff.splitlines()[:25])
        pytest.fail(
            f"{filename} differs from the golden file. Regenerate on purpose with "
            f"`uv run pytest --update-golden`.\n\n{head}",
            pytrace=False,
        )

    return check
