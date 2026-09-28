"""§5.4 connected to the decision loop, and gated like every other module (SPEC-v0.2 §5.4, §7, §9).

Three seams, all of them slots the loop already had:

* **the recall** — `PlanningMixin._offer_memories` is the one place this package touches a round.
  It reads the store, renders the relevant rows and hands them to `WorkingMemory.render()`, which
  spends the *same* character budget over them that it spends over a debt line. Nothing here writes
  a `WorldState`, a plan row or a `Status`: §5.4's 检索结果作为模型参考，不直接改写 WorldState is
  enforced by the fact that this module has no instrument that could write one.
* **the judgement** — `_record_feedback` runs *after* the ledger has settled the action, and asks
  `retrieval.mark_used` whether the round's actual executed skill repeats a step the recalled
  experience records — of the rows that *reached the page*, since a row the render budget cut cannot
  have been used and is booked as unmeasurable instead. §12.3's 以实际动作和最终状态为准 is the whole
  of it: a rationale that says "as I did last time" while the action matches nothing is recorded as
  not-used.
* **the residue** — `write_experience` turns the finished episode into the stored row, through the
  same `experience_from_episode` an offline reader uses. One extractor means a row a batch acted on
  can never differ from a row someone rebuilds from the archive afterwards.

## What the gate is allowed to switch

`wo_episodic_memory` turns off the module that owns `memory_write`, `memory_retrieval` and
`memory_use`, so on that arm this mixin files none of the three, offers no `recalled` section, and
writes nothing to the store. What remains is the P2 arm exactly — the same payload keys, the same
records — which is what makes a `full` vs `wo_episodic_memory` difference attributable to memory
rather than to two different loops. `wo_working_memory` is a different cut and stays honest in the
opposite direction: retrieval still happens and is still recorded, but §5.4's rows ride
`WorkingMemoryState.recalled`, and with no ledger there is no section to ride, so the round is
measurably *offered nothing*. That is the arm's finding, not a bug to hide.

## Two orderings that are deliberate

* **`memory_write` lands after `episode_end`.** The experience is a claim about how an episode
  finished, and the runtime cannot extract it before the run has said so. A reader that counts
  records *inside* the episode must stop at the `episode_end` sequence number; `evaluation/run.py`
  writes the summary first and the store row second for the same reason.
* **the recall is paid for out of its own pool, and lands after the debts.** See
  `WorkingMemory.render`: §5.4's rows are charged to `RECALL_BUDGET_CHARS` and the debt block keeps
  `RENDER_BUDGET_CHARS` untouched, so a memory can never starve the list of promises this run owes
  — which is the failure §5.3 exists to fix, and the reason the two numbers are declared apart
  rather than shared. The counts say how much of each pool a round spent.

## Two limitations, named here rather than found later

`contradicted_current_state` is computed against the predicates **this episode's plan carries**. The
verdicts come from the episode's own verifier, which is the only instrument this arm is allowed to
read, and a recalled predicate with no row in this plan therefore cannot be contradicted — so the
negative-transfer guard is a floor, not a ceiling. §11's negative-transfer measurement has to be
built on tasks whose memory predicates *are* rows of the later plan (which is what a "similar
task" means in §13's RQ3), and P3-d's task set is written to that constraint rather than discovering
it as a surprise.

The second one is the same fact seen from the other side, and it was measured before it was believed:
**a seed/read episode pair on one task id resets the world, so the fresh snapshot refutes every
placement the successful row recorded.** On `dev_c1` the probe's rounds measured
`placed:obj_blue_2:tray_right` and its two siblings `unknown` at round 1 (nothing verified yet), all
three `false` at round 2, two still `false` at rounds 3–4, one at rounds 5–6, and all three `true`
again at round 7 once the episode had redone the work — so the guard fired on five of seven rounds
and a row-level veto would have made `episodic.policy` indistinguishable from `planning.policy`
across most of the contrast P3 exists to run. The memory would be shown, labelled, and never able to
matter, which is the unfalsifiable switch again, one layer up. So the policy declines **per object,
not per row**: an object a remembered step acted on is passed over only when the refuted claims
about *that* object are none of the debts this plan already carries, and the objects it passed over
are filed as `memory_declined` instead of being silently skipped.

What that costs is stated rather than hidden: a memory that misleads about a placement the agent
already owes is followed, because the arm cannot tell "your memory is stale" from "your memory is
wrong about a job you still have to do" without a second layer of judgement. P3-d was written to
find the case where that cost is paid, and what it found is that this environment does not contain
one. Two measurements, both in `evaluation/episodic_tasks.py`'s frozen manifest. Reach: the clause
above needs a predicate refuted *and* not owed, and on a store of successful episodes a `true`
claim is refuted only while its row is unsatisfied — i.e. only while it is owed — so the branch
fires in no round of any pair there (0 declines across 4 pairs × 2 arms). A *failed* episode does
record `placed:X:R=false`, so `work/p3d_decline_reachability.py` built that store — 6 long-horizon
episodes, 2 false claims, both from the one `partial` case — and re-ran the same six cases against
it in both arms: 12 episodes, 0 declines, 0 divergent cases. The claim is refuted only by a reader
that measures `true`, which means only by a reader that finishes work the writer could not, and one
deterministic policy repeats the other's failure. It is exercised in
`tests/contract/test_v02_episodic_arm.py` on a hand-built recall, which is the only place its input
can be made.
Cost: a wrong order has to *fail* somewhere to be negative transfer, and walking every placement
order of 15 flat place-each-object cases (78 orders, `work/p3d_order_cost_scan.py`) completed 78 of
78 with 0 refused calls — `_respect_region_capacity` and `MAX_OBJECTS_PER_REGION = 3` forbid the one
physical channel. `retrieved but declined` is therefore a row §11 reports as unobserved here, not a
row this set can produce; a costed version needs dependency-bearing work, where a wrong order fails
verification.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.contracts import ExecutionFeedback, WorldState
from ..core.runtime import Runtime
from ..core.v02 import Unknownable
from ..perception.arm import PerceptRuntime
from ..planning.arm import PLANNING_ARMS, PlanningMixin
from ..planning.working_memory import RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS, predicate_of
from .experience import _truth, experience_from_episode
from .retrieval import DEFAULT_LIMIT, mark_used, query_from, render_rows, retrieve
from .store import ExperienceStore

#: the arms this phase can be checked against. `full` means every module §9 names for *this*
# phase, which is P2's four plus the memory one; the VLM claim stays P5's, for the reason
# `build_planning_arm` already records.
EPISODIC_ARMS = PLANNING_ARMS + ("wo_episodic_memory",)


class EpisodicMixin:
    """`PlanningMixin` with an experience store connected to the round, and disconnectable."""

    #: no store is the same answer as a store with nothing in it as far as *retrieval* is
    # concerned, and a different answer as far as the record is: with no store installed there is
    # no `memory_retrieval` event either, because nothing was looked up.
    experience_store: Optional[ExperienceStore] = None
    #: the §5.4 任务上下文的 kind term — the frozen set name. `Runtime` does not know which
    # batch it is running, and inventing a kind from the utterance would be this module's opinion
    # about the task rather than the experiment's fact about the set, so the caller names it.
    memory_task_kind: str = ""
    memory_limit: int = DEFAULT_LIMIT
    #: rows retrieved on the current round, before the render budget decided how many were shown.
    # Rebound, never mutated, so the class-level empty list below is a default and not a channel
    # between episodes.
    retrieved: list[Any] = []

    # ------------------------------------------------------------------ episode ----
    def _begin_episode(self) -> None:
        super()._begin_episode()
        self.retrievals = 0
        self.retrieved_rows = 0
        self.used_rows = 0
        self.written = 0
        self.retrieved = []

    # ------------------------------------------------------- §7 step 2: the recall ----
    def _offer_memories(self, state, world: WorldState,
                        round_index: int) -> Optional[list[tuple[Any, str]]]:
        """Ask the store what it has that bears on this round, and hand the answer to the ledger.

        Returns `(row, line)` pairs in the arm's own order — relevance descending, which is
        `retrieve()`'s order — or `None` when this arm has no memory module. `[]` is a different
        return and says so in the log: it means the store was queried and nothing in it was
        relevant, which is §11's *retrieval relevance* denominator.
        """
        if self.experience_store is None or not self._on("episodic_memory"):
            return None
        query, measured = self._memory_query(state, world)
        records = self.experience_store.all()
        rows = retrieve(query, records, measured=measured, limit=self.memory_limit,
                        round_index=round_index)
        self.retrievals += 1
        self.retrieved_rows += len(rows)
        self.retrieved = list(rows)
        lines = render_rows(rows, {r.experience_id: r for r in records})
        self._file("memory_retrieval", {
            "round_index": round_index,
            "query_terms": sorted(query),
            "task_kind": self.memory_task_kind,
            "store_path": self.experience_store.path,
            "store_size": len(records),
            "store_fingerprint": self.experience_store.fingerprint(),
            "limit": self.memory_limit,
            # The contradiction check's other input, filed with its output: a reader who wants to
            # know why a row was *not* flagged has to be able to see what this snapshot had
            # measured, and "it had not looked" is a different answer from "it looked and agreed".
            "measured_predicates": measured,
            "retrieved": [row.model_dump(mode="json") for row in rows],
            "state_version": world.state_version,
            "observation_ref": world.observation_ref})
        return list(zip(rows, lines))

    def _memory_query(self, state, world: WorldState) -> tuple[set[str], dict[str, str]]:
        """The query, out of records this round already holds — and the verdicts to check against.

        Every term comes from a row, a goal assignment or a booked failure; none of it is guessed
        from the utterance. `measured` is the second return and the guard's whole evidence base:
        predicate id -> `true`/`false`/`unknown` as *this episode's verifier* answered it, read off
        the plan rows the loop already has.
        """
        plan = getattr(self, "plan", None)
        rows = list(plan.subgoals) if plan is not None else []
        predicates = [p for p in (predicate_of(s) for s in rows) if p]
        regions = sorted({p.rsplit(":", 1)[-1] for p in predicates if p.count(":") == 2}
                         | {str(t.target_id) for t in world.targets if t.target_id})
        kinds: set[str] = set()
        goal = getattr(self, "goal", None)
        for assignment in (goal.assignments if goal is not None else []):
            kinds |= _kind_terms(getattr(assignment, "entity", None))
        for entity in world.entities:
            kinds |= _kind_terms(entity)
        codes = sorted({str(f.failure_code) for f in state.failed_attempts
                        if str(f.failure_code or "")}) if state is not None else []
        query = query_from(task_kind=self.memory_task_kind, regions=regions, kinds=sorted(kinds),
                           predicates=predicates, failure_codes=codes)
        measured = {predicate_of(s): _truth(s.satisfied.value) for s in rows
                    if predicate_of(s)}
        # `_truth` is imported from the extractor rather than restated here because the two sides
        # of the contradiction check must speak one vocabulary: a `pending` read as `false` on this
        # side would talk the agent out of a memory that was never refuted, which is the exact
        # confusion `Status`'s docstring forbids and P2 spent a defect learning.
        return query, measured

    # -------------------------------------------------- §7 step 10: was it used? ----
    def _record_feedback(self, fb: ExecutionFeedback) -> None:
        """Judge the recalls against the action that happened, then record the judgement.

        Booked after `super()`, which is where the ledger settles the debt against the post-action
        world: the same feedback that closes a promise is the evidence for whether a remembered
        step was repeated, and reading the two from different snapshots would let a row be
        discharged and *used* against two different worlds.
        """
        super()._record_feedback(fb)
        if self.experience_store is None or not self._on("episodic_memory") or not self.retrieved:
            return
        store = getattr(self, "memory", None)
        has_page = store is not None and self._on("working_memory")
        shown = ([row for row in self.retrieved
                  if any(row is kept for kept in store.state.recalled)] if has_page else [])
        executed = self._executed(fb)
        # Only the rows that reached the page are judged. A row the budget cut cannot have been
        # used, and marking it `unknown` with the reason is the difference between "the agent
        # ignored this memory" and "this arm never showed the agent this memory" — the first is a
        # measurement about the decision maker and the second is a measurement about the arm, and
        # §11's relevance row has to be the first one.
        mark_used(shown, self.experience_store, executed)
        admitted = {id(row) for row in shown}
        for row in self.retrieved:
            if id(row) in admitted:
                continue
            row.used_by_model = Unknownable(
                value="unknown",
                unmeasured=["this round's page did not carry the row ("
                            + ("no working-memory section on this arm" if not has_page
                               else "left out by the render budget")
                            + "), so no action it took can be read as using it"])
        used = [row for row in shown if row.used_by_model.value is True]
        self.used_rows += len(used)
        self._file("memory_use", {
            "round_index": self.current_round, "decision_id": fb.decision_id,
            "status": str(fb.status), "executed": [dict(d) for d in executed],
            "retrieved": [row.experience_id for row in self.retrieved],
            # `shown` and `retrieved` are different lists whenever the render budget cut a row,
            # and the difference is the fact §11's relevance row needs: an experience that was
            # retrieved but never displayed cannot have been used, and counting it as a miss
            # would blame the model for a budget.
            "shown": [row.experience_id for row in shown],
            "used": [row.experience_id for row in used],
            "judgements": {row.experience_id: {
                "value": str(row.used_by_model.value),
                "evidence_refs": list(row.used_by_model.evidence_refs),
                "unmeasured": list(row.used_by_model.unmeasured)} for row in self.retrieved},
            "post_observation_ref": fb.post_observation_ref})

    def _executed(self, fb: ExecutionFeedback) -> list[dict[str, Any]]:
        """The one action this feedback is about, in the shape `mark_used` compares.

        A decision `PlanValidator` refused never became an action, so it contributes nothing: an
        unexecuted `place` is not evidence that a remembered `place` was repeated. A round that
        produced no decision at all is recorded as `unknown` by `mark_used` for the same reason.
        """
        decision = self.decision_by_id.get(str(fb.decision_id or ""))
        execute = getattr(decision, "execute", None) if decision is not None else None
        if execute is None or str(fb.status) == "rejected":
            return []
        return [{"skill": execute.skill, "args": dict(execute.args or {})}]

    # ---------------------------------------------------------------- the residue ----
    def write_experience(self, summary: dict[str, Any], *,
                         run_ref: str = "") -> Optional[Any]:
        """Store this episode's residue, and file the record that says it was stored.

        `summary` is the in-memory `episode_summary.json` payload, so the online write and the
        offline re-read of the archive call the extractor with the same arguments. A store that
        already holds this id refuses the append (`store._put`) rather than rewriting it — a second
        repeat of the same episode must be visible as a crash in the run record, not as a row that
        quietly changed under a citation.
        """
        store = self.experience_store
        if store is None or not self._on("episodic_memory"):
            return None
        events = self.store.read_all()
        experience = experience_from_episode(
            summary, events, run_ref=(run_ref
                                      or str((summary.get("artifacts") or {})
                                             .get("episode_dir") or "")))
        store.append(experience)
        self.written += 1
        self._file("memory_write", {
            "experience_id": experience.experience_id,
            "episode_id": experience.episode_id,
            "experience": experience.model_dump(mode="json"),
            "store_path": store.path, "store_size": len(store),
            "store_fingerprint": store.fingerprint(),
            # Named in the payload rather than left to the sequence number, so a reader who sorts
            # the log by type alone cannot pool this record with the ones inside the episode.
            "written_after": "episode_end"})
        return experience


def _kind_terms(spec: Any) -> set[str]:
    """The `shape:color` term an entity or assignment contributes, in the stored vocabulary.

    Read from `attributes`, which is where the task case puts the public identity of an object and
    what `experience._kinds_of` recorded, so the query side and the index side speak one language.
    An entity with no shape has no kind term; a colour alone is not a kind either, and is not
    invented into one.
    """
    attributes = dict(getattr(spec, "attributes", {}) or {})
    shape = str(attributes.get("shape") or "")
    if not shape:
        return set()
    return {f"{shape}:{attributes.get('color', '')}"}


class ExperiencedPlannedRuntime(EpisodicMixin, PlanningMixin, Runtime):
    """The privileged loop with the plan, the ledger and an experience store on the page.

    Bases are declared once, here, for the reason `planning/arm.py` declares its two: which loop
    ran has to be answerable from a class name, and an ad-hoc mixture of three mixins built at a
    call site is not answerable at all.
    """

    def __init__(self, *args, ablation=None, experience_store: Optional[ExperienceStore] = None,
                 memory_task_kind: str = "", memory_limit: int = DEFAULT_LIMIT,
                 memory_recall_budget: int = RECALL_BUDGET_CHARS, **kw):
        super().__init__(*args, memory_recall_budget=memory_recall_budget, **kw)
        self.ablation = ablation
        self.experience_store = experience_store
        self.memory_task_kind = memory_task_kind
        self.memory_limit = int(memory_limit)


class ExperiencedPerceptRuntime(EpisodicMixin, PlanningMixin, PerceptRuntime):
    """The camera loop with the episodic arm installed.

    No `__init__` of its own beyond the store: `PerceptRuntime.__init__` already takes `ablation`,
    checks the claim against the channel and files the `ablation` record, so this arm does not
    file the claim a second time.
    """

    files_ablation_claim = False

    def __init__(self, *args, experience_store: Optional[ExperienceStore] = None,
                 memory_task_kind: str = "", memory_limit: int = DEFAULT_LIMIT,
                 memory_recall_budget: int = RECALL_BUDGET_CHARS, **kw):
        super().__init__(*args, memory_recall_budget=memory_recall_budget, **kw)
        self.experience_store = experience_store
        self.memory_task_kind = memory_task_kind
        self.memory_limit = int(memory_limit)


def build_episodic_arm(*, case, scene, executor, store, run_dir: str, episode_id: str,
                       budgets, experience_store: Optional[ExperienceStore] = None,
                       task_kind: str = "", memory_limit: int = DEFAULT_LIMIT,
                       perceive: str = "privileged", ablation=None, perceiver=None, gmap=None,
                       environment=None, task_planner=None,
                       memory_render_budget: int = RENDER_BUDGET_CHARS,
                       memory_recall_budget: int = RECALL_BUDGET_CHARS) -> Runtime:
    """Assemble the memory arm in one place, so a runner cannot build a half-connected loop.

    Unlike `planning/arm.py:build_planning_arm` this function takes `experience_store` and
    `task_kind` rather than `ablation` alone, because §5.4's module is not switchable by an
    `Ablation` record on its own: with no store installed there is nothing to retrieve from, and
    an episode that *claims* the memory module is on while holding no store would measure a
    cold start and report it as a memory. `cli.py` refuses that combination; this function accepts
    it (an empty store is a legitimate cold start, a *missing* one is not) and the distinction is
    tested at the entry that has to make it.

    The camera path needs a finished `perceiver` and `gmap` for the reason
    `build_planning_arm` records: neither can be reconstructed from what this function is handed.
    """
    if perceive == "privileged":
        return ExperiencedPlannedRuntime(
            scene, executor, run_dir, budgets, episode_id, config=case.verify,
            environment=environment, store=store, ablation=ablation, task_planner=task_planner,
            memory_render_budget=memory_render_budget,
            memory_recall_budget=memory_recall_budget, experience_store=experience_store,
            memory_task_kind=task_kind, memory_limit=memory_limit)
    if perceiver is None or gmap is None:
        raise ValueError(
            f"the camera episodic arm needs both `perceiver` and `gmap` (got "
            f"perceiver={type(perceiver).__name__}, gmap={type(gmap).__name__}): build them with "
            f"`perception/arm.py:build_perceiver` and pass them in, or run the memory contrast "
            f"on `perceive='privileged'`, which spends nothing")
    return ExperiencedPerceptRuntime(
        scene, executor, run_dir, budgets, episode_id, perceiver=perceiver, gmap=gmap,
        ablation=ablation, config=case.verify, environment=environment, store=store,
        task_planner=task_planner, memory_render_budget=memory_render_budget,
        memory_recall_budget=memory_recall_budget,
        experience_store=experience_store, memory_task_kind=task_kind,
        memory_limit=memory_limit)


__all__ = ["EPISODIC_ARMS", "EpisodicMixin", "ExperiencedPerceptRuntime",
           "ExperiencedPlannedRuntime", "build_episodic_arm"]
