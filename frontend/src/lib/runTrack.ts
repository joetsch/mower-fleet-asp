// How much of the moving horizon's executed track the Gantt draws (ADR-0042).
//
// `useRunLog.executed` grows by one roll's worth of tasks at every step and never shrinks
// — that is the record the run log exists to keep. The chart is a different question: its
// `xMin` is the earliest thing it draws, so an uncapped track pushes the left edge back
// 24 h per roll and the *plan* — the part the user is reading — ends up with an
// ever-smaller share of the width (58% after five daily rolls, 41% after ten, with no
// limit).
//
// So the cap is applied at render time only. Nothing is discarded: the run log, its
// metrics table and the offline study still see every roll.

import type { ScheduledTask } from "../types";

/** How far back the chart draws executed work, in hours.
 *
 *  One week, because that is the model's own period — availability wraps `mod 168` — so
 *  "a week behind, a week ahead" is the production picture rather than an arbitrary
 *  cut-off. The span reaches that steady state after seven daily rolls and never grows
 *  again. */
export const RUN_TRACK_WINDOW_H = 168;

/** The executed tasks the Gantt should draw: those that started within the window.
 *
 *  Filtering on `start` (not `end`) is what makes the bound hold — it guarantees `xMin`
 *  is never earlier than `-RUN_TRACK_WINDOW_H`, so the plot cannot widen further. The
 *  cost is that a service which *began* just outside the window and ran into it is not
 *  drawn; at the far-left edge of a week-long history that is not worth a clipped bar. */
export function visibleRunTrack(
  executed: ScheduledTask[],
  windowH: number = RUN_TRACK_WINDOW_H,
): ScheduledTask[] {
  return executed.filter((t) => t.start >= -windowH);
}
