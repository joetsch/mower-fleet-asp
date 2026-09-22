/** The app's on/off toggle — a real checkbox, visually restyled as a macOS-style sliding
 *  switch (`.switch-*` in `App.css`).
 *
 *  A checkbox rather than a styled `<div>` so keyboard and screen-reader behaviour come
 *  for free; `role="switch"` then tells assistive tech it is an on/off toggle rather than
 *  a form checkbox, and is what the component tests query by.
 *
 *  Extracted on its third copy — expert mode, the Gantt's band toggles, and the schedule
 *  editor were each carrying the same seven lines. */
export function Switch({
  label,
  on,
  onChange,
  disabled,
}: {
  label: string;
  on: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="mode-toggle">
      <span>{label}</span>
      <input
        type="checkbox"
        role="switch"
        className="switch-input"
        checked={on}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="switch-track" aria-hidden="true">
        <span className="switch-thumb" />
      </span>
    </label>
  );
}
