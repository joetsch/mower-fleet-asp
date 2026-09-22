import { useMemo } from "react";

import { defaultDurationHours } from "../../lib/scenarioDefaults";
import {
  addMower,
  deleteMower,
  historyReassignedByDeletingMower,
  renameMower,
  setBaseDuration,
  setMowerModel,
  strandedAreasWithoutMower,
  toggleCapability,
  updateMower,
} from "../../lib/scenarioEdits";
import { errorFor } from "../../lib/scenarioValidation";
import type { Catalog, Scenario } from "../../types";
import { NameCell, NumCell } from "../cells";
import type { TabProps } from "./common";

interface Props extends TabProps {
  expert: boolean;
  catalog: Catalog | null;
  mowerKey: (m: { name: string }, i: number) => string;
  /** A structural delete — hand `App` the pre-delete scenario for Undo, then apply. */
  removeRow: (next: Scenario, label: string) => void;
}

export function FleetTab({
  scenario,
  areas,
  editing,
  expert,
  errors,
  solving,
  catalog,
  edit,
  removeRow,
  areaKey,
  mowerKey,
}: Props) {
  const durationRange = useMemo(() => {
    const out = new Map<string, [number, number]>();
    for (const d of scenario.base_durations ?? []) {
      const prev = out.get(d.mower);
      out.set(
        d.mower,
        prev ? [Math.min(prev[0], d.hours), Math.max(prev[1], d.hours)] : [d.hours, d.hours],
      );
    }
    return out;
  }, [scenario]);

  if (!editing) {
    return (
      <section>
        <ul className="summary-fleet">
          {scenario.mowers.map((m, mi) => {
            const range = durationRange.get(m.name);
            return (
              <li key={mowerKey(m, mi)}>
                <strong>{m.name}</strong>
                {m.model && <span className="summary-fleet-model"> · {m.model}</span>}
                {expert && m.area_capacity_m2_per_day != null && (
                  <span className="summary-fleet-model">
                    {" "}
                    · {m.area_capacity_m2_per_day.toLocaleString()} m²/day
                  </span>
                )}
                <br />
                {m.can_mow.length} area{m.can_mow.length === 1 ? "" : "s"}: {m.can_mow.join(", ")}
                {expert && range && (
                  <span className="summary-fleet-model">
                    {" "}
                    · {range[0] === range[1] ? `${range[0]} h` : `${range[0]}–${range[1]} h`}{" "}
                    per service
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  return (
    <section className="fleet-edit">
      <div className="summary-table-scroll">
        <table className="summary-table cap-grid">
          <thead>
            <tr>
              <th>Can service</th>
              {scenario.mowers.map((m, mi) => (
                <th key={mowerKey(m, mi)}>{m.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {areas.map((a, ai) => {
              const orphan = errorFor(errors, `areas.${a.name}.capability`);
              return (
                <tr key={areaKey(a, ai)}>
                  <td className={orphan ? "cap-orphan" : undefined}>
                    {a.name}
                    {orphan && <span className="field-error cap-orphan-msg">{orphan}</span>}
                  </td>
                  {scenario.mowers.map((m, mi) => (
                    <td key={mowerKey(m, mi)} className="cap-cell">
                      <input
                        type="checkbox"
                        checked={m.can_mow.includes(a.name)}
                        disabled={solving}
                        onChange={(e) =>
                          edit(toggleCapability(scenario, a.name, m.name, e.target.checked))
                        }
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {scenario.mowers.map((m, mi) => {
        const merr = (f: string) => errorFor(errors, `mowers.${m.name}.${f}`);
        return (
          <div key={mowerKey(m, mi)} className="fleet-edit-mower">
            <div className="fleet-edit-head">
              <NameCell
                value={m.name}
                onCommit={(name) => edit(renameMower(scenario, m.name, name))}
                error={merr("name")}
                disabled={solving}
              />
              <label>
                model{" "}
                <select
                  className="cell-input"
                  value={m.model ?? ""}
                  disabled={solving}
                  onChange={(e) =>
                    edit(
                      setMowerModel(
                        scenario,
                        m.name,
                        e.target.value,
                        catalog?.mower_models.find((x) => x.name === e.target.value),
                      ),
                    )
                  }
                >
                  {(!m.model || !catalog?.mower_models.some((x) => x.name === m.model)) && (
                    <option value={m.model ?? ""}>{m.model ?? "—"}</option>
                  )}
                  {catalog?.mower_models.map((x) => (
                    <option key={x.name} value={x.name}>
                      {x.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                rate{" "}
                <NumCell
                  value={m.area_capacity_m2_per_day}
                  onChange={(v) => edit(updateMower(scenario, m.name, { area_capacity_m2_per_day: v }))}
                  error={merr("area_capacity_m2_per_day")}
                  disabled={solving}
                  suffix="m²/day"
                  width="wide"
                />
              </label>
              {(() => {
                const stranded = strandedAreasWithoutMower(scenario, m.name);
                return (
                  <span className="fleet-edit-remove">
                    {stranded.length > 0 && (
                      <span className="muted">only mower for {stranded.join(", ")}</span>
                    )}
                    <button
                      type="button"
                      className="link-button danger-link"
                      disabled={solving || stranded.length > 0}
                      onClick={() => {
                        const n = historyReassignedByDeletingMower(scenario, m.name);
                        const moved =
                          n > 0
                            ? ` — its ${n} history event${n === 1 ? "" : "s"} moved to another mower`
                            : "";
                        removeRow(deleteMower(scenario, m.name), `mower "${m.name}"${moved}`);
                      }}
                    >
                      remove
                    </button>
                  </span>
                );
              })()}
            </div>
            <table className="summary-table fleet-dur">
              <tbody>
                {m.can_mow.map((areaName) => {
                  const bd = scenario.base_durations?.find(
                    (d) => d.area === areaName && d.mower === m.name,
                  );
                  const area = scenario.areas.find((a) => a.name === areaName);
                  const canDefault = area?.size_m2 != null && m.area_capacity_m2_per_day != null;
                  return (
                    <tr key={areaName}>
                      <td>{areaName}</td>
                      <td>
                        <NumCell
                          value={bd?.hours ?? null}
                          onChange={(v) => edit(setBaseDuration(scenario, areaName, m.name, v ?? 1))}
                          error={errorFor(errors, `durations.${areaName}.${m.name}`)}
                          disabled={solving || !bd}
                          suffix="h / service"
                        />
                      </td>
                      <td>
                        {canDefault && (
                          <button
                            type="button"
                            className="link-button"
                            disabled={solving}
                            onClick={() =>
                              edit(
                                setBaseDuration(
                                  scenario,
                                  areaName,
                                  m.name,
                                  defaultDurationHours(
                                    area.size_m2 as number,
                                    m.area_capacity_m2_per_day as number,
                                  ),
                                ),
                              )
                            }
                          >
                            use default
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
      <div className="fleet-edit-add">
        <button
          type="button"
          className="link-button"
          disabled={solving}
          onClick={() => edit(addMower(scenario, catalog))}
        >
          + Add mower
        </button>
      </div>
    </section>
  );
}
