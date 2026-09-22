// The Gantt chart's horizontal scale, both directions.
//
// `GanttSchedule` draws an inline <svg> with a fixed `viewBox="0 0 1180 …"` that CSS then
// stretches to whatever width the column is (`width: 100%; min-width: 900px`). So there
// are two conversions between a screen pixel and an hour offset:
//
//   screen px ──(viewBox stretch)──> user unit ──(plot fit)──> hour
//
// The forward direction (`hourToX`) is what the chart has always used to place bars; the
// inverse is what a drag needs. Keeping both here, as plain-number functions, means they
// cannot drift apart and they are testable with no DOM (jsdom gives zeros from
// `getBoundingClientRect()` and has no `getScreenCTM()`, ADR-0025 keeps `lib/` pure).
//
// This is deliberately *not* folded into `lib/strip.ts`: that is an integer pixel-grid
// geometry (`cellW = max(3, floor(760 / span))`) for the scenario-tab hover strips — a
// different construction from the Gantt's continuous fit into a fixed plot width.

/** viewBox width — the SVG's own coordinate system, independent of rendered size. */
export const GANTT_WIDTH = 1180;
/** Left margin (row labels) and right margin, in user units. */
export const GANTT_LEFT = 156;
export const GANTT_RIGHT = 16;
/** Width of the plot area proper, in user units. */
export const GANTT_PLOT_W = GANTT_WIDTH - GANTT_LEFT - GANTT_RIGHT;

/** The horizontal domain of one render: hour offsets from t=0 at the left and right edges
 *  of the plot area. `xMin` can be negative once a run-history track is shown. */
export interface GanttScale {
  xMin: number;
  xMax: number;
}

/** X user-coordinate for an hour offset — the function the chart places every bar with. */
export function hourToX(s: GanttScale, hour: number): number {
  return GANTT_LEFT + ((hour - s.xMin) / (s.xMax - s.xMin)) * GANTT_PLOT_W;
}

/** Hours travelled for a horizontal pointer move of `dxClient` CSS pixels, given the SVG
 *  element's current rendered width in CSS pixels (`el.getBoundingClientRect().width`).
 *
 *  Delta-based on purpose: it depends only on how far the pointer moved, not on where
 *  inside the bar it grabbed, so there is no anchor offset to correct for. */
export function hoursForPixels(s: GanttScale, dxClient: number, renderedW: number): number {
  if (renderedW <= 0) return 0;
  const userUnitsPerPixel = GANTT_WIDTH / renderedW;
  const userUnitsPerHour = GANTT_PLOT_W / (s.xMax - s.xMin);
  return (dxClient * userUnitsPerPixel) / userUnitsPerHour;
}

/** Snap a fractional hour to the nearest whole hour — the drag lands on hour boundaries,
 *  the same granularity the popover's number field commits at. */
export function snapHour(hour: number): number {
  return Math.round(hour);
}

/** Keep a dragged start within the week: `[0, horizonHours - 1]`. A *completion* past the
 *  horizon is legal and by design, but a start before "now" or beyond the last hour is
 *  not something the plot can show. The popover's number field stays unclamped as the
 *  escape hatch for anything unusual. */
export function clampStart(hour: number, horizonHours: number): number {
  return Math.max(0, Math.min(horizonHours - 1, hour));
}
