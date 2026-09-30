import { useState } from "react";

import { HourCell } from "./cells";
import type { Scenario } from "../types";
import "./AddServicePanel.css";

/** "Add a service" from the schedule editor (ADR-0039). Lives here rather than in the
 *  table footer so the chart view reaches it too. It only picks the slot — area, mower,
 *  start hour; `App.addService` also raises the area's `min_services` so the solver must
 *  produce the extra service, and records the slot as a weak preference.
 *
 *  Collapsed to a `+ Add a service` disclosure by default (UI review 2026-09-10) — adding
 *  a service is the exception, not the common case, so the picker stays out of the way
 *  until asked for. The `open` state is local and resets whenever the schedule editor
 *  closes (this component unmounts with it). */
/** The mower select's "leave it to the solver" option — a time-only preference
 *  (`PreferredTask.mower = null`, ADR-0031). `<select>` values are always strings, so this
 *  sentinel stands in for `null` in the DOM and is translated back at the edges. It can
 *  never collide with a real mower name (those come from the scenario, never blank). */
const ANY_MOWER = "";

export function AddServicePanel({
  scenario,
  onAdd,
  disabled = false,
}: {
  scenario: Scenario;
  onAdd: (area: string, start: number, mower: string | null) => void;
  /** Frozen while a solve is in flight — an add during a solve is discarded by the next
   *  poll but leaves an orphaned min_services bump on the draft (code-review follow-up). */
  disabled?: boolean;
}) {
  const mowersFor = (a: string) =>
    scenario.mowers.filter((m) => m.can_mow.includes(a)).map((m) => m.name);

  const [open, setOpen] = useState(false);
  const [area, setArea] = useState(scenario.areas[0]?.name ?? "");
  const [hour, setHour] = useState(0);
  const [mower, setMower] = useState(mowersFor(area)[0] ?? "");

  const options = mowersFor(area);
  // Keep the mower valid when the area changes without a second render. ANY_MOWER is
  // always valid, on top of whatever the area's own capable mowers are.
  const currentMower =
    mower === ANY_MOWER || options.includes(mower) ? mower : (options[0] ?? "");

  return (
    <div className="add-service-block">
      <button
        type="button"
        className="add-service-trigger link-button"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        + Add a service
      </button>
      {open && (
        <div className="add-service">
          <select
            aria-label="area for the new service"
            value={area}
            disabled={disabled}
            onChange={(e) => {
              setArea(e.target.value);
              setMower(mowersFor(e.target.value)[0] ?? "");
            }}
          >
            {scenario.areas.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
          <select
            aria-label="mower for the new service"
            value={currentMower}
            disabled={disabled || options.length === 0}
            onChange={(e) => setMower(e.target.value)}
          >
            <option value={ANY_MOWER}>(any mower)</option>
            {options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <HourCell
            value={hour}
            onCommit={setHour}
            label="start hour for the new service"
            commitOnChange
            disabled={disabled}
            max={scenario.horizon_hours - 1}
          />
          <button
            type="button"
            className="link-button"
            disabled={disabled || options.length === 0}
            onClick={() => onAdd(area, hour, currentMower === ANY_MOWER ? null : currentMower)}
          >
            + add
          </button>
          <span className="muted add-service-note">
            The count is forced up by one; the exact area, hour and (if given) mower are a
            request the solver tries to honour — leave the mower as "(any mower)" to ask only
            for the hour.
          </span>
        </div>
      )}
    </div>
  );
}
