// Small presentational cells shared by the ScenarioSummary overview and its tab bodies
// (components/scenario/). Styling lives in ScenarioSummary.css.

/** A single labelled figure in the overview's fact row. */
export function Fact({ value, label }: { value: string; label: string }) {
  return (
    <div className="summary-fact">
      <div className="summary-fact-value">{value}</div>
      <div className="summary-fact-label">{label}</div>
    </div>
  );
}

/** A name cell — uncontrolled so typing doesn't re-render the row (a rename cascades
 * through `can_mow` / history / durations, which would otherwise steal focus). Commits on
 * blur or Enter; `key` on the input resets it when the name changes from outside (reset). */
export function NameCell({
  value,
  onCommit,
  error,
  disabled,
}: {
  value: string;
  onCommit: (name: string) => void;
  error?: string;
  disabled?: boolean;
}) {
  return (
    <span className="cell-num">
      <input
        key={value}
        type="text"
        className={`cell-input cell-input-name-cell${error ? " cell-input-invalid" : ""}`}
        defaultValue={value}
        disabled={disabled}
        spellCheck={false}
        aria-invalid={error ? true : undefined}
        onBlur={(e) => onCommit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
      {error && <span className="field-error">{error}</span>}
    </span>
  );
}

/** A compact number cell for the edit mode. Empty input -> null (means "unset" for the
 * optional fields; the caller coerces to a floor for the required ones). */
export function NumCell({
  value,
  onChange,
  error,
  disabled,
  suffix,
  placeholder,
  width = "normal",
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  error?: string;
  disabled?: boolean;
  suffix?: string;
  placeholder?: string;
  width?: "narrow" | "normal" | "wide";
}) {
  return (
    <span className="cell-num">
      <input
        type="number"
        className={`cell-input cell-input-${width}${error ? " cell-input-invalid" : ""}`}
        value={value ?? ""}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
      {suffix && <span className="cell-suffix">{suffix}</span>}
      {error && <span className="field-error">{error}</span>}
    </span>
  );
}

/** An hour-of-day cell that commits on blur or Enter, not per keystroke (ADR-0029).
 *
 * Availability windows are keyed by their interval, so a half-typed value can transiently
 * equal another window and merge the two rows under the cursor. Committing once, when the
 * field is left, closes that window — and stops each keystroke firing a derived-figures
 * recompute. Uncontrolled like `NameCell`, with `key` resetting it from outside (Undo,
 * a scenario switch). */
export function HourCell({
  value,
  onCommit,
  error,
  disabled,
  label,
  commitOnChange = false,
}: {
  value: number;
  onCommit: (hour: number) => void;
  error?: string;
  disabled?: boolean;
  /** Accessible name — the field has no visible label of its own. */
  label: string;
  /** Commit on every change — a spinner click or a keystroke — instead of waiting for
   * blur/Enter. Stays uncontrolled (no `key`), so free typing is not disrupted and an
   * emptied field simply waits for the next digit. Use only where the field is not keyed
   * by its own value the way availability windows are (ADR-0029) and where an external
   * change to `value` always remounts the row (the schedule editor: a new solve mints
   * fresh task uids). Without it a user who nudged the number spinner saw nothing happen
   * until they also pressed Enter. */
  commitOnChange?: boolean;
}) {
  if (commitOnChange) {
    return (
      <span className="cell-num">
        <input
          type="number"
          className={`cell-input cell-input-narrow${error ? " cell-input-invalid" : ""}`}
          defaultValue={value}
          disabled={disabled}
          aria-label={label}
          aria-invalid={error ? true : undefined}
          // Empty commits 0 (a valid start-of-week hour) rather than being skipped, so the
          // field and the committed value never disagree — clearing then acting on it
          // can't submit a stale value (code-review follow-up).
          onChange={(e) => onCommit(e.target.value === "" ? 0 : Number(e.target.value))}
        />
        {error && <span className="field-error">{error}</span>}
      </span>
    );
  }
  return (
    <span className="cell-num">
      <input
        key={value}
        type="number"
        className={`cell-input cell-input-narrow${error ? " cell-input-invalid" : ""}`}
        defaultValue={value}
        disabled={disabled}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        onBlur={(e) => {
          const v = e.target.value === "" ? 0 : Number(e.target.value);
          if (v !== value) onCommit(v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
      {error && <span className="field-error">{error}</span>}
    </span>
  );
}
