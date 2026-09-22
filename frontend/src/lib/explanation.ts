// Turning an `ExplanationReport` (POST /api/explain, Iteration 6) into UI copy.
//
// Sentences are composed here from the structured fields (`outcome`, `conflicts`, …),
// never from the backend's `detail` string — `detail` carries raw hour offsets and some
// internal phrasing meant for logs/tests, not a golf-course manager. Same precedent as
// `lib/solver.ts`'s hardcoded gloss and `schedulePreferences.ts`'s `agreementSentence`.
//
// Copy rules, pinned by the tests in this module:
// - "a reason", never "the reason" — the literature note (`docs/explainability-
//   literature.md` §5.4) measured several distinct minimal conflicts for the same
//   instance, so claiming uniqueness would be a false claim, not just a hedge.
// - `not_determined` never implies impossible — it is a budget timeout, nothing more.
// - `minimal === false` flags the conflict as not proven smallest, never as wrong: the
//   keep-on-timeout degradation rule only ever leaves a conflict larger than necessary.
// - `not_yet_found` points at the search having stopped short, not at the edit being bad.

import { clockLabel } from "./schedule";
import type {
  ConflictingTask,
  EditExplanation,
  PreferredTask,
  ReinstatedService,
  RippleMove,
  Schedule,
  SolvePreferences,
} from "../types";

/** Every edited/added preference not realised in `schedule` — a client-side mirror of the
 *  backend's own dropped-edit filter (`explain/__init__.py`'s `explain_scenario`), so the
 *  UI can offer "Why?" (and count how many edits it would explain) without a solver call.
 *  The server stays the authority on the actual explanation. */
export function droppedEdits(
  preferences: SolvePreferences,
  schedule: Schedule | null,
): PreferredTask[] {
  if (!schedule) return [];
  const keptStarts = new Set(schedule.tasks.map((t) => `${t.area}#${t.start}`));
  return preferences.tasks.filter(
    (p) => (p.origin === "edited" || p.origin === "added") && !keptStarts.has(`${p.area}#${p.start}`),
  );
}

/** One sentence per outcome — the *reason* half of an `EditExplanation`. The edit's own
 *  area/clock/mower is rendered by the caller alongside this, not repeated here. */
export function explanationSentence(edit: EditExplanation): string {
  switch (edit.outcome) {
    case "blocked_statically":
    case "conflicts_with":
      return edit.conflicts.length === 1
        ? "conflicts with a kept task"
        : `conflicts with ${edit.conflicts.length} kept tasks`;
    case "individually_impossible":
      return "no schedule could ever place this — a rule of the scenario rules it out on " +
        "its own (an unavailable hour, or no mower able to reach it)";
    case "not_yet_found":
      return "a schedule keeping this alongside everything else you kept does exist — the " +
        "search had not found it yet";
    case "not_determined":
      return "ran out of time working this out before reaching an answer";
  }
}

/** The kept tasks a conflict names, as one line: "Green 3 Mon 16:00 (M1), …". */
export function conflictText(conflicts: ConflictingTask[], startHour: number): string {
  return conflicts
    .map((c) => `${c.area} ${clockLabel(c.start, startHour)}${c.mower ? ` (${c.mower})` : ""}`)
    .join(", ");
}

/** Set only when minimisation ran (`conflicts_with`) and the budget cut it short — never
 *  disputes the conflict, only that it might name more tasks than strictly necessary. */
export function minimalCaveat(edit: EditExplanation): string | null {
  if (edit.outcome !== "conflicts_with" || edit.minimal) return null;
  return "not proven to be the smallest possible conflict — the explain budget ran out first";
}

export function reinstatedSentence(r: ReinstatedService): string {
  const added = r.actual_count - r.submitted_count;
  const noun = r.min_services === 1 ? "service" : "services";
  return `${r.area} needs at least ${r.min_services} ${noun} this week — you asked for ` +
    `${r.submitted_count}, so the solver added ${added} back`;
}

export function rippleSentence(r: RippleMove): string {
  const parts: string[] = [];
  if (r.shares_mower_with) parts.push(`shares a mower with your edit to ${r.shares_mower_with}`);
  if (r.shares_area_with) parts.push("shares this area with another edit you made");
  return `moved — ${parts.join(" and ")}`;
}
