import { describe, expect, it } from "vitest";

import { RUN_TRACK_WINDOW_H, visibleRunTrack } from "./runTrack";

import type { ScheduledTask } from "../types";

const past = (start: number, end: number): ScheduledTask => ({
  area: "A1",
  task: 1,
  mower: "M1",
  start,
  end,
});

/**
 * The executed track accumulates one roll's worth of tasks per step, and the Gantt's
 * `xMin` is the earliest thing it draws — so an uncapped track pushes the left edge back
 * 24 h per roll and squeezes the plan itself into an ever-smaller share of the width
 * (58% after five daily rolls, 41% after ten). Capping what is *drawn* — the run log
 * still stores everything — makes the span stop growing after one week.
 */
describe("visibleRunTrack", () => {
  it("keeps a track shorter than the window whole", () => {
    const track = [past(-40, -30), past(-12, -4)];
    expect(visibleRunTrack(track)).toEqual(track);
  });

  it("drops executed tasks older than one week, so the span stops growing", () => {
    const track = [past(-300, -290), past(-200, -190), past(-100, -90), past(-10, -2)];
    expect(visibleRunTrack(track).map((t) => t.start)).toEqual([-100, -10]);
  });

  it("bounds the earliest drawn hour at exactly one week back", () => {
    // Ten daily rolls: uncapped this reaches -240 and never stops.
    const track = Array.from({ length: 10 }, (_, i) => past(-24 * (i + 1), -24 * (i + 1) + 6));
    const earliest = Math.min(...visibleRunTrack(track).map((t) => t.start));
    expect(earliest).toBeGreaterThanOrEqual(-RUN_TRACK_WINDOW_H);
  });

  it("is empty for an empty track", () => {
    expect(visibleRunTrack([])).toEqual([]);
  });
});
