import { useEffect, useState } from "react";

import "./SolveProgress.css";

interface Props {
  timeLimitS: number;
}

/**
 * Elapsed time vs. the time budget, ticking while a solve is in flight. Mount this only
 * while `solving` is true, with a fresh `key` per solve, so each mount starts its own
 * clock at 0 — cheaper and cleaner than resetting a persistent timer.
 *
 * This is deliberately just elapsed-vs-budget, not solver-internal progress (no percent
 * proven, no conflict count) — the schedule view itself now updates live with each
 * improved model (ADR-0022, anytime solving), this bar only answers "how much of the
 * budget has passed". The bar can disappear well short of 100% (the solve finished
 * early, e.g. it proved optimal, or the user pressed Stop) or reach the end (it ran to
 * the budget) — all truthful outcomes.
 */
export function SolveProgress({ timeLimitS }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = performance.now();
    const id = setInterval(() => setElapsed((performance.now() - start) / 1000), 100);
    return () => clearInterval(id);
  }, []);

  const fraction = Math.min(elapsed / timeLimitS, 1);
  return (
    <div className="solve-progress">
      <div className="solve-progress-head">
        <span>Computing schedule…</span>
        <span className="solve-progress-time">
          {elapsed.toFixed(1)}s / {timeLimitS}s
        </span>
      </div>
      <div className="solve-progress-track">
        <div className="solve-progress-fill" style={{ width: `${fraction * 100}%` }} />
      </div>
    </div>
  );
}
