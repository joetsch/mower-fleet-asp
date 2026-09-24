"""Recompute every number the TAASP 2026 paper cites, from the recorded study outputs.

    uv run python experiments/paper_numbers.py            # print claim -> value
    uv run python experiments/paper_numbers.py --check    # exit 1 on any mismatch

``PAPER`` holds each value exactly as ``main.tex`` prints it; ``tests/test_paper_numbers.py``
checks both directions (data -> ``PAPER``, ``PAPER`` -> ``main.tex``). Pass other paths to
read the output of a re-run instead. Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# --- data locations ---
REPLAN_CSV = ROOT / "experiments/replan-pilot/summary.csv"
# --- end data locations ---

#: The values as printed in the paper, keyed by claim.
PAPER: dict[str, str] = {
    # Editing and re-planning — preference-mechanism pilot re-run on current code
    # (2026-09-16), -t4 portfolio rows.
    "replan.scenarios": "6",
    "replan.pinned": "30%",
    "replan.budget": "60 s",
    "replan.weak_top.kept_proven": "100%",
    "replan.weak_top.kept_unproven": "100%",
    "replan.weak_top.extra_time": "2%",
    "replan.weak_top.extra_p1_violations": "0",
    "replan.weak_top.worst_p1_violations": "3",
    "replan.weak_tiebreak.kept_unproven": "8%",
    "replan.cold.kept_unproven": "13%",
    "replan.heur.best_kept_unproven": "91%",  # the better of the two heuristic variants
    "replan.runs": "18",
    "replan.weak_top.all_kept_runs": "18",
    "replan.heur.best_all_kept_runs": "6",
}

HARD = ("balanced-four-hole", "six-hole-course")


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(100 * x)}%"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def replan(path: Path) -> dict[str, str]:
    """Portfolio rows; replicates collapsed to a median per (scenario, arm), then the
    median over scenarios in a stratum. Stratum = does the cold portfolio solve prove
    optimality. Quality price = scenario median of (arm's best − cold's best) on the
    priority-1 max-interval slot."""
    rows = [r for r in _rows(path) if r["mode"] == "portfolio"]
    cell: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        cell[(r["scenario_id"], r["arm"])].append(r)
    scenarios = sorted({s for s, _ in cell})
    proves = {s for s in scenarios if all(r["status"] == "optimal" for r in cell[(s, "cold")])}

    def kept(s: str, arm: str) -> float:
        return st.median(
            int(r["time_kept"]) / int(r["time_total"]) for r in cell[(s, arm)] if r["time_total"]
        )

    def stratum(arm: str, proven: bool) -> float | None:
        values = [kept(s, arm) for s in scenarios if (s in proves) == proven]
        return st.median(values) if values else None  # a partial re-run may lack a stratum

    def time_ratio(arm: str) -> float:
        def t(s: str, a: str) -> float:
            return st.median(float(r["solve_time_s"]) for r in cell[(s, a)] if r["solve_time_s"])

        return st.median(t(s, arm) / t(s, "cold") for s in scenarios)

    def best_p1(s: str, arm: str) -> float:
        slots = ("score_p1", "score_p2", "score_p3", "score_avoid", "score_min")
        vecs = [tuple(float(r[k]) for k in slots) for r in cell[(s, arm)] if r["score_p1"]]
        return min(vecs)[0]

    def all_kept_runs(arm: str) -> str:
        return str(sum(
            r["time_kept"] == r["time_total"] for s in scenarios for r in cell[(s, arm)]
            if r["time_total"]
        ))

    pinned = {r["edit_set"] for r in rows}
    p1_price = [best_p1(s, "weak@top") - best_p1(s, "cold") for s in scenarios]
    return {
        "replan.scenarios": str(len(scenarios)),
        "replan.pinned": ", ".join(sorted(_pct(float(e.split("@")[1])) for e in pinned)),
        "replan.weak_top.kept_proven": _pct(stratum("weak@top", True)),
        "replan.weak_top.kept_unproven": _pct(stratum("weak@top", False)),
        "replan.weak_top.extra_time": _pct(time_ratio("weak@top") - 1),
        "replan.weak_top.extra_p1_violations": f"{st.median(p1_price):g}",
        "replan.weak_top.worst_p1_violations": f"{max(p1_price):g}",
        "replan.weak_tiebreak.kept_unproven": _pct(stratum("weak@tiebreak", False)),
        "replan.cold.kept_unproven": _pct(stratum("cold", False)),
        "replan.heur.best_kept_unproven": _pct(
            max(stratum(a, False) or 0.0 for a in ("heur[1,true]", "heur[10,true]"))
        ),
        "replan.runs": str(len(cell[(scenarios[0], "cold")]) * len(scenarios)),
        "replan.weak_top.all_kept_runs": all_kept_runs("weak@top"),
        "replan.heur.best_all_kept_runs": str(
            max(int(all_kept_runs(a)) for a in ("heur[1,true]", "heur[10,true]"))
        ),
    }


def replan_meta(path: Path) -> dict[str, str]:
    meta = json.loads(path.read_text(encoding="utf-8"))
    return {"replan.budget": f"{meta['budget_s']:g} s"}


def compute(replan_csv: Path = REPLAN_CSV) -> dict[str, str]:
    return replan(replan_csv) | replan_meta(replan_csv.with_name("meta.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--replan-csv", type=Path, default=REPLAN_CSV)
    ap.add_argument("--check", action="store_true", help="exit 1 unless data == paper")
    args = ap.parse_args()

    values = compute(args.replan_csv)
    bad = 0
    for key, printed in PAPER.items():
        got = values.get(key, "(no data)")
        mark = "ok " if got == printed else "!! "
        bad += got != printed
        print(f"{mark}{key:42} paper {printed:>10}   data {got}")
    if args.check and bad:
        print(f"\n{bad} mismatch(es)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
