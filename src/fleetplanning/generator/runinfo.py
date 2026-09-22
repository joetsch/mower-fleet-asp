"""Run provenance shared by the evaluation and study runners (ADR-0050 amendment).

Kept free of ``numpy`` / ``typer`` / the calibration tables so the paper's study runners
(``pref_study``, ``explain_study``) ship in the public companion without the rest of the
generator.
"""

from __future__ import annotations

import platform
from importlib.metadata import version


def tool_versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for pkg in ("clingo", "clingcon", "numpy", "typer"):
        try:
            out[pkg] = version(pkg)
        except Exception:  # noqa: BLE001 - best effort
            out[pkg] = "unknown"
    return out


#: Per-scenario manifest fields a study record carries as covariates.
COVARIATE_KEYS = (
    "holes",
    "areas",
    "mowers",
    "groups",
    "capable_pairs",
    "capability_density",
    "load_factor",
    "over_window_pair_fraction",
    "structurally_unsat",
    "distinct_priorities",
    "n_completion_facts",
    "task_atom_bound",
)
