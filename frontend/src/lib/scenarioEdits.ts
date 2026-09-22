// Immutable updates to a Scenario draft (ADR-0024, scenario editing).
//
// Every function returns a NEW Scenario built from a structuredClone, mutating only the
// primitive values the caller asked for. Key order is preserved, so `scenariosEqual` can
// compare with JSON.stringify — the dirty check the editor relies on.

import {
  applyWindowRows,
  type Weekday,
  type WindowKind,
  type WindowRow,
  windowRows,
} from "./availability";
import { WEEKDAYS } from "./schedule";
import { defaultDurationHours, FALLBACK_DURATION_H } from "./scenarioDefaults";
import type {
  Area,
  BaseDuration,
  Catalog,
  CatalogAreaType,
  CatalogMower,
  DaySchedule,
  Mower,
  Scenario,
  ServiceEvent,
} from "../types";

/** Deep value equality for two scenarios. Safe with JSON.stringify because the updaters
 * here only ever replace primitives inside a clone of the same object, so both operands
 * carry their keys in the same order. */
export function scenariosEqual(a: Scenario, b: Scenario): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Apply `patch` to the area named `areaName`. Areas are keyed by name (the component
 * sorts its rows, so a row index is not stable). A name with no match is a no-op. */
export function updateArea(scenario: Scenario, areaName: string, patch: Partial<Area>): Scenario {
  const next = structuredClone(scenario);
  const area = next.areas.find((a) => a.name === areaName);
  if (area) Object.assign(area, patch);
  return next;
}

/** "Drop a service" from the schedule editor (ADR-0039): lower the area's cap to one
 * below what the plan currently holds for it, so the next Re-solve produces one fewer.
 * `min_services` is clamped down so it cannot exceed the new cap. A no-op below
 * `currentCount` 2 — the model requires `max_services >= 1` (an area cannot be dropped to
 * zero services; remove the area itself for that). Callers should hide the control there
 * (`canDropService`). */
export function dropOneService(scenario: Scenario, area: string, currentCount: number): Scenario {
  const a = scenario.areas.find((x) => x.name === area);
  if (!a || currentCount < 2) return scenario;
  const nextMax = currentCount - 1;
  return updateArea(scenario, area, {
    max_services: nextMax,
    min_services: a.min_services != null ? Math.min(a.min_services, nextMax) : a.min_services,
  });
}

/** Whether "drop a service" can do anything for an area holding `currentCount` services —
 * false at 1, where the only lower cap the model allows (0) is invalid. */
export function canDropService(currentCount: number): boolean {
  return currentCount >= 2;
}

/** "Add a service" from the schedule editor (ADR-0039): raise the area's floor to one
 * above what the plan currently holds, forcing the solver to produce the extra service.
 * `max_services` is raised to match if it was below the new floor. */
export function forceOneMoreService(
  scenario: Scenario,
  area: string,
  currentCount: number,
): Scenario {
  const a = scenario.areas.find((x) => x.name === area);
  if (!a) return scenario;
  const nextMin = currentCount + 1;
  return updateArea(scenario, area, {
    min_services: nextMin,
    max_services: a.max_services != null ? Math.max(a.max_services, nextMin) : a.max_services,
  });
}

/** Set an area's type. When `catalogType` is given (the user picked a known type), also
 * adopt that type's service policy as the new defaults — priority and the interval band.
 * A free-typed / unknown type only changes the label. */
export function setAreaType(
  scenario: Scenario,
  areaName: string,
  type: string,
  catalogType?: CatalogAreaType,
): Scenario {
  return updateArea(
    scenario,
    areaName,
    catalogType
      ? {
          type: catalogType.name,
          priority: catalogType.priority,
          min_interval: catalogType.min_interval_h,
          max_interval: catalogType.max_interval_h,
        }
      : { type },
  );
}

/** Apply `patch` to the mower named `mowerName`. */
export function updateMower(
  scenario: Scenario,
  mowerName: string,
  patch: Partial<Mower>,
): Scenario {
  const next = structuredClone(scenario);
  const mower = next.mowers.find((m) => m.name === mowerName);
  if (mower) Object.assign(mower, patch);
  return next;
}


/** True when an area's 7 weekday schedules are identical (all curated scenarios are). */
export function scheduleIsUniform(area: Area): boolean {
  const ref = JSON.stringify(area.schedule.Monday);
  return WEEKDAYS.every((d) => JSON.stringify(area.schedule[d]) === ref);
}

// --- structural editing internals (ADR-0027) -----------------------------------------

/** "Area 1", "Area 2", … — the first `${base} ${n}` not already taken. */
function uniqueName(base: string, taken: string[]): string {
  const used = new Set(taken);
  for (let n = 1; ; n++) {
    const candidate = `${base} ${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** Seven identical weekdays, Monday..Sunday — the key order `model.py` and the server
 * emit, so `scenariosEqual`'s JSON compare stays honest. */
function weekSchedule(day: DaySchedule): Record<string, DaySchedule> {
  return Object.fromEntries(WEEKDAYS.map((d) => [d, structuredClone(day)]));
}

/** Every capable (area, mower) pair, in `mowers` order then `can_mow` order — the same
 * order `generator/compile.py` emits `base_durations` in. */
function capablePairs(s: Scenario): Array<{ area: string; mower: string }> {
  const pairs: Array<{ area: string; mower: string }> = [];
  for (const m of s.mowers) for (const area of m.can_mow) pairs.push({ area, mower: m.name });
  return pairs;
}

/** Rebuild `next.base_durations` so it covers EXACTLY the capable pairs (the `Scenario`
 * validator requires that). Rows that already existed in `before` keep their hours — a
 * hand-set duration is never silently recomputed; new pairs are seeded from size × rate.
 * NULL-PRESERVING: a scenario with `base_durations === null` runs the `duration_seed`
 * sampling path and must stay there. Mutates `next` in place. */
function syncBaseDurations(next: Scenario, before: Scenario): void {
  if (next.base_durations === null) return;
  const key = (area: string, mower: string) => `${area} ${mower}`;
  const known = new Map((before.base_durations ?? []).map((d) => [key(d.area, d.mower), d.hours]));
  const rows: BaseDuration[] = capablePairs(next).map(({ area, mower }) => {
    let hours = known.get(key(area, mower));
    if (hours === undefined) {
      const a = next.areas.find((x) => x.name === area);
      const m = next.mowers.find((x) => x.name === mower);
      hours =
        a?.size_m2 != null && m?.area_capacity_m2_per_day != null
          ? defaultDurationHours(a.size_m2, m.area_capacity_m2_per_day)
          : FALLBACK_DURATION_H;
    }
    return { area, mower, hours };
  });
  next.base_durations = rows.length ? rows : null;
}

// --- availability windows (ADR-0029) -------------------------------------------------
//
// A window is a ROW — an interval plus the set of weekdays it applies to — not a slot in
// a Monday template broadcast to all seven. `lib/availability.ts` owns that projection;
// every helper below goes through `editWindowRows` so there is one place where the row
// view and the stored per-day lists have to agree.
//
// `index` addresses the row within its own kind. For a weekday-uniform area that is the
// same number it always was, so these three keep their old signatures and their old
// behaviour byte for byte.

/** Apply `mutate` to an area's window rows and write them back. `mutate` returns `null`
 * to mean "no change", which leaves the schedule untouched rather than round-tripping it
 * (the round trip is meaning-preserving, but not byte-preserving — see availability.ts). */
function editWindowRows(
  scenario: Scenario,
  areaName: string,
  mutate: (rows: WindowRow[]) => WindowRow[] | null,
): Scenario {
  const next = structuredClone(scenario);
  const area = next.areas.find((a) => a.name === areaName);
  if (!area) return next;
  const rows = mutate(windowRows(area));
  if (rows === null) return next;
  area.schedule = applyWindowRows(rows);
  return next;
}

/** The `index`-th row of `kind`, or undefined. */
function rowAt(rows: WindowRow[], kind: WindowKind, index: number): WindowRow | undefined {
  return rows.filter((r) => r.kind === kind)[index];
}

/** Replace one endpoint of one existing window, on that window's own weekdays. No window
 * is added or removed, and no other weekday is touched. */
export function setIntervalEndpoint(
  scenario: Scenario,
  areaName: string,
  kind: WindowKind,
  index: number,
  end: "start" | "end",
  value: number,
): Scenario {
  return editWindowRows(scenario, areaName, (rows) => {
    const row = rowAt(rows, kind, index);
    if (!row) return null;
    if (end === "start") row.start = value;
    else row.end = value;
    return rows;
  });
}

/** Set a mower's model. With `catalogMower` (a known model picked), also adopt its
 * mowing rate as the new default (still overridable). An empty / unknown model clears it. */
export function setMowerModel(
  scenario: Scenario,
  mowerName: string,
  model: string,
  catalogMower?: CatalogMower,
): Scenario {
  return updateMower(
    scenario,
    mowerName,
    catalogMower
      ? { model: catalogMower.name, area_capacity_m2_per_day: catalogMower.area_capacity_m2_per_day }
      : { model: model || null },
  );
}

/** Set the base mowing hours for one (area, mower) pair. A pair with no existing
 * `base_durations` entry is left alone (capability edits are Phase 7). */
export function setBaseDuration(
  scenario: Scenario,
  area: string,
  mower: string,
  hours: number,
): Scenario {
  const next = structuredClone(scenario);
  const entry = next.base_durations?.find((d) => d.area === area && d.mower === mower);
  if (entry) entry.hours = hours;
  return next;
}

/** Apply `patch` to the single history event for `areaName` (there is exactly one per
 * area — `model.py` validates this). */
export function updateHistoryEvent(
  scenario: Scenario,
  areaName: string,
  patch: Partial<ServiceEvent>,
): Scenario {
  const next = structuredClone(scenario);
  const event = next.history.find((h) => h.area === areaName);
  if (event) Object.assign(event, patch);
  return next;
}

/** Apply a scenario-level patch (currently just `name` — horizon and "now" are fixed). */
export function updateScenario(scenario: Scenario, patch: Partial<Scenario>): Scenario {
  return { ...structuredClone(scenario), ...patch };
}

/** Rename an area, cascading to every reference: `Mower.can_mow`, `history[].area`,
 * `base_durations[].area`. A no-op if `newName` is blank or already taken. */
export function renameArea(scenario: Scenario, oldName: string, newName: string): Scenario {
  const name = newName.trim();
  if (!name || name === oldName || scenario.areas.some((a) => a.name === name)) return scenario;
  const next = structuredClone(scenario);
  for (const a of next.areas) if (a.name === oldName) a.name = name;
  for (const m of next.mowers) m.can_mow = m.can_mow.map((a) => (a === oldName ? name : a));
  for (const h of next.history) if (h.area === oldName) h.area = name;
  for (const d of next.base_durations ?? []) if (d.area === oldName) d.area = name;
  return next;
}

/** Rename a mower, cascading to `history[].mower` and `base_durations[].mower`. */
export function renameMower(scenario: Scenario, oldName: string, newName: string): Scenario {
  const name = newName.trim();
  if (!name || name === oldName || scenario.mowers.some((m) => m.name === name)) return scenario;
  const next = structuredClone(scenario);
  for (const m of next.mowers) if (m.name === oldName) m.name = name;
  for (const h of next.history) if (h.mower === oldName) h.mower = name;
  for (const d of next.base_durations ?? []) if (d.mower === oldName) d.mower = name;
  return next;
}

/** Add or remove one (area, mower) capability. `base_durations` is kept in exact sync
 * with the capable pairs by `syncBaseDurations` (the `Scenario` validator requires that,
 * and a `null` — sampling-path — scenario stays `null`). Disabling a pair that a history
 * event used reassigns that event to another capable mower when one exists. */
export function toggleCapability(
  scenario: Scenario,
  areaName: string,
  mowerName: string,
  enabled: boolean,
): Scenario {
  const next = structuredClone(scenario);
  const mower = next.mowers.find((m) => m.name === mowerName);
  const area = next.areas.find((a) => a.name === areaName);
  if (!mower || !area) return next;

  if (enabled) {
    if (!mower.can_mow.includes(areaName)) mower.can_mow = [...mower.can_mow, areaName].sort();
  } else {
    mower.can_mow = mower.can_mow.filter((a) => a !== areaName);
    const event = next.history.find((h) => h.area === areaName && h.mower === mowerName);
    if (event) {
      const other = next.mowers.find((m) => m.name !== mowerName && m.can_mow.includes(areaName));
      if (other) event.mower = other.name;
    }
  }

  syncBaseDurations(next, scenario);
  return next;
}

// --- adding and removing whole entries (ADR-0027) ------------------------------------

/** Add a fully-wired area: `catalog.area_types[0]`, an always-free schedule, capability on
 * the catalogue-capable mowers (else the first mower), the mandatory "just serviced at
 * t = 0" history event, and its `base_durations` rows. A **no-op when there are no
 * mowers** (an area with no capable mower has no valid history event) **or no catalogue**
 * (no service-policy defaults to give the area) — the UI disables the button in both
 * cases. */
export function addArea(scenario: Scenario, catalog?: Catalog | null): Scenario {
  const t = catalog?.area_types[0];
  if (scenario.mowers.length === 0 || !t) return scenario;
  const next = structuredClone(scenario);
  const name = uniqueName(
    "Area",
    next.areas.map((a) => a.name),
  );
  const area: Area = {
    name,
    type: t.name,
    hole: Math.max(0, ...next.areas.map((a) => a.hole)) + 1,
    priority: t.priority,
    min_interval: t.min_interval_h,
    max_interval: t.max_interval_h,
    schedule: weekSchedule({ no_go: [], avoid: [] }),
    size_m2: t.default_size_m2,
    min_services: null,
    max_services: null,
  };
  next.areas.push(area);

  const catalogueCapable = (m: Mower) =>
    catalog?.mower_models
      .find((cm) => cm.name === m.model)
      ?.capable_area_types.includes(t.name) ?? false;
  let wired = next.mowers.filter(catalogueCapable).map((m) => m.name);
  if (wired.length === 0) wired = [next.mowers[0].name];
  for (const m of next.mowers) {
    if (wired.includes(m.name)) m.can_mow = [...m.can_mow, name].sort();
  }

  const firstMower = next.mowers.find((m) => m.name === wired[0])!;
  const d =
    area.size_m2 != null && firstMower.area_capacity_m2_per_day != null
      ? defaultDurationHours(area.size_m2, firstMower.area_capacity_m2_per_day)
      : FALLBACK_DURATION_H;
  next.history.push({ area: name, mower: wired[0], start: -d, completion: 0 });

  syncBaseDurations(next, scenario);
  return next;
}

/** Remove an area and every reference to it: `can_mow`, its history event, its
 * `base_durations` rows. A mower left with `can_mow: []` is kept (an idle mower is legal). */
export function deleteArea(scenario: Scenario, areaName: string): Scenario {
  if (!scenario.areas.some((a) => a.name === areaName)) return scenario;
  const next = structuredClone(scenario);
  next.areas = next.areas.filter((a) => a.name !== areaName);
  for (const m of next.mowers) m.can_mow = m.can_mow.filter((a) => a !== areaName);
  next.history = next.history.filter((h) => h.area !== areaName);
  syncBaseDurations(next, scenario);
  return next;
}

/** Add a mower with an **empty** capability row — the user ticks the grid. The model is
 * the catalogue model that covers the most area types present (ties → catalogue order);
 * with no catalogue the mower has no model or rate. */
export function addMower(scenario: Scenario, catalog?: Catalog | null): Scenario {
  const next = structuredClone(scenario);
  const name = uniqueName(
    "Mower",
    next.mowers.map((m) => m.name),
  );
  const present = new Set(next.areas.map((a) => a.type));
  const coverage = (cm: CatalogMower) =>
    cm.capable_area_types.filter((x) => present.has(x)).length;
  const best = catalog?.mower_models.length
    ? [...catalog.mower_models].sort((a, b) => coverage(b) - coverage(a))[0]
    : undefined;
  next.mowers.push({
    name,
    can_mow: [],
    model: best?.name ?? null,
    area_capacity_m2_per_day: best?.area_capacity_m2_per_day ?? null,
  });
  return next;
}

/** Areas that would be left with no capable mower if `mowerName` were removed. Non-empty
 * means the delete is blocked — such an area can't have a valid history event, so the
 * scenario would be unrepresentable, not merely unsolvable. */
export function strandedAreasWithoutMower(scenario: Scenario, mowerName: string): string[] {
  return scenario.areas
    .filter((a) => {
      const capable = scenario.mowers.filter((m) => m.can_mow.includes(a.name));
      return capable.length === 1 && capable[0].name === mowerName;
    })
    .map((a) => a.name);
}

/** How many history events `deleteMower(scenario, mowerName)` would reassign — for the
 * Undo message. */
export function historyReassignedByDeletingMower(scenario: Scenario, mowerName: string): number {
  return scenario.history.filter((h) => h.mower === mowerName).length;
}

/** Remove a mower, its `base_durations` rows, and reassign each history event it owned to
 * another capable mower. **No-op when it would strand an area** (`strandedAreasWithoutMower`). */
export function deleteMower(scenario: Scenario, mowerName: string): Scenario {
  if (!scenario.mowers.some((m) => m.name === mowerName)) return scenario;
  if (strandedAreasWithoutMower(scenario, mowerName).length > 0) return scenario;
  const next = structuredClone(scenario);
  next.mowers = next.mowers.filter((m) => m.name !== mowerName);
  for (const h of next.history) {
    if (h.mower === mowerName) {
      const other = next.mowers.find((m) => m.can_mow.includes(h.area));
      if (other) h.mower = other.name;
    }
  }
  syncBaseDurations(next, scenario);
  return next;
}

/** Append a no-go / avoid window to an area on `days` (all seven by default — the
 * behaviour before ADR-0029, and what "+ no-go" still does). The first window of a kind
 * is the canonical night-irrigation `[22, 6]` (no-go) or daytime-play `[8, 20]` (avoid);
 * a further one sits just after the last. No-op on an unknown area or an empty day set. */
export function addAvailabilityWindow(
  scenario: Scenario,
  areaName: string,
  kind: WindowKind,
  days: readonly Weekday[] = WEEKDAYS,
): Scenario {
  return editWindowRows(scenario, areaName, (rows) => {
    if (days.length === 0) return null;
    const ofKind = rows.filter((r) => r.kind === kind);
    const last = ofKind[ofKind.length - 1];
    const [start, end] = last ? [last.end, (last.end + 2) % 24] : kind === "no_go" ? [22, 6] : [8, 20];
    rows.push({ kind, start, end, days: WEEKDAYS.filter((d) => days.includes(d)), at: {} });
    return rows;
  });
}

/** Remove the `index`-th no-go / avoid window from the weekdays it applies to. Other
 * weekdays and the other kind are untouched. No-op on a bad index. */
export function deleteAvailabilityWindow(
  scenario: Scenario,
  areaName: string,
  kind: WindowKind,
  index: number,
): Scenario {
  return editWindowRows(scenario, areaName, (rows) => {
    const row = rowAt(rows, kind, index);
    if (!row) return null;
    return rows.filter((r) => r !== row);
  });
}

/** Set which weekdays the `index`-th no-go / avoid window applies to. An empty day set is
 * a no-op, not a delete — removal has its own button. No-op on a bad index. */
export function setWindowDays(
  scenario: Scenario,
  areaName: string,
  kind: WindowKind,
  index: number,
  days: readonly Weekday[],
): Scenario {
  return editWindowRows(scenario, areaName, (rows) => {
    if (days.length === 0) return null;
    const row = rowAt(rows, kind, index);
    if (!row) return null;
    row.days = WEEKDAYS.filter((d) => days.includes(d));
    return rows;
  });
}
