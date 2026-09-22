import { useEffect, useRef, useState } from "react";

import type { ScenarioListEntry, ScenarioSummary } from "../types";
import "./LoadScenarioMenu.css";

interface Props {
  scenarios: ScenarioListEntry[];
  currentId: string | null;
  onSelect: (id: string) => void;
  /** "New scenario…" — a command below the list (ADR-0027). Omitted = no entry. */
  onNew?: () => void;
}

/** One-line factual summary — computed from the scenario data, never authored, so it
 *  cannot drift when a later iteration adds scenario editing. */
function summaryLine(s: ScenarioSummary): string {
  const parts = [`${s.holes} holes`, `${s.areas} areas`, `${s.mowers} mowers`];
  if (s.load_factor != null) parts.push(`load ${s.load_factor.toFixed(2)}`);
  return parts.join(" · ");
}

/**
 * "Load scenario" popover. A plain button + absolutely-positioned list; closes on
 * pick, on Escape, and on a click outside. Save / Save as… / Delete live in the
 * Scenario card's edit toolbar, not here (ADR-0026).
 */
export function LoadScenarioMenu({ scenarios, currentId, onSelect, onNew }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="load-menu" ref={rootRef}>
      <button
        type="button"
        className="load-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        scenario: <strong>{currentId ?? "…"}</strong> <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <ul className="load-menu-list" role="menu">
          {scenarios.map((s) => (
            <li key={s.id} role="none">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={s.id === currentId}
                className={s.id === currentId ? "active" : ""}
                onClick={() => {
                  onSelect(s.id);
                  setOpen(false);
                }}
              >
                <span className="load-menu-name">
                  {s.id}
                  {s.id === currentId && <span aria-hidden="true"> ✓</span>}
                </span>
                <span className="load-menu-sub">{summaryLine(s.summary)}</span>
              </button>
            </li>
          ))}
          {onNew && (
            <>
              <li role="separator" className="load-menu-sep" />
              <li role="none">
                <button
                  type="button"
                  role="menuitem"
                  className="load-menu-new"
                  onClick={() => {
                    onNew();
                    setOpen(false);
                  }}
                >
                  <span className="load-menu-name">New scenario…</span>
                  <span className="load-menu-sub">a minimal course you can build on</span>
                </button>
              </li>
            </>
          )}
        </ul>
      )}
    </div>
  );
}
