import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRunLog } from "./useRunLog";
import type { ScheduledTask } from "../types";

const task = (area: string, start: number, end: number): ScheduledTask => ({
  area,
  task: 1,
  mower: "M1",
  start,
  end,
});

const outcome = {
  services: 40,
  carried: 3,
  kept: 3,
  quality: [0, 0, 0, 0, 0],
  solveTimeS: 2.1,
  optimal: true,
  setting: "top" as const,
};

describe("useRunLog", () => {
  it("rebases the executed track by -hours on every step and keeps the now-past tasks", () => {
    const { result } = renderHook(() => useRunLog());

    act(() =>
      result.current.begin({
        hours: 24,
        nowLabel: "Tue 13:00",
        executedThisRoll: [task("A1", 4, 10), task("A2", 20, 26)],
      }),
    );
    // both tasks were before hour 24 -> now at negative offsets relative to the new now
    expect(result.current.executed.map((t) => [t.area, t.start])).toEqual([
      ["A1", -20],
      ["A2", -4],
    ]);

    act(() =>
      result.current.begin({
        hours: 24,
        nowLabel: "Wed 13:00",
        executedThisRoll: [task("A1", 12, 18)],
      }),
    );
    // the first step's tasks slid another -24; the new step's task is appended at -12
    expect(result.current.executed.map((t) => [t.area, t.start])).toEqual([
      ["A1", -44],
      ["A2", -28],
      ["A1", -12],
    ]);
  });

  it("opens a pending roll entry and settles it once", () => {
    const { result } = renderHook(() => useRunLog());
    act(() => result.current.begin({ hours: 24, nowLabel: "Tue 13:00", executedThisRoll: [] }));

    expect(result.current.pending).toBe(true);
    expect(result.current.rolls).toEqual([
      { hours: 24, nowLabel: "Tue 13:00", outcome: null, failed: false },
    ]);

    act(() => result.current.settle(outcome));
    expect(result.current.pending).toBe(false);
    expect(result.current.rolls[0].outcome).toEqual(outcome);
  });

  // A roll's re-solve can end with no plan of its own (stopped, or failed). The entry must
  // stop being pending without borrowing figures from whatever plan is on screen — which,
  // after a stop, is the one from before the roll.
  it("marks a roll as having produced no plan, and stops pending", () => {
    const { result } = renderHook(() => useRunLog());
    act(() => result.current.begin({ hours: 24, nowLabel: "Tue 13:00", executedThisRoll: [] }));
    expect(result.current.pending).toBe(true);

    act(() => result.current.abandon());
    expect(result.current.pending).toBe(false);
    expect(result.current.rolls[0]).toEqual({
      hours: 24,
      nowLabel: "Tue 13:00",
      outcome: null,
      failed: true,
    });
  });

  it("steps back one roll on undo, executed track and entries together", () => {
    const { result } = renderHook(() => useRunLog());
    act(() => result.current.begin({ hours: 24, nowLabel: "Tue", executedThisRoll: [task("A1", 4, 10)] }));
    act(() => result.current.settle(outcome));
    act(() => result.current.begin({ hours: 24, nowLabel: "Wed", executedThisRoll: [task("A2", 5, 11)] }));

    expect(result.current.rolls).toHaveLength(2);
    expect(result.current.executed).toHaveLength(2);

    act(() => result.current.undo());
    expect(result.current.rolls).toEqual([
      { hours: 24, nowLabel: "Tue", outcome, failed: false },
    ]);
    // back to the first step's state: A1 started at 4, rebased once by -24
    expect(result.current.executed.map((t) => [t.area, t.start])).toEqual([["A1", -20]]);
  });

  it("clears everything on reset", () => {
    const { result } = renderHook(() => useRunLog());
    act(() => result.current.begin({ hours: 24, nowLabel: "Tue", executedThisRoll: [task("A1", 4, 10)] }));

    act(() => result.current.reset());
    expect(result.current.rolls).toEqual([]);
    expect(result.current.executed).toEqual([]);
    // undo has nothing to restore after a reset
    act(() => result.current.undo());
    expect(result.current.rolls).toEqual([]);
  });
});
