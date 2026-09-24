import { describe, expect, it } from "vitest";

import {
  conflictText,
  droppedEdits,
  explanationSentence,
  minimalCaveat,
  reinstatedSentence,
  rippleSentence,
} from "./explanation";
import type {
  ConflictingTask,
  EditExplanation,
  PreferredTask,
  ReinstatedService,
  RippleMove,
  Schedule,
  SolvePreferences,
} from "../types";

const conflict = (over: Partial<ConflictingTask> = {}): ConflictingTask => ({
  area: "Green 3",
  start: 10,
  mower: "M1",
  ...over,
});

const edit = (over: Partial<EditExplanation> = {}): EditExplanation => ({
  area: "Green 3",
  start: 10,
  mower: "M1",
  outcome: "conflicts_with",
  detail: "conflicts with 1 kept task(s)",
  conflicts: [],
  minimal: true,
  solve_time_s: 0.01,
  ...over,
});

describe("explanationSentence", () => {
  // One sentence per outcome. Wording must never claim uniqueness ("the reason") —
  // the literature note (§5.4) measured several distinct minimal conflicts for the same
  // instance, so "a reason" is the honest claim, not a hedge.
  it("names a conflict count for blocked_statically and conflicts_with, never 'the' reason", () => {
    const one = explanationSentence(edit({ outcome: "conflicts_with", conflicts: [conflict()] }));
    expect(one).not.toMatch(/\bthe reason\b/i);
    expect(one).toMatch(/conflicts with/i);

    const many = explanationSentence(
      edit({ outcome: "blocked_statically", conflicts: [conflict(), conflict({ start: 20 })] }),
    );
    expect(many).toMatch(/2 kept tasks/);
  });

  it("never implies not_determined means impossible", () => {
    const s = explanationSentence(edit({ outcome: "not_determined", conflicts: [] }));
    expect(s).not.toMatch(/impossible/i);
    expect(s.toLowerCase()).toContain("ran out of time");
  });

  it("individually_impossible reads as a hard structural fact, not a conflict", () => {
    const s = explanationSentence(edit({ outcome: "individually_impossible", conflicts: [] }));
    expect(s.toLowerCase()).toContain("no schedule could ever place this");
  });

  it("not_yet_found points at the search, not at the edit being wrong", () => {
    const s = explanationSentence(edit({ outcome: "not_yet_found", conflicts: [] }));
    expect(s.toLowerCase()).toContain("had not found it yet");
  });

  // "the search had not found it yet" rests on preferences outranking every service-quality
  // level, which is only true at `top`. At the expert settings the optimiser may trade an
  // edit away for plan quality at a *proven* optimum, so blaming the search is false — and
  // in heuristic mode there is no preference objective at all.
  it("does not blame the search when the setting lets quality outrank the edit", () => {
    for (const level of ["high", "low", "avoid", "tiebreak"] as const) {
      const s = explanationSentence(edit({ outcome: "not_yet_found", conflicts: [] }), level);
      expect(s.toLowerCase(), level).not.toContain("had not found it yet");
      expect(s.toLowerCase(), level).toContain("exist");
    }
  });

  it("keeps the search wording at the default setting", () => {
    const s = explanationSentence(edit({ outcome: "not_yet_found", conflicts: [] }), "top");
    expect(s.toLowerCase()).toContain("had not found it yet");
  });
});

describe("conflictText", () => {
  it("names area, clock label, and mower when present", () => {
    expect(conflictText([conflict({ area: "Green 3", start: 10, mower: "M1" })], 6)).toBe(
      "Green 3 Mon 16:00 (M1)",
    );
  });

  it("omits the parenthetical when a conflict has no mower", () => {
    expect(conflictText([conflict({ mower: null })], 6)).toBe("Green 3 Mon 16:00");
  });

  it("joins several conflicts with a comma", () => {
    const text = conflictText(
      [conflict({ area: "A", mower: null }), conflict({ area: "B", start: 11, mower: null })],
      0,
    );
    expect(text).toBe("A Mon 10:00, B Mon 11:00");
  });
});

describe("minimalCaveat", () => {
  // The keep-on-timeout degradation rule: a conflict is only ever reported larger than
  // necessary, never wrong — so the caveat must never claim the conflict is invalid.
  it("is null when the conflict is proven minimal", () => {
    expect(minimalCaveat(edit({ outcome: "conflicts_with", minimal: true }))).toBeNull();
  });

  it("flags an unproven-minimal conflict without disputing it", () => {
    const caveat = minimalCaveat(edit({ outcome: "conflicts_with", minimal: false }));
    expect(caveat).not.toBeNull();
    expect(caveat).not.toMatch(/wrong|invalid/i);
  });

  it("is null for outcomes minimisation never runs on", () => {
    expect(minimalCaveat(edit({ outcome: "blocked_statically", minimal: false }))).toBeNull();
    expect(minimalCaveat(edit({ outcome: "not_determined", minimal: false }))).toBeNull();
  });
});

describe("reinstatedSentence", () => {
  const svc = (over: Partial<ReinstatedService> = {}): ReinstatedService => ({
    area: "Green 3",
    min_services: 2,
    submitted_count: 1,
    actual_count: 2,
    forced_by_minimum: true,
    ...over,
  });

  it("names the minimum and how many the solver added back", () => {
    const s = reinstatedSentence(svc());
    expect(s).toContain("2");
    expect(s.toLowerCase()).toContain("at least");
  });

  it("pluralises 'service' correctly at min_services = 1", () => {
    expect(reinstatedSentence(svc({ min_services: 1 }))).not.toMatch(/1 services\b/);
  });

  // The backend fires this row on a bare count mismatch; only `forced_by_minimum` says
  // the floor actually bound. Naming the minimum otherwise asserts a cause nobody checked
  // — and the common case (releasing a service from an area already at its minimum) is
  // exactly the unforced one.
  it("does not blame the minimum when the minimum was already met", () => {
    const s = reinstatedSentence(
      svc({ min_services: 2, submitted_count: 3, actual_count: 4, forced_by_minimum: false }),
    );
    expect(s.toLowerCase()).not.toContain("at least");
    expect(s.toLowerCase()).not.toContain("needs");
    expect(s).toContain("1");
  });

  // `ExplanationPanel` already prefixes the row with the area name.
  it("does not repeat the area name the panel already prints", () => {
    expect(reinstatedSentence(svc())).not.toContain("Green 3");
  });

  // Owner report, pre-workshop review 2026-09-24: `submitted_count` is now the plan
  // *before* the re-solve (payload + released, ADR-0048 amendment), so it must never be
  // worded as something the user asked for -- a released service is the opposite of that.
  it("never says 'you asked for', in either variant", () => {
    expect(reinstatedSentence(svc({ forced_by_minimum: true }))).not.toMatch(/asked for/);
    expect(
      reinstatedSentence(svc({ forced_by_minimum: false, submitted_count: 3, actual_count: 4 })),
    ).not.toMatch(/asked for/);
  });

  it("says 'the plan had N' when the minimum forced it", () => {
    const s = reinstatedSentence(svc({ forced_by_minimum: true, submitted_count: 4 }));
    expect(s).toContain("the plan had 4");
  });

  // "back in the plan" implies a removal-then-return, which is not true of a genuinely
  // new addition (an untouched area the user never released from at all).
  it("never says 'back in the plan' for an unforced addition", () => {
    const s = reinstatedSentence(
      svc({ forced_by_minimum: false, submitted_count: 3, actual_count: 4 }),
    );
    expect(s).not.toMatch(/back in the plan/);
    expect(s).toContain("not in the plan before");
  });
});

describe("rippleSentence", () => {
  const move = (over: Partial<RippleMove> = {}): RippleMove => ({
    area: "Green 4",
    start: 5,
    mower: "M2",
    shares_mower_with: null,
    shares_area_with: null,
    ...over,
  });

  it("attributes a mower-sharing ripple to the named edited area", () => {
    const s = rippleSentence(move({ shares_mower_with: "Green 3" }));
    expect(s).toContain("Green 3");
    expect(s.toLowerCase()).toContain("mower");
  });

  it("attributes an area-sharing ripple without naming a mower cause", () => {
    const s = rippleSentence(move({ shares_area_with: "Green 4" }));
    expect(s.toLowerCase()).toContain("this area");
  });

  it("composes both causes when both apply", () => {
    const s = rippleSentence(
      move({ shares_mower_with: "Green 3", shares_area_with: "Green 4" }),
    );
    expect(s).toContain("Green 3");
    expect(s.toLowerCase()).toContain("and");
  });
});

describe("droppedEdits", () => {
  const pref = (over: Partial<PreferredTask> = {}): PreferredTask => ({
    area: "Green 3",
    start: 10,
    mower: "M1",
    origin: "edited",
    ...over,
  });
  const schedule = (starts: [string, number][]): Schedule => ({
    tasks: starts.map(([area, start]) => ({
      area,
      task: 1,
      mower: "M1",
      start,
      end: start + 5,
    })),
    cost: [],
    violations: [],
  });

  const preferences = (tasks: PreferredTask[]): SolvePreferences => ({
    tasks,
    mode: "weak",
    level: "top",
  });

  it("returns an edited/added preference whose (area, start) is not in the schedule", () => {
    const dropped = droppedEdits(preferences([pref()]), schedule([]));
    expect(dropped).toEqual([pref()]);
  });

  it("excludes a preference that was kept", () => {
    const dropped = droppedEdits(preferences([pref()]), schedule([["Green 3", 10]]));
    expect(dropped).toEqual([]);
  });

  it("never reports a frozen (untouched) task as a dropped edit", () => {
    const dropped = droppedEdits(preferences([pref({ origin: "frozen" })]), schedule([]));
    expect(dropped).toEqual([]);
  });

  it("returns [] with no schedule to compare against", () => {
    expect(droppedEdits(preferences([pref()]), null)).toEqual([]);
  });
});
