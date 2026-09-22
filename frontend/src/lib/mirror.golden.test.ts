// Cross-boundary mirror guard — docs/known-hazards.md "Refactor before Iteration 5",
// item 4. `scenarioEdits.ts` constructs scenarios and `scenarioValidation.ts` judges
// them, both against a *hand copy* of the `model.py` ruleset. Nothing pinned the copy:
// if a rule moved server-side the editor would keep building scenarios the server
// rejects on Save, and you'd find out in a demo.
//
// This test emits two committed goldens under `tests/golden/`:
//
//   * edit_helper_outputs.json  — every scenario the structural-edit helpers build, so a
//     pure-pydantic case in `tests/test_model.py` can assert each one is a valid
//     `Scenario`.
//   * edit_validation_table.json — a table of accept/reject verdicts from
//     `validateScenario`, so the same test can assert pydantic agrees on each row.
//
// Regenerate on purpose (the `--update-golden` convention, from `frontend/`):
//
//   UPDATE_GOLDEN=1 npm run test
//
// then review the diff like any golden. Without the flag this test just asserts the
// committed goldens still match what the helpers produce today (the frontend half of
// the guard; `test_model.py` is the backend half).

/// <reference types="node" />
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { makeCatalog, makeScenario } from "./fixtures";
import {
  addArea,
  addAvailabilityWindow,
  addMower,
  deleteArea,
  deleteAvailabilityWindow,
  deleteMower,
  renameArea,
  renameMower,
  setAreaType,
  setMowerModel,
  setWindowDays,
  toggleCapability,
  updateArea,
} from "./scenarioEdits";
import { validateScenario } from "./scenarioValidation";
import type { Scenario } from "../types";

const UPDATING = Boolean(process.env.UPDATE_GOLDEN);

// vitest runs with cwd = frontend/; the golden dir is one level up. Deliberately not
// `new URL(..., import.meta.url)` — Vite would try to bundle the whole directory as
// assets and trip its fs allow-list (tests/ is outside the frontend root).
function goldenPath(name: string): string {
  return resolve(process.cwd(), "../tests/golden", name);
}

/** Write `data` as pretty JSON when regenerating; otherwise assert it equals the
 * committed file. Mirrors `conftest.py::check_golden`. */
function checkGolden(name: string, data: unknown): void {
  const path = goldenPath(name);
  const text = JSON.stringify(data, null, 2) + "\n";
  if (UPDATING) {
    writeFileSync(path, text);
    return;
  }
  expect(JSON.parse(readFileSync(path, "utf-8"))).toEqual(data);
}

/** A second mower that only covers A1 — lets `deleteMower` drop it without stranding. */
function twoMowerScenario(): Scenario {
  const s = makeScenario();
  s.mowers.push({ name: "M2", can_mow: ["A1"], model: null, area_capacity_m2_per_day: 6000 });
  s.base_durations = [...(s.base_durations ?? []), { area: "A1", mower: "M2", hours: 12 }];
  return s;
}

const SEMIROUGH_C = {
  name: "SEMIROUGH_C",
  cut_height_mm: 45,
  priority: 3,
  min_interval_h: 39,
  max_interval_h: 72,
  default_size_m2: 6000,
};
const CEORA = {
  name: "Ceora_546 EPOS",
  area_capacity_m2_per_day: 25000,
  min_cut_height_mm: 20,
  max_cut_height_mm: 70,
  capable_area_types: ["SEMIROUGH_A", "FAIRWAY"],
};

// --- 1. helper outputs: every one must be a valid pydantic Scenario ------------------

interface HelperCase {
  helper: string;
  call: string;
  output: Scenario;
}

function helperCases(): HelperCase[] {
  const cases: HelperCase[] = [];
  const add = (helper: string, call: string, output: Scenario) =>
    cases.push({ helper, call, output });

  add("addArea", "addArea(makeScenario(), makeCatalog())", addArea(makeScenario(), makeCatalog()));
  add(
    "addArea",
    "addArea(addArea(makeScenario(), cat), cat) — two areas in a row",
    addArea(addArea(makeScenario(), makeCatalog()), makeCatalog()),
  );
  add(
    "addArea",
    "addArea({...makeScenario(), base_durations: null}, cat) — sampling-path scenario",
    addArea({ ...makeScenario(), base_durations: null }, makeCatalog()),
  );
  add("deleteArea", "deleteArea(makeScenario(), 'A2')", deleteArea(makeScenario(), "A2"));
  add(
    "deleteArea",
    "deleteArea(deleteArea(makeScenario(), 'A1'), 'A2') — empties the library",
    deleteArea(deleteArea(makeScenario(), "A1"), "A2"),
  );
  add("addMower", "addMower(makeScenario(), makeCatalog())", addMower(makeScenario(), makeCatalog()));
  add("addMower", "addMower(makeScenario(), null) — no catalogue", addMower(makeScenario(), null));
  add("deleteMower", "deleteMower(twoMowerScenario(), 'M2')", deleteMower(twoMowerScenario(), "M2"));
  add(
    "toggleCapability",
    "toggleCapability(toggleCapability(makeScenario(), 'A2', 'M1', false), 'A2', 'M1', true)",
    toggleCapability(toggleCapability(makeScenario(), "A2", "M1", false), "A2", "M1", true),
  );
  add(
    "toggleCapability",
    "toggleCapability on a base_durations: null scenario (re-enable)",
    toggleCapability(
      toggleCapability({ ...makeScenario(), base_durations: null }, "A2", "M1", false),
      "A2",
      "M1",
      true,
    ),
  );
  add(
    "addAvailabilityWindow",
    "addAvailabilityWindow(makeScenario(), 'A2', 'no_go') — canonical night window",
    addAvailabilityWindow(makeScenario(), "A2", "no_go"),
  );
  add(
    "addAvailabilityWindow",
    "addAvailabilityWindow(makeScenario(), 'A1', 'avoid') — appended after the last",
    addAvailabilityWindow(makeScenario(), "A1", "avoid"),
  );
  add(
    "deleteAvailabilityWindow",
    "deleteAvailabilityWindow(addAvailabilityWindow(makeScenario(), 'A1', 'avoid'), 'A1', 'avoid', 1)",
    deleteAvailabilityWindow(
      addAvailabilityWindow(makeScenario(), "A1", "avoid"),
      "A1",
      "avoid",
      1,
    ),
  );
  // Per-weekday windows (ADR-0029). A non-uniform schedule is user-reachable now, so
  // pydantic has to agree it is a valid Scenario — the client no longer keeps every
  // weekday identical.
  add(
    "addAvailabilityWindow",
    "addAvailabilityWindow(makeScenario(), 'A2', 'no_go', ['Tuesday', 'Thursday']) — weekday subset",
    addAvailabilityWindow(makeScenario(), "A2", "no_go", ["Tuesday", "Thursday"]),
  );
  add(
    "setWindowDays",
    "setWindowDays(makeScenario(), 'A1', 'avoid', 0, ['Monday'..'Friday']) — weekdays only",
    setWindowDays(makeScenario(), "A1", "avoid", 0, [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
    ]),
  );
  add(
    "deleteAvailabilityWindow",
    "deleteAvailabilityWindow(<weekday-subset no_go>, 'A2', 'no_go', 0) — back to always free",
    deleteAvailabilityWindow(
      addAvailabilityWindow(makeScenario(), "A2", "no_go", ["Tuesday", "Thursday"]),
      "A2",
      "no_go",
      0,
    ),
  );
  add("renameArea", "renameArea(makeScenario(), 'A1', 'Green 1')", renameArea(makeScenario(), "A1", "Green 1"));
  add("renameMower", "renameMower(makeScenario(), 'M1', 'Ranger')", renameMower(makeScenario(), "M1", "Ranger"));
  add(
    "setAreaType",
    "setAreaType(makeScenario(), 'A1', 'SEMIROUGH_C', <catalogType>)",
    setAreaType(makeScenario(), "A1", "SEMIROUGH_C", SEMIROUGH_C),
  );
  add(
    "setMowerModel",
    "setMowerModel(makeScenario(), 'M1', 'Ceora_546 EPOS', <catalogMower>)",
    setMowerModel(makeScenario(), "M1", "Ceora_546 EPOS", CEORA),
  );
  return cases;
}

// --- 2. validation table: pydantic must agree on each accept/reject ------------------

interface ValidationCase {
  note: string;
  verdict: "accept" | "reject";
  clientErrorPaths: string[];
  scenario: Scenario;
}

/** `makeScenario()` with one primitive mutation applied — keeps the cases terse. */
function mutate(fn: (s: Scenario) => void): Scenario {
  const s = makeScenario();
  fn(s);
  return s;
}

function validationCases(): ValidationCase[] {
  const raw: Array<{ note: string; scenario: Scenario }> = [
    { note: "well-formed baseline", scenario: makeScenario() },
    { note: "priority above 3", scenario: updateArea(makeScenario(), "A1", { priority: 9 }) },
    { note: "priority below 1", scenario: updateArea(makeScenario(), "A1", { priority: 0 }) },
    {
      note: "min_interval > max_interval",
      scenario: updateArea(makeScenario(), "A1", { min_interval: 99 }),
    },
    {
      note: "min_services > max_services",
      scenario: updateArea(makeScenario(), "A1", { min_services: 5, max_services: 2 }),
    },
    {
      note: "history completion before start",
      scenario: mutate((s) => {
        s.history[0].completion = s.history[0].start - 5;
      }),
    },
    {
      note: "duplicate area names",
      scenario: mutate((s) => {
        s.areas.push(structuredClone(s.areas[0]));
      }),
    },
    {
      note: "duplicate mower names",
      scenario: mutate((s) => {
        s.mowers.push(structuredClone(s.mowers[0]));
      }),
    },
    {
      note: "no_go covers the whole day (wrap-aware)",
      scenario: mutate((s) => {
        for (const d of Object.keys(s.areas[0].schedule)) {
          s.areas[0].schedule[d].no_go = [
            [1, 0],
            [0, 1],
          ];
        }
      }),
    },
    {
      note: "zero-width availability interval",
      scenario: mutate((s) => {
        for (const d of Object.keys(s.areas[0].schedule)) s.areas[0].schedule[d].avoid = [[8, 8]];
      }),
    },
    {
      note: "availability hour outside 0..23",
      scenario: mutate((s) => {
        for (const d of Object.keys(s.areas[0].schedule)) s.areas[0].schedule[d].avoid = [[8, 24]];
      }),
    },
    {
      note: "base_durations misses a capable (area, mower) pair",
      scenario: mutate((s) => {
        s.base_durations = [s.base_durations![0]];
      }),
    },
    {
      note: "an area has no service history event",
      scenario: mutate((s) => {
        s.history = [s.history[0]];
      }),
    },
  ];

  return raw.map(({ note, scenario }) => {
    const errors = validateScenario(scenario);
    return {
      note,
      verdict: errors.length === 0 ? "accept" : "reject",
      clientErrorPaths: [...new Set(errors.map((e) => e.path))].sort(),
      scenario,
    };
  });
}

// --- the goldens --------------------------------------------------------------------

const COMMENT =
  "Cross-boundary mirror guard — docs/known-hazards.md 'Refactor before Iteration 5' " +
  "item 4. Regenerate: cd frontend && UPDATE_GOLDEN=1 npm run test. Consumed by " +
  "tests/test_model.py (validates each entry through pydantic).";

describe("frontend↔backend scenario mirror", () => {
  it("edit_helper_outputs.json matches the committed golden", () => {
    checkGolden("edit_helper_outputs.json", { $comment: COMMENT, cases: helperCases() });
  });

  it("edit_validation_table.json matches the committed golden", () => {
    checkGolden("edit_validation_table.json", { $comment: COMMENT, cases: validationCases() });
  });

  it("every validation case has a distinct scenario", () => {
    const seen = new Set(validationCases().map((c) => JSON.stringify(c.scenario)));
    expect(seen.size).toBe(validationCases().length);
  });
});
