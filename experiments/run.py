"""Re-run the TAASP 2026 paper's experiments with the parameters the paper reports.

    uv run python experiments/run.py replan  --out /tmp/replan    # ~2.5 h
    uv run python experiments/run.py explain --out /tmp/explain   # ~5 min
    uv run python experiments/run.py explain --out /tmp/explain --replay
    uv run python experiments/run.py optmode --out /tmp/optmode   # ~15 min

Then point ``paper_numbers.py`` at the fresh output (see the README). The plans behind the
explanation experiments come from the ``-t4`` portfolio and are not reproducible, so they
are recorded (``cells.json``): ``--replay`` explains the recorded plans again, and
``optmode`` always does, so both modes see the same dropped edits. A thin stdlib wrapper
over the study runners, so it needs no extra dependencies.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from fleetplanning.generator.explain_study import run_explain_pilot
from fleetplanning.generator.pref_study import ARMS, run_pref_study

ROOT = Path(__file__).resolve().parents[1]
# --- data locations ---
REPLAN_INSTANCES = ROOT / "experiments/replan-pilot/instances"
EXPLAIN_CELLS = ROOT / "experiments/explain-pilot/cells.json"
# --- end data locations ---

EXPLAIN_SCENARIOS = ["small-but-hard", "over-critical-load", "balanced-four-hole",
                     "six-hole-course"]
HARD = ["balanced-four-hole", "six-hole-course"]


def replan(out: Path, limit: int | None, budget_s: float, repeats: int) -> None:
    """Six frozen instances x seven mechanisms x (one -t1 + ``repeats`` -t4 solves)."""
    # Resumable: re-running the same command after an interruption (sleep, crash) skips
    # the scenarios already written under pref-study/by_scenario/.
    if not (out / "manifest.json").exists():
        if out.exists():
            raise SystemExit(f"{out} exists but holds no copied instances — pick a fresh dir")
        shutil.copytree(REPLAN_INSTANCES, out)
    written = run_pref_study(
        out, budget_s=budget_s, t1_budget_s=budget_s, portfolio_repeats=repeats,
        base_seed=1, arms=ARMS, freeze_fractions=(0.3,), t1_config="jumpy", limit=limit,
    )
    print(f"wrote {written}/summary.csv")


def explain(
    out: Path, scenarios: list[str], n_seeds: int, opt_mode: str, replay: Path | None
) -> Path:
    written = run_explain_pilot(
        scenarios, out, base_seed=1, fraction=0.4, n_seeds=n_seeds, time_limit_s=20.0,
        refute_time_limit_s=10.0, step_budget_s=5.0, max_minimise_total_s=15.0,
        stability_repeats=5, opt_mode=opt_mode, replay=replay,
    )
    print(f"wrote {written}/summary.csv")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="experiment", required=True)

    r = sub.add_parser("replan", help="edit mechanisms (paper: Editing and re-planning)")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--limit", type=int, help="only the first N instances (smoke test)")
    r.add_argument("--budget-s", type=float, default=60.0)
    r.add_argument("--repeats", type=int, default=3, help="-t4 portfolio repeats per cell")

    e = sub.add_parser("explain", help="explanation pilot (paper: Explaining dropped edits)")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--scenarios", default=",".join(EXPLAIN_SCENARIOS))
    e.add_argument("--n-seeds", type=int, default=3)
    e.add_argument("--opt-mode", default="ignore")
    e.add_argument("--replay", nargs="?", type=Path, const=EXPLAIN_CELLS, metavar="CELLS",
                   help="explain recorded plans instead of solving new ones "
                        "(default: the paper's recorded cells.json)")

    o = sub.add_parser("optmode", help="--opt-mode=opt vs ignore on the two hardest scenarios")
    o.add_argument("--out", type=Path, required=True)
    o.add_argument("--cells", type=Path, default=EXPLAIN_CELLS, help="recorded plans to replay")

    args = ap.parse_args()
    if args.experiment == "replan":
        replan(args.out, args.limit, args.budget_s, args.repeats)
    elif args.experiment == "explain":
        scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
        explain(args.out, scenarios, args.n_seeds, args.opt_mode, args.replay)
    else:
        for mode in ("ignore", "opt"):
            explain(args.out / f"opt-mode-{mode}", HARD, 3, mode, args.cells)


if __name__ == "__main__":
    main()
