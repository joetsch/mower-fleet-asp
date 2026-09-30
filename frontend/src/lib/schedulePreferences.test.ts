import { describe, expect, it } from "vitest";

import {
  NO_PREFERENCES,
  type PlanTask,
  agreementSentence,
  isInPayload,
  preferencesFor,
  releasedCounts,
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
  solved: { start: 2, end: 17, mower: "M1" },
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
    // "Solve from scratch", so the plan is discarded rather than sent and ignored.
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
    // A re-solve at this setting must reach the wire the same as "Solve from scratch".
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

describe("releasedCounts", () => {
  // The counterpart to toPreferences' payload -- what the backend cannot otherwise see,
  // since a release is the absence of a task, not a value in the payload (2026-09-24).
  it("counts released tasks per area, ignoring kept ones", () => {
    const tasks = [
      task({ uid: "t0", area: "A1", pin: "released" }),
      task({ uid: "t1", area: "A1", pin: "released" }),
      task({ uid: "t2", area: "A2", pin: "released" }),
      task({ uid: "t3", area: "A1" }), // kept
    ];
    expect(releasedCounts(tasks)).toEqual({ A1: 2, A2: 1 });
  });

  it("is empty when nothing was released", () => {
    expect(releasedCounts(plan)).toEqual({});
  });

  it("does not count a released task that was added -- it never had a solved position", () => {
    const added = task({ uid: "t9", area: "A1", added: true, pin: "released" });
    expect(releasedCounts([added])).toEqual({});
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

/** The top-level counters are the sum over the origin buckets — `preference_agreement`
 *  increments both together, so a fixture that sets only `by_origin` describes a report
 *  the backend cannot produce. Derive them here unless a case overrides them explicitly;
 *  the audit found an unreachable fixture hiding a real defect, so this matters. */
const report = (over: Partial<PreferenceReport["agreement"]> = {}): PreferenceReport => {
  const buckets = Object.values(over.by_origin ?? {});
  const sum = (f: "total" | "time_kept" | "mower_total" | "mower_kept") =>
    buckets.reduce((n, b) => n + (b?.[f] ?? 0), 0);
  return {
    agreement: {
      total: sum("total"),
      time_kept: sum("time_kept"),
      mower_total: sum("mower_total"),
      mower_kept: sum("mower_kept"),
      by_origin: {},
      ...over,
    },
    dropped: [],
    level: "top",
    pref_level: 6,
  };
};

describe("summariseAgreement", () => {
  it("splits the user's own edits from the whole preference set", () => {
    const r = report({
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 3 },
        frozen: { total: 44, time_kept: 41, mower_total: 44, mower_kept: 44 },
      },
    });
    expect(summariseAgreement(r)).toEqual({
      edits: { kept: 3, total: 3 },
      overall: { kept: 44, total: 47 },
      editsOnAnotherMower: 0,
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

  it("counts an edit that kept its hour on another mower, and only edits", () => {
    const r = report({
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 1 },
        frozen: { total: 5, time_kept: 5, mower_total: 5, mower_kept: 3 },
      },
    });
    expect(summariseAgreement(r)?.editsOnAnotherMower).toBe(2);
  });

  // The derivation leans on a backend invariant — `mower_kept <= time_kept`, because the
  // mower half is keyed on the requested hour. Should that ever stop holding, report
  // nothing rather than a negative or silently wrong count.
  it("is null for the mower count when mower_kept exceeds time_kept", () => {
    const r = report({
      by_origin: { edited: { total: 3, time_kept: 2, mower_total: 3, mower_kept: 3 } },
    });
    expect(summariseAgreement(r)?.editsOnAnotherMower).toBeNull();
  });

  it("is null for the mower count when an edit named no mower", () => {
    const r = report({
      by_origin: { edited: { total: 2, time_kept: 2, mower_total: 1, mower_kept: 1 } },
    });
    expect(summariseAgreement(r)?.editsOnAnotherMower).toBeNull();
  });

  it("is null when the solve carried no preferences at all", () => {
    expect(summariseAgreement(null)).toBeNull();
    expect(summariseAgreement(report())).toBeNull();
  });
});

describe("agreementSentence", () => {
  // Two quantities, because they answer different questions: what happened to what I
  // explicitly asked for, and how much of everything I sent survived. The second is the
  // optimisation score itself — a re-solve is MaxSAT over the preferences, and untouched
  // services are preferences too, just implicit ones (ADR-0051).
  it("reports the user's own edits and the overall preference count", () => {
    const r = report({
      total: 47,
      time_kept: 44,
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 3 },
        frozen: { total: 44, time_kept: 41, mower_total: 44, mower_kept: 44 },
      },
    });
    expect(agreementSentence(r)).toBe(
      "All 3 of your edits kept · 44 of 47 preferences kept overall",
    );
  });

  it("counts the shortfall plainly when an edit did not survive", () => {
    const r = report({
      by_origin: { edited: { total: 3, time_kept: 2, mower_total: 3, mower_kept: 2 } },
    });
    expect(agreementSentence(r)).toBe("2 of 3 of your edits kept");
  });

  // The mower half is keyed on the *requested* hour in the backend
  // (`preferences.py::preference_agreement`), so `mower_kept` implies `time_kept`. Summing
  // `mower_total - mower_kept` therefore mixed "kept the hour, different mower" with
  // "missed the hour entirely" and labelled the sum with the first. Found while writing
  // the TAASP Fig. 1 caption: the shipped line read "0 of 2 of your edits kept · 2 kept
  // the hour but changed mower" about the same two edits.
  it("never says an edit kept its hour when it missed the hour entirely", () => {
    const r = report({
      by_origin: { edited: { total: 2, time_kept: 0, mower_total: 2, mower_kept: 0 } },
    });
    expect(agreementSentence(r)).not.toMatch(/kept the hour/);
    expect(agreementSentence(r)).toBe("0 of 2 of your edits kept");
  });

  it("reports an edit that kept its hour but landed on another mower", () => {
    const r = report({
      by_origin: { edited: { total: 2, time_kept: 2, mower_total: 2, mower_kept: 1 } },
    });
    expect(agreementSentence(r)).toBe(
      "All 2 of your edits kept · 1 of those on a different mower",
    );
  });

  // Mower churn outside the user's own edits is visible in the bar colours and was never
  // what the line is for; reporting it invited the reader to attach it to their edits.
  it("does not report mower changes among services the user did not touch", () => {
    const r = report({
      total: 5,
      time_kept: 5,
      by_origin: { frozen: { total: 5, time_kept: 5, mower_total: 5, mower_kept: 3 } },
    });
    expect(agreementSentence(r)).toBe("5 of 5 preferences kept overall");
  });

  // `time_kept - mower_kept` is only the different-mower count when every preference named
  // a mower. A time-only preference ("keep the hour, any mower", ADR-0031) breaks that, so
  // the clause is dropped rather than guessed.
  it("omits the mower clause when an edit did not name a mower", () => {
    const r = report({
      by_origin: { edited: { total: 2, time_kept: 2, mower_total: 1, mower_kept: 0 } },
    });
    expect(agreementSentence(r)).toBe("All 2 of your edits kept");
  });

  it("is null when there is nothing to report", () => {
    expect(agreementSentence(null)).toBeNull();
  });

  // Owner report, 2026-09-24: the default view showed "N of M preferences kept overall"
  // after every re-solve, including a plain Move forward where the user made no edits at
  // all -- confusing, since nothing was explicitly asked for. The clause is expert-only.
  it("drops the overall clause when showOverall is false", () => {
    const r = report({
      total: 47,
      time_kept: 44,
      by_origin: {
        edited: { total: 3, time_kept: 3, mower_total: 3, mower_kept: 3 },
        frozen: { total: 44, time_kept: 41, mower_total: 44, mower_kept: 44 },
      },
    });
    expect(agreementSentence(r, false)).toBe("All 3 of your edits kept");
  });

  it("is null with showOverall false when the only thing to report was the overall count", () => {
    // A plain roll: no edits of the user's own, only frozen (implicit) preferences.
    const r = report({
      total: 5,
      time_kept: 5,
      by_origin: { frozen: { total: 5, time_kept: 5, mower_total: 5, mower_kept: 3 } },
    });
    expect(agreementSentence(r, false)).toBe("");
  });
});
