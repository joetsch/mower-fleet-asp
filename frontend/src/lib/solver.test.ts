import { describe, expect, it } from "vitest";

import { costReadout } from "./solver";

/**
 * Where the preference weak constraints sit decides whether the unmet-preference count can
 * be read off the cost vector at all.
 *
 * At `top` they get a priority level of their own above every service-quality level, so a
 * preference-bearing solve returns a vector one slot longer than a cold one (pinned
 * backend-side by `test_end_to_end.py::test_the_weak_overlay_adds_exactly_one_cost_slot`);
 * at `tiebreak` the extra slot is trailing instead. At `high` / `low` / `avoid` they share
 * a level with the service objective, so clingo *sums* the two into one slot and there is
 * nothing to split — rendering the raw vector there would silently pass off a churn count
 * as schedule quality, the trap `docs/known-hazards.md` records.
 */
describe("costReadout", () => {
  it("passes a cold solve's vector through untouched", () => {
    expect(costReadout([1, 0, 3, 0, 2], null)).toEqual({
      unmetEdits: null,
      quality: [1, 0, 3, 0, 2],
      folded: false,
    });
  });

  it("splits the leading slot off a solve that ran at top", () => {
    expect(costReadout([2, 1, 0, 3, 0, 2], "top")).toEqual({
      unmetEdits: 2,
      quality: [1, 0, 3, 0, 2],
      folded: false,
    });
  });

  it("leaves the two quality vectors directly comparable", () => {
    const cold = costReadout([1, 0, 3, 0, 2], null);
    const warm = costReadout([7, 1, 0, 3, 0, 2], "top");
    expect(warm.quality).toEqual(cold.quality);
  });

  it("splits the trailing slot off a solve that ran at tiebreak", () => {
    // `tiebreak` is pref_level -1, below min-interval@0 — so the extra slot is last.
    expect(costReadout([1, 0, 3, 0, 2, 5], "tiebreak")).toEqual({
      unmetEdits: 5,
      quality: [1, 0, 3, 0, 2],
      folded: false,
    });
  });

  it("reports no splittable count at a level shared with the service objective", () => {
    for (const level of ["high", "low", "avoid"] as const) {
      expect(costReadout([1, 0, 3, 0, 2], level)).toEqual({
        unmetEdits: null,
        quality: [1, 0, 3, 0, 2],
        folded: true,
      });
    }
  });

  it("does not invent a slot to split when the vector is empty", () => {
    expect(costReadout([], "top")).toEqual({ unmetEdits: null, quality: [], folded: false });
  });
});
