import { useCallback, useEffect, useRef, useState } from "react";

import {
  type SolveTarget,
  advanceScenario,
  createScenario,
  deleteScenario,
  fetchScenarioTemplate,
  saveScenario,
} from "./api/client";
import { AddServicePanel } from "./components/AddServicePanel";
import { ExplanationPanel } from "./components/ExplanationPanel";
import { GanttSchedule } from "./components/GanttSchedule";
import { LoadScenarioMenu } from "./components/LoadScenarioMenu";
import { type Tab as ScenarioTab, ScenarioSummary } from "./components/ScenarioSummary";
import { ScheduleTable } from "./components/ScheduleTable";
import { SolveProgress } from "./components/SolveProgress";
import { useScenarioDraft } from "./hooks/useScenarioDraft";
import { pickScenarioId, useScenarioLibrary } from "./hooks/useScenarioLibrary";
import { usePlanHistory } from "./hooks/usePlanHistory";
import { type RollEntry, useRunLog } from "./hooks/useRunLog";
import { useScheduleEdits } from "./hooks/useScheduleEdits";
import { Switch } from "./components/Switch";
import { TaskEditPopover } from "./components/TaskEditPopover";
import { useSolveJob } from "./hooks/useSolveJob";
import { useExplanation } from "./hooks/useExplanation";
import { canDropService, dropOneService, forceOneMoreService } from "./lib/scenarioEdits";
import { isValidSlug, slugify } from "./lib/slug";
import { clockLabel } from "./lib/schedule";
import {
  costReadout,
  parseClingoArgs,
  plannedSolverInfo,
  solverModeLabel,
  solverModeProse,
} from "./lib/solver";
import {
  NO_PREFERENCES,
  type PlanTask,
  type StabilitySetting,
  agreementSentence,
  isInPayload,
  optimumContext,
  preferencesFor,
  releasedCounts,
  toPreferences,
} from "./lib/schedulePreferences";
import { droppedEdits } from "./lib/explanation";
import type {
  ExplanationReport,
  PreferenceReport,
  ScenarioBundle,
  SolveResult,
} from "./types";
import "./App.css";

type Theme = "system" | "light" | "dark";
type View = "chart" | "table";

// Expert-mode command-line override (ADR-0023): the field's starting value, matching the
// API's own default portfolio (service._portfolio_args(threads=4)).
const DEFAULT_CLINGO_ARGS = "-t4 --configuration=many";

// The expert-mode stability setting. The first four are *prices*: each says what one
// churned task costs against the service objective it competes with (`PREFERENCE_LEVELS`
// in `service.py` holds the `-c pref_level=N` each maps to). `top` is first and is the
// default — today's behaviour and the ADR-0034 pilot's choice.
//
// The fifth is a different **mechanism**, not a price: `#heuristic` directives bias which
// atoms the search decides first and put nothing in the objective (ADR-0032). ADR-0034
// decision 4 kept it specifically for the rolling use case; it was reachable from the API
// but not from here until ADR-0043's amendment, which was an oversight of axis — the
// selector varied `level`, a price *within* weak mode, and never touched `mode`.
//
// The sixth, `cold`, is **no stability at all** (UI review 2026-09-10): the week is
// re-planned from scratch each step. `preferencesFor` collapses it to `NO_PREFERENCES`, so
// it reaches the wire exactly like "Re-solve from scratch". It is here because the
// heuristic mechanism has a measured price (6× the solve time at 0.69 agreement, ADR-0043)
// and a greenkeeper who does not care about churn should pay neither that nor the
// weak-constraint distortion.
//
// `tiebreak` is deliberately absent: ADR-0034 measured it as indistinguishable from
// ignoring the edits on any instance the solver cannot prove within budget, and keeps it
// as a study arm, not a product option. The API still accepts it.
const STABILITY_OPTIONS: [StabilitySetting, string][] = [
  ["top", "more than any missed service"],
  ["high", "one missed High-priority service"],
  ["low", "one missed Low-priority service"],
  ["avoid", "one hour worked in an avoid window"],
  ["heuristic", "nothing — bias the search instead, and leave the objective alone"],
  ["cold", "nothing — re-plan the week from scratch each step"],
];

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("theme") as Theme | null) ?? "system",
  );
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);
  return [theme, setTheme];
}

/**
 * Expert mode: a persisted UI preference, same pattern as `useTheme` — seed state from
 * localStorage, write it back on change. Off by default; the default view shows only
 * end-user-relevant information, expert mode reveals solver diagnostics (ADR-0011).
 */
function useExpertMode(): [boolean, (v: boolean) => void] {
  const [expert, setExpert] = useState<boolean>(() => localStorage.getItem("expertMode") === "1");
  useEffect(() => {
    localStorage.setItem("expertMode", expert ? "1" : "0");
  }, [expert]);
  return [expert, setExpert];
}

export default function App() {
  const [theme, setTheme] = useTheme();
  const [expert, setExpert] = useExpertMode();
  const [view, setView] = useState<View>("chart");
  // Which scenario tab is open, or none (UI review, 2026-09-24 — the four tabs cluttered
  // the screen when nobody had asked to see one). Lifted out of ScenarioSummary so
  // "Edit scenario" / "New scenario…" / "Drop a service" can open Areas on the user's
  // behalf, rather than leaving them to hunt for the tab bar themselves.
  const [scenarioTab, setScenarioTab] = useState<ScenarioTab | null>(null);
  const [timeLimit, setTimeLimit] = useState(20);
  // How far a "Move forward" step advances "now" (ADR-0042). Default 24 h — the
  // production loop's default cadence. Not persisted, like `timeLimit`.
  const [advanceHours, setAdvanceHours] = useState(24);
  // Expert-mode command-line override (ADR-0023). Not persisted — like `timeLimit`, an
  // advanced per-session override that resets to the safe default on reload. Only ever
  // sent to the API while `expert` is on (see the Solve button); typing here has zero effect
  // on a non-expert user's solve request.
  const [clingoArgs, setClingoArgs] = useState(DEFAULT_CLINGO_ARGS);
  // The stability setting: what one churned task is worth against the service objective —
  // or, at `heuristic`, the other mechanism entirely. Expert-mode only and not persisted,
  // on the same reasoning as `clingoArgs` — and, like it, only ever reaches the API while
  // `expert` is on (`stability` below), so the default view always solves at `top`,
  // today's behaviour.
  const [stabilityChoice, setStabilityChoice] = useState<StabilitySetting>("top");
  const stability: StabilitySetting = expert ? stabilityChoice : "top";
  // The explain-budget override (Iteration 6, ADR-0048) — expert-mode only and not
  // persisted, same reasoning as `clingoArgs`/`stabilityChoice`. `undefined` means "the
  // backend's own default", never sent as a literal value.
  const [explainBudgetChoice, setExplainBudgetChoice] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  // The plan a Move forward started from, so its run-log row can tell a plan produced by
  // this roll's re-solve from the one that was already on screen (ADR-0042 follow-up).
  const rollBaseline = useRef<SolveResult | null>(null);
  // The whole anytime-solve machine (ADR-0022) — result, solving, the poll timer and the
  // job's identity — lives in its own hook, so every caller that has to abandon a solve
  // calls one `discardSolve()` instead of open-coding the reset. Destructured so the
  // members are plain, stable identifiers (readable JSX, and useCallback deps the
  // exhaustive-deps lint can check).
  const {
    result,
    solving,
    stoppedByUser,
    solveRun,
    lastRequest,
    run: runSolve,
    stop: stopSolve,
    discard: discardSolve,
    clearResult: clearSolveResult,
    restore: restorePlan,
  } = useSolveJob({ onError: setError });
  // Why a dropped edit was not kept (Iteration 6). Lives on `lastRequest`, not `result`
  // itself — see `useSolveJob`'s doc comment on why the two can drift (a scenario switch,
  // a replan Undo).
  const explanation = useExplanation({ result, lastRequest, onError: setError });
  // The replan undo stack (ADR-0035). Declared next to the solve machine it shadows;
  // every path that abandons a solve also drops the history — a plan from a different
  // scenario must never be restorable onto this one.
  const history = usePlanHistory();
  const { clear: clearHistory } = history;
  // The moving-horizon run log (ADR-0042): the cumulative "what actually happened" track
  // and the per-roll metrics. Cleared whenever the scenario changes underneath it, same
  // as the replan history.
  const runLog = useRunLog();
  const { reset: resetRunLog } = runLog;
  // `discardSolve` plus the history + run log — they always go together, so bundling them
  // keeps the four transition handlers below from each having to remember the others
  // (the drift `discard()` itself was extracted to prevent).
  const abandonSolve = useCallback(() => {
    discardSolve();
    clearHistory();
    resetRunLog();
  }, [discardSolve, clearHistory, resetRunLog]);
  // A structural scenario edit (an area or mower added/removed) invalidates the shown
  // schedule — and every plan behind it, which describes a fleet that no longer exists.
  const invalidateSolve = useCallback(() => {
    clearSolveResult();
    clearHistory();
    resetRunLog();
  }, [clearSolveResult, clearHistory, resetRunLog]);
  // When a Move-forward step's re-solve settles, fill in its run-log entry (ADR-0042).
  // The solve runs asynchronously (ADR-0022); `pending` is true only between `begin`
  // (in `moveForward`) and this effect firing once `solving` drops with a schedule.
  const { pending: rollPending, settle: settleRoll, abandon: abandonRoll } = runLog;
  useEffect(() => {
    if (solving || !rollPending) return;
    // Only a plan this roll's own re-solve produced may fill the row. After a stop or a
    // failure `result` still holds the plan from *before* the roll, and settling from it
    // reported that week's services, kept/carried, quality and solve time as if they were
    // this step's. Identity is the test: `rollBaseline` is the result the roll started on.
    if (!result?.schedule || result === rollBaseline.current) {
      abandonRoll();
      return;
    }
    const frozen = result.preferences?.agreement.by_origin?.frozen;
    settleRoll({
      services: result.schedule.tasks.length,
      carried: frozen?.total ?? 0,
      kept: frozen?.time_kept ?? 0,
      quality: result.quality,
      solveTimeS: result.solve_time_s,
      optimal: result.optimal,
      // A report at all means a plan was carried; a report whose `level` is null means it
      // was carried by the heuristic mechanism, which prices nothing (ADR-0032).
      setting: result.preferences ? (result.preferences.level ?? "heuristic") : null,
    });
  }, [solving, result, rollPending, settleRoll, abandonRoll]);
  // Everything the scenario editor works on (ADR-0024 / 0026 / 0027) — the draft, the
  // catalogue, the debounced derived figures, the Undo strip and the edit-mode toolbar
  // state. Ending a draft is one `resetDraftState()` rather than eight setters.
  const {
    editing,
    draft,
    catalog,
    liveDerived,
    removed,
    saveAsName,
    confirmDelete,
    dirty,
    isNew,
    fieldErrors,
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
    reset: resetDraftState,
  } = useScenarioDraft({ onStructuralChange: invalidateSolve });
  // Which scenarios exist, which is selected, and the bundle loaded for it. Declared
  // last because it needs `isNew`: an unsaved from-scratch draft pauses the fetch.
  const {
    scenarios,
    scenarioId,
    bundle,
    select: selectFromLibrary,
    showUnsaved,
    adopt,
    clearSelection,
    refresh: refreshLibrary,
  } = useScenarioLibrary({ paused: isNew, onError: setError });
  // `persisting` disables the Save/Delete controls during a write (ADR-0026). Unlike the
  // rest of the persistence UI it is a transient request flag, not draft state.
  const [persisting, setPersisting] = useState(false);
  // The Scenario card, so the "adjust the requirement" link from the schedule can scroll
  // it back into view — edit mode opens there, three sections above the schedule.
  const scenarioCardRef = useRef<HTMLElement>(null);

  const selectScenario = useCallback(
    (id: string) => {
      const sameScenario = id === scenarioId && !isNew;
      // A clean re-pick of the loaded scenario is a genuine no-op — don't wipe a solved
      // plan. But a re-pick after confirming "discard changes" must still reset: guardDiscard
      // only lets this run past the prompt when the draft was dirty (or new).
      if (sameScenario && !dirty) return;
      abandonSolve();
      resetDraftState();
      setError(null);
      // Skip the re-fetch for an unchanged id: the fetch effect keys on `scenarioId`, so it
      // would blank the bundle and never refill it — the "Loading scenario…" hang.
      if (!sameScenario) selectFromLibrary(id);
    },
    [scenarioId, isNew, dirty, selectFromLibrary, abandonSolve, resetDraftState],
  );

  // Solve the draft body (not `scenarioId`) when it's edited OR brand-new (ADR-0027 — a
  // pristine from-scratch draft is not `dirty` but there is no saved id to solve).
  const solveFromDraft = draft !== null && (isNew || dirty);

  // The solve target: the draft body when it's edited or brand-new, else the saved id.
  const solveTarget = (): SolveTarget =>
    solveFromDraft ? { scenario: draft! } : { scenarioId };

  // Pressing Solve when a plan is already on screen is a *replan*: push the outgoing plan
  // so Undo can bring it back. Pushed here, not on completion, so that a solve which ends
  // in nothing (a timeout with no model, or Stop before the first one) is undoable too —
  // that is exactly the case where the user most wants the old plan back.
  //
  // `cold` is the "Re-solve from scratch" button (ADR-0038): send no payload and ignore the
  // plan on screen. Plain "Re-solve" keeps the plan — every task the user has not released.
  // The first solve (empty working copy) is cold either way: `toPreferences([])` is already
  // the no-payload form.
  const doSolve = (cold = false) => {
    // The note describes an edit to the plan being replaced, so it does not survive the
    // replan it was warning about.
    setReleased(null);
    // Re-solve is how editing *finishes* (ADR-0053). The payload is read from `planTasks`
    // just below, so closing the editor here loses nothing — and it must close, because a
    // solve rebuilds the working copy from every incumbent it polls, which would silently
    // discard anything typed while it ran. Not the toggle's own handler: that discards.
    setEditingSchedule(false);
    setEditingTask(null);
    if (result) history.push({ result, scenario: null, planTasks });
    void runSolve(
      solveTarget(),
      timeLimit,
      expert ? clingoArgsTokens : undefined,
      cold ? NO_PREFERENCES : toPreferences(planTasks, stability),
      // "Re-solve from scratch" ignores the plan on screen entirely, releases included —
      // there is nothing for `POST /api/explain`'s `reinstated` check to compare against.
      cold ? undefined : releasedCounts(planTasks),
    );
  };

  // Releasing is worth explaining, so both entry points (the table row and the chart's
  // task editor) go through here rather than each remembering to raise the strip.
  const pinTask = (uid: string, pin: PlanTask["pin"]) => {
    setPin(uid, pin);
    const t = planTasks.find((p) => p.uid === uid);
    setReleased(pin === "released" && t ? { area: t.area, start: t.start } : null);
  };

  // How many services the plan on screen currently holds for one area — the pivot the
  // "drop a service" / "add a service" bound nudges turn on (ADR-0039).
  const areaServiceCount = (area: string) => planTasks.filter((t) => t.area === area).length;

  // "Drop a service from <area>" (release strip): lower that area's cap to one below its
  // current count and hold it as an unsaved draft. The schedule stays on screen; the
  // "Edited — not saved" badge signals the change, and Re-solve produces one fewer.
  const dropServiceFrom = (area: string) => {
    if (!workingScenario || !bundle) return;
    const n = areaServiceCount(area);
    setReleased(null);
    editRequirements(workingScenario, bundle.scenario, (s) => dropOneService(s, area, n));
    setScenarioTab("areas"); // so the changed bound is actually visible
    scenarioCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // "Add a service" (the add panel): record the requested slot as a weak preference AND
  // raise the area's floor by one so the solver must produce it — an unsaved draft.
  const addService = (area: string, start: number, mower: string) => {
    if (!workingScenario || !bundle) return;
    // Count only the area's services that will survive the next Re-solve — a released one
    // is on its way out, so releasing one and adding one in the same area is a *swap*, not
    // a forced +1 (code-review follow-up).
    const kept = planTasks.filter((t) => t.area === area && isInPayload(t)).length;
    addTask(area, start, mower, workingScenario);
    editRequirements(workingScenario, bundle.scenario, (s) => forceOneMoreService(s, area, kept));
  };

  // "Move forward N hours" (ADR-0042): advance "now", fold the now-past plan into history,
  // and re-solve carrying the still-future plan forward. One press — the replan *is* the
  // reaction to time passing. The roll happens on the server (rolling.advance); the plan
  // being executed is the plan on screen, edits and all (no execution simulation).
  const moveForward = async () => {
    if (!workingScenario || !bundle || !result?.schedule) return;
    setError(null);
    const executedPlan = planTasks.map((t) => ({
      area: t.area,
      mower: t.mower,
      start: t.start,
      end: t.end,
      task: t.sourceTask ?? 1, // rank is not read by the roll; a placeholder is fine
    }));
    try {
      const rolled = await advanceScenario(workingScenario, executedPlan, advanceHours);
      // Undo must step "now" and the plan back together (ADR-0042) — and the working copy
      // with them, so an un-re-solved edit the roll just carried is not lost on Undo.
      history.push({ result, scenario: workingScenario, planTasks });
      setReleased(null);
      rollBaseline.current = result;
      // Record the step in the run log: the tasks now in the past join the cumulative
      // "executed" track, and a pending entry opens for this step's re-solve.
      runLog.begin({
        hours: advanceHours,
        nowLabel: clockLabel(0, rolled.scenario.horizon_start_hour),
        executedThisRoll: executedPlan.filter((t) => t.start < advanceHours),
      });
      // Hold the rolled scenario as a dirty, unsaved draft without opening the editor —
      // the "Edited — not saved" badge signals the roll. Same door as "drop a service".
      editRequirements(workingScenario, bundle.scenario, () => rolled.scenario);
      void runSolve(
        { scenario: rolled.scenario },
        timeLimit,
        expert ? clingoArgsTokens : undefined,
        preferencesFor(rolled.carried, stability),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const undoReplan = () => {
    const previous = history.undo();
    if (!previous) return;
    // Restore the working copy first, then swap its plan back on screen — same click, one
    // batch, so `useScheduleEdits` keeps the restored edits instead of rebuilding from the
    // result (UI review 2026-09-10).
    restoreEdits(previous.result, previous.planTasks);
    restorePlan(previous.result);
    // A Move-forward snapshot also carries the pre-roll scenario — step "now" and the run
    // log back too.
    if (previous.scenario && bundle) {
      const before = previous.scenario;
      editRequirements(before, bundle.scenario, () => before);
      runLog.undo();
    }
  };

  // The plan as the user currently wants it (ADR-0035). Rebuilt whenever a new plan
  // arrives; it is what `toPreferences` turns into the re-solve payload.
  const {
    tasks: planTasks,
    dirty: planEdited,
    reset: resetPlanEdits,
    restore: restoreEdits,
    moveTask,
    reassign,
    setPin,
    setAllPins,
    addTask,
  } = useScheduleEdits(result);
  // Whether any task would go into the next re-solve's payload — drives the toolbar's
  // "release all" / "keep all" toggle (ADR-0038).
  const anyKept = planTasks.some(isInPayload);
  // What the "proven optimal" line may claim — see `optimumContext`.
  const { constrained: constrainedOptimum, hasEdits: carriedEdits } = optimumContext(
    result?.preferences ?? null,
  );
  // Schedule edit mode is separate from *scenario* edit mode: one changes the plan, the
  // other changes the requirements the plan is computed from. Domain data, so both are
  // default-view controls, not expert-mode ones (ADR-0011).
  const [editingSchedule, setEditingSchedule] = useState(false);
  // The last task the user released, for the strip that explains what that does and
  // offers the requirement change that would actually drop a service.
  const [released, setReleased] = useState<{ area: string; start: number } | null>(null);
  // The bar the Gantt's task editor is open on. Held here rather than in the chart so
  // it survives the re-render each edit causes, and so the popover reads the *current*
  // task rather than the snapshot that was clicked.
  const [editingTask, setEditingTask] = useState<{
    uid: string;
    x: number;
    y: number;
    flip: boolean;
  } | null>(null);

  const scenario = bundle?.scenario ?? null;
  const workingScenario = draft ?? scenario;
  const derivedFigures = (dirty ? liveDerived : null) ?? bundle?.derived ?? null;
  // "New scenario…" (ADR-0027): open the server-built minimal template as an unsaved
  // draft, straight into edit mode. `scenarioId` is deliberately NOT touched — the
  // scenario-fetch effect keys on it, and localStorage should keep pointing at a real
  // library scenario for reload recovery.
  const doNewScenario = useCallback(async () => {
    abandonSolve();
    setError(null);
    try {
      const b = await fetchScenarioTemplate();
      showUnsaved(b);
      startNew(b.scenario);
      setScenarioTab("areas"); // otherwise the editor opens with nothing visible to edit
    } catch (e) {
      setError((e as Error).message);
    }
  }, [abandonSolve, showUnsaved, startNew]);

  // Switching scenario or starting a new one throws a dirty draft away. Ask first
  // (the ADR-0027 deferral), using the same two-step-confirm idiom as Delete rather than
  // a native `window.confirm` — that blocks the event loop and looks nothing like the
  // rest of the app. `guardDiscard` wraps only the *user-initiated* transitions; the
  // post-delete `selectScenario` in `doDelete` must not ask, its draft is already gone.
  // What a confirmed discard would throw away — the strip words itself from this, so the
  // callback below stays free of render-scoped values that would defeat its memoization.
  const [pendingDiscard, setPendingDiscard] = useState<{
    what: "scenario" | "plan";
    run: () => void;
  } | null>(null);
  const guardDiscard = useCallback(
    (run: () => void) => {
      // `isNew` counts as unsaved even before the first keystroke: a from-scratch draft has
      // no file behind it, so leaving it — to switch scenario or start another new one —
      // throws it away just as a dirty draft would.
      if (dirty || isNew) {
        setPendingDiscard({ what: "scenario", run });
      } else run();
    },
    [dirty, isNew, setPendingDiscard],
  );

  // Scenario persistence (ADR-0026). The editable "Scenario name" field is the file
  // identity: its slug is the save target. Save == the loaded slug means overwrite; a
  // different slug means "save under a new name" (PUT is an upsert). "Save as…" always
  // creates (409 on a clash) and leaves the current file alone.
  const loadedSlug = bundle?.scenario.name ?? scenarioId;
  const targetSlug = draft ? slugify(draft.name) : loadedSlug;
  const saveIsRename = targetSlug !== loadedSlug;
  // A draft can be saved from the editor toolbar *or* from the "not saved" badge that
  // stands after "Done editing" (Stage 1) — so this gates on the draft existing, not on
  // being in edit mode.
  const canPersist =
    draft !== null && !solving && !persisting && fieldErrors.length === 0 && isValidSlug(targetSlug);

  // What a completed write clears: the draft, and the library's switch to what was just
  // written. It deliberately does NOT abandon the solve (Stage 2 / ADR-0026 amendment) —
  // a schedule-invalidating edit (an area or mower added/removed/renamed) already cleared
  // the plan mid-edit via `invalidateSolve`, so a plan still on screen at save time was
  // computed against requirements the save did not disturb. Keeping it lets the greenkeeper
  // Re-solve and blend rather than starting cold after every calibration tweak.
  const afterWrite = useCallback(
    async (slug: string, written: ScenarioBundle) => {
      resetDraftState();
      await adopt(slug, written);
    },
    [adopt, resetDraftState],
  );

  const doSave = async () => {
    if (!draft || !canPersist) return;
    setPersisting(true);
    setError(null);
    try {
      const written = await saveScenario(targetSlug, draft);
      await afterWrite(targetSlug, written);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPersisting(false);
    }
  };

  const doSaveAs = async () => {
    if (!draft || saveAsName === null) return;
    const slug = slugify(saveAsName);
    if (!isValidSlug(slug)) {
      setError(`"${saveAsName}" is not a usable scenario name.`);
      return;
    }
    setPersisting(true);
    setError(null);
    try {
      const written = await createScenario(slug, { ...draft, name: slug });
      await afterWrite(slug, written);
    } catch (e) {
      const msg = (e as Error).message;
      setError(
        msg.includes("already exists")
          ? `A scenario named "${slug}" already exists — pick another name or use Save to overwrite it.`
          : msg,
      );
    } finally {
      setPersisting(false);
    }
  };

  const doDelete = async () => {
    setPersisting(true);
    setError(null);
    try {
      await deleteScenario(scenarioId);
      // "" is never in the listing, so this is the same rule the initial load uses,
      // minus the "keep the current selection" arm — the current one is gone.
      const next = pickScenarioId(await refreshLibrary(), "");
      setConfirmDelete(false);
      if (next) {
        selectScenario(next);
      } else {
        // Library now empty — show the "No scenarios yet" card (ADR-0027).
        abandonSolve();
        resetDraftState();
        clearSelection();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPersisting(false);
    }
  };
  // Expert mode shows the solver configuration. Before the first solve, preview what the
  // API will run (reflecting the editable command line below, ADR-0023); once a result is
  // in, SolveResult.solver is authoritative.
  const clingoArgsTokens = parseClingoArgs(clingoArgs);
  const solverInfo = result?.solver ?? plannedSolverInfo(timeLimit, clingoArgsTokens);
  // Compares normalised tokens, not the raw string, so extra/odd whitespace doesn't spuriously
  // show "reset to default"; an empty field already behaves as the default (see startSolve
  // above and plannedSolverInfo), so it counts as "default" here too.
  const isDefaultClingoArgs =
    clingoArgsTokens.length === 0 || clingoArgsTokens.join(" ") === DEFAULT_CLINGO_ARGS;

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Fleet-Planning Demonstrator</h1>
          <p className="app-sub">
            Resource-based weekly scheduling for a robotic mower fleet · Phase 1
          </p>
        </div>
        <div className="header-controls">
          <LoadScenarioMenu
            scenarios={scenarios ?? []}
            currentId={isNew ? "new (unsaved)" : scenarioId}
            onSelect={(id) => guardDiscard(() => selectScenario(id))}
            onNew={() => guardDiscard(doNewScenario)}
            disabled={editingSchedule}
          />
          <Switch label="expert mode" on={expert} onChange={setExpert} />
          <label className="theme-toggle">
            theme{" "}
            <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
              <option value="system">system</option>
              <option value="light">light</option>
              <option value="dark">dark</option>
            </select>
          </label>
        </div>
      </header>

      {/* Only the scenario-discard variant lives here. The plan-discard one is rendered
          right beside the "edit schedule" toggle that raises it (UI review, 2026-09-24) —
          this bar sits above the fold on a tall schedule, so a prompt about the toggle
          three sections below went unseen (owner report). */}
      {pendingDiscard?.what === "scenario" && (
        <DiscardPrompt
          text={`Discard unsaved changes to “${workingScenario?.name ?? "this scenario"}”?`}
          onDiscard={() => {
            const { run } = pendingDiscard;
            setPendingDiscard(null);
            run();
          }}
          onKeepEditing={() => setPendingDiscard(null)}
        />
      )}
      {error && <div className="app-error">Error: {error}</div>}
      {!workingScenario && !error && (scenarios === null || scenarios.length > 0) && (
        <p>Loading scenario…</p>
      )}
      {!workingScenario && !error && scenarios?.length === 0 && (
        <section className="app-card app-empty">
          <h2>No scenarios yet</h2>
          <p className="muted">
            The library is empty. Start from a minimal course — one hole, one area, one
            mower — and build it up.
          </p>
          <button
            type="button"
            onClick={() => guardDiscard(doNewScenario)}
            disabled={persisting}
          >
            New scenario
          </button>
        </section>
      )}

      {workingScenario && bundle && (
        <>
          <section className="app-card" ref={scenarioCardRef}>
            <div className="card-head">
              <h2>Scenario</h2>
              <div className="card-head-actions">
                {editing && dirty && (
                  <button type="button" className="link-button" onClick={() => resetDraft(scenario)}>
                    {isNew ? "reset to the blank template" : "revert to saved"}
                  </button>
                )}
                {editing && (
                  <>
                    <button
                      type="button"
                      onClick={doSave}
                      disabled={!canPersist || (!dirty && !saveIsRename)}
                      title={
                        saveIsRename && isValidSlug(targetSlug)
                          ? `saves as "${targetSlug}" (the loaded scenario stays)`
                          : undefined
                      }
                    >
                      {saveIsRename && isValidSlug(targetSlug) ? `Save as ${targetSlug}` : "Save"}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setConfirmDelete(false);
                        setSaveAsName(draft?.name ?? "");
                      }}
                      disabled={solving || persisting}
                    >
                      Save as…
                    </button>
                    {/* A brand-new scenario has no file to delete (ADR-0027). */}
                    {!isNew &&
                      (confirmDelete ? (
                        <>
                          <button
                            type="button"
                            className="danger"
                            onClick={doDelete}
                            disabled={persisting}
                          >
                            Confirm delete
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => setConfirmDelete(false)}
                          >
                            cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setSaveAsName(null);
                            setConfirmDelete(true);
                          }}
                          disabled={solving || persisting}
                        >
                          Delete
                        </button>
                      ))}
                  </>
                )}
                <button
                  type="button"
                  onClick={() => {
                    if (editing) return stopEditing();
                    startEditing(scenario);
                    setScenarioTab("areas"); // otherwise editing opens with nothing visible
                  }}
                  disabled={solving || persisting || editingSchedule}
                >
                  {editing ? "Done editing" : "Edit scenario"}
                </button>
              </div>
            </div>
            {/* "Done editing" leaves the draft live and unsaved. This says so, next to the
                two ways out of that state, so the changes don't read as either saved or
                gone (Stage 1 / ADR-0036). Only while not editing — the toolbar covers it
                otherwise. */}
            {!editing && (dirty || isNew) && (
              <div className="unsaved-row">
                <span>
                  <span className="unsaved-dot" aria-hidden="true" />
                  {isNew ? "New scenario — not saved." : "Edited — not saved."}
                </span>
                {isNew ? (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => {
                      setConfirmDelete(false);
                      setSaveAsName(draft?.name ?? "");
                    }}
                    disabled={solving || persisting}
                  >
                    Save as…
                  </button>
                ) : (
                  <button
                    type="button"
                    className="link-button"
                    onClick={doSave}
                    disabled={!canPersist || (!dirty && !saveIsRename)}
                  >
                    {saveIsRename && isValidSlug(targetSlug) ? `Save as ${targetSlug}` : "Save"}
                  </button>
                )}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => {
                    if (isNew) return resetDraft(scenario);
                    // After rolls, the plan and the run log are keyed to a "now" that
                    // reverting throws away — clear them too, so "Revert to saved" reads
                    // as "reset the run".
                    if (runLog.rolls.length > 0) abandonSolve();
                    resetDraftState();
                  }}
                  disabled={solving || persisting}
                >
                  {isNew ? "reset to the blank template" : "Revert to saved"}
                </button>
              </div>
            )}
            {removed && (
              <div className="undo-row">
                <span>Removed {removed.label}.</span>
                <button type="button" className="link-button" onClick={undoRemove}>
                  Undo
                </button>
                <button
                  type="button"
                  className="link-button undo-dismiss"
                  aria-label="dismiss"
                  onClick={dismissRemoved}
                >
                  ×
                </button>
              </div>
            )}
            {editing && saveAsName !== null && (
              <form
                className="save-as-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  void doSaveAs();
                }}
              >
                <label>
                  save as{" "}
                  <input
                    type="text"
                    autoFocus
                    value={saveAsName}
                    onChange={(e) => setSaveAsName(e.target.value)}
                    placeholder="new scenario name"
                  />
                </label>
                <span className="muted">
                  {isValidSlug(slugify(saveAsName))
                    ? `→ ${slugify(saveAsName)}`
                    : "enter a name (letters, digits, hyphens)"}
                </span>
                <button
                  type="submit"
                  disabled={persisting || !isValidSlug(slugify(saveAsName)) || fieldErrors.length > 0}
                >
                  Create
                </button>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setSaveAsName(null)}
                >
                  cancel
                </button>
              </form>
            )}
            <ScenarioSummary
              scenario={workingScenario}
              derived={derivedFigures ?? bundle.derived}
              expert={expert}
              editing={editing}
              catalog={catalog}
              errors={fieldErrors}
              solving={solving}
              onChange={applyDraft}
              onRowRemoved={rowRemoved}
              tab={scenarioTab}
              onTabChange={setScenarioTab}
            />
          </section>

          <section className="app-card">
            <div className="solve-bar">
              <button
                onClick={() => void (solving ? stopSolve() : doSolve())}
                disabled={
                  !solving && (fieldErrors.length > 0 || workingScenario.areas.length === 0)
                }
              >
                {solving ? "Stop solving" : result ? "Re-solve" : "Solve schedule"}
              </button>
              {result && !solving && (
                <button
                  className="secondary"
                  onClick={() => doSolve(true)}
                  title="Ignore the plan on screen and solve cold. Undo brings it back."
                >
                  Re-solve from scratch
                </button>
              )}
              {history.canUndo && (
                // Plain "Undo": it steps back whatever last replaced the plan — a
                // re-solve, a cold re-solve, or a Move forward (which also steps "now" and
                // the run log back). The old "Undo replan" label stopped being true when
                // the roll control landed. The scenario editor's own undo is the labelled
                // strip inside edit mode, so there is no ambiguity here.
                <button
                  className="secondary"
                  onClick={undoReplan}
                  disabled={solving || editingSchedule}
                  title="Step back to the previous plan — and, after a Move forward, to the previous “now”."
                >
                  Undo
                </button>
              )}
              {result && !solving && (
                <span className="muted replan-hint">
                  {stability === "cold"
                    ? "Re-solve will plan the week from scratch, ignoring the plan on screen."
                    : "Re-solve asks the solver to keep every service you have not released."}
                </span>
              )}
              <label>
                time budget{" "}
                <input
                  type="number"
                  min={1}
                  max={14400}
                  value={timeLimit}
                  onChange={(e) => setTimeLimit(Number(e.target.value))}
                  disabled={solving}
                />{" "}
                s
              </label>
              {solveFromDraft && !solving && fieldErrors.length === 0 && workingScenario.areas.length > 0 && (
                <span className="muted">
                  solving your {isNew ? "new" : "edited"} scenario
                </span>
              )}
              {fieldErrors.length > 0 && (
                <span className="app-error-inline">
                  fix {fieldErrors.length} field{fieldErrors.length === 1 ? "" : "s"} to solve
                </span>
              )}
              {fieldErrors.length === 0 && workingScenario.areas.length === 0 && (
                <span className="muted">add an area to solve</span>
              )}
            </div>

            {/* Stays mounted through its own re-solve (`rollPending`) so the key can hold
                itself down rather than vanishing under the cursor and jumping the layout.
                Deliberately *not* shown during any other solve: a first cold solve
                produces a best-so-far mid-flight, and a key that appeared already pressed
                would be claiming a press that never happened. */}
            {result?.solved && result.schedule && (!solving || rollPending) && (
              <div className="move-forward-bar">
                {/* The hero control, styled as a transport key: rolling the horizon is
                    what the demonstrator is *for*, so once a plan exists this outweighs
                    Re-solve rather than sitting beside it as a ghost button. The glyph is
                    aria-hidden — the accessible name stays plain "Move forward". */}
                <button
                  type="button"
                  className={`move-forward-key${rollPending ? " is-rolling" : ""}`}
                  onClick={() => void moveForward()}
                  // `editingSchedule` alone is not enough: Undo can restore a working copy
                  // that still carries an un-re-solved edit while leaving the editor toggle
                  // closed (owner report, 2026-09-24 — the modal rule in ADR-0053 only
                  // reopens the editor via the toggle, not via Undo). `planEdited` is the
                  // fact that actually matters — a roll must never execute a plan the
                  // solver has not seen.
                  disabled={solving || editingSchedule || planEdited || fieldErrors.length > 0}
                  aria-busy={rollPending || undefined}
                >
                  <svg viewBox="0 0 24 16" aria-hidden="true" focusable="false">
                    <path d="M1 1 L11 8 L1 15 Z" />
                    <path d="M12 1 L22 8 L12 15 Z" />
                  </svg>
                  Move forward
                </button>
                <label className="move-forward-step">
                  <input
                    type="number"
                    min={1}
                    max={workingScenario.horizon_hours}
                    value={advanceHours}
                    onChange={(e) => setAdvanceHours(Number(e.target.value))}
                    disabled={solving}
                  />{" "}
                  hours
                </label>
                <span className="muted move-forward-hint">
                  advances “now”, folds the elapsed plan into history, and re-solves the
                  week ahead keeping what is left.
                </span>
              </div>
            )}

            {/* A fixed-height slot for the progress bar, so a Re-solve or a Move forward
                doesn't shove the schedule down when the bar appears and let it snap back
                when the solve ends (UI review 2026-09-10). Reserved only once a plan is on
                screen — the first solve has nothing below it to push. */}
            {(solving || result) && (
              <div className={`solve-progress-slot${result ? " is-reserved" : ""}`}>
                {solving && <SolveProgress key={solveRun} timeLimitS={timeLimit} />}
              </div>
            )}

            {expert && (
              <div className="solver-config">
                <label className="solver-args-field">
                  clingo command line{" "}
                  <input
                    type="text"
                    className="solver-args-input"
                    value={clingoArgs}
                    onChange={(e) => setClingoArgs(e.target.value)}
                    disabled={solving}
                    spellCheck={false}
                    placeholder={DEFAULT_CLINGO_ARGS}
                  />
                </label>
                {!isDefaultClingoArgs && (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setClingoArgs(DEFAULT_CLINGO_ARGS)}
                    disabled={solving}
                  >
                    reset to default
                  </button>
                )}
                <span className="muted solver-args-hint">
                  flags only — the horizon constant (-c) is appended automatically
                </span>
                {/* The lead-in stays fixed even though the last two options are not prices:
                    "one churned task costs nothing — …" reads correctly for both, and a
                    <select> whose accessible name changes with its own value renames the
                    control under the user. The caveats live in the hint below. */}
                <label className="stability-field">
                  one churned task costs{" "}
                  <select
                    value={stabilityChoice}
                    onChange={(e) => setStabilityChoice(e.target.value as StabilitySetting)}
                    disabled={solving}
                  >
                    {STABILITY_OPTIONS.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <span className="muted solver-args-hint">
                  {stabilityChoice === "cold" ? (
                    <>
                      no attempt to keep the plan on screen — a re-solve, and every Move
                      forward, re-plans the week from scratch. No preference constraints and
                      no search bias, so it is the fastest option and quality is undistorted
                      — but <strong>nothing carries over</strong>, your own edits included.
                      This is the study&rsquo;s baseline arm (ADR-0043); choose it when
                      churn between weeks is not a concern.
                    </>
                  ) : stabilityChoice === "heuristic" ? (
                    <>
                      how a re-solve — or a Move forward — tries to keep the plan on screen.
                      This setting puts <em>nothing</em> in the objective: it biases which
                      decisions the search makes first (<code>#heuristic</code>, ADR-0032),
                      so quality is undistorted by construction — but nothing argues for the
                      old plan once the budget runs out. Measured over a week of rolls at
                      0.69 agreement against the top setting&rsquo;s 0.87, at{" "}
                      <strong>6× the solve time</strong> (ADR-0043), so{" "}
                      <strong>your own edits can be dropped</strong> as well as the carried
                      plan.
                    </>
                  ) : (
                    <>
                      how hard a re-solve — or a Move forward — tries to keep the plan on
                      screen. Sent as the priority level of the preference weak constraints
                      (ADR-0034); below the top setting they share a level with the service
                      objective, so the two costs are summed and no longer separable.
                    </>
                  )}
                </span>
                <p className="solver-mode">
                  <span>
                    {result?.solver ? "" : solving ? "running: " : "will run: "}
                    {solverModeLabel(solverInfo)}
                  </span>
                  {solverModeProse(solverInfo) && (
                    <span className="muted">{solverModeProse(solverInfo)}</span>
                  )}
                </p>
              </div>
            )}

            {result && result.solved && result.schedule && (
              <>
                <div className="stat-row">
                  <Stat label="services" value={String(result.schedule.tasks.length)} />
                  <Stat label="solve time" value={`${result.solve_time_s.toFixed(1)} s`} />
                  <Stat
                    label="proven optimal"
                    value={
                      // A weak-mode solve puts the preference level *above* every
                      // service-quality level (ADR-0034), so a proven optimum is the best
                      // plan **subject to that payload** — not the best plan outright.
                      // But three things have to be true before the edits can be named:
                      // the payload has to exist, it has to be weak mode (in heuristic
                      // mode the objective is untouched, ADR-0032, so the optimum is the
                      // plain one), and it has to contain an edit — a Move forward carries
                      // the plan and no edits at all (ADR-0051).
                      !result.optimal
                        ? "no — best so far"
                        : !constrainedOptimum
                          ? "yes"
                          : carriedEdits
                            ? "yes — best that keeps your edits"
                            : "yes — best that keeps this plan"
                    }
                    hint={
                      result.optimal && constrainedOptimum
                        ? `Optimal within the ${carriedEdits ? "edits" : "plan"} you asked to keep — Re-solve from scratch may score better.`
                        : undefined
                    }
                  />
                  {expert &&
                    (() => {
                      // A preference-bearing solve carries the unmet-edit count inside the
                      // cost vector; whether it can be split back out depends on the level
                      // the solve *ran at*, not the one currently selected — so read it off
                      // the result (ADR-0034/0035, and the Iteration-3 widening).
                      const { unmetEdits, quality, folded } = costReadout(
                        result.schedule.cost,
                        result.preferences?.level ?? null,
                      );
                      // Folded: the preference cost shares a level with the service
                      // objective and was summed into it. Fall back to `SolveResult.quality`
                      // — recomputed from the schedule, so it is unpolluted and keeps the
                      // same five slots whatever the setting.
                      const shown = folded ? (result.quality ?? quality) : quality;
                      return (
                        <>
                          <Stat
                            label={folded ? "service quality" : "objective cost"}
                            value={`[${shown.join(", ")}]`}
                            hint={
                              folded
                                ? `lexicographic [max@P1, max@P2, max@P3, avoid, min], recomputed from the schedule — the raw objective cannot be shown here, because at this stability setting the unmet-preference count is summed into the level it shares (pref_level=${result.preferences?.pref_level})`
                                : "lexicographic: highest-priority max-interval violations first, min-interval last"
                            }
                          />
                          {unmetEdits !== null && (
                            <Stat
                              label="unmet preference halves"
                              value={String(unmetEdits)}
                              hint={`preference weak constraints left unsatisfied — an hour and a mower count separately, and untouched services count too, so this is not a number of edits (pref_level=${result.preferences?.pref_level} at the '${result.preferences?.level}' setting, ADR-0034)`}
                            />
                          )}
                        </>
                      );
                    })()}
                </div>

                {result.preferences && (
                  <PreferenceBanner
                    report={result.preferences}
                    startHour={workingScenario.horizon_start_hour}
                    explanation={explanation.report}
                    explainLoading={explanation.loading}
                    droppedCount={droppedEdits(lastRequest?.preferences ?? NO_PREFERENCES, result.schedule).length}
                    expert={expert}
                    explainBudget={explainBudgetChoice}
                    onExplainBudgetChange={setExplainBudgetChoice}
                    onExplain={() => void explanation.explain(expert ? explainBudgetChoice : undefined)}
                  />
                )}

                <div className="schedule-head">
                  <div className="view-toggle">
                    <button
                      className={view === "chart" ? "active" : ""}
                      onClick={() => setView("chart")}
                    >
                      chart
                    </button>
                    <button
                      className={view === "table" ? "active" : ""}
                      onClick={() => setView("table")}
                    >
                      table
                    </button>
                  </div>
                  <div className="schedule-head-edit">
                    {editingSchedule && (
                      <button
                        type="button"
                        className="link-button"
                        disabled={solving}
                        onClick={() => setAllPins(anyKept ? "released" : "auto")}
                      >
                        {anyKept ? "release all" : "keep all"}
                      </button>
                    )}
                    <Switch
                      label="edit schedule"
                      on={editingSchedule}
                      onChange={(on) => {
                        if (on) return setEditingSchedule(true);
                        // Re-solve is the commit path (ADR-0053), so this is the only way
                        // to lose plan edits — ask before it costs someone their work.
                        const close = () => {
                          setEditingSchedule(false);
                          resetPlanEdits();
                          setReleased(null);
                          setEditingTask(null);
                        };
                        if (planEdited) {
                          setPendingDiscard({ what: "plan", run: close });
                        } else close();
                      }}
                      disabled={solving}
                    />
                  </div>
                </div>

                {/* Local to the toggle that raised it (UI review, 2026-09-24) — see the
                    comment on the scenario-discard variant near the header. */}
                {pendingDiscard?.what === "plan" && (
                  <DiscardPrompt
                    text="Discard your edits to the plan?"
                    onDiscard={() => {
                      const { run } = pendingDiscard;
                      setPendingDiscard(null);
                      run();
                    }}
                    onKeepEditing={() => setPendingDiscard(null)}
                  />
                )}

                {released && (
                  <div className="undo-row">
                    <span>
                      Released {released.area},{" "}
                      {clockLabel(released.start, workingScenario.horizon_start_hour)} — it is out
                      of the next re-solve, but the solver may still move or re-add it to meet the
                      area's service requirements.
                    </span>
                    {/* "Drop a service" is a *requirement* change, not a schedule edit — the
                        preference layer can only ask for a task to be somewhere, never for
                        it to be absent (ADR-0035). This lowers the area's cap to one below
                        what the plan holds and leaves an unsaved draft (ADR-0039). Offered
                        only when the area has 2+ services — the model needs max ≥ 1. */}
                    {canDropService(areaServiceCount(released.area)) && (
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => dropServiceFrom(released.area)}
                      >
                        Drop {released.area} to {areaServiceCount(released.area) - 1}{" "}
                        service{areaServiceCount(released.area) - 1 === 1 ? "" : "s"}/wk →
                      </button>
                    )}
                    <button
                      type="button"
                      className="link-button undo-dismiss"
                      aria-label="dismiss"
                      onClick={() => setReleased(null)}
                    >
                      ×
                    </button>
                  </div>
                )}

                {editingSchedule && (
                  <AddServicePanel
                    scenario={workingScenario}
                    onAdd={addService}
                    disabled={solving}
                  />
                )}

                {planEdited && (
                  <p className="muted schedule-stale">
                    Issues are hidden while the plan is edited — re-solve to check the edited one.
                  </p>
                )}

                {view === "chart" ? (
                  <GanttSchedule
                    scenario={workingScenario}
                    tasks={planTasks}
                    executed={runLog.executed}
                    violations={result.schedule.violations}
                    violationsStale={planEdited}
                    editing={editingSchedule}
                    onSelectTask={(t, at) => setEditingTask({ uid: t.uid, ...at })}
                    onMoveTask={moveTask}
                  />
                ) : (
                  <ScheduleTable
                    scenario={workingScenario}
                    tasks={planTasks}
                    violations={result.schedule.violations}
                    violationsStale={planEdited}
                    editing={editingSchedule}
                    onMove={moveTask}
                    onReassign={(uid, mower) => reassign(uid, mower, workingScenario)}
                    onPin={pinTask}
                  />
                )}

                {editingTask &&
                  (() => {
                    // Look the task up fresh each render: the popover edits it, so the
                    // snapshot captured at click time goes stale immediately. A task can
                    // also vanish under it if a new plan arrives.
                    const t = planTasks.find((p) => p.uid === editingTask.uid);
                    if (!t) return null;
                    return (
                      <TaskEditPopover
                        task={t}
                        scenario={workingScenario}
                        at={{ x: editingTask.x, y: editingTask.y, flip: editingTask.flip }}
                        onMove={(start) => moveTask(t.uid, start)}
                        onReassign={(mower) => reassign(t.uid, mower, workingScenario)}
                        onPin={(pin) => pinTask(t.uid, pin)}
                        onClose={() => setEditingTask(null)}
                      />
                    );
                  })()}

                <RunLogPanel rolls={runLog.rolls} expert={expert} />
              </>
            )}

            {result && !result.solved && (
              <p className="app-error">
                {result.status === "unsatisfiable"
                  ? "No feasible schedule exists — the requirements can't all be met at once."
                  : stoppedByUser
                    ? "Stopped — no feasible schedule found yet."
                    : "The solver found no schedule within the time budget."}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}

/** What the last re-solve actually did with the user's edits (ADR-0031). Reports rather
 *  than promises: `dropped` carries halves the solver could not even express, and the
 *  agreement line states the count that survived.
 *
 *  The legal-but-not-chosen case (Iteration 6) is a separate mechanism, `ExplanationPanel`
 *  below — an edit whose hour was individually illegal ends up in *both* `report.dropped`
 *  (the pre-solve check) and, once explained, `explanation.edits` (`outcome:
 *  "individually_impossible"`); `explainedKeys` suppresses the older row so nothing is
 *  said twice in two different voices. */
function PreferenceBanner({
  report,
  startHour,
  explanation,
  explainLoading,
  droppedCount,
  expert,
  explainBudget,
  onExplainBudgetChange,
  onExplain,
}: {
  report: PreferenceReport;
  startHour: number;
  explanation: ExplanationReport | null;
  explainLoading: boolean;
  droppedCount: number;
  expert: boolean;
  explainBudget: number | undefined;
  onExplainBudgetChange: (v: number | undefined) => void;
  onExplain: () => void;
}) {
  // The "N of M preferences kept overall" clause is expert-only (owner report, 2026-09-24):
  // it fires on every re-solve, edited or not, and a default-view user with no edits of
  // their own has no way to place where the number comes from.
  const sentence = agreementSentence(report, expert);
  const explainedKeys = new Set((explanation?.edits ?? []).map((e) => `${e.area}#${e.start}`));
  const dropped = report.dropped.filter((d) => !explainedKeys.has(`${d.area}#${d.start}`));
  if (!sentence && dropped.length === 0 && droppedCount === 0 && !explanation) return null;
  return (
    <div className="pref-banner">
      {sentence && <p className="pref-agreement">{sentence}</p>}
      {dropped.length > 0 && (
        <ul className="pref-dropped">
          {dropped.map((d, i) => (
            <li key={`${d.area}#${d.start}#${d.half}#${i}`}>
              {d.area}, {clockLabel(d.start, startHour)}: {d.half === "mower" ? "mower" : "time"} not
              applied — {d.reason}
            </li>
          ))}
        </ul>
      )}
      <ExplanationPanel
        droppedCount={droppedCount}
        loading={explainLoading}
        report={explanation}
        startHour={startHour}
        expert={expert}
        level={report.level ?? null}
        explainBudget={explainBudget}
        onExplainBudgetChange={onExplainBudgetChange}
        onExplain={onExplain}
      />
    </div>
  );
}

/** The "Discard …?" confirmation strip, shared by the scenario-discard and plan-discard
 *  variants — same markup, rendered wherever the action it warns about lives (App.tsx). */
function DiscardPrompt({
  text,
  onDiscard,
  onKeepEditing,
}: {
  text: string;
  onDiscard: () => void;
  onKeepEditing: () => void;
}) {
  return (
    <div className="discard-row">
      <span>{text}</span>
      <button type="button" className="danger" onClick={onDiscard}>
        Discard
      </button>
      <button type="button" onClick={onKeepEditing}>
        Keep editing
      </button>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

/** The moving-horizon run log (ADR-0042): one row per "Move forward" step. Default view
 *  gets a plain sentence per roll; expert mode gets the metrics table (quality vector,
 *  solve time) that the offline roll-study also reports. */
function RunLogPanel({ rolls, expert }: { rolls: RollEntry[]; expert: boolean }) {
  if (rolls.length === 0) return null;

  const line = (r: RollEntry) => {
    // The roll itself stands — "now" moved and the past was folded into history — so the
    // row is kept and says what is missing, rather than borrowing the previous plan's
    // figures, which is what it used to do.
    if (r.failed) return `+${r.hours} h → ${r.nowLabel} · no plan — the re-solve did not finish`;
    if (!r.outcome) return `+${r.hours} h → ${r.nowLabel} · re-solving…`;
    const { services, carried, kept } = r.outcome;
    const keptPart =
      carried > 0
        ? kept === carried
          ? `all ${carried} carried tasks kept`
          : `${kept} of ${carried} carried tasks kept`
        : "nothing to carry";
    return `+${r.hours} h → ${r.nowLabel} · ${services} services · ${keptPart}`;
  };

  return (
    <section className="run-log">
      <h3>Run log</h3>
      {expert ? (
        <div className="run-log-scroll">
          <table>
            <thead>
              <tr>
                <th>step</th>
                <th>now</th>
                <th>services</th>
                <th>kept / carried</th>
                <th>stability</th>
                <th>quality</th>
                <th>solve</th>
              </tr>
            </thead>
            <tbody>
              {rolls.map((r, i) => (
                <tr key={i}>
                  <td>+{r.hours} h</td>
                  <td>{r.nowLabel}</td>
                  <td>{r.outcome ? r.outcome.services : r.failed ? "no plan" : "…"}</td>
                  <td>{r.outcome ? `${r.outcome.kept} / ${r.outcome.carried}` : "—"}</td>
                  {/* Which arm this row is, so the columns beside it stay comparable
                      across a run where the setting changed. "cold" = nothing was
                      carried at all; "heuristic" is a carried plan priced at nothing. */}
                  <td>{r.outcome ? (r.outcome.setting ?? "cold") : "—"}</td>
                  <td>
                    {r.outcome?.quality ? `[${r.outcome.quality.join(", ")}]` : "—"}
                    {r.outcome && !r.outcome.optimal && " (best so far)"}
                  </td>
                  <td>{r.outcome ? `${r.outcome.solveTimeS.toFixed(1)} s` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ol className="run-log-lines">
          {rolls.map((r, i) => (
            <li key={i}>{line(r)}</li>
          ))}
        </ol>
      )}
    </section>
  );
}
