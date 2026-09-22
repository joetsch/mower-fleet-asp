// Minimal valid Scenario for unit tests. Two areas, one mower that can service both,
// one history event per area — mirrors the `model.py` construction rules.

import type { Catalog, DaySchedule, RollResponse, Scenario, SolveResult } from "../types";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function week(day: DaySchedule): Record<string, DaySchedule> {
  return Object.fromEntries(DAYS.map((d) => [d, structuredClone(day)]));
}

export function makeScenario(): Scenario {
  return {
    name: "test",
    areas: [
      {
        name: "A1",
        type: "FAIRWAY",
        hole: 1,
        priority: 1,
        min_interval: 18,
        max_interval: 24,
        schedule: week({ no_go: [], avoid: [[8, 20]] }),
        size_m2: 5000,
        min_services: null,
        max_services: null,
      },
      {
        name: "A2",
        type: "SEMIROUGH_A",
        hole: 1,
        priority: 2,
        min_interval: 39,
        max_interval: 48,
        schedule: week({ no_go: [], avoid: [] }),
        size_m2: 3000,
        min_services: null,
        max_services: null,
      },
    ],
    mowers: [
      { name: "M1", can_mow: ["A1", "A2"], model: "AM_580L EPOS", area_capacity_m2_per_day: 8000 },
    ],
    history: [
      { area: "A1", mower: "M1", start: -20, completion: -14 },
      { area: "A2", mower: "M1", start: -40, completion: -33 },
    ],
    horizon_hours: 168,
    horizon_start_hour: 13,
    duration_seed: 0,
    base_durations: [
      { area: "A1", mower: "M1", hours: 15 },
      { area: "A2", mower: "M1", hours: 9 },
    ],
  };
}

/** A solved result over `makeScenario()` — one service per area, no violations. Enough
 *  for the stat row and the Gantt to render in the App-level tests (ADR-0028). */
export function makeSolveResult(): SolveResult {
  return {
    scenario_name: "test",
    horizon_hours: 168,
    solved: true,
    optimal: true,
    status: "optimal",
    solve_time_s: 1.5,
    schedule: {
      tasks: [
        { task: 1, area: "A1", mower: "M1", start: 2, end: 17 },
        { task: 1, area: "A2", mower: "M1", start: 20, end: 29 },
      ],
      violations: [],
      cost: [0, 0, 0, 0, 0],
    },
    quality: [0, 0, 0, 0, 0],
    solver: { name: "clingcon", args: ["-t4"], config: "many", threads: 4, time_limit_s: 20 },
    // A cold solve carries no preferences (ADR-0031).
    preferences: null,
  };
}

/** What `POST /api/scenario/advance` returns for `makeScenario()` rolled 24 h (ADR-0042):
 *  "now" moved on one weekday, both areas folded into history (A2's service straddles the
 *  new now), and one still-future task carried forward as a frozen preference. Pass
 *  `derived` from the test's own helper. */
export function makeRollResponse(derived: RollResponse["derived"]): RollResponse {
  const rolled: Scenario = {
    ...makeScenario(),
    horizon_start_hour: 37,
    history: [
      { area: "A1", mower: "M1", start: -22, completion: -7 },
      { area: "A2", mower: "M1", start: -4, completion: 5 },
    ],
  };
  return {
    scenario: rolled,
    derived,
    consumed: rolled.history,
    carried: [{ area: "A1", start: 50, mower: "M1", origin: "frozen" }],
    notes: ["A2: M1 is still running at the new now (finishes at +5 h)"],
  };
}

/** A small catalogue for the add-area / add-mower helpers. FAIRWAY first (matches the
 * real `/api/catalog` order); only the 10 mm model reaches it. */
export function makeCatalog(): Catalog {
  return {
    mower_models: [
      {
        name: "AM_520 EPOS",
        area_capacity_m2_per_day: 2000,
        min_cut_height_mm: 20,
        max_cut_height_mm: 60,
        capable_area_types: ["SEMIROUGH_A", "SEMIROUGH_B", "SEMIROUGH_C"],
      },
      {
        name: "AM_580L EPOS",
        area_capacity_m2_per_day: 8000,
        min_cut_height_mm: 10,
        max_cut_height_mm: 50,
        capable_area_types: ["FAIRWAY", "SEMIROUGH_A", "SEMIROUGH_B", "SEMIROUGH_C"],
      },
    ],
    area_types: [
      {
        name: "FAIRWAY",
        cut_height_mm: 14,
        priority: 1,
        min_interval_h: 18,
        max_interval_h: 24,
        default_size_m2: 6000,
      },
      {
        name: "SEMIROUGH_B",
        cut_height_mm: 45,
        priority: 3,
        min_interval_h: 39,
        max_interval_h: 48,
        default_size_m2: 3350,
      },
    ],
  };
}
