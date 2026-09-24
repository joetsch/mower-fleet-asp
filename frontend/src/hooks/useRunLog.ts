// The run log for the moving time horizon (ADR-0042).
//
// Each "Move forward" step folds the elapsed part of the plan into history and re-solves
// the week ahead. This hook keeps the *cumulative* record of that loop, so the demo can
// show — not just tell — how the schedule holds up across replans:
//
//   - `executed`: every now-past task from every roll so far, in offsets relative to the
//     *current* now (so all negative). Rebased by `-hours` on each subsequent roll, and
//     drawn behind the now-line on the Gantt.
//   - `rolls`: one entry per step — how far it moved, how much of the carried plan
//     survived, the quality vector and solve time. `outcome` is filled in when that
//     step's re-solve settles (it runs asynchronously, ADR-0022).
//
// Not persisted and never part of a `Scenario` — like the schedule-edit layer, it is a
// property of this session's exploration, not of the problem. Cleared when the scenario
// changes underneath it (switch / revert / new / delete). A snapshot stack backs `undo()`
// so "Undo" steps the run log back in lock-step with the plan and the scenario.

import { useCallback, useState } from "react";

import type { ScheduledTask } from "../types";
import type { StabilitySetting } from "../lib/schedulePreferences";

/** What a roll's re-solve produced — null until it settles. */
export interface RollOutcome {
  services: number;
  /** Carried tasks whose hour survived the re-solve (`agreement.by_origin.frozen`). */
  kept: number;
  carried: number;
  quality: number[] | null;
  solveTimeS: number;
  optimal: boolean;
  /** The stability setting this roll's re-solve actually ran at, read off its
   *  `PreferenceReport` rather than off the selector — the user may change the setting
   *  between rolls, and without this the kept/carried and quality columns would be
   *  compared across arms with nothing saying so.
   *
   *  Null means the roll carried nothing at all (no report came back). `"heuristic"` is
   *  the mechanism whose `PreferenceReport.level` is null *because it puts nothing in the
   *  objective* — a different thing from carrying nothing, and reporting it as "cold"
   *  would claim the plan was ignored when it was not. */
  setting: StabilitySetting | null;
}

export interface RollEntry {
  /** How far this step advanced "now", in hours. */
  hours: number;
  /** Weekday + clock of the new now, e.g. "Tue 13:00". */
  nowLabel: string;
  outcome: RollOutcome | null;
  /** The re-solve ended without producing a plan — stopped, or it failed. Distinct from
   *  `outcome === null`, which means it is still running. The roll itself stands: "now"
   *  moved and the past was folded into history; only the new plan is missing. */
  failed: boolean;
}

interface Snapshot {
  executed: ScheduledTask[];
  rolls: RollEntry[];
}

export interface RunLog {
  executed: ScheduledTask[];
  rolls: RollEntry[];
  /** The most recent roll's re-solve has not settled yet. */
  pending: boolean;
  /** Record a step as it starts: shift the accumulated `executed` back by `hours`, append
   *  this step's now-past tasks (also rebased), and open a pending `rolls` entry. */
  begin: (args: {
    hours: number;
    nowLabel: string;
    executedThisRoll: ScheduledTask[];
  }) => void;
  /** Fill in the pending entry once its re-solve settles. */
  settle: (outcome: RollOutcome) => void;
  /** Mark the pending entry as having produced no plan (stopped, or failed). */
  abandon: () => void;
  /** Step the run log back one roll — pairs with "Undo". */
  undo: () => void;
  /** Drop the whole log — the scenario it describes is gone. */
  reset: () => void;
}

const rebase = (tasks: ScheduledTask[], hours: number): ScheduledTask[] =>
  tasks.map((t) => ({ ...t, start: t.start - hours, end: t.end - hours }));

export function useRunLog(): RunLog {
  const [executed, setExecuted] = useState<ScheduledTask[]>([]);
  const [rolls, setRolls] = useState<RollEntry[]>([]);
  // Undo stack — only ever written and popped, never read as a whole.
  const [, setStack] = useState<Snapshot[]>([]);

  const begin = useCallback(
    ({
      hours,
      nowLabel,
      executedThisRoll,
    }: {
      hours: number;
      nowLabel: string;
      executedThisRoll: ScheduledTask[];
    }) => {
      setStack((s) => [...s, { executed, rolls }]);
      setExecuted((prev) => [...rebase(prev, hours), ...rebase(executedThisRoll, hours)]);
      setRolls((prev) => [...prev, { hours, nowLabel, outcome: null, failed: false }]);
    },
    [executed, rolls],
  );

  const settle = useCallback((outcome: RollOutcome) => {
    setRolls((prev) =>
      prev.map((r, i) => (i === prev.length - 1 ? { ...r, outcome } : r)),
    );
  }, []);

  /** The re-solve ended with no plan of its own. Without this the pending row was filled
   *  from whatever `result` happened to hold — which, after a stop, is the plan from
   *  *before* the roll, so every figure in the row described the wrong week. */
  const abandon = useCallback(() => {
    setRolls((prev) =>
      prev.map((r, i) => (i === prev.length - 1 ? { ...r, failed: true } : r)),
    );
  }, []);

  const undo = useCallback(() => {
    setStack((s) => {
      const prev = s[s.length - 1];
      if (prev) {
        setExecuted(prev.executed);
        setRolls(prev.rolls);
      }
      return s.slice(0, -1);
    });
  }, []);

  const reset = useCallback(() => {
    setExecuted([]);
    setRolls([]);
    setStack([]);
  }, []);

  const last = rolls[rolls.length - 1];
  const pending = last !== undefined && last.outcome === null && !last.failed;

  return { executed, rolls, pending, begin, settle, abandon, undo, reset };
}
