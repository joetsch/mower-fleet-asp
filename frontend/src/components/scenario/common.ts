// Shared prop shape for the ScenarioSummary tab bodies. Each tab is a read branch + an
// edit branch over the same data; ScenarioSummary owns the state (`tab`, `tip`) and the
// derived `areas` list, and threads these down.

import type { FieldError } from "../../lib/scenarioValidation";
import type { Area, Scenario } from "../../types";

export interface TabProps {
  scenario: Scenario;
  /** Sorted for the read view, original order while editing (see ScenarioSummary). */
  areas: Area[];
  editing: boolean;
  errors: FieldError[];
  solving: boolean;
  /** `onChange` wrapper — push a new scenario up. */
  edit: (next: Scenario) => void;
  /** Stable React key for an area row (`a.name` when read-only, positional while editing). */
  areaKey: (a: Area, i: number) => string;
}

export type Tip = { x: number; y: number; text: string } | null;
