// Domain + API types for the frontend.
//
// These are NOT hand-written: `src/api/schema.ts` is generated from the backend's
// OpenAPI document (`openapi.json`) by `openapi-typescript` — run `npm run types:generate`
// after any response-shape change, and `npm run types:check` (in the gate) fails on drift.
// This module is the thin, stable naming layer over that generated file, so the rest of
// the app keeps importing `Scenario`, `SolveResult`, … from `./types` unchanged.
//
// `Response<T>` un-optionalises every property: a pydantic field with `default=None`
// (`Area.size_m2`, `SolveResult.schedule`, …) is absent from the schema's `required`
// list, so `openapi-typescript` types it `x?: T | null` — but pydantic *always*
// serialises it, so in a **response** it is present-and-possibly-null, never missing.
// Tuple element types (`[number, number]`) are preserved.
//
// The only genuinely hand-authored entry is `Interval` (a tuple alias, a convenience)
// and `ViolationKind` (the documented value set of the backend's plain
// `Violation.kind: str` field — a domain note, not a schema mirror).

import type { components } from "./api/schema";

type Schemas = components["schemas"];

type Response<T> = T extends readonly unknown[]
  ? { [K in keyof T]: Response<T[K]> }
  : T extends object
    ? { [K in keyof T]-?: Response<T[K]> }
    : T;

export type Interval = [number, number];

export type DaySchedule = Response<Schemas["DaySchedule"]>;
export type Area = Response<Schemas["Area"]>;
export type Mower = Response<Schemas["Mower"]>;
export type BaseDuration = Response<Schemas["BaseDuration"]>;
export type ServiceEvent = Response<Schemas["ServiceEvent"]>;
export type Scenario = Response<Schemas["Scenario"]>;

// --- Scenario library (GET /api/scenarios, GET /api/scenario/{id}) ---

export type ScenarioSummary = Response<Schemas["ScenarioSummary"]>;
export type ScenarioListEntry = Response<Schemas["ScenarioListEntry"]>;
export type ScenarioDerived = Response<Schemas["ScenarioDerived"]>;
export type ScenarioBundle = Response<Schemas["ScenarioBundle"]>;

// --- Catalogue (GET /api/catalog) — the editor's defaults source (ADR-0024) ---

export type CatalogMower = Response<Schemas["CatalogMower"]>;
export type CatalogAreaType = Response<Schemas["CatalogAreaType"]>;
export type Catalog = Response<Schemas["Catalog"]>;

// --- Solve result (POST /api/solve, GET /api/solve/{job_id}) ---

export type ScheduledTask = Response<Schemas["ScheduledTask"]>;

/** The value set the backend documents for `Violation.kind` (a plain `str` on the wire). */
export type ViolationKind = "max_interval" | "min_interval" | "avoid_zone";

export type Violation = Response<Schemas["Violation"]>;
export type Schedule = Response<Schemas["Schedule"]>;
export type SolverInfo = Response<Schemas["SolverInfo"]>;
export type SolveResult = Response<Schemas["SolveResult"]>;

// --- Schedule edits carried into a re-solve (ADR-0031 / ADR-0034) ---
//
// A preference names a *position* — "this area has a task starting at hour S, on mower M"
// — never a task index, because the encoding numbers an area's tasks by rank and the very
// next solve is free to renumber them. `origin` is for this UI's own reporting; the solver
// never sees it.

export type PreferredTask = Response<Schemas["PreferredTask"]>;
export type SolvePreferences = Response<Schemas["SolvePreferences"]>;
export type AgreementCounts = Response<Schemas["AgreementCounts"]>;
export type PreferenceAgreement = Response<Schemas["PreferenceAgreement"]>;
export type DroppedPreference = Response<Schemas["DroppedPreference"]>;
export type PreferenceReport = Response<Schemas["PreferenceReport"]>;

/** What one churned task is worth against the service objective — the expert-mode
 *  stability setting. Aliased off `SolvePreferences` rather than re-typed, so widening the
 *  set backend-side reaches here through `npm run types:check`. */
export type PreferenceLevel = SolvePreferences["level"];

// --- Anytime solving / stop button (ADR-0022) ---

export type SolveJobStarted = Response<Schemas["SolveJobStarted"]>;
export type SolveJobStatus = Response<Schemas["SolveJobStatus"]>;

// --- Moving time horizon (POST /api/scenario/advance, ADR-0042) ---

export type RollResponse = Response<Schemas["RollResponse"]>;

// --- Explainability (POST /api/explain, Iteration 6) ---

export type ConflictingTask = Response<Schemas["ConflictingTask"]>;
export type EditExplanation = Response<Schemas["EditExplanation"]>;
export type ReinstatedService = Response<Schemas["ReinstatedService"]>;
export type RippleMove = Response<Schemas["RippleMove"]>;
export type ExplanationReport = Response<Schemas["ExplanationReport"]>;
