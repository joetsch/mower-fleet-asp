// Client-side scenario validation (ADR-0024) — a hand mirror of the `model.py` rules,
// used to give inline field errors and gate the Solve button while editing. The server's
// 422 stays the authority; this is for immediate feedback, not correctness.

import type { Interval, Scenario } from "../types";

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

/** Hours-of-day covered by `intervals`, honouring the past-midnight wrap
 * (mirror of `model.py::_covered_hours`). */
function coveredHours(intervals: Interval[]): Set<number> {
  const hours = new Set<number>();
  for (const [start, end] of intervals) {
    if (start < end) {
      for (let h = start; h < end; h++) hours.add(h);
    } else {
      for (let h = start; h < 24; h++) hours.add(h);
      for (let h = 0; h < end; h++) hours.add(h);
    }
  }
  return hours;
}

export interface FieldError {
  /** Dotted path to the offending value, e.g. "areas.H1_FW.min_interval". */
  path: string;
  message: string;
}

const isInt = (v: number): boolean => Number.isInteger(v);

/** All field errors in `s`. Empty means the draft is safe to send. */
export function validateScenario(s: Scenario): FieldError[] {
  const errors: FieldError[] = [];
  const add = (path: string, message: string) => errors.push({ path, message });

  const names = s.areas.map((a) => a.name);
  const dupNames = new Set(names.filter((n, i) => names.indexOf(n) !== i));
  const mowerNames = s.mowers.map((m) => m.name);
  const dupMowers = new Set(mowerNames.filter((n, i) => mowerNames.indexOf(n) !== i));

  for (const a of s.areas) {
    const at = `areas.${a.name}`;
    if (!a.name.trim()) add(`${at}.name`, "name can't be empty");
    else if (dupNames.has(a.name)) add(`${at}.name`, "area names must be unique");
    if (!isInt(a.hole) || a.hole < 1) add(`${at}.hole`, "hole must be a whole number ≥ 1");
    if (!isInt(a.priority) || a.priority < 1 || a.priority > 3)
      add(`${at}.priority`, "priority must be 1, 2 or 3");
    if (!isInt(a.min_interval) || a.min_interval <= 0)
      add(`${at}.min_interval`, "min interval must be a whole number > 0");
    if (!isInt(a.max_interval) || a.max_interval <= 0)
      add(`${at}.max_interval`, "max interval must be a whole number > 0");
    if (a.min_interval > a.max_interval)
      add(`${at}.min_interval`, "min interval must be ≤ max interval");
    if (a.size_m2 != null && (!isInt(a.size_m2) || a.size_m2 <= 0))
      add(`${at}.size_m2`, "size must be a whole number > 0");
    if (a.min_services != null && (!isInt(a.min_services) || a.min_services < 1))
      add(`${at}.min_services`, "min services must be a whole number ≥ 1");
    if (a.max_services != null && (!isInt(a.max_services) || a.max_services < 1))
      add(`${at}.max_services`, "max services must be a whole number ≥ 1");
    if (a.min_services != null && a.max_services != null && a.min_services > a.max_services)
      add(`${at}.min_services`, "min services must be ≤ max services");

    // Paths carry the weekday since ADR-0029: a window can live on some weekdays and not
    // others, and `model.py` validates each `DaySchedule` separately, so an error belongs
    // to one (weekday, kind, index) — not to a row of a Monday template.
    for (const day of WEEKDAYS) {
      const sched = a.schedule[day];
      if (!sched) continue;
      for (const kind of ["no_go", "avoid"] as const) {
        sched[kind].forEach(([start, end], i) => {
          if (start < 0 || start > 23 || end < 0 || end > 23)
            add(`${at}.schedule.${day}.${kind}.${i}`, "hours must be 0–23");
          if (start === end)
            add(`${at}.schedule.${day}.${kind}.${i}`, "start and end can't be equal");
        });
      }
      if (coveredHours(sched.no_go).size >= 24)
        add(`${at}.schedule.${day}.no_go`, "no-go can't cover the whole day");
    }
  }

  for (const m of s.mowers) {
    if (!m.name.trim()) add(`mowers.${m.name}.name`, "name can't be empty");
    else if (dupMowers.has(m.name)) add(`mowers.${m.name}.name`, "mower names must be unique");
    if (
      m.area_capacity_m2_per_day != null &&
      (!isInt(m.area_capacity_m2_per_day) || m.area_capacity_m2_per_day <= 0)
    )
      add(`mowers.${m.name}.area_capacity_m2_per_day`, "rate must be a whole number > 0");
  }

  // Every area needs at least one capable mower (else the solve is UNSAT).
  const covered = new Set(s.mowers.flatMap((m) => m.can_mow));
  for (const a of s.areas) {
    if (!covered.has(a.name)) add(`areas.${a.name}.capability`, "no mower can service this area");
  }

  // Exactly one history event per area (mirrors model.py). The editor's helpers keep this
  // true; the one path that can break it is adding an area while there are no mowers.
  const withHistory = new Set(s.history.map((h) => h.area));
  for (const a of s.areas) {
    if (!withHistory.has(a.name))
      add(`history.${a.name}`, "this area has no service history event");
  }

  // base_durations must cover exactly the capable pairs (mirrors model.py).
  if (s.base_durations != null) {
    const have = new Set(s.base_durations.map((d) => `${d.area} ${d.mower}`));
    const want = new Set(
      s.mowers.flatMap((m) => m.can_mow.map((area) => `${area} ${m.name}`)),
    );
    if (have.size !== want.size || [...want].some((k) => !have.has(k)))
      add("base_durations", "durations must cover exactly the capable (area, mower) pairs");
  }

  for (const d of s.base_durations ?? []) {
    if (!isInt(d.hours) || d.hours <= 0)
      add(`durations.${d.area}.${d.mower}`, "duration must be a whole number of hours > 0");
  }

  for (const h of s.history) {
    const at = `history.${h.area}`;
    if (!isInt(h.start)) add(`${at}.start`, "start must be a whole number of hours");
    if (!isInt(h.completion)) add(`${at}.completion`, "completion must be a whole number of hours");
    if (h.completion < h.start)
      add(`${at}.completion`, "completion can't be before start");
  }

  return errors;
}

/** The subset of errors under a given dotted path prefix — for showing one field's error. */
export function errorFor(errors: FieldError[], path: string): string | undefined {
  return errors.find((e) => e.path === path)?.message;
}
