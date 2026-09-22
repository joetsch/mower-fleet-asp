// Availability windows: the projection between how they are *stored* and how they are
// *edited*, plus the per-hour resolution the strips and the Gantt draw from.
//
// Stored (`model.py::Area.schedule`): seven independent `DaySchedule`s, each a pair of
// `no_go` / `avoid` interval lists. Weekday lives in the dict key; an interval carries no
// identity of its own.
//
// Edited (ADR-0029): one row per *distinct interval*, carrying the set of weekdays it
// applies to — "avoid 08–20, Mon–Fri" is one row, not five. `windowRows` derives that
// view and `applyWindowRows` writes it back; they are the only pair that has to agree,
// so every availability edit funnels through them.
//
// The round-trip is MEANING-preserving, not byte-preserving, in exactly two cases:
//
//   * a duplicate interval within one day's list collapses to one row;
//   * a day's list order is normalised to the global row order.
//
// Both are safe because order and duplication are inert on the read side: `_in_intervals`
// (`model.py:59-66`) is a disjunction and `_covered_hours` (`model.py:41-50`) a set union.
// Neither is reachable from the UI — only from a hand-written curated JSON.
//
// Availability is strictly PER HOUR OF WEEK (ADR-0014): a `(22, 6)` window on Monday
// blocks Monday's 22, 23 *and Monday's 00–05*. It does not spill into Tuesday. The editor
// spells the resolved hours out per row so the wrap cannot be misread.

import { WEEKDAYS } from "./schedule";
import type { Area, Interval, Scenario } from "../types";

export type Weekday = (typeof WEEKDAYS)[number];
export type WindowKind = "no_go" | "avoid";

/** One editable window: an interval plus the weekdays it applies to. */
export interface WindowRow {
  kind: WindowKind;
  start: number;
  end: number;
  /** The weekdays this interval appears on, in Monday..Sunday order. Never empty. */
  days: Weekday[];
  /** Index within *that* weekday's own `kind` list — the key validation errors carry. */
  at: Partial<Record<Weekday, number>>;
}

/** A contiguous run of hours in one availability state, in hour offsets from t = 0. */
export interface Band {
  state: WindowKind;
  from: number;
  to: number;
}

const KINDS: readonly WindowKind[] = ["no_go", "avoid"] as const;

/** True when `hourOfDay` falls in any of `intervals`, honouring the same-day wrap. */
export function inIntervals(hourOfDay: number, intervals: Interval[]): boolean {
  return intervals.some(([s, e]) =>
    s < e ? s <= hourOfDay && hourOfDay < e : hourOfDay >= s || hourOfDay < e,
  );
}

/**
 * The area's windows as editor rows: all `no_go` rows, then all `avoid` rows, each kind
 * in first-appearance order walking Monday→Sunday. A weekday-uniform area therefore
 * yields exactly the rows the editor has always shown, in the same order, with all seven
 * days set — which is what keeps the `scenarioEdits` helpers' indices back-compatible.
 */
export function windowRows(area: Area): WindowRow[] {
  const out: WindowRow[] = [];
  for (const kind of KINDS) {
    const byInterval = new Map<string, WindowRow>();
    for (const day of WEEKDAYS) {
      const list = area.schedule[day]?.[kind] ?? [];
      list.forEach(([start, end], i) => {
        const key = `${start}-${end}`;
        let row = byInterval.get(key);
        if (!row) {
          row = { kind, start, end, days: [], at: {} };
          byInterval.set(key, row);
          out.push(row);
        }
        // A repeat of the same interval within one day is inert; keep the first index.
        if (row.at[day] === undefined) {
          row.days.push(day);
          row.at[day] = i;
        }
      });
    }
  }
  return out;
}

/** Rebuild all seven `DaySchedule`s from `rows`. Keys stay in Monday..Sunday order — the
 * order `model.py` and the server emit, so `scenariosEqual`'s JSON compare stays honest.
 * The rows carry everything: nothing of the area they came from survives. */
export function applyWindowRows(rows: WindowRow[]): Area["schedule"] {
  return Object.fromEntries(
    WEEKDAYS.map((day) => [
      day,
      {
        no_go: rows
          .filter((r) => r.kind === "no_go" && r.days.includes(day))
          .map((r) => [r.start, r.end] as Interval),
        avoid: rows
          .filter((r) => r.kind === "avoid" && r.days.includes(day))
          .map((r) => [r.start, r.end] as Interval),
      },
    ]),
  ) as Area["schedule"];
}

/** Availability state for one hour offset from t = 0, or null when the area is free.
 * `no_go` wins over `avoid`. Resolves against *that hour's own weekday* (ADR-0014). */
function stateAt(area: Area, horizonStartHour: number, offset: number): WindowKind | null {
  const abs = horizonStartHour + offset;
  const day = WEEKDAYS[((Math.floor(abs / 24) % 7) + 7) % 7];
  const hourOfDay = ((abs % 24) + 24) % 24;
  const sched = area.schedule[day];
  if (!sched) return null;
  if (inIntervals(hourOfDay, sched.no_go)) return "no_go";
  if (inIntervals(hourOfDay, sched.avoid)) return "avoid";
  return null;
}

/**
 * The area's avoid / no-go bands over the half-open hour range `[from, to)`, coalesced
 * and expressed in absolute hour offsets from t = 0. `from` may be negative — the Gantt's
 * domain starts before t = 0, at the earliest history event.
 */
export function availabilityBands(
  area: Area,
  scenario: Scenario,
  from: number,
  to: number,
): Band[] {
  const out: Band[] = [];
  let cur: Band | null = null;
  for (let offset = from; offset < to; offset++) {
    const state = stateAt(area, scenario.horizon_start_hour, offset);
    if (cur && state === cur.state) {
      cur.to = offset + 1;
      continue;
    }
    if (cur) out.push(cur);
    cur = state === null ? null : { state, from: offset, to: offset + 1 };
  }
  if (cur) out.push(cur);
  return out;
}
