// Why a dropped edit was not kept (Iteration 6, POST /api/explain). A thin hook: the real
// work is `client.explainSolve` (the HTTP call) and `lib/explanation.ts` (turning the
// result into copy) — this owns only the request/loading/report state and the rule for
// when a stale answer must disappear.

import { useEffect, useRef, useState } from "react";

import { explainSolve } from "../api/client";
import type { SolveRequestInfo } from "./useSolveJob";
import type { ExplanationReport, SolveResult } from "../types";

export interface Explanation {
  /** null before the first "Why?" press, or once a different plan is on screen. */
  report: ExplanationReport | null;
  loading: boolean;
  /** Explain every dropped edit in `lastRequest`/`result` under one budget. No-op if
   *  either is missing (nothing was submitted, or nothing has been solved yet). */
  explain: (budgetS?: number) => Promise<void>;
}

export function useExplanation({
  result,
  lastRequest,
  onError,
}: {
  result: SolveResult | null;
  lastRequest: SolveRequestInfo | null;
  onError: (message: string | null) => void;
}): Explanation {
  const [report, setReport] = useState<ExplanationReport | null>(null);
  const [loading, setLoading] = useState(false);

  // Which plan a still-running explain belongs to. Two things can invalidate it, and the
  // during-render reset below catches only the first:
  //
  //   * a report that has *already* arrived, when a new plan replaces this one;
  //   * an answer still in flight. "Why?" stays clickable while a solve runs, and every
  //     poll installs a new incumbent, so a response can resolve after its plan is gone.
  //     Rendering it then describes a schedule the user is not looking at, and suppresses
  //     the current plan's own "not applied" rows, which are filtered by the explained
  //     positions.
  //
  // Same shape as `useSolveJob`'s `activeJobRef`: re-check after the await, drop the
  // response if it no longer belongs. `latestPlan` is written in an effect rather than in
  // render — a ref must not be touched while rendering.
  const latestPlan = useRef<SolveResult | null>(result);
  useEffect(() => {
    latestPlan.current = result;
  }, [result]);
  const pendingFor = useRef<SolveResult | null>(null);

  // Drop a stale explanation the instant a different plan replaces this one — during
  // render, the same guarded-reset pattern `useScheduleEdits` uses for its working copy
  // (`useScheduleEdits.ts`), so a new plan is never shown for a frame still carrying the
  // previous one's answer. Doing this in an effect would commit the stale report first.
  const [source, setSource] = useState<SolveResult | null>(result);
  if (source !== result) {
    setSource(result);
    setReport(null);
    // The in-flight answer no longer has anywhere to land; stop advertising it as pending.
    setLoading(false);
  }

  const explain = async (budgetS?: number) => {
    if (!lastRequest || !result?.schedule) return;
    const computedFor = result;
    pendingFor.current = computedFor;
    setLoading(true);
    onError(null);
    // Still ours? Only if no newer explain started *and* the plan has not moved on.
    const current = () => pendingFor.current === computedFor && latestPlan.current === computedFor;
    try {
      const r = await explainSolve(
        lastRequest.target,
        lastRequest.preferences,
        result.schedule,
        budgetS,
        lastRequest.released,
      );
      if (!current()) return;
      setReport(r);
    } catch (e) {
      if (!current()) return;
      onError((e as Error).message);
    } finally {
      if (current()) {
        pendingFor.current = null;
        setLoading(false);
      }
    }
  };

  return { report, loading, explain };
}
