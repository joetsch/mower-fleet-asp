"""Replaying a recorded explanation cell reproduces its recorded outcomes (ADR-0050 amendment).

The plans behind the explanation pilot come from the ``-t4`` portfolio and are recorded in
``cells.json``; the explanation phase over them is single-threaded and deterministic. This
pins that claim on the recorded run itself, for the scenario that replays in seconds.
Timing-bound outcomes (a check that hit its budget) would be machine-dependent; the
scenario used here has none.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fleetplanning.generator.explain_study import CellFile, explain_cell

RECORDED = Path(__file__).resolve().parents[1] / "experiments/explain-pilot"
SCENARIO = "small-but-hard"
FIELDS = ("seed_tag", "area", "requested_start", "requested_mower", "outcome",
          "n_kept_relevant", "min_core_size", "stable", "pruning_missed")


def _csv(value: object) -> str:
    return "" if value is None else str(value)  # as csv.DictWriter wrote it


@pytest.mark.slow
def test_replaying_recorded_cells_reproduces_the_recorded_explanations() -> None:
    cells = CellFile.model_validate_json((RECORDED / "cells.json").read_text(encoding="utf-8"))
    replayed = []
    for cell in (c for c in cells.cells if c.scenario == SCENARIO):
        records, _ = explain_cell(
            cell, refute_time_limit_s=10.0, step_budget_s=5.0, max_minimise_total_s=15.0,
            stability_repeats=5,
        )
        replayed += [tuple(_csv(getattr(r, f)) for f in FIELDS) for r in records]

    with (RECORDED / "summary.csv").open(newline="") as f:
        recorded = [
            tuple(row[f] for f in FIELDS) for row in csv.DictReader(f)
            if row["scenario"] == SCENARIO
        ]
    assert recorded, "the recorded run has no dropped edits for this scenario"
    assert all(r[4] != "not_determined" for r in recorded)
    assert sorted(replayed) == sorted(recorded)
