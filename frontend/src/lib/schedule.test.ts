import { describe, expect, it } from "vitest";

import type { Scenario } from "../types";
import { mowerColors } from "./schedule";

// mowerColors only reads scenario.mowers[].name — a minimal cast keeps these focused.
function scenarioWithMowers(names: string[]): Scenario {
  return { mowers: names.map((name) => ({ name, can_mow: [] })) } as unknown as Scenario;
}

describe("mowerColors", () => {
  it("assigns the 8 fixed slots in sorted-name order for up to 8 mowers", () => {
    // Names sort lexicographically, so use letters to keep sort order == list order.
    const names = ["A", "B", "C", "D", "E", "F", "G", "H"];
    const colors = mowerColors(scenarioWithMowers([...names].reverse()));
    names.forEach((name, i) => expect(colors.get(name)).toBe(`var(--series-${i + 1})`));
  });

  it("cycles back to slot 1 for a 9th+ mower rather than inventing a 9th hue", () => {
    const names = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"];
    const colors = mowerColors(scenarioWithMowers(names));
    expect(colors.get("I")).toBe("var(--series-1)"); // 9th
    expect(colors.get("J")).toBe("var(--series-2)"); // 10th
    expect(colors.get("K")).toBe("var(--series-3)"); // 11th
  });
});
