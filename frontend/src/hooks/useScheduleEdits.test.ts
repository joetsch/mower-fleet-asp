import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useScheduleEdits } from "./useScheduleEdits";
import { makeScenario, makeSolveResult } from "../lib/fixtures";
import type { SolveResult } from "../types";

/** The two-task plan `makeSolveResult()` returns: A1 @ h2 and A2 @ h20, both on M1. */
const render = (result: SolveResult | null = makeSolveResult()) =>
  renderHook(({ r }) => useScheduleEdits(r), { initialProps: { r: result } });

describe("useScheduleEdits", () => {
  it("mirrors the solved plan, with a uid that is not the solver's rank", () => {
    const { result } = render();
    expect(result.current.tasks).toHaveLength(2);
    expect(result.current.tasks[0]).toMatchObject({
      area: "A1",
      mower: "M1",
      start: 2,
      end: 17,
      pin: "auto",
      edited: false,
      added: false,
      sourceTask: 1,
    });
    expect(result.current.tasks.map((t) => t.uid)).toEqual(["p0", "p1"]);
    expect(result.current.dirty).toBe(false);
  });

  it("records a move as an edit and keeps the duration", () => {
    const { result } = render();
    act(() => result.current.moveTask("p0", 40));
    expect(result.current.tasks[0]).toMatchObject({ start: 40, end: 55, edited: true });
    expect(result.current.dirty).toBe(true);
  });

  it("recomputes the duration from the scenario when the mower changes", () => {
    const withSecondMower = makeScenario();
    withSecondMower.mowers.push({
      name: "M2",
      can_mow: ["A1", "A2"],
      model: "AM_580L EPOS",
      area_capacity_m2_per_day: 8000,
    });
    withSecondMower.base_durations!.push(
      { area: "A1", mower: "M2", hours: 6 },
      { area: "A2", mower: "M2", hours: 4 },
    );
    const { result } = render();
    act(() => result.current.reassign("p0", "M2", withSecondMower));
    expect(result.current.tasks[0]).toMatchObject({ mower: "M2", start: 2, end: 8, edited: true });
  });

  it("pins and releases without counting as an edit", () => {
    const { result } = render();
    act(() => result.current.setPin("p1", "released"));
    expect(result.current.tasks[1]).toMatchObject({ pin: "released", edited: false });
    expect(result.current.dirty).toBe(true);

    act(() => result.current.setPin("p1", "auto"));
    expect(result.current.dirty).toBe(false);
  });

  it("drops a drag when the task is released, so a later 'keep' does not resurrect it", () => {
    // Owner report, 2026-09-24: drag a bar, then release it. No preference is ever sent
    // for a released task, so the drag was invisible to the solver either way -- but the
    // task kept its dragged hour and `edited: true`. Clicking "keep" afterwards then
    // resurrected the drag as if it had just been made.
    const { result } = render();
    act(() => result.current.moveTask("p0", 40));
    expect(result.current.tasks[0]).toMatchObject({ start: 40, edited: true });

    act(() => result.current.setPin("p0", "released"));
    expect(result.current.tasks[0]).toMatchObject({ start: 2, end: 17, edited: false });

    act(() => result.current.setPin("p0", "auto"));
    expect(result.current.tasks[0]).toMatchObject({ start: 2, end: 17, edited: false });
    expect(result.current.dirty).toBe(false);
  });

  it("throws the whole working copy away on reset", () => {
    const { result } = render();
    act(() => result.current.moveTask("p0", 40));
    act(() => result.current.setPin("p1", "released"));
    act(() => result.current.reset());
    expect(result.current.dirty).toBe(false);
    expect(result.current.tasks[0].start).toBe(2);
  });

  it("rebuilds when a new plan arrives, dropping the edits with it", () => {
    const { result, rerender } = render();
    act(() => result.current.moveTask("p0", 40));

    const next = makeSolveResult();
    next.schedule!.tasks = [{ task: 1, area: "A1", mower: "M1", start: 8, end: 23 }];
    rerender({ r: next });

    expect(result.current.tasks).toHaveLength(1);
    expect(result.current.tasks[0].start).toBe(8);
    expect(result.current.dirty).toBe(false);
  });

  it("restores a captured working copy instead of rebuilding, when its plan comes back", () => {
    // The replan Undo (UI review 2026-09-10): a Re-solve / Move forward that consumed an
    // un-re-solved edit must be undoable back to that edit, not to the last solver output.
    const original = makeSolveResult();
    const { result, rerender } = render(original);
    act(() => result.current.moveTask("p0", 40));
    const captured = result.current.tasks;

    // A re-solve replaces the plan; the working copy rebuilds and the edit is gone.
    const resolved = makeSolveResult();
    resolved.schedule!.tasks = [{ task: 1, area: "A1", mower: "M1", start: 8, end: 23 }];
    rerender({ r: resolved });
    expect(result.current.tasks[0]).toMatchObject({ start: 8, edited: false });

    // Undo: the app restores the captured copy and swaps `original` back in one batch.
    act(() => {
      result.current.restore(original, captured);
      rerender({ r: original });
    });
    expect(result.current.tasks[0]).toMatchObject({ start: 40, edited: true });
    expect(result.current.dirty).toBe(true);
  });

  it("adds a service with its own uid, origin marker and computed duration", () => {
    const { result } = render();
    act(() => result.current.addTask("A2", 100, "M1", makeScenario()));

    const added = result.current.tasks.at(-1)!;
    expect(added).toMatchObject({
      area: "A2",
      mower: "M1",
      start: 100,
      end: 109, // A2/M1 base duration is 9 h in the fixture
      added: true,
      sourceTask: null,
    });
    expect(added.uid).not.toBe("p0");
    expect(result.current.dirty).toBe(true);
  });

  it("gives every added service a distinct uid, even two in the same tick", () => {
    const { result } = render();
    const s = makeScenario();
    // Both in one `act`: a counter held in state would still read its pre-update value
    // for the second call and mint the same uid twice.
    act(() => {
      result.current.addTask("A2", 100, "M1", s);
      result.current.addTask("A2", 120, "M1", s);
    });
    const uids = result.current.tasks.map((t) => t.uid);
    expect(uids).toHaveLength(4);
    expect(new Set(uids).size).toBe(uids.length);
  });

  it("copes with a result that has no schedule at all", () => {
    const { result } = render(null);
    expect(result.current.tasks).toEqual([]);
    expect(result.current.dirty).toBe(false);
  });
});
