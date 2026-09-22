import { describe, expect, it } from "vitest";

import { isValidSlug, slugify } from "./slug";

describe("slugify", () => {
  it("lowercases and hyphenates runs of non-alphanumerics", () => {
    expect(slugify("My New Course")).toBe("my-new-course");
    expect(slugify("  spaced  out  ")).toBe("spaced-out");
    expect(slugify("Hole #3 (back nine)")).toBe("hole-3-back-nine");
    expect(slugify("UPPER_case__mix")).toBe("upper-case-mix");
  });

  it("leaves an existing slug alone", () => {
    expect(slugify("already-a-slug")).toBe("already-a-slug");
  });

  it("returns an empty string when nothing usable is left", () => {
    expect(slugify("")).toBe("");
    expect(slugify("!!!")).toBe("");
    expect(slugify("   ")).toBe("");
  });

  it("truncates to 64 chars without a trailing hyphen", () => {
    const s = slugify("a ".repeat(50));
    expect(s.length).toBeLessThanOrEqual(64);
    expect(s.endsWith("-")).toBe(false);
  });
});

describe("isValidSlug", () => {
  it("accepts lowercase, digits and internal hyphens", () => {
    expect(isValidSlug("well-resourced")).toBe(true);
    expect(isValidSlug("a")).toBe(true);
  });

  it("rejects uppercase, spaces, path bits and edge hyphens", () => {
    expect(isValidSlug("Bad")).toBe(false);
    expect(isValidSlug("with space")).toBe(false);
    expect(isValidSlug("../escape")).toBe(false);
    expect(isValidSlug("-lead")).toBe(false);
    expect(isValidSlug("trail-")).toBe(false);
    expect(isValidSlug("")).toBe(false);
  });
});
