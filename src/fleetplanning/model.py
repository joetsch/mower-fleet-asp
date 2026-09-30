"""Data model shared by the scenario definitions, the solver core, and the HTTP API.

All types are `pydantic` models, so the same definitions give us validation *and*
automatic request/response schemas for FastAPI.

Validation (ADR-0010)
---------------------
Every rule the notebook's sanity-check cell enforced — plus the structural checks it
assumed — runs here, at construction. An invalid scenario raises ``ValidationError``
immediately instead of hanging or ``KeyError``-ing deep in the solver pipeline. See
``docs/known-hazards.md`` for what is *not* yet guarded.

Time convention
---------------
The planner works in whole hours. The instant planning starts is ``t = 0``. Service
history lies at negative offsets; the planning horizon covers ``0 .. horizon_hours``.
Separately, ``horizon_start_hour`` says which hour *of the week* ``t = 0`` falls on
(hour 0 = Monday 00:00), which is what the weekly availability schedule is looked up
against.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# A half-open hour interval [start, end). If start > end it wraps past midnight,
# e.g. (22, 6) means 22:00-24:00 and 00:00-06:00.
Interval = tuple[int, int]


def _covered_hours(intervals: list[Interval]) -> set[int]:
    """The set of hours-of-day covered by ``intervals``, honouring the midnight wrap."""
    hours: set[int] = set()
    for start, end in intervals:
        if start < end:
            hours.update(range(start, end))
        else:  # wraps past midnight
            hours.update(range(start, 24))
            hours.update(range(0, end))
    return hours


class DaySchedule(BaseModel):
    """Availability for one weekday."""

    no_go: list[Interval] = Field(
        default_factory=list,
        description="Hours a mower may not operate at all (irrigation, night rules, ...).",
    )
    avoid: list[Interval] = Field(
        default_factory=list,
        description="Hours a mower should avoid but may use if necessary (daytime play).",
    )

    @model_validator(mode="after")
    def _check_intervals(self) -> DaySchedule:
        for label, intervals in (("no_go", self.no_go), ("avoid", self.avoid)):
            for start, end in intervals:
                if not (0 <= start <= 23 and 0 <= end <= 23):
                    raise ValueError(f"{label} interval ({start}, {end}) has an hour outside 0..23")
                if start == end:
                    # (h, h) matches every hour in _in_intervals — always a mistake.
                    raise ValueError(f"{label} interval ({start}, {end}) is zero-width")
        return self


class Area(BaseModel):
    """A part of the course that must be serviced regularly."""

    name: str
    type: str
    hole: int = Field(ge=1)
    priority: int = Field(
        ge=1,
        le=3,
        description=(
            "1 = highest priority. The encoding maps priority P to weak-constraint "
            "level 5-P (see docs/design-choices.md B4 and ADR-0012). The range is "
            "capped at 3 so that level 5-P stays in {4, 3, 2}, strictly above the "
            "avoid-zone level (1) and the min-interval level (0); P >= 4 would put a "
            "max-interval violation on the same level as an avoid violation. Relax "
            "this bound only together with an encoding change."
        ),
    )
    min_interval: int = Field(gt=0, description="Minimum hours between two service starts.")
    max_interval: int = Field(gt=0, description="Maximum hours between two service starts.")
    schedule: dict[str, DaySchedule] = Field(
        description="Weekly availability, keyed by weekday name (see DAYS)."
    )
    size_m2: int | None = Field(
        default=None,
        gt=0,
        description="Area size. Promoted from the generator source (ADR-0024) so the "
        "scenario is self-describing; None for scenarios authored before that. Feeds the "
        "default per-(area, mower) duration and the recomputed load factor.",
    )
    min_services: int | None = Field(
        default=None,
        ge=1,
        description="Required minimum service starts this week — a user-owned requirement "
        "(ADR-0024). None means derive it (ADR-0017). See docs/known-hazards.md: a "
        "hand-set value can make the scenario genuinely UNSAT.",
    )
    max_services: int | None = Field(
        default=None,
        ge=1,
        description="Cap on service starts this week — a user-owned requirement (ADR-0024). "
        "None means derive it (ADR-0017). Also caps the ground-program size.",
    )

    @model_validator(mode="after")
    def _check_area(self) -> Area:
        if self.min_interval > self.max_interval:
            raise ValueError(
                f"min_interval ({self.min_interval}) > max_interval ({self.max_interval})"
            )
        if (
            self.min_services is not None
            and self.max_services is not None
            and self.min_services > self.max_services
        ):
            raise ValueError(
                f"min_services ({self.min_services}) > max_services ({self.max_services})"
            )
        if set(self.schedule) != set(DAYS):
            missing = sorted(set(DAYS) - set(self.schedule))
            extra = sorted(set(self.schedule) - set(DAYS))
            raise ValueError(
                f"schedule must have exactly the 7 weekday keys; missing={missing} extra={extra}"
            )
        for weekday, day in self.schedule.items():
            if len(_covered_hours(day.no_go)) >= 24:
                # A full-day no-go makes completion_table._adjusted loop forever.
                raise ValueError(
                    f"{weekday}: no_go windows cover the whole day for area {self.name!r}"
                )
        return self


class Mower(BaseModel):
    name: str
    can_mow: list[str] = Field(description="Names of areas this mower is able to service.")
    model: str | None = Field(
        default=None,
        description="Mower model label. Promoted from the generator source (ADR-0024); "
        "None for scenarios authored before that. Display + catalogue lookup only.",
    )
    area_capacity_m2_per_day: int | None = Field(
        default=None,
        gt=0,
        description="Mowing rate. Promoted from the generator source (ADR-0024); None for "
        "scenarios authored before that. Feeds the default per-(area, mower) duration and "
        "the recomputed load factor.",
    )


class BaseDuration(BaseModel):
    """The productive mowing time (whole hours) for one ``(area, mower)`` pair.

    Set by the scenario generator (ADR-0013): it derives this from area size and mower
    area-capacity instead of the notebook's seeded sampling. When
    :attr:`Scenario.base_durations` is populated the completion table uses these numbers
    as the base for its no-go/avoid walk; when it is ``None`` the sampling path
    (``duration_seed``) still applies, so ``toy_course`` and the notebook are unchanged.
    """

    area: str
    mower: str
    hours: int = Field(gt=0)


class ServiceEvent(BaseModel):
    """One past (or in-progress) service, relative to t = 0."""

    area: str
    mower: str
    start: int
    completion: int

    @model_validator(mode="after")
    def _check_order(self) -> ServiceEvent:
        if self.completion < self.start:
            raise ValueError(f"completion ({self.completion}) precedes start ({self.start})")
        return self


def history_event(area: str, mower: str, hours: int, *, completion: int = 0) -> ServiceEvent:
    """A :class:`ServiceEvent` of length ``hours`` ending at ``completion`` (default t = 0).

    The one place the "a service is ``hours`` long, so ``start = completion - hours``"
    convention lives. ``generator/params.py::_synthesise_history`` (which draws
    ``completion``) and ``scenarios/template.py`` (which always uses the default "just
    serviced at t = 0") both build their history through here so the two cannot drift.
    """
    return ServiceEvent(area=area, mower=mower, start=completion - hours, completion=completion)


class Scenario(BaseModel):
    """A complete planning problem instance."""

    name: str
    areas: list[Area]
    mowers: list[Mower]
    history: list[ServiceEvent]
    horizon_hours: int = Field(default=168, gt=0)
    horizon_start_hour: int = Field(
        default=13,
        ge=0,
        le=167,
        description="Hour of the week (0 = Mon 00:00) that t = 0 corresponds to.",
    )
    duration_seed: int = Field(
        default=0,
        description="Seed for the (otherwise deterministic) mowing-duration sampling. "
        "Ignored when base_durations is set.",
    )
    base_durations: list[BaseDuration] | None = Field(
        default=None,
        description="Data-driven per-(area, mower) base mowing hours (ADR-0013). "
        "When set, the completion table uses these instead of sampling.",
    )

    @model_validator(mode="after")
    def _check_scenario(self) -> Scenario:
        area_names = [a.name for a in self.areas]
        mower_names = [m.name for m in self.mowers]
        if len(set(area_names)) != len(area_names):
            raise ValueError("area names must be unique")
        if len(set(mower_names)) != len(mower_names):
            raise ValueError("mower names must be unique")

        areas = set(area_names)
        mowers = {m.name: set(m.can_mow) for m in self.mowers}

        for mower in self.mowers:
            unknown = sorted(set(mower.can_mow) - areas)
            if unknown:
                raise ValueError(
                    f"mower {mower.name!r} can_mow references unknown areas: {unknown}"
                )

        seen: set[str] = set()
        for ev in self.history:
            if ev.area not in areas:
                raise ValueError(f"history references unknown area {ev.area!r}")
            if ev.mower not in mowers:
                raise ValueError(f"history references unknown mower {ev.mower!r}")
            if ev.area not in mowers[ev.mower]:
                raise ValueError(f"history: mower {ev.mower!r} cannot service area {ev.area!r}")
            if ev.area in seen:
                raise ValueError(f"history has more than one event for area {ev.area!r}")
            seen.add(ev.area)

        missing = sorted(areas - seen)
        if missing:
            raise ValueError(f"service history missing for areas: {missing}")

        if self.base_durations is not None:
            given = {(bd.area, bd.mower) for bd in self.base_durations}
            if len(given) != len(self.base_durations):
                raise ValueError("base_durations has duplicate (area, mower) entries")
            capable = {
                (a, m_name) for m_name, m_areas in mowers.items() for a in m_areas if a in areas
            }
            if given != capable:
                missing = sorted(capable - given)
                extra = sorted(given - capable)
                raise ValueError(
                    "base_durations must cover exactly the capable (area, mower) pairs; "
                    f"missing={missing} extra={extra}"
                )
        return self


# --- user preferences (ADR-0031) ---

#: Where the preference weak constraints sit against the forward model's own levels
#: (max-interval at ``5-P`` in {4,3,2}, avoid-zone at 1, min-interval at 0) — i.e. what one
#: churned task costs. The names are the product vocabulary; ``service.PREFERENCE_LEVELS``
#: holds the ``-c pref_level=N`` each one maps to. Kept here rather than in ``service`` so
#: the API schema (and the generated TypeScript) names them.
PreferenceLevel = Literal["top", "high", "low", "avoid", "tiebreak"]


class PreferredTask(BaseModel):
    """One task the user edited or froze; a re-solve should try to preserve it (ADR-0031).

    Deliberately **index-free**. The encoding numbers an area's tasks 1..N in time order
    (``task(A,1..M)``, ordered by ``start(A,N+1) >= completion(A,N)``), so a preference
    phrased as "task 3 of this area" silently re-targets the moment the solver picks a
    different ``num_starts``. "This area has a task starting at hour S, on mower M"
    survives that, and is what the user actually meant by dragging a bar.

    The two halves are scored independently, so ``mower=None`` is meaningful on its own:
    a **time-only** preference — keep the hour, let the solver choose the mower. The
    Iteration 5 schedule editor always names a mower; the field is optional because that
    is the shape a later mower-breakdown control needs, and it costs nothing to allow.
    """

    area: str = Field(min_length=1)
    start: int = Field(ge=0, description="Start hour offset from t = 0, as in ScheduledTask.")
    mower: str | None = Field(
        default=None,
        description="Preferred mower; None means 'keep the time, any mower'.",
    )
    origin: Literal["frozen", "edited", "added"] = Field(
        default="edited",
        description="Where the preference came from. Read by the UI and the mechanism "
        "study only — it is never emitted as a solver fact.",
    )


class SolvePreferences(BaseModel):
    """The edited/frozen tasks a re-solve should try to preserve, and how (ADR-0031).

    ``mode="off"`` (the default) means the solve is byte-identical to one with no
    preferences at all: no extra program, no extra flag. That invariant is what keeps
    every existing golden and contract test meaningful.
    """

    tasks: list[PreferredTask] = Field(default_factory=list)
    mode: Literal["off", "weak", "heuristic"] = Field(
        default="off",
        description="How preferences reach the solver: 'weak' adds weak constraints at "
        "their own priority level, 'heuristic' adds #heuristic directives (ADR-0032). "
        "'off' ignores `tasks` entirely.",
    )
    level: PreferenceLevel = Field(
        default="top",
        description="Weak mode only: where the preference weak constraints sit relative to "
        "the forward model's own service-quality levels, i.e. what one churned task is "
        "worth. 'top' outranks every service violation (the default); 'high' ties it with "
        "one missed High-priority service, 'low' with a Low-priority one, 'avoid' with an "
        "hour worked in an avoid window; 'tiebreak' puts it below all of them. Prefer "
        "'top' — ADR-0034 found 'tiebreak' indistinguishable from ignoring the edits "
        "(0.131 vs 0.134 agreement) on any instance the solver cannot prove within budget, "
        "which is most of them. It is kept for the study, not as a product option.",
    )


class DroppedPreference(BaseModel):
    """One half of one preference that could not be expressed, and why.

    ``half`` is ``"time"`` or ``"mower"``. Dropping the mower half leaves the time half
    standing — the right degradation for "that machine cannot mow this area".
    """

    area: str
    start: int
    mower: str | None = None
    half: Literal["time", "mower"]
    reason: str


class AgreementCounts(BaseModel):
    """How much of what was asked for survived.

    ``mower_total`` counts only preferences that named a mower, so ``mower_kept /
    mower_total`` is not diluted by time-only preferences that never asked for one.
    """

    total: int = 0
    time_kept: int = 0
    mower_total: int = 0
    mower_kept: int = 0


class PreferenceAgreement(AgreementCounts):
    """Overall agreement plus the same counts split by :attr:`PreferredTask.origin` —
    "7 of 9 frozen tasks untouched" is the sentence the UI wants to write."""

    by_origin: dict[str, AgreementCounts] = Field(default_factory=dict)


class PreferenceReport(BaseModel):
    """What the re-solve did with the user's edits. Attached to :class:`SolveResult`."""

    agreement: PreferenceAgreement
    dropped: list[DroppedPreference] = Field(default_factory=list)
    level: PreferenceLevel | None = Field(
        default=None,
        description="The stability setting this solve actually ran at; None in heuristic "
        "mode, which biases the search and leaves the objective alone. Echoed because "
        "`Schedule.cost`'s shape depends on it — at 'top' the unmet-preference count is a "
        "separate leading slot, at the others it is summed into an existing "
        "service-quality slot and cannot be split back out.",
    )
    pref_level: int | None = Field(
        default=None,
        description="The `-c pref_level=N` constant `level` maps to, for the expert "
        "readout. None whenever `level` is.",
    )


# --- solver output -------------------------------------------------------------


class ScheduledTask(BaseModel):
    area: str
    task: int
    mower: str
    start: int
    end: int


class Violation(BaseModel):
    kind: str = Field(description="max_interval | min_interval | avoid_zone")
    area: str
    task: int | None = None
    since: int | None = Field(
        default=None,
        description="Hour offset from t=0 when this max-interval violation's elapsed "
        "window began — the latest moment the area could still have been serviced on "
        "time. Set only for a violation with a concrete window inside this horizon (a "
        "gap between two scheduled tasks, or the first task starting too late against "
        "service history); None for every other violation kind, and for the "
        "last-task-too-early wraparound case, whose 'violation' is a risk into next "
        "week's cycle rather than an elapsed window this week.",
    )
    until: int | None = Field(
        default=None,
        description="Hour offset when the elapsed window named in `since` ended — the "
        "start of the task that finally serviced the area. None under the same "
        "conditions as `since`.",
    )


class Schedule(BaseModel):
    tasks: list[ScheduledTask]
    violations: list[Violation]
    cost: list[int] = Field(description="clingcon optimisation cost vector (lexicographic).")


class SolverInfo(BaseModel):
    """How the solver was configured for one solve — expert-mode diagnostics, not
    end-user data.

    Echoed to the client so the UI reports the real configuration instead of a hardcoded
    guess (an earlier frontend string claimed "single-threaded"; the default portfolio
    is 4-thread — see ADR-0007, ADR-0009).
    """

    name: str = "clingcon"
    threads: int
    config: str | None = Field(
        default=None,
        description="clingo --configuration, present only when threads > 1.",
    )
    args: list[str] = Field(
        description="Thread/portfolio flags passed to clingo (ADR-0007). "
        "Excludes the -c horizon=N instance constant.",
    )
    time_limit_s: float


class SolveResult(BaseModel):
    scenario_name: str
    horizon_hours: int
    solved: bool
    optimal: bool = Field(
        default=False,
        description="True if the solver proved optimality; False if it returned a best-so-far.",
    )
    status: str = Field(
        default="unknown",
        description="optimal | satisfiable | unsatisfiable | unknown (see ADR-0010).",
    )
    solve_time_s: float
    solver: SolverInfo | None = Field(
        default=None,
        description="Solver configuration for this solve — expert-mode diagnostics. "
        "None only for results not produced by a real solve (e.g. stubbed in tests).",
    )
    schedule: Schedule | None = None
    quality: list[int] | None = Field(
        default=None,
        description="The fixed 5-slot service-quality vector [max@P1, max@P2, max@P3, "
        "avoid, min] recomputed by solver/score.py (ADR-0016). Unlike the raw clingcon "
        "`schedule.cost`, its length and meaning do not change with the instance or the "
        "preference level, so it is the axis to compare across solves — a rolling horizon "
        "compares it across rolls (ADR-0042). None when there is no schedule.",
    )
    preferences: PreferenceReport | None = Field(
        default=None,
        description="What became of the user's schedule edits (ADR-0031). None when the "
        "solve carried no preferences.",
    )


# --- rolling horizon (ADR-0042) ----------------------------------------------------------


class RollResult(BaseModel):
    """The outcome of advancing "now" by a fixed number of hours (``rolling.advance``).

    The demonstrator has no execution simulation (deliberately out of scope), so what a
    roll "executed" is just the previous plan's tasks. A roll:

    - slides ``horizon_start_hour`` forward (mod 168) and rebases every offset by
      ``-hours`` — a fixed-length window sliding along the same weekly calendar, so a
      carried task keeps its exact wall-clock hour and availability window;
    - folds each area's most recent now-past task into its single history event
      (``ServiceEvent``); a task still running at the new ``t = 0`` becomes an in-progress
      event (``completion > 0``);
    - carries every still-future task forward as a ``PreferredTask`` (``origin="frozen"``),
      so the re-solve tries to keep the plan it has not yet had to execute.
    """

    scenario: Scenario = Field(description="The rolled scenario — new now, folded history.")
    consumed: list[ServiceEvent] = Field(
        default_factory=list,
        description="The new history events that came from now-past tasks (one per area "
        "that was serviced in the elapsed window). For the run log.",
    )
    carried: list[PreferredTask] = Field(
        default_factory=list,
        description="Still-future tasks rebased to the new t = 0, as frozen preferences "
        "for the re-solve.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Human-readable remarks about this roll — areas not serviced in the "
        "window, services now straddling the new t = 0.",
    )


# --- explainability (Iteration 6) --------------------------------------------------------
#
# Answers one contrastive question: for a schedule edit the last re-solve did not keep,
# what stopped it? The edit itself is the foil ("why not the plan I drew"), which is what
# makes the question answerable at all (see docs/explainability-literature.md §1).
#
# Computed from the *already-returned* SolvePreferences + Schedule, never by re-solving —
# a second solve under the -t4 portfolio could return a different equally-optimal plan
# (ADR-0007), which would make an explanation describe a schedule the user is not looking
# at. `service.explain_scenario` is the orchestrator; `explain/` holds the mechanism.


ExplanationOutcome = Literal[
    "blocked_statically",
    "individually_impossible",
    "conflicts_with",
    "not_yet_found",
    "not_determined",
]


class ConflictingTask(BaseModel):
    """One task from the plan on screen that a dropped edit conflicts with — a member of
    the conflict named in :attr:`EditExplanation.conflicts`."""

    area: str
    start: int
    mower: str | None = None


class EditExplanation(BaseModel):
    """Why one dropped edit — a submitted preference with ``origin`` ``"edited"`` or
    ``"added"`` that does not appear in the resulting schedule — was not kept.

    **What each outcome means:**

    - ``blocked_statically`` — conflicts with a *kept* task's mower or area interval;
      found by a plain overlap check, no solver call.
    - ``individually_impossible`` — no schedule could ever place this edit, even alone
      (a structural conflict the encoding's own hard constraints rule out regardless of
      anything else).
    - ``conflicts_with`` — conflicts specifically with the kept tasks named in
      ``conflicts``, proven by assumption-based refutation over the real forward model.
    - ``not_yet_found`` — a schedule keeping this edit alongside every currently-kept task
      does exist (checked directly) — but the solve that produced the plan on screen had
      not proven optimality. This is the *only* way a "could have been kept" answer can
      arise: if the parent solve **had** been proven optimal, a model satisfying this edit
      plus everything already kept would mean strictly fewer unmet preferences than the
      claimed optimum — a contradiction. So this outcome is a solid signal that searching
      longer (or re-solving with this edit pinned) may do better, never an arbitrary tie.
    - ``not_determined`` — the explain budget ran out before an answer was reached. Never
      a claim that the edit is impossible.
    """

    area: str
    start: int
    mower: str | None = None
    outcome: ExplanationOutcome
    detail: str = Field(
        description="A short, composable reason — the specific static overlap found, or "
        "a plain count for a conflict."
    )
    conflicts: list[ConflictingTask] = Field(default_factory=list)
    minimal: bool = Field(
        default=True,
        description="False if the explain budget ran out before every candidate in "
        "`conflicts` could be checked for redundancy — the conflict is still valid, just "
        "not proven smallest (the keep-on-timeout degradation rule: an assumption is only "
        "ever removed once proven unnecessary, so a timeout can only leave this larger, "
        "never wrong).",
    )
    solve_time_s: float | None = Field(
        default=None,
        description="Wall time this edit's counterfactual check(s) took. None for "
        "blocked_statically, which makes no solver call.",
    )


class ReinstatedService(BaseModel):
    """An area whose schedule carries more tasks than its *baseline* named for it —
    services the solver added beyond what the plan before the re-solve already had.
    Invisible to the kept/dropped count (there is no negative preference, ADR-0035
    decision 3: release means "out of the payload", not "never do this"), so this is the
    one explanation class that needs no dropped edit to trigger.

    A released service brought straight back by the area's own service-count minimum is
    *not* reported — that is exactly what release means may happen, not a mismatch to
    explain (amendment, 2026-09-24: ``submitted_count`` folds in whatever was released from
    the area, so the baseline is the plan before the re-solve, not the payload alone)."""

    area: str
    min_services: int
    submitted_count: int = Field(
        description="The plan before this re-solve: the payload's count for this area plus "
        "whatever was released from it. Not literally 'submitted' any more — kept the name "
        "since the frontend and ADR-0048 already read it that way."
    )
    actual_count: int
    forced_by_minimum: bool = Field(
        default=False,
        description="Whether the area's service-count minimum actually required the extra "
        "service (``submitted_count < min_services``). When False the minimum was already "
        "met and the solver added it for its own reasons — the max-interval objective, "
        "typically. The UI must not name the minimum as the cause in that case.",
    )


class RippleMove(BaseModel):
    """A *frozen* task — one the user did not touch — that still moved, attributed when
    possible to sharing a mower or area with one of the user's edits."""

    area: str
    start: int
    mower: str | None = None
    shares_mower_with: str | None = Field(
        default=None, description="Area name of an edited task using the same mower."
    )
    shares_area_with: str | None = Field(
        default=None, description="Set when this task's own area was edited elsewhere."
    )


class ExplanationReport(BaseModel):
    """Everything explainable about one solve result relative to what the user asked for.
    Computed from the submitted :class:`SolvePreferences` and the resulting
    :class:`Schedule` — no re-solve, so nothing here can disagree with the plan already on
    screen."""

    edits: list[EditExplanation] = Field(default_factory=list)
    reinstated: list[ReinstatedService] = Field(default_factory=list)
    ripple: list[RippleMove] = Field(default_factory=list)
    budget_s: float = Field(description="The overall wall-clock budget this report ran under.")
    budget_exhausted: bool = Field(
        default=False,
        description="True if the budget ran out before every dropped edit could be "
        "explained — the remainder are reported `not_determined`, never silently omitted.",
    )
