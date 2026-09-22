// The curated scenario library as the UI sees it (ADR-0019 / 0026 / 0027): which
// scenarios exist, which one is selected, and the loaded bundle for it — plus the two
// fetches that keep those in step.
//
// The error banner is deliberately *not* here: it is shared by the solve job, the
// persistence handlers and this hook, so App owns it and every cluster reports into it
// through an `onError` callback.

import { useCallback, useEffect, useState } from "react";

import { fetchScenario, fetchScenarios } from "../api/client";
import type { ScenarioBundle, ScenarioListEntry } from "../types";

/** The scenario a fresh visitor lands on. */
export const DEFAULT_SCENARIO = "well-resourced";

/**
 * Pick a valid selection out of a library listing: keep `current` if it still exists,
 * else the default, else the first — or "" when the library is empty (ADR-0027), which
 * the scenario fetch skips. Shared by the initial load and the post-delete reselect, so
 * the two cannot drift.
 */
export function pickScenarioId(list: ScenarioListEntry[], current: string): string {
  if (!list.length) return "";
  if (list.some((s) => s.id === current)) return current;
  if (list.some((s) => s.id === DEFAULT_SCENARIO)) return DEFAULT_SCENARIO;
  return list[0].id;
}

export interface ScenarioLibrary {
  /** `null` while the first list fetch is in flight — lets the empty-library card wait
   *  instead of flashing (ADR-0027). */
  scenarios: ScenarioListEntry[] | null;
  /** The selected slug, persisted so a pitch resumes where it left off; "" = none. */
  scenarioId: string;
  bundle: ScenarioBundle | null;
  /** Switch the selection; the bundle for it arrives from the fetch effect. */
  select: (id: string) => void;
  /** Show a bundle with no file behind it (the from-scratch template) — `scenarioId` is
   *  deliberately untouched, so localStorage keeps pointing at a real library scenario. */
  showUnsaved: (bundle: ScenarioBundle) => void;
  /** After a write: adopt the written bundle under `slug` and re-read the listing. */
  adopt: (slug: string, written: ScenarioBundle) => Promise<void>;
  /** After deleting the last scenario: nothing selected, nothing loaded. */
  clearSelection: () => void;
  /** Re-read the listing (and return it, for callers that need to choose from it). */
  refresh: () => Promise<ScenarioListEntry[]>;
}

export function useScenarioLibrary({
  paused,
  onError,
}: {
  /** Skip the scenario fetch — an unsaved from-scratch draft is on screen (ADR-0027). */
  paused: boolean;
  onError: (message: string) => void;
}): ScenarioLibrary {
  const [scenarios, setScenarios] = useState<ScenarioListEntry[] | null>(null);
  const [scenarioId, setScenarioId] = useState<string>(
    () => localStorage.getItem("scenarioId") ?? DEFAULT_SCENARIO,
  );
  const [bundle, setBundle] = useState<ScenarioBundle | null>(null);

  useEffect(() => {
    localStorage.setItem("scenarioId", scenarioId);
  }, [scenarioId]);

  const refresh = useCallback(async () => {
    const list = await fetchScenarios();
    setScenarios(list);
    return list;
  }, []);

  // The initial load. Spelled out rather than reusing `refresh()` so the state updates
  // sit visibly inside the `.then` — the react(set-state-in-effect) lint can't see
  // through a helper to tell that they land after an await, and it is right to ask.
  useEffect(() => {
    fetchScenarios()
      .then((list) => {
        setScenarios(list);
        setScenarioId((cur) => pickScenarioId(list, cur));
      })
      .catch((e: Error) => onError(e.message));
  }, [onError]);

  // Fetch the scenario whenever the selection changes. Waits for the library list
  // (`scenarios === null`) so an empty library never races a stale fetch onto the page;
  // skipped for an unsaved from-scratch draft (`paused`) and an empty library
  // (`scenarioId` ""). `select` clears the previous bundle first.
  useEffect(() => {
    if (paused || !scenarioId || scenarios === null) return;
    let cancelled = false;
    fetchScenario(scenarioId)
      .then((b) => !cancelled && setBundle(b))
      .catch((e: Error) => !cancelled && onError(e.message));
    return () => {
      cancelled = true;
    };
  }, [scenarioId, paused, scenarios, onError]);

  const select = useCallback((id: string) => {
    setScenarioId(id);
    setBundle(null);
  }, []);

  const showUnsaved = useCallback((b: ScenarioBundle) => setBundle(b), []);

  // The written bundle goes straight in, so there's no empty flash while the GET re-runs.
  const adopt = useCallback(
    async (slug: string, written: ScenarioBundle) => {
      setBundle(written);
      setScenarioId(slug);
      try {
        await refresh();
      } catch (e) {
        onError((e as Error).message);
      }
    },
    [refresh, onError],
  );

  const clearSelection = useCallback(() => {
    setBundle(null);
    setScenarioId("");
  }, []);

  return { scenarios, scenarioId, bundle, select, showUnsaved, adopt, clearSelection, refresh };
}
