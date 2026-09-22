"""Read a pref-study output and answer the ADR-0033 pilot's gate questions.

Deliberately not report.py: this is the go/no-go read on whether the matrix is worth a
night, not the study's published figures.
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ps = Path(sys.argv[1])
recs = [json.loads(p.read_text()) for p in sorted((ps / "by_scenario").glob("*.json"))]
meta = json.loads((ps / "meta.json").read_text())
ARMS = meta["arms"]


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 3) if xs else None


def iqr(xs):
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 4:
        return None
    q1, q3 = xs[len(xs) // 4], xs[(3 * len(xs)) // 4]
    return round(q3 - q1, 3)


runs = [(r["scenario_id"], run) for r in recs for run in r["runs"]]
print(f"scenarios={len(recs)}  runs={len(runs)}  arms={ARMS}")
print(f"budget={meta['budget_s']}s  t1_budget={meta['t1_budget_s']}s  "
      f"repeats={meta['portfolio_repeats']}  fractions={meta['freeze_fractions']}")
print(f"skipped (no t1 reference): {meta['skipped']}")

# ---------------------------------------------------------------- Q1/Q2: proof and budget
print("\n=== Q2  budget / difficulty ===")
print(f"{'arm':>14} {'mode':>9} {'n':>3} {'%optimal':>9} {'med solve':>10} "
      f"{'med proof':>10} {'med 1st inc':>12} {'med incs':>9}")
for arm in ARMS:
    for mode in ("t1", "portfolio"):
        sub = [r for _, r in runs if r["arm"] == arm and r["mode"] == mode]
        if not sub:
            continue
        opt = sum(r["status"] == "optimal" for r in sub) / len(sub)
        print(f"{arm:>14} {mode:>9} {len(sub):>3} {opt:>8.0%} "
              f"{str(med([r['solve_time_s'] for r in sub])):>10} "
              f"{str(med([r['proof_time_s'] for r in sub])):>10} "
              f"{str(med([r['first_incumbent_s'] for r in sub])):>12} "
              f"{str(med([len(r['incumbents']) for r in sub])):>9}")

# per-scenario difficulty, cold t1
print("\n  cold t1 per scenario:")
for r in recs:
    c = [x for x in r["runs"] if x["arm"] == "cold" and x["mode"] == "t1"][0]
    cov = r["covariates"]
    print(f"    {r['scenario_id']:>15} areas={cov.get('areas'):>3} load={cov.get('load_factor'):.2f} "
          f"tasks={len(r['reference_tasks']):>3} status={c['status']:>12} "
          f"solve={c['solve_time_s']:.1f}s incs={len(c['incumbents'])}")

# ------------------------------------------------------- Q1: do the arms reach different plans?
print("\n=== Q1  do the arms differ? ===")


def triples(tasks):
    return tuple(sorted((t[0], t[3], t[2]) for t in tasks))  # area, start, mower


collapsed = 0
cells = 0
for r in recs:
    by_cell = defaultdict(dict)
    for run in r["runs"]:
        if run["mode"] == "t1" and run["incumbents"]:
            by_cell[run["edit_set"]][run["arm"]] = triples(run["incumbents"][-1]["tasks"])
    for es, plans in by_cell.items():
        cells += 1
        distinct = len(set(plans.values()))
        if distinct == 1:
            collapsed += 1
        print(f"    {r['scenario_id']:>15} {es:>12}  distinct final plans across "
              f"{len(plans)} arms (t1): {distinct}")
print(f"  --> {collapsed}/{cells} t1 cells collapsed to a single plan across all arms")

print("\n  agreement with the edit set (final incumbent), by arm:")
print(f"{'arm':>14} {'mode':>9} {'med time_kept':>14} {'med mower_kept':>15} {'n@1.0':>7}")
for arm in ARMS:
    for mode in ("t1", "portfolio"):
        sub = [r for _, r in runs if r["arm"] == arm and r["mode"] == mode and r["agreement"]]
        if not sub:
            continue
        tk = [r["agreement"]["time_kept"] / r["agreement"]["total"]
              for r in sub if r["agreement"]["total"]]
        mk = [r["agreement"]["mower_kept"] / r["agreement"]["mower_total"]
              for r in sub if r["agreement"]["mower_total"]]
        full = sum(1 for x in tk if x == 1.0)
        print(f"{arm:>14} {mode:>9} {str(med(tk)):>14} {str(med(mk)):>15} {full:>4}/{len(tk)}")

# ------------------------------------------------------------- Q3: does the heuristic fire?
print("\n=== Q3  domain_choices ===")
print("  the guard is a t1 reading: at one thread the non-heuristic arms are a hard 0, so")
print("  non-zero == the overlay fired. Under --configuration=many the portfolio supplies")
print("  its own domain heuristic, so cold is non-zero there too (ADR-0033 decision 3).")
print(f"{'arm':>14} {'mode':>10} {'runs':>5} {'nonzero':>9} {'median':>12} {'max':>12}")
for arm in ARMS:
    for mode in ("t1", "portfolio"):
        sub = [r for _, r in runs if r["arm"] == arm and r["mode"] == mode]
        if not sub:
            continue
        dc = [r["stats"].get("domain_choices") for r in sub]
        nz = sum(1 for x in dc if x)
        print(f"{arm:>14} {mode:>10} {len(sub):>5} {nz:>5}/{len(dc):<3} {str(med(dc)):>12} "
              f"{str(max((x for x in dc if x is not None), default=None)):>12}")

# ------------------------------------- Q4 (freeze-only form): do the two weak levels separate?
print("\n=== Q4  weak@top vs weak@tiebreak (search dynamics, freeze-only) ===")
for metric in ("solve_time_s", "proof_time_s", "first_incumbent_s"):
    line = f"  {metric:>18}: "
    for arm in ("cold", "weak@top", "weak@tiebreak", "weak-graded"):
        sub = [r for _, r in runs if r["arm"] == arm]
        line += f"{arm}={med([r[metric] for r in sub])}  "
    print(line)
for stat in ("choices", "conflicts"):
    line = f"  {stat:>18}: "
    for arm in ("cold", "weak@top", "weak@tiebreak", "weak-graded"):
        sub = [r for _, r in runs if r["arm"] == arm]
        line += f"{arm}={med([r['stats'].get(stat) for r in sub])}  "
    print(line)

# paired, per cell — the only fair comparison
print("\n  paired per (scenario, edit_set, mode, replicate), top - tiebreak:")
pairs = defaultdict(dict)
for sid, run in runs:
    key = (sid, run["edit_set"], run["mode"], run["replicate"])
    pairs[key][run["arm"]] = run
deltas = defaultdict(list)
for key, d in pairs.items():
    if "weak@top" in d and "weak@tiebreak" in d:
        for m in ("solve_time_s", "plan_distance"):
            a, b = d["weak@top"][m], d["weak@tiebreak"][m]
            if a is not None and b is not None:
                deltas[m].append(a - b)
        pa = triples(d["weak@top"]["incumbents"][-1]["tasks"]) if d["weak@top"]["incumbents"] else None
        pb = (triples(d["weak@tiebreak"]["incumbents"][-1]["tasks"])
              if d["weak@tiebreak"]["incumbents"] else None)
        deltas["same_plan"].append(pa == pb)
for m in ("solve_time_s", "plan_distance"):
    print(f"    Δ{m}: median={med(deltas[m])} n={len(deltas[m])} "
          f"n_nonzero={sum(1 for x in deltas[m] if abs(x) > 1e-9)}")
print(f"    identical final plan: {sum(deltas['same_plan'])}/{len(deltas['same_plan'])}")

# ------------------------------------------------------------ Q5: is plan_distance useful?
print("\n=== Q5  plan_distance ===")
print(f"{'arm':>14} {'mode':>9} {'n':>4} {'median':>8} {'IQR':>7} {'min':>7} {'max':>7} "
      f"{'n=0':>6} {'n=1':>5}")
for arm in ARMS:
    for mode in ("t1", "portfolio"):
        pd = [r["plan_distance"] for _, r in runs
              if r["arm"] == arm and r["mode"] == mode and r["plan_distance"] is not None]
        if not pd:
            continue
        print(f"{arm:>14} {mode:>9} {len(pd):>4} {med(pd):>8} {str(iqr(pd)):>7} "
              f"{min(pd):>7.3f} {max(pd):>7.3f} {sum(1 for x in pd if x == 0):>6} "
              f"{sum(1 for x in pd if x == 1):>5}")

print("\n  service quality (fixed 5-slot score), median per arm:")
for arm in ARMS:
    sub = [r for _, r in runs if r["arm"] == arm and r["score"]]
    if sub:
        cols = list(zip(*[r["score"] for r in sub]))
        print(f"    {arm:>14} {[med(list(c)) for c in cols]}")

# ============================================================ mechanism verdict
# The pilot was scoped as a go/no-go gate, but N=6 x 7 arms x 4 runs may already be enough
# to see which mechanism produces stable schedules. Everything here is *paired*: arms are
# compared only within the same (scenario, edit_set, mode) group, with replicates
# collapsed to a median first, so portfolio nondeterminism (ADR-0007) cannot masquerade as
# an arm effect.
print("\n\n=== MECHANISM VERDICT (paired against cold, within-cell) ===")

groups = defaultdict(lambda: defaultdict(list))
for sid, run in runs:
    groups[(sid, run["edit_set"], run["mode"])][run["arm"]].append(run)


def _agree(rs):
    v = [r["agreement"]["time_kept"] / r["agreement"]["total"]
         for r in rs if r["agreement"].get("total")]
    return med(v)


def _mower(rs):
    v = [r["agreement"]["mower_kept"] / r["agreement"]["mower_total"]
         for r in rs if r["agreement"].get("mower_total")]
    return med(v)


def _lex(a, b):
    """Lexicographic compare of two fixed 5-slot score vectors: -1 if a is better."""
    if a is None or b is None:
        return None
    return -1 if a < b else (1 if a > b else 0)


for mode in ("t1", "portfolio"):
    print(f"\n  --- {mode} ---")
    if mode == "t1":
        print("  CAUTION: cold@t1 is the reference plan itself — same instance, same config,")
        print("  deterministic — so it satisfies every frozen preference by construction and")
        print("  scores agreement 1.0. Δagree/Δmower are therefore <= 0 here as an artifact,")
        print("  not a verdict. The informative t1 columns are Δdist and time ratio; read")
        print("  agreement off the portfolio rows, where cold is a genuine competitor.")
    print(f"{'arm':>14} {'n':>3} {'Δagree':>8} {'Δmower':>8} {'Δdist':>8} "
          f"{'quality better/eq/worse':>24} {'time ratio':>11}")
    for arm in ARMS:
        if arm == "cold":
            continue
        d_ag, d_mw, d_pd, ratio, qual = [], [], [], [], []
        for (sid, es, md), byarm in groups.items():
            if md != mode or arm not in byarm or "cold" not in byarm:
                continue
            a, c = byarm[arm], byarm["cold"]
            for fn, acc in ((_agree, d_ag), (_mower, d_mw)):
                va, vc = fn(a), fn(c)
                if va is not None and vc is not None:
                    acc.append(va - vc)
            pa = med([r["plan_distance"] for r in a])
            pc = med([r["plan_distance"] for r in c])
            if pa is not None and pc is not None:
                d_pd.append(pa - pc)
            ta = med([r["solve_time_s"] for r in a])
            tc = med([r["solve_time_s"] for r in c])
            if ta and tc:
                ratio.append(ta / tc)
            # quality: compare the best (lexicographically smallest) score in each arm
            sa = sorted([r["score"] for r in a if r["score"]])
            sc_ = sorted([r["score"] for r in c if r["score"]])
            if sa and sc_:
                qual.append(_lex(sa[0], sc_[0]))
        if not d_ag:
            continue
        better = sum(1 for q in qual if q == -1)
        equal = sum(1 for q in qual if q == 0)
        worse = sum(1 for q in qual if q == 1)
        print(f"{arm:>14} {len(d_ag):>3} {str(med(d_ag)):>8} {str(med(d_mw)):>8} "
              f"{str(med(d_pd)):>8} {f'{better}/{equal}/{worse}':>24} {str(med(ratio)):>11}")

print("""
  Reading it: Δagree/Δmower are the gain in kept preferences over cold (higher is better,
  the point of the mechanism). Δdist is churn in the *unnamed* remainder against the same
  anchor (negative = the mechanism also settles what the user did not name; positive = it
  buys named stability by rearranging everything else, which is the failure mode worth
  catching). Quality is the fixed 5-slot vector compared lexicographically against cold's
  best (ADR-0033 decision 3) — 'worse' is the price paid for stability. time ratio > 1
  means the mechanism costs search time.

  What N=6 can support: a direction and a rough magnitude per mechanism, and a clear
  negative (an arm that never helps, or always costs quality). What it cannot: a ranking
  between arms that land close together, any claim conditioned on a covariate (load, size),
  or the freeze-fraction trend — only f=0.3 was run.""")
