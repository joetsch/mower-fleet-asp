// Why a dropped edit was not kept (Iteration 6, POST /api/explain). A thin hook: the real
// work is `client.explainSolve` (the HTTP call) and `lib/explanation.ts` (turning the
// result into copy) — this owns only the request/loading/report state and the rule for
// when a stale answer must disappear.

import { useState } from "react";

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

  // Drop a stale explanation the instant a different plan replaces this one — during
  // render, the same guarded-reset pattern `useScheduleEdits` uses for its working copy
  // (`useScheduleEdits.ts`), so a new plan is never shown for a frame still carrying the
  // previous one's answer. Doing this in an effect would commit the stale report first.
  const [source, setSource] = useState<SolveResult | null>(result);
  if (source !== result) {
    setSource(result);
    setReport(null);
  }

  const explain = async (budgetS?: number) => {
    if (!lastRequest || !result?.schedule) return;
    setLoading(true);
    onError(null);
    try {
      const r = await explainSolve(
        lastRequest.target,
        lastRequest.preferences,
        result.schedule,
        budgetS,
      );
      setReport(r);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return { report, loading, explain };
}
