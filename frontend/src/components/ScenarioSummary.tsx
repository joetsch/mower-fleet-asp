import { useMemo, useState } from "react";

import { PRIORITY_LABEL, WEEKDAYS } from "../lib/schedule";
import { updateScenario } from "../lib/scenarioEdits";
import type { FieldError } from "../lib/scenarioValidation";
import type { Area, Catalog, Scenario, ScenarioDerived } from "../types";
import { Fact } from "./cells";
import { AreasTab } from "./scenario/AreasTab";
import { AvailabilityTab } from "./scenario/AvailabilityTab";
import type { Tip } from "./scenario/common";
import { FleetTab } from "./scenario/FleetTab";
import { HistoryTab } from "./scenario/HistoryTab";
import "./ScenarioSummary.css";

export type Tab = "areas" | "fleet" | "availability" | "history";

function loadWord(lf: number): string {
  if (lf < 0.85) return "comfortable";
  if (lf <= 1.1) return "tight";
  return "over capacity";
}

const LOAD_COLOR: Record<string, string> = {
  comfortable: "var(--series-3)",
  tight: "var(--status-warning)",
  "over capacity": "var(--status-critical)",
};

interface Props {
  scenario: Scenario;
  derived: ScenarioDerived;
  expert: boolean;
  /** Scenario editing (ADR-0024). When `editing`, tab cells become inputs and every
   * change is pushed up via `onChange`. */
  editing?: boolean;
  catalog?: Catalog | null;
  errors?: FieldError[];
  solving?: boolean;
  onChange?: (next: Scenario) => void;
  /** Called just before a structural delete (ADR-0027) with the scenario as it was and a
   * label like `area "Green 2"`, so `App` can offer an Undo. */
  onRowRemoved?: (before: Scenario, label: string) => void;
  /** Which tab is open, or none (UI review, 2026-09-24 — the four tabs cluttered the
   *  screen when nobody had asked to see one). Controlled by `App` rather than local state
   *  so "Edit scenario" / "New scenario…" / "Drop a service" can open Areas on the caller's
   *  behalf. */
  tab: Tab | null;
  onTabChange: (t: Tab | null) => void;
}

export function ScenarioSummary({
  scenario,
  derived,
  expert,
  editing = false,
  catalog = null,
  errors = [],
  solving = false,
  onChange,
  onRowRemoved,
  tab,
  onTabChange,
}: Props) {
  const bounds = derived.bounds;
  const edit = (next: Scenario) => onChange?.(next);
  /** A structural delete: hand `App` the pre-delete scenario for Undo, then apply. */
  const removeRow = (next: Scenario, label: string) => {
    onRowRemoved?.(scenario, label);
    onChange?.(next);
  };
  const [tip, setTip] = useState<Tip>(null);

  // Sorted for the read view; original order while editing so a rename or a hole change
  // doesn't reshuffle rows mid-edit (and row keys stay stable — see the name inputs).
  const areas = useMemo(
    () =>
      editing
        ? scenario.areas
        : [...scenario.areas].sort((a, b) => a.hole - b.hole || a.name.localeCompare(b.name)),
    [scenario, editing],
  );
  const areaKey = (a: Area, i: number) => (editing ? `area-${i}` : a.name);
  const mowerKey = (m: { name: string }, i: number) => (editing ? `mower-${i}` : m.name);
  const holes = useMemo(() => new Set(scenario.areas.map((a) => a.hole)).size, [scenario]);
  const priorityMix = useMemo(
    () => [1, 2, 3].map((p) => scenario.areas.filter((a) => a.priority === p).length),
    [scenario],
  );
  const serviceTotal = useMemo(() => {
    const vals = Object.values(bounds);
    return vals.length
      ? [vals.reduce((s, b) => s + b[0], 0), vals.reduce((s, b) => s + b[1], 0)]
      : null;
  }, [bounds]);

  const totalAreaM2 = scenario.areas.every((a) => a.size_m2 != null)
    ? scenario.areas.reduce((s, a) => s + (a.size_m2 ?? 0), 0)
    : null;
  const loadFactor = derived.load_factor;
  const nowDay = WEEKDAYS[((Math.floor(scenario.horizon_start_hour / 24) % 7) + 7) % 7];
  const nowClock = `${String(scenario.horizon_start_hour % 24).padStart(2, "0")}:00`;

  const tabProps = { scenario, areas, editing, errors, solving, edit, areaKey };

  return (
    <div className="summary">
      <section className="summary-overview">
        {editing && (
          <label className="summary-name-edit">
            Scenario name{" "}
            <input
              type="text"
              className="cell-input cell-input-name"
              value={scenario.name}
              disabled={solving}
              spellCheck={false}
              onChange={(e) => edit(updateScenario(scenario, { name: e.target.value }))}
            />
          </label>
        )}
        <div className="summary-facts">
          <Fact value={String(holes)} label={holes === 1 ? "hole" : "holes"} />
          <Fact value={String(scenario.areas.length)} label="areas" />
          <Fact value={String(scenario.mowers.length)} label="mowers" />
          <Fact
            value={`${Math.round(scenario.horizon_hours / 24)} d`}
            label="planning horizon"
          />
          {expert && serviceTotal && (
            <Fact
              value={
                serviceTotal[0] === serviceTotal[1]
                  ? String(serviceTotal[0])
                  : `${serviceTotal[0]}–${serviceTotal[1]}`
              }
              label="services / week"
            />
          )}
          {totalAreaM2 != null && (
            <Fact value={`${(totalAreaM2 / 1000).toFixed(1)}k`} label="m² to service" />
          )}
        </div>

        <p className="summary-now">
          Now (planning start): <strong>{nowDay} {nowClock}</strong>
        </p>

        {expert && (
          <p className="summary-priority">
            Priority mix: <strong>{priorityMix[0]}</strong> {PRIORITY_LABEL[1]} (P1)
            {priorityMix[1] > 0 && (
              <>
                {" · "}
                <strong>{priorityMix[1]}</strong> {PRIORITY_LABEL[2]} (P2)
              </>
            )}
            {priorityMix[2] > 0 && (
              <>
                {" · "}
                <strong>{priorityMix[2]}</strong> {PRIORITY_LABEL[3]} (P3)
              </>
            )}
          </p>
        )}

        {loadFactor != null && Number.isFinite(loadFactor) && (
          <div className="summary-load">
            <div className="summary-load-head">
              <span>Fleet load</span>
              <span className="summary-load-word">
                {loadWord(loadFactor)}
                {expert && ` — load factor ${loadFactor.toFixed(2)}`}
              </span>
            </div>
            <div className="summary-load-track">
              <div
                className="summary-load-fill"
                style={{
                  width: `${Math.min(loadFactor / 2, 1) * 100}%`,
                  background: LOAD_COLOR[loadWord(loadFactor)],
                }}
              />
            </div>
          </div>
        )}

      </section>

      <div className="tab-bar">
        {(["areas", "fleet", "availability", "history"] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            aria-pressed={tab === t}
            // Clicking the open tab closes it — collapsed is a real state, not just the
            // absence of a selection.
            onClick={() => onTabChange(tab === t ? null : t)}
          >
            {t === "availability" ? "Availability" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === "areas" && (
        <AreasTab {...tabProps} bounds={bounds} catalog={catalog} removeRow={removeRow} />
      )}
      {tab === "fleet" && (
        <FleetTab
          {...tabProps}
          expert={expert}
          catalog={catalog}
          mowerKey={mowerKey}
          removeRow={removeRow}
        />
      )}
      {tab === "availability" && (
        <AvailabilityTab {...tabProps} tip={tip} setTip={setTip} removeRow={removeRow} />
      )}
      {tab === "history" && <HistoryTab {...tabProps} tip={tip} setTip={setTip} />}
    </div>
  );
}
