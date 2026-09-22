import { type PointerEvent as ReactPointerEvent, useMemo, useRef, useState } from "react";

import { availabilityBands } from "../lib/availability";
import {
  GANTT_LEFT,
  GANTT_PLOT_W,
  GANTT_WIDTH,
  type GanttScale,
  clampStart,
  hourToX,
  hoursForPixels,
  snapHour,
} from "../lib/ganttScale";
import {
  VIOLATION_COLOR,
  VIOLATION_LABEL,
  clockLabel,
  firstMidnightOffset,
  mowerColors,
  weekdayAtMidnight,
} from "../lib/schedule";
import type { PlanTask } from "../lib/schedulePreferences";
import { visibleRunTrack } from "../lib/runTrack";
import type { Scenario, ScheduledTask, Violation } from "../types";
import { Switch } from "./Switch";
import "./GanttSchedule.css";

const ROW_H = 30;
const BAR_H = 16;
const NO_RUN_TRACK: ScheduledTask[] = [];
const TOP = 30;

/** A pointer has to travel this many CSS pixels before a press counts as a drag rather
 *  than a click. Below it the press still opens the popover (ADR-0045); above it the
 *  click is suppressed. Without this a 1–2px twitch while clicking would mark the task
 *  `edited`, which there is no per-edit undo for. */
const DRAG_THRESHOLD_PX = 3;

interface DragState {
  uid: string;
  /** `clientX` at pointerdown, to measure travel from. */
  fromX: number;
  /** Whole hours the pointer has moved the bar so far (snapped, clamped). 0 until the
   *  threshold is crossed. */
  hours: number;
  /** Has travel crossed `DRAG_THRESHOLD_PX`? Once true it stays true for the gesture. */
  active: boolean;
}

interface Props {
  scenario: Scenario;
  /** The working copy of the plan (ADR-0035), so schedule edits show on the chart too. */
  tasks: PlanTask[];
  /** Cumulative "what actually happened" from the moving-horizon run log (ADR-0042), in
   *  offsets relative to the current now (all negative). Drawn behind the now-line. */
  executed?: ScheduledTask[];
  violations: Violation[];
  /** Violations are joined on the rank the *solve* assigned; after an edit they describe
   *  a plan the user has already changed, so they are suppressed rather than misplaced. */
  violationsStale: boolean;
  /** Schedule edit mode (ADR-0035): bars become clickable and open the task editor. */
  editing?: boolean;
  /** `at` is in *page* coordinates, so the editor stays anchored to its bar on scroll.
   *  `flip` asks for it to open upwards, when the bar is near the bottom of the viewport. */
  onSelectTask?: (task: PlanTask, at: { x: number; y: number; flip: boolean }) => void;
  /** A bar was dragged to a new start hour (ADR-0045). Same effect as typing the hour
   *  into the popover — `useScheduleEdits.moveTask`, which keeps the duration. */
  onMoveTask?: (uid: string, start: number) => void;
}

interface Tip {
  x: number;
  y: number;
  task: PlanTask;
  violations: Violation[];
}

/** Edit state cannot be a fill: `--series-*` means "which mower", `--status-*` means
 *  "which violation" and `--history-fill` means "already done". So a released or edited
 *  bar keeps its mower colour and is marked by a dashed outline / reduced opacity
 *  instead (see `GanttSchedule.css`). */
function barClass(t: PlanTask): string {
  if (t.pin === "released") return "gantt-bar gantt-bar-released";
  if (t.edited || t.added) return "gantt-bar gantt-bar-edited";
  return "gantt-bar";
}

export function GanttSchedule({
  scenario,
  tasks,
  executed = [],
  violations,
  violationsStale,
  editing = false,
  onSelectTask,
  onMoveTask,
}: Props) {
  const colors = useMemo(() => mowerColors(scenario), [scenario]);
  const [tip, setTip] = useState<Tip | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  // pointerup is followed by a synthetic click on the same element; after a real drag we
  // do not want that click opening the popover. One-shot, read and cleared in onClick.
  const suppressNextClick = useRef(false);
  // The run-log track grows by a roll at every step; on by default once there is one,
  // with a switch to fold it away and keep the plot on the week ahead. Only the last
  // `RUN_TRACK_WINDOW_H` of it is drawn — otherwise `xMin` walks left forever and the plan
  // itself is squeezed into a shrinking share of the width. The run log keeps the rest.
  const [showRun, setShowRun] = useState(true);
  const visible = useMemo(() => visibleRunTrack(executed), [executed]);
  const runTrack = showRun ? visible : NO_RUN_TRACK;
  // Availability behind the bars, off by default: it is context for reading a schedule,
  // not part of it, and both bands at once over eight rows is a lot of ink. Deliberately
  // *not* persisted like `expertMode` — this is a per-look question, not a standing one.
  const [showAvoid, setShowAvoid] = useState(false);
  const [showNoGo, setShowNoGo] = useState(false);

  const areas = useMemo(
    () => [...scenario.areas].sort((a, b) => a.hole - b.hole || a.name.localeCompare(b.name)),
    [scenario],
  );

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

  const { xMin, xMax } = useMemo(() => {
    const ends = tasks.map((t) => t.end);
    const histStarts = scenario.history.map((h) => h.start);
    return {
      xMin: Math.min(0, ...histStarts, ...runTrack.map((t) => t.start)),
      xMax: Math.max(scenario.horizon_hours, ...ends),
    };
  }, [tasks, scenario, runTrack]);

  const scale: GanttScale = { xMin, xMax };
  const x = (hour: number) => hourToX(scale, hour);
  const height = TOP + areas.length * ROW_H + 8;

  // A dragged bar renders at its shifted start; the shift is applied to `t.start` at draw
  // time so nothing is committed until pointerup. Duration is preserved, so `end` moves
  // with it.
  const shownStart = (t: PlanTask) =>
    drag?.uid === t.uid && drag.active ? t.start + drag.hours : t.start;

  const beginDrag = (t: PlanTask, e: ReactPointerEvent) => {
    if (!editing || !onMoveTask) return;
    setTip(null);
    const fromX = e.clientX;
    // A ref would be cleaner, but the listeners below need the up-to-date `hours` and
    // React's batching makes reading it back from state unreliable mid-gesture — so the
    // move handler recomputes `hours` from `fromX` each time and stores it.
    let current: DragState = { uid: t.uid, fromX, hours: 0, active: false };
    setDrag(current);

    // Falls back to the viewBox width if the element cannot be measured (never in a real
    // browser; jsdom without the stub) — a 1:1 scale is a saner degrade than a dead drag.
    const measured = svgRef.current?.getBoundingClientRect().width ?? 0;
    const renderedW = measured > 0 ? measured : GANTT_WIDTH;

    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - fromX;
      const active = current.active || Math.abs(dx) >= DRAG_THRESHOLD_PX;
      if (!active) return;
      const raw = t.start + hoursForPixels(scale, dx, renderedW);
      const hours = clampStart(snapHour(raw), scenario.horizon_hours) - t.start;
      current = { ...current, hours, active: true };
      setDrag(current);
    };
    const finish = (committed: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
      if (committed && current.active && current.hours !== 0) {
        onMoveTask(t.uid, t.start + current.hours);
      }
      // A real drag is followed by a synthetic `click` on the bar — swallow it in the
      // bar's onClick so it does not open the popover on the moved bar. The trailing
      // click fires synchronously right after pointerup; a macrotask later clears the
      // flag whether or not that click actually arrived (some pointer stacks skip it),
      // so a later genuine click is never eaten.
      if (current.active) {
        suppressNextClick.current = true;
        setTimeout(() => {
          suppressNextClick.current = false;
        }, 0);
      }
      setDrag(null);
    };
    const onUp = () => finish(true);
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") finish(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
  };

  // Day columns: one segment per calendar day of the horizon, starting at t=0
  // (the first segment is a partial day — planning begins mid-afternoon).
  const daySegments: Array<{ from: number; to: number; label: string }> = [];
  {
    let from = 0;
    for (let m = firstMidnightOffset(scenario.horizon_start_hour); m < xMax; m += 24) {
      daySegments.push({
        from,
        to: m,
        label: weekdayAtMidnight(from, scenario.horizon_start_hour),
      });
      from = m;
    }
    daySegments.push({
      from,
      to: xMax,
      label: weekdayAtMidnight(from, scenario.horizon_start_hour),
    });
  }

  return (
    <figure className="gantt">
      <figcaption>
        Weekly maintenance schedule — {tasks.length} services, bars coloured by mower
      </figcaption>

      <div className="gantt-toggles">
        <Switch label="show avoid" on={showAvoid} onChange={setShowAvoid} />
        <Switch label="show no-go" on={showNoGo} onChange={setShowNoGo} />
        {executed.length > 0 && (
          <Switch label="show run history" on={showRun} onChange={setShowRun} />
        )}
      </div>

      <div className="gantt-scroll">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${GANTT_WIDTH} ${height}`}
          className={`gantt-svg${drag?.active ? " gantt-svg-dragging" : ""}`}
          role="img"
        >
          {/* day columns + labels */}
          {daySegments.map((seg, i) => (
            <g key={`day-${seg.from}`}>
              {i > 0 && (
                <line
                  x1={x(seg.from)}
                  x2={x(seg.from)}
                  y1={TOP}
                  y2={height - 8}
                  className="gantt-grid"
                />
              )}
              <text x={(x(seg.from) + x(seg.to)) / 2} y={20} className="gantt-daylabel">
                {seg.label}
              </text>
            </g>
          ))}

          {/* "now" marker at t = 0 */}
          <line x1={x(0)} x2={x(0)} y1={TOP - 6} y2={height - 8} className="gantt-now" />
          <text x={x(0)} y={TOP - 10} className="gantt-now-label">
            now
          </text>

          {/* rows */}
          {areas.map((area, i) => {
            const y = TOP + i * ROW_H;
            const rowTasks = tasks.filter((t) => t.area === area.name);
            const rowHistory = scenario.history.filter((h) => h.area === area.name);
            return (
              <g key={area.name}>
                {i % 2 === 1 && (
                  <rect
                    x={GANTT_LEFT}
                    y={y}
                    width={GANTT_PLOT_W}
                    height={ROW_H}
                    className="gantt-rowband"
                  />
                )}

                {/* Availability behind everything else — same classes as the Availability
                    tab's strip, so the two read as one system. */}
                {(showAvoid || showNoGo) &&
                  availabilityBands(area, scenario, xMin, xMax)
                    .filter((b) => (b.state === "avoid" ? showAvoid : showNoGo))
                    .map((b) => (
                      <rect
                        key={`${b.state}-${b.from}`}
                        x={x(b.from)}
                        y={y}
                        width={Math.max(1, x(b.to) - x(b.from))}
                        height={ROW_H}
                        className={`av-${b.state}`}
                      >
                        <title>
                          {`${area.name} · ${b.state === "no_go" ? "no-go" : "avoid"} · ${clockLabel(
                            b.from,
                            scenario.horizon_start_hour,
                          )} – ${clockLabel(b.to, scenario.horizon_start_hour)}`}
                        </title>
                      </rect>
                    ))}
                <text x={GANTT_LEFT - 10} y={y + ROW_H / 2} className="gantt-rowlabel">
                  {area.name}
                </text>

                {/* run-log track: services this area has already had, across every roll */}
                {runTrack
                  .filter((t) => t.area === area.name)
                  .map((t, ti) => (
                    <rect
                      key={`x-${ti}`}
                      x={x(t.start)}
                      y={y + (ROW_H - BAR_H) / 2 + 1}
                      width={Math.max(1, x(t.end) - x(t.start))}
                      height={BAR_H - 2}
                      rx={3}
                      className="gantt-history"
                    >
                      <title>
                        {`${area.name} · ${t.mower} · ${clockLabel(
                          t.start,
                          scenario.horizon_start_hour,
                        )} (executed)`}
                      </title>
                    </rect>
                  ))}

                {rowHistory.map((h, hi) => (
                  <rect
                    key={`h-${hi}`}
                    x={x(h.start)}
                    y={y + (ROW_H - BAR_H) / 2 + 1}
                    width={Math.max(1, x(h.completion) - x(h.start))}
                    height={BAR_H - 2}
                    rx={3}
                    className="gantt-history"
                  />
                ))}

                {rowTasks.map((t) => {
                  const vs =
                    violationsStale || t.sourceTask === null
                      ? []
                      : (violationsByTask.get(`${t.area}#${t.sourceTask}`) ?? []);
                  const ring = vs[0] ? VIOLATION_COLOR[vs[0].kind] : undefined;
                  const s0 = shownStart(t);
                  const s1 = s0 + (t.end - t.start);
                  const beingDragged = drag?.uid === t.uid && drag.active;
                  return (
                    <g key={t.uid}>
                      <rect
                        x={x(s0) + 1}
                        y={y + (ROW_H - BAR_H) / 2 + 1}
                        width={Math.max(2, x(s1) - x(s0) - 2)}
                        height={BAR_H - 2}
                        rx={4}
                        fill={colors.get(t.mower)}
                        stroke={ring}
                        strokeWidth={ring ? 2 : 0}
                        className={`${barClass(t)}${
                          editing && onMoveTask ? " gantt-bar-draggable" : ""
                        }${beingDragged ? " gantt-bar-dragging" : ""}`}
                        onMouseEnter={(e) =>
                          !drag && setTip({ x: e.clientX, y: e.clientY, task: t, violations: vs })
                        }
                        onMouseMove={(e) =>
                          !drag && setTip({ x: e.clientX, y: e.clientY, task: t, violations: vs })
                        }
                        onMouseLeave={() => !drag && setTip(null)}
                        onPointerDown={(e) => beginDrag(t, e)}
                        onClick={(e) => {
                          if (!editing) return;
                          // Swallow the click that follows a real drag's pointerup — it
                          // would otherwise open the popover on top of the moved bar.
                          if (suppressNextClick.current) {
                            suppressNextClick.current = false;
                            return;
                          }
                          // The hover tip is `pointer-events: none`, but it would still
                          // sit over the popover we are about to open.
                          setTip(null);
                          onSelectTask?.(t, {
                            x: e.pageX,
                            y: e.pageY,
                            // Roughly the popover's own height: below this the editor
                            // would open past the fold and its controls be unreachable.
                            flip: e.clientY > window.innerHeight - 180,
                          });
                        }}
                      />
                      {vs.length > 0 && !beingDragged && (
                        <text
                          x={(x(s0) + x(s1)) / 2}
                          y={y + (ROW_H - BAR_H) / 2 - 1}
                          className="gantt-viol-mark"
                        >
                          ▲
                        </text>
                      )}
                      {beingDragged && (
                        <text
                          x={x(s0) + 4}
                          y={y + (ROW_H - BAR_H) / 2 - 2}
                          className="gantt-drag-label"
                        >
                          {clockLabel(s0, scenario.horizon_start_hour)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>

      <Legend scenario={scenario} colors={colors} violations={violationsStale ? [] : violations} />

      {tip && (
        <div
          className="gantt-tip"
          style={{ left: tip.x + 14, top: tip.y + 14 }}
          role="tooltip"
        >
          <strong>{tip.task.area}</strong> · {tip.task.mower}
          <br />
          {clockLabel(tip.task.start, scenario.horizon_start_hour)} →{" "}
          {clockLabel(tip.task.end, scenario.horizon_start_hour)} ({tip.task.end - tip.task.start} h
          elapsed)
          {tip.violations.map((v, i) => (
            <div key={i} className="gantt-tip-viol">
              ▲ {VIOLATION_LABEL[v.kind]}
            </div>
          ))}
        </div>
      )}
    </figure>
  );
}

function Legend({
  scenario,
  colors,
  violations,
}: {
  scenario: Scenario;
  colors: Map<string, string>;
  violations: Violation[];
}) {
  const kinds = Array.from(new Set(violations.map((v) => v.kind)));
  return (
    <div className="gantt-legend">
      {[...scenario.mowers]
        .map((m) => m.name)
        .sort()
        .map((name) => (
          <span key={name} className="gantt-legend-item">
            <span className="gantt-swatch" style={{ background: colors.get(name) }} />
            {name}
          </span>
        ))}
      <span className="gantt-legend-item">
        <span className="gantt-swatch gantt-swatch-history" />
        past service
      </span>
      {kinds.map((k) => (
        <span key={k} className="gantt-legend-item">
          <span className="gantt-swatch gantt-swatch-ring" style={{ borderColor: VIOLATION_COLOR[k] }} />
          {VIOLATION_LABEL[k]}
        </span>
      ))}
    </div>
  );
}

/** One band toggle — the same hidden-checkbox + sliding-lever markup as the header's
 * expert-mode switch (`App.tsx`), so the two read as the same control. */
