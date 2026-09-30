// Shared helpers for turning hour-offsets into human-readable weekday/clock labels,
// and for the stable mower -> colour-slot mapping.

import type { Scenario, Violation } from "../types";

export const DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** Full weekday names — the keys of `Area.schedule`. */
export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

/** Weekday + clock for an hour offset from t=0, given which hour-of-week t=0 is. */
export function clockLabel(offset: number, horizonStartHour: number): string {
  const abs = horizonStartHour + offset;
  const dayIndex = (((Math.floor(abs / 24) % 7) + 7) % 7) as number;
  const hourOfDay = (((abs % 24) + 24) % 24) as number;
  return `${DAYS_SHORT[dayIndex]} ${String(hourOfDay).padStart(2, "0")}:00`;
}

/** Offset of the first midnight at or after t=0. */
export function firstMidnightOffset(horizonStartHour: number): number {
  return (24 - (horizonStartHour % 24)) % 24;
}

/** Offset of the first midnight at or after an arbitrary (possibly negative) hour offset
 * — the generalisation of `firstMidnightOffset`, which is just this at `offset = 0`.
 * Used for the History tab's strip, whose domain starts before t=0. */
export function firstMidnightAtOrAfter(offset: number, horizonStartHour: number): number {
  const abs = horizonStartHour + offset;
  const rem = ((abs % 24) + 24) % 24;
  return offset + ((24 - rem) % 24);
}

/** Weekday name for the day that starts at the given midnight offset. */
export function weekdayAtMidnight(midnightOffset: number, horizonStartHour: number): string {
  const abs = horizonStartHour + midnightOffset;
  const dayIndex = ((Math.floor(abs / 24) % 7) + 7) % 7;
  return DAYS_SHORT[dayIndex];
}

/** The structural cap on a colorblind-safe categorical palette (dataviz skill) — a 9th
 *  hue is never invented, so slots repeat past this count instead. */
const MOWER_COLOR_SLOTS = 8;

/**
 * Assign each mower a categorical colour slot in a fixed order. Mower 9+ repeats a
 * slot (`(i % 8) + 1`) rather than folding into "Other" — the mower name is always
 * shown alongside the colour (hover text, legend), so a repeat is a scan aid, not the
 * only identifier.
 */
export function mowerColors(scenario: Scenario): Map<string, string> {
  const names = [...scenario.mowers].map((m) => m.name).sort();
  const map = new Map<string, string>();
  names.forEach((name, i) => map.set(name, `var(--series-${(i % MOWER_COLOR_SLOTS) + 1})`));
  return map;
}

/** Area priority in words for the UI. The model stores 1–3 with 1 = highest (the encoding
 *  maps priority P to weak-constraint level 5−P, ADR-0012); the numbers mean nothing to a
 *  greenkeeper, so the UI shows High / Medium / Low. */
export const PRIORITY_LABEL: Record<number, string> = { 1: "High", 2: "Medium", 3: "Low" };

/** One hue, light → dark (dataviz skill: an ordinal tier takes a sequential ramp, not a
 *  categorical one) — darker/more saturated reads as "more important". Falls back to the
 *  lowest step for anything outside 1–3 (validated range, but the Gantt chart draws
 *  whatever `Scenario.areas` holds without re-checking it). */
export const PRIORITY_COLOR: Record<number, string> = {
  1: "var(--priority-high)",
  2: "var(--priority-medium)",
  3: "var(--priority-low)",
};

export const VIOLATION_LABEL: Record<Violation["kind"], string> = {
  max_interval: "Max-interval exceeded",
  min_interval: "Min-interval undercut",
  avoid_zone: "Worked in an avoid window",
};

export const VIOLATION_COLOR: Record<Violation["kind"], string> = {
  max_interval: "var(--status-critical)",
  min_interval: "var(--status-warning)",
  avoid_zone: "var(--status-serious)",
};

/** Plain-English span for an elapsed hour count: "29h", or "1d 5h" once it clears a day. */
export function durationLabel(hours: number): string {
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  const rem = hours % 24;
  return rem === 0 ? `${days}d` : `${days}d ${rem}h`;
}
