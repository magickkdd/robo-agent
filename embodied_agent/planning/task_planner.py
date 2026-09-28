"""The rolling plan: a versioned work object the decision model argues with (SPEC-v0.2 §5.2, §7.4).

`RulePlanner` in `core/planner.py` is v0.1's baseline — one-shot, never revised, and
deliberately so. This is the other thing: a plan that is *recomputed against the latest
snapshot* every round and keeps a version history with a reason for every jump, because §5.2
says 计划是可更新工作状态，不是一次生成后强制执行的脚本 and §11 asks for
recovery/replanning effectiveness, which needs a denominator that means "the plan changed"
rather than "another round passed".

Four rules carry most of the scientific weight, and a reviewer may disagree with each:

1. **A revision is a graph change, not a status change.** Completing a subgoal refreshes the
   record in place; adding an obligation (a blocker found, a `recover` debt, an `observe`
   question), re-labelling one, dropping an open one, rewiring a dependency, *re-opening* a
   subgoal that had been done, or acting against an unsatisfied dependency each bump `version`
   and write a :class:`~..core.v02.PlanRevision` with the evidence refs that forced it. Counting
   status refreshes as replans would report a busy-looking number about nothing.
2. **Satisfaction is read from an instrument, never from the plan's own optimism.** Every
   `placed:` answer comes from the episode's *own* verifier — the privileged one or the
   per-channel `SensingVerifier` — so on the stub/vlm arm a subgoal cannot be `done` on better
   evidence than the model was allowed to see (§5.8's layer separation, P1-d's rule applied one
   layer up).
3. **Rows survive being inconvenient.** A `done` row, a `recover`/`maintain`/`observe` row and a
   `clear:` row are never dropped by a later derivation that no longer happens to mention them.
   That is the difference between a work object and a render of the current snapshot.
4. **The plan does not choose.** `ready()` returns a *set*. Which ready subgoal to work on, and
   whether to work on one at all, belongs to the decision source (§3.4, §5.7). A policy that
   ignores the offer is measured as a deviation, not repaired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ..core.contracts import (
    Decision,
    ExecutionFeedback,
    GoalSpec,
    PredicateVerdict,
    TaskInput,
    WorldState,
)
from ..core.v02 import (
    Dependency,
    PlanRevision,
    Status,
    Subgoal,
    TaskPlan,
    Unknownable,
)
from .subgoals import Instruments, clear_predicate, derive, occupants, placement_predicate, row_key

#: characters of plan summary the decision model is offered. A plan that outgrows the budget
#: must shed detail, not obligations — `summary` spends the budget in debt-first order for
#: exactly that reason.
SUMMARY_BUDGET_CHARS = 640

OPEN_STATUSES = (Status.pending, Status.active, Status.unknown)
#: kinds that must not vanish when a later snapshot stops deriving them
PERSISTENT = ("recover", "maintain", "observe", "control")
#: the precondition entries that describe what *happened to this row* rather than what the
#: current snapshot reports. A fresh derivation replaces the latter and keeps the former:
#: dropping the record that a subgoal was done and returned would erase a regression.
HISTORY_PREFIX = "[history] "
#: carried-forward history lines per row, newest last. Bounded so a long episode cannot grow a
#: single row's precondition list without limit.
HISTORY_CAP = 6

_VERDICT_TO_BOOL = {PredicateVerdict.true: True, PredicateVerdict.false: False}


@dataclass
class Refresh:
    """What one round of reading the world did to the plan."""

    plan: TaskPlan
    version_bumped: bool = False
    reopened: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    relabelled: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    rewired: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


def evidence_for(sub: Subgoal, verifier, world: WorldState):
    """Read one subgoal's answer from the episode's own verifier.

    Returns `(verdict, evidence_refs, unmeasured)`, or None when no instrument can answer it
    (`maintain` rows, a `clear:` row whose object this snapshot did not locate, and any row whose
    entity the snapshot never named).

    **A verifier may own the answering.** If it exposes `evidence_for(sub, world)` the question is
    handed to it and this function does not run. The reason is a fact about channels, not an
    extension point kept for comfort: the predicate ids a plan carries are the *channel's* vocabulary
    (`benchmark/obligations.py` authors `in2:peppershaker:drawer`, which is answered from stated
    text facts and can never be answered by `verify_placement`), and "which instrument can answer
    which claim" is exactly the question `§5.5: a claim is only checkable against the criterion that
    made it` assigns to the verifier. A verifier without the method — every desktop one — takes the
    path below unchanged.

    `clear:` is *not* the negation of `placed:`. `placed:` reads false both for an object that
    left the region and for one shoved against its wall — `IMPULSE_PIN_TO_WALL` in
    `evaluation/tasks.py` is that state, and it still occupies the descent envelope. So a
    `clear:` claim is checked against the same fact the derivation used to call the region
    blocked: whether the object is among the region's occupants in this snapshot
    (`subgoals.occupants`, which is `PlacementPlanner.recheck`'s own rule). SPEC 5.5: a claim is
    only checkable against the criterion that made it.
    """
    pid = sub.predicate_id or ""
    hook = getattr(verifier, "evidence_for", None)
    if hook is not None:
        return hook(sub, world)
    if not pid or not sub.target_entity_ids or not sub.target_region_id:
        return None
    eid, tid = sub.target_entity_ids[0], sub.target_region_id
    if not world.has_entity(eid):
        return None
    if pid.startswith("clear:"):
        entity = world.entity(eid)
        unseen = any(n.entity_id == eid for n in (world.not_seen or []))
        if entity.pose is None or unseen:
            # a blocker this frame cannot locate has not been shown to have moved
            return None
        inside = eid in occupants(world, tid)
        refs = [world.observation_ref or ""]
        return (PredicateVerdict.false if inside else PredicateVerdict.true), refs, []
    report = verifier.verify_placement(eid, tid)
    refs = list(report.evidence_refs or [world.observation_ref or ""])
    return report.value, refs, list(report.unmeasured)


class TaskPlanner:
    """Authors and revises the `TaskPlan` a round offers to the decision model."""

    def __init__(self, *, config=None, summary_budget: int = SUMMARY_BUDGET_CHARS,
                 derivation=None):
        """`derivation` replaces `subgoals.derive`, and nothing else.

        One argument, because one thing genuinely differs between channels: *which rows a task
        obliges*. The desktop answers that from `GoalSpec.assignments` plus a placement instrument;
        a text backend has neither (`benchmark/obligations.py` reads the instruction sentence, and
        `benchmark/state.py` keeps `assignments` empty because filling it would be the answer
        sheet). Everything downstream of a row — identity by `row_key`, the version bump, the ready
        set, the debt rules, the summary — is this module's, and stays the same object in both arms,
        which is what lets a `full` vs `wo_planning` difference on ALFWorld be about the plan rather
        than about a second planner that happens to share its name. `None` is the desktop rule, so
        no existing caller changes.
        """
        from ..core.contracts import VerifyConfig

        self.config = config or VerifyConfig()
        self.summary_budget = int(summary_budget)
        self.derivation = derivation or derive

    # ---------- authoring ----------
    def initial(self, task: TaskInput, goal: GoalSpec, world: WorldState, *, verifier,
                planner=None, progress=None) -> TaskPlan:
        """Plan v1, authored from one snapshot by the rules in `subgoals.derive`.

        `verifier` is passed but not called here: every row starts `unknown` and is answered by
        the first `refresh`, so the plan's own opening move is never also its first measurement.
        """
        instruments = Instruments(planner, world)
        derived = self.derivation(goal, world, task, planner=planner, progress=progress)
        subgoals, deps = derived.subgoals, derived.dependencies
        plan = TaskPlan(
            goal_ref=goal.goal_id, task_utterance=task.utterance, version=1,
            subgoals=subgoals, dependencies=deps, authored_by="rule",
            provenance={"author": "planning/task_planner.py:TaskPlanner",
                        "derivation": _qualified(self.derivation),
                        "notes": derived.notes,
                        "instruments": (["placement_planner"] if instruments.have_placement
                                        else ["none: no blocking evidence available"])},
            based_on_state_version=world.state_version,
            based_on_observation_ref=world.observation_ref,
            revisions=[PlanRevision(
                from_version=0, to_version=1, trigger="initial",
                evidence_refs=[world.observation_ref or ""],
                rationale=f"{len(subgoals)} subgoal(s), {len(deps)} dependency(ies) read off "
                          f"goal {goal.goal_id} and snapshot v{world.state_version}",
                changed_subgoal_ids=[s.subgoal_id for s in subgoals])])
        plan = plan.model_copy(update={"summary": self.summary(plan)})
        return self._recount(plan)

    # ---------- rolling ----------
    def refresh(self, plan: TaskPlan, world: WorldState, *, verifier, planner=None,
                task: Optional[TaskInput] = None, goal: Optional[GoalSpec] = None,
                progress=None, new_obligations: Sequence[Subgoal] = (),
                trigger: Optional[str] = None, evidence_refs: Sequence[str] = (),
                rationale: str = "") -> Refresh:
        """Re-read the world, merge the derivation into the live rows, and revise only if the
        *work* changed.

        Identity is the *row key* (`subgoals.row_key`), not the row's `subgoal_id`: `derive`
        authors fresh rows every round, and matching them to the live plan by key is what lets
        `attempts`, `last_decision_id` and `completion_evidence_refs` accumulate instead of
        resetting. The key remap (`id_map`) is applied to `depends_on` and to the dependency
        edges in the same pass, so the graph never mixes a fresh id with a retired one.

        A key is a *rule*, not a snapshot: a row whose kind has become persistent (`recover`,
        `observe`, `control`) is never silently downgraded by a later derivation that happens to
        describe the same predicate as plain work. The debt was created by a measurement; the
        snapshot that stopped mentioning it cancels nothing (rule 3).
        """
        before = {s.subgoal_id: s for s in plan.subgoals}
        by_key = {row_key(s): s for s in plan.subgoals}
        added: list[str] = []
        relabelled: list[str] = []
        dropped: list[str] = []
        new_deps: list[Dependency] = []

        if task is not None and goal is not None:
            derived = self.derivation(goal, world, task, planner=planner, progress=progress)
            id_map: dict[str, str] = {}
            merged: list[Subgoal] = []
            seen: set[str] = set()
            for sub in derived.subgoals:
                key = row_key(sub)
                old = by_key.get(key)
                if old is not None:
                    id_map[sub.subgoal_id] = old.subgoal_id
                    # a persistent kind is sticky: only a real promotion is a relabel
                    kind = old.kind if (old.kind in PERSISTENT
                                        and sub.kind not in PERSISTENT) else sub.kind
                    if kind != old.kind:
                        relabelled.append(key)
                    adopted = kind == sub.kind
                    keep = {k: getattr(old, k) for k in
                            ("status", "attempts", "last_decision_id",
                             "completion_evidence_refs")}
                    merged.append(old.model_copy(update={
                        "statement": sub.statement if adopted else old.statement,
                        "kind": kind,
                        "preconditions": _merge_preconditions(sub.preconditions,
                                                              old.preconditions, adopted=adopted),
                        "target_entity_ids": list(sub.target_entity_ids),
                        "target_region_id": sub.target_region_id,
                        "depends_on": list(sub.depends_on if adopted else old.depends_on),
                        **keep}))
                else:
                    id_map[sub.subgoal_id] = sub.subgoal_id
                    merged.append(sub)
                    added.append(key)
                seen.add(key)
            for key, old in by_key.items():
                if key in seen:
                    continue
                if old.status is Status.done or old.kind in PERSISTENT \
                        or (old.predicate_id or "").startswith("clear:"):
                    merged.append(old.model_copy(update={"depends_on": list(old.depends_on)}))
                elif _measured_true(old, verifier, world):
                    # The rule is "a row the task stopped asking for is retired", not "a row
                    # retired in the same snapshot that satisfied it loses its satisfaction":
                    # `_answer` runs after the merge, so without this read the completion that
                    # arrived on the round the clause went away would leave no evidence, and a
                    # later re-ask would be counted as new work instead of rework.
                    merged.append(old.model_copy(update={"depends_on": list(old.depends_on)}))
                else:
                    dropped.append(key)
            for sub in merged:
                sub.depends_on = sorted({id_map.get(d, d) for d in sub.depends_on})
            subgoals = merged
            for dep in derived.dependencies:
                edge = dep.model_copy(update={
                    "from_subgoal_id": id_map.get(dep.from_subgoal_id, dep.from_subgoal_id),
                    "on_subgoal_id": id_map.get(dep.on_subgoal_id, dep.on_subgoal_id)})
                if not any(_same(edge, d) for d in new_deps):
                    new_deps.append(edge)
        else:
            subgoals = [s.model_copy(deep=True) for s in plan.subgoals]

        for sub in new_obligations:
            if row_key(sub) in {row_key(s) for s in subgoals}:
                continue
            subgoals.append(sub)
            added.append(row_key(sub))

        reopened, events = self._answer(subgoals, verifier, world)

        # `TaskPlan` forbids a dependency edge onto a row the plan no longer carries
        # (`_ids_unique`). `model_copy` does not re-run validators, so the invariant is kept
        # here by *pruning and reporting*, never by leaving a dangling id or by dropping the
        # edge quietly: losing a prerequisite is a change to the work, so it is a revision.
        live_ids = {s.subgoal_id for s in subgoals}
        lost_edges = sorted({row_key(s) for s in subgoals
                             if [d for d in s.depends_on if d not in live_ids]})
        events += [f"pruned-prerequisite:{k}" for k in lost_edges]
        for sub in subgoals:
            sub.depends_on = [d for d in sub.depends_on if d in live_ids]

        rewired = sorted(set(lost_edges) | {row_key(s) for s in subgoals
                                            if s.subgoal_id in before
                                            and sorted(before[s.subgoal_id].depends_on) !=
                                            sorted(s.depends_on)})
        deps = [d.model_copy(deep=True) for d in plan.dependencies]
        for edge in new_deps:
            if not any(_same(edge, d) for d in deps):
                deps.append(edge)
        live_ids = {s.subgoal_id for s in subgoals}
        deps = [d for d in deps if d.from_subgoal_id in live_ids and d.on_subgoal_id in live_ids]
        violations = self._dependency_states(subgoals, deps)

        bumped = bool(added or dropped or relabelled or reopened or rewired or violations
                      or trigger)
        new_plan = plan.model_copy(update={
            "subgoals": subgoals, "dependencies": deps,
            "based_on_state_version": world.state_version,
            "based_on_observation_ref": world.observation_ref})
        if task is not None and goal is not None:
            # provenance is not the graph: re-deriving the same notes must never be a revision,
            # but a plan that carried stale provenance about *why* it was authored would be worse
            prov = dict(new_plan.provenance)
            prov["notes"] = list(dict.fromkeys(list(prov.get("notes") or []) +
                                               list(derived.notes)))
            new_plan = new_plan.model_copy(update={"provenance": prov})
        if bumped:
            reasons = (
                ([f"added:{','.join(added)}"] if added else []) +
                ([f"relabelled:{','.join(relabelled)}"] if relabelled else []) +
                ([f"dropped:{','.join(dropped)}"] if dropped else []) +
                ([f"reopened:{','.join(reopened)}"] if reopened else []) +
                ([f"rewired:{','.join(rewired)}"] if rewired else []) +
                ([f"violated:{','.join(violations)}"] if violations else []) +
                ([f"trigger:{trigger}"] if trigger and not (added or dropped or relabelled or
                                                            reopened or rewired or
                                                            violations) else []))
            revision = PlanRevision(
                from_version=plan.version, to_version=plan.version + 1,
                trigger=trigger or ("dependency_violation" if violations else "new_observation"),
                evidence_refs=list(evidence_refs) or [world.observation_ref or ""],
                rationale=(rationale + "; " if rationale else "") + "; ".join(reasons),
                changed_subgoal_ids=sorted({key_of(subgoals, k) for k in
                                            (added + dropped + relabelled + reopened + rewired)
                                            if key_of(subgoals, k)}))
            new_plan = new_plan.model_copy(update={
                "version": plan.version + 1,
                "revisions": list(plan.revisions) + [revision]})
        new_plan = self._recount(new_plan)
        return Refresh(plan=new_plan.model_copy(update={"summary": self.summary(new_plan)}),
                       version_bumped=bumped, reopened=reopened, added=added, dropped=dropped,
                       relabelled=relabelled, rewired=rewired, violations=violations,
                       events=events)

    # ---------- reading ----------
    def ready(self, plan: TaskPlan) -> list[Subgoal]:
        """The subgoals nothing currently prevents. A set, in row-identity order, not a queue.

        Ordered by `row_key` and not by `subgoal_id` because the id is a random `uuid4` suffix: an
        id order is stable within one episode and different in the next process, which would make
        two runs of one snapshot visit the same set in different sequences (§12.1).

        `maintain` and `control` rows are excluded on purpose: they name a relation to keep or a
        judgement to make, not an operation to run, so listing them as "ready" would tell the
        decision model that some skill could close them — and it cannot. They stay in
        `obligations()`, which is where a debt with no action is still reported as owed.
        """
        by_id = {s.subgoal_id: s for s in plan.subgoals}
        return sorted((s for s in plan.subgoals
                       if s.status in OPEN_STATUSES and s.kind not in ("maintain", "control")
                       and all(_done(by_id.get(d)) for d in s.depends_on)),
                      key=lambda s: (row_key(s), s.subgoal_id))

    def obligations(self, plan: TaskPlan) -> list[Subgoal]:
        """The debt ledger: every row whose satisfaction has not been *measured* true.

        One rule, and it is deliberately not "status != done". A `pending` row nobody attempted
        is owed (it came from the task); a `done` row whose measurement later flipped is owed
        again; a `failed` or `abandoned` row is owed because nothing resolved it and this
        framework reports a cancelled obligation instead of erasing it (§5.3's 恢复义务 must
        survive being inconvenient); and a `maintain` row no instrument can answer is owed by
        definition. That last clause is why `open_obligation_count` can be non-zero while
        `ready()` is empty, which is the state a task ending in "success" must not be allowed to
        hide (§11's obligation_check).
        """
        return sorted((s for s in plan.subgoals if s.satisfied.value is not True),
                      key=lambda s: (row_key(s), s.subgoal_id))

    def summary(self, plan: TaskPlan, budget: Optional[int] = None) -> str:
        """The bounded text a decision model reads each round (§5.2's 长程计划摘要).

        Debt first, detail last, and the *counts* first of all: the head line is always the
        first line and the last one cut, so a summary that ran out of budget can be terse but can
        never make an unmet obligation look met. Lines are then appended in the order
        `restore`/`maintain`/`look-for-evidence` (debts no ready list implies), `violated`,
        `stalled`, `blocked`, `ready`, `unmeasured`, `last revision` — and any line that does not
        fit is replaced by a count of what was cut. The structured `open_obligation_count` field
        is what §11 reads; this text is what the model reads, and both are rendered from one
        `obligations()` call so they cannot disagree.
        """
        budget = self.summary_budget if budget is None else int(budget)
        owed = self.obligations(plan)
        open_subs = [s for s in plan.subgoals if s.status in OPEN_STATUSES]
        ready = self.ready(plan)
        ready_ids = {s.subgoal_id for s in ready}
        by_id = {s.subgoal_id: s for s in plan.subgoals}
        head = (f"plan v{plan.version} | subgoals {len(plan.subgoals)} ({len(open_subs)} open, "
                f"{len(owed)} owed) | dependencies {len(plan.dependencies)} "
                f"({sum(1 for d in plan.dependencies if d.violated.value is True)} violated)")
        priority: list[str] = []
        for kind, label in (("recover", "restore"), ("maintain", "maintain"),
                            ("observe", "look-for-evidence")):
            rows = [s for s in owed if s.kind == kind]
            if rows:
                priority.append(f"  {label}: " + "; ".join(
                    f"{s.statement} [{s.status.value}]" for s in rows))
        violated = [d for d in plan.dependencies if d.violated.value is True]
        if violated:
            priority.append("  violated: " + "; ".join(d.note or d.dep_id for d in violated))
        stalled = [s for s in owed
                   if s.status in (Status.failed, Status.abandoned) or s.attempts]
        if stalled:
            priority.append("  stalled (acted on, not resolved): " + "; ".join(
                f"{s.predicate_id or s.statement} [{s.status.value}, {s.attempts} attempt(s)]"
                for s in stalled[:8]))
        blocked = [s for s in owed
                   if s.kind not in ("maintain", "control") and s.subgoal_id not in ready_ids]
        if blocked:
            priority.append("  blocked (owed, nothing can start it yet): " + "; ".join(
                f"{s.predicate_id or s.statement} <- " +
                (", ".join((by_id[d].predicate_id or d) if d in by_id else d
                           for d in s.depends_on)
                 if s.depends_on else f"status {s.status.value}")
                for s in blocked[:8]))
        if ready:
            priority.append("  ready (a set, not an order): " +
                            "; ".join(s.statement for s in ready))
        unknowns = [s for s in open_subs if s.satisfied.value == "unknown" and s.predicate_id]
        if unknowns:
            priority.append("  unmeasured: " + "; ".join(f"{s.predicate_id}"
                                                         for s in unknowns[:12]))
        last = plan.revisions[-1] if plan.revisions else None
        if last and last.trigger != "initial":
            priority.append(f"  last revision -> v{last.to_version} ({last.trigger}): "
                            f"{last.rationale}")

        lines, cut = [head], 0
        for line in priority:
            if len("\n".join(lines + [line])) > budget:
                cut += 1
            else:
                lines.append(line)
        if cut:
            note = (f"  ({cut} detail line(s) cut by the {budget}-char budget; the owed count in "
                    f"the first line is not a detail)")
            if len("\n".join(lines + [note])) <= budget:
                lines.append(note)
        return "\n".join(lines)[:budget]

    def mark_attempt(self, plan: TaskPlan, decision: Decision, *, subgoal: Optional[str] = None):
        """Record that this decision acted, and on which subgoal it was working.

        The binding is by the entity the action names, not by the plan's preference: a `pick` of
        `obj_red_1` is an attempt on whichever open row is about `obj_red_1`, and the caller
        (`planning/arm.py`) reports "the decision matched no open subgoal" as a deviation rather
        than quietly filing it under whatever the planner would have chosen.
        """
        ex = decision.execute
        if ex is None:
            return plan, None
        entity = str(dict(ex.args or {}).get("object_id") or "")
        target = str(dict(ex.args or {}).get("target_id") or "")
        want = subgoal or ""
        hits = [s for s in plan.subgoals
                if s.status in OPEN_STATUSES and (
                    (want and s.subgoal_id == want) or
                    (entity and entity in s.target_entity_ids and
                     (not target or s.target_region_id in (target, None) or
                      placement_predicate(entity, target) == s.predicate_id or
                      clear_predicate(entity, target) == s.predicate_id)))]
        if not hits:
            return plan, None
        # A row the decision named by id is the row it worked on, whatever else matches the entity:
        # falling through to the entity tie-break would let a random `subgoal_id` suffix overrule
        # the agent's own words. Without a name, the tie-break is on `row_key` — the key this
        # planner matches rows by — for the same reason: `subgoal_id` is not reproducible across
        # processes, and an attempt booked on a different row in the next run is a different record.
        hit = next((s for s in hits if want and s.subgoal_id == want), None) or \
            sorted(hits, key=lambda s: (row_key(s), s.subgoal_id))[0]
        rows = []
        for s in plan.subgoals:
            if s.subgoal_id == hit.subgoal_id:
                s = s.model_copy(update={
                    "attempts": s.attempts + 1, "last_decision_id": decision.decision_id,
                    "status": s.status if s.status is not Status.done else Status.done})
            rows.append(s)
        return plan.model_copy(update={"subgoals": rows}), hit

    # ---------- internals ----------
    def _recount(self, plan: TaskPlan) -> TaskPlan:
        return plan.model_copy(update={"open_obligation_count": len(self.obligations(plan))})

    def _answer(self, subgoals: list[Subgoal], verifier, world: WorldState):
        """Apply every instrument answer the current snapshot supports."""
        reopened: list[str] = []
        events: list[str] = []
        for sub in subgoals:
            answer = evidence_for(sub, verifier, world)
            if answer is None:
                continue
            value, refs, _unmeasured = answer
            sub.satisfied = Unknownable(value=_VERDICT_TO_BOOL.get(value, "unknown"),
                                        evidence_refs=refs)
            if value is PredicateVerdict.true:
                sub.status = Status.done
                if not sub.completion_evidence_refs:
                    sub.completion_evidence_refs = refs
                continue
            if sub.status is Status.done:
                # a true->false flip is the one measurement that *adds* work: the obligation
                # returned, so the row re-opens and the plan version moves (§11's regressions)
                sub.status = Status.pending
                _add_history(sub, f"was done and is {_VERDICT_TO_BOOL.get(value)} on "
                                  f"{refs[0] if refs else world.observation_ref}: "
                                  f"the obligation returned")
                reopened.append(row_key(sub))
                events.append(f"reopened:{row_key(sub)}")
            elif sub.status is Status.failed or sub.status is Status.abandoned:
                # an instrument that answers again has not been superseded by a decision: leave
                # a failed/abandoned row as the agent left it, or `repeated_failures` loses
                # its memory of the attempts that failed
                continue
            elif sub.status is not Status.pending and sub.status is not Status.active:
                sub.status = Status.pending
        return reopened, events

    def _dependency_states(self, subgoals: list[Subgoal], deps: list[Dependency]) -> list[str]:
        """Fill `satisfied`/`violated` on every dependency and return the newly violated notes.

        A dependency is satisfied when the work it waits for is measured done. It is *violated*
        when the subgoal that waits has been acted on while the prerequisite was not satisfied —
        the decision is the fact, so this is read after the fact, and the planner neither blocks
        the action nor edits the plan to excuse it (§3.4).
        """
        live = {s.subgoal_id: s for s in subgoals}
        violations: list[str] = []
        for dep in deps:
            src, dst = live.get(dep.from_subgoal_id), live.get(dep.on_subgoal_id)
            if src is None or dst is None:
                continue
            dep.satisfied = Unknownable(
                value=True if src.status is Status.done else
                ("unknown" if src.satisfied.value == "unknown" else False),
                evidence_refs=list(src.completion_evidence_refs))
            acted = dst.last_decision_id is not None or bool(dst.attempts)
            already = dep.violated.value is True
            if acted and src.status is not Status.done:
                if not already:
                    violations.append(dep.note or dep.dep_id)
                dep.violated = Unknownable(
                    value=True, evidence_refs=[dst.last_decision_id or "",
                                               src.satisfied.evidence_refs[0] if
                                               src.satisfied.evidence_refs else ""])
            elif acted and not already:
                dep.violated = Unknownable(value=False,
                                           evidence_refs=list(src.completion_evidence_refs))
        return violations


def key_of(subgoals: Iterable[Subgoal], row_key_value: str) -> str:
    """The live `subgoal_id` carrying a row key, or "" when no row does."""
    for sub in subgoals:
        if row_key(sub) == row_key_value:
            return sub.subgoal_id
    return ""


def _merge_preconditions(fresh: Sequence[str], old: Sequence[str], *, adopted: bool) -> list[str]:
    """What the *snapshot* says now, plus what this row has survived.

    `fresh` replaces `old` wholesale because a stale "no feasible slot at v3" would be a false
    claim about v9. `[history]` lines are the exception: they are statements about the row's own
    past, and a derivation that cannot see them must not delete them.
    """
    if not adopted:
        return list(dict.fromkeys(old))
    carried = [p for p in old if p.startswith(HISTORY_PREFIX)]
    return list(dict.fromkeys(list(fresh) + carried[-HISTORY_CAP:]))


def _add_history(sub: Subgoal, text: str) -> None:
    """Append one dated fact about this row, oldest history dropped past `HISTORY_CAP`."""
    plain = [p for p in sub.preconditions if not p.startswith(HISTORY_PREFIX)]
    kept = [p for p in sub.preconditions if p.startswith(HISTORY_PREFIX)] + [HISTORY_PREFIX + text]
    sub.preconditions = plain + kept[-HISTORY_CAP:]


def _done(sub: Optional[Subgoal]) -> bool:
    return bool(sub is not None and sub.status is Status.done)


def _qualified(fn) -> str:
    """`module.py:function` for a derivation rule, so a record names the code that wrote its rows."""
    module = str(getattr(fn, "__module__", "?"))
    name = str(getattr(fn, "__qualname__", getattr(fn, "__name__", "?")))
    return f"{module.replace('.', '/')}.py:{name}"


def _measured_true(sub: Subgoal, verifier, world: WorldState) -> bool:
    """What the instrument says about a row *right now*, before the plan decides its fate."""
    answer = evidence_for(sub, verifier, world) if verifier is not None else None
    return bool(answer is not None and answer[0] is PredicateVerdict.true)


def _same(a: Dependency, b: Dependency) -> bool:
    return ((a.from_subgoal_id, a.on_subgoal_id, a.kind) ==
            (b.from_subgoal_id, b.on_subgoal_id, b.kind))


def observe_subgoal(predicate_id: str, statement: str, *, resolves: Iterable[str] = (),
                    entity_id: str = "", region_id: str = "") -> Subgoal:
    """A look the plan asked for because an answer is missing and a named view could give it.

    This is the consumer P1 left unwritten: `PerceptVerifier.would_resolve()` reported that
    **558 of 558** withheld placement verdicts would be settled by a further look from another
    camera (表 13, `V3.resolvable_unknown`) and nothing in the loop acted on that. The row is
    keyed to the predicate it asks about, so `evidence_for` answers it automatically the moment
    the measurement exists — and if nobody looks it stays open, which is what §11's
    unknown-handling row is supposed to measure rather than hide.
    """
    ways = [r for r in (list(resolves) or []) if r]
    return Subgoal(
        statement=statement, kind="observe", status=Status.pending,
        target_entity_ids=[entity_id] if entity_id else [],
        target_region_id=region_id or None, predicate_id=predicate_id,
        preconditions=[f"unmeasured; this arm says a further look from {', '.join(ways)} would "
                       f"answer it"] if ways else
                      ["unmeasured, and no further look this arm has would answer it (a declined "
                       "fact or an absent camera)"])


__all__ = ["HISTORY_CAP", "HISTORY_PREFIX", "PERSISTENT", "Refresh", "SUMMARY_BUDGET_CHARS",
           "TaskPlanner", "evidence_for", "key_of", "observe_subgoal"]
