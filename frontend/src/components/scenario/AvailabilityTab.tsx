import { availabilityBands, windowRows, type Weekday, type WindowRow } from "../../lib/availability";
import { clockLabel, DAYS_SHORT, WEEKDAYS } from "../../lib/schedule";
import {
  addAvailabilityWindow,
  deleteAvailabilityWindow,
  setIntervalEndpoint,
  setWindowDays,
} from "../../lib/scenarioEdits";
import { errorFor } from "../../lib/scenarioValidation";
import { stripGeometry, stripX } from "../../lib/strip";
import type { Area, Scenario } from "../../types";
import { HourCell } from "../cells";
import type { TabProps, Tip } from "./common";

const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

/** What a row actually blocks, spelled out. Availability is per hour of week (ADR-0014),
 * so `22–6` is two spans of the *same* weekday — never a spill into the next one. This
 * label is the only thing standing between that rule and a misreading. */
function rowEffect(row: WindowRow): string {
  return row.start < row.end
    ? `${hh(row.start)}–${hh(row.end)}`
    : `${hh(row.start)}–24:00 + 00:00–${hh(row.end)}, same day`;
}

/** The seven weekday toggles on one window row. Single-letter faces (two pairs collide,
 * so the accessible name is the full weekday); the last remaining day cannot be unset —
 * removing a window is what the remove button is for. */
function DayChips({
  row,
  uniform,
  disabled,
  onToggle,
}: {
  row: WindowRow;
  /** Every row on this area covers all seven days — nothing to look at yet, so mute it. */
  uniform: boolean;
  disabled?: boolean;
  onToggle: (days: Weekday[]) => void;
}) {
  const only = row.days.length === 1;
  return (
    <span className={`day-chips${uniform ? " day-chips-uniform" : ""}`}>
      {WEEKDAYS.map((day, i) => {
        const on = row.days.includes(day);
        const last = on && only;
        return (
          <button
            key={day}
            type="button"
            className={`day-chip${on ? " day-chip-on" : ""}`}
            aria-label={day}
            aria-pressed={on}
            disabled={disabled || last}
            title={last ? "a window must apply to at least one weekday" : day}
            onClick={() =>
              onToggle(on ? row.days.filter((d) => d !== day) : [...row.days, day])
            }
          >
            {DAYS_SHORT[i][0]}
          </button>
        );
      })}
    </span>
  );
}

interface Props extends TabProps {
  tip: Tip;
  setTip: (t: Tip) => void;
  /** Structural delete with Undo (ADR-0027) — see `ScenarioSummary`. */
  removeRow: (next: Scenario, label: string) => void;
}

export function AvailabilityTab({
  scenario,
  areas,
  editing,
  errors,
  solving,
  edit,
  areaKey,
  tip,
  setTip,
  removeRow,
}: Props) {
  // The whole planning week, 0..horizon_hours (lib/strip.ts).
  const avail = stripGeometry(0, scenario.horizon_hours, scenario.horizon_start_hour);

  return (
    <section>
      <div className="summary-legend">
        <span><span className="av-key av-free" /> free</span>
        <span><span className="av-key av-avoid" /> avoid</span>
        <span><span className="av-key av-no_go" /> no-go</span>
      </div>
      <div className="summary-strip-scroll">
        <div className="summary-strips" style={{ width: avail.width + 132 }}>
          <div className="summary-strip-daylabels" style={{ marginLeft: 132 }}>
            {avail.daySegments.map((seg) => (
              <span key={seg.from} style={{ width: (seg.to - seg.from) * avail.cellW }}>
                {seg.label}
              </span>
            ))}
          </div>
          {areas.map((a, ai) => (
            <div key={areaKey(a, ai)} className="summary-strip-row">
              <span className="summary-strip-label">{a.name}</span>
              <svg width={avail.width} height={14} className="summary-strip">
                {availabilityBands(a, scenario, 0, scenario.horizon_hours).map((w, i) => {
                  const label = `${a.name} · ${w.state === "no_go" ? "no-go" : "avoid"} · ${clockLabel(
                    w.from,
                    scenario.horizon_start_hour,
                  )} – ${clockLabel(w.to, scenario.horizon_start_hour)}`;
                  return (
                    <rect
                      key={i}
                      x={stripX(avail, w.from)}
                      y={0}
                      width={(w.to - w.from) * avail.cellW}
                      height={14}
                      className={`av-${w.state}`}
                      onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, text: label })}
                      onMouseLeave={() => setTip(null)}
                    >
                      <title>{label}</title>
                    </rect>
                  );
                })}
                {avail.midnights.map((m) => (
                  <line
                    key={m}
                    x1={stripX(avail, m)}
                    x2={stripX(avail, m)}
                    y1={0}
                    y2={14}
                    className="av-grid"
                  />
                ))}
              </svg>
            </div>
          ))}
        </div>
      </div>
      {tip && (
        <div className="summary-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          {tip.text}
        </div>
      )}

      {editing && (
        <div className="avail-edit">
          <p className="summary-note">
            Add, remove or re-time no-go / avoid windows, and pick the weekdays each one
            applies to. A window that starts after it ends covers two spans of the{" "}
            <em>same</em> weekday, not the next one.
          </p>
          {areas.map((a, ai) => {
            const rows = windowRows(a);
            // Only worth drawing attention to the day picker once something is not "every
            // day" — until then the chips are decoration on a schedule that has no
            // per-weekday structure to show.
            const uniform = rows.every((r) => r.days.length === WEEKDAYS.length);
            const byKind = { no_go: 0, avoid: 0 };
            return (
              <div key={areaKey(a, ai)} className="avail-edit-area">
                <span className="summary-strip-label">{a.name}</span>
                <div className="avail-edit-rows">
                  {rows.map((row) => {
                    const i = byKind[row.kind]++;
                    const kindLabel = row.kind === "no_go" ? "no-go" : "avoid";
                    return (
                      <div key={`${row.kind}-${i}`} className="avail-edit-row">
                        <span className={`av-key av-${row.kind}`} />
                        <span className="avail-edit-kind">{kindLabel}</span>
                        <HourCell
                          value={row.start}
                          label={`${a.name} ${kindLabel} window ${i + 1} start hour`}
                          onCommit={(v) =>
                            edit(setIntervalEndpoint(scenario, a.name, row.kind, i, "start", v))
                          }
                          error={rowError(a, row, errors, "interval")}
                          disabled={solving}
                        />
                        <span>–</span>
                        <HourCell
                          value={row.end}
                          label={`${a.name} ${kindLabel} window ${i + 1} end hour`}
                          onCommit={(v) =>
                            edit(setIntervalEndpoint(scenario, a.name, row.kind, i, "end", v))
                          }
                          error={
                            row.kind === "no_go" ? rowError(a, row, errors, "day") : undefined
                          }
                          disabled={solving}
                        />
                        <DayChips
                          row={row}
                          uniform={uniform}
                          disabled={solving}
                          onToggle={(days) =>
                            edit(setWindowDays(scenario, a.name, row.kind, i, days))
                          }
                        />
                        <span className="avail-edit-effect muted">{rowEffect(row)}</span>
                        <button
                          type="button"
                          className="link-button danger-link"
                          disabled={solving}
                          onClick={() =>
                            removeRow(
                              deleteAvailabilityWindow(scenario, a.name, row.kind, i),
                              `${kindLabel} window on ${a.name}`,
                            )
                          }
                        >
                          remove
                        </button>
                      </div>
                    );
                  })}
                  <div className="avail-edit-add">
                    {rows.length === 0 && <span className="muted">always free</span>}
                    {rows.length > 0 && uniform && (
                      <span className="muted">same every weekday</span>
                    )}
                    <button
                      type="button"
                      className="link-button"
                      disabled={solving}
                      onClick={() => edit(addAvailabilityWindow(scenario, a.name, "no_go"))}
                    >
                      + no-go
                    </button>
                    <button
                      type="button"
                      className="link-button"
                      disabled={solving}
                      onClick={() => edit(addAvailabilityWindow(scenario, a.name, "avoid"))}
                    >
                      + avoid
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** A row's validation error, looked up on the weekdays it actually sits on (paths carry
 * the weekday since ADR-0029). `interval` = the per-interval rules, reported on the start
 * cell; `day` = the whole-day no-go rule, reported on the end cell as it always was. */
function rowError(
  area: Area,
  row: WindowRow,
  errors: Parameters<typeof errorFor>[0],
  which: "interval" | "day",
): string | undefined {
  for (const day of row.days) {
    const path =
      which === "interval"
        ? `areas.${area.name}.schedule.${day}.${row.kind}.${row.at[day]}`
        : `areas.${area.name}.schedule.${day}.no_go`;
    const err = errorFor(errors, path);
    if (err) return err;
  }
  return undefined;
}
