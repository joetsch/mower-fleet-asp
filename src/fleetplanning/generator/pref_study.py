"""`pref-study` — the preference-mechanism study runner (ADR-0033).

Mirrors ``generator/evaluate.py`` (ADR-0016) deliberately rather than reinventing it: the
threading, budget and stat plumbing are the same, only the cells differ. A **cell** is one
scenario × one arm × one edit set; an **arm** (decision 1) is a choice of overlay ``.lp``
plus command-line flags and nothing else in the pipeline varies.

Per scenario:

- solve cold at ``threads=1`` (deterministic — ADR-0016 decision 1) to get the **reference
  plan**; this run doubles as the ``cold`` arm's ``t1`` row and as the fixed anchor every
  ``plan_distance`` is measured against (decision 3);
- sample three edit sets from the reference plan with a seeded RNG — **freeze-k** at
  ``f ∈ {0.1, 0.3, 0.6}`` (decision 2, freeze-only since the 2026-09-07 amendment). The
  same edit set is handed to all seven arms, so arms differ only in mechanism;
- for every ``(arm, edit set)`` cell, solve once at ``threads=1`` and ``portfolio_repeats``
  times at ``-t4 --configuration=many`` (decision 4), tracing each incumbent's atoms
  (``trace_models=True``, decision 6) so agreement / score / ``plan_distance`` can be read
  at any budget off one run.

Writes under ``<dataset>/pref-study/``:

- ``by_scenario/<id>.json`` — every raw record and the full anytime trace, so re-analysis
  never re-solves;
- ``summary.csv`` — one row per scenario × arm × edit set × mode × replicate, with the
  manifest covariates joined on;
- ``meta.json`` — arms, seeds, budgets, tool + host versions, timestamps, and the list of
  scenarios skipped for want of a reference incumbent.

``plan_distance`` lives here only; ``SolveResult`` is untouched (decision 7).
"""

from __future__ import annotations

import csv
import json
import os
import platform
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from fleetplanning.generator.runinfo import COVARIATE_KEYS as _COVARIATE_KEYS
from fleetplanning.generator.runinfo import tool_versions as _tool_versions
from fleetplanning.model import PreferredTask, Scenario, ScheduledTask, SolvePreferences
from fleetplanning.service import control_args, encoding_text
from fleetplanning.solver.completion_table import CompletionRow, build_completion_table
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.parse import parse_schedule
from fleetplanning.solver.preferences import preference_agreement, render_preferences
from fleetplanning.solver.runner import ClingconResult, ClingconSolver
from fleetplanning.solver.score import score_schedule

# --------------------------------------------------------------------------- arms


@dataclass(frozen=True)
class Arm:
    """One study arm (ADR-0033 decision 1): an overlay ``.lp`` plus static flags.

    ``overlay`` is ``None`` for ``cold`` — the control, solved with no overlay and no
    facts, the plan a greenkeeper gets by pressing solve again and ignoring their edits.
    """

    name: str
    overlay: str | None
    flags: tuple[str, ...]


ARMS: tuple[Arm, ...] = (
    Arm("cold", None, ()),
    Arm("weak@top", "preferences_weak.lp", ("-c", "pref_level=6")),
    Arm("weak@tiebreak", "preferences_weak.lp", ("-c", "pref_level=-1")),
    Arm("weak-graded", "preferences_graded.lp", ("-c", "pref_level=6")),
    Arm("heur[10,true]", "preferences_heuristic.lp", ("--heuristic=Domain", "-c", "pref_w=10")),
    Arm("heur[1,true]", "preferences_heuristic.lp", ("--heuristic=Domain", "-c", "pref_w=1")),
    Arm("heur-soft", "preferences_heuristic_soft.lp", ("--heuristic=Domain", "-c", "pref_w=10")),
)

def select_arms(spec: str | None, arms: Sequence[Arm] = ARMS) -> tuple[Arm, ...]:
    """Resolve a comma-separated arm spec to a subset of ``arms``, ``cold`` always included.

    Split at **top level only**: ``heur[1,true]`` and ``heur[10,true]`` carry a comma
    *inside the name* (it is the ``[weight,sign]`` of the ``#heuristic`` directive), so a
    plain ``spec.split(",")`` silently shreds them into two unknown arms — which made the
    one arm ADR-0034 decision 4 kept for the rolling use case unselectable from the CLI.

    Returns them in ``ARMS`` order, not the caller's, so ``run_scenario``'s
    ``arms[0].name == "cold"`` anchor assumption holds however the spec was written.
    """
    if spec is None:
        return tuple(arms)

    names, depth, current = [], 0, []
    for ch in spec:
        if ch == "," and depth == 0:
            names.append("".join(current))
            current = []
            continue
        depth += (ch == "[") - (ch == "]")
        current.append(ch)
    names.append("".join(current))

    wanted = {"cold", *(n.strip() for n in names if n.strip())}
    unknown = wanted - {a.name for a in arms}
    if unknown:
        raise ValueError(f"unknown arm(s): {sorted(unknown)}")
    return tuple(a for a in arms if a.name in wanted)


#: The ``--configuration`` the deterministic ``threads=1`` runs use.
#:
#: Originally study-local (the product's ``threads=1`` inherited clingo's ``tweety``
#: default); ADR-0041 moved the product default to this same ``jumpy``, so the two now
#: agree. Kept as an explicit knob here — ``--t1-config`` on the CLI, ``None`` for
#: clingo's default — so a future bake-off can pin a different one for the study without
#: moving the product. ``solve_args`` builds the ``threads=1`` command line explicitly
#: for exactly that reason.
#:
#: Why it matters: tweety is pathological on this encoding — the pilot triage found it
#: returning *zero* incumbents in 300 s on instances every other configuration solves in
#: under 2.5 s, which silently excluded half a hard pool (the reference anchor, ADR-0033
#: decision 2, is a ``threads=1`` solve, and a scenario with no anchor is skipped).
#:
#: ``jumpy`` is chosen on a bake-off over the 18-instance pilot pool (5 configurations x 18
#: instances, ``threads=1``, 60 s): it anchors 18/18 where tweety manages 9/18, has the best
#: mean final-incumbent cost rank (2.11 of 5, against handy 2.44, crafty 2.94, frumpy 3.67),
#: and reaches its first incumbent in a median 1.15 s. Every instance in the pool is
#: anchorable by *some* configuration, so nothing in it is genuinely too hard for ``t1`` —
#: the NO-ANCHOR stratum was a tweety artifact.
T1_CONFIG: str | None = "jumpy"

#: ``--stats=2`` on *every* arm: it is the only detail level under which
#: ``solving.solvers.extra.domain_choices`` materialises (the inert-``#heuristic`` guard,
#: ADR-0033).
#:
#: **The guard reads clean only at ``threads=1``.** There the non-heuristic arms are a hard
#: 0 and any non-zero value is the overlay's ``#heuristic`` firing. Under the portfolio it
#: is not: ``--configuration=many`` gives different threads different configurations and
#: some of those use a domain heuristic of their own, so ``cold`` returns non-zero — and
#: *nondeterministically* so (observed 0, 179, 227, 1323 across four replicates of one
#: cell). So "did the mechanism fire" is answered off the ``t1`` rows; on portfolio rows
#: the stat is only a magnitude comparison against ``cold`` on the same scenario.
_STATS_FLAG = ("--stats=2",)

FREEZE_FRACTIONS: tuple[float, ...] = (0.1, 0.3, 0.6)


# ------------------------------------------------------------------- edit-set sampling


def _rng(base_seed: int, scenario_id: str, shape: str, fraction: float) -> random.Random:
    """A per-cell RNG, seeded deterministically. ``random.Random`` hashes a str seed with
    SHA-512, so this is reproducible across processes (unlike ``hash()``)."""
    return random.Random(f"pref-study|{base_seed}|{scenario_id}|{shape}|{fraction}")


def _k(fraction: float, n_tasks: int) -> int:
    return max(1, round(fraction * n_tasks))


def freeze_edit_set(
    reference: Sequence[ScheduledTask], fraction: float, rng: random.Random
) -> list[PreferredTask]:
    """**freeze-k**: sample ``max(1, round(f·|tasks|))`` reference tasks and freeze each
    *whole* — area, start hour and mower (ADR-0033 decision 2). "Keep these, redo the rest.\""""
    chosen = rng.sample(list(reference), _k(fraction, len(reference)))
    return [
        PreferredTask(area=t.area, start=t.start, mower=t.mower, origin="frozen")
        for t in sorted(chosen, key=lambda t: (t.area, t.start))
    ]


@dataclass(frozen=True)
class EditSet:
    label: str
    tasks: list[PreferredTask]


def build_edit_sets(
    reference: Sequence[ScheduledTask],
    scenario_id: str,
    base_seed: int,
    *,
    freeze_fractions: Sequence[float] = FREEZE_FRACTIONS,
) -> list[EditSet]:
    """One **freeze-k** set per fraction (decision 2, freeze-only). Never empty: the
    reference plan has at least one task or the scenario is skipped, and ``_k`` floors at 1."""
    return [
        EditSet(
            f"freeze@{f}",
            freeze_edit_set(reference, f, _rng(base_seed, scenario_id, "freeze", f)),
        )
        for f in freeze_fractions
    ]


# ------------------------------------------------------------------------ plan_distance

_Triple = tuple[str, int, str]


def _triples(tasks: Sequence[ScheduledTask]) -> Counter[_Triple]:
    return Counter((t.area, t.start, t.mower) for t in tasks)


def plan_distance(
    reference: Sequence[ScheduledTask],
    candidate: Sequence[ScheduledTask],
    edit_set: Sequence[PreferredTask],
) -> float:
    """Churn *outside* the preference set, against the ``threads=1`` reference plan
    (ADR-0033 decision 3), index-free.

    Let ``T(P)`` be the multiset of ``(area, start, mower)`` triples of plan ``P`` and
    ``F`` the triples named by the edit set. Then::

        |(T(cold) \\ F) △ (T(new) \\ F)| / (|T(cold) \\ F| + |T(new) \\ F|)

    0 when the unnamed remainder is untouched, 1 when it is wholly rearranged; an empty
    denominator is defined as 0. ``Counter`` subtraction is multiset difference (negative
    counts dropped), which is exactly ``\\`` and ``△`` here.
    """
    named = Counter(
        (p.area, p.start, p.mower) for p in edit_set if p.mower is not None
    )
    ref = _triples(reference) - named
    new = _triples(candidate) - named
    symmetric = (ref - new) + (new - ref)
    denom = sum(ref.values()) + sum(new.values())
    return sum(symmetric.values()) / denom if denom else 0.0


# ----------------------------------------------------------------------------- records

_TaskTuple = tuple[str, int, str, int, int]  # area, task, mower, start, end


def _as_tuples(tasks: Sequence[ScheduledTask]) -> list[_TaskTuple]:
    """A plan as plain ``(area, task, mower, start, end)`` tuples — JSON-friendly, and all
    that ``report.py`` needs to recompute any metric off a stored trace without re-solving."""
    return [(t.area, t.task, t.mower, t.start, t.end) for t in tasks]


class Incumbent(BaseModel):
    elapsed_s: float
    cost: list[int]
    tasks: list[_TaskTuple]


class PrefRun(BaseModel):
    arm: str
    edit_set: str
    mode: str  # "t1" | "portfolio"
    replicate: int
    status: str
    solve_time_s: float
    ground_time_s: float
    proof_time_s: float | None
    first_incumbent_s: float | None
    stats: dict[str, float]
    incumbents: list[Incumbent]
    # final-incumbent metrics (also derivable from the trace)
    agreement: dict
    score: list[int] | None
    score_detail: dict[str, int]
    plan_distance: float | None


class PrefScenarioRecord(BaseModel):
    scenario_id: str
    covariates: dict
    reference_status: str
    reference_tasks: list[_TaskTuple]
    edit_sets: dict[str, list[dict]]  # label -> [PreferredTask.model_dump(), ...]
    runs: list[PrefRun]


class PrefStudyMeta(BaseModel):
    arms: list[str]
    freeze_fractions: list[float]
    base_seed: int
    budget_s: float
    t1_budget_s: float
    portfolio_repeats: int
    t1_config: str | None
    tool_versions: dict[str, str]
    host: dict[str, str]
    started_utc: str
    finished_utc: str
    skipped: list[str]


# -------------------------------------------------------------------------- the solves


@dataclass
class _Solve:
    """One clingcon run plus the parsed plan of each incumbent it reported."""

    result: ClingconResult
    incumbents: list[tuple[float, list[int], list[ScheduledTask]]] = field(default_factory=list)

    @property
    def final_tasks(self) -> list[ScheduledTask]:
        return self.incumbents[-1][2] if self.incumbents else []


def solve_args(
    scenario: Scenario, arm: Arm, threads: int, t1_config: str | None = T1_CONFIG
) -> list[str]:
    """The clingo argument list for one cell — pure, so it is unit-tested directly (the
    same reason ``service.control_args`` is).

    At ``threads=1`` the study builds the portfolio explicitly (``clingo_args``) rather
    than letting ``control_args`` supply it, so ``t1_config`` stays a study knob — the
    product's own ``threads=1`` default is ``--configuration=jumpy`` since ADR-0041, and
    appending a second ``--configuration`` on top of it is what clingo rejects. Above one
    thread ``control_args`` sets ``--configuration=many`` and the study does not touch it.
    """
    flags = list(arm.flags) + list(_STATS_FLAG)
    if arm.name == "weak-graded":
        flags += ["-c", f"pref_maxdev={scenario.horizon_hours}"]
    if threads == 1:
        portfolio = ["-t1"]
        if t1_config is not None:
            portfolio.append(f"--configuration={t1_config}")
        return control_args(scenario, threads, clingo_args=portfolio, extra_args=flags)
    return control_args(scenario, threads, extra_args=flags)


def _run_solve(
    scenario: Scenario,
    rows: list[CompletionRow],
    arm: Arm,
    edit_facts: str,
    *,
    threads: int,
    budget_s: float,
    t1_config: str | None = T1_CONFIG,
) -> _Solve:
    solver = ClingconSolver(solve_args(scenario, arm, threads, t1_config))
    solver.add(encoding_text())
    solver.add(render_instance(scenario, rows))
    if arm.overlay is not None:
        solver.add(encoding_text(arm.overlay))
        solver.add(edit_facts)
    result = solver.solve(time_limit_s=budget_s, trace_models=True)

    parsed: list[tuple[float, list[int], list[ScheduledTask]]] = []
    for (elapsed, cost), atoms in zip(result.incumbents, result.incumbent_atoms, strict=True):
        parsed.append((elapsed, cost, parse_schedule(atoms, cost).tasks))
    return _Solve(result=result, incumbents=parsed)


def _pref_run(
    arm: Arm,
    edit_set: EditSet,
    mode: str,
    replicate: int,
    solve: _Solve,
    scenario: Scenario,
    rows: list[CompletionRow],
    reference: Sequence[ScheduledTask],
) -> PrefRun:
    prefs = SolvePreferences(tasks=edit_set.tasks, mode="weak")

    def metrics(tasks: Sequence[ScheduledTask]) -> tuple[dict, list[int] | None, dict, float]:
        agreement = preference_agreement(prefs, tasks).model_dump()
        if tasks:
            sv = score_schedule(scenario, tasks, completion_rows=rows)
            score, detail = sv.slots, sv.detail
        else:
            score, detail = None, {}
        return agreement, score, detail, plan_distance(reference, tasks, edit_set.tasks)

    trace = [
        Incumbent(elapsed_s=elapsed, cost=cost, tasks=_as_tuples(tasks))
        for (elapsed, cost, tasks) in solve.incumbents
    ]
    r = solve.result
    agreement, score, detail, distance = metrics(solve.final_tasks)
    return PrefRun(
        arm=arm.name,
        edit_set=edit_set.label,
        mode=mode,
        replicate=replicate,
        status=r.status,
        solve_time_s=r.solve_time_s,
        ground_time_s=r.ground_time_s,
        proof_time_s=r.proof_time_s,
        first_incumbent_s=r.incumbents[0][0] if r.incumbents else None,
        stats=r.stats,
        incumbents=trace,
        agreement=agreement,
        score=score,
        score_detail=detail,
        plan_distance=distance if solve.final_tasks else None,
    )


# ------------------------------------------------------------------------ the scenario


def _empty_facts_error(edit_set: EditSet, dropped: object) -> str:
    return (
        f"edit set {edit_set.label!r} rendered no facts (dropped={dropped!r}); "
        "ADR-0033 decision 2 says a frozen edit comes from the reference plan and so is "
        "legal by construction and never dropped"
    )


def run_scenario(
    scenario: Scenario,
    covariates: dict,
    *,
    base_seed: int,
    budget_s: float,
    t1_budget_s: float,
    portfolio_repeats: int,
    arms: Sequence[Arm] = ARMS,
    freeze_fractions: Sequence[float] = FREEZE_FRACTIONS,
    t1_config: str | None = T1_CONFIG,
) -> PrefScenarioRecord | None:
    """Run every arm × edit set for one scenario. Returns ``None`` (caller records it as
    skipped) when the cold ``threads=1`` solve finds no incumbent to anchor on."""
    rows = build_completion_table(scenario)

    cold_arm = arms[0]
    assert cold_arm.name == "cold"
    cold_t1 = _run_solve(
        scenario, rows, cold_arm, "", threads=1, budget_s=t1_budget_s, t1_config=t1_config
    )
    if not cold_t1.final_tasks:
        return None
    reference = cold_t1.final_tasks

    edit_sets = build_edit_sets(
        reference, scenario.name, base_seed, freeze_fractions=freeze_fractions
    )

    # Render the facts once per edit set — the same program feeds every mechanism arm.
    facts: dict[str, str] = {}
    for es in edit_sets:
        rendered = render_preferences(
            SolvePreferences(tasks=es.tasks, mode="weak"), scenario, rows
        )
        if not rendered.text:  # a non-empty edit set that produced no facts breaks decision 2
            raise RuntimeError(_empty_facts_error(es, rendered.dropped))
        facts[es.label] = rendered.text

    runs: list[PrefRun] = []

    # cold: solved once per (mode, replicate), then scored against every edit set.
    cold_solves = [("t1", 0, cold_t1)]
    for k in range(portfolio_repeats):
        cold_solves.append(
            ("portfolio", k, _run_solve(
                scenario, rows, cold_arm, "", threads=4, budget_s=budget_s, t1_config=t1_config
            ))
        )
    for edit_set in edit_sets:
        for mode, replicate, solve in cold_solves:
            runs.append(
                _pref_run(cold_arm, edit_set, mode, replicate, solve, scenario, rows, reference)
            )

    # the six mechanism arms: a fresh solve per (arm, edit set, run).
    for arm in arms[1:]:
        for edit_set in edit_sets:
            plans = [
                ("t1", 0, _run_solve(
                    scenario, rows, arm, facts[edit_set.label], threads=1,
                    budget_s=t1_budget_s, t1_config=t1_config,
                ))
            ]
            for k in range(portfolio_repeats):
                plans.append(
                    ("portfolio", k, _run_solve(
                        scenario, rows, arm, facts[edit_set.label], threads=4,
                        budget_s=budget_s, t1_config=t1_config,
                    ))
                )
            for mode, replicate, solve in plans:
                runs.append(
                    _pref_run(arm, edit_set, mode, replicate, solve, scenario, rows, reference)
                )

    return PrefScenarioRecord(
        scenario_id=scenario.name,
        covariates=covariates,
        reference_status=cold_t1.result.status,
        reference_tasks=_as_tuples(reference),
        edit_sets={es.label: [t.model_dump() for t in es.tasks] for es in edit_sets},
        runs=runs,
    )


# -------------------------------------------------------------------------- the dataset


def run_pref_study(
    dataset_dir: Path,
    *,
    budget_s: float,
    t1_budget_s: float,
    portfolio_repeats: int,
    base_seed: int = 1,
    arms: Sequence[Arm] = ARMS,
    freeze_fractions: Sequence[float] = FREEZE_FRACTIONS,
    t1_config: str | None = T1_CONFIG,
    limit: int | None = None,
    force: bool = False,
) -> Path:
    """Run the study over every scenario in ``dataset_dir``; return the ``pref-study/`` path.

    Resumable exactly like ``evaluate`` (ADR-0016): a scenario whose ``by_scenario/<id>.json``
    already exists is skipped unless ``force``; ``summary.csv`` is always rebuilt from every
    record on disk.
    """
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    out = dataset_dir / "pref-study"
    by = out / "by_scenario"
    by.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC).isoformat(timespec="seconds")
    entries = manifest["scenarios"][: limit if limit is not None else len(manifest["scenarios"])]
    skipped: list[str] = []

    for i, entry in enumerate(entries, 1):
        sid = entry["id"]
        dest = by / f"{sid}.json"
        if dest.exists() and not force:
            continue
        print(f"[{i}/{len(entries)}] {sid}", flush=True)
        scenario = Scenario.model_validate_json(
            (dataset_dir / entry["scenario"]).read_text(encoding="utf-8")
        )
        covariates = {k: entry[k] for k in _COVARIATE_KEYS if k in entry}
        cell = entry.get("seed_spec", {}).get("cell")
        covariates["design"] = cell.get("design") if isinstance(cell, dict) else None

        record = run_scenario(
            scenario, covariates,
            base_seed=base_seed, budget_s=budget_s, t1_budget_s=t1_budget_s,
            portfolio_repeats=portfolio_repeats, arms=arms, freeze_fractions=freeze_fractions,
            t1_config=t1_config,
        )
        if record is None:
            skipped.append(sid)
            print(f"    skipped — no reference incumbent within {t1_budget_s}s", flush=True)
            continue
        dest.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    csv_rows: list[dict] = []
    for entry in entries:
        p = by / f"{entry['id']}.json"
        if p.exists():
            csv_rows.extend(
                _csv_rows(PrefScenarioRecord.model_validate_json(p.read_text(encoding="utf-8")))
            )
    _write_summary(out / "summary.csv", csv_rows)

    prev = out / "meta.json"
    if prev.exists() and not force:
        prev_meta = json.loads(prev.read_text(encoding="utf-8"))
        started = prev_meta.get("started_utc", started)
        skipped = sorted(set(skipped) | set(prev_meta.get("skipped", [])))
    meta = PrefStudyMeta(
        arms=[a.name for a in arms],
        freeze_fractions=list(freeze_fractions),
        base_seed=base_seed,
        budget_s=budget_s,
        t1_budget_s=t1_budget_s,
        portfolio_repeats=portfolio_repeats,
        t1_config=t1_config,
        tool_versions=_tool_versions(),
        host={
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "cpu_count": str(os.cpu_count()),
        },
        started_utc=started,
        finished_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        skipped=skipped,
    )
    (out / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    return out


# ------------------------------------------------------------------------------- summary

_SUMMARY_FIELDS = (
    "scenario_id", "arm", "edit_set", "mode", "replicate",
    "design", "load_factor", "holes", "areas", "mowers",
    "distinct_priorities", "n_completion_facts", "task_atom_bound",
    "status", "first_incumbent_s", "proof_time_s", "solve_time_s", "ground_time_s",
    "ground_rules", "choices", "conflicts", "domain_choices",
    "time_kept", "time_total", "mower_kept", "mower_total",
    "score_p1", "score_p2", "score_p3", "score_avoid", "score_min",
    "plan_distance",
)


def _csv_rows(record: PrefScenarioRecord) -> list[dict]:
    cov = record.covariates
    base = {
        "scenario_id": record.scenario_id,
        "design": cov.get("design"),
        "load_factor": cov.get("load_factor"),
        "holes": cov.get("holes"),
        "areas": cov.get("areas"),
        "mowers": cov.get("mowers"),
        "distinct_priorities": cov.get("distinct_priorities"),
        "n_completion_facts": cov.get("n_completion_facts"),
        "task_atom_bound": cov.get("task_atom_bound"),
    }
    out: list[dict] = []
    for run in record.runs:
        row = dict(base)
        row.update(
            arm=run.arm,
            edit_set=run.edit_set,
            mode=run.mode,
            replicate=run.replicate,
            status=run.status,
            first_incumbent_s=run.first_incumbent_s,
            proof_time_s=run.proof_time_s,
            solve_time_s=run.solve_time_s,
            ground_time_s=run.ground_time_s,
            ground_rules=run.stats.get("ground_rules"),
            choices=run.stats.get("choices"),
            conflicts=run.stats.get("conflicts"),
            domain_choices=run.stats.get("domain_choices"),
            time_kept=run.agreement.get("time_kept"),
            time_total=run.agreement.get("total"),
            mower_kept=run.agreement.get("mower_kept"),
            mower_total=run.agreement.get("mower_total"),
            plan_distance=run.plan_distance,
        )
        for key, value in zip(
            ("score_p1", "score_p2", "score_p3", "score_avoid", "score_min"),
            run.score or [None] * 5,
            strict=True,
        ):
            row[key] = value
        out.append(row)
    return out


def _write_summary(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
