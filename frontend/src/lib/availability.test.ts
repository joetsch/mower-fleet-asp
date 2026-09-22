import { describe, expect, it } from "vitest";

import { applyWindowRows, availabilityBands, windowRows } from "./availability";
import { makeScenario } from "./fixtures";
import type { Area, DaySchedule, Scenario } from "../types";

const ALL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** The fixture's A1: `avoid [[8, 20]]` on all seven days. */
function uniformArea(): Area {
  return makeScenario().areas[0];
}

/** A1 with a genuinely per-weekday schedule: a weekday-only avoid, a Mon+Wed no-go. */
function nonUniformArea(): Area {
  const a = uniformArea();
  a.schedule.Saturday = { no_go: [], avoid: [] };
  a.schedule.Sunday = { no_go: [], avoid: [] };
  a.schedule.Monday.no_go = [[22, 6]];
  a.schedule.Wednesday.no_go = [[22, 6]];
  return a;
}

function withArea(area: Area): Scenario {
  const s = makeScenario();
  s.areas[0] = area;
  return s;
}

describe("windowRows", () => {
  it("gives a uniform area one all-seven-day row per interval", () => {
    const rows = windowRows(uniformArea());
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "avoid", start: 8, end: 20 });
    expect(rows[0].days).toEqual(ALL);
    expect(rows[0].at).toEqual(Object.fromEntries(ALL.map((d) => [d, 0])));
  });

  it("orders no_go rows before avoid rows (the order the editor has always shown)", () => {
    const a = uniformArea();
    a.schedule = Object.fromEntries(
      ALL.map((d) => [d, { no_go: [[22, 6]] as [number, number][], avoid: [[8, 20]] } as DaySchedule]),
    );
    expect(windowRows(a).map((r) => r.kind)).toEqual(["no_go", "avoid"]);
  });

  it("groups an interval that appears on only some weekdays", () => {
    const rows = windowRows(nonUniformArea());

    const noGo = rows.filter((r) => r.kind === "no_go");
    expect(noGo).toHaveLength(1);
    expect(noGo[0]).toMatchObject({ start: 22, end: 6 });
    expect(noGo[0].days).toEqual(["Monday", "Wednesday"]);

    const avoid = rows.filter((r) => r.kind === "avoid");
    expect(avoid).toHaveLength(1);
    expect(avoid[0].days).toEqual(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]);
  });

  it("records each row's index within its own day's list, for error lookup", () => {
    const a = uniformArea();
    // Tuesday carries an extra earlier avoid, so [8, 20] sits at index 1 there and 0 elsewhere.
    a.schedule.Tuesday.avoid = [
      [2, 4],
      [8, 20],
    ];
    const rows = windowRows(a);
    const main = rows.find((r) => r.start === 8 && r.end === 20)!;
    expect(main.at.Monday).toBe(0);
    expect(main.at.Tuesday).toBe(1);
  });

  it("keeps two distinct overlapping intervals as two rows", () => {
    const a = uniformArea();
    a.schedule = Object.fromEntries(
      ALL.map((d) => [
        d,
        { no_go: [], avoid: [[8, 12], [10, 14]] as [number, number][] } as DaySchedule,
      ]),
    );
    expect(windowRows(a)).toHaveLength(2);
  });
});

describe("applyWindowRows round-trip", () => {
  it("is exact for a uniform schedule", () => {
    const a = uniformArea();
    expect(applyWindowRows(windowRows(a))).toEqual(a.schedule);
  });

  it("is exact for a per-weekday schedule", () => {
    const a = nonUniformArea();
    expect(applyWindowRows(windowRows(a))).toEqual(a.schedule);
  });

  it("preserves the Monday..Sunday key order the server emits", () => {
    const a = nonUniformArea();
    expect(Object.keys(applyWindowRows(windowRows(a)))).toEqual(ALL);
  });

  // The two documented normalisations. Both are meaning-preserving because `_in_intervals`
  // is a disjunction and `_covered_hours` a set union — order and duplication are inert.
  it("collapses a duplicate interval within one day (meaning-preserving)", () => {
    const a = uniformArea();
    a.schedule.Monday.avoid = [
      [8, 20],
      [8, 20],
    ];
    expect(windowRows(a)).toHaveLength(1);
    expect(applyWindowRows(windowRows(a)).Monday.avoid).toEqual([[8, 20]]);
  });

  it("normalises per-day list order to the row order (meaning-preserving)", () => {
    const a = uniformArea();
    a.schedule.Monday.avoid = [
      [22, 6],
      [8, 20],
    ];
    a.schedule.Tuesday.avoid = [
      [8, 20],
      [22, 6],
    ];
    const rebuilt = applyWindowRows(windowRows(a));
    expect(rebuilt.Monday.avoid).toEqual(rebuilt.Tuesday.avoid);
  });
});

describe("availabilityBands", () => {
  // t = 0 is Monday 13:00 (horizon_start_hour 13), so offset 0..6 is Mon 13:00-19:00,
  // which sits inside the fixture's `avoid [8, 20]`.
  it("coalesces contiguous hours into one band, in absolute hour offsets", () => {
    const s = withArea(uniformArea());
    const bands = availabilityBands(s.areas[0], s, 0, 24);
    expect(bands[0]).toEqual({ state: "avoid", from: 0, to: 7 }); // Mon 13:00-20:00
    expect(bands[1]).toEqual({ state: "avoid", from: 19, to: 24 }); // Tue 08:00-13:00
  });

  it("resolves each weekday against its own intervals", () => {
    const s = withArea(nonUniformArea());
    // Saturday is free: hour-of-week 5*24 = 120, i.e. offset 120 - 13 = 107 onwards.
    const sat = availabilityBands(s.areas[0], s, 107, 107 + 24);
    expect(sat).toEqual([]);
  });

  it("no_go wins over avoid on the same hour", () => {
    const a = uniformArea();
    a.schedule.Monday.no_go = [[13, 15]];
    const s = withArea(a);
    expect(availabilityBands(s.areas[0], s, 0, 3)).toEqual([
      { state: "no_go", from: 0, to: 2 },
      { state: "avoid", from: 2, to: 3 },
    ]);
  });

  it("handles a range starting before t = 0 (the Gantt's history domain)", () => {
    const s = withArea(uniformArea());
    // Offset -1 is Monday 12:00 — inside `avoid [8, 20]`; -6 is Monday 07:00 — free.
    expect(availabilityBands(s.areas[0], s, -6, 0)).toEqual([{ state: "avoid", from: -5, to: 0 }]);
  });
});
