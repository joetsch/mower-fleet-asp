// Expert-mode helpers: turn the backend's SolverInfo into human-readable text.
//
// The prose gloss for each argument string is HARDCODED on purpose (ADR-0007,
// ADR-0011) — we describe the configurations we actually run, we do not try to
// derive an explanation from the flags at runtime.

import type { PreferenceLevel, SolverInfo } from "../types";

const ARG_PROSE: Record<string, string> = {
  "-t4 --configuration=many":
    "4-thread portfolio search — proves the optimum in seconds on good instances; " +
    "may return a different equally-optimal schedule between runs.",
  "-t1 --configuration=jumpy":
    "Single-threaded — fully reproducible, slower to prove the optimum. Uses the " +
    "'jumpy' configuration, not clingo's fragile default (ADR-0041).",
  "-t1": "Single-threaded, clingo's default configuration — reproducible but can stall on this encoding.",
};

/** One-line configuration summary, e.g. "clingcon · 4 threads (many portfolio) · up to 20 s". */
export function solverModeLabel(s: SolverInfo): string {
  const threads = `${s.threads} thread${s.threads === 1 ? "" : "s"}`;
  const portfolio = s.config ? ` (${s.config} portfolio)` : "";
  return `${s.name} · ${threads}${portfolio} · up to ${s.time_limit_s} s`;
}

/** Hardcoded plain-language gloss for the argument string, or null if unrecognised.
 *
 * A custom command line (ADR-0023) essentially never matches one of these exact strings,
 * so it naturally falls back to null — the prose gloss disappears once a user overrides
 * the default portfolio, deliberately: expert mode's audience can read the flags
 * themselves once they've chosen to change them.
 */
export function solverModeProse(s: SolverInfo): string | null {
  return ARG_PROSE[s.args.join(" ")] ?? null;
}

const DEFAULT_PORTFOLIO_ARGS = ["-t4", "--configuration=many"];

/** Split an expert-mode clingo command-line string into argv tokens (ADR-0023).
 * Whitespace-separated, no quoting support — the flags this is meant for don't need
 * embedded spaces. Collapses repeated whitespace and trims, so "" -> [].
 */
export function parseClingoArgs(raw: string): string[] {
  return raw.trim().split(/\s+/).filter(Boolean);
}

/** Best-effort thread count read out of a raw arg list, mirroring
 * `service._parse_threads` — clingo itself defaults to 1 thread when `-tN` is absent. */
function parseThreads(args: string[]): number {
  for (const a of args) {
    const m = /^-t(\d+)$/.exec(a);
    if (m) return Number(m[1]);
  }
  return 1;
}

/** Mirrors `service._parse_config`. */
function parseConfig(args: string[]): string | null {
  const prefix = "--configuration=";
  const hit = args.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : null;
}

/**
 * The configuration the API *will* run, for an expert-mode preview shown before the
 * first solve. `argsOverride`, when non-empty, is the expert-mode command-line override
 * (ADR-0023) — threads/config are then recovered from it (`parseThreads`/`parseConfig`),
 * same as the backend's own preview for a custom command line. With no override (or an
 * empty one — a user who cleared the field gets the default, not an error), mirrors
 * `service._portfolio_args(threads=4)` (the API fixes threads at 4). Once a solve
 * returns, `SolveResult.solver` is authoritative and replaces this either way.
 */
export function plannedSolverInfo(timeLimitS: number, argsOverride?: string[]): SolverInfo {
  const args = argsOverride && argsOverride.length ? argsOverride : DEFAULT_PORTFOLIO_ARGS;
  return {
    name: "clingcon",
    threads: parseThreads(args),
    config: parseConfig(args),
    args,
    time_limit_s: timeLimitS,
  };
}

/** The expert cost readout, split so the two solve modes stay comparable (ADR-0035).
 *
 * The preference weak constraints are added at the priority level the stability setting
 * names (ADR-0034, and the Iteration-3 widening). Where that level sits decides whether
 * the unmet-preference count is separable from schedule quality at all:
 *
 * - `top` (6) is above every service-quality level, so the vector grows a **leading** slot
 *   counting unmet edits — split it off and the rest compares with a cold solve's.
 * - `tiebreak` (-1) is below all of them: same, but the extra slot is **trailing**.
 * - `high` (4) / `low` (2) / `avoid` (1) *share* a level with the service objective, so
 *   clingo sums the two into one slot. Nothing can be split back out — say so (`folded`)
 *   and show `SolveResult.quality` instead, which is recomputed from the schedule and
 *   stays the same shape whatever the setting.
 *
 * `level` is the one the result *ran at* (`PreferenceReport.level`), never the one
 * currently selected — those differ the moment the user changes the setting after a solve.
 * Null means the solve carried no weak preferences (a cold solve, or heuristic mode, which
 * leaves the objective untouched).
 */
export function costReadout(
  cost: number[],
  level: PreferenceLevel | null,
): { unmetEdits: number | null; quality: number[]; folded: boolean } {
  if (level === null || cost.length === 0) return { unmetEdits: null, quality: cost, folded: false };
  if (level === "top") return { unmetEdits: cost[0], quality: cost.slice(1), folded: false };
  if (level === "tiebreak") {
    return { unmetEdits: cost[cost.length - 1], quality: cost.slice(0, -1), folded: false };
  }
  return { unmetEdits: null, quality: cost, folded: true };
}
