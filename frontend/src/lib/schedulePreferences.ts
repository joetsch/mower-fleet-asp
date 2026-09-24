// Turning the schedule on screen into a re-solve payload (ADR-0031 / ADR-0034 / ADR-0035 /
// ADR-0038).
//
// The mechanism is always the pilot's choice — weak constraints — over one payload: every
// task on screen the user has not *released*. There is no longer a mode enum (ADR-0038
// dropped "keep my edits" and made "from scratch" a one-shot button); a re-solve either
// keeps the plan or, from the "Re-solve from scratch" button, carries no payload at all.
//
// What *is* adjustable, in expert mode only, is the **stability setting**: the priority
// level those weak constraints sit at — how much one churned task is worth against the
// service objective — or, as a fifth option, the heuristic mechanism instead of weak
// constraints altogether (ADR-0032, ADR-0034 decision 4). The default view, and this
// module's default argument, stay at `top`.
//
// Why keep the plan by default: stability is the normal case for a greenkeeper, and making
// it opt-in means pinning every bar by hand. It is also free — a whole-plan preference set
// is the same payload as a three-task one, just longer. "Release all" then re-pin a few is
// the path for "re-optimise most of the week, keep these".
//
// The honest caveat this module exists to support: a preference is an *optimisation*, not
// a guarantee (ADR-0031). Even under `weak@top` the untouched part of the week moves
// (measured churn 0.663 at a freeze fraction of 0.3), so the UI reports what actually
// survived rather than promising anything up front.

import type {
  PreferenceLevel,
  PreferenceReport,
  PreferredTask,
  SolvePreferences,
} from "../types";

/** Per-task keep state. `auto` and `released` are the only two the UI sets; `pinned` is
 *  kept in the type as a synonym of `auto` (ADR-0038 folded the old "keep my edits" pin
 *  into the default) so older working copies deserialise without a migration. */
export type TaskPinState = "auto" | "pinned" | "released";

/** One task of the working copy of the displayed plan.
 *
 *  `uid` is synthesized when a plan is adopted, never derived from `ScheduledTask.task` —
 *  that field is a rank within the area, reassigned by every solve (ADR-0031).
 */
export interface PlanTask {
  uid: string;
  area: string;
  mower: string;
  start: number;
  end: number;
  pin: TaskPinState;
  /** The user changed this task's start or mower. */
  edited: boolean;
  /** The user asked for this task; it was not in the solved plan. */
  added: boolean;
  /** The rank this task had in the plan it came from — the only thing `Violation.task`
   *  can be joined on. Null for an added task. Never used as an identity: the next solve
   *  renumbers freely (ADR-0031), which is why `uid` exists. */
  sourceTask: number | null;
  /** Where the solver actually put this task — `null` for one the user added, which the
   *  solver never placed. Releasing a task restores it here and clears `edited` (owner
   *  report, 2026-09-24: drag, then release, otherwise left the dragged hour and
   *  `edited: true` sitting under a released task with no preference behind either — a
   *  click on "keep" would silently resurrect the drag as an edit). */
  solved: { start: number; end: number; mower: string } | null;
}

/** `mode: "off"` with no tasks — the payload that leaves the solve byte-identical to one
 *  carrying no preferences at all (ADR-0031 decision 5). Sent by "Re-solve from scratch",
 *  and by the first solve (empty plan). */
export const NO_PREFERENCES: SolvePreferences = { tasks: [], mode: "off", level: "top" };

function originOf(t: PlanTask): PreferredTask["origin"] {
  if (t.added) return "added";
  return t.edited ? "edited" : "frozen";
}

/** Would this task be in the next re-solve's payload? Every task except a released one
 *  (ADR-0038). The per-task control's verb turns on it: a kept task offers **release**, a
 *  released one offers **keep**. */
export function isInPayload(t: PlanTask): boolean {
  return t.pin !== "released";
}

/** What the expert-mode selector offers: the four weak-constraint prices, the heuristic
 *  mechanism (not a price at all), and `cold` — no stability at all, the week re-planned
 *  from scratch every step (UI review 2026-09-10).
 *
 *  `cold` exists because the heuristic mechanism has a measured cost — 6× the solve time
 *  at 0.69 agreement (ADR-0043) — and a greenkeeper who does not care about churn is
 *  better served by paying neither that nor the weak-constraint distortion. It is the
 *  study's own baseline made reachable: `preferencesFor` collapses it to `NO_PREFERENCES`,
 *  so a roll at `cold` reaches the wire byte-identical to "Re-solve from scratch".
 *
 *  `tiebreak` is deliberately absent — ADR-0034 decision 2 measured it as indistinguishable
 *  from ignoring the plan on any instance the solver cannot prove within budget. The API
 *  still accepts it, for the study. */
export type StabilitySetting = PreferenceLevel | "heuristic" | "cold";

/** Turn a preference list into the payload one stability setting implies.
 *
 *  The four weak settings differ only in `level` — what one churned task costs against the
 *  service objective. `heuristic` is a different *mechanism* (ADR-0032): `#heuristic`
 *  directives bias which atoms the search decides first and leave the objective untouched,
 *  so there is no quality distortion by construction — and no guarantee either, since a
 *  heuristic makes no argument about what survives when the budget cuts in (ADR-0034
 *  measured 0.919 agreement against `weak@top`'s 1.000).
 *
 *  `level` still travels in heuristic mode because the request schema requires it; the
 *  backend ignores it there and `PreferenceReport.level` comes back null, which is what
 *  `costReadout` keys off.
 *
 *  An empty list stays `NO_PREFERENCES` whatever the setting: the setting must never turn a
 *  dead payload into a live one, or a cold solve's request body would stop being
 *  byte-identical to the pre-Iteration-5 one (ADR-0031 decision 5). */
export function preferencesFor(
  tasks: PreferredTask[],
  setting: StabilitySetting = "top",
): SolvePreferences {
  // `cold` carries nothing even with a full plan in hand — no weak constraints, no
  // heuristic, a plain re-solve of the (rolled) scenario every step.
  if (tasks.length === 0 || setting === "cold") return NO_PREFERENCES;
  return setting === "heuristic"
    ? { mode: "heuristic", level: "top", tasks }
    : { mode: "weak", level: setting, tasks };
}

/** Area name -> count of tasks released from it (`pin === "released"`) in this working
 *  copy — the counterpart to `toPreferences`' payload, for `POST /api/explain`'s
 *  `released` field (2026-09-24 amendment). The backend has no other way to see a
 *  release: it is the *absence* of a task from the payload, not a value in it, and
 *  without this the `reinstated` explanation reads a released service as "you asked for
 *  fewer" — the opposite of what releasing means. An added task released before ever
 *  being solved is not counted here — it never had a "before" position to explain. */
export function releasedCounts(tasks: PlanTask[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const t of tasks) {
    if (t.pin !== "released" || t.added) continue;
    counts[t.area] = (counts[t.area] ?? 0) + 1;
  }
  return counts;
}

/** The re-solve payload: every task the user has not released, under the stability setting
 *  (expert mode; `top` — today's behaviour — everywhere else). */
export function toPreferences(
  tasks: PlanTask[],
  setting: StabilitySetting = "top",
): SolvePreferences {
  return preferencesFor(
    tasks.filter(isInPayload).map((t) => ({
      area: t.area,
      start: t.start,
      mower: t.mower,
      origin: originOf(t),
    })),
    setting,
  );
}

/** What became of the payload. Two quantities, because they answer different questions:
 *  what happened to what the user explicitly asked for, and how much of everything sent
 *  survived — the latter being the optimisation score itself, since a re-solve is MaxSAT
 *  over the preferences and an untouched service is an implicit preference (ADR-0051). */
export interface AgreementSummary {
  edits: { kept: number; total: number };
  /** Every preference, explicit and implicit alike. */
  overall: { kept: number; total: number };
  /** The user's own edits whose hour survived but whose mower did not — partial success,
   *  which ADR-0031 scores as such. `null` when it cannot be derived (see below). */
  editsOnAnotherMower: number | null;
}

const EMPTY = { total: 0, time_kept: 0, mower_total: 0, mower_kept: 0 };

export function summariseAgreement(report: PreferenceReport | null): AgreementSummary | null {
  if (!report) return null;
  const by = report.agreement.by_origin ?? {};
  const edited = by.edited ?? EMPTY;
  const added = by.added ?? EMPTY;
  const edits = {
    kept: edited.time_kept + added.time_kept,
    total: edited.total + added.total,
  };
  const overall = { kept: report.agreement.time_kept, total: report.agreement.total };
  if (overall.total === 0) return null;
  // The backend keys the mower half on the *requested* hour, so `mower_kept` already
  // implies `time_kept`, and `time_kept - mower_kept` is exactly "kept the hour, different
  // mower" — under two conditions, both checked rather than assumed: every edit named a
  // mower (`toPreferences` always sends one; a time-only preference, ADR-0031, would not),
  // and that backend invariant still holds. Otherwise report nothing: a missing clause is
  // honest, a wrong count is the bug this replaced (ADR-0051).
  const named = edited.mower_total + added.mower_total;
  const mowerKept = edited.mower_kept + added.mower_kept;
  const editsOnAnotherMower =
    named === edits.total && mowerKept <= edits.kept ? edits.kept - mowerKept : null;
  return { edits, overall, editsOnAnotherMower };
}

/** Whether this re-solve's payload actually constrained the optimum, and whether it
 *  carried any of the user's own edits — the two facts the "proven optimal" wording needs
 *  (ADR-0051).
 *
 *  Both were previously assumed from "a payload exists", which overclaimed twice: in
 *  heuristic mode the directives bias the search and leave the objective alone (ADR-0032),
 *  so a proven optimum is the plain one; and a Move forward carries the whole plan as
 *  implicit preferences with no edit anywhere in it, so "keeps your edits" named something
 *  the user never did. */
export function optimumContext(report: PreferenceReport | null): {
  constrained: boolean;
  hasEdits: boolean;
} {
  if (!report) return { constrained: false, hasEdits: false };
  const by = report.agreement.by_origin ?? {};
  const edits = (by.edited?.total ?? 0) + (by.added?.total ?? 0);
  return { constrained: report.level !== null, hasEdits: edits > 0 };
}

/** The one line shown after a re-solve. Deliberately factual — never "held" or "locked",
 *  which would promise something a weak constraint does not (ADR-0031).
 *
 *  `showOverall` gates the "N of M preferences kept overall" clause — the churn count
 *  across *every* service, edited or not. Default view drops it: with no edits of their
 *  own (the common case — a plain Move forward, or a first solve) the sentence would open
 *  with a number the user never asked about and cannot place the source of (owner report,
 *  2026-09-24 — "I'm not sure we should have it in standard-user mode ... he doesn't know
 *  where this is coming from"). Expert mode is where churn-vs-stability is the point
 *  (ADR-0043), so it stays there. */
export function agreementSentence(
  report: PreferenceReport | null,
  showOverall: boolean = true,
): string | null {
  const s = summariseAgreement(report);
  if (!s) return null;
  const parts: string[] = [];
  if (s.edits.total > 0) {
    parts.push(
      s.edits.kept === s.edits.total
        ? `All ${s.edits.total} of your edits kept`
        : `${s.edits.kept} of ${s.edits.total} of your edits kept`,
    );
  }
  if (s.editsOnAnotherMower) {
    parts.push(`${s.editsOnAnotherMower} of those on a different mower`);
  }
  // The overall count, which is what the solver actually optimised. Never "unchanged":
  // only the hour is checked, so a service on a different mower still counts as kept
  // here, and the mower qualifier above is the only place that distinction is made.
  // Suppressed when every preference *is* an edit — it would restate the first clause.
  if (showOverall && s.overall.total > s.edits.total) {
    parts.push(`${s.overall.kept} of ${s.overall.total} preferences kept overall`);
  }
  return parts.join(" · ");
}
