import { describe, expect, it } from "vitest";

import { defaultDurationHours } from "./scenarioDefaults";

describe("defaultDurationHours", () => {
  it("matches ceil(size / (rate/24))", () => {
    expect(defaultDurationHours(5000, 8000)).toBe(15); // 5000 / 333.33 = 15.0
    expect(defaultDurationHours(3000, 8000)).toBe(9); // 3000 / 333.33 = 9.0
    expect(defaultDurationHours(100, 8000)).toBe(1); // floors at 1
  });

  it("is safe for a zero / negative rate", () => {
    expect(defaultDurationHours(5000, 0)).toBe(1);
  });
});
