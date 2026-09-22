// The working copy of the plan on screen (ADR-0035).
//
// Deliberately *not* part of `useScenarioDraft`: preferences are per-solve, never part of
// a `Scenario` (ADR-0031 decision 3). A `Scenario` is persisted verbatim into
// `scenarios/curated/*.json`, and `scenariosEqual` / `dirty` / the frontend↔backend
// mirror goldens all assume the draft is the only thing that varies. Schedule edits ride
// on `SolveRequest`, so they live in their own hook.
//
// Every task gets a `uid` synthesized here. `ScheduledTask.task` is NOT an identity — it
// is a rank within the area, and the encoding is free to renumber on the next solve
// (ADR-0031). Nothing in this layer may key off it.

import { useRef, useState } from "react";

import { FALLBACK_DURATION_H, defaultDurationHours } from "../lib/scenarioDefaults";
import type { PlanTask, TaskPinState } from "../lib/schedulePreferences";
import type { Scenario, SolveResult } from "../types";

export interface ScheduleEdits {
  /** The plan as the user currently wants it — the source of the re-solve payload. */
  tasks: PlanTask[];
  /** True once the user has changed, added, pinned or released anything. */
  dirty: boolean;
  /** Throw the edit layer away and go back to the solved plan as returned. */
  reset: () => void;
  /** Put a working copy captured earlier back in place — the replan Undo (ADR-0035, UI
   *  review 2026-09-10). `forResult` is the plan that copy belongs to; the caller swaps
   *  that same result back on screen in the same click, so the `source` guard below sees
   *  them already matched and does not rebuild over the restored tasks. */
  restore: (forResult: SolveResult | null, tasks: PlanTask[]) => void;
  /** Move a task to a different start hour, keeping its duration. */
  moveTask: (uid: string, start: number) => void;
  /** Put a task on a different mower, recomputing its duration from the scenario. */
  reassign: (uid: string, mower: string, scenario: Scenario) => void;
  /** Keep a task in the next re-solve's payload (`auto`) or release it (`released`). */
  setPin: (uid: string, pin: TaskPinState) => void;
  /** Set every task's keep state at once — the "release all" / "keep all" toggle. */
  setAllPins: (pin: TaskPinState) => void;
  /** Ask for a service that was not in the solved plan (`origin: "added"`). */
  addTask: (area: string, start: number, mower: string, scenario: Scenario) => void;
}

/** Hours this mower needs for this area — the scenario's own `base_durations` when it has
 *  the pair, else the same size/rate default the scenario editor fills in (ADR-0024).
 *  Only ever a *preview*: the solver recomputes the real completion from its own table. */
function durationFor(scenario: Scenario, area: string, mower: string): number {
  const given = scenario.base_durations?.find((d) => d.area === area && d.mower === mower);
  if (given) return given.hours;
  const size = scenario.areas.find((a) => a.name === area)?.size_m2;
  const rate = scenario.mowers.find((m) => m.name === mower)?.area_capacity_m2_per_day;
  if (size == null || rate == null) return FALLBACK_DURATION_H;
  return defaultDurationHours(size, rate);
}

function fromResult(result: SolveResult | null): PlanTask[] {
  const tasks = result?.schedule?.tasks ?? [];
  return tasks.map((t, i) => ({
    uid: `p${i}`,
    area: t.area,
    mower: t.mower,
    start: t.start,
    end: t.end,
    pin: "auto" as const,
    edited: false,
    added: false,
    sourceTask: t.task,
  }));
}

export function useScheduleEdits(result: SolveResult | null): ScheduleEdits {
  const [tasks, setTasks] = useState<PlanTask[]>(() => fromResult(result));
  // Added tasks need uids that cannot collide with the `p<n>` ones `fromResult` mints,
  // nor with each other. A ref, not state: two `addTask` calls in the same tick would
  // both read a state counter's pre-update value and mint the same uid twice. Uids are
  // client-side identity for one plan — never persisted, never sent to the solver.
  const nextAdded = useRef(0);
  // React's documented way to reset state when a prop changes: remember which `result`
  // the working copy was built from, and rebuild *during render* when a different one
  // arrives. Doing it in an effect instead would render once with the stale plan, commit
  // it, then immediately render again — a visible flash of the previous week's bars, and
  // what the `react(set-state-in-effect)` lint is warning about. Setting state during
  // render is fine as long as it is guarded like this: React re-runs the component
  // immediately, before touching the DOM.
  const [source, setSource] = useState<SolveResult | null>(result);
  if (source !== result) {
    setSource(result);
    setTasks(fromResult(result));
  }

  // Anytime solving (ADR-0022) delivers a fresh `result` object on every poll, so the
  // working copy is rebuilt repeatedly *during* a solve. That is correct: each
  // best-so-far is a different plan, and the schedule editor is closed while solving, so
  // there is never a user edit to lose here.
  const dirty = tasks.some((t) => t.edited || t.added || t.pin !== "auto");

  const patch = (uid: string, change: (t: PlanTask) => PlanTask) =>
    setTasks((current) => current.map((t) => (t.uid === uid ? change(t) : t)));

  return {
    tasks,
    dirty,
    reset: () => setTasks(fromResult(result)),
    restore: (forResult, restored) => {
      // Set both in one batch with the caller's result swap: on the next render
      // `source === result` already holds, so the guard above leaves `restored` be.
      setSource(forResult);
      setTasks(restored);
    },
    moveTask: (uid, start) =>
      patch(uid, (t) => ({ ...t, start, end: start + (t.end - t.start), edited: true })),
    reassign: (uid, mower, scenario) =>
      patch(uid, (t) => ({
        ...t,
        mower,
        end: t.start + durationFor(scenario, t.area, mower),
        edited: true,
      })),
    // Pinning is not an edit: the user has not asked for anything to *change*, only for
    // this task to be in (or out of) the payload. Keeping the two separate is what lets
    // the banner say "all 3 of your edits kept" without counting pins as edits.
    setPin: (uid, pin) => patch(uid, (t) => ({ ...t, pin })),
    setAllPins: (pin) => setTasks((current) => current.map((t) => ({ ...t, pin }))),
    addTask: (area, start, mower, scenario) => {
      // Minted outside the updater, so the updater stays a pure function of `current`.
      const uid = `a${nextAdded.current++}`;
      setTasks((current) => [
        ...current,
        {
          uid,
          area,
          mower,
          start,
          end: start + durationFor(scenario, area, mower),
          pin: "auto",
          edited: false,
          added: true,
          sourceTask: null,
        },
      ]);
    },
  };
}
