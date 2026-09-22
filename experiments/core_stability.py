"""Is the raw unsatisfiable core stable across fresh solves? (paper §3, "which thread")

Re-creates the Stage 0 spike (docs/explainability-literature.md §5.4, originally not
scripted) on the toy course: pick two areas that share a capable mower and a legal common
start hour, assume both are served by that mower at that hour — a genuine conflict with the
encoding's no-overlap constraint — and solve that assumption set ``--repeats`` times, each
on a fresh ``Control`` with a fresh grounding, once under the ``-t4`` portfolio and once
under the deterministic single-threaded path. Prints the distinct raw cores per
configuration.

    uv run python experiments/core_stability.py [--repeats 6]
"""

from __future__ import annotations

import argparse
import itertools
import platform
from importlib.metadata import version

import clingo
from clingcon import ClingconTheory
from clingo.ast import ProgramBuilder, parse_string

from fleetplanning.model import PreferredTask, SolvePreferences
from fleetplanning.scenarios.toy_course import toy_course
from fleetplanning.service import DETERMINISTIC_CONFIG, encoding_text
from fleetplanning.solver.completion_table import build_completion_table
from fleetplanning.solver.instance import render_instance
from fleetplanning.solver.preferences import legal_starts, render_preferences

CONFIGS = {
    "portfolio (-t4 --configuration=many)": ["-t4", "--configuration=many"],
    f"deterministic (-t1 --configuration={DETERMINISTIC_CONFIG})": [
        "-t1",
        f"--configuration={DETERMINISTIC_CONFIG}",
    ],
}


def pick_conflict(scenario, rows):
    """First (area, area, mower, hour) in sorted order where both areas can start on the
    same mower at the same legal hour."""
    _, by_pair = legal_starts(scenario, rows)
    areas = sorted({a for a, _ in by_pair})
    mowers = sorted({m for _, m in by_pair})
    for a1, a2 in itertools.combinations(areas, 2):
        for m in mowers:
            common = by_pair.get((a1, m), set()) & by_pair.get((a2, m), set())
            if common:
                return a1, a2, m, min(common)
    raise SystemExit("no two areas share a mower and a legal start hour")


def raw_core(scenario, rows, prefs, targets, args: list[str]) -> tuple[str, ...]:
    ctl = clingo.Control(
        [*args, "-c", f"horizon={scenario.horizon_hours}", "-c", "pref_level=6"]
    )
    theory = ClingconTheory()
    theory.register(ctl)
    programs = [
        encoding_text(),
        encoding_text("preferences_weak.lp"),
        render_instance(scenario, completion_rows=rows),
        render_preferences(prefs, scenario, rows).text,
    ]
    with ProgramBuilder(ctl) as b:
        for p in programs:
            parse_string(p, lambda ast: theory.rewrite_ast(ast, b.add))
    ctl.ground([("base", [])])
    theory.prepare(ctl)

    name_of = {}
    for sym in targets:
        atom = ctl.symbolic_atoms[sym]
        if atom is None:
            raise SystemExit(f"{sym} did not survive grounding")
        name_of[atom.literal] = str(sym)
    with ctl.solve(assumptions=list(name_of), yield_=False, async_=True) as handle:
        handle.wait()
        if not handle.get().unsatisfiable:
            raise SystemExit("the hand-built conflict is satisfiable")
        return tuple(sorted(name_of[lit] for lit in handle.core()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repeats", type=int, default=6)
    n = ap.parse_args().repeats

    scenario = toy_course()
    rows = build_completion_table(scenario)
    a1, a2, mower, hour = pick_conflict(scenario, rows)
    prefs = SolvePreferences(
        tasks=[PreferredTask(area=a, start=hour, mower=mower) for a in (a1, a2)],
        mode="weak",
        level="top",
    )
    targets = [
        clingo.Function(name, [clingo.String(a), clingo.Number(hour), *extra])
        for a in (a1, a2)
        for name, extra in (("pref_time_met", []), ("pref_mower_met", [clingo.String(mower)]))
    ]
    print(f"conflict: {a1} and {a2} both on {mower} at hour {hour}; {len(targets)} assumptions")
    print(f"clingo {version('clingo')}, clingcon {version('clingcon')}, {platform.platform()}")
    for label, args in CONFIGS.items():
        cores = [raw_core(scenario, rows, prefs, targets, args) for _ in range(n)]
        distinct = sorted(set(cores), key=lambda c: (len(c), c))
        sizes = ", ".join(str(len(c)) for c in distinct)
        print(f"\n{label}: {len(distinct)} distinct raw core(s) over {n} fresh solves "
              f"(sizes {sizes})")
        for c in distinct:
            print(f"  {cores.count(c)}x  {' '.join(c)}")


if __name__ == "__main__":
    main()
