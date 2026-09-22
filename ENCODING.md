# How the solver works

The scheduling semantics live in six small clingcon programs under
[`src/fleetplanning/solver/encodings/`](src/fleetplanning/solver/encodings/). Python does
everything calendar-shaped beforehand and hands the solver a table of feasible
`start → completion` pairs; the encodings only *choose*.

```
Scenario ──► completion table + service-count bounds   (Python: solver/completion_table.py, bounds.py)
         ──► instance facts                            (solver/instance.py)
         ──► clingcon: forward model [+ one preference overlay]   (solver/runner.py)
         ──► Schedule                                  (solver/parse.py)
```

`service.py` orchestrates the pipeline. The default solver configuration is the
multi-threaded portfolio `-t4 --configuration=many`; a single-threaded
`--configuration=jumpy` run is the reproducible path the tests use.

## Input facts

| predicate | meaning |
|---|---|
| `area/1`, `mower/1` | the areas and the fleet |
| `priority/2` | area priority, 1 (highest) … 3 |
| `min_interval/2`, `max_interval/2` | allowed hours between two service starts of an area |
| `capable/2` | `capable(M,A)`: mower `M` can service area `A` |
| `last/4` | `last(A,M,S,C)`: the most recent service of `A` (history), by `M`, from `S` to `C` |
| `min_starts/2`, `max_starts/2` | bounds on the number of services of `A` in the horizon |
| `completion/5` | `completion(A,M,S,C,AH)`: if `M` starts `A` at hour `S` it finishes at `C`, with `AH` worked hours inside *avoid* windows |

`completion/5` is where the calendar goes: a mower pauses during an area's **no-go** hours
and resumes afterwards, so `C − S` is not a constant; *avoid* hours are allowed but
counted. Computing this table in Python keeps the encoding free of calendar arithmetic.

## `golf_schedule_forward_model.lp` — the active model

- **Choice:** for each area a number of services within `[min_starts, max_starts]`; for
  each resulting task one capable mower.
- **Integer variables:** each task's `start` and `completion` are clingcon variables whose
  domains (`&dom`) are exactly the start/completion values in the precomputed table for
  the chosen mower.
- **Hard constraints:** consecutive tasks of an area are ordered; two tasks on the same
  mower never overlap; no task overlaps the mower's still-running historical task.
- **Soft constraints (weak constraints, lexicographic):**
  - level `5 − P` ∈ {4, 3, 2} — *max-interval* violations of a priority-`P` area,
    including a first service that comes too late after the history and a last service
    that leaves too long a gap before the end of the horizon;
  - level 1 — services that touch *avoid* hours;
  - level 0 — *min-interval* violations (services too close together), including a first
    service too early after the history.

  So: coverage of high-priority areas first, then lower priorities, then avoid-zone use,
  then spacing. The cost vector only has entries for the priority levels that occur in
  the instance.

The horizon is **not** circular: the first task is checked against the service history,
the last against the horizon end, which is what lets the plan be re-solved on a moving
horizon (`rolling.py`: slide the window by a day, fold the past into the history, re-plan).

## `golf_schedule_wrap_around_model.lp` — dormant

The earlier variant, treating the week as circular (a steady-state schedule, no history).
Consumes the same facts; kept for reference, not used by the app.

## Preference overlays — re-planning after a manual edit

When the user edits a solved schedule (moves a service, changes its mower, adds one) and
re-plans, the edits become facts `pref_time(A,S)` ("area `A` should have a service starting
at `S`") and `pref_mower(A,S,M)` ("…on mower `M`"), emitted by `solver/preferences.py`.
Hour and mower are separate facts so that keeping the hour but not the mower is a partial
success. No task index appears: a preference is met if *any* service of that area is
there, which keeps the preference layer independent of how tasks get numbered. Exactly one
overlay is added to the forward model:

- **`preferences_weak.lp`** (default) — one weak constraint per unmet preference at level
  `pref_level`: `6` puts honouring the edits above every service-quality level, `-1` makes
  them a tie-break among equally good schedules.
- **`preferences_graded.lp`** — the same, but an unmet time preference costs its distance
  (in hours) to the nearest service of that area.
- **`preferences_heuristic.lp`** / **`preferences_heuristic_soft.lp`** — domain heuristics
  (`--heuristic=Domain`) that steer the search toward the edits without changing the
  optimisation problem at all (`true` vs. `init` + `sign` modifiers).

## Explanations — "why wasn't my edit kept?"

`explain/` answers this for each edit a re-plan dropped, cheapest mechanism first:

1. a static overlap check against the plan on screen (no solver call);
2. an **assumption-based counterfactual** over the real forward model: the dropped edit's
   `pref_time_met` / `pref_mower_met` atom is *assumed true*, and the solver is asked
   whether any schedule satisfies the hard constraints under that assumption
   (`--opt-mode=ignore`: the weak constraints are irrelevant to that question). If none
   does, the unsatisfiable core over the assumptions, minimised, names the requirements
   the edit conflicts with.

Two things keep this cheap. Kept preferences sharing a mower or an area with the edit
are assumed first, which bounds core minimisation by local density rather than plan size.
An unsatisfiable answer over that local set is a genuine conflict; a satisfiable one is
confirmed against *every* kept preference, because a conflict can run through a service
that no preference pins (pinning the edit can push one of its area's other services onto
a different mower, where it collides with a kept preference sharing nothing with the
edit). And one `Control` is grounded per request, after which every
dropped edit is a handful of assumption-solves on it (multi-shot solving), run with the
deterministic configuration because the portfolio's raw cores vary between runs.

No `.lp` file is modified for this: the atoms it assumes are ones `preferences_weak.lp`
already derives.
