import { describe, expect, it } from "vitest";

import { stripGeometry, stripX } from "./strip";

describe("stripGeometry", () => {
  it("covers a whole week from a Monday-06:00 start (Availability strip)", () => {
    const s = stripGeometry(0, 168, 6);
    expect(s.cellW).toBe(Math.max(3, Math.floor(760 / 168)));
    expect(s.width).toBe(s.cellW * 168);
    expect(s.nowX).toBe(0);
    // First midnight 18h in (Tuesday), then every 24h.
    expect(s.midnights).toEqual([18, 42, 66, 90, 114, 138, 162]);
    expect(s.daySegments).toHaveLength(8);
    expect(s.daySegments[0]).toEqual({ from: 0, to: 18, label: "Mon" });
    expect(s.daySegments[1]).toEqual({ from: 18, to: 42, label: "Tue" });
    expect(s.daySegments[7]).toEqual({ from: 162, to: 168, label: "Mon" });
  });

  it("preserves the leading zero-width segment when t=0 is itself a midnight", () => {
    // firstMidnightAtOrAfter(0, 0) === 0, so offset 0 is in `midnights` and the first
    // day segment collapses — matches the pre-extraction behaviour verbatim.
    const s = stripGeometry(0, 48, 0);
    expect(s.midnights).toEqual([0, 24]);
    expect(s.daySegments[0]).toEqual({ from: 0, to: 0, label: "Mon" });
    expect(s.daySegments[1]).toEqual({ from: 0, to: 24, label: "Mon" });
  });

  it("offsets the midnight grid when t=0 is not itself a midnight", () => {
    // t=0 is Monday 06:00 -> first midnight 18h later, Tuesday.
    const s = stripGeometry(0, 48, 6);
    expect(s.midnights).toEqual([18, 42]);
    expect(s.daySegments[0]).toEqual({ from: 0, to: 18, label: "Mon" });
    expect(s.daySegments[1]).toEqual({ from: 18, to: 42, label: "Tue" });
  });

  it("handles a domain that starts before t=0 (History strip)", () => {
    const s = stripGeometry(-30, 6, 0);
    expect(s.width).toBe(s.cellW * 36);
    expect(s.nowX).toBe(30 * s.cellW);
    // Midnights at -24 and 0 (0 is < end = 6, so it is included).
    expect(s.midnights).toEqual([-24, 0]);
    expect(s.daySegments[0].from).toBe(-30);
    expect(s.daySegments[s.daySegments.length - 1].to).toBe(6);
  });

  it("never divides by zero on a degenerate domain", () => {
    const s = stripGeometry(0, 0, 0);
    expect(s.cellW).toBeGreaterThan(0);
    expect(Number.isFinite(s.width)).toBe(true);
  });
});

describe("stripX", () => {
  it("translates an hour-offset into the strip's pixel space", () => {
    const s = stripGeometry(-30, 6, 0);
    expect(stripX(s, -30)).toBe(0);
    expect(stripX(s, 0)).toBe(30 * s.cellW);
    expect(stripX(s, 6)).toBe(s.width);
  });
});
