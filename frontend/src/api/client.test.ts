import { beforeEach, describe, expect, it, vi } from "vitest";

import { explainSolve, startSolve } from "./client";
import type { Schedule } from "../types";

/**
 * ADR-0031 decision 5: a solve carrying no preferences must produce exactly the request
 * it produced before the feature existed. `mode: "off"` and an absent payload are the
 * same thing on the wire, and the backend's own byte-identity invariant
 * (`test_service.py::test_inactive_preferences_leave_the_program_and_args_untouched`)
 * only holds if the client never sends a dead payload for the API to ignore.
 */
describe("startSolve", () => {
  const body = () => JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ job_id: "j1" })))),
    );
  });
  // No `unstubAllGlobals` here: it would also drop the shared `localStorage` stub that
  // `src/test/setup.ts` installs once for the whole run. Each test file gets its own
  // environment, and `beforeEach` re-stubs `fetch` anyway.

  it("omits the preferences key entirely for a cold solve", async () => {
    await startSolve({ scenarioId: "toy-course" }, { timeLimitS: 20 });
    expect(body()).toEqual({ scenario_id: "toy-course", time_limit_s: 20 });
  });

  it("omits it for an explicit mode 'off' too", async () => {
    await startSolve(
      { scenarioId: "toy-course" },
      { preferences: { tasks: [], mode: "off", level: "top" } },
    );
    expect(body()).not.toHaveProperty("preferences");
  });

  it("sends the payload when there is one", async () => {
    const preferences = {
      mode: "weak" as const,
      level: "top" as const,
      tasks: [{ area: "A1", start: 2, mower: "M1", origin: "frozen" as const }],
    };
    await startSolve({ scenarioId: "toy-course" }, { preferences });
    expect(body().preferences).toEqual(preferences);
  });

  it("still omits the expert command line when it is empty", async () => {
    await startSolve({ scenario: { name: "x" } as never }, { clingoArgs: [] });
    expect(body()).not.toHaveProperty("clingo_args");
  });
});

describe("explainSolve", () => {
  const body = () => JSON.parse(vi.mocked(fetch).mock.calls.at(-1)![1]!.body as string);
  const schedule: Schedule = { tasks: [], cost: [], violations: [] };
  const preferences = {
    mode: "weak" as const,
    level: "top" as const,
    tasks: [{ area: "A1", start: 2, mower: "M1", origin: "edited" as const }],
  };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ report: { edits: [], reinstated: [], ripple: [], budget_s: 8, budget_exhausted: false } }),
          ),
        ),
      ),
    );
  });

  it("sends scenario_id, not scenario, for a scenario-id target", async () => {
    await explainSolve({ scenarioId: "toy-course" }, preferences, schedule);
    expect(body()).toEqual({ scenario_id: "toy-course", preferences, schedule });
  });

  it("sends the full scenario body, not scenario_id, for a scenario target", async () => {
    const scenario = { name: "x" } as never;
    await explainSolve({ scenario }, preferences, schedule);
    expect(body()).toEqual({ scenario, preferences, schedule });
  });

  it("omits budget_s when not given, includes it when given", async () => {
    await explainSolve({ scenarioId: "toy-course" }, preferences, schedule);
    expect(body()).not.toHaveProperty("budget_s");

    await explainSolve({ scenarioId: "toy-course" }, preferences, schedule, 15);
    expect(body().budget_s).toBe(15);
  });

  it("resolves to the report, not the wrapper envelope", async () => {
    const report = await explainSolve({ scenarioId: "toy-course" }, preferences, schedule);
    expect(report).toEqual({ edits: [], reinstated: [], ripple: [], budget_s: 8, budget_exhausted: false });
  });
});
