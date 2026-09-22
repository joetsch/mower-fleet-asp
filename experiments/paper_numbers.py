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
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# --- data locations ---
REPLAN_CSV = ROOT / "experiments/replan-pilot/summary.csv"
EXPLAIN_CSV = ROOT / "experiments/explain-pilot/summary.csv"
RESULTS = ROOT / "experiments/results"
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
    # Explaining dropped edits — explanation pilot, recorded run (2026-09-16).
    "explain.scenarios": "4",
    "explain.dropped": "163",
    "explain.static_share": "52%",
    "explain.solver_median": "0.2 s",
    "explain.not_determined": "0",
    "explain.not_determined_scenarios": "0",
    "explain.refute_budget": "10 s",
    "explain.conflicts": "37",
    "explain.conflicts_stable": "37",
    "explain.stability_repeats": "5",
    "explain.largest_raw_set": "42",
    "explain.its_minimised_size": "2",
    "explain.local_sat": "66",
    "explain.local_sat_overturned": "25",
    "explain.free": "41",
    "explain.free_unproven": "41",
    # --opt-mode ablation over the two hardest scenarios (results/opt-mode-*): the explanation
    # phase only (grounding + checks), both modes replaying the same recorded cells.
    "optmode.opt": "412/373 s",
    "optmode.ignore": "62/52 s",
    # Raw-core stability (results/core_stability.txt).
    "core.fresh_solves": "6",
    "core.portfolio_distinct": "4",
    "core.deterministic_distinct": "1",
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


def explain(path: Path) -> dict[str, str]:
    rows = _rows(path)
    solver = [float(r["refute_time_s"]) for r in rows if r["refute_time_s"]]
    undetermined = [r for r in rows if r["outcome"] == "not_determined"]
    conflicts = [r for r in rows if r["outcome"] == "conflicts_with"]
    largest = max(conflicts, key=lambda r: int(r["raw_core_size"]), default={})
    # A local (pruned) check that came back satisfiable was then confirmed against every
    # kept edit: overturned ones became conflicts (pruning_missed), the rest stayed free.
    overturned = sum(r["pruning_missed"] == "True" for r in rows)
    confirmed_free = sum(r["outcome"] == "free_but_untaken" for r in rows)
    return {
        "explain.scenarios": str(len({r["scenario"] for r in rows})),
        "explain.dropped": str(len(rows)),
        "explain.static_share": _pct(
            sum(r["outcome"] == "blocked_statically" for r in rows) / len(rows)
        ),
        "explain.solver_median": f"{st.median(solver):.1f} s" if solver else "n/a",
        "explain.not_determined": str(len(undetermined)),
        "explain.not_determined_scenarios": str(len({r["scenario"] for r in undetermined})),
        "explain.conflicts": str(len(conflicts)),
        "explain.conflicts_stable": str(sum(r["stable"] == "True" for r in conflicts)),
        "explain.stability_repeats": str(
            max((int(r["stability_repeats"]) for r in conflicts), default=0)
        ),
        "explain.largest_raw_set": largest.get("raw_core_size", "n/a"),
        "explain.its_minimised_size": largest.get("min_core_size", "n/a"),
        "explain.local_sat": str(overturned + confirmed_free),
        "explain.local_sat_overturned": str(overturned),
        "explain.free": str(confirmed_free),
        "explain.free_unproven": str(
            sum(r["outcome"] == "free_but_untaken" and r["parent_optimal"] == "False"
                for r in rows)
        ),
    }


def explain_meta(path: Path) -> dict[str, str]:
    meta = json.loads(path.read_text(encoding="utf-8"))
    return {"explain.refute_budget": f"{meta['refute_time_limit_s']:g} s"}


def optmode(results: Path) -> dict[str, str]:
    out = {}
    for mode in ("opt", "ignore"):
        wall = json.loads((results / f"opt-mode-{mode}" / "meta.json").read_text())[
            "explain_s_by_scenario"
        ]
        out[f"optmode.{mode}"] = "/".join(f"{round(wall[s])}" for s in HARD) + " s"
    return out


def core(results: Path) -> dict[str, str]:
    text = (results / "core_stability.txt").read_text(encoding="utf-8")
    found = re.findall(r"^(portfolio|deterministic).*?: (\d+) distinct raw core\(s\) over (\d+)",
                       text, re.MULTILINE)
    by = {label: (n, repeats) for label, n, repeats in found}
    return {
        "core.fresh_solves": by["portfolio"][1],
        "core.portfolio_distinct": by["portfolio"][0],
        "core.deterministic_distinct": by["deterministic"][0],
    }


def compute(
    replan_csv: Path = REPLAN_CSV, explain_csv: Path = EXPLAIN_CSV, results: Path = RESULTS
) -> dict[str, str]:
    values = replan(replan_csv) | replan_meta(replan_csv.with_name("meta.json"))
    values |= explain(explain_csv) | explain_meta(explain_csv.with_name("meta.json"))
    if all((results / f"opt-mode-{m}" / "meta.json").exists() for m in ("opt", "ignore")):
        values |= optmode(results)
    if (results / "core_stability.txt").exists():
        values |= core(results)
    return values


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--replan-csv", type=Path, default=REPLAN_CSV)
    ap.add_argument("--explain-csv", type=Path, default=EXPLAIN_CSV)
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--check", action="store_true", help="exit 1 unless data == paper")
    args = ap.parse_args()

    values = compute(args.replan_csv, args.explain_csv, args.results)
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
