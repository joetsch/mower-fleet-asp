// Default values the editor fills in so a scenario is not all-manual (ADR-0024/0027).
// These MIRROR backend formulas / data by hand and can drift — the server stays the
// authority (see docs/known-hazards.md).

/** Default productive mowing hours for one (area, mower) pair, from area size and the
 * mower's rate. Mirrors `generator/duration.py::base_duration` WITHOUT the complexity
 * multiplier (complexity is a generator-internal knob, not a user concept). Always >= 1. */
export function defaultDurationHours(sizeM2: number, ratePerDay: number): number {
  if (ratePerDay <= 0) return 1;
  return Math.max(1, Math.ceil(sizeM2 / (ratePerDay / 24)));
}

/** Base duration when a pair is made capable but the area size or the mower rate is
 * unknown (only authored scenarios; every generated one carries both). */
export const FALLBACK_DURATION_H = 4;
