"""The paper's numbers stay tied to the data that produced them (ADR-0050 amendment).

Two directions: the recorded study outputs recompute to ``paper_numbers.PAPER``, and every
``PAPER`` value appears in ``main.tex`` in the sentence that cites it. Editing a number on
either side without the other fails here. Pure CSV/JSON reading — no solver.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments/paper_numbers.py"
MAIN_TEX = ROOT / "paper/taasp2026/main.tex"

_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six"}

#: Each cited sentence, with ``{key}`` (as printed) or ``{key:word}`` (spelled out).
CITATIONS = [
    "({replan.scenarios} scenarios, {replan.pinned} of services pinned, {replan.budget})",
    "for {replan.weak_top.extra_time} {replan.weak_top.extra_time_word} solve time than "
    "re-planning from scratch",
    "a median of {replan.weak_top.extra_p1_violations} and at most "
    "{replan.weak_top.worst_p1_violations:word} extra maximum-interval violations",
    "but only {replan.weak_tiebreak.kept_unproven} when it was not",
    "ignoring the edits ({replan.cold.kept_unproven})",
    "kept up to {replan.heur.best_kept_unproven} of edits but guarantee nothing: they kept "
    "every edit in at most {replan.heur.best_all_kept_runs} of {replan.runs} runs, weak@top in all "
    "{replan.weak_top.all_kept_runs}",
]

#: Claims the paper words without a digit; the value they rest on is pinned here instead.
WORDED = {
    "kept every edited start hour, proven optimal or not": {
        "replan.weak_top.kept_proven": "100%",
        "replan.weak_top.kept_unproven": "100%",
    },
}

#: Table~\ref{tab:pilot-outcomes} and its caption (App. C) — one row per mechanism, plus
#: the caption's own proven/not-proven split. `{key}` cells only; no spelled-out numbers.
APP_C_TABLE = [
    "by scenario stratum ({replan.strata})",
    "\\texttt{weak@top} & {replan.weak_top.kept_proven} & {replan.weak_top.kept_unproven} & "
    "{replan.weak_top.all_kept_runs}/{replan.runs}",
    "\\texttt{weak@tiebreak} & {replan.weak_tiebreak.kept_proven} & "
    "{replan.weak_tiebreak.kept_unproven} & {replan.weak_tiebreak.all_kept_runs}/{replan.runs}",
    "re-plan from scratch (\\texttt{cold}) & {replan.cold.kept_proven} & "
    "{replan.cold.kept_unproven} & {replan.cold.all_kept_runs}/{replan.runs}",
    "heuristic & {replan.heur.best_kept_proven} & {replan.heur.best_kept_unproven} & "
    "{replan.heur.best_all_kept_runs}/{replan.runs}",
]


def _load() -> SimpleNamespace:
    # runpy compiles from source every time; an importlib load can reuse a stale .pyc when
    # an edit keeps the file size and lands within the same second (seen while mutating).
    return SimpleNamespace(**runpy.run_path(str(SCRIPT), run_name="paper_numbers"))


def _tex(value: str) -> str:
    return value.replace("%", r"\%").replace(" s", r"\,s")


def _render(template: str, paper: dict[str, str]) -> str:
    # Every real PAPER key is dotted ("replan.xxx.yyy"); requiring a dot keeps this from
    # also matching a literal `\texttt{cold}` or similar in a template's own LaTeX.
    def sub(m: re.Match[str]) -> str:
        value = paper[m.group(1)]
        return _WORDS[value] if m.group(2) else _tex(value)

    return re.sub(r"\{([a-z0-9_]+\.[a-z0-9_.]+)(:word)?\}", sub, template)


def test_recorded_data_recomputes_to_the_papers_numbers() -> None:
    pn = _load()
    values = pn.compute()
    mismatches = {k: (v, values.get(k)) for k, v in pn.PAPER.items() if values.get(k) != v}
    assert not mismatches, f"paper value -> recomputed value: {mismatches}"


@pytest.mark.skipif(not MAIN_TEX.exists(), reason="the paper source is not part of this tree")
def test_every_number_is_cited_in_the_paper_as_recorded() -> None:
    pn = _load()
    tex = " ".join(MAIN_TEX.read_text(encoding="utf-8").split())
    tex = re.sub(r"\\allowbreak\s*", "", tex)
    cited: set[str] = set()
    for template in CITATIONS + APP_C_TABLE + list(WORDED):
        cited |= set(re.findall(r"\{([a-z0-9_]+\.[a-z0-9_.]+)", template))
        sentence = _render(template, pn.PAPER)
        assert sentence in tex, f"not in main.tex: {sentence!r}"
    for pins in WORDED.values():
        cited |= set(pins)
        assert {k: pn.PAPER[k] for k in pins} == pins
    assert cited == set(pn.PAPER), "every PAPER value must be tied to a sentence"
