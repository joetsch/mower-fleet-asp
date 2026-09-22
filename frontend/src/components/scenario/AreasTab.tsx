import {
  addArea,
  deleteArea,
  renameArea,
  setAreaType,
  updateArea,
} from "../../lib/scenarioEdits";
import { PRIORITY_LABEL } from "../../lib/schedule";
import { errorFor } from "../../lib/scenarioValidation";
import type { Area, Catalog, Scenario, ScenarioDerived } from "../../types";
import { NameCell, NumCell } from "../cells";
import type { TabProps } from "./common";

interface Props extends TabProps {
  bounds: ScenarioDerived["bounds"];
  catalog: Catalog | null;
  /** A structural delete — hand `App` the pre-delete scenario for Undo, then apply. */
  removeRow: (next: Scenario, label: string) => void;
}

export function AreasTab({
  scenario,
  areas,
  bounds,
  editing,
  catalog,
  errors,
  solving,
  edit,
  removeRow,
  areaKey,
}: Props) {
  return (
    <section className="summary-table-scroll">
      <table className="summary-table">
        <thead>
          <tr>
            <th>Hole</th>
            <th>Area</th>
            <th>Type</th>
            <th>Priority</th>
            <th>Min interval</th>
            <th>Max interval</th>
            <th>Services / wk</th>
            <th>Size</th>
            {editing && <th className="col-actions" aria-label="row actions" />}
          </tr>
        </thead>
        <tbody>
          {areas.map((a, ai) => {
            const b = bounds[a.name];
            const set = (patch: Partial<Area>) => edit(updateArea(scenario, a.name, patch));
            const err = (field: string) => errorFor(errors, `areas.${a.name}.${field}`);
            if (!editing) {
              return (
                <tr key={a.name}>
                  <td>{a.hole}</td>
                  <td>{a.name}</td>
                  <td>{a.type}</td>
                  <td>{PRIORITY_LABEL[a.priority] ?? a.priority}</td>
                  <td>{a.min_interval} h</td>
                  <td>{a.max_interval} h</td>
                  <td>{b ? (b[0] === b[1] ? b[0] : `${b[0]}–${b[1]}`) : "—"}</td>
                  <td>{a.size_m2 != null ? `${a.size_m2.toLocaleString()} m²` : "—"}</td>
                </tr>
              );
            }
            return (
              <tr key={areaKey(a, ai)}>
                <td>
                  <NumCell
                    value={a.hole}
                    onChange={(v) => set({ hole: v ?? 1 })}
                    error={err("hole")}
                    disabled={solving}
                    width="narrow"
                  />
                </td>
                <td>
                  <NameCell
                    value={a.name}
                    onCommit={(name) => edit(renameArea(scenario, a.name, name))}
                    error={err("name")}
                    disabled={solving}
                  />
                </td>
                <td>
                  <select
                    className="cell-input"
                    value={a.type}
                    disabled={solving}
                    onChange={(e) =>
                      edit(
                        setAreaType(
                          scenario,
                          a.name,
                          e.target.value,
                          catalog?.area_types.find((x) => x.name === e.target.value),
                        ),
                      )
                    }
                  >
                    {!catalog?.area_types.some((t) => t.name === a.type) && (
                      <option value={a.type}>{a.type}</option>
                    )}
                    {catalog?.area_types.map((t) => (
                      <option key={t.name} value={t.name}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    className="cell-input"
                    aria-label={`priority for ${a.name}`}
                    value={a.priority}
                    disabled={solving}
                    onChange={(e) => set({ priority: Number(e.target.value) })}
                  >
                    <option value={1}>{PRIORITY_LABEL[1]}</option>
                    <option value={2}>{PRIORITY_LABEL[2]}</option>
                    <option value={3}>{PRIORITY_LABEL[3]}</option>
                  </select>
                </td>
                <td>
                  <NumCell
                    value={a.min_interval}
                    onChange={(v) => set({ min_interval: v ?? 1 })}
                    error={err("min_interval")}
                    disabled={solving}
                    suffix="h"
                  />
                </td>
                <td>
                  <NumCell
                    value={a.max_interval}
                    onChange={(v) => set({ max_interval: v ?? 1 })}
                    error={err("max_interval")}
                    disabled={solving}
                    suffix="h"
                  />
                </td>
                <td className="cell-pair">
                  <NumCell
                    value={a.min_services ?? (b ? b[0] : null)}
                    onChange={(v) => set({ min_services: v })}
                    error={err("min_services")}
                    disabled={solving}
                    placeholder={b ? String(b[0]) : ""}
                  />
                  <span>–</span>
                  <NumCell
                    value={a.max_services ?? (b ? b[1] : null)}
                    onChange={(v) => set({ max_services: v })}
                    error={err("max_services")}
                    disabled={solving}
                    placeholder={b ? String(b[1]) : ""}
                  />
                </td>
                <td>
                  <NumCell
                    value={a.size_m2}
                    onChange={(v) => set({ size_m2: v })}
                    error={err("size_m2")}
                    disabled={solving}
                    suffix="m²"
                    width="wide"
                  />
                </td>
                <td className="col-actions">
                  <button
                    type="button"
                    className="link-button danger-link"
                    disabled={solving}
                    onClick={() => removeRow(deleteArea(scenario, a.name), `area "${a.name}"`)}
                  >
                    remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
        {editing && (
          <tfoot>
            <tr className="table-add-row">
              <td colSpan={9}>
                <button
                  type="button"
                  className="link-button"
                  disabled={solving || scenario.mowers.length === 0 || !catalog}
                  onClick={() => edit(addArea(scenario, catalog))}
                >
                  + Add area
                </button>
                {scenario.mowers.length === 0 ? (
                  <span className="muted"> — add a mower first</span>
                ) : !catalog ? (
                  <span className="muted"> — catalogue unavailable</span>
                ) : null}
              </td>
            </tr>
            <tr className="table-note-row">
              <td colSpan={9}>
                <span className="muted">
                  Removing an area also removes its history and capability data.
                </span>
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </section>
  );
}
