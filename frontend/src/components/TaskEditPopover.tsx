import { useEffect, useRef } from "react";

import { HourCell } from "./cells";
import { type PlanTask, isInPayload } from "../lib/schedulePreferences";
import { clockLabel } from "../lib/schedule";
import type { Scenario } from "../types";
import "./TaskEditPopover.css";

/** The per-task editor the Gantt opens on a bar click (ADR-0035).
 *
 *  A popover rather than drag-and-drop: dragging would fight the tooltip's `onMouseMove`
 *  and gives no keyboard path, while this reuses the same `HourCell` the scenario editor
 *  uses and is reachable with Tab. Outside-click + Escape to close, mirroring
 *  `LoadScenarioMenu` so the app has one popover behaviour rather than two.
 *
 *  Positioned in *page* coordinates, unlike the chart's hover tip: the tip dies on
 *  `mouseleave` so it never outlives a scroll, but this one has controls the user may
 *  scroll to reach, and fixed positioning would leave it floating over whatever happened
 *  to scroll under it.
 */
export function TaskEditPopover({
  task,
  scenario,
  at,
  onMove,
  onReassign,
  onPin,
  onClose,
}: {
  task: PlanTask;
  scenario: Scenario;
  at: { x: number; y: number; flip: boolean };
  onMove: (start: number) => void;
  onReassign: (mower: string) => void;
  onPin: (pin: PlanTask["pin"]) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("keydown", onKey);
    // `mousedown`, not `click`: a click that started inside the popover and ended outside
    // it would otherwise close it mid-interaction (dragging across a select, say).
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  // Only the mowers the scenario says can service this area. The one check worth doing
  // here — the rest of feasibility is the solver's answer.
  const mowers = scenario.mowers.filter((m) => m.can_mow.includes(task.area));
  const kept = isInPayload(task);

  return (
    <div
      ref={ref}
      className="task-popover"
      role="dialog"
      aria-label={`edit task: ${task.area}`}
      style={
        // Page coordinates + `position: absolute`, so the editor scrolls with the bar it
        // belongs to rather than hanging in the viewport where it was opened.
        at.flip ? { left: at.x, bottom: `calc(100% - ${at.y}px)` } : { left: at.x, top: at.y }
      }
    >
      <p className="task-popover-head">
        <strong>{task.area}</strong>
        <span className="muted">
          {" "}
          · {clockLabel(task.start, scenario.horizon_start_hour)}
        </span>
      </p>

      <label className="task-popover-field">
        <span>start</span>
        <HourCell
          value={task.start}
          onCommit={onMove}
          label={`start hour for ${task.area}`}
          commitOnChange
          max={scenario.horizon_hours - 1}
        />
      </label>

      <label className="task-popover-field">
        <span>mower</span>
        <select
          aria-label={`mower for ${task.area}`}
          value={task.mower ?? ""}
          disabled={mowers.length < 2}
          onChange={(e) => onReassign(e.target.value)}
        >
          {/* Only an added, not-yet-solved task can still be "(any)" — see
              PlanTask.mower. Picking a real option below is an ordinary reassign; there
              is no way back to "(any)" once one is picked. */}
          {task.mower === null && <option value="">(any)</option>}
          {mowers.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        className="link-button"
        onClick={() => onPin(kept ? "released" : "auto")}
      >
        {kept ? "release this service" : "keep this service"}
      </button>
    </div>
  );
}
