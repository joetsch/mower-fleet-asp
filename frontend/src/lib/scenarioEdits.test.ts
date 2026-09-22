import { describe, expect, it } from "vitest";

import { makeCatalog, makeScenario } from "./fixtures";
import { defaultDurationHours } from "./scenarioDefaults";
import {
  addArea,
  addAvailabilityWindow,
  addMower,
  canDropService,
  deleteArea,
  deleteAvailabilityWindow,
  deleteMower,
  dropOneService,
  forceOneMoreService,
  historyReassignedByDeletingMower,
  scenariosEqual,
  scheduleIsUniform,
  setAreaType,
  setBaseDuration,
  setIntervalEndpoint,
  setWindowDays,
  renameArea,
  renameMower,
  setMowerModel,
  strandedAreasWithoutMower,
  toggleCapability,
  updateArea,
  updateHistoryEvent,
  updateMower,
  updateScenario,
} from "./scenarioEdits";
import { validateScenario } from "./scenarioValidation";
import type { Scenario } from "../types";

/** Scenario with a second mower that only covers A1 — for stranding / reassignment tests. */
function twoMowerScenario(): Scenario {
  const s = makeScenario();
  s.mowers.push({ name: "M2", can_mow: ["A1"], model: null, area_capacity_m2_per_day: 6000 });
  s.base_durations = [...(s.base_durations ?? []), { area: "A1", mower: "M2", hours: 12 }];
  return s;
}

describe("scenariosEqual", () => {
  it("is true for a fresh clone and false after any change", () => {
    const s = makeScenario();
    expect(scenariosEqual(s, structuredClone(s))).toBe(true);
    expect(scenariosEqual(s, updateArea(s, "A1", { priority: 3 }))).toBe(false);
  });
});

describe("updateArea", () => {
  it("patches the named area and leaves the input untouched", () => {
    const s = makeScenario();
    const next = updateArea(s, "A1", { priority: 2, min_interval: 12 });
    expect(next.areas[0].priority).toBe(2);
    expect(next.areas[0].min_interval).toBe(12);
    expect(s.areas[0].priority).toBe(1); // original unmutated
    expect(next.areas[1]).toEqual(s.areas[1]); // other area untouched
  });

  it("is a no-op for an unknown area name", () => {
    const s = makeScenario();
    expect(scenariosEqual(updateArea(s, "nope", { priority: 3 }), s)).toBe(true);
  });
});

describe("setAreaType", () => {
  const catalogType = {
    name: "SEMIROUGH_C",
    cut_height_mm: 45,
    priority: 3,
    min_interval_h: 39,
    max_interval_h: 72,
    default_size_m2: 6000,
  };

  it("adopts the catalogue type's policy as defaults", () => {
    const a = setAreaType(makeScenario(), "A1", "SEMIROUGH_C", catalogType).areas[0];
    expect(a.type).toBe("SEMIROUGH_C");
    expect(a.priority).toBe(3);
    expect(a.min_interval).toBe(39);
    expect(a.max_interval).toBe(72);
  });

  it("only relabels for an unknown / free-typed type", () => {
    const before = makeScenario().areas[0];
    const a = setAreaType(makeScenario(), "A1", "CUSTOM").areas[0];
    expect(a.type).toBe("CUSTOM");
    expect(a.priority).toBe(before.priority);
    expect(a.min_interval).toBe(before.min_interval);
  });
});

describe("setMowerModel", () => {
  const catalogMower = {
    name: "Ceora_546 EPOS",
    area_capacity_m2_per_day: 25000,
    min_cut_height_mm: 20,
    max_cut_height_mm: 70,
    capable_area_types: ["SEMIROUGH_A"],
  };

  it("adopts the catalogue model's rate", () => {
    const m = setMowerModel(makeScenario(), "M1", "Ceora_546 EPOS", catalogMower).mowers[0];
    expect(m.model).toBe("Ceora_546 EPOS");
    expect(m.area_capacity_m2_per_day).toBe(25000);
  });

  it("clears the model for an empty pick, keeps the rate", () => {
    const m = setMowerModel(makeScenario(), "M1", "").mowers[0];
    expect(m.model).toBeNull();
    expect(m.area_capacity_m2_per_day).toBe(8000);
  });
});

describe("setBaseDuration", () => {
  it("updates the matching (area, mower) hours only", () => {
    const s = setBaseDuration(makeScenario(), "A1", "M1", 22);
    expect(s.base_durations?.find((d) => d.area === "A1" && d.mower === "M1")?.hours).toBe(22);
    expect(s.base_durations?.find((d) => d.area === "A2" && d.mower === "M1")?.hours).toBe(9);
  });
});

describe("setIntervalEndpoint", () => {
  it("moves one endpoint across all 7 weekdays, keeping the schedule uniform", () => {
    const s = setIntervalEndpoint(makeScenario(), "A1", "avoid", 0, "end", 18);
    expect(scheduleIsUniform(s.areas[0])).toBe(true);
    for (const d of ["Monday", "Wednesday", "Sunday"]) {
      expect(s.areas[0].schedule[d].avoid[0]).toEqual([8, 18]);
    }
    // A2 (no avoid window) untouched
    expect(s.areas[1].schedule.Monday.avoid).toEqual([]);
  });

  it("is a no-op for an out-of-range interval index", () => {
    const s = makeScenario();
    expect(scenariosEqual(setIntervalEndpoint(s, "A1", "no_go", 5, "start", 3), s)).toBe(true);
  });
});

describe("toggleCapability", () => {
  it("enabling adds the can_mow entry + a seeded base_durations row", () => {
    const s = makeScenario();
    // remove A2 from M1 first, then re-add
    const removed = toggleCapability(s, "A2", "M1", false);
    expect(removed.mowers[0].can_mow).toEqual(["A1"]);
    expect(removed.base_durations?.some((d) => d.area === "A2")).toBe(false);

    const readded = toggleCapability(removed, "A2", "M1", true);
    expect(readded.mowers[0].can_mow).toEqual(["A1", "A2"]);
    const bd = readded.base_durations?.find((d) => d.area === "A2" && d.mower === "M1");
    expect(bd?.hours).toBe(9); // ceil(3000 / (8000/24))
    expect(validateScenario(readded)).toEqual([]);
  });

  it("disabling the last capable mower orphans the area — flagged, base_durations stays exact", () => {
    const s = toggleCapability(makeScenario(), "A1", "M1", false);
    expect(s.base_durations?.some((d) => d.area === "A1")).toBe(false);
    const errs = validateScenario(s);
    expect(errs.some((e) => e.path === "areas.A1.capability")).toBe(true);
    expect(errs.some((e) => e.path === "base_durations")).toBe(false); // still consistent
  });

  it("disabling reassigns a history event to another capable mower", () => {
    let s = makeScenario();
    s = updateMower(s, "M1", { name: "M1" }); // no-op, keep shape
    // add a second mower capable of A1
    s = structuredClone(s);
    s.mowers.push({ name: "M2", can_mow: ["A1"], model: null, area_capacity_m2_per_day: 6000 });
    s.base_durations = [...(s.base_durations ?? []), { area: "A1", mower: "M2", hours: 12 }];
    const toggled = toggleCapability(s, "A1", "M1", false);
    expect(toggled.history.find((h) => h.area === "A1")?.mower).toBe("M2");
    expect(validateScenario(toggled)).toEqual([]);
  });
});

describe("renameArea / renameMower", () => {
  it("renameArea cascades to can_mow, history and base_durations", () => {
    const s = renameArea(makeScenario(), "A1", "Green 1");
    expect(s.areas.map((a) => a.name)).toEqual(["Green 1", "A2"]);
    expect(s.mowers[0].can_mow).toContain("Green 1");
    expect(s.history.find((h) => h.area === "Green 1")).toBeTruthy();
    expect(s.base_durations?.some((d) => d.area === "Green 1")).toBe(true);
    expect(s.base_durations?.some((d) => d.area === "A1")).toBe(false);
    expect(validateScenario(s)).toEqual([]);
  });

  it("renameMower cascades to history and base_durations, not can_mow", () => {
    const s = renameMower(makeScenario(), "M1", "Ranger");
    expect(s.mowers[0].name).toBe("Ranger");
    expect(s.history.every((h) => h.mower === "Ranger")).toBe(true);
    expect(s.base_durations?.every((d) => d.mower === "Ranger")).toBe(true);
    expect(s.mowers[0].can_mow).toEqual(["A1", "A2"]); // area names unchanged
  });

  it("is a no-op for a blank or duplicate name", () => {
    const s = makeScenario();
    expect(scenariosEqual(renameArea(s, "A1", "  "), s)).toBe(true);
    expect(scenariosEqual(renameArea(s, "A1", "A2"), s)).toBe(true);
    expect(scenariosEqual(renameMower(s, "M1", "M1"), s)).toBe(true);
  });
});

describe("updateMower / updateHistoryEvent / updateScenario", () => {
  it("patch by key without touching siblings", () => {
    const s = makeScenario();
    expect(updateMower(s, "M1", { area_capacity_m2_per_day: 9999 }).mowers[0].area_capacity_m2_per_day).toBe(9999);
    expect(updateHistoryEvent(s, "A2", { completion: -30 }).history[1].completion).toBe(-30);
    expect(updateScenario(s, { name: "renamed" }).name).toBe("renamed");
    expect(s.name).toBe("test");
  });
});

// --- structural editing (ADR-0027) ---------------------------------------------------

describe("addArea", () => {
  it("adds a valid, fully-wired area with a new hole number", () => {
    const next = addArea(makeScenario(), makeCatalog());
    expect(next.areas).toHaveLength(3);
    const a = next.areas[2];
    expect(a.name).toBe("Area 1");
    expect(a.hole).toBe(2);
    expect(a.type).toBe("FAIRWAY");
    expect(a.size_m2).toBe(6000);
    expect(scheduleIsUniform(a)).toBe(true);
    expect(validateScenario(next)).toEqual([]);
  });

  it("keeps the key order of Area / ServiceEvent / BaseDuration and the Scenario itself", () => {
    const s = makeScenario();
    const next = addArea(s, makeCatalog());
    expect(Object.keys(next.areas.at(-1)!)).toEqual(Object.keys(s.areas[0]));
    expect(Object.keys(next.history.at(-1)!)).toEqual(Object.keys(s.history[0]));
    expect(Object.keys(next.base_durations!.at(-1)!)).toEqual(Object.keys(s.base_durations![0]));
    expect(Object.keys(next)).toEqual(Object.keys(s));
  });

  it("synthesises exactly one history event, 'just serviced at t = 0'", () => {
    const next = addArea(makeScenario(), makeCatalog());
    const evs = next.history.filter((h) => h.area === "Area 1");
    expect(evs).toHaveLength(1);
    expect(evs[0].completion).toBe(0);
    expect(evs[0].start).toBe(-defaultDurationHours(6000, 8000));
  });

  it("wires only the catalogue-capable mowers, else the first mower", () => {
    // M1 = AM_580L (reaches FAIRWAY); M2 = AM_520 (does not)
    const s = makeScenario();
    s.mowers.push({ name: "M2", can_mow: ["A2"], model: "AM_520 EPOS", area_capacity_m2_per_day: 2000 });
    s.base_durations!.push({ area: "A2", mower: "M2", hours: 30 });
    const next = addArea(s, makeCatalog());
    expect(next.mowers.find((m) => m.name === "M1")!.can_mow).toContain("Area 1");
    expect(next.mowers.find((m) => m.name === "M2")!.can_mow).not.toContain("Area 1");
    expect(validateScenario(next)).toEqual([]);
  });

  it("numbers successive areas", () => {
    let s = addArea(makeScenario(), makeCatalog());
    s = addArea(s, makeCatalog());
    expect(s.areas.map((a) => a.name)).toEqual(["A1", "A2", "Area 1", "Area 2"]);
    expect(validateScenario(s)).toEqual([]);
  });

  it("is a no-op with no catalogue (the button is disabled instead)", () => {
    const s = makeScenario();
    expect(scenariosEqual(addArea(s, null), s)).toBe(true);
  });

  it("leaves base_durations null when it started null", () => {
    const next = addArea({ ...makeScenario(), base_durations: null }, makeCatalog());
    expect(next.base_durations).toBeNull();
    expect(validateScenario(next)).toEqual([]);
  });

  it("is a no-op when there are no mowers", () => {
    const s: Scenario = { ...makeScenario(), mowers: [], history: [], base_durations: null };
    expect(scenariosEqual(addArea(s, makeCatalog()), s)).toBe(true);
  });
});

describe("deleteArea", () => {
  it("removes the area and every reference to it", () => {
    const next = deleteArea(makeScenario(), "A2");
    expect(next.areas.map((a) => a.name)).toEqual(["A1"]);
    expect(next.mowers[0].can_mow).toEqual(["A1"]);
    expect(next.history.some((h) => h.area === "A2")).toBe(false);
    expect(next.base_durations!.some((d) => d.area === "A2")).toBe(false);
    expect(validateScenario(next)).toEqual([]);
  });

  it("leaves an idle mower and collapses base_durations to null when nothing is left", () => {
    const next = deleteArea(deleteArea(makeScenario(), "A1"), "A2");
    expect(next.areas).toEqual([]);
    expect(next.mowers[0].can_mow).toEqual([]);
    expect(next.base_durations).toBeNull();
    expect(validateScenario(next)).toEqual([]);
  });

  it("is a no-op for an unknown area", () => {
    const s = makeScenario();
    expect(scenariosEqual(deleteArea(s, "nope"), s)).toBe(true);
  });
});

describe("addMower", () => {
  it("adds a mower with an empty capability row and the best-coverage model", () => {
    const s = makeScenario();
    const next = addMower(s, makeCatalog());
    expect(next.mowers).toHaveLength(2);
    const m = next.mowers[1];
    expect(m.name).toBe("Mower 1");
    expect(m.can_mow).toEqual([]);
    expect(m.model).toBe("AM_580L EPOS"); // covers both present types
    expect(m.area_capacity_m2_per_day).toBe(8000);
    expect(Object.keys(m)).toEqual(Object.keys(s.mowers[0]));
    expect(next.base_durations).toEqual(s.base_durations); // no new pairs
    expect(validateScenario(next)).toEqual([]);
  });

  it("numbers successive mowers and works with no catalogue", () => {
    let s = addMower(makeScenario(), null);
    s = addMower(s, null);
    expect(s.mowers.map((m) => m.name)).toEqual(["M1", "Mower 1", "Mower 2"]);
    expect(s.mowers[1].model).toBeNull();
    expect(validateScenario(s)).toEqual([]);
  });
});

describe("strandedAreasWithoutMower / deleteMower", () => {
  it("blocks removing the sole mower for an area", () => {
    const s = makeScenario();
    expect(strandedAreasWithoutMower(s, "M1")).toEqual(["A1", "A2"]);
    expect(scenariosEqual(deleteMower(s, "M1"), s)).toBe(true);
  });

  it("removes a redundant mower and keeps the scenario valid", () => {
    const s = twoMowerScenario();
    expect(strandedAreasWithoutMower(s, "M2")).toEqual([]);
    const next = deleteMower(s, "M2");
    expect(next.mowers.map((m) => m.name)).toEqual(["M1"]);
    expect(next.base_durations!.some((d) => d.mower === "M2")).toBe(false);
    expect(validateScenario(next)).toEqual([]);
  });

  it("reassigns the removed mower's history events and reports the count", () => {
    const s = twoMowerScenario();
    s.history.find((h) => h.area === "A1")!.mower = "M2";
    expect(historyReassignedByDeletingMower(s, "M2")).toBe(1);
    const next = deleteMower(s, "M2");
    expect(next.history.find((h) => h.area === "A1")!.mower).toBe("M1");
    expect(validateScenario(next)).toEqual([]);
  });

  it("never silently recomputes a surviving hand-set duration", () => {
    const s = twoMowerScenario();
    s.base_durations!.find((d) => d.area === "A1" && d.mower === "M1")!.hours = 99;
    const next = deleteMower(s, "M2");
    expect(next.base_durations!.find((d) => d.area === "A1" && d.mower === "M1")!.hours).toBe(99);
  });
});

describe("addAvailabilityWindow / deleteAvailabilityWindow", () => {
  it("adds the canonical first window to an always-free area, on all 7 days", () => {
    const next = addAvailabilityWindow(makeScenario(), "A2", "no_go");
    const a = next.areas[1];
    expect(scheduleIsUniform(a)).toBe(true);
    for (const d of ["Monday", "Thursday", "Sunday"]) {
      expect(a.schedule[d].no_go).toEqual([[22, 6]]);
    }
    expect(validateScenario(next)).toEqual([]);
  });

  it("places a further window just after the last one", () => {
    const next = addAvailabilityWindow(makeScenario(), "A1", "avoid");
    expect(next.areas[0].schedule.Monday.avoid).toEqual([
      [8, 20],
      [20, 22],
    ]);
  });

  it("round-trips add then delete", () => {
    const s = makeScenario();
    const added = addAvailabilityWindow(s, "A2", "no_go");
    expect(scenariosEqual(deleteAvailabilityWindow(added, "A2", "no_go", 0), s)).toBe(true);
  });

  it("is a no-op for a bad index", () => {
    const s = makeScenario();
    expect(scenariosEqual(deleteAvailabilityWindow(s, "A1", "no_go", 9), s)).toBe(true);
  });
});

// Per-weekday availability editing (ADR-0029). A window is a row — an interval plus the
// weekdays it applies to — so every helper below writes only that row's own days. Before
// this, all four broadcast a Monday template to all seven and refused non-uniform areas.
describe("per-weekday availability windows", () => {
  /** A1 with a weekday-only avoid and a Mon+Wed no-go. */
  function nonUniform() {
    const s = makeScenario();
    const a = s.areas[0];
    a.schedule.Saturday = { no_go: [], avoid: [] };
    a.schedule.Sunday = { no_go: [], avoid: [] };
    a.schedule.Monday.no_go = [[22, 6]];
    a.schedule.Wednesday.no_go = [[22, 6]];
    return s;
  }

  it("addAvailabilityWindow writes only the given weekdays", () => {
    const next = addAvailabilityWindow(makeScenario(), "A2", "no_go", ["Tuesday", "Thursday"]);
    const a = next.areas[1];
    expect(a.schedule.Tuesday.no_go).toEqual([[22, 6]]);
    expect(a.schedule.Thursday.no_go).toEqual([[22, 6]]);
    expect(a.schedule.Monday.no_go).toEqual([]);
    expect(a.schedule.Sunday.no_go).toEqual([]);
    expect(scheduleIsUniform(a)).toBe(false);
    expect(validateScenario(next)).toEqual([]);
  });

  it("addAvailabilityWindow still defaults to all 7 weekdays", () => {
    const withDefault = addAvailabilityWindow(makeScenario(), "A2", "no_go");
    const explicit = addAvailabilityWindow(makeScenario(), "A2", "no_go", [
      "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    ]);
    expect(scenariosEqual(withDefault, explicit)).toBe(true);
  });

  it("addAvailabilityWindow works on an already non-uniform area", () => {
    // The area already has an avoid row ([8, 20] on Mon-Fri), so the new one sits just
    // after it — the same "after the last of this kind" rule as a uniform area.
    const next = addAvailabilityWindow(nonUniform(), "A1", "avoid", ["Saturday"]);
    expect(next.areas[0].schedule.Saturday.avoid).toEqual([[20, 22]]);
    // The weekday avoid it was seeded from is untouched.
    expect(next.areas[0].schedule.Monday.avoid).toEqual([[8, 20]]);
    expect(next.areas[0].schedule.Sunday.avoid).toEqual([]);
  });

  it("setIntervalEndpoint moves the endpoint on the row's own days only", () => {
    const next = setIntervalEndpoint(nonUniform(), "A1", "no_go", 0, "start", 21);
    const a = next.areas[0];
    expect(a.schedule.Monday.no_go).toEqual([[21, 6]]);
    expect(a.schedule.Wednesday.no_go).toEqual([[21, 6]]);
    // The days the row was never on must not gain it (the flattening hazard).
    expect(a.schedule.Tuesday.no_go).toEqual([]);
    expect(a.schedule.Saturday.no_go).toEqual([]);
    // The unrelated avoid row keeps its own day set.
    expect(a.schedule.Saturday.avoid).toEqual([]);
    expect(a.schedule.Friday.avoid).toEqual([[8, 20]]);
  });

  it("deleteAvailabilityWindow removes the row from its own days only", () => {
    const next = deleteAvailabilityWindow(nonUniform(), "A1", "no_go", 0);
    const a = next.areas[0];
    expect(a.schedule.Monday.no_go).toEqual([]);
    expect(a.schedule.Wednesday.no_go).toEqual([]);
    expect(a.schedule.Monday.avoid).toEqual([[8, 20]]); // the avoid row survives
    expect(a.schedule.Saturday.avoid).toEqual([]);
  });

  it("setWindowDays adds and removes weekdays", () => {
    const grown = setWindowDays(nonUniform(), "A1", "no_go", 0, [
      "Monday", "Wednesday", "Friday",
    ]);
    expect(grown.areas[0].schedule.Friday.no_go).toEqual([[22, 6]]);

    const shrunk = setWindowDays(grown, "A1", "no_go", 0, ["Friday"]);
    expect(shrunk.areas[0].schedule.Friday.no_go).toEqual([[22, 6]]);
    expect(shrunk.areas[0].schedule.Monday.no_go).toEqual([]);
  });

  it("setWindowDays is a no-op for an empty day set — removal has its own button", () => {
    const s = nonUniform();
    expect(scenariosEqual(setWindowDays(s, "A1", "no_go", 0, []), s)).toBe(true);
  });

  it("setWindowDays is a no-op for a bad index", () => {
    const s = nonUniform();
    expect(scenariosEqual(setWindowDays(s, "A1", "no_go", 4, ["Monday"]), s)).toBe(true);
  });
});

describe("toggleCapability keeps a null base_durations null (bugfix)", () => {
  it("enabling a capability on a sampling-path scenario does not create a partial array", () => {
    const s: Scenario = { ...makeScenario(), base_durations: null };
    const disabled = toggleCapability(s, "A2", "M1", false);
    const reenabled = toggleCapability(disabled, "A2", "M1", true);
    expect(reenabled.base_durations).toBeNull();
    expect(validateScenario(reenabled)).toEqual([]);
  });
});

// Service-bound nudges from the schedule editor (ADR-0039).
describe("dropOneService", () => {
  it("lowers max to one below the plan's current count and keeps min ≤ max", () => {
    const s = updateArea(makeScenario(), "A1", { min_services: 3, max_services: 5 });
    const a = dropOneService(s, "A1", 3).areas.find((x) => x.name === "A1");
    expect(a?.max_services).toBe(2);
    expect(a?.min_services).toBe(2); // clamped down from 3
  });

  it("leaves min alone when it is already within the new cap", () => {
    const s = updateArea(makeScenario(), "A1", { min_services: 1, max_services: 4 });
    const a = dropOneService(s, "A1", 3).areas.find((x) => x.name === "A1");
    expect(a?.max_services).toBe(2);
    expect(a?.min_services).toBe(1);
  });

  it("is a no-op below count 2 (the model needs max_services ≥ 1) and stays valid", () => {
    const s = makeScenario();
    expect(scenariosEqual(dropOneService(s, "A1", 1), s)).toBe(true);
    expect(validateScenario(dropOneService(s, "A1", 3))).toEqual([]);
  });
});

describe("forceOneMoreService", () => {
  it("raises min to one above the current count", () => {
    const a = forceOneMoreService(makeScenario(), "A2", 2).areas.find((x) => x.name === "A2");
    expect(a?.min_services).toBe(3);
  });

  it("raises max to match when it was below the new floor", () => {
    const s = updateArea(makeScenario(), "A2", { max_services: 2 });
    const a = forceOneMoreService(s, "A2", 3).areas.find((x) => x.name === "A2");
    expect(a?.min_services).toBe(4);
    expect(a?.max_services).toBe(4);
    expect(validateScenario(forceOneMoreService(s, "A2", 3))).toEqual([]);
  });
});

describe("canDropService", () => {
  it("is false at 1 and true from 2", () => {
    expect(canDropService(1)).toBe(false);
    expect(canDropService(2)).toBe(true);
  });
});
