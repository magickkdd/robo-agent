"""Working memory: the debts that outlive the feedback window (SPEC-v0.2 §5.3).

v0.1 had pieces of this in two places and the whole of it nowhere. `Runtime` kept
`recent_feedbacks` (a window of the last few results) and `AttemptRecord`s, so a commitment
made on round 3 was simply off the page by round 16, and the one place a plan said "put it
back before you finish" was a sentence in a summary that got re-rendered away. §5.3 asks for
the opposite: a live record of 当前目标与子目标 / 已完成-未完成-未知 / 当前承诺与暂存关系 /
恢复义务 / 已失效假设 / 关键失败尝试 / 当前计划摘要 — and, in the clause that makes it a
research question rather than a data structure, 它必须在线参与决策: it has to be in front of
the model when the model decides.

Three rules carry the weight, and each one refuses something easier:

1. **A debt closes on an instrument, never on the agent's own say-so.** A commitment is
   `discharged` only when the episode's verifier reports the predicate it named true, with the
   refs that report produced. `SkillResult.status == "success"` is §5.8's layer 1 — what
   happened — and is not evidence that the world now has the shape the commitment claimed. An
   unmeasured commitment stays `unknown` and is named at the end; §5.3's 恢复义务 is exactly
   the kind of row that otherwise disappears unremarked.
2. **Nothing here chooses.** The module writes records and renders a payload. Which obligation
   to work on, whether to retry, whether to replan: all of it belongs to the decision source
   (§3.4). `current_subgoal_id` is therefore *the row the last decision named*, not the row the
   planner would have preferred, and a decision that named no open row is recorded as one.
3. **Every row says where it came from.** The decision, row key, predicate and observation ref
   each claim rests on are in `provenance` and the envelope. A report that cannot answer "who
   said this, against which frame?" is a rendering, not evidence.

`Assumption` is deliberately the narrowest row: the only belief recorded here is that *a
prerequisite of the row the decision worked* was already true while no instrument had answered
it. Not the row's own predicate — working a subgoal that is not yet satisfied is the ordinary
case, and recording it as an assumption would put a refutable "belief" behind every act in the
episode, which is §11's stale-memory count turned into a count of decisions. The distinction
costs nothing to check and is the one that matters: a model that places into a tray it cannot
see is reasoning on an unanswered question, and when the answer arrives and says the tray was
not clear, the row names the round the agent kept reasoning on a refuted premise.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Sequence

from ..core.contracts import (
    Decision,
    ExecutionFeedback,
    PredicateVerdict,
    SkillStatus,
    WorldState,
)
from ..core.v02 import (
    Assumption,
    Commitment,
    FailedAttempt,
    Status,
    Subgoal,
    SuspendedRelation,
    TaskPlan,
    Unknownable,
    WorkingMemoryState,
)
from .subgoals import occupants, row_key
from .task_planner import Refresh, evidence_for

#: Skills that gather information and move nothing. A failure followed by one of these is
#: `re_observe` (§5.7's list): the agent went looking rather than pushing again.
OBSERVE_SKILLS = ("observe", "alfred_examine", "alfred_inventory")

#: characters of working memory the decision payload may spend. Bounded so a long episode
#: cannot grow the prompt by growing its debt list — and, unlike a plan summary, the render
#: drops *detail* and never the counts, so a shortened block still reports the whole truth
#: about how much was left out.
RENDER_BUDGET_CHARS = 900

#: characters of the same page that §5.4's recalled experiences may spend, in their *own* pool.
#: Declared separately from `RENDER_BUDGET_CHARS` because one pool is provably the wrong shape:
#: measured on a real six-step `dev_c1` experience, one rendered recall line cost 717 characters
#: and the debt block's own apology ~119, so inside a shared 900-character page the recall lost to
#: the reservation every round of every `full` run — §5.4's module would exist in the code and be
#: unobservable in the configuration, which is the same defect as a switch no experiment can read.
#:
#: Sized from that measurement rather than picked. After `episodic/retrieval.py:render_rows`
#: stopped repeating what the structured row beside the line already says (the matched-term list,
#: and the contradiction sentence per refuted claim), the same recall renders 572 characters with
#: the guard quiet and 748 with the three claims a reset world refutes. `DEFAULT_LIMIT` is 3, so
#: the pool that lets the retrieval limit — not the page — decide how many memories a round
#: carries is 3 x 748 + 118 = 2362, rounded up. A smaller number is not a cheaper design; it is a
#: second unfalsifiable switch. And because the text is not the whole cost,
#: `counts.recall_json_chars` reports what the admitted rows' structured copies add, so no reader
#: has to mistake `recalled_chars` for the section's price.
RECALL_BUDGET_CHARS = 2400

#: statuses an executed action may report and still be called an attempt that worked.
#:
#: The first entry is the contract's own success value (`SkillStatus.completed`), and it has to
#: be spelled out because the loop never emits the strings after it: a measured batch of 12
#: text-channel and 16 desktop episodes showed this ledger filing exactly one row per round in
#: episodes with zero rejections, which made §11's `repeated failures` read as a property of the
#: layer instead of a property of the behaviour. `evaluation/long_horizon.py:388 _completed` was
#: already guarding the same drift for its own rows; the guard belonged here too.
_OK = (SkillStatus.completed.value, "success", "ok", "succeeded")

#: feedbacks that report a control outcome rather than a move. `Runtime` emits these from
#: `core/runtime.py:442` when a finish claim is accepted, and an accepted claim is not an attempt
#: that failed. `finish_rejected` is deliberately absent: a refused completion is exactly the
#: row the next round should see.
_NOT_A_MOVE = ("finish_accepted",)


def predicate_of(sub: Optional[Subgoal]) -> str:
    """The predicate a row claims, or "" for a row that claims a sentence instead."""
    return str((sub.predicate_id or "") if sub is not None else "")


def row_for(plan: TaskPlan, ref: str) -> Optional[Subgoal]:
    """Find a row by any of the four names a decision can legitimately use for it.

    `Decision.execute.subgoal` is free text, and in the frozen dev rounds it carried a
    `subgoal_id`, a predicate id and a row statement. Resolving all three is not choosing for
    the model — it is reading what it named. A name that resolves to nothing stays unresolved
    and is reported as such; it is never mapped onto the nearest-looking row, which is how a
    plan would otherwise launder a wrong reference into a plausible one.
    """
    if not ref:
        return None
    for sub in plan.subgoals:
        if ref in (sub.subgoal_id, predicate_of(sub), sub.statement) or row_key(sub) == ref:
            return sub
    return None


class WorkingMemory:
    """The episode's live ledger of beliefs and debts (§5.3's seven lists)."""

    def __init__(self, *, episode_id: Optional[str] = None,
                 render_budget: int = RENDER_BUDGET_CHARS,
                 recall_budget: int = RECALL_BUDGET_CHARS):
        self.render_budget = int(render_budget)
        self.recall_budget = int(recall_budget)
        self.state = WorkingMemoryState(episode_id=episode_id)
        # Rows this ledger already wrote for a given (decision, row) pair, so a re-sync of the
        # same round cannot double-book a debt.
        self._seen_commitments: set[str] = set()
        self._seen_assumptions: set[str] = set()
        self._suspended: dict[str, str] = {}
        self._labels: dict[str, str] = {}

    # ------------------------------------------------------------------ §7 step 2 ----
    def sync(self, plan: TaskPlan, world: WorldState, *, verifier, round_index: int,
             revision: Optional[Refresh] = None) -> WorkingMemoryState:
        """Re-read every open debt against the instruments, then refresh the plan's view.

        Called once per round before the decision context is built, which is what makes the
        memory *online*: a commitment made on round 3 is still on the page on round 40, and it
        leaves the page only when something measured it away.
        """
        st = self.state
        st.round_index = round_index
        st.plan_version = plan.version
        st.plan_summary = plan.summary
        st.subgoal_status = {row_key(s): s.status for s in plan.subgoals}
        # Keys are hashes for the rows that carry no predicate, which is right for a record and
        # useless in a prompt: the label is what the model can read, so the render keeps it.
        self._labels = {row_key(s): (predicate_of(s) or s.statement) for s in plan.subgoals}
        st.based_on_state_version = world.state_version
        st.based_on_observation_ref = world.observation_ref

        unknown: list[str] = []
        questions: list[str] = []
        for sub in plan.subgoals:
            why = [p for p in sub.preconditions if not p.startswith("[history] ")]
            if sub.satisfied.value == "unknown":
                unknown.append(f"{predicate_of(sub) or sub.statement} is unmeasured in "
                               f"{world.observation_ref or 'this snapshot'}: "
                               f"{'; '.join(why) or 'no reason recorded'}")
            if sub.kind == "observe" and sub.status is not Status.done:
                questions.append(f"{sub.statement} — asked on "
                                 f"{sub.based_on_observation_ref or 'an earlier round'}, "
                                 f"answered by no frame yet")
        st.unknown_facts = sorted(set(unknown))
        st.open_questions = sorted(set(questions))

        self._discharge(plan, world, verifier, round_index)
        self._restore(plan, world, verifier, round_index)
        self._invalidate(plan, world, verifier, round_index)

        if revision is not None and (revision.version_bumped or revision.reopened
                                     or revision.violations):
            # A request the loop must honour by re-offering the planner next round. The
            # planner is re-offered every round anyway in this design, so honouring it is
            # bookkeeping, never a strategy choice (§3.4).
            st.pending_replan = True
            st.replan_reason = (plan.revisions[-1].rationale if plan.revisions
                                else "the plan's graph changed")
        st.provenance.update({
            "author": "planning/working_memory.py",
            "goal_ref": plan.goal_ref,
            "plan_version": plan.version,
            "plan_open_obligations": plan.open_obligation_count,
            "unresolved_obligations": st.unresolved_obligations(),
        })
        return st

    # ------------------------------------------------- closing debts on evidence ----
    @staticmethod
    def _answer(sub: Optional[Subgoal], world: WorldState, verifier):
        """The instrument's answer for the row a claim was made about, or None.

        A claim whose row has left the plan cannot be re-read here: `evidence_for` needs the
        row's own entity/region pairing, and rebuilding one by parsing a predicate string would
        be this module writing a measurement it did not take.
        """
        if sub is None:
            return None
        return evidence_for(sub, verifier, world)

    def _row_of(self, plan: TaskPlan, provenance: dict) -> tuple[Optional[Subgoal], str]:
        pid = str(provenance.get("predicate_id") or "")
        return (row_for(plan, str(provenance.get("row_key") or "")) or row_for(plan, pid), pid)

    def _discharge(self, plan: TaskPlan, world: WorldState, verifier, round_index: int):
        """A commitment is `discharged` only by the measurement it named (§5.8 layer 2)."""
        for c in self.state.commitments:
            if c.discharged.value is True:
                continue
            sub, pid = self._row_of(plan, c.provenance)
            if sub is None or not pid:
                c.discharged = Unknownable(
                    unmeasured=[f"{pid or c.text} names no row this plan still carries, so "
                                f"no channel on this arm can discharge it"])
                continue
            answer = self._answer(sub, world, verifier)
            if answer is None:
                continue
            verdict, refs, unmeasured = answer
            if verdict is PredicateVerdict.true:
                c.discharged = Unknownable(value=True, evidence_refs=list(refs))
            elif verdict is PredicateVerdict.false:
                c.discharged = Unknownable(value=False, evidence_refs=list(refs))
            else:
                c.discharged = Unknownable(unmeasured=list(unmeasured) or
                                           ["the verifier returned unknown without a reason"])
            c.provenance["settled_at_round"] = round_index

    def _restore(self, plan: TaskPlan, world: WorldState, verifier, round_index: int):
        """A suspended relation is `restored` when its predicate measures true again."""
        for r in self.state.suspended_relations:
            if r.restored.value is True:
                continue
            sub, pid = self._row_of(plan, r.provenance)
            answer = self._answer(sub, world, verifier)
            if answer is None:
                continue
            verdict, refs, unmeasured = answer
            if verdict is PredicateVerdict.true:
                r.restored = Unknownable(value=True, evidence_refs=list(refs))
                r.provenance["restored_at_round"] = round_index
                self._suspended.pop(pid, None)
            elif verdict is PredicateVerdict.false:
                r.restored = Unknownable(
                    unmeasured=[f"{pid} still measures false at "
                                f"{world.observation_ref or 'this snapshot'}"])
            else:
                r.restored = Unknownable(unmeasured=list(unmeasured) or
                                         ["the verifier returned unknown without a reason"])

    def _invalidate(self, plan: TaskPlan, world: WorldState, verifier, round_index: int):
        """An assumption the instruments now contradict is marked, not deleted."""
        for a in self.state.assumptions:
            if a.invalidated_at_round is not None:
                continue
            sub, pid = self._row_of(plan, a.provenance)
            answer = self._answer(sub, world, verifier)
            if answer is None:
                continue
            verdict, refs, _un = answer
            if verdict is PredicateVerdict.unknown:
                continue
            believed = str(a.provenance.get("believed") or "true") == "true"
            if (verdict is PredicateVerdict.true) is believed:
                a.holds = Unknownable(value=True, evidence_refs=list(refs))
            else:
                a.holds = Unknownable(value=False, evidence_refs=list(refs))
                a.invalidated_at_round = round_index
                a.invalidated_by_ref = world.observation_ref or (refs[0] if refs else "")
                a.provenance["contradicted_by"] = f"{pid} measured {verdict.value}"

    # ---------------------------------------------------------------- §7 steps 6-7 ----
    def commit(self, decision: Decision, plan: TaskPlan, world: WorldState, *,
               subgoal: Optional[Subgoal] = None) -> WorkingMemoryState:
        """Write down what the agent just said it would do, and what it did about the last failure.

        The row recorded is the one the *decision* named (rule 2). `expected_effect` is the
        model's own claim about what will be true afterwards, and it becomes the commitment's
        text verbatim so a later report can quote the agent rather than paraphrase it.
        """
        st = self.state
        ex = decision.execute
        ref = str((ex.subgoal if ex is not None else "") or "")
        row = subgoal if subgoal is not None else row_for(plan, ref)

        self._classify_follow_up(decision, st)
        st.current_subgoal_id = row.subgoal_id if row is not None else None
        if st.pending_replan and row is not None:
            st.pending_replan = False
            st.replan_reason = ""

        if decision.action != "execute" or ex is None:
            if decision.action == "finish":
                # A completion claim is not a debt with a predicate: the thing that can check
                # it is §5.8's independent evaluator, whose input must stay isolated. So the
                # claim is filed as the *round it happened*, and `obligation_check` reads it
                # back against the open rows — naming it a commitment here would either be
                # permanently unresolved or need an instrument that does not exist.
                st.provenance["claimed_complete_round"] = st.round_index
                st.provenance["claimed_complete_decision_id"] = decision.decision_id
                st.provenance["claimed_complete_evidence_refs"] = list(decision.evidence_refs)
            return st

        pid = predicate_of(row)
        text = (decision.expected_effect or (row.statement if row is not None else "") or
                f"{ex.skill}({', '.join(f'{k}={v}' for k, v in sorted((ex.args or {}).items()))})")
        key = f"{decision.decision_id}:{row.subgoal_id if row else ''}"
        if key not in self._seen_commitments:
            self._seen_commitments.add(key)
            st.commitments.append(Commitment(
                text=text, subgoal_id=(row.subgoal_id if row else None),
                made_at_round=st.round_index, made_at_decision_id=decision.decision_id,
                evidence_refs=list(decision.evidence_refs),
                based_on_state_version=world.state_version,
                based_on_observation_ref=(decision.evidence_refs[0] if decision.evidence_refs
                                          else world.observation_ref),
                provenance={"row_key": (row_key(row) if row else ""), "predicate_id": pid,
                            "decision_id": decision.decision_id,
                            "authored_by": "planning/working_memory.py:WorkingMemory.commit"}))

        if row is not None:
            for prereq in self._prerequisites(plan, row):
                pid_p = predicate_of(prereq)
                if prereq.satisfied.value != "unknown" or not pid_p:
                    continue
                akey = f"{decision.decision_id}:{prereq.subgoal_id}"
                if akey in self._seen_assumptions:
                    continue
                self._seen_assumptions.add(akey)
                st.assumptions.append(Assumption(
                    text=f"{decision.decision_id} worked "
                         f"{pid or ex.skill} as if {pid_p} were already true, and no instrument "
                         f"had answered it at v{world.state_version}",
                    supporting_refs=list(decision.evidence_refs) or
                    ([world.observation_ref] if world.observation_ref else []),
                    based_on_state_version=world.state_version,
                    based_on_observation_ref=world.observation_ref,
                    provenance={"row_key": row_key(prereq), "predicate_id": pid_p,
                                "believed": "true", "decision_id": decision.decision_id,
                                "worked_row_key": row_key(row)}))
        return st

    @staticmethod
    def _prerequisites(plan: TaskPlan, row: Subgoal) -> list[Subgoal]:
        """The rows this one waits for, by either of the two names the plan uses.

        `Subgoal.depends_on` and `Dependency` are filled by the same derivation and agree, but a
        shape-1 block is recorded as an edge only (§5.2's `dependencies` is a description of the
        constraint, not a second queue), so reading only the field would miss it.

        A `maintain` row is excluded on purpose: it is a constraint the world is asked to keep,
        never a fact an agent can choose to believe, and it is measured by no instrument, so
        treating it as an assumption would add a permanent row to the ledger on every decision.
        """
        live = {s.subgoal_id: s for s in plan.subgoals}
        ids = list(row.depends_on) + [d.from_subgoal_id for d in plan.dependencies
                                      if d.on_subgoal_id == row.subgoal_id]
        return [live[i] for i in dict.fromkeys(ids)
                if i in live and live[i].kind != "maintain"]

    def _classify_follow_up(self, decision: Decision, st: WorkingMemoryState):
        """Say what the agent did about the last unanswered failure — from its action, not its prose.

        §11's `repeated failures` and `recovery/replanning effectiveness` are both read off
        this field, so it is judged from the decision payload alone: same skill, same arguments
        and same slot is a retry; a different slot is a candidate change; an information skill
        is a re-observe. §12.3 (以实际动作和最终状态为准) is why `rationale` is never consulted
        — a model that writes "let me try another slot" and re-sends the identical call
        retried, and the log has to be able to show that.
        """
        pending = [f for f in st.failed_attempts if f.followed_by == "nothing"]
        if not pending:
            return
        last = pending[-1]
        ex = decision.execute
        if decision.action != "execute" or ex is None:
            followed = "give_up"
        elif ex.skill in OBSERVE_SKILLS:
            followed = "re_observe"
        elif st.pending_replan:
            followed = "replan"
        elif ex.skill != last.skill:
            followed = "abandon"
        elif dict(ex.args or {}) == dict(last.args or {}) and \
                str(ex.candidate_id or "") == str(last.provenance.get("candidate_id") or ""):
            followed = "retry"
        else:
            followed = "change_candidate"
        last.followed_by = followed
        last.evidence_refs = sorted(set(last.evidence_refs) | set(decision.evidence_refs))

    # --------------------------------------------------------------- §7 steps 9-10 ----
    def settle(self, decision: Optional[Decision], feedback: Optional[ExecutionFeedback],
               world: WorldState, *, plan: TaskPlan, verifier,
               round_index: Optional[int] = None) -> WorkingMemoryState:
        """Record what happened: the attempt that failed, and the relation a move suspended."""
        if round_index is not None:
            self.state.round_index = round_index
        if feedback is not None:
            self._record_attempt(decision, feedback, world)
            self._record_suspended(plan, world)
        self._discharge(plan, world, verifier, self.state.round_index)
        self._restore(plan, world, verifier, self.state.round_index)
        self._invalidate(plan, world, verifier, self.state.round_index)
        return self.state

    def _record_attempt(self, decision: Optional[Decision], feedback: ExecutionFeedback,
                        world: WorldState):
        """One row per key failure, in the layer's own words (§5.8, §10.3).

        A runtime rejection never reached the executor; an action that ran and did not verify
        did. Collapsing them would turn "the model asked for something impossible" into "the
        skill failed", which is a different defect and needs a different fix.
        """
        ex = decision.execute if decision is not None else None
        rejected = bool(feedback.rejection_reasons) and not feedback.executed
        status = str(feedback.status or "")
        if status in _NOT_A_MOVE:
            return
        if status in _OK and not rejected:
            return
        args = dict(ex.args or {}) if ex is not None else {}
        st = self.state
        st.failed_attempts.append(FailedAttempt(
            round_index=st.round_index,
            skill=str(feedback.skill or (ex.skill if ex is not None else "")),
            args=args,
            failure_code=feedback.failure_code or
            (feedback.rejection_reasons[0] if feedback.rejection_reasons else None),
            environment_text=feedback.environment_text,
            evidence_refs=[r for r in (feedback.post_observation_ref,
                                       feedback.pre_observation_ref) if r] or
            [world.observation_ref or ""],
            based_on_state_version=world.state_version,
            based_on_observation_ref=feedback.post_observation_ref or world.observation_ref,
            provenance={"executed": bool(feedback.executed),
                        "rejected_by_runtime": rejected,
                        "status": str(feedback.status or ""),
                        "decision_id": feedback.decision_id,
                        "candidate_id": str((ex.candidate_id if ex is not None else "") or
                                            args.get("candidate_id") or ""),
                        "all_reasons": list(feedback.rejection_reasons)}))

    def _record_suspended(self, plan: TaskPlan, world: WorldState):
        """A `recover` debt whose object has measurably left is now a suspended relation.

        The plan already knows which rows are `recover` rows — `subgoals` relabels one only
        when the *instrument* said the region was blocked by that object and the same object
        owes that region — so this is a transfer of an existing claim into the ledger, not a
        new belief. The trigger is evidence: the object must no longer be an occupant of the
        region in this snapshot. Its temporary home is read from the snapshot too, never from
        the plan's intention.
        """
        for row in [s for s in plan.subgoals if s.kind == "recover"]:
            pid = predicate_of(row)
            region = row.target_region_id or ""
            if not pid or not row.target_entity_ids or pid in self._suspended:
                continue
            entity = row.target_entity_ids[0]
            if entity in occupants(world, region):
                continue                # nothing has been displaced in this snapshot
            home = world.entity(entity).supported_by if world.has_entity(entity) else None
            st = self.state
            rel = SuspendedRelation(
                subject_id=entity, predicate="placed", related_id=region,
                was="true", now="false",
                reason=f"{entity} was moved out of {region} to let another object use it"
                       + (f"; this snapshot has it on {home}" if isinstance(home, str) else ""),
                restore_by=f"{row.subgoal_id} (before the task ends)",
                disturbed_at_round=st.round_index,
                based_on_state_version=world.state_version,
                based_on_observation_ref=world.observation_ref,
                provenance={"predicate_id": pid, "row_key": row_key(row),
                            "authored_by": "planning/working_memory.py:WorkingMemory.settle"})
            self._suspended[pid] = rel.relation_id
            st.suspended_relations.append(rel)

    # -------------------------------------------------------------------- read back ----
    def render(self, st: Optional[WorkingMemoryState] = None, *,
               recalled: Optional[Sequence[tuple[Any, str]]] = None) -> dict[str, Any]:
        """The bounded model-facing block: what is owed, and what is still unknown.

        Detail is dropped before counts, and the drop is announced with the number of lines
        left out — a payload that silently shortened a debt list would reproduce the exact
        failure this module exists to fix.

        `recalled` is §5.4's offer: `(row, line)` pairs from the episodic arm, admitted into a
        section of their own and paid for out of `recall_budget`, which is a separate allowance
        from the debt pool rather than a share of it. The separation is what makes the ordering
        claim true instead of merely stated: on a long episode the debts are what this run is
        actually doing, and a memory that could starve `open[...]` lines would hide a promise the
        agent still owes behind a story about a different task. Two pools, and the debt pool is
        arithmetically the one P2 measured — so a page with no memory on it is the page P2 froze.

        `None` means no episodic arm looked, which is not the same fact as an empty list, and the
        returned `recalled` is `None` in that case so the payload can keep the two apart. An empty
        list must not change one character of the debt block: "queried, nothing relevant" is a
        measurement about the store, and it may not arrive as a shrinkage of the ledger.
        """
        st = st or self.state
        open_promises = sum(1 for c in st.commitments if c.discharged.value is not True)
        to_restore = sum(1 for r in st.suspended_relations if r.restored.value is not True)
        lines: list[str] = [
            f"plan v{st.plan_version} round {st.round_index}: "
            f"{st.provenance.get('plan_open_obligations', '?')} subgoal(s) still owed, "
            f"{st.unresolved_obligations()} unresolved obligation(s) in memory "
            f"({open_promises} promise(s), {to_restore} to restore)",
            f"current subgoal: {st.current_subgoal_id or 'none named by the last decision'}",
        ]
        detail: list[str] = []
        detail += [f"open[{self._labels.get(k, k)}] {v.value}"
                   for k, v in sorted(st.subgoal_status.items())
                   if v is not Status.done]
        detail += [f"commitment[{c.commitment_id}] r{c.made_at_round}: {c.text} "
                   f"-> discharged={c.discharged.value}" for c in st.commitments]
        detail += [f"suspended[{r.relation_id}] {r.subject_id} {r.predicate} {r.related_id}: "
                   f"was {r.was}, now {r.now}, restore by {r.restore_by or '(never named)'} "
                   f"-> restored={r.restored.value}" for r in st.suspended_relations]
        detail += [f"assumption[{a.assumption_id}] {a.text} -> holds={a.holds.value}"
                   for a in st.assumptions if a.invalidated_at_round is None]
        detail += [f"REFUTED assumption[{a.assumption_id}] r{a.invalidated_at_round}: {a.text} "
                   f"<- {a.provenance.get('contradicted_by', '')}"
                   for a in st.assumptions if a.invalidated_at_round is not None]
        detail += [f"failed r{f.round_index}: {f.skill} "
                   f"{' '.join(f'{k}={v}' for k, v in sorted(f.args.items()))} "
                   f"[{f.failure_code or ''}] -> then {f.followed_by}"
                   for f in st.failed_attempts]
        detail += [f"unknown: {u}" for u in st.unknown_facts]
        detail += [f"question: {q}" for q in st.open_questions]
        if st.pending_replan:
            detail.append(f"replan owed: {st.replan_reason}")

        # The two pools, spent in order. Everything above this line and the loop below are P2's
        # debt block, arithmetic unchanged; §5.4's lines are appended after it and charged to
        # `recall_budget`, so a page with nothing recalled on it is byte-identical to the page P2
        # froze and a page with something recalled keeps the debt block as its prefix.
        offered = list(recalled or []) if recalled is not None else []

        def notice(n: int) -> str:
            return (f"({n} line(s) dropped by the {self.render_budget}-char working-memory "
                    f"budget; the unresolved count in the first line is not one of them)")

        def recall_notice(m: int) -> str:
            return (f"({m} recalled experience line(s) left out by the "
                    f"{self.recall_budget}-char memory budget; the debts above are what this run "
                    f"owes)")

        uncuttable = sum(len(x) + 1 for x in lines)
        # The announcement is paid for before any detail is admitted, so a block that fits its
        # budget still fits it *with* the apology: a render that honoured 900 characters until
        # the moment it had to say something was left out would overrun on exactly the rounds
        # whose overrun matters.
        room = self.render_budget - uncuttable - len(notice(len(detail))) - 2
        spent = 0
        dropped = 0
        for line in detail:
            if spent + len(line) + 1 > room:
                dropped += 1
                continue
            lines.append(line)
            spent += len(line) + 1
        if dropped:
            lines.append(notice(dropped))
        debt_text = "\n".join(lines)
        debt_lines = len(lines)

        # In the arm's own order (relevance descending), so a row is never shown out of rank
        # because a cheaper one happened to come after it. The apology is reserved only when there
        # is something it could be about: reserving it against an empty offer is how a cold start
        # ended up shrinking the debt pool.
        recall_room = (self.recall_budget - len(recall_notice(len(offered))) - 1) if offered else 0
        admitted: list[Any] = []
        spent = 0
        for row, line in offered:
            if spent + len(line) + 1 > recall_room:
                continue
            lines.append(line)
            spent += len(line) + 1
            admitted.append(row)
        memory_dropped = len(offered) - len(admitted)
        if memory_dropped:
            lines.append(recall_notice(memory_dropped))
        text = "\n".join(lines)
        return {"header": lines[0],
                "lines": lines,
                "text": text,
                "recalled": (admitted if recalled is not None else None),
                "counts": {
                    "commitments": len(st.commitments),
                    "commitments_open": sum(1 for c in st.commitments
                                            if c.discharged.value is not True),
                    "suspended": len(st.suspended_relations),
                    "suspended_open": sum(1 for r in st.suspended_relations
                                          if r.restored.value is not True),
                    "failed_attempts": len(st.failed_attempts),
                    "assumptions": len(st.assumptions),
                    "assumptions_invalidated": sum(1 for a in st.assumptions
                                                   if a.invalidated_at_round is not None),
                    "unknown_facts": len(st.unknown_facts),
                    "open_questions": len(st.open_questions),
                    # §5.4's three numbers, kept apart because §11 asks which of them a row was:
                    # retrieved (offered), shown (admitted), and never reached the page (dropped).
                    # `None` rather than three zeroes when no episodic arm looked.
                    "recalled_offered": (None if recalled is None else len(offered)),
                    "recalled_shown": (None if recalled is None else len(admitted)),
                    "recalled_dropped": (None if recalled is None else memory_dropped),
                    "plan_open_obligations": st.provenance.get("plan_open_obligations"),
                    "unresolved_obligations": st.unresolved_obligations(),
                    # Two numbers the arm has to be able to tell apart: how much of the block the
                    # budget is not allowed to touch, and whether what was emitted still fits.
                    # The second can only be true of a misconfigured arm rather than a long
                    # episode — and it is measured against the *debt* section alone, because on an
                    # arm that shows recalls the whole page is meant to exceed this pool. Which is
                    # why `recall_budget`, `debt_chars` and `page_chars` are all here: `over_budget`
                    # kept P2's meaning and P2's number, so a reader of a `full` run must not have
                    # to guess which section a overrun belongs to.
                    "budget": self.render_budget,
                    "recall_budget": self.recall_budget,
                    "debt_chars": len(debt_text),
                    "page_chars": len(text),
                    "recalled_chars": sum(len(line) + 1 for line in lines[debt_lines:]),
                    # The other half of what an admitted recall costs. `memory_view` publishes the
                    # admitted rows as structured records beside the text, and the row's JSON is
                    # about as long again as its line (a `dev_c1` row's dump is ~900 characters
                    # against a 572-character line), so a reader who took `recalled_chars` for the
                    # section's price would understate the page by half. The pool above gates the
                    # *text* — that is what a character budget over rendered lines means — and this
                    # is the disclosure that the gate is not the whole cost.
                    "recalled_json_chars": sum(
                        len(json.dumps(row.model_dump(mode="json"), ensure_ascii=False))
                        for row in admitted),
                    "uncuttable_chars": uncuttable,
                    "over_budget": len(debt_text) > self.render_budget,
                    "page_over_budget": len(text) > self.render_budget + self.recall_budget},
                "lines_dropped": dropped}

    # ------------------------------------------------------- end-of-task audit ----
    def obligation_check(self, *, status: str, plan: Optional[TaskPlan] = None,
                         final_round: Optional[int] = None) -> dict[str, Any]:
        """What was still owed when the episode stopped (§5.3, §11's Task and Long-horizon rows).

        `unknown` counts as owed. That single choice is why this event exists: an unanswered
        "did I put it back?" is the failure mode §8's example task is built to produce, and a
        report that read it as "not known to be broken" would score the agent for the sensor's
        silence. `invalid_completion` is §11's row of the same name: the agent said `finish`
        while the ledger still had a debt.
        """
        st = self.state
        open_commitments = [c for c in st.commitments if c.discharged.value is not True]
        unrestored = [r for r in st.suspended_relations if r.restored.value is not True]
        acted_on_refuted = [a for a in st.assumptions if a.invalidated_at_round is not None]
        owed_rows = [{"row_key": row_key(s), "kind": s.kind, "predicate_id": predicate_of(s),
                      "satisfied": str(s.satisfied.value), "statement": s.statement,
                      "evidence_refs": list(s.completion_evidence_refs)}
                     for s in (plan.subgoals if plan is not None else [])
                     if s.satisfied.value is not True]
        unfollowed = [f for f in st.failed_attempts
                      if f.followed_by == "nothing" and
                      (final_round is None or f.round_index < final_round)]
        claimed = st.provenance.get("claimed_complete_round") is not None
        return {
            "status": status,
            "round_index": st.round_index,
            "plan_version": st.plan_version,
            "unresolved_obligations": st.unresolved_obligations(),
            "open_commitments": [{"commitment_id": c.commitment_id, "round": c.made_at_round,
                                  "text": c.text,
                                  "predicate_id": str(c.provenance.get("predicate_id") or ""),
                                  "discharged": str(c.discharged.value),
                                  "unmeasured": list(c.discharged.unmeasured),
                                  "evidence_refs": sorted(set(c.evidence_refs) |
                                                          set(c.discharged.evidence_refs))}
                                 for c in open_commitments],
            "unrestored_relations": [{"relation_id": r.relation_id, "subject_id": r.subject_id,
                                      "predicate_id": str(r.provenance.get("predicate_id") or ""),
                                      "restore_by": r.restore_by,
                                      "disturbed_at_round": r.disturbed_at_round,
                                      "restored": str(r.restored.value),
                                      "unrestored_because": list(r.restored.unmeasured),
                                      "evidence_refs": list(r.restored.evidence_refs)}
                                     for r in unrestored],
            "invalid_assumptions_acted_on": [{"assumption_id": a.assumption_id,
                                              "round": a.invalidated_at_round, "text": a.text,
                                              "refuted_by": str(
                                                  a.provenance.get("contradicted_by") or "")}
                                             for a in acted_on_refuted],
            "owed_subgoals": owed_rows,
            "unknown_facts": list(st.unknown_facts),
            "open_questions": list(st.open_questions),
            "failures_never_answered": [{"attempt_id": f.attempt_id, "round": f.round_index,
                                         "skill": f.skill, "code": f.failure_code}
                                        for f in unfollowed],
            "claimed_complete": claimed,
            "claimed_complete_round": st.provenance.get("claimed_complete_round"),
            "invalid_completion": bool(claimed and (open_commitments or unrestored or
                                                    owed_rows)),
        }


def summarize_memory(states: Iterable[WorkingMemoryState]) -> dict[str, Any]:
    """Episode-level totals over the `working_memory` events, recomputed from the records.

    Counted from the log rather than from a counter kept in the loop, so a summary can always
    be rebuilt after the fact by anyone holding the episode file.
    """
    states = list(states)
    if not states:
        return {"snapshots": 0}
    last = states[-1]
    return {"snapshots": len(states),
            "commitments": len(last.commitments),
            "commitments_open": sum(1 for c in last.commitments
                                    if c.discharged.value is not True),
            "suspended_relations": len(last.suspended_relations),
            "suspended_unrestored": sum(1 for r in last.suspended_relations
                                        if r.restored.value is not True),
            "failed_attempts": len(last.failed_attempts),
            "invalidated_assumptions": sum(1 for a in last.assumptions
                                           if a.invalidated_at_round is not None),
            "unresolved_obligations": last.unresolved_obligations(),
            "max_plan_version": max(s.plan_version for s in states)}


__all__ = ["OBSERVE_SKILLS", "RECALL_BUDGET_CHARS", "RENDER_BUDGET_CHARS", "WorkingMemory",
           "predicate_of", "row_for", "summarize_memory"]
