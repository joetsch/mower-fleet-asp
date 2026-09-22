// The replan undo stack (roadmap Iteration 5 / ADR-0035; extended for the moving horizon,
// ADR-0042).
//
// A greenkeeper who replans and dislikes the result must be able to get the old plan
// back — without it, editing the schedule is a one-way door and the feature is scary to
// use. This hook is that stack, and nothing else: it stores whole snapshots and hands the
// previous one back on `undo()`.
//
// A snapshot is `{ result, scenario, planTasks }`. For an ordinary replan `scenario` is
// null — the requirements did not change, only the plan. For a **Move forward** step it
// also carries the pre-roll scenario, so Undo steps "now" back as well as the plan
// (ADR-0042). `planTasks` is the *working copy* of the plan as the user had it when the
// replan started — drag/mower edits and releases included — so Undo lands exactly where
// they were, not on the last plan the solver returned (UI review 2026-09-10). Without it,
// a Re-solve or Move forward that consumed un-re-solved edits could not be faithfully
// undone: `useScheduleEdits` rebuilds its working copy from whatever `result` it is handed.
//
// Why a stack rather than watching `result` change: anytime solving (ADR-0022) calls
// `setResult` on *every* 2 s poll with an improving best-so-far, so "the result changed"
// fires many times per solve. The caller therefore pushes explicitly, once, at the
// moment it replaces a plan — see `App.tsx`'s solve handler.

import { useCallback, useState } from "react";

import type { PlanTask } from "../lib/schedulePreferences";
import type { Scenario, SolveResult } from "../types";

/** A superseded plan, plus the scenario it was computed against when a roll changed it. */
export interface PlanSnapshot {
  result: SolveResult;
  /** The pre-roll scenario, or null when the requirements did not change (a plain replan). */
  scenario: Scenario | null;
  /** The working copy of the plan when the replan started — the user's un-re-solved
   *  drag/mower edits and releases, so Undo can restore them rather than reverting to the
   *  last solver output (UI review 2026-09-10). */
  planTasks: PlanTask[];
}

export interface PlanHistory {
  /** True when there is a superseded plan to go back to. */
  canUndo: boolean;
  /** Remember the plan currently on screen, because it is about to be replaced. */
  push: (snapshot: PlanSnapshot) => void;
  /** Pop the most recent snapshot, or null when the stack is empty. */
  undo: () => PlanSnapshot | null;
  /** Drop the whole stack — the plans no longer describe the scenario on screen. */
  clear: () => void;
}

export function usePlanHistory(): PlanHistory {
  const [stack, setStack] = useState<PlanSnapshot[]>([]);

  const push = useCallback((snapshot: PlanSnapshot) => setStack((s) => [...s, snapshot]), []);

  // Returns the popped snapshot rather than applying it: the schedule lives in
  // `useSolveJob` and the scenario in `useScenarioDraft`, and having the history reach
  // into either hook would tie them together for no gain. Reads `stack` (this render's
  // value) rather than the value inside a `setStack` updater — updaters run during the
  // *next* render, so a `popped` captured in one is still null by the time this returns.
  const undo = useCallback(() => {
    if (stack.length === 0) return null;
    setStack((s) => s.slice(0, -1));
    return stack[stack.length - 1];
  }, [stack]);

  const clear = useCallback(() => setStack([]), []);

  return { canUndo: stack.length > 0, push, undo, clear };
}
