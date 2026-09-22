// Shared geometry for the hover strips in ScenarioSummary (the Availability strip and the
// History strip). Both draw hour-offsets from t=0 onto a fixed ~760px-wide band with a
// midnight grid and per-day labels; only their domain differs — Availability spans
// [0, horizon_hours], History a domain that starts before t=0.

import { firstMidnightAtOrAfter, weekdayAtMidnight } from "./schedule";

export interface DaySegment {
  /** Hour-offset the segment starts at (inclusive). */
  from: number;
  /** Hour-offset the segment ends at (exclusive). */
  to: number;
  label: string;
}

export interface Strip {
  /** Domain start, in hour-offsets from t=0 (0 for Availability, negative for History). */
  start: number;
  /** Domain end, in hour-offsets from t=0. */
  end: number;
  /** Pixel width of one hour. */
  cellW: number;
  /** Pixel width of the whole strip (`cellW * span`). */
  width: number;
  /** Midnight offsets falling inside the domain, for the vertical grid lines. */
  midnights: number[];
  /** Contiguous per-day segments covering `[start, end]`, for the day labels. */
  daySegments: DaySegment[];
  /** X pixel of t=0 within the strip (0 when the domain starts at t=0). */
  nowX: number;
}

/** Work out cell width, the midnight grid and the day-label segments for a strip whose
 * domain is `[start, end]` (hour-offsets from t=0), given which hour-of-week t=0 is. */
export function stripGeometry(start: number, end: number, horizonStartHour: number): Strip {
  const span = Math.max(end - start, 1);
  const cellW = Math.max(3, Math.floor(760 / span));
  const width = cellW * span;

  const midnights: number[] = [];
  for (let m = firstMidnightAtOrAfter(start, horizonStartHour); m < end; m += 24) {
    midnights.push(m);
  }

  const daySegments: DaySegment[] = [];
  let from = start;
  for (const m of midnights) {
    daySegments.push({ from, to: m, label: weekdayAtMidnight(from, horizonStartHour) });
    from = m;
  }
  daySegments.push({ from, to: end, label: weekdayAtMidnight(from, horizonStartHour) });

  return { start, end, cellW, width, midnights, daySegments, nowX: (0 - start) * cellW };
}

/** X pixel for an hour-offset within the strip. */
export function stripX(strip: Strip, offset: number): number {
  return (offset - strip.start) * strip.cellW;
}
