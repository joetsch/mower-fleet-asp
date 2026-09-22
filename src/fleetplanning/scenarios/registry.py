"""Curated demo scenario library (ADR-0019, ADR-0026).

Phase 1 shipped a single hard-coded scenario. This module turns
``scenarios/curated/*.json`` into a small named library the API can list, load, and —
since ADR-0026 — save to and delete from.

Each ``<slug>.json`` is a serialized :class:`fleetplanning.model.Scenario`. Where a
``<slug>.source.json`` sits beside it, that is the generator's :class:`ScenarioSource`
(ADR-0014) — it carries the richer study/provenance data the runtime ``Scenario`` drops.
Hand-authored or UI-saved scenarios have no source file (``toy-course`` was the first).

Identity is the filename slug; there is deliberately no metadata/description file (see
ADR-0019). ``Scenario.name`` is kept equal to the slug.

Writes land in the package's ``curated/`` directory. Under an editable install (``uv
run``) that is the working tree, so a saved scenario shows up in ``git status`` and can
be committed. There is deliberately no separate "user scenarios" directory and no guard
against overwriting the scenarios that ship in git — in this solo repo ``git checkout``
is the backstop (ADR-0026).
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import fleetplanning.scenarios as _scenarios_pkg
from fleetplanning.generator.source import ScenarioSource  # pydantic-only, no `gen` extra
from fleetplanning.model import Scenario

# ``curated/`` is a data directory (no ``__init__.py``); resolve it from the parent
# package's file so the path is a real, writable ``Path`` rather than an
# ``importlib.resources`` traversable.
_CURATED_DIR = Path(_scenarios_pkg.__file__).parent / "curated"

# Test hook: point the library at a throwaway directory so save/delete tests never touch
# the real curated set. ``None`` means "use the packaged directory".
_dir_override: Path | None = None

# A conservative slug: lowercase alphanumerics and internal hyphens, 1–64 chars. Rejects
# path separators, ``..``, leading/trailing/doubled hyphens, uppercase and spaces.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SLUG_MAX = 64


class ScenarioNotFound(KeyError):
    """Raised by :func:`load` / :func:`delete` for an unknown slug."""


class ScenarioExists(KeyError):
    """Raised by :func:`save` when ``overwrite=False`` and the slug is already taken."""


class InvalidSlug(ValueError):
    """Raised by :func:`save` / :func:`delete` for a slug that fails :data:`_SLUG_RE`."""


def curated_dir() -> Path:
    """The directory the library reads from and writes to (test-overridable)."""
    return _dir_override or _CURATED_DIR


def valid_slug(slug: str) -> bool:
    return len(slug) <= _SLUG_MAX and _SLUG_RE.match(slug) is not None


def slugify(text: str) -> str:
    """Best-effort slug from a free-text scenario name. Lowercases, turns any run of
    non-alphanumerics into a single hyphen, trims hyphens, truncates to 64 chars.

    Raises :class:`InvalidSlug` if nothing usable is left.
    """
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:_SLUG_MAX].strip("-")
    if not valid_slug(s):
        raise InvalidSlug(f"cannot derive a slug from {text!r}")
    return s


@functools.lru_cache(maxsize=1)
def _library() -> dict[str, tuple[Scenario, ScenarioSource | None]]:
    """Parse every curated file once. Cached; invalidated by :func:`save` / :func:`delete`.

    A broken curated file raises here (``pydantic.ValidationError``) on first access,
    which surfaces as a 500 rather than silently serving a bad scenario.
    """
    out: dict[str, tuple[Scenario, ScenarioSource | None]] = {}
    for entry in sorted(curated_dir().iterdir(), key=lambda p: p.name):
        name = entry.name
        if not name.endswith(".json") or name.endswith(".source.json"):
            continue
        slug = name[: -len(".json")]
        scenario = Scenario.model_validate_json(entry.read_text(encoding="utf-8"))
        src_entry = curated_dir() / f"{slug}.source.json"
        source = (
            ScenarioSource.model_validate_json(src_entry.read_text(encoding="utf-8"))
            if src_entry.is_file()
            else None
        )
        out[slug] = (scenario, source)
    return out


def _invalidate() -> None:
    _library.cache_clear()


def scenario_ids() -> list[str]:
    """Slugs of every curated scenario, sorted."""
    return list(_library())


def load(slug: str) -> tuple[Scenario, ScenarioSource | None]:
    """The scenario and its source. Since ADR-0024 the running app ignores the source
    (the compiled ``Scenario`` is self-describing) — it is still parsed here for the
    generator/study tooling and the registry tests.

    Raises :class:`ScenarioNotFound` if the slug is unknown.
    """
    try:
        return _library()[slug]
    except KeyError as exc:
        raise ScenarioNotFound(slug) from exc


def save(slug: str, scenario: Scenario, *, overwrite: bool) -> None:
    """Write ``<slug>.json`` to the curated directory (ADR-0026).

    ``scenario.name`` is forced to ``slug`` so the on-disk invariant holds regardless of
    what the caller passed. Any ``<slug>.source.json`` sibling is left untouched — a
    hand-edited overwrite of a generated scenario keeps its (now possibly stale)
    provenance file; delete it by hand if the edit restructured the scenario.

    Raises :class:`InvalidSlug` for a malformed slug, :class:`ScenarioExists` when the
    slug is taken and ``overwrite`` is false.
    """
    if not valid_slug(slug):
        raise InvalidSlug(slug)
    path = curated_dir() / f"{slug}.json"
    if path.exists() and not overwrite:
        raise ScenarioExists(slug)
    to_write = scenario if scenario.name == slug else scenario.model_copy(update={"name": slug})
    # ``exclude_none`` matches how the curated library was written (scripts/promote_curated.py)
    # so re-saving an unchanged scenario is a no-op diff; the None-valued optionals
    # (min_services / max_services / model / …) are simply omitted.
    path.write_text(to_write.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    _invalidate()


def delete(slug: str) -> None:
    """Remove ``<slug>.json`` (and a ``<slug>.source.json`` sibling, if present).

    Raises :class:`InvalidSlug` for a malformed slug, :class:`ScenarioNotFound` if there
    is no such scenario.
    """
    if not valid_slug(slug):
        raise InvalidSlug(slug)
    path = curated_dir() / f"{slug}.json"
    if not path.is_file():
        raise ScenarioNotFound(slug)
    path.unlink()
    (curated_dir() / f"{slug}.source.json").unlink(missing_ok=True)
    _invalidate()
