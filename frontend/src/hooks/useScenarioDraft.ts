// The scenario editor's working state (ADR-0024 / 0026 / 0027), lifted out of App.tsx:
// the draft itself, what it was opened from, the catalogue that seeds its pickers, the
// debounced recompute of the derived figures, the per-row Undo strip and the two bits of
// edit-mode toolbar state (the "Save as…" field and the delete confirmation).
//
// The hook owns *when* a draft exists, so the transitions that end one — a scenario
// switch, a write, "New scenario…", deleting the last scenario — call `reset()` instead
// of each clearing seven pieces of state by hand.

import { useCallback, useEffect, useState } from "react";

import { fetchCatalog, fetchDerived } from "../api/client";
import { scenariosEqual } from "../lib/scenarioEdits";
import { type FieldError, validateScenario } from "../lib/scenarioValidation";
import type { Catalog, Scenario, ScenarioDerived } from "../types";

/** A row delete held for Undo: the scenario as it was, plus what to call the row. */
export interface RemovedRow {
  before: Scenario;
  label: string;
}

export interface ScenarioDraft {
  editing: boolean;
  /** The working copy while editing; null when not editing (or nothing is loaded). */
  draft: Scenario | null;
  /** The mower/area-type reference, fetched once — the editor's defaults source. */
  catalog: Catalog | null;
  /** Debounced recompute of bounds + load factor for a dirty draft; null otherwise. */
  liveDerived: ScenarioDerived | null;
  removed: RemovedRow | null;
  /** The inline "Save as…" field (null = closed). */
  saveAsName: string | null;
  /** Gates the two-step delete. */
  confirmDelete: boolean;
  /** The draft differs from whatever it was opened from. */
  dirty: boolean;
  /** `isNew` = an unsaved from-scratch template, with no file behind it (ADR-0027). */
  isNew: boolean;
  fieldErrors: FieldError[];
  /** Open the loaded scenario for editing (null = nothing loaded: edit mode, no draft). */
  startEditing: (scenario: Scenario | null) => void;
  /** Open an unsaved template straight into edit mode ("New scenario…"). */
  startNew: (scenario: Scenario) => void;
  /** Leave edit mode, keeping the draft (Done editing). */
  stopEditing: () => void;
  /** Throw the edits away, back to what the draft was opened from. */
  resetDraft: (scenario: Scenario | null) => void;
  applyDraft: (next: Scenario) => void;
  /** Apply a one-shot transform to the working scenario and hold the result as a dirty,
   *  unsaved draft — WITHOUT opening the scenario editor (ADR-0039). The schedule editor's
   *  "drop a service" / "add a service" links use this: they change a service bound, the
   *  "Edited — not saved" badge signals it, and the user stays looking at the schedule.
   *  `working` is the scenario on screen; `savedBaseline` is what "Revert to saved" undoes
   *  to when no draft is in progress yet. */
  editRequirements: (
    working: Scenario,
    savedBaseline: Scenario,
    transform: (s: Scenario) => Scenario,
  ) => void;
  /** A structural row delete: hold the pre-delete scenario for Undo, then apply. */
  rowRemoved: (before: Scenario, label: string) => void;
  undoRemove: () => void;
  /** Dismiss the Undo strip without restoring — the row stays deleted. */
  dismissRemoved: () => void;
  setSaveAsName: (v: string | null) => void;
  setConfirmDelete: (v: boolean) => void;
  /** No draft at all — the scenario it belonged to is gone or has been written. */
  reset: () => void;
}

export function useScenarioDraft({
  onStructuralChange,
}: {
  /** A change to the area/mower count invalidates any schedule on screen. */
  onStructuralChange: () => void;
}): ScenarioDraft {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Scenario | null>(null);
  // What the draft was opened from. App used to compare against the live `bundle`
  // instead; capturing it here is equivalent (the bundle and the draft are only ever
  // replaced together) and keeps the hook independent of the library one.
  const [baseline, setBaseline] = useState<Scenario | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [liveDerived, setLiveDerived] = useState<ScenarioDerived | null>(null);
  const [removed, setRemoved] = useState<RemovedRow | null>(null);
  const [saveAsName, setSaveAsName] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [isNew, setIsNew] = useState(false);

  // The editor's defaults source (ADR-0024) — fetched once, never changes.
  useEffect(() => {
    fetchCatalog()
      .then(setCatalog)
      .catch(() => {
        // A missing catalogue only costs the type/model picker suggestions — not fatal.
      });
  }, []);

  const dirty = draft !== null && baseline !== null && !scenariosEqual(draft, baseline);

  // Recompute bounds + load factor for the edited draft, debounced so a burst of
  // keystrokes makes one request (ADR-0024). Falls back to the loaded bundle's figures
  // when the draft matches the loaded scenario.
  useEffect(() => {
    if (!dirty || !draft) return;
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      fetchDerived(draft, ctrl.signal)
        .then(setLiveDerived)
        .catch(() => {
          // A transient invalid draft 422s — keep showing the last good figures.
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [draft, dirty]);

  const startEditing = useCallback(
    (scenario: Scenario | null) => {
      // Re-entering edit mode on the scenario the current draft already belongs to keeps
      // that draft — "Done editing" leaves it live and unsaved, and re-cloning from the
      // loaded bundle here would silently throw those edits away. Clone only when opening a
      // different scenario (`baseline` is the exact `bundle.scenario` ref the draft was
      // opened from, and the bundle is replaced only on a real scenario change).
      if (scenario && scenario !== baseline) {
        setDraft(structuredClone(scenario));
        setBaseline(scenario);
        setLiveDerived(null);
      }
      setEditing(true);
    },
    [baseline],
  );

  const startNew = useCallback((scenario: Scenario) => {
    setDraft(structuredClone(scenario));
    setBaseline(scenario);
    setLiveDerived(null);
    setRemoved(null);
    setSaveAsName(null);
    setConfirmDelete(false);
    setIsNew(true);
    setEditing(true);
  }, []);

  const stopEditing = useCallback(() => {
    setEditing(false);
    setSaveAsName(null);
    setConfirmDelete(false);
    setRemoved(null);
  }, []);

  const resetDraft = useCallback((scenario: Scenario | null) => {
    if (scenario) {
      setDraft(structuredClone(scenario));
      setBaseline(scenario);
    }
    setLiveDerived(null);
    setRemoved(null);
  }, []);

  const editRequirements = useCallback(
    (
      working: Scenario,
      savedBaseline: Scenario,
      transform: (s: Scenario) => Scenario,
    ) => {
      // Keep an in-progress draft's baseline; otherwise anchor to the saved scenario, so
      // `dirty` compares the transformed draft against what a Save would overwrite.
      setBaseline((prev) => prev ?? savedBaseline);
      setDraft(transform(structuredClone(working)));
      setLiveDerived(null);
      setRemoved(null);
      // Deliberately not `setEditing(true)` — see the interface doc.
    },
    [],
  );

  // Every edit from `ScenarioSummary` lands here. A change to the *set* of area or mower
  // names — added, removed, or renamed — is structural: a schedule on screen is drawn
  // against those names and that fleet, so it no longer matches (Stage 2). A value edit
  // (interval, size, priority, availability, capability, history) is not — the schedule
  // stays a valid object, just possibly worth re-solving, and Stage 2 keeps it through a
  // Save for exactly that reason. Growing the count (an add) also supersedes a pending
  // row-Undo (a shrink is a delete, which set its own `removed`).
  const applyDraft = useCallback(
    (next: Scenario) => {
      if (draft) {
        const names = (s: Scenario) =>
          JSON.stringify([
            s.areas.map((a) => a.name).sort(),
            s.mowers.map((m) => m.name).sort(),
          ]);
        if (names(next) !== names(draft)) onStructuralChange();
        if (next.areas.length > draft.areas.length || next.mowers.length > draft.mowers.length)
          setRemoved(null);
      }
      setDraft(next);
    },
    [draft, onStructuralChange],
  );

  const rowRemoved = useCallback(
    (before: Scenario, label: string) => {
      setRemoved({ before, label });
      onStructuralChange();
    },
    [onStructuralChange],
  );

  const undoRemove = useCallback(() => {
    if (removed) setDraft(removed.before);
    setRemoved(null);
  }, [removed]);

  const dismissRemoved = useCallback(() => setRemoved(null), []);

  const reset = useCallback(() => {
    setEditing(false);
    setDraft(null);
    setBaseline(null);
    setLiveDerived(null);
    setRemoved(null);
    setSaveAsName(null);
    setConfirmDelete(false);
    setIsNew(false);
  }, []);

  return {
    editing,
    draft,
    catalog,
    liveDerived,
    removed,
    saveAsName,
    confirmDelete,
    dirty,
    isNew,
    // Validated whenever a draft exists, not only in edit mode: an unsaved draft left by
    // "Done editing" can still be saved (from the card badge) and still feeds Solve, so
    // its errors must gate both (Stage 1).
    fieldErrors: draft ? validateScenario(draft) : [],
    startEditing,
    startNew,
    stopEditing,
    resetDraft,
    applyDraft,
    editRequirements,
    rowRemoved,
    undoRemove,
    dismissRemoved,
    setSaveAsName,
    setConfirmDelete,
    reset,
  };
}
