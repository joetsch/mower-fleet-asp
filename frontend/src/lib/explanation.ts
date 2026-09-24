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
  PreferenceLevel,
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
 *  area/clock/mower is rendered by the caller alongside this, not repeated here.
 *
 *  `level` is the stability setting the *re-solve* ran at (`PreferenceReport.level`), and
 *  only the `not_yet_found` wording depends on it. "The search had not found it yet" is a
 *  claim about the search, and it rests on preferences outranking every service-quality
 *  level — true at `top`, the default and the only setting outside expert mode. Below that
 *  the optimiser may trade the edit for plan quality at a *proven* optimum, so blaming the
 *  search would be false; `null` (heuristic mode) has no preference objective at all. */
export function explanationSentence(
  edit: EditExplanation,
  level?: PreferenceLevel | null,
): string {
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
      return level === undefined || level === "top"
        ? "a schedule keeping this alongside everything else you kept does exist — the " +
          "search had not found it yet"
        : "a schedule keeping this alongside everything else you kept does exist — at this " +
          "stability setting the solver may trade an edit away for a better plan";
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

/** The area name is *not* repeated here — `ExplanationPanel` already prefixes the row.
 *
 *  `submitted_count` is the plan *before* this re-solve — the payload plus whatever was
 *  released from the area (2026-09-24 amendment) — never "what you asked for": a released
 *  service is the opposite of asking, and a sentence that named it that way is exactly
 *  what a released service reappearing must not say (owner report, pre-workshop review).
 *  Never "back in the plan" either — that implies something was removed and came back,
 *  which is not true of a genuinely new addition, and the wording no longer distinguishes
 *  the two on the frontend's side (the backend already does, via `released`).
 *
 *  Only `forced_by_minimum` licenses naming the minimum as the cause. The backend raises
 *  this row on a plain count mismatch, and the commonest case — releasing a service from
 *  an area already at its minimum — is precisely the one the minimum did *not* force; the
 *  extra service is the max-interval objective's doing. Saying "needs at least N" there
 *  asserts a cause nobody checked. */
export function reinstatedSentence(r: ReinstatedService): string {
  const added = r.actual_count - r.submitted_count;
  const services = added === 1 ? "service" : "services";
  if (!r.forced_by_minimum) {
    return `the solver added ${added} ${services} that ${added === 1 ? "was" : "were"} not ` +
      `in the plan before — the minimum was already met, so this is most likely to shorten ` +
      `the gaps between services`;
  }
  const noun = r.min_services === 1 ? "service" : "services";
  return `needs at least ${r.min_services} ${noun} this week — the plan had ` +
    `${r.submitted_count}, so the solver added ${added}`;
}

export function rippleSentence(r: RippleMove): string {
  const parts: string[] = [];
  if (r.shares_mower_with) parts.push(`shares a mower with your edit to ${r.shares_mower_with}`);
  if (r.shares_area_with) parts.push("shares this area with another edit you made");
  return `moved — ${parts.join(" and ")}`;
}
