import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import * as client from "./api/client";
import { makeCatalog, makeRollResponse, makeScenario, makeSolveResult } from "./lib/fixtures";
import type {
  ExplanationReport,
  PreferenceLevel,
  Scenario,
  ScenarioBundle,
  ScenarioDerived,
  ScenarioListEntry,
  SolveResult,
} from "./types";

vi.mock("./api/client");

const derivedFor = (s: Scenario): ScenarioDerived => ({
  bounds: Object.fromEntries(s.areas.map((a) => [a.name, [3, 6]])),
  load_factor: 0.5,
});
const bundleFor = (s: Scenario): ScenarioBundle => ({ scenario: s, derived: derivedFor(s) });

/** The minimal from-scratch template `POST /api/scenario/template` returns. */
function templateScenario(): Scenario {
  return {
    name: "new-scenario",
    areas: [
      {
        name: "Area 1",
        type: "FAIRWAY",
        hole: 1,
        priority: 1,
        min_interval: 18,
        max_interval: 24,
        schedule: makeScenario().areas[0].schedule,
        size_m2: 6000,
        min_services: null,
        max_services: null,
      },
    ],
    mowers: [
      { name: "Mower 1", can_mow: ["Area 1"], model: "AM_580L EPOS", area_capacity_m2_per_day: 8000 },
    ],
    history: [{ area: "Area 1", mower: "Mower 1", start: -18, completion: 0 }],
    horizon_hours: 168,
    horizon_start_hour: 13,
    duration_seed: 0,
    base_durations: [{ area: "Area 1", mower: "Mower 1", hours: 18 }],
  };
}

const entry = (id: string): ScenarioListEntry => ({
  id,
  summary: { holes: 1, areas: 2, mowers: 1, load_factor: 0.5 },
});

beforeEach(() => {
  vi.mocked(client.fetchCatalog).mockResolvedValue(makeCatalog());
  vi.mocked(client.fetchDerived).mockImplementation((s) => Promise.resolve(derivedFor(s)));
  vi.mocked(client.fetchScenarioTemplate).mockResolvedValue(bundleFor(templateScenario()));
  // Default: a one-scenario library.
  vi.mocked(client.fetchScenarios).mockResolvedValue([entry("well-resourced")]);
  vi.mocked(client.fetchScenario).mockResolvedValue(bundleFor(makeScenario()));
  // Auto-mocked client functions return `undefined`, which the solve machine would then
  // `.catch()` on — give the cancel path a real resolved status.
  vi.mocked(client.cancelSolve).mockResolvedValue({ job_id: "j1", done: true, result: null });
});

type User = ReturnType<typeof userEvent.setup>;

async function openNewScenario(user: User) {
  await user.click(await screen.findByRole("button", { name: /scenario:/i }));
  await user.click(screen.getByRole("menuitem", { name: /New scenario/i }));
  await screen.findByDisplayValue("new-scenario");
}

const SOLVE_BUTTON = /^(Solve schedule|Re-solve)$/;

/** Run one solve to completion, so a schedule is on screen. */
async function solveToCompletion(user: User) {
  vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j1" });
  vi.mocked(client.pollSolve).mockResolvedValue({
    job_id: "j1",
    done: true,
    result: makeSolveResult(),
  });
  await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
  await screen.findByText("services");
}

/** Start a solve that never reports `done` — the job stays live until something stops it. */
async function startEndlessSolve(user: User) {
  vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j1" });
  vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: false, result: null });
  await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
  await screen.findByRole("button", { name: "Stop solving" });
}

async function switchScenarioTo(user: User, id: string) {
  await user.click(screen.getByRole("button", { name: /scenario:/i }));
  await user.click(screen.getByRole("menuitemradio", { name: new RegExp(id) }));
}

describe("create from scratch", () => {
  it("opens the template in edit mode with no Delete button", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openNewScenario(user);

    expect(screen.getByRole("button", { name: "Done editing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new \(unsaved\)/i })).toBeInTheDocument();
    // never fetched a fake slug for the unsaved draft — only the initial library load
    expect(client.fetchScenario).toHaveBeenCalledTimes(1);
  });

  it("Save saves under the slugified name via createScenario", async () => {
    const user = userEvent.setup();
    vi.mocked(client.createScenario).mockImplementation((id, s) =>
      Promise.resolve(bundleFor({ ...s, name: id })),
    );
    render(<App />);
    await openNewScenario(user);

    await user.click(screen.getByRole("button", { name: "Save as…" }));
    const field = screen.getByPlaceholderText("new scenario name");
    await user.clear(field);
    await user.type(field, "My Course");
    await screen.findByText("→ my-course");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(client.createScenario).toHaveBeenCalledWith(
      "my-course",
      expect.objectContaining({ name: "my-course" }),
    );
  });

  it("shows the empty-library card when the library is empty", async () => {
    vi.mocked(client.fetchScenarios).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText("No scenarios yet");
    expect(screen.queryByText(/^Error:/)).not.toBeInTheDocument();
    expect(client.fetchScenario).not.toHaveBeenCalled(); // no fake slug fetched

    await user.click(screen.getByRole("button", { name: "New scenario" }));
    await screen.findByDisplayValue("new-scenario");
  });
});

/**
 * The net under the App.tsx hook extraction (known-hazards "Refactor before Iteration 5"
 * item 1). Four transitions — `selectScenario`, `afterWrite`, `doNewScenario` and
 * `doDelete`'s empty-library branch — each hand-maintain a list of state resets that must
 * be kept in sync. These cases pin what those lists actually do, through the DOM only, so
 * they survive the state moving into hooks unchanged.
 */
describe("state resets across scenario transitions", () => {
  const twoScenarios = () =>
    vi.mocked(client.fetchScenarios).mockResolvedValue([
      entry("well-resourced"),
      entry("tight-fleet"),
    ]);

  it("switching scenario clears the result, the draft, edit mode, save-as and the undo strip", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await user.click(screen.getAllByRole("button", { name: "remove" })[1]); // A2 — dirty + undo
    await screen.findByText(/Removed area "A2"/);
    await user.click(screen.getByRole("button", { name: "Save as…" }));
    await solveToCompletion(user);
    expect(screen.getByPlaceholderText("new scenario name")).toBeInTheDocument();

    await switchScenarioTo(user, "tight-fleet");
    // The draft is dirty, so the discard guard asks first (see the guard describe below).
    await user.click(await screen.findByRole("button", { name: "Discard" }));

    expect(await screen.findByRole("button", { name: "Edit scenario" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Solve schedule" })).toBeInTheDocument();
    expect(screen.queryByText("services")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("new scenario name")).not.toBeInTheDocument();
    expect(screen.queryByText(/Removed area/)).not.toBeInTheDocument();
    expect(screen.getAllByText("A2").length).toBeGreaterThan(0); // the draft is gone
    expect(client.fetchScenario).toHaveBeenLastCalledWith("tight-fleet");
  });

  it("switching scenario with a clean draft does not ask", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await switchScenarioTo(user, "tight-fleet");

    expect(screen.queryByText(/Discard unsaved changes/)).not.toBeInTheDocument();
    expect(client.fetchScenario).toHaveBeenLastCalledWith("tight-fleet");
  });

  it("switching scenario mid-solve cancels the abandoned job server-side", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    await startEndlessSolve(user);
    await switchScenarioTo(user, "tight-fleet");

    expect(client.cancelSolve).toHaveBeenCalledWith("j1");
    expect(await screen.findByRole("button", { name: "Solve schedule" })).toBeInTheDocument();
  });

  it("switching scenario clears an error banner", async () => {
    twoScenarios();
    vi.mocked(client.startSolve).mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Solve schedule" }));
    await screen.findByText(/boom/);

    await switchScenarioTo(user, "tight-fleet");
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  });

  it("Save exits edit mode and reloads the library, keeping a value-only edit's plan", async () => {
    const user = userEvent.setup();
    vi.mocked(client.saveScenario).mockImplementation((_id, s) =>
      Promise.resolve(bundleFor(s)),
    );
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await user.type(screen.getByDisplayValue("5000"), "1"); // a value edit — not structural
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(client.saveScenario).toHaveBeenCalledWith(
      "test",
      expect.objectContaining({ name: "test" }),
    );
    expect(await screen.findByRole("button", { name: "Edit scenario" })).toBeInTheDocument();
    // Stage 2 (ADR-0037): the plan the save did not invalidate stays on screen.
    expect(screen.getByText("services")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-solve" })).toBeInTheDocument();
    await waitFor(() => expect(client.fetchScenarios).toHaveBeenCalledTimes(2));
  });

  it("Save drops the plan when a structural edit already invalidated it", async () => {
    const user = userEvent.setup();
    vi.mocked(client.saveScenario).mockImplementation((_id, s) =>
      Promise.resolve(bundleFor(s)),
    );
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await solveToCompletion(user);
    expect(screen.getByText("services")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "remove" })[1]); // remove A2 — structural
    await screen.findByText(/Removed area "A2"/);
    expect(screen.queryByText("services")).not.toBeInTheDocument(); // cleared the instant it changed

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("button", { name: "Solve schedule" })).toBeInTheDocument();
    expect(screen.queryByText("services")).not.toBeInTheDocument();
  });

  it("renaming an area is structural — it clears the plan (bars are keyed by the old name)", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await solveToCompletion(user);
    expect(screen.getByText("services")).toBeInTheDocument();

    const nameField = screen.getByDisplayValue("A1");
    await user.clear(nameField);
    await user.type(nameField, "Fairway 1");
    await user.tab(); // NameCell commits on blur

    expect(screen.queryByText("services")).not.toBeInTheDocument();
  });

  it("a failed Save keeps the draft, edit mode and the shown error", async () => {
    const user = userEvent.setup();
    vi.mocked(client.saveScenario).mockRejectedValue(new Error("409 already exists"));
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await user.type(screen.getByDisplayValue("5000"), "1");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/409 already exists/);
    expect(screen.getByRole("button", { name: "Done editing" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("50001")).toBeInTheDocument();
  });

  it("New scenario… clears the result and opens the template in edit mode", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    await solveToCompletion(user);
    await openNewScenario(user);

    expect(screen.getByRole("button", { name: "Solve schedule" })).toBeInTheDocument();
    expect(screen.queryByText("services")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done editing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("deleting the last scenario clears everything and shows the empty-library card", async () => {
    const user = userEvent.setup();
    vi.mocked(client.fetchScenarios)
      .mockResolvedValueOnce([entry("well-resourced")])
      .mockResolvedValueOnce([]);
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));

    await screen.findByText("No scenarios yet");
    expect(client.deleteScenario).toHaveBeenCalledWith("well-resourced");
    expect(screen.queryByText("services")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Done editing" })).not.toBeInTheDocument();

    // The two assertions above are weak on their own — with no scenario the whole panel
    // unmounts, so a stale `result` / `editing` would be invisible. Starting a new
    // scenario mounts it again: that is where a schedule left over from the deleted
    // scenario would surface, as "Re-solve" instead of "Solve schedule".
    await user.click(screen.getByRole("button", { name: "New scenario" }));
    await screen.findByDisplayValue("new-scenario");
    expect(screen.getByRole("button", { name: "Solve schedule" })).toBeInTheDocument();
    expect(screen.queryByText("services")).not.toBeInTheDocument();
  });
});

// The "discard unsaved changes?" guard (the ADR-0027 deferral). A dirty draft is only
// thrown away on purpose. Same two-step-confirm idiom as Delete — deliberately not
// `window.confirm`, which blocks the event loop and looks nothing like the rest of the app.
describe("discard-unsaved-changes guard", () => {
  const twoScenarios = () =>
    vi.mocked(client.fetchScenarios).mockResolvedValue([
      entry("well-resourced"),
      entry("tight-fleet"),
    ]);

  /** Open the editor and make one real edit, so the draft is dirty. */
  async function dirtyDraft(user: User) {
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await user.click(screen.getAllByRole("button", { name: "remove" })[1]); // A2
    await screen.findByText(/Removed area "A2"/);
  }

  it("asks before a scenario switch discards a dirty draft", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);
    await dirtyDraft(user);

    await switchScenarioTo(user, "tight-fleet");

    expect(await screen.findByText(/Discard unsaved changes/)).toBeInTheDocument();
    expect(client.fetchScenario).not.toHaveBeenCalledWith("tight-fleet");
  });

  it("Discard goes through with the switch", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);
    await dirtyDraft(user);
    await switchScenarioTo(user, "tight-fleet");

    await user.click(await screen.findByRole("button", { name: "Discard" }));

    expect(await screen.findByRole("button", { name: "Edit scenario" })).toBeInTheDocument();
    expect(screen.queryByText(/Discard unsaved changes/)).not.toBeInTheDocument();
    expect(client.fetchScenario).toHaveBeenLastCalledWith("tight-fleet");
  });

  it("Keep editing leaves the draft, edit mode and the switch alone", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);
    await dirtyDraft(user);
    await switchScenarioTo(user, "tight-fleet");

    await user.click(await screen.findByRole("button", { name: "Keep editing" }));

    expect(screen.queryByText(/Discard unsaved changes/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done editing" })).toBeInTheDocument();
    expect(screen.queryByText(/^A2$/)).not.toBeInTheDocument(); // the removal survived
    expect(client.fetchScenario).not.toHaveBeenCalledWith("tight-fleet");
  });

  it("asks before New scenario… discards a dirty draft", async () => {
    const user = userEvent.setup();
    render(<App />);
    await dirtyDraft(user);

    await user.click(screen.getByRole("button", { name: /scenario:/i }));
    await user.click(screen.getByRole("menuitem", { name: /New scenario/i }));

    expect(await screen.findByText(/Discard unsaved changes/)).toBeInTheDocument();
    expect(client.fetchScenarioTemplate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Discard" }));
    await screen.findByDisplayValue("new-scenario");
  });

  it("asks before New scenario… replaces a pristine unsaved new scenario", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    await openNewScenario(user); // an unsaved, unedited from-scratch draft
    expect(client.fetchScenarioTemplate).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /scenario:/i }));
    await user.click(screen.getByRole("menuitem", { name: /New scenario/i }));

    // isNew alone is "unsaved" — no keystroke needed to earn the guard.
    expect(await screen.findByText(/Discard unsaved changes/)).toBeInTheDocument();
    expect(client.fetchScenarioTemplate).toHaveBeenCalledTimes(1);
  });

  it("asks before a scenario switch abandons a pristine unsaved new scenario", async () => {
    twoScenarios();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await openNewScenario(user);

    await switchScenarioTo(user, "tight-fleet");

    expect(await screen.findByText(/Discard unsaved changes/)).toBeInTheDocument();
    expect(client.fetchScenario).not.toHaveBeenCalledWith("tight-fleet");
  });
});

// Re-entering the scenario editor after "Done editing" used to silently reload the saved
// values, throwing away a draft that was still on screen and still solvable.
describe("edit mode re-entry", () => {
  it("resumes the in-progress draft instead of reverting to the saved scenario", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    const size = screen.getByDisplayValue("5000"); // A1 size
    await user.clear(size);
    await user.type(size, "9999");
    await user.click(screen.getByRole("button", { name: "Done editing" }));

    // The draft is still live and unsaved (the Solve bar says so).
    expect(screen.getByText(/solving your edited scenario/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit scenario" }));
    expect(screen.getByDisplayValue("9999")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("5000")).not.toBeInTheDocument();
  });
});

// The "not saved" strip (Stage 1 / ADR-0036): "Done editing" leaves the draft live and
// unsaved, and the card says so next to the two ways out of that state.
describe("unsaved-draft badge", () => {
  async function editAndLeave(user: User) {
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    const size = screen.getByDisplayValue("5000"); // A1 size
    await user.clear(size);
    await user.type(size, "9999");
    await user.click(screen.getByRole("button", { name: "Done editing" }));
  }

  it("appears once the editor closes over a dirty draft", async () => {
    const user = userEvent.setup();
    await editAndLeave(user);
    expect(screen.getByText(/Edited — not saved/)).toBeInTheDocument();
  });

  it("Revert to saved drops the draft back to the loaded values", async () => {
    const user = userEvent.setup();
    await editAndLeave(user);

    await user.click(screen.getByRole("button", { name: /revert to saved/i }));

    expect(screen.queryByText(/not saved/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Edit scenario" }));
    expect(screen.getByDisplayValue("5000")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("9999")).not.toBeInTheDocument();
  });

  it("Save persists straight from the badge, without re-entering the editor", async () => {
    vi.mocked(client.saveScenario).mockImplementation((_id, s) =>
      Promise.resolve(bundleFor(s)),
    );
    const user = userEvent.setup();
    await editAndLeave(user);

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(client.saveScenario).toHaveBeenCalledWith(
      "test",
      expect.objectContaining({ name: "test" }),
    );
    await waitFor(() =>
      expect(screen.queryByText(/Edited — not saved/)).not.toBeInTheDocument(),
    );
  });

  it("reads 'New scenario — not saved' for a from-scratch draft", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await openNewScenario(user);
    await user.click(screen.getByRole("button", { name: "Done editing" }));

    expect(screen.getByText(/New scenario — not saved/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as…" })).toBeInTheDocument();
  });
});

// Re-picking the scenario already on screen once blanked the view forever: the library
// cleared the bundle but the fetch effect, keyed on an unchanged id, never refilled it.
describe("re-selecting the loaded scenario", () => {
  it("is a no-op, not a permanent 'Loading scenario…'", async () => {
    vi.mocked(client.fetchScenarios).mockResolvedValue([
      entry("well-resourced"),
      entry("tight-fleet"),
    ]);
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    expect(client.fetchScenario).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /scenario:/i }));
    await user.click(screen.getByRole("menuitemradio", { name: /well-resourced/ }));

    expect(screen.queryByText("Loading scenario…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit scenario" })).toBeInTheDocument();
    expect(client.fetchScenario).toHaveBeenCalledTimes(1); // no refetch
  });

  it("still discards the draft when re-picked after confirming 'Discard' (code-review follow-up)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    const size = screen.getByDisplayValue("5000");
    await user.clear(size);
    await user.type(size, "9999");
    await user.click(screen.getByRole("button", { name: "Done editing" }));
    expect(screen.getByText(/Edited — not saved/)).toBeInTheDocument();

    // Re-pick the already-loaded scenario, then confirm the discard.
    await user.click(screen.getByRole("button", { name: /scenario:/i }));
    await user.click(screen.getByRole("menuitemradio", { name: /well-resourced/ }));
    await user.click(await screen.findByRole("button", { name: "Discard" }));

    expect(screen.queryByText(/Edited — not saved/)).not.toBeInTheDocument();
    expect(screen.queryByText("Loading scenario…")).not.toBeInTheDocument();
    expect(client.fetchScenario).toHaveBeenCalledTimes(1); // no refetch / hang
    await user.click(screen.getByRole("button", { name: "Edit scenario" }));
    expect(screen.getByDisplayValue("5000")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("9999")).not.toBeInTheDocument();
  });
});

// Per-weekday availability editing (ADR-0029). The strips themselves stay out of jsdom
// (ADR-0028) — what is checked here is the day picker's wiring and the invariants a user
// can hit: the last day cannot be unticked, and a delete is undoable like any other row.
describe("availability day picker", () => {
  async function openAvailabilityEditor(user: User) {
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));
    await user.click(screen.getByRole("button", { name: "Availability" }));
  }

  /** The chips for A1's one avoid window (the fixture's only window). */
  const chip = (day: string) => screen.getAllByRole("button", { name: day })[0];

  it("starts with every weekday selected and says so", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openAvailabilityEditor(user);

    expect(screen.getByText("same every weekday")).toBeInTheDocument();
    for (const d of ["Monday", "Wednesday", "Sunday"]) {
      expect(chip(d)).toHaveAttribute("aria-pressed", "true");
    }
  });

  it("unticking a weekday drops the window from that day only", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openAvailabilityEditor(user);

    await user.click(chip("Sunday"));

    expect(chip("Sunday")).toHaveAttribute("aria-pressed", "false");
    expect(chip("Monday")).toHaveAttribute("aria-pressed", "true");
    // No longer uniform, so the "same every weekday" note is gone.
    expect(screen.queryByText("same every weekday")).not.toBeInTheDocument();
  });

  it("will not let the last remaining weekday be unticked", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openAvailabilityEditor(user);

    for (const d of ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]) {
      await user.click(chip(d));
    }
    expect(chip("Monday")).toHaveAttribute("aria-pressed", "true");
    expect(chip("Monday")).toBeDisabled();

    await user.click(chip("Monday"));
    expect(chip("Monday")).toHaveAttribute("aria-pressed", "true");
  });

  it("spells out what a wrapping window blocks, on the same weekday", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openAvailabilityEditor(user);

    // A2 is always free, so "+ no-go" gives it the canonical [22, 6] night window.
    await user.click(screen.getAllByRole("button", { name: "+ no-go" })[1]);

    expect(screen.getByText("22:00–24:00 + 00:00–06:00, same day")).toBeInTheDocument();
  });

  it("removing a window is undoable like any other row", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openAvailabilityEditor(user);

    await user.click(screen.getByRole("button", { name: "remove" }));
    expect(await screen.findByText(/Removed avoid window on A1/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByRole("button", { name: "Monday" })).toBeInTheDocument();
  });
});

// Availability on the Gantt. The band geometry is `availabilityBands`' own business
// (availability.test.ts); what is checked here is the two toggles' wiring — ADR-0028
// keeps SVG layout out of jsdom.
describe("availability overlay on the Gantt", () => {
  // The fixture only carries `avoid` windows, so "only avoid bands" would hold vacuously.
  // Give A2 a no-go as well, so each toggle has something of its own to draw.
  beforeEach(() => {
    const s = makeScenario();
    for (const d of Object.keys(s.areas[1].schedule)) s.areas[1].schedule[d].no_go = [[22, 6]];
    vi.mocked(client.fetchScenario).mockResolvedValue(bundleFor(s));
  });

  it("draws no bands until a toggle is switched on", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    expect(screen.getByRole("switch", { name: "show avoid" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "show no-go" })).not.toBeChecked();
    expect(document.querySelectorAll(".gantt-svg .av-avoid")).toHaveLength(0);
    expect(document.querySelectorAll(".gantt-svg .av-no_go")).toHaveLength(0);
  });

  it("show avoid draws the avoid bands, and only those", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    await user.click(screen.getByRole("switch", { name: "show avoid" }));

    // A1 has `avoid [8, 20]`, A2 a `no_go [22, 6]` — only A1's may be drawn.
    expect(document.querySelectorAll(".gantt-svg .av-avoid").length).toBeGreaterThan(0);
    expect(document.querySelectorAll(".gantt-svg .av-no_go")).toHaveLength(0);
  });

  it("show no-go draws the no-go bands, and only those", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    await user.click(screen.getByRole("switch", { name: "show no-go" }));

    expect(document.querySelectorAll(".gantt-svg .av-no_go").length).toBeGreaterThan(0);
    expect(document.querySelectorAll(".gantt-svg .av-avoid")).toHaveLength(0);
  });

  it("the two toggles are independent", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    await user.click(screen.getByRole("switch", { name: "show avoid" }));
    await user.click(screen.getByRole("switch", { name: "show avoid" }));
    expect(document.querySelectorAll(".gantt-svg .av-avoid")).toHaveLength(0);

    await user.click(screen.getByRole("switch", { name: "show no-go" }));
    expect(screen.getByRole("switch", { name: "show no-go" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "show avoid" })).not.toBeChecked();
  });
});

describe("scenario tabs (UI review, 2026-09-24)", () => {
  // Owner report: one of Areas / Fleet / Availability / History was always visible,
  // cluttering the screen even when nobody had asked to see one.
  it("starts with every tab collapsed, and toggles on a repeat click", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    expect(screen.getByRole("button", { name: "Areas" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByText("High")).not.toBeInTheDocument(); // A1's priority word

    await user.click(screen.getByRole("button", { name: "Areas" }));
    expect(screen.getByRole("button", { name: "Areas" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("High")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Areas" }));
    expect(screen.getByRole("button", { name: "Areas" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.queryByText("High")).not.toBeInTheDocument();
  });

  it("Edit scenario opens the Areas tab, even collapsed to start", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));

    expect(screen.getByRole("button", { name: "Areas" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByDisplayValue("A1")).toBeInTheDocument();
  });
});

describe("per-row Undo", () => {
  it("restores a removed area and is sticky through a field edit", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));

    // Areas tab: one "remove" per row (A1, A2)
    await user.click(screen.getAllByRole("button", { name: "remove" })[1]); // A2
    await screen.findByText(/Removed area "A2"/);
    expect(screen.queryByDisplayValue("A2")).not.toBeInTheDocument();

    // a plain field edit does not dismiss the strip
    await user.type(screen.getByDisplayValue("test"), "x");
    expect(screen.getByText(/Removed area "A2"/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.queryByText(/Removed area/)).not.toBeInTheDocument();
    expect(screen.getByDisplayValue("A2")).toBeInTheDocument();
  });
});

describe("plan history", () => {
  /** The number rendered above the "services" stat label — our proxy for "which plan". */
  const shownServices = () =>
    screen.getByText("services").parentElement?.querySelector(".stat-value")?.textContent;

  /** A second, visibly different plan: three services instead of two. */
  function threeTaskResult() {
    const r = makeSolveResult();
    r.schedule!.tasks = [
      ...r.schedule!.tasks,
      { task: 2, area: "A2", mower: "M1", start: 60, end: 69 },
    ];
    return r;
  }

  async function replanReturning(user: User, result: SolveResult) {
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j2", done: true, result });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
  }

  it("has nothing to undo before a replan", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    expect(screen.queryByRole("button", { name: /^Undo$/ })).not.toBeInTheDocument();
  });

  it("brings the previous plan back", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    expect(shownServices()).toBe("2");

    await replanReturning(user, threeTaskResult());
    await screen.findByRole("button", { name: /^Undo$/ });
    expect(shownServices()).toBe("3");

    await user.click(screen.getByRole("button", { name: /^Undo$/ }));
    expect(shownServices()).toBe("2");
  });

  it("stops offering an undo once the stack is spent", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await replanReturning(user, threeTaskResult());

    await user.click(await screen.findByRole("button", { name: /^Undo$/ }));
    expect(screen.queryByRole("button", { name: /^Undo$/ })).not.toBeInTheDocument();
  });

  it("cannot be undone into while a solve is running", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await replanReturning(user, threeTaskResult());
    await screen.findByRole("button", { name: /^Undo$/ });

    await startEndlessSolve(user);
    expect(screen.getByRole("button", { name: /^Undo$/ })).toBeDisabled();
  });

  it("is dropped when the scenario changes underneath it", async () => {
    const user = userEvent.setup();
    vi.mocked(client.fetchScenarios).mockResolvedValue([entry("well-resourced"), entry("tight")]);
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await replanReturning(user, threeTaskResult());
    await screen.findByRole("button", { name: /^Undo$/ });

    await switchScenarioTo(user, "tight");
    expect(screen.queryByRole("button", { name: /^Undo$/ })).not.toBeInTheDocument();
  });
});

describe("moving time horizon (ADR-0042)", () => {
  const MOVE = { name: /^Move forward$/ };

  /** Solve, then stub the roll + the re-solve it triggers, and press Move forward. The
   *  rolled scenario carries one frozen task (see `makeRollResponse`), so the re-solve
   *  result carries the matching agreement report. */
  async function moveForward(user: User, level: PreferenceLevel | null = "top") {
    vi.mocked(client.advanceScenario).mockResolvedValue(makeRollResponse(derivedFor(makeScenario())));
    const replanned = makeSolveResult();
    replanned.schedule!.tasks = [{ task: 1, area: "A1", mower: "M1", start: 50, end: 65 }];
    replanned.preferences = {
      agreement: {
        total: 1,
        time_kept: 1,
        mower_total: 1,
        mower_kept: 1,
        by_origin: { frozen: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 } },
      },
      dropped: [],
      // null level = heuristic mode: it biases the search and leaves the objective
      // alone, so there is no level and no `pref_level` to report (ADR-0043).
      level,
      pref_level: level === null ? null : { top: 6, high: 4, low: 2, avoid: 1, tiebreak: -1 }[level],
    };
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j2", done: true, result: replanned });
    await user.click(screen.getByRole("button", MOVE));
    await waitFor(() => expect(vi.mocked(client.advanceScenario)).toHaveBeenCalled());
  }

  it("rolls the plan on screen forward by the chosen hours", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await moveForward(user);

    const [scenario, tasks, hours] = vi.mocked(client.advanceScenario).mock.calls.at(-1)!;
    expect(scenario.horizon_start_hour).toBe(13);
    expect(hours).toBe(24);
    expect(tasks.map((t) => [t.area, t.start])).toEqual([
      ["A1", 2],
      ["A2", 20],
    ]);
  });

  it("advances by whatever the hours field says — 24 is only the default", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    const field = screen.getByRole("spinbutton", { name: /hours/ });
    expect(field).toHaveValue(24);
    await user.clear(field);
    await user.type(field, "1");
    await moveForward(user);

    expect(vi.mocked(client.advanceScenario).mock.calls.at(-1)![2]).toBe(1);
  });

  it("re-solves the rolled scenario, carrying the still-future plan as preferences", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await moveForward(user);

    const call = vi.mocked(client.startSolve).mock.calls.at(-1)!;
    expect(call[0]).toEqual({ scenario: expect.objectContaining({ horizon_start_hour: 37 }) });
    expect(call[1]?.preferences).toEqual({
      mode: "weak",
      level: "top",
      tasks: [{ area: "A1", start: 50, mower: "M1", origin: "frozen" }],
    });
  });

  it("records the stability setting each roll ran at", async () => {
    // The run log exists to compare rolls against each other. A setting the user can change
    // mid-run makes the quality and kept/carried columns incomparable unless each row says
    // which one it was — so the row reports the level off the *result*, not the selector.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));
    await user.selectOptions(screen.getByRole("combobox", { name: /churned task/i }), "low");
    await moveForward(user, "low");

    const row = await screen.findByRole("row", { name: /\+24 h/ });
    expect(within(row).getByText("low")).toBeInTheDocument();
  });

  it("reports a heuristic roll as 'heuristic', not as 'cold'", async () => {
    // The heuristic mechanism returns `PreferenceReport.level = null` *because it prices
    // nothing* — a different thing from carrying nothing. Rendering `level ?? "cold"` would
    // claim the previous plan was ignored on a roll that carried it in full.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));
    await user.selectOptions(screen.getByRole("combobox", { name: /churned task/i }), "heuristic");
    await moveForward(user, null);

    const row = await screen.findByRole("row", { name: /\+24 h/ });
    expect(within(row).getByText("heuristic")).toBeInTheDocument();
    expect(within(row).queryByText("cold")).not.toBeInTheDocument();
  });

  it("carries no preferences on a roll at the 'cold' stability setting", async () => {
    // UI review 2026-09-10: 'cold' is "no stability" — the roll's re-solve must send the
    // no-payload form, the same one "Re-solve from scratch" uses, not weak@cold.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));
    await user.selectOptions(screen.getByRole("combobox", { name: /churned task/i }), "cold");
    await moveForward(user, null);

    expect(vi.mocked(client.startSolve).mock.calls.at(-1)![1]?.preferences).toEqual({
      tasks: [],
      mode: "off",
      level: "top",
    });
  });

  it("carries the expert-mode stability setting into the roll's re-solve", async () => {
    // One control, both entry points: a roll is a replan, so it must be priced the same
    // way pressing Re-solve would be.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));
    await user.selectOptions(screen.getByRole("combobox", { name: /churned task/i }), "low");
    await moveForward(user);

    expect(vi.mocked(client.startSolve).mock.calls.at(-1)![1]?.preferences?.level).toBe("low");
  });

  it("holds the key on screen, pressed and disabled, until its re-solve settles", async () => {
    // It used to unmount the moment it was pressed — the block vanished and everything
    // below jumped up. A transport key should stay down while it is doing the thing.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    vi.mocked(client.advanceScenario).mockResolvedValue(
      makeRollResponse(derivedFor(makeScenario())),
    );
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    // The roll's re-solve never reports done, so the key stays in its pressed state.
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j2", done: false, result: null });
    await user.click(screen.getByRole("button", MOVE));
    await waitFor(() => expect(vi.mocked(client.advanceScenario)).toHaveBeenCalled());

    const key = await screen.findByRole("button", MOVE);
    expect(key).toBeDisabled();
    expect(key).toHaveAttribute("aria-busy", "true");
    // The step size cannot be changed mid-roll either.
    expect(screen.getByRole("spinbutton", { name: /hours/ })).toBeDisabled();
  });

  it("marks the scenario unsaved — 'now' has moved", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await moveForward(user);

    expect(await screen.findByText(/Edited — not saved/)).toBeInTheDocument();
  });

  it("Undo steps the plan and 'now' back together", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    const services = () =>
      screen.getByText("services").parentElement?.querySelector(".stat-value")?.textContent;
    expect(services()).toBe("2");

    await moveForward(user);
    await screen.findByRole("button", { name: /^Undo$/ });
    expect(services()).toBe("1");

    await user.click(screen.getByRole("button", { name: /^Undo$/ }));
    expect(services()).toBe("2");
    expect(screen.queryByText(/Edited — not saved/)).not.toBeInTheDocument();
  });

  it("is not offered while a solve is running", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    expect(screen.getByRole("button", MOVE)).toBeInTheDocument();

    await startEndlessSolve(user);
    expect(screen.queryByRole("button", MOVE)).not.toBeInTheDocument();
  });

  it("records each roll in the run log, settled once its re-solve returns", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    expect(screen.queryByText(/Run log/)).not.toBeInTheDocument();

    await moveForward(user);
    const item = await screen.findByRole("listitem");
    // "+24 h → Tue 13:00 · 1 services · all 1 carried tasks kept"
    expect(item).toHaveTextContent(/\+24 h/);
    expect(item).toHaveTextContent(/1 services/);
    expect(item).toHaveTextContent(/carried tasks kept/);
  });

  // A roll's re-solve can end without producing a plan — stopped, or failed. The settle
  // effect only checked "not solving, and a schedule exists", and after a stop `result` is
  // still the *pre-roll* plan, so the row was filled in with the previous solve's figures:
  // services, kept/carried, quality and solve time all describing a plan from before the
  // roll. The row must say no plan came back instead.
  it("does not fill a roll's run-log row with the previous solve's figures", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user); // this plan has 1 service and settles fine

    // Roll, but let the re-solve hang, then stop it — no new plan is ever produced.
    vi.mocked(client.advanceScenario).mockResolvedValue(
      makeRollResponse(derivedFor(makeScenario())),
    );
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "roll1" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "roll1", done: false, result: null });
    await user.click(screen.getByRole("button", { name: "Move forward" }));
    await user.click(await screen.findByRole("button", { name: "Stop solving" }));

    const item = await screen.findByRole("listitem");
    expect(item).toHaveTextContent(/\+24 h/);
    expect(item).not.toHaveTextContent(/carried tasks kept/);
    expect(item).not.toHaveTextContent(/1 services/);
    expect(item).toHaveTextContent(/no plan/i);
  });

  it("drops the last run-log row on Undo", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await moveForward(user);
    await screen.findByRole("listitem");

    await user.click(screen.getByRole("button", { name: /^Undo$/ }));
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(screen.queryByText(/Run log/)).not.toBeInTheDocument();
  });

  it("clears the run log when the scenario changes underneath it", async () => {
    const user = userEvent.setup();
    vi.mocked(client.fetchScenarios).mockResolvedValue([entry("well-resourced"), entry("tight")]);
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await moveForward(user);
    await screen.findByRole("listitem");

    await switchScenarioTo(user, "tight");
    // the roll left an unsaved draft — confirm the discard prompt
    await user.click(await screen.findByRole("button", { name: "Discard" }));
    // solve the new scenario: the panel only renders with a plan, so re-solving is what
    // would surface a run log that had not actually been cleared.
    await solveToCompletion(user);
    expect(screen.queryByText(/Run log/)).not.toBeInTheDocument();
  });
});

describe("replan mode", () => {
  const prefsOf = () => vi.mocked(client.startSolve).mock.calls.at(-1)?.[1]?.preferences;
  /** What actually reaches the wire: `startSolve` drops a `mode: "off"` payload entirely
   *  (its own test pins that), so "off" and "absent" are the same request. */
  const sendsNothing = () => (prefsOf()?.mode ?? "off") === "off";

  it("sends no preferences on the first solve — there is no plan to keep", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    expect(sendsNothing()).toBe(true);
  });

  it("defaults to keeping the whole plan on a replan", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(prefsOf()).toEqual({
      mode: "weak",
      level: "top",
      tasks: [
        { area: "A1", start: 2, mower: "M1", origin: "frozen" },
        { area: "A2", start: 20, mower: "M1", origin: "frozen" },
      ],
    });
  });

  // --- the stability setting (Iteration 3, expert mode only) ---
  //
  // How much one churned task is worth against the service objective, sent as the
  // preference weak constraints' priority level. Expert-only per the frontend's
  // progressive-disclosure rule: the default view gets the default, not a dial.

  const STABILITY = { name: /churned task/i };
  const enableExpert = (user: User) =>
    user.click(screen.getByRole("switch", { name: /expert mode/i }));

  it("hides the stability setting from the default view", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    expect(screen.queryByRole("combobox", STABILITY)).not.toBeInTheDocument();
    await enableExpert(user);
    expect(screen.getByRole("combobox", STABILITY)).toBeInTheDocument();
  });

  it("prices a replan at the selected setting", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await enableExpert(user);

    await user.selectOptions(screen.getByRole("combobox", STABILITY), "high");
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(prefsOf()?.level).toBe("high");
    // Only the price moves — the payload is the same plan either way.
    expect(prefsOf()?.tasks).toHaveLength(2);
  });

  it("sends the heuristic mechanism, not a price, for the heuristic setting", async () => {
    // ADR-0034 decision 4 kept `mode: "heuristic"` for exactly the rolling use case, but
    // ADR-0043's selector only varied `level` — a price *within* weak mode — so the one
    // arm earmarked for rolling was reachable from the API and not from the UI.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await enableExpert(user);

    await user.selectOptions(screen.getByRole("combobox", STABILITY), "heuristic");
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(prefsOf()?.mode).toBe("heuristic");
    // Same payload — only the mechanism carrying it changes.
    expect(prefsOf()?.tasks).toHaveLength(2);
  });

  it("warns that the heuristic setting cannot promise the edits survive", async () => {
    // The four weak settings are prices and always keep what they are asked to; this one
    // biases the search and measured 0.919 agreement (ADR-0034). The selector feeds
    // Re-solve as well as Move forward, so the caveat has to be on screen.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await enableExpert(user);
    await user.selectOptions(screen.getByRole("combobox", STABILITY), "heuristic");

    expect(screen.getByText(/your own edits can be dropped/i)).toBeInTheDocument();
  });

  it("falls back to the default price when expert mode is switched off", async () => {
    // The default view must never inherit a dial the user can no longer see.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await enableExpert(user);
    await user.selectOptions(screen.getByRole("combobox", STABILITY), "avoid");
    await enableExpert(user); // off again

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    expect(prefsOf()?.level).toBe("top");
  });

  it("'Re-solve from scratch' sends no payload at all (ADR-0038)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    await user.click(screen.getByRole("button", { name: "Re-solve from scratch" }));

    expect(sendsNothing()).toBe(true);
  });

  it("reports what survived, without promising anything", async () => {
    // The "N of M preferences kept overall" clause is expert-only (owner report,
    // 2026-09-24) -- this case is about what it says when shown, not where it shows.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));
    await solveToCompletion(user);

    const kept = makeSolveResult();
    kept.preferences = {
      agreement: {
        total: 2,
        time_kept: 1,
        mower_total: 2,
        mower_kept: 1,
        by_origin: { frozen: { total: 2, time_kept: 1, mower_total: 2, mower_kept: 1 } },
      },
      dropped: [],
      level: "top",
      pref_level: 6,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: kept });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    // This case used to expect "· 1 kept the hour but changed mower" — about the service
    // that had in fact *missed* its hour (time_kept 1 of 2, and mower_kept <= time_kept).
    // Mower churn among untouched services is no longer reported at all (ADR-0051).
    expect(await screen.findByText("1 of 2 preferences kept overall")).toBeInTheDocument();
    expect(screen.queryByText(/different mower|changed mower/)).not.toBeInTheDocument();
  });

  it("hides the overall preference count outside expert mode", async () => {
    // Owner report, 2026-09-24: after a plain Move forward (no edits of the user's own)
    // the default view showed "N of M preferences kept overall" with nothing to explain
    // where the number came from -- confusing for a user who set no preferences at all.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    const kept = makeSolveResult();
    kept.preferences = {
      agreement: {
        total: 2,
        time_kept: 1,
        mower_total: 2,
        mower_kept: 1,
        by_origin: { frozen: { total: 2, time_kept: 1, mower_total: 2, mower_kept: 1 } },
      },
      dropped: [],
      level: "top",
      pref_level: 6,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: kept });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(screen.queryByText(/preferences kept overall/)).not.toBeInTheDocument();
  });

  it("does not claim a plain optimum when the optimum was constrained by the edits", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    // A cold solve's "yes" is the unqualified claim.
    expect(screen.getByText("yes")).toBeInTheDocument();

    const kept = makeSolveResult();
    kept.preferences = {
      agreement: {
        total: 2,
        time_kept: 2,
        mower_total: 2,
        mower_kept: 2,
        by_origin: {
          frozen: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 },
          edited: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 },
        },
      },
      dropped: [],
      level: "top",
      pref_level: 6,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: kept });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(await screen.findByText(/best that keeps your edits/i)).toBeInTheDocument();
    expect(screen.queryByText("yes")).not.toBeInTheDocument();
  });

  // A Move forward carries the plan as implicit preferences and no edits at all, so
  // "keeps your edits" names something the user never did. The optimum is still
  // constrained — by the carried plan — so neither a plain "yes" nor the edits wording
  // is right.
  it("does not mention edits when the payload carried none", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    const carried = makeSolveResult();
    carried.preferences = {
      agreement: {
        total: 2,
        time_kept: 2,
        mower_total: 2,
        mower_kept: 2,
        by_origin: { frozen: { total: 2, time_kept: 2, mower_total: 2, mower_kept: 2 } },
      },
      dropped: [],
      level: "top",
      pref_level: 6,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: carried });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(await screen.findByText(/best that keeps this plan/i)).toBeInTheDocument();
    expect(screen.queryByText(/your edits/i)).not.toBeInTheDocument();
  });

  // In heuristic mode the directives bias the search and leave the objective alone
  // (ADR-0032), so a proven optimum is the plain optimum — and "Re-solve from scratch may
  // score better" is false.
  it("claims a plain optimum in heuristic mode, where the objective is untouched", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    const heur = makeSolveResult();
    heur.preferences = {
      agreement: {
        total: 1,
        time_kept: 1,
        mower_total: 1,
        mower_kept: 1,
        by_origin: { edited: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 } },
      },
      dropped: [],
      level: null,
      pref_level: null,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: heur });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(await screen.findByText("yes")).toBeInTheDocument();
    expect(screen.queryByText(/best that keeps/i)).not.toBeInTheDocument();
  });

  it("surfaces a preference the solver could not even express", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    const dropped = makeSolveResult();
    dropped.preferences = {
      agreement: {
        total: 1,
        time_kept: 1,
        mower_total: 1,
        mower_kept: 1,
        by_origin: { frozen: { total: 1, time_kept: 1, mower_total: 1, mower_kept: 1 } },
      },
      dropped: [{ area: "A1", start: 2, mower: "M1", half: "mower", reason: "M1 cannot service A1" }],
      level: "top",
      pref_level: 6,
    };
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: dropped });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(await screen.findByText(/M1 cannot service A1/)).toBeInTheDocument();
  });
});

describe("schedule editing", () => {
  const TABLE = /^table$/;

  async function openTableEditor(user: User) {
    await user.click(screen.getByRole("button", { name: TABLE }));
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
  }

  /** The add-service picker is a collapsed disclosure (UI review 2026-09-10) — open it
   *  before touching the area / hour / "+ add" controls inside. */
  async function openAddPanel(user: User) {
    await user.click(screen.getByRole("button", { name: /^\+ Add a service$/ }));
  }

  async function solvedTable(user: User) {
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await openTableEditor(user);
  }

  const prefTasks = () =>
    vi.mocked(client.startSolve).mock.calls.at(-1)?.[1]?.preferences?.tasks ?? [];

  // Editing the schedule is modal (ADR-0053). While it is on, every other action that
  // would consume or discard the plan is unavailable, and Re-solve is the way out. This
  // closes four defects at once: a release or an un-re-solved edit reaching Move forward,
  // a client-side preview duration being written into the service history as though it
  // had been measured, and edits being thrown away by a poll during a solve.
  it("disables the other plan actions while the schedule is being edited", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    expect(screen.getByRole("button", { name: "Move forward" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit scenario" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^scenario:/i })).toBeDisabled();
    // The way out stays available.
    expect(screen.getByRole("button", { name: SOLVE_BUTTON })).toBeEnabled();
  });

  it("shows the plan-discard prompt next to the toggle that raised it, not at the top of the page", async () => {
    // Owner report, 2026-09-24: on a tall schedule the toggle sits below the fold, so a
    // "Discard your edits to the plan?" prompt rendered at the top of the page (next to
    // the header) was never seen.
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));

    const prompt = await screen.findByText(/Discard your edits to the plan\?/);
    const toggle = screen.getByRole("switch", { name: /edit schedule/i });
    expect(prompt.closest(".app-card")).toBe(toggle.closest(".app-card"));
  });

  it("re-solving finishes editing and carries the edits with it", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    // The edit was sent...
    expect(prefTasks()).toContainEqual(
      expect.objectContaining({ area: "A1", start: 40, origin: "edited" }),
    );
    // ...and edit mode is over, so nothing can be typed while the solve runs.
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /edit schedule/i })).not.toBeChecked(),
    );
  });

  // Since Re-solve is the commit path (ADR-0053), the toggle is the only way to throw plan
  // edits away — so it asks first, the same two-step idiom as discarding a scenario draft.
  it("asks before discarding plan edits, and keeps them if you decline", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    expect(screen.getByText(/discard your edits to the plan/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /keep editing/i }));
    expect(screen.getAllByLabelText(/start hour for A1/i)[0]).toHaveValue(40);

    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    await user.click(screen.getByRole("button", { name: /^discard$/i }));
    expect(screen.getByRole("switch", { name: /edit schedule/i })).not.toBeChecked();
  });

  it("closes without asking when nothing was edited", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    expect(screen.queryByText(/discard your edits to the plan/i)).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /edit schedule/i })).not.toBeChecked();
  });

  it("has no schedule editor until there is a schedule", async () => {
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    expect(screen.queryByRole("switch", { name: /edit schedule/i })).not.toBeInTheDocument();
  });

  it("moves a task and carries the new hour into the replan", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    expect(prefTasks()).toContainEqual({
      area: "A1",
      start: 40,
      mower: "M1",
      origin: "edited",
    });
  });

  it("Undo after a re-solve restores an un-re-solved edit, not the last solver output", async () => {
    // UI review 2026-09-10: a Re-solve that carried a manual edit must be undoable back to
    // that edit. Before the working-copy snapshot, Undo reverted to the plan the solver
    // last returned and the edit was silently lost.
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    const resolved = makeSolveResult();
    resolved.schedule!.tasks = [{ task: 1, area: "A1", mower: "M1", start: 8, end: 23 }];
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j2", done: true, result: resolved });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await user.click(await screen.findByRole("button", { name: /^Undo$/ }));

    // Re-solve closed the editor (ADR-0053), so the hour is on screen as a label rather
    // than a field; re-open to confirm the working copy itself carries the restored edit.
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    expect(screen.getAllByLabelText(/start hour for A1/i)[0]).toHaveValue(40);
    expect(screen.getByText(/issues are hidden while the plan is edited/i)).toBeInTheDocument();
  });

  it("keeps Move forward unreachable after Undo restores an un-re-solved edit", async () => {
    // Owner report, 2026-09-24: solve -> edit -> re-solve -> Undo left the schedule editor
    // closed (ADR-0053 only reopens it via the toggle) but the restored working copy still
    // carried the edit, so `editingSchedule` alone no longer explained whether the plan on
    // screen was safe to hand to a roll. Move forward must stay blocked until that edit is
    // taken care of by a Re-solve — never bypassable by going through Undo.
    const user = userEvent.setup();
    await solvedTable(user);

    const start = screen.getAllByLabelText(/start hour for A1/i)[0];
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    const resolved = makeSolveResult();
    resolved.schedule!.tasks = [{ task: 1, area: "A1", mower: "M1", start: 8, end: 23 }];
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j2", done: true, result: resolved });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await user.click(await screen.findByRole("button", { name: /^Undo$/ }));

    expect(screen.getByRole("switch", { name: /edit schedule/i })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Move forward" })).toBeDisabled();
  });

  it("releases a task out of the payload and says what that means", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]);
    expect(await screen.findByText(/may still move or re-add it/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    expect(prefTasks().map((t) => t.area)).toEqual(["A2"]);
  });

  it("'release all' flips every row's verb from 'release' to 'keep'", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    // The plan is kept by default, so every row offers to let one go.
    expect(screen.getAllByRole("button", { name: /^release$/ })).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "release all" }));

    expect(screen.queryByRole("button", { name: /^release$/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^keep$/ })).toHaveLength(2);
  });

  it("'release all' then re-keeping one task sends only that task", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    await user.click(screen.getByRole("button", { name: "release all" }));

    await user.click(screen.getAllByRole("button", { name: /^keep$/ })[0]);
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(prefTasks()).toEqual([{ area: "A1", start: 2, mower: "M1", origin: "frozen" }]);
  });

  it("'keep all' puts every released task back into the payload", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    await user.click(screen.getByRole("button", { name: "release all" }));
    await user.click(screen.getByRole("button", { name: "keep all" }));

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    expect(prefTasks()).toHaveLength(2);
  });

  it("the release-all control is hidden until the schedule editor is on", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: TABLE }));

    expect(screen.queryByRole("button", { name: /^(release|keep) all$/ })).not.toBeInTheDocument();
  });

  it("clears the release note once the replan it described has run", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]);
    await screen.findByText(/may still move or re-add it/i);

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(screen.queryByText(/may still move or re-add it/i)).not.toBeInTheDocument();
  });

  it("'drop a service' lowers the area's cap and leaves an unsaved draft (ADR-0039)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    // A plan with two A1 services, so dropping A1 to one is a valid cap (model needs ≥ 1).
    const twoA1 = makeSolveResult();
    twoA1.schedule!.tasks = [
      { task: 1, area: "A1", mower: "M1", start: 2, end: 17 },
      { task: 2, area: "A1", mower: "M1", start: 90, end: 105 },
      { task: 1, area: "A2", mower: "M1", start: 20, end: 29 },
    ];
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j1" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: twoA1 });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await screen.findByText("services");
    await user.click(screen.getByRole("button", { name: TABLE }));
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));

    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]); // release an A1 task
    await user.click(await screen.findByRole("button", { name: /drop a1 to 1 service/i }));

    expect(screen.getByText(/Edited — not saved/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    const target = vi.mocked(client.startSolve).mock.calls.at(-1)?.[0] as { scenario: Scenario };
    expect(target.scenario.areas.find((a) => a.name === "A1")?.max_services).toBe(1);
  });

  it("does not offer a drop when the area has only one service", async () => {
    const user = userEvent.setup();
    await solvedTable(user); // makeSolveResult: one service per area
    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]);

    expect(await screen.findByText(/out of the next re-solve/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /drop a1/i })).not.toBeInTheDocument();
  });

  it("adds a service — records the slot and forces the area's count up (ADR-0039)", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    await openAddPanel(user);

    await user.selectOptions(screen.getByLabelText(/area for the new service/i), "A2");
    const hour = screen.getByLabelText(/start hour for the new service/i);
    await user.clear(hour);
    await user.type(hour, "100");
    await user.click(screen.getByRole("button", { name: /^\+ add$/ }));

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    expect(prefTasks()).toContainEqual({ area: "A2", start: 100, mower: "M1", origin: "added" });
    const target = vi.mocked(client.startSolve).mock.calls.at(-1)?.[0] as { scenario: Scenario };
    expect(target.scenario.areas.find((a) => a.name === "A2")?.min_services).toBe(2);
  });

  it("only offers the add panel while editing the schedule", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: TABLE }));

    expect(screen.queryByRole("button", { name: /^\+ Add a service$/ })).not.toBeInTheDocument();
  });

  it("keeps the add picker collapsed until asked for", async () => {
    const user = userEvent.setup();
    await solvedTable(user);

    expect(screen.queryByLabelText(/area for the new service/i)).not.toBeInTheDocument();
    await openAddPanel(user);
    expect(screen.getByLabelText(/area for the new service/i)).toBeInTheDocument();
  });

  // Originally this froze the add button while a solve ran. Since ADR-0053 a solve closes
  // the editor outright, so the panel is not on screen at all — a stronger guarantee, and
  // the reason edits can no longer be lost to a poll mid-solve.
  it("takes the add panel off screen while a solve is in flight", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await openTableEditor(user);
    await openAddPanel(user);
    expect(screen.getByRole("button", { name: /^\+ add$/ })).toBeEnabled();

    await startEndlessSolve(user);
    expect(screen.queryByRole("button", { name: /^\+ add$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /edit schedule/i })).not.toBeChecked();
  });

  it("adding after releasing in the same area is a swap, not a forced +1 (code-review follow-up)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });

    const twoA1 = makeSolveResult();
    twoA1.schedule!.tasks = [
      { task: 1, area: "A1", mower: "M1", start: 2, end: 17 },
      { task: 2, area: "A1", mower: "M1", start: 90, end: 105 },
      { task: 1, area: "A2", mower: "M1", start: 20, end: 29 },
    ];
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j1" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j1", done: true, result: twoA1 });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await screen.findByText("services");
    await openTableEditor(user);

    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]); // A1: 2 → 1 kept
    await openAddPanel(user);
    await user.selectOptions(screen.getByLabelText(/area for the new service/i), "A1");
    await user.click(screen.getByRole("button", { name: /^\+ add$/ }));
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    const target = vi.mocked(client.startSolve).mock.calls.at(-1)?.[0] as { scenario: Scenario };
    // kept (1) + 1 = 2, NOT total (2) + 1 = 3.
    expect(target.scenario.areas.find((a) => a.name === "A1")?.min_services).toBe(2);
  });

  // The line used to say the issues on screen were the *last solved plan's*. They are not
  // shown at all once the working copy is edited — `violationsStale` suppresses them — and
  // a bare release counts as an edit, so it fired with nothing on screen to be wrong about.
  it("says issues are hidden once the plan is edited, and a release counts", async () => {
    const user = userEvent.setup();
    await solvedTable(user);
    expect(screen.queryByText(/issues are hidden/i)).not.toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]);
    expect(await screen.findByText(/issues are hidden while the plan is edited/i)).toBeInTheDocument();
    expect(screen.queryByText(/from the last solved plan/i)).not.toBeInTheDocument();
  });
});

describe("schedule editing on the chart", () => {
  const bars = () => document.querySelectorAll(".gantt-svg .gantt-bar");

  async function solvedChart(user: User) {
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
  }

  it("marks a released bar apart from the rest", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    expect(document.querySelectorAll(".gantt-bar-released")).toHaveLength(0);

    await user.click(bars()[0]);
    await user.click(await screen.findByRole("button", { name: /release this service/i }));

    expect(document.querySelectorAll(".gantt-bar-released")).toHaveLength(1);
  });

  it("marks an edited bar apart from the rest", async () => {
    const user = userEvent.setup();
    await solvedChart(user);

    await user.click(bars()[0]);
    const start = await screen.findByLabelText(/start hour for A1/i);
    await user.clear(start);
    await user.type(start, "40");
    await user.tab();

    expect(document.querySelectorAll(".gantt-bar-edited")).toHaveLength(1);
  });

  it("does not open the task editor when edit mode is off", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);

    await user.click(bars()[0]);
    expect(screen.queryByRole("dialog", { name: /edit task/i })).not.toBeInTheDocument();
  });

  it("closes the task editor on Escape", async () => {
    const user = userEvent.setup();
    await solvedChart(user);

    await user.click(bars()[0]);
    expect(await screen.findByRole("dialog", { name: /edit task/i })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: /edit task/i })).not.toBeInTheDocument();
  });

  // Drag-to-move (ADR-0045). jsdom's getBoundingClientRect is all zeros, so the SVG's
  // rendered width has to be stubbed for the pixel→hour maths to run; 1180 == the viewBox
  // width, which makes 1 CSS px == 1 user unit. The drawn domain is [-40, 168] — the
  // fixture's history reaches back to hour -40 and that feeds `xMin` — so 1008 plot units
  // span 208 h and 60 px ≈ 12.4 h, which snaps to +12.
  const RECT_1180 = { width: 1180, height: 0, x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0 };
  function stubGanttWidth() {
    const svg = document.querySelector(".gantt-svg");
    if (!svg) throw new Error("no gantt svg");
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue(RECT_1180 as DOMRect);
  }

  it("dragging a bar to a new hour marks it edited and carries the hour into the re-solve", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    stubGanttWidth();

    // A1's task starts at hour 2; +60 px ≈ +12 h ⇒ hour 14 (see the note above).
    fireEvent.pointerDown(bars()[0], { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 160 });
    fireEvent.pointerUp(window);

    expect(document.querySelectorAll(".gantt-bar-edited")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    const tasks = vi.mocked(client.startSolve).mock.calls.at(-1)?.[1]?.preferences?.tasks ?? [];
    expect(tasks).toContainEqual({ area: "A1", start: 14, mower: "M1", origin: "edited" });
  });

  it("a sub-threshold twitch still opens the popover and edits nothing", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    stubGanttWidth();

    fireEvent.pointerDown(bars()[0], { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 102 }); // 2 px < DRAG_THRESHOLD_PX
    fireEvent.pointerUp(window);
    fireEvent.click(bars()[0]);

    expect(await screen.findByRole("dialog", { name: /edit task/i })).toBeInTheDocument();
    expect(document.querySelectorAll(".gantt-bar-edited")).toHaveLength(0);
  });

  it("a click on another bar after a drag still opens the popover", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    stubGanttWidth();

    fireEvent.pointerDown(bars()[0], { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 160 });
    fireEvent.pointerUp(window);
    // The synthetic click that a real drag emits never arrives here; the suppression
    // must clear itself on its own (a macrotask) so the next real click is not eaten.
    await new Promise((r) => setTimeout(r, 0));

    fireEvent.click(bars()[1]);
    expect(await screen.findByRole("dialog", { name: /edit task/i })).toBeInTheDocument();
  });

  it("a drag that returns to the original hour edits nothing (no-op guard)", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    stubGanttWidth();

    fireEvent.pointerDown(bars()[0], { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 160 });
    fireEvent.pointerMove(window, { clientX: 100 });
    fireEvent.pointerUp(window);

    expect(document.querySelectorAll(".gantt-bar-edited")).toHaveLength(0);
  });

  it("Escape mid-drag returns the bar to where it was", async () => {
    const user = userEvent.setup();
    await solvedChart(user);
    stubGanttWidth();

    fireEvent.pointerDown(bars()[0], { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 160 });
    expect(document.querySelectorAll(".gantt-bar-dragging")).toHaveLength(1);

    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(window);

    expect(document.querySelectorAll(".gantt-bar-dragging")).toHaveLength(0);
    expect(document.querySelectorAll(".gantt-bar-edited")).toHaveLength(0);
  });
});

// The model stores area priority as 1–3 (1 = highest, ADR-0012); the numbers mean nothing
// to a greenkeeper, so the UI shows High / Medium / Low (ADR-0012 is unchanged).
describe("priority labels", () => {
  it("the Areas tab reads priority as words, not numbers", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    // The tabs are collapsed by default (UI review, 2026-09-24).
    await user.click(screen.getByRole("button", { name: "Areas" }));

    // fixture: A1 priority 1, A2 priority 2.
    expect(screen.getByText("High")).toBeInTheDocument();
    expect(screen.getByText("Medium")).toBeInTheDocument();
    expect(screen.queryByText("Low")).not.toBeInTheDocument(); // no priority-3 area
  });

  it("the priority editor offers High / Medium / Low", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Edit scenario" }));

    const sel = screen.getByRole("combobox", { name: "priority for A1" });
    expect(sel).toHaveValue("1");
    expect(within(sel).getByRole("option", { name: "High" })).toBeInTheDocument();
    expect(within(sel).getByRole("option", { name: "Low" })).toBeInTheDocument();
  });
});

describe("explainability (Iteration 6)", () => {
  const TABLE = /^table$/;

  /** A re-solve whose result carries an added preference the schedule does not realise
   *  (its own agreement report says so) — the shape `droppedEdits`/`ExplanationPanel`
   *  need something to explain. */
  function resultWithDroppedAdd(): SolveResult {
    const r = makeSolveResult();
    r.preferences = {
      agreement: {
        total: 3,
        time_kept: 2,
        mower_total: 3,
        mower_kept: 2,
        by_origin: {
          frozen: { total: 2, time_kept: 2, mower_total: 2, mower_kept: 2 },
          added: { total: 1, time_kept: 0, mower_total: 1, mower_kept: 0 },
        },
      },
      dropped: [],
      level: "top",
      pref_level: 6,
    };
    return r;
  }

  /** Add a service at A2/hour 100, then re-solve with a result that does not realise it
   *  (`resultWithDroppedAdd`) — one dropped edit for the panel to explain. */
  async function solveWithOneDroppedEdit(user: User) {
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: TABLE }));
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    await user.click(screen.getByRole("button", { name: /^\+ Add a service$/ }));
    await user.selectOptions(screen.getByLabelText(/area for the new service/i), "A2");
    const hour = screen.getByLabelText(/start hour for the new service/i);
    await user.clear(hour);
    await user.type(hour, "100");
    await user.click(screen.getByRole("button", { name: /^\+ add$/ }));

    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({
      job_id: "j2",
      done: true,
      result: resultWithDroppedAdd(),
    });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await screen.findByRole("button", { name: /Why weren't 1 edit kept\?/i });
  }

  it("keeps explaining the schedule on screen, not an in-flight request, while a re-solve runs", async () => {
    const user = userEvent.setup();
    await solveWithOneDroppedEdit(user); // "Why weren't 1 edit kept?" — the added A2@100

    // Release that same edit, then start another re-solve that never reports `done`. The
    // outgoing request no longer carries any edit at all — if `lastRequest` were committed
    // the instant this solve started (rather than alongside the next `result` that
    // actually lands), the trigger would recompute against the *old* schedule and vanish
    // mid-solve, before anything has actually changed on screen.
    // The solve in the setup closed the editor (ADR-0053); re-open it to release.
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));
    const releaseButtons = screen.getAllByRole("button", { name: /^release$/ });
    await user.click(releaseButtons[releaseButtons.length - 1]); // the added task, last row

    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j3" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j3", done: false, result: null });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
    await screen.findByRole("button", { name: "Stop solving" });

    expect(
      screen.getByRole("button", { name: /Why weren't 1 edit kept\?/i }),
    ).toBeInTheDocument();
  });

  it("clamps an out-of-range expert-mode explain budget rather than sending it to the backend", async () => {
    const user = userEvent.setup();
    await solveWithOneDroppedEdit(user);
    await user.click(screen.getByRole("switch", { name: /expert mode/i }));

    const budgetInput = screen.getByLabelText(/explain budget/i);
    await user.clear(budgetInput);
    await user.type(budgetInput, "90");
    expect(budgetInput).toHaveValue(60); // ExplainRequest.budget_s: le=60

    await user.clear(budgetInput);
    await user.type(budgetInput, "0");
    expect(budgetInput).toHaveValue(1); // ExplainRequest.budget_s: gt=0
  });

  const explainReport = {
    edits: [
      {
        area: "A2",
        start: 100,
        mower: "M1",
        outcome: "conflicts_with" as const,
        detail: "conflicts with 1 kept task(s)",
        conflicts: [{ area: "A2", start: 20, mower: "M1" }],
        minimal: true,
        solve_time_s: 0.02,
      },
    ],
    reinstated: [],
    ripple: [],
    budget_s: 8,
    budget_exhausted: false,
  };

  it("explains using the submitted preferences, not the rebuilt working copy", async () => {
    const user = userEvent.setup();
    await solveWithOneDroppedEdit(user);

    // The working copy on screen right now was rebuilt from `resultWithDroppedAdd`'s
    // *schedule* (two untouched tasks, both `origin: "frozen"`) — if explain read that
    // instead of the request that was actually sent, there would be nothing to explain.
    // The button already having appeared (in the helper above) is half this test; the
    // other half is confirming the outgoing call carries the added preference.
    vi.mocked(client.explainSolve).mockResolvedValue(explainReport);
    await user.click(screen.getByRole("button", { name: /Why weren't 1 edit kept\?/i }));

    expect(await screen.findByText(/conflicts with a kept task/i)).toBeInTheDocument();
    const call = vi.mocked(client.explainSolve).mock.calls.at(-1)!;
    const [, preferences, schedule] = call;
    expect(preferences.tasks).toContainEqual({
      area: "A2",
      start: 100,
      mower: "M1",
      origin: "added",
    });
    expect(schedule).toEqual(resultWithDroppedAdd().schedule);
  });

  it("sends the released count alongside the payload", async () => {
    // Owner report, pre-workshop review 2026-09-24: the reinstated explanation used to
    // compare only against the payload, so a released service the minimum brought back
    // always read as "you asked for fewer" -- the opposite of what releasing means. The
    // fix needs the frontend to tell the backend what was released; this checks it does.
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Edit scenario" });
    await solveToCompletion(user);
    await user.click(screen.getByRole("button", { name: TABLE }));
    await user.click(screen.getByRole("switch", { name: /edit schedule/i }));

    // Release A1 (frozen -- not itself an edit) and edit A2's start (a genuine edit, so
    // there is something for the "Why?" button to explain).
    await user.click(screen.getAllByRole("button", { name: /^release$/ })[0]); // A1
    const start = screen.getAllByLabelText(/start hour for A2/i)[0];
    await user.clear(start);
    await user.type(start, "50");
    await user.tab();

    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j2" });
    vi.mocked(client.pollSolve).mockResolvedValue({
      job_id: "j2",
      done: true,
      result: resultWithDroppedAdd(), // schedule unchanged: A1@2, A2@20 -- A2's edit is dropped
    });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));

    vi.mocked(client.explainSolve).mockResolvedValue(explainReport);
    await user.click(await screen.findByRole("button", { name: /Why weren't 1 edit kept\?/i }));

    await screen.findByText(/conflicts with a kept task/i);
    const call = vi.mocked(client.explainSolve).mock.calls.at(-1)!;
    expect(call[4]).toEqual({ A1: 1 });
  });

  it("clears a previous explanation once a new solve replaces the plan", async () => {
    const user = userEvent.setup();
    await solveWithOneDroppedEdit(user);
    vi.mocked(client.explainSolve).mockResolvedValue(explainReport);
    await user.click(screen.getByRole("button", { name: /Why weren't 1 edit kept\?/i }));
    await screen.findByText(/conflicts with a kept task/i);

    // A second solve, `preferences` non-null either way (the added task did not survive
    // into the rebuilt working copy, since it was never realised — so this one reports no
    // dropped edits of its own). The point is not what this plan has to explain; it is
    // that the *previous* plan's answer must not survive onto it regardless.
    await replanReturningExplain(user, resultWithDroppedAdd());

    expect(screen.queryByText(/conflicts with a kept task/i)).not.toBeInTheDocument();
  });

  // The synchronous reset above only covers a report that had already arrived. "Why?" is
  // deliberately clickable while a solve runs, and every poll installs a new incumbent, so
  // an explain can resolve *after* the plan it was computed for is gone — and then be
  // rendered beside a different plan, and suppress that plan's own "not applied" rows.
  // `useSolveJob` re-checks its job identity after every await; this had no equivalent.
  it("drops an explanation that arrives after the plan it was computed for", async () => {
    const user = userEvent.setup();
    await solveWithOneDroppedEdit(user);

    let release!: (r: ExplanationReport) => void;
    vi.mocked(client.explainSolve).mockReturnValue(
      new Promise<ExplanationReport>((resolve) => {
        release = resolve;
      }),
    );
    await user.click(screen.getByRole("button", { name: /Why weren't 1 edit kept\?/i }));

    // The plan changes while the explain is still in flight, then the answer lands.
    // `act` flushes the resolution so the late `setReport` really is applied before the
    // assertion — a bare `waitFor` on an absence passes on its first tick and proves
    // nothing.
    await replanReturningExplain(user, resultWithDroppedAdd());
    await act(async () => {
      release(explainReport);
    });

    expect(screen.queryByText(/conflicts with a kept task/i)).not.toBeInTheDocument();
  });

  async function replanReturningExplain(user: User, result: SolveResult) {
    vi.mocked(client.startSolve).mockResolvedValue({ job_id: "j3" });
    vi.mocked(client.pollSolve).mockResolvedValue({ job_id: "j3", done: true, result });
    await user.click(screen.getByRole("button", { name: SOLVE_BUTTON }));
  }
});
