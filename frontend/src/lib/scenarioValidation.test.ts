import { describe, expect, it } from "vitest";

import { makeScenario } from "./fixtures";
import { updateArea } from "./scenarioEdits";
import { errorFor, validateScenario } from "./scenarioValidation";

describe("validateScenario", () => {
  it("passes a well-formed scenario", () => {
    expect(validateScenario(makeScenario())).toEqual([]);
  });

  it("flags priority outside 1..3", () => {
    const errors = validateScenario(updateArea(makeScenario(), "A1", { priority: 9 }));
    expect(errorFor(errors, "areas.A1.priority")).toMatch(/1, 2 or 3/);
  });

  it("flags min interval > max interval on the min field", () => {
    const errors = validateScenario(updateArea(makeScenario(), "A1", { min_interval: 99 }));
    expect(errorFor(errors, "areas.A1.min_interval")).toMatch(/≤ max interval/);
  });

  it("flags a non-integer / zero size", () => {
    expect(errorFor(validateScenario(updateArea(makeScenario(), "A1", { size_m2: 0 })), "areas.A1.size_m2")).toBeDefined();
    expect(errorFor(validateScenario(updateArea(makeScenario(), "A1", { size_m2: 1.5 })), "areas.A1.size_m2")).toBeDefined();
  });

  it("flags min_services > max_services and sub-1 values", () => {
    const s = updateArea(makeScenario(), "A1", { min_services: 5, max_services: 2 });
    expect(errorFor(validateScenario(s), "areas.A1.min_services")).toMatch(/≤ max services/);
    expect(
      errorFor(validateScenario(updateArea(makeScenario(), "A1", { min_services: 0 })), "areas.A1.min_services"),
    ).toMatch(/≥ 1/);
  });

  it("null service bounds are allowed (means: derive)", () => {
    expect(validateScenario(updateArea(makeScenario(), "A1", { min_services: null }))).toEqual([]);
  });

  it("flags a history completion before its start", () => {
    const s = makeScenario();
    s.history[0].completion = s.history[0].start - 5;
    expect(errorFor(validateScenario(s), "history.A1.completion")).toMatch(/before start/);
  });

  it("allows a history event that runs past t=0 (completion > 0)", () => {
    const s = makeScenario();
    s.history[0].start = -3;
    s.history[0].completion = 4;
    expect(validateScenario(s)).toEqual([]);
  });

  it("flags an out-of-range / zero-width availability interval, per weekday", () => {
    const s = makeScenario();
    for (const d of Object.keys(s.areas[0].schedule)) s.areas[0].schedule[d].avoid = [[8, 24]];
    expect(errorFor(validateScenario(s), "areas.A1.schedule.Monday.avoid.0")).toMatch(/0–23/);
    for (const d of Object.keys(s.areas[0].schedule)) s.areas[0].schedule[d].avoid = [[8, 8]];
    expect(errorFor(validateScenario(s), "areas.A1.schedule.Sunday.avoid.0")).toMatch(
      /can't be equal/,
    );
  });

  // Since ADR-0029 a window can live on some weekdays and not others, so an error has to
  // name the weekday it is on — not spray onto every row of every day.
  it("reports a bad interval only on the weekdays it is actually on", () => {
    const s = makeScenario();
    s.areas[0].schedule.Tuesday.avoid = [[8, 25]];
    const errs = validateScenario(s);
    expect(errorFor(errs, "areas.A1.schedule.Tuesday.avoid.0")).toMatch(/0–23/);
    expect(errorFor(errs, "areas.A1.schedule.Monday.avoid.0")).toBeUndefined();
  });

  it("flags an area with no service history event", () => {
    const s = makeScenario();
    s.history = [s.history[0]]; // drop A2's event
    expect(errorFor(validateScenario(s), "history.A2")).toMatch(/no service history/);
    expect(validateScenario(makeScenario()).some((e) => e.path.startsWith("history."))).toBe(false);
  });

  it("flags a no-go window covering the whole day (wrap-aware), on that weekday", () => {
    const s = makeScenario();
    // [1,0] covers 1..23 then wraps 0..0 -> {1..23}; [0,1] adds {0} -> all 24 hours
    for (const d of Object.keys(s.areas[0].schedule)) {
      s.areas[0].schedule[d].no_go = [
        [1, 0],
        [0, 1],
      ];
    }
    expect(errorFor(validateScenario(s), "areas.A1.schedule.Monday.no_go")).toMatch(/whole day/);
  });

  it("a full-day no-go on one weekday is reported on that weekday alone", () => {
    const s = makeScenario();
    s.areas[0].schedule.Thursday.no_go = [
      [1, 0],
      [0, 1],
    ];
    const errs = validateScenario(s);
    expect(errorFor(errs, "areas.A1.schedule.Thursday.no_go")).toMatch(/whole day/);
    expect(errorFor(errs, "areas.A1.schedule.Friday.no_go")).toBeUndefined();
  });
});
