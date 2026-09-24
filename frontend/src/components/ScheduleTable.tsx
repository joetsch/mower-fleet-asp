import { useMemo } from "react";

import { HourCell } from "./cells";
import { type PlanTask, isInPayload } from "../lib/schedulePreferences";
import { VIOLATION_LABEL, clockLabel } from "../lib/schedule";
import type { Scenario, Violation } from "../types";
import "./ScheduleTable.css";

interface Props {
  scenario: Scenario;
  /** The working copy of the plan, not the solved one — so edits show here too. */
  tasks: PlanTask[];
  violations: Violation[];
  /** Violations describe the plan as *solved*; once the user edits, they are stale. */
  violationsStale: boolean;
  /** Schedule edit mode (ADR-0035). Off, the table is the read-only view it always was. */
  editing: boolean;
  onMove: (uid: string, start: number) => void;
  onReassign: (uid: string, mower: string) => void;
  onPin: (uid: string, pin: PlanTask["pin"]) => void;
}

export function ScheduleTable({
  scenario,
  tasks,
  violations,
  violationsStale,
  editing,
  onMove,
  onReassign,
  onPin,
}: Props) {
  const sorted = useMemo(
    () => [...tasks].sort((a, b) => a.start - b.start || a.area.localeCompare(b.area)),
    [tasks],
  );

  // Violations are reported against (area, task-rank) — the rank the *solve* assigned, so
  // they can only be joined on `sourceTask`. An added task has none, and after any edit
  // the whole mapping is stale; the caller says so above the table rather than silently
  // showing marks that describe a plan the user has already changed.
  const violationsByTask = useMemo(() => {
    const map = new Map<string, Violation[]>();
    for (const v of violations) {
      const key = `${v.area}#${v.task}`;
      const existing = map.get(key);
      if (existing) existing.push(v);
      else map.set(key, [v]);
    }
    return map;
  }, [violations]);

  /** Only mowers the scenario says can service this area — the one feasibility check
   *  worth doing client-side. Everything else is the solver's answer (ADR-0035). */
  const mowersFor = (area: string) =>
    scenario.mowers.filter((m) => m.can_mow.includes(area)).map((m) => m.name);

  return (
    <table className="schedule-table">
      <thead>
        <tr>
          <th>Area</th>
          <th>#</th>
          <th>Mower</th>
          <th>Start</th>
          <th>End</th>
          <th>Elapsed</th>
          <th>Issues</th>
          {editing && <th className="col-actions" aria-label="row actions" />}
        </tr>
      </thead>
      <tbody>
        {sorted.map((t) => {
          const vs =
            violationsStale || t.sourceTask === null
              ? []
              : (violationsByTask.get(`${t.area}#${t.sourceTask}`) ?? []);
          const options = mowersFor(t.area);
          return (
            <tr key={t.uid} className={t.pin === "released" ? "task-released" : undefined}>
              <td>{t.area}</td>
              <td>{t.sourceTask ?? "new"}</td>
              <td>
                {editing && options.length > 1 ? (
                  <select
                    aria-label={`mower for ${t.area} at hour ${t.start}`}
                    value={t.mower}
                    onChange={(e) => onReassign(t.uid, e.target.value)}
                  >
                    {options.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                ) : (
                  t.mower
                )}
              </td>
              <td>
                {editing ? (
                  <HourCell
                    value={t.start}
                    onCommit={(h) => onMove(t.uid, h)}
                    label={`start hour for ${t.area}`}
                    commitOnChange
                    max={scenario.horizon_hours - 1}
                  />
                ) : (
                  <>
                    {clockLabel(t.start, scenario.horizon_start_hour)}
                    <span className="muted"> (h{t.start})</span>
                  </>
                )}
              </td>
              <td>
                {clockLabel(t.end, scenario.horizon_start_hour)}
                <span className="muted"> (h{t.end})</span>
              </td>
              <td>{t.end - t.start} h</td>
              <td>{vs.map((v) => VIOLATION_LABEL[v.kind]).join("; ") || "—"}</td>
              {editing && (
                <td className="col-actions">
                  <TaskKeepButton task={t} onPin={onPin} />
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** One button per row: **release** a task the next re-solve would keep in place, **keep**
 *  one that has been released. The plan is kept by default (ADR-0038), so most rows offer
 *  "release"; after "release all" they offer "keep". */
export function TaskKeepButton({
  task,
  onPin,
}: {
  task: PlanTask;
  onPin: (uid: string, pin: PlanTask["pin"]) => void;
}) {
  const kept = isInPayload(task);
  return (
    <button
      type="button"
      className="link-button"
      title={
        kept
          ? "Leave this out of the next re-solve — the solver may still move or re-add it."
          : "Ask the next re-solve to keep this where it is."
      }
      onClick={() => onPin(task.uid, kept ? "released" : "auto")}
    >
      {kept ? "release" : "keep"}
    </button>
  );
}
