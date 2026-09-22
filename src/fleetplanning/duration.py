"""The one data-driven mowing-duration formula (ADR-0013, spec §6.4).

``generator/compile.py`` (building ``Scenario.base_durations``) and
``generator/params.py`` (synthesising history) both call this — so there is a single
definition.

    base_duration = ceil( area_size / (model_capacity_per_day / 24) * complexity_mult )

No travel term: at the observed station distances (max ~1000 m) and the slowest
transport speed (0.45 m/s) travel is < 1 h, below the whole-hour granularity. Durations
are deterministic — corpus variety comes from area size, complexity and mower model.

Lives at the package top level alongside ``reference.py`` so ``scenarios/template.py``
can call it without importing the optional generator package (hazard "the ADR-0014
boundary no longer holds"). Stdlib + one dict lookup, no ``numpy``.
"""

from __future__ import annotations

import math

from fleetplanning.reference import COMPLEXITY_MULT


def base_duration(size_m2: int, complexity: str, model_capacity_m2_per_day: int) -> int:
    """Whole productive hours for one (area, mower) pair. Always >= 1."""
    capacity_per_h = model_capacity_m2_per_day / 24
    mow_h = size_m2 / capacity_per_h
    return max(1, math.ceil(mow_h * COMPLEXITY_MULT[complexity]))
