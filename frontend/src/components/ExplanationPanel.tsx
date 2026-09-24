// "Why?" — the Iteration 6 explainability surface, rendered inside the preference banner
// (`App.tsx`'s `PreferenceBanner`). On demand only: explaining costs a real (deterministic,
// single-threaded) solver call, so nothing here fires until the user asks.

import { clockLabel } from "../lib/schedule";
import {
  conflictText,
  explanationSentence,
  minimalCaveat,
  reinstatedSentence,
  rippleSentence,
} from "../lib/explanation";
import type { EditExplanation, ExplanationReport, PreferenceLevel } from "../types";

export interface ExplanationPanelProps {
  /** How many currently-dropped edits an explain call would cover. */
  droppedCount: number;
  loading: boolean;
  report: ExplanationReport | null;
  startHour: number;
  /** The stability setting the re-solve ran at, from `PreferenceReport.level` (null in
   *  heuristic mode). Only the "a schedule does exist" wording depends on it. */
  level: PreferenceLevel | null;
  /** Solver diagnostics (solve time, minimality, the budget field) are expert-only —
   *  ADR-0011's progressive-disclosure rule; the plain-language answer stays default view. */
  expert: boolean;
  explainBudget: number | undefined;
  onExplainBudgetChange: (v: number | undefined) => void;
  onExplain: () => void;
}

export function ExplanationPanel({
  droppedCount,
  loading,
  report,
  startHour,
  expert,
  level,
  explainBudget,
  onExplainBudgetChange,
  onExplain,
}: ExplanationPanelProps) {
  if (droppedCount === 0 && !report) return null;

  return (
    <div className="explain-panel">
      {droppedCount > 0 && !report && (
        <div className="explain-trigger">
          <button className="link-button" onClick={onExplain} disabled={loading}>
            {loading
              ? "working…"
              : `Why weren't ${droppedCount} edit${droppedCount === 1 ? "" : "s"} kept?`}
          </button>
          {expert && (
            <label className="explain-budget muted">
              budget{" "}
              <input
                type="number"
                aria-label="explain budget"
                min={1}
                max={60}
                placeholder="8"
                value={explainBudget ?? ""}
                disabled={loading}
                onChange={(e) => {
                  // Clamped here, not left to the backend's `gt=0, le=60` validation
                  // (`ExplainRequest.budget_s`) — that path exists for a body built some
                  // other way, but a 422 surfacing through the generic error banner for a
                  // typo in this one field would be a needless round trip.
                  if (e.target.value === "") {
                    onExplainBudgetChange(undefined);
                    return;
                  }
                  const n = Number(e.target.value);
                  onExplainBudgetChange(Number.isNaN(n) ? undefined : Math.min(60, Math.max(1, n)));
                }}
              />{" "}
              s
            </label>
          )}
        </div>
      )}
      {report && (
        <ul className="explain-report">
          {report.edits.map((edit) => (
            <EditExplanationRow
              key={`edit#${edit.area}#${edit.start}`}
              edit={edit}
              startHour={startHour}
              expert={expert}
              level={level}
            />
          ))}
          {report.reinstated.map((r) => (
            <li key={`reinstated#${r.area}`}>
              {r.area}: {reinstatedSentence(r)}
            </li>
          ))}
          {report.ripple.map((r) => (
            <li key={`ripple#${r.area}#${r.start}`}>
              {r.area}, {clockLabel(r.start, startHour)}
              {r.mower ? ` (${r.mower})` : ""}: {rippleSentence(r)}
            </li>
          ))}
          {expert && report.budget_exhausted && (
            <li className="muted">
              the {report.budget_s}s explain budget ran out before every edit could be checked —
              the rest above are reported "ran out of time", not "impossible"
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function EditExplanationRow({
  edit,
  startHour,
  expert,
  level,
}: {
  edit: EditExplanation;
  startHour: number;
  expert: boolean;
  /** What the re-solve ran at — only `not_yet_found`'s wording depends on it. */
  level: PreferenceLevel | null;
}) {
  const caveat = minimalCaveat(edit);
  return (
    <li>
      {edit.area}, {clockLabel(edit.start, startHour)}
      {edit.mower ? ` (${edit.mower})` : ""}: {explanationSentence(edit, level)}
      {edit.conflicts.length > 0 && <> — {conflictText(edit.conflicts, startHour)}</>}
      {expert && edit.solve_time_s != null && (
        <span className="muted"> ({edit.solve_time_s.toFixed(2)}s)</span>
      )}
      {expert && caveat && <span className="muted"> — {caveat}</span>}
    </li>
  );
}
