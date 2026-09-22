import { describe, expect, it } from "vitest";

import {
  NO_PREFERENCES,
  type PlanTask,
  agreementSentence,
  isInPayload,
  preferencesFor,
  summariseAgreement,
  toPreferences,
} from "./schedulePreferences";
import type { PreferenceReport, PreferredTask } from "../types";

const task = (over: Partial<PlanTask> = {}): PlanTask => ({
  uid: "t0",
  area: "A1",
  mower: "M1",
  start: 2,
  end: 17,
  pin: "auto",
  edited: false,
  added: false,
  sourceTask: 1,
  ...over,
});

const plan: PlanTask[] = [
  task({ uid: "t0", area: "A1", start: 2 }),
  task({ uid: "t1", area: "A2", start: 20 }),
  task({ uid: "t2", area: "A2", start: 60 }),
];

describe("isInPayload", () => {
  // The plan is kept by default (ADR-0038): every task goes into the next re-solve's
  // payload except one the user has explicitly released. The per-task control's verb
  // turns on this — a kept task offers "release", a released one offers "keep".
  it("is every task except a released one", () => {
    expect(isInPayload(task())).toBe(true);
    expect(isInPayload(task({ edited: true }))).toBe(true);
    expect(isInPayload(task({ added: true }))).toBe(true);
    expect(isInPayload(task({ pin: "pinned" }))).toBe(true);
    expect(isInPayload(task({ pin: "released" }))).toBe(false);
  });
});

describe("toPreferences", () => {
  it("keeps every task not released, as weak@top", () => {
    const prefs = toPreferences(plan);
    expect(prefs.mode).toBe("weak");
    expect(prefs.level).toBe("top");
    expect(prefs.tasks).toEqual([
      { area: "A1", start: 2, mower: "M1", origin: "frozen" },
      { area: "A2", start: 20, mower: "M1", origin: "frozen" },
      { area: "A2", start: 60, mower: "M1", origin: "frozen" },
    ]);
  });

  it("excludes a released task", () => {
    const released = [plan[0], { ...plan[1], pin: "released" as const }, plan[2]];
    expect(toPreferences(released).tasks.map((t) => t.start)).toEqual([2, 60]);
  });

  it("emits no payload when every task is released — byte-identical to a cold solve", () => {
    // ADR-0031 decision 5: "release all" then Re-solve must reach the wire the same as
    // "Re-solve from scratch", so the plan is discarded rather than sent and ignored.
    const allReleased = plan.map((t) => ({ ...t, pin: "released" as const }));
    expect(toPreferences(allReleased)).toEqual(NO_PREFERENCES);
  });

  it("emits no payload for an empty plan", () => {
    expect(toPreferences([])).toEqual(NO_PREFERENCES);
  });

  it("carries the stability setting it is given", () => {
    // The expert-mode selector prices one churned task against the service objective; the
    // payload is otherwise identical, so only `level` moves.
    expect(toPreferences(plan, "high").level).toBe("high");
    expect(toPreferences(plan, "avoid").level).toBe("avoid");
    expect(toPreferences(plan, "high").tasks).toEqual(toPreferences(plan, "top").tasks);
  });

  it("still emits nothing at all for an empty plan whatever the setting", () => {
    // ADR-0031 decision 5 again: the level must not turn a dead payload into a live one.
    expect(toPreferences([], "avoid")).toEqual(NO_PREFERENCES);
  });

  it("carries nothing at all for the cold setting, even with a full plan", () => {
    // UI review 2026-09-10: `cold` is "no stability" — no weak constraints, no heuristic.
    // A re-solve at this setting must reach the wire the same as "Re-solve from scratch".
    expect(toPreferences(plan, "cold")).toEqual(NO_PREFERENCES);
  });

  it("switches mechanism, not level, for the heuristic setting", () => {
    // ADR-0034 decision 4 kept `mode: "heuristic"` alive specifically for the rolling use
    // case; it biases the search and leaves the objective untouched, so it is not a price
    // and carries no meaningful level.
    const prefs = toPreferences(plan, "heuristic");
    expect(prefs.mode).toBe("heuristic");
    expect(prefs.tasks).toEqual(toPreferences(plan, "top").tasks);
  });

  it("emits nothing for an empty plan under the heuristic setting too", () => {
    expect(toPreferences([], "heuristic")).toEqual(NO_PREFERENCES);
  });

  it("marks each task with its origin", () => {
    const mixed = [
      plan[0],
      { ...plan[1], edited: true },
      task({ uid: "t3", area: "A1", start: 90, added: true }),
    ];
    expect(toPreferences(mixed).tasks).toEqual([
      { area: "A1", start: 2, mower: "M1", origin: "frozen" },
      { area: "A2", start: 20, mower: "M1", origin: "edited" },
      { area: "A1", start: 90, mower: "M1", origin: "added" },
    ]);
  });
});

describe("preferencesFor", () => {
  // The Move-forward path carries `PreferredTask`s straight off the roll, never `PlanTask`s,
  // so the mechanism decision has to live somewhere both call sites reach.
  const carried: PreferredTask[] = [
    { area: "A1", start: 4, mower: "M1", origin: "frozen" },
    { area: "A2", start: 30, mower: "M2", origin: "frozen" },
  ];

  it("prices a weak setting at its level", () => {
    const prefs = preferencesFor(carried, "low");
    expect(prefs.mode).toBe("weak");
    expect(prefs.level).toBe("low");
    expect(prefs.tasks).toEqual(carried);
  });

  it("sends the heuristic mechanism for the heuristic setting", () => {
    expect(preferencesFor(carried, "heuristic").mode).toBe("heuristic");
  });

  it("sends no payload for the cold setting, dropping the carried plan", () => {
    // A Move forward at `cold` carries the still-future tasks off the roll but forwards
    // none of them — the week is re-planned from scratch each step (UI review 2026-09-10).
    expect(preferencesFor(carried, "cold")).toEqual(NO_PREFERENCES);
  });

  it("emits no payload when nothing carried", () => {
    expect(preferencesFor([], "top")).toEqual(NO_PREFERENCES);
    expect(preferencesFor([], "heuristic")).toEqual(NO_PREFERENCES);
  });
});

const report = (over: Partial<PreferenceReport["agreement"]> = {}): PreferenceReport => ({
  agreement: {
    total: 0,
    time_kept: 0,
    mower_total: 0,
    mower_kept: 0,
    by_origin: {},
    ...over,
  },
  dropped: [],
  level: "top",
  pref_level: 6,
});

describe("summariseAgreement", () => {
  it("splits the user's own edits from the rest of the plan", () => {
    const r = report({
      total: 44,
      time_kept: 44,
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 3 },
        frozen: { total: 44, time_kept: 41, mower_total: 44, mower_kept: 44 },
      },
    });
    expect(summariseAgreement(r)).toEqual({
      edits: { kept: 3, total: 3 },
      rest: { kept: 41, total: 44 },
      movedToAnotherMower: 0,
    });
  });

  it("counts added tasks as the user's own edits", () => {
    const r = report({
      by_origin: {
        edited: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 },
        added: { total: 2, time_kept: 1, mower_total: 2, mower_kept: 1 },
      },
    });
    expect(summariseAgreement(r)?.edits).toEqual({ kept: 2, total: 3 });
  });

  it("reports a kept hour on a different mower separately", () => {
    const r = report({
      by_origin: { frozen: { total: 5, time_kept: 5, mower_total: 5, mower_kept: 3 } },
    });
    expect(summariseAgreement(r)?.movedToAnotherMower).toBe(2);
  });

  it("is null when the solve carried no preferences at all", () => {
    expect(summariseAgreement(null)).toBeNull();
    expect(summariseAgreement(report())).toBeNull();
  });
});

describe("agreementSentence", () => {
  it("says all when every edit survived, and does not say 'other'", () => {
    const r = report({
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 3 },
        frozen: { total: 44, time_kept: 41, mower_total: 44, mower_kept: 44 },
      },
    });
    expect(agreementSentence(r)).toBe("All 3 of your edits kept · 41 of 44 tasks unchanged");
  });

  it("counts the shortfall plainly when an edit did not survive", () => {
    const r = report({
      by_origin: { edited: { total: 3, time_kept: 2, mower_total: 3, mower_kept: 3 } },
    });
    expect(agreementSentence(r)).toBe("2 of 3 of your edits kept");
  });

  it("adds the different-mower note when there is one", () => {
    const r = report({
      by_origin: { frozen: { total: 5, time_kept: 5, mower_total: 5, mower_kept: 3 } },
    });
    expect(agreementSentence(r)).toBe(
      "5 of 5 tasks unchanged · 2 kept the hour but changed mower",
    );
  });

  it("is null when there is nothing to report", () => {
    expect(agreementSentence(null)).toBeNull();
  });
});
