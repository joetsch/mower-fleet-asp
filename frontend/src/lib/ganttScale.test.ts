import { describe, expect, it } from "vitest";

import {
  GANTT_LEFT,
  GANTT_PLOT_W,
  GANTT_WIDTH,
  type GanttScale,
  clampStart,
  hourToX,
  hoursForPixels,
  snapHour,
} from "./ganttScale";

// A full-week domain — the shape every curated scenario has (168 h horizon, t=0 at the
// left edge). 1008 plot units / 168 h = exactly 6 user units per hour.
const WEEK: GanttScale = { xMin: 0, xMax: 168 };

describe("hourToX", () => {
  it("puts t=0 at the left edge of the plot and the horizon at the right edge", () => {
    expect(hourToX(WEEK, 0)).toBe(GANTT_LEFT);
    expect(hourToX(WEEK, 168)).toBe(GANTT_LEFT + GANTT_PLOT_W);
  });

  it("is linear at 6 user units per hour for a full week", () => {
    expect(hourToX(WEEK, 1) - hourToX(WEEK, 0)).toBeCloseTo(6);
    expect(hourToX(WEEK, 84)).toBe(GANTT_LEFT + GANTT_PLOT_W / 2);
  });

  it("handles a domain that starts before t=0 (run-history shown)", () => {
    const rolled: GanttScale = { xMin: -48, xMax: 168 };
    expect(hourToX(rolled, -48)).toBe(GANTT_LEFT);
    expect(hourToX(rolled, 168)).toBe(GANTT_LEFT + GANTT_PLOT_W);
  });
});

describe("hoursForPixels", () => {
  it("round-trips against hourToX when the SVG renders at its own viewBox width", () => {
    // renderedW == GANTT_WIDTH -> 1 CSS px is 1 user unit, so the only conversion left is
    // the plot fit: 6 user units == 1 hour.
    expect(hoursForPixels(WEEK, 6, GANTT_WIDTH)).toBeCloseTo(1);
    expect(hoursForPixels(WEEK, 60, GANTT_WIDTH)).toBeCloseTo(10);
    // The inverse of a known forward step: bar at hour 20 is 120 user units from hour 0.
    const dx = hourToX(WEEK, 20) - hourToX(WEEK, 0);
    expect(hoursForPixels(WEEK, dx, GANTT_WIDTH)).toBeCloseTo(20);
  });

  it("accounts for the viewBox being stretched to a wider rendered width", () => {
    // Rendered at double the viewBox width: 1 CSS px is half a user unit, so a pixel buys
    // half the hours it would at 1:1.
    expect(hoursForPixels(WEEK, 6, GANTT_WIDTH * 2)).toBeCloseTo(0.5);
  });

  it("accounts for a squeezed rendered width", () => {
    expect(hoursForPixels(WEEK, 6, GANTT_WIDTH / 2)).toBeCloseTo(2);
  });

  it("is coarser once the domain is wider than a week (post-roll)", () => {
    // xMax 336 -> 3 user units per hour, so a 6px move at 1:1 is now 2 hours.
    expect(hoursForPixels({ xMin: 0, xMax: 336 }, 6, GANTT_WIDTH)).toBeCloseTo(2);
  });

  it("returns 0 for a non-positive rendered width rather than dividing by zero", () => {
    expect(hoursForPixels(WEEK, 40, 0)).toBe(0);
  });

  it("is sign-preserving — a leftward move is negative hours", () => {
    expect(hoursForPixels(WEEK, -30, GANTT_WIDTH)).toBeCloseTo(-5);
  });
});

describe("snapHour", () => {
  it("rounds to the nearest whole hour", () => {
    expect(snapHour(2.2)).toBe(2);
    expect(snapHour(2.5)).toBe(3);
    expect(snapHour(-1.5)).toBe(-1); // Math.round rounds half toward +∞
  });
});

describe("clampStart", () => {
  it("keeps a start within [0, horizon - 1]", () => {
    expect(clampStart(50, 168)).toBe(50);
    expect(clampStart(-5, 168)).toBe(0);
    expect(clampStart(200, 168)).toBe(167);
    expect(clampStart(168, 168)).toBe(167);
  });
});
