import { useMemo } from "react";

import { clockLabel } from "../../lib/schedule";
import { updateHistoryEvent } from "../../lib/scenarioEdits";
import { errorFor } from "../../lib/scenarioValidation";
import { stripGeometry, stripX } from "../../lib/strip";
import type { ServiceEvent } from "../../types";
import { NumCell } from "../cells";
import type { TabProps, Tip } from "./common";

/** Hover label for one area's history bar — every area has exactly one history event
 * (`Scenario` validates this at construction, model.py). */
function historyLabel(area: string, ev: ServiceEvent, horizonStartHour: number): string {
  const span = `${clockLabel(ev.start, horizonStartHour)} – ${clockLabel(ev.completion, horizonStartHour)}`;
  // A history event can complete after t=0 — the mower was still finishing it when
  // planning starts (docs/known-hazards.md, "Neither encoding blocks a mower during an
  // in-progress history service"). Worth surfacing, not just a display curiosity.
  const inProgress = ev.completion > 0 ? " · still running past planning start" : "";
  return `${area} · ${ev.mower} · ${span} (${ev.completion - ev.start} h)${inProgress}`;
}

interface Props extends TabProps {
  tip: Tip;
  setTip: (t: Tip) => void;
}

export function HistoryTab({
  scenario,
  areas,
  editing,
  errors,
  solving,
  edit,
  areaKey,
  tip,
  setTip,
}: Props) {
  // Same hover-strip pattern as Availability, but the domain runs from the earliest
  // history event up to "now" — t=0 — rather than 0..horizon_hours. One history event per
  // area is guaranteed by Scenario's own validation (model.py).
  const historyByArea = useMemo(
    () => new Map(scenario.history.map((h) => [h.area, h])),
    [scenario],
  );
  const histMin = Math.min(0, ...scenario.history.map((h) => h.start));
  const histMax = Math.max(0, ...scenario.history.map((h) => h.completion));
  const hist = stripGeometry(histMin, histMax, scenario.horizon_start_hour);

  return (
    <section>
      <p className="summary-note">
        Each area's most recent service before planning start — one per area, added and
        removed with the area.
      </p>

      {editing && (
        <div className="summary-table-scroll">
          <table className="summary-table">
            <thead>
              <tr>
                <th>Area</th>
                <th>Serviced by</th>
                <th>Started</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {areas.map((a, ai) => {
                const ev = historyByArea.get(a.name);
                if (!ev) {
                  return (
                    <tr key={areaKey(a, ai)}>
                      <td>{a.name}</td>
                      <td colSpan={3}>
                        <span className="field-error">
                          {errorFor(errors, `history.${a.name}`) ??
                            "this area has no service history event"}
                        </span>
                      </td>
                    </tr>
                  );
                }
                const capable = scenario.mowers.filter((m) => m.can_mow.includes(a.name));
                const setEv = (patch: Partial<ServiceEvent>) =>
                  edit(updateHistoryEvent(scenario, a.name, patch));
                const err = (f: string) => errorFor(errors, `history.${a.name}.${f}`);
                return (
                  <tr key={areaKey(a, ai)}>
                    <td>{a.name}</td>
                    <td>
                      <select
                        className="cell-input"
                        value={ev.mower}
                        disabled={solving}
                        onChange={(e) => setEv({ mower: e.target.value })}
                      >
                        {!capable.some((m) => m.name === ev.mower) && (
                          <option value={ev.mower}>{ev.mower}</option>
                        )}
                        {capable.map((m) => (
                          <option key={m.name} value={m.name}>
                            {m.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <NumCell
                        value={ev.start}
                        onChange={(v) => setEv({ start: v ?? 0 })}
                        error={err("start")}
                        disabled={solving}
                        suffix="h"
                      />
                      <span className="cell-suffix"> {clockLabel(ev.start, scenario.horizon_start_hour)}</span>
                    </td>
                    <td>
                      <NumCell
                        value={ev.completion}
                        onChange={(v) => setEv({ completion: v ?? 0 })}
                        error={err("completion")}
                        disabled={solving}
                        suffix="h"
                      />
                      <span className="cell-suffix"> {clockLabel(ev.completion, scenario.horizon_start_hour)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="summary-legend">
        <span><span className="av-key hist-key" /> past service</span>
        <span><span className="av-key hist-now-key" /> now (planning start)</span>
      </div>
      <div className="summary-strip-scroll">
        <div className="summary-strips" style={{ width: hist.width + 132 }}>
          <div className="summary-strip-daylabels" style={{ marginLeft: 132 }}>
            {hist.daySegments.map((seg) => (
              <span key={seg.from} style={{ width: (seg.to - seg.from) * hist.cellW }}>
                {seg.label}
              </span>
            ))}
          </div>
          {areas.map((a, ai) => {
            const ev = historyByArea.get(a.name);
            return (
              <div key={areaKey(a, ai)} className="summary-strip-row">
                <span className="summary-strip-label">{a.name}</span>
                <svg width={hist.width} height={14} className="summary-strip">
                  {ev && (
                    <rect
                      x={stripX(hist, ev.start)}
                      y={0}
                      width={Math.max(2, (ev.completion - ev.start) * hist.cellW)}
                      height={14}
                      rx={2}
                      className="hist-bar"
                      onMouseMove={(e) =>
                        setTip({
                          x: e.clientX,
                          y: e.clientY,
                          text: historyLabel(a.name, ev, scenario.horizon_start_hour),
                        })
                      }
                      onMouseLeave={() => setTip(null)}
                    >
                      <title>{historyLabel(a.name, ev, scenario.horizon_start_hour)}</title>
                    </rect>
                  )}
                  {hist.midnights.map((m) => (
                    <line
                      key={m}
                      x1={stripX(hist, m)}
                      x2={stripX(hist, m)}
                      y1={0}
                      y2={14}
                      className="av-grid"
                    />
                  ))}
                  <line x1={hist.nowX} x2={hist.nowX} y1={0} y2={14} className="hist-now" />
                </svg>
              </div>
            );
          })}
        </div>
      </div>
      {tip && (
        <div className="summary-tip" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          {tip.text}
        </div>
      )}
    </section>
  );
}
