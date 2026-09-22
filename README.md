# Plan, Edit, Explain — robotic mower fleet scheduling with clingcon

Companion repository for the TAASP 2026 extended abstract *Plan, Edit, Explain:
Interactive Scheduling of Robotic Mower Fleets with Hybrid ASP* (J. Oetsch). It holds two
things:

1. **The demonstrator.** A web app that plans a golf course's robotic mower fleet for a
   week, re-plans around the user's edits, and explains edits it could not keep.
2. **The experiments.** Everything needed to recompute or re-run every number the paper
   reports.

All scenarios are synthetic. How the encodings work is described in
[`ENCODING.md`](ENCODING.md).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.11, which is needed because
  `clingcon` has no wheels for newer versions.
- [Node.js](https://nodejs.org/) 20 or newer, with `npm`.
- git.

Developed and measured on macOS (Apple silicon, 8 cores). Linux should work the same way.

## Run the demonstrator

```sh
git clone https://github.com/joetsch/mower-fleet-asp.git
cd mower-fleet-asp
uv sync                          # Python environment: clingo, clingcon, FastAPI
uv run fleetplanning-api         # backend on http://localhost:8000
```

In a second terminal:

```sh
cd mower-fleet-asp/frontend
npm ci
npm run dev                      # web UI on http://localhost:5173
```

Open <http://localhost:5173> and try the following:

1. **Plan.** Click **Load scenario**, pick `small-but-hard`, then click **Solve schedule**.
2. **Edit.** Drag a service to another hour, or click it to change its mower, then click
   **Re-solve**.
3. **Explain.** When an edit could not be kept, **Why?** names what it conflicts with.
4. **Roll forward.** **Move forward** advances "now" by a day and re-plans the rest of the
   week.

**Edit scenario** changes the course itself (areas, mowers, time windows). **Expert
mode** shows the solver settings and the raw cost vector.

## Reproduce the paper's numbers

All commands run from the repository root.

### Step 1 — recompute from the recorded outputs (seconds)

```sh
uv run python experiments/paper_numbers.py --check
```

This prints one line per number cited in the paper: the value as printed, then the value
recomputed from the recorded outputs in `experiments/`. It exits non-zero on any
mismatch. The test suite runs the same check.

### Step 2 — re-run the experiments (optional)

| Paper paragraph | Command | Time |
|---|---|---|
| *Editing and re-planning* (6 scenarios, 7 mechanisms) | `uv run python experiments/run.py replan --out /tmp/replan` | ~2.5 h |
| *Explaining dropped edits*, on the recorded plans | `uv run python experiments/run.py explain --replay --out /tmp/explain` | ~2 min |
| *Explaining dropped edits*, on new plans | `uv run python experiments/run.py explain --out /tmp/explain-new` | ~5 min |
| `--opt-mode=ignore` timings | `uv run python experiments/run.py optmode --out /tmp/optmode` | ~15 min |
| raw-core stability, portfolio vs. single thread | `uv run python experiments/core_stability.py` | seconds |

For a long run, keep the machine awake (`caffeinate -i …` on macOS) and don't run anything
heavy alongside it, because the timings depend on it.

Then compare a fresh run with the paper:

```sh
uv run python experiments/paper_numbers.py \
    --replan-csv  /tmp/replan/pref-study/summary.csv \
    --explain-csv /tmp/explain/summary.csv \
    --results     /tmp/optmode
uv run python experiments/replan-pilot/analyse.py /tmp/replan/pref-study   # full tables
```

For the core-stability numbers, compare the script's output with
`experiments/results/core_stability.txt`.

**What reproduces exactly, and what doesn't.**

- **Explanations reproduce exactly.** The plans being edited come from clingo's
  multi-threaded portfolio (`-t4`) under a time limit, as in the demonstrator. That
  portfolio is not deterministic, so the plans are recorded
  (`experiments/explain-pilot/cells.json`). The explanation checks over them are
  single-threaded and deterministic, so `explain --replay` gives the recorded outcomes
  again (up to timings). `optmode` always replays, so both modes explain the same edits.
- **New plans give new counts.** `explain` without `--replay` drops a different set of
  edits each run, so the counts move while the findings should hold.
- **Re-planning results vary.** `replan` compares mechanisms on the portfolio itself, so
  its agreement values vary between runs.
- **Timings depend on the machine.**

### What is where

| Path | What |
|---|---|
| `experiments/paper_numbers.py` | maps each number in the paper to the data it comes from |
| `experiments/run.py` | re-runs the experiments with the paper's parameters |
| `experiments/core_stability.py` | the raw-core stability measurement |
| `experiments/replan-pilot/` | the six frozen scenarios (`instances/`), the recorded `summary.csv` + `meta.json`, and the analysis script as it was run |
| `experiments/explain-pilot/` | the recorded plans (`cells.json`), `summary.csv` + `meta.json` (its scenarios are in the demo library) |
| `experiments/results/` | the recorded `--opt-mode` runs and core-stability output |
| `src/fleetplanning/generator/{pref_study,explain_study}.py` | the study runners |

The six re-planning scenarios came from a scenario generator that is not included. They
are shipped as fixed instances, and those instances are the benchmark.

## Tests

```sh
uv run pytest                    # backend (a few minutes, includes real solver runs)
uv run ruff check .
cd frontend && npm run test      # frontend
```

## Layout

| Path | What |
|---|---|
| `src/fleetplanning/solver/encodings/` | the clingcon encodings (see `ENCODING.md`) |
| `src/fleetplanning/solver/` | fact generation, clingcon runner, answer parsing |
| `src/fleetplanning/explain/` | "why wasn't my edit kept?" |
| `src/fleetplanning/api/` | FastAPI backend |
| `src/fleetplanning/scenarios/curated/` | the demo scenario library |
| `frontend/` | React + TypeScript client |
| `experiments/` | see above |

Code comments sometimes cite material that is not part of this snapshot: numbered design
records (`ADR-…`), design and study notes under `docs/`, and the exploratory notebook the
solver code was first ported from. The citations are left in place because they say *why*
a piece of code is the way it is, which is worth more than a tidy reference list.

## Licence

MIT — see [`LICENSE`](LICENSE).
