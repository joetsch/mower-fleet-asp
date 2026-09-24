import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HourCell } from "./cells";

// The number spinner "did nothing" until Enter/blur (schedule editor). `commitOnChange`
// makes a spinner click or a keystroke land immediately; the default stays blur-only,
// which availability windows still need (ADR-0029).
describe("HourCell", () => {
  it("commits only on blur by default", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<HourCell value={5} onCommit={onCommit} label="h" />);
    const input = screen.getByLabelText("h");

    await user.clear(input);
    await user.type(input, "9");
    expect(onCommit).not.toHaveBeenCalled(); // still typing — nothing committed

    await user.tab();
    expect(onCommit).toHaveBeenCalledWith(9);
  });

  it("commits on change when commitOnChange is set", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<HourCell value={5} onCommit={onCommit} label="h" commitOnChange />);

    await user.type(screen.getByLabelText("h"), "7"); // 5 -> "57"
    expect(onCommit).toHaveBeenCalledWith(57);
  });

  it("commits 0 when a commitOnChange field is cleared, so it can't submit a stale value", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<HourCell value={5} onCommit={onCommit} label="h" commitOnChange />);

    await user.clear(screen.getByLabelText("h"));
    expect(onCommit).toHaveBeenCalledWith(0);
  });

  // Dragging a bar is bounded by `ganttScale.clampStart`; typing an hour was not bounded
  // at all. A negative or fractional hour fails `PreferredTask.start` (int, ge=0) and
  // surfaces as a raw 422 in the error banner, and an hour past the end of the week is
  // reported with a no-go-window reason that does not fit it.
  it("clamps a typed hour into the week and keeps it whole", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(
      <HourCell value={5} onCommit={onCommit} label="h" commitOnChange max={167} />,
    );
    const input = screen.getByLabelText("h");

    await user.clear(input);
    await user.type(input, "-3");
    expect(onCommit).toHaveBeenLastCalledWith(0);

    await user.clear(input);
    await user.type(input, "999");
    expect(onCommit).toHaveBeenLastCalledWith(167);

    await user.clear(input);
    await user.type(input, "4.5");
    expect(onCommit).toHaveBeenLastCalledWith(4);
  });
});
