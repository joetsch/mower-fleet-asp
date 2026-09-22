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

/** What became of the payload, split the way the user thinks about it: their own edits
 *  on one side, the rest of the plan on the other. */
export interface AgreementSummary {
  edits: { kept: number; total: number };
  rest: { kept: number; total: number };
  /** Preferences whose hour survived but whose mower did not. */
  movedToAnotherMower: number;
}

const EMPTY = { total: 0, time_kept: 0, mower_total: 0, mower_kept: 0 };

export function summariseAgreement(report: PreferenceReport | null): AgreementSummary | null {
  if (!report) return null;
  const by = report.agreement.by_origin ?? {};
  const edited = by.edited ?? EMPTY;
  const added = by.added ?? EMPTY;
  const frozen = by.frozen ?? EMPTY;
  const edits = {
    kept: edited.time_kept + added.time_kept,
    total: edited.total + added.total,
  };
  const rest = { kept: frozen.time_kept, total: frozen.total };
  if (edits.total === 0 && rest.total === 0) return null;
  const mowerTotal = edited.mower_total + added.mower_total + frozen.mower_total;
  const mowerKept = edited.mower_kept + added.mower_kept + frozen.mower_kept;
  return { edits, rest, movedToAnotherMower: mowerTotal - mowerKept };
}

/** The one line shown after a re-solve. Deliberately factual — never "held" or "locked",
 *  which would promise something a weak constraint does not (ADR-0031). */
export function agreementSentence(report: PreferenceReport | null): string | null {
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
  // "other tasks" asked the reader to hold two categories in mind (ADR-0038 decision 4);
  // the count of tasks that stayed put is the whole point, so say just that.
  if (s.rest.total > 0) parts.push(`${s.rest.kept} of ${s.rest.total} tasks unchanged`);
  if (s.movedToAnotherMower > 0) {
    parts.push(`${s.movedToAnotherMower} kept the hour but changed mower`);
  }
  return parts.join(" · ");
}
