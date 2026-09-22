// Thin typed wrapper around the backend HTTP API.
// In dev, Vite proxies /api -> http://127.0.0.1:8000 (see vite.config.ts).

import type {
  Catalog,
  ExplanationReport,
  RollResponse,
  Scenario,
  ScenarioBundle,
  ScenarioDerived,
  ScenarioListEntry,
  Schedule,
  ScheduledTask,
  SolveJobStarted,
  SolveJobStatus,
  SolvePreferences,
} from "../types";

async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return response.json() as Promise<T>;
}

/** The curated scenario library — drives the "Load scenario" picker. */
export function fetchScenarios(): Promise<ScenarioListEntry[]> {
  return getJSON<ScenarioListEntry[]>("/api/scenarios");
}

/** One scenario plus its recomputed derived figures (`{bounds, load_factor}`, ADR-0024). */
export function fetchScenario(id: string): Promise<ScenarioBundle> {
  return getJSON<ScenarioBundle>(`/api/scenario/${encodeURIComponent(id)}`);
}

/** The mower/area-type reference catalogue — the editor's defaults source (ADR-0024). */
export function fetchCatalog(): Promise<Catalog> {
  return getJSON<Catalog>("/api/catalog");
}

/** A minimal, unsaved scenario for "New scenario…" (ADR-0027). Nothing is written — the
 * editor opens it as a draft; it reaches the library only via a later save. */
export function fetchScenarioTemplate(name?: string): Promise<ScenarioBundle> {
  return getJSON<ScenarioBundle>("/api/scenario/template", {
    method: "POST",
    body: JSON.stringify(name ? { name } : {}),
  });
}

/** Recompute bounds + load factor for an edited scenario (ADR-0024). */
export function fetchDerived(scenario: Scenario, signal?: AbortSignal): Promise<ScenarioDerived> {
  return getJSON<ScenarioDerived>("/api/scenario/derived", {
    method: "POST",
    body: JSON.stringify(scenario),
    signal,
  });
}

/** Advance "now" for the moving horizon (ADR-0042): slide the window forward by `hours`,
 * fold the now-past plan (`tasks`) into history, and hand back the still-future plan as
 * frozen preferences. The caller then re-solves the returned scenario carrying `carried`. */
export function advanceScenario(
  scenario: Scenario,
  tasks: ScheduledTask[],
  hours: number,
): Promise<RollResponse> {
  return getJSON<RollResponse>("/api/scenario/advance", {
    method: "POST",
    body: JSON.stringify({ scenario, tasks, hours }),
  });
}

// Scenario persistence (ADR-0026): the library is writable. Slug validation and the
// scenario.name = slug rule are enforced server-side; the client just shapes the request.

/** Save the edited scenario over an existing slug — create or overwrite ("Save"). */
export function saveScenario(id: string, scenario: Scenario): Promise<ScenarioBundle> {
  return getJSON<ScenarioBundle>(`/api/scenario/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(scenario),
  });
}

/** Save the edited scenario under a new slug ("Save as…"). Rejects (409) if taken. */
export function createScenario(id: string, scenario: Scenario): Promise<ScenarioBundle> {
  return getJSON<ScenarioBundle>("/api/scenarios", {
    method: "POST",
    body: JSON.stringify({ id, scenario }),
  });
}

/** Remove a scenario from the library ("Delete"). */
export async function deleteScenario(id: string): Promise<void> {
  const response = await fetch(`/api/scenario/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}${detail ? ` — ${detail}` : ""}`);
  }
}

// Anytime solving / stop button (ADR-0022): solving is a job you start, poll, and can
// cancel — not one blocking request. `startSolve` returns as soon as the search begins.
//
// `clingoArgs` is the expert-mode command-line override (ADR-0023) — omitted (or empty)
// means "use the API's default portfolio", same request shape as before that feature.
export type SolveTarget = { scenarioId: string } | { scenario: Scenario };

/** The optional half of a solve request. An options object rather than more positional
 *  parameters: `preferences` is the fourth thing a caller might want to vary, and
 *  `startSolve(t, 20, undefined, undefined, prefs)` is not a call anyone should have to
 *  write or read. */
export interface SolveOptions {
  timeLimitS?: number;
  /** Expert-mode command-line override (ADR-0023); omitted means the API's portfolio. */
  clingoArgs?: string[];
  /** Schedule edits to preserve on a re-solve (ADR-0031). Omitted, or `mode: "off"`,
   *  leaves the solver program exactly as a cold solve's. */
  preferences?: SolvePreferences;
  signal?: AbortSignal;
}

export function startSolve(
  target: SolveTarget,
  { timeLimitS = 20, clingoArgs, preferences, signal }: SolveOptions = {},
): Promise<SolveJobStarted> {
  const targetBody =
    "scenario" in target ? { scenario: target.scenario } : { scenario_id: target.scenarioId };
  return getJSON<SolveJobStarted>("/api/solve", {
    method: "POST",
    body: JSON.stringify({
      ...targetBody,
      time_limit_s: timeLimitS,
      ...(clingoArgs && clingoArgs.length ? { clingo_args: clingoArgs } : {}),
      // Send nothing at all rather than `mode: "off"`, so a cold solve's request body is
      // byte-identical to what it was before this feature existed (ADR-0031 decision 5).
      ...(preferences && preferences.mode !== "off" ? { preferences } : {}),
    }),
    signal,
  });
}

/** Non-blocking check-in — call on an interval while a job is running. */
export function pollSolve(jobId: string, signal?: AbortSignal): Promise<SolveJobStatus> {
  return getJSON<SolveJobStatus>(`/api/solve/${encodeURIComponent(jobId)}`, { signal });
}

/** The stop button: ends the search early and returns the final (best-so-far) result. */
export function cancelSolve(jobId: string, signal?: AbortSignal): Promise<SolveJobStatus> {
  return getJSON<SolveJobStatus>(`/api/solve/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    signal,
  });
}

// Explainability (Iteration 6): why a submitted edit was not kept. A plain synchronous
// call, not a job like startSolve/pollSolve — the backend bounds it with `budgetS`
// (expert-mode setting, capped at 60s server-side) and there is nothing to poll or cancel.
//
// `target`, `preferences` and `schedule` must be the exact triple from the solve being
// explained — a second solve under the `-t4` portfolio could return a different equally-
// optimal plan (ADR-0007), which would explain a schedule the user is not looking at.
export function explainSolve(
  target: SolveTarget,
  preferences: SolvePreferences,
  schedule: Schedule,
  budgetS?: number,
  signal?: AbortSignal,
): Promise<ExplanationReport> {
  const targetBody =
    "scenario" in target ? { scenario: target.scenario } : { scenario_id: target.scenarioId };
  return getJSON<{ report: ExplanationReport }>("/api/explain", {
    method: "POST",
    body: JSON.stringify({
      ...targetBody,
      preferences,
      schedule,
      ...(budgetS !== undefined ? { budget_s: budgetS } : {}),
    }),
    signal,
  }).then((r) => r.report);
}
