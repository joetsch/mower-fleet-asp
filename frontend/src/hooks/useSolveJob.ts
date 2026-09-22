// The anytime-solve job machine (ADR-0022), lifted out of App.tsx.
//
// A "custom hook" is just a function that calls React's own hooks — it owns state on
// behalf of its caller, so the caller gets one object instead of a handful of loose
// `useState`s it has to reset by hand. That is the whole point here: `discard()` is the
// single abandon path App used to open-code in three slightly different places.

import { useCallback, useEffect, useRef, useState } from "react";

import { type SolveTarget, cancelSolve, pollSolve, startSolve } from "../api/client";
import { NO_PREFERENCES } from "../lib/schedulePreferences";
import type { SolvePreferences, SolveResult } from "../types";

// Anytime solving (ADR-0022): how often the UI checks in on a running solve. Independent
// of the server-side time limit, which is enforced precisely by a backend timer — this
// only governs how often the schedule view can update and how much poll traffic there is.
const POLL_INTERVAL_MS = 2000;

type PendingAction = "none" | "stop" | "supersede";

/** The `(target, preferences)` a solve actually ran with — what `POST /api/explain`
 *  needs alongside `result.schedule` to explain it (Iteration 6). Kept distinct from
 *  `result` itself since `useScheduleEdits` rebuilds its working copy from a fresh
 *  `result` immediately, so the payload that produced it would otherwise be gone by the
 *  time anyone could ask "why". */
export interface SolveRequestInfo {
  target: SolveTarget;
  preferences: SolvePreferences;
}

export interface SolveJob {
  /** The best schedule so far, or the final one; null before the first model. */
  result: SolveResult | null;
  solving: boolean;
  /** The user pressed Stop before any feasible schedule was found — wording only. */
  stoppedByUser: boolean;
  /** Bumped per run, so `<SolveProgress key={solveRun}>` remounts with a clock at 0. */
  solveRun: number;
  /** The request that produced `result`, or null when `result` does not (or no longer)
   *  correspond to one — see the per-method notes on `discard`/`clearResult`/`restore`. */
  lastRequest: SolveRequestInfo | null;
  run: (
    target: SolveTarget,
    timeLimitS: number,
    clingoArgs?: string[],
    preferences?: SolvePreferences,
  ) => Promise<void>;
  /** The Stop button: end the search early, keep whatever schedule is showing. */
  stop: () => Promise<void>;
  /** Abandon the job and clear its result — a scenario switch, a write, "New scenario…". */
  discard: () => void;
  /** Invalidate the shown schedule without touching a running job (a structural edit). */
  clearResult: () => void;
  /** Put a superseded plan back on screen (the replan undo, ADR-0035). Deliberately does
   *  not touch the job: Undo is disabled while solving, so there is nothing to cancel.
   *  Clears `lastRequest`: the restored plan's own request was never tracked (it predates
   *  this feature, or is itself a restore), so explaining it would risk pairing a plan
   *  with a mismatched request — exactly what the backend's `ExplainRequest` docstring
   *  warns against. "Why?" simply disappears until the next solve. */
  restore: (plan: SolveResult) => void;
}

export function useSolveJob({
  onError,
}: {
  onError: (message: string | null) => void;
}): SolveJob {
  const [result, setResult] = useState<SolveResult | null>(null);
  const [solving, setSolving] = useState(false);
  // Set when the *user* pressed Stop and no feasible schedule had been found yet, purely
  // to word the "no schedule" message accurately (distinct from a real timeout). Local
  // UI state only — the backend doesn't need a fifth solve status for this (ADR-0022).
  const [stoppedByUser, setStoppedByUser] = useState(false);
  const [solveRun, setSolveRun] = useState(0);
  const [lastRequest, setLastRequest] = useState<SolveRequestInfo | null>(null);

  // The running solve job, if any — lets a scenario switch (or Stop) discard a stale
  // poll instead of its result landing on the new scenario, and tells us what to cancel.
  // Same identity-check role the old AbortController ref played, now over a job id
  // (solving itself now outlives any one HTTP request — ADR-0022).
  const activeJobRef = useRef<string | null>(null);
  // The request behind the currently-active job, for `stop()` to pair with whatever
  // (possibly cancelled-with-a-result) schedule that produces — `run()`'s own paths use
  // the `requestInfo` each closes over directly. Not read anywhere a result isn't also
  // about to be set, so a request left here after its job ends is inert, never stale.
  const activeRequestRef = useRef<SolveRequestInfo | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Set by stop()/discard() when they run before startSolve() has even resolved (no job
  // id yet to cancel) — run() checks this the moment the job exists, so the action isn't
  // silently lost/undone once the in-flight request finally comes back. "stop" still
  // applies the cancelled result (same scenario, user asked to see it); "supersede" (a
  // scenario switch) must not touch state at all — the switch has already reset
  // everything for the *new* scenario, so it just cancels server-side.
  const pendingRef = useRef<PendingAction>("none");

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Stop the solve early (or discard an abandoned one) and actually tell the server —
  // fire-and-forget when called from a scenario switch, since the UI has already moved
  // on locally. This is what makes the switch also stop the abandoned solve server-side.
  const stopJob = useCallback((jobId: string) => {
    cancelSolve(jobId).catch(() => {
      // The job may already be finished or superseded — nothing to do either way.
    });
  }, []);

  const run = useCallback(
    async (
      target: SolveTarget,
      timeLimitS: number,
      clingoArgs?: string[],
      preferences?: SolvePreferences,
    ) => {
      setSolving(true);
      onError(null);
      setStoppedByUser(false);
      pendingRef.current = "none";
      setSolveRun((n) => n + 1); // fresh SolveProgress mount, its clock starts at 0
      // Captured once, then only ever committed to `lastRequest` state in the same call
      // that commits the schedule it describes to `result` — never eagerly. `result` is
      // an anytime, possibly-many-polls-away thing (`tick` below); setting `lastRequest`
      // here instead would pair the new request with whatever *old* schedule is still on
      // screen for the whole window until the next poll (or forever, if Stop lands before
      // any model does) — exactly the stale-pairing bug `POST /api/explain`'s "exact
      // triple" contract exists to prevent.
      const requestInfo: SolveRequestInfo = { target, preferences: preferences ?? NO_PREFERENCES };
      try {
        const { job_id: jobId } = await startSolve(target, {
          timeLimitS,
          clingoArgs,
          preferences,
        });

        // `as PendingAction`, not just an annotation: stop()/discard() may have mutated
        // pendingRef during the `await` above, but TS's control-flow narrowing can't see
        // across that and (wrongly) treats it as still the "none" it was set to before
        // the await — an assertion is the standard escape hatch here.
        const pending = pendingRef.current as PendingAction;
        pendingRef.current = "none";
        if (pending === "supersede") {
          // A scenario switch already reset every bit of state — just stop the orphaned
          // job server-side. `lastRequest` was never touched for this request, so there
          // is nothing to undo.
          cancelSolve(jobId).catch(() => {});
          return;
        }
        if (pending === "stop") {
          // Stop was pressed before this request even came back — end it immediately
          // instead of starting to poll, same outcome as stop() below.
          setStoppedByUser(true);
          try {
            const status = await cancelSolve(jobId);
            if (status.result) {
              setResult(status.result);
              setLastRequest(requestInfo);
            }
          } catch (e) {
            onError((e as Error).message);
          } finally {
            setSolving(false);
          }
          return;
        }
        activeJobRef.current = jobId;
        activeRequestRef.current = requestInfo;

        const tick = async () => {
          if (activeJobRef.current !== jobId) return; // superseded — ignore this poll
          try {
            const status = await pollSolve(jobId);
            if (activeJobRef.current !== jobId) return;
            if (status.result) {
              setResult(status.result); // the entire "anytime" wiring
              setLastRequest(requestInfo);
            }
            if (status.done) {
              stopPolling();
              activeJobRef.current = null;
              setSolving(false);
            }
          } catch (e) {
            if (activeJobRef.current === jobId) {
              stopPolling();
              activeJobRef.current = null;
              setSolving(false);
              onError((e as Error).message);
            }
          }
        };
        pollTimerRef.current = setInterval(tick, POLL_INTERVAL_MS);
        void tick(); // an immediate check-in too, rather than waiting a full interval
      } catch (e) {
        onError((e as Error).message);
        setSolving(false);
      }
    },
    [onError, stopPolling],
  );

  const stop = useCallback(async () => {
    const jobId = activeJobRef.current;
    stopPolling();
    activeJobRef.current = null;
    if (!jobId) {
      // startSolve() is still in flight (no job id yet) — flag it so run() cancels the
      // job the instant it exists; leave `solving` as-is, run()'s own path clears it.
      pendingRef.current = "stop";
      return;
    }
    setStoppedByUser(true);
    try {
      const status = await cancelSolve(jobId);
      if (status.result) {
        setResult(status.result);
        setLastRequest(activeRequestRef.current);
      }
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSolving(false);
    }
  }, [onError, stopPolling]);

  // The one abandon path. App used to open-code this three times, with two of the copies
  // drifting: "New scenario…" never armed the `pendingRef` guard (so a solve request
  // still in flight could land on the new draft) and the post-save reset never cancelled
  // the job server-side at all. Both are fixed by there being a single copy.
  const discard = useCallback(() => {
    const jobId = activeJobRef.current;
    activeJobRef.current = null;
    stopPolling();
    if (jobId) stopJob(jobId);
    else pendingRef.current = "supersede"; // in case startSolve() is still in flight
    setSolving(false);
    setResult(null);
    setLastRequest(null);
    setStoppedByUser(false);
  }, [stopPolling, stopJob]);

  const clearResult = useCallback(() => {
    setResult(null);
    setLastRequest(null);
  }, []);

  const restore = useCallback((plan: SolveResult) => {
    setResult(plan);
    setLastRequest(null);
  }, []);

  // Clear the poll timer on unmount — App never actually unmounts in practice, but this
  // is the correct hygiene for an interval started in an event handler rather than an
  // effect. It does not stop the job server-side; there is nothing left client-side to
  // do that from once the page is gone.
  useEffect(() => stopPolling, [stopPolling]);

  return {
    result,
    solving,
    stoppedByUser,
    solveRun,
    lastRequest,
    run,
    stop,
    discard,
    clearResult,
    restore,
  };
}
