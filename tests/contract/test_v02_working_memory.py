"""SPEC-v0.2 §5.3 contract tests for working memory, and the `obligation_check` audit.

The question this file asks of the code is narrow: **does a debt the agent incurred on round 3
still exist on round 40, and can it only be closed by something that measured it?** §5.3's
"它必须在线参与决策" is the research claim, and the failure mode it is aimed at is the one v0.1
had — a decision payload that shows the last few feedbacks and nothing else, so "put it back
before you finish" ages out of the conversation while the task is still running.

Fixtures follow the split the planner tests use: real geometry where a claim is about what an
instrument reports, a scripted verifier where a claim is about what the ledger does with an
answer. `WorkingMemory` is deliberately the only thing under test here: the same calls the
runtime arm will make (§7 steps 2, 6-7, 9-10), driven in that order.
"""
from __future__ import annotations

from collections import namedtuple

import pytest

from embodied_agent.core.contracts import (
    Decision,
    DecisionExecute,
    EntityRef,
    GoalAssignment,
    GoalProgressItem,
    GoalSpec,
    NotSeen,
    OccupancyRecord,
    PredicateReport,
    PredicateVerdict,
    Source,
    TaskInput,
    ExecutionFeedback,
    SkillStatus,
)
from embodied_agent.core.placement_planner import PlacementPlanner
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.v02 import (
    EVENT_MODULE,
    RecalledExperience,
    Status,
    WorkingMemoryState,
    ablation,
)
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.planning.subgoals import (
    clear_predicate,
    placement_predicate,
    row_key,
)
from embodied_agent.planning.task_planner import Refresh, TaskPlanner
from embodied_agent.planning.working_memory import (
    RECALL_BUDGET_CHARS,
    RENDER_BUDGET_CHARS,
    WorkingMemory,
    predicate_of,
    row_for,
    summarize_memory,
)

DEV_UTTERANCE = find_case("dev_c1").utterance
Check = namedtuple("Check", "ok candidate reasons")


class StubPlanner:
    """A placement instrument with refusals a test can aim at one (object, region) pair."""

    def __init__(self, *, slots: dict[tuple[str, str], int], inside: dict[str, list[str]]):
        self.slots = slots
        self.inside = inside

    def generate(self, target_id, entity_id, world, limit=None):
        n = self.slots.get((entity_id, target_id), 0)
        return [Candidate(f"cand_{entity_id}_{target_id}_{i}", target_id, entity_id, i)
                for i in range(n)]

    def recheck(self, cand, world):
        others = [o for o in self.inside.get(cand.target_id, []) if o != cand.entity_id]
        if others:
            return Check(False, cand, [f"slot is inside the hand's descent envelope of "
                                       f"{', '.join(others)}"])
        return Check(True, cand, [])


Candidate = namedtuple("Candidate", "candidate_id target_id entity_id index")


class ScriptVerifier:
    """The verification channel with the answers fixed.

    `WorkingMemory` asks a verifier exactly one question — `verify_placement` — so this is the
    whole interface, not a partial one. Every "the ledger closed a debt because…" test needs the
    instrument to answer on cue, and no amount of pybullet buys evidence about what a *record*
    does with an answer.
    """

    def __init__(self, table=None, default=PredicateVerdict.false, source=Source.sensor):
        self.table = dict(table or {})
        self.default = default
        self.source = source
        self.calls: list[str] = []

    def verify_placement(self, eid, target_id):
        pid = placement_predicate(eid, target_id)
        self.calls.append(pid)
        return PredicateReport(predicate_id=pid, description="scripted",
                               value=self.table.get(pid, self.default),
                               evidence_refs=[f"obs_script:{pid}"], source=self.source)

    def flip(self, eid, target_id, value):
        self.table[placement_predicate(eid, target_id)] = value


def goal_for(pairs) -> GoalSpec:
    return GoalSpec(assignments=[GoalAssignment(entity=EntityRef(entity_id=e), target_id=t)
                                 for e, t in pairs])


def progress_for(pairs, value=PredicateVerdict.false) -> list[GoalProgressItem]:
    return [GoalProgressItem(entity_id=e, target_id=t, value=value,
                             predicate_id=placement_predicate(e, t),
                             evidence_refs=["obs_0001"]) for e, t in pairs]


def task_for(constraints=(), utterance=DEV_UTTERANCE) -> TaskInput:
    return TaskInput(task_id="t_p2b", utterance=utterance,
                     declared_constraints=list(constraints))


def seated_in(world, entity_id, target_id, *, version=None, obs=None):
    """A snapshot that measures `entity_id` resting inside `target_id`."""
    rec = OccupancyRecord(target_id=target_id, entity_id=entity_id, rest_xy=(0.66, -0.32),
                          footprint_half_xy=(0.021, 0.021), fully_inside=True)
    return world.model_copy(update={
        "state_version": version if version is not None else world.state_version + 1,
        "observation_ref": obs or f"obs_{world.state_version + 1:04d}",
        "occupancy": [o for o in world.occupancy if o.entity_id != entity_id] + [rec]})


def vacated(world, entity_id, target_id, *, version=None, obs=None):
    """A snapshot that still measures the body — just no longer inside `target_id`."""
    return world.model_copy(update={
        "state_version": version if version is not None else world.state_version + 1,
        "observation_ref": obs or f"obs_{world.state_version + 1:04d}",
        "entities": [e if e.entity_id != entity_id else
                     e.model_copy(update={"supported_by": "table"}) for e in world.entities],
        "occupancy": [o for o in world.occupancy if not (o.entity_id == entity_id and
                                                         o.target_id == target_id)]})


def decide(skill="place", *, args=None, subgoal="", candidate=None, action="execute",
           expected="", state_version=1, goal_ref="g_1", evidence=(), rationale="",
           missing=None) -> Decision:
    ex = (DecisionExecute(skill=skill, args=dict(args or {}),
                          candidate_id=candidate, subgoal=subgoal)
          if action == "execute" else None)
    return Decision(context_id="ctx_1", based_on_state_version=state_version, goal_ref=goal_ref,
                    action=action, execute=ex, expected_effect=expected,
                    evidence_refs=list(evidence), rationale=rationale,
                    missing_information=missing)


def feedback(*, skill="place", executed=True, status="success", code=None, reasons=(),
             decision_id="d_1", pre="obs_0001", post="obs_0002", text=None) -> ExecutionFeedback:
    return ExecutionFeedback(decision_id=decision_id, skill=skill, executed=executed,
                             status=status, failure_code=code, rejection_reasons=list(reasons),
                             pre_observation_ref=pre, post_observation_ref=post,
                             environment_text=text)


@pytest.fixture(scope="module")
def real():
    """One real scene, snapshot, placement instrument and privileged verifier (`dev_c1`)."""
    case = find_case("dev_c1")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        yield (case, scene, world, PlacementPlanner(scene, config=case.verify),
               RuntimeVerifier(world, case.verify))
    finally:
        scene.close()


def unlocated(world, entity_id, *, view="front_left"):
    """The frame looked for `entity_id` in one view and did not find it.

    The one way this file can produce a predicate *nobody* answers: `evidence_for` leaves a
    `clear:` row alone when the object is named as not-seen, so the row is open and unmeasured
    rather than false.
    """
    return world.model_copy(update={"not_seen": list(world.not_seen) +
                                     [NotSeen(entity_id=entity_id, view=view)]})


@pytest.fixture(scope="module")
def blocked_case():
    """The frozen protocol case whose only legal region already has an occupant.

    The planner tests use it for the same reason: `obj_blocker_1` is a real body in a real tray,
    so a claim about what discharges a `clear:` debt is answered by geometry rather than by a
    fixture's opinion.
    """
    case = find_case("pr_target_full")
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        yield case, world, PlacementPlanner(scene, config=case.verify)
    finally:
        scene.close()


@pytest.fixture(scope="module")
def ids(real):
    _, _, world, _, _ = real
    return sorted(e.entity_id for e in world.entities)


@pytest.fixture(scope="module")
def regions(real):
    _, _, world, _, _ = real
    return sorted(t.target_id for t in world.targets)


def _row(plan, predicate_id):
    rows = [s for s in plan.subgoals if s.predicate_id == predicate_id]
    assert len(rows) == 1, f"{predicate_id}: {[r.kind for r in rows]}"
    return rows[0]


def _plan_for(pairs, world, *, verifier, planner, task=None, goal=None):
    tp = TaskPlanner()
    task = task or task_for()
    goal = goal or goal_for(pairs)
    plan = tp.initial(task, goal, world, verifier=verifier, planner=planner,
                      progress=progress_for(pairs))
    return tp, tp.refresh(plan, world, verifier=verifier, planner=planner, task=task, goal=goal,
                          progress=progress_for(pairs)).plan


def _shape2(real, ids, regions):
    """The plan for "the object blocking the tray is itself owed that tray" (SPEC §8's case)."""
    _, _, world, _, _ = real
    a, b, c = ids[0], ids[1], ids[2]
    left, middle, right = regions
    pairs = [(a, left), (b, left), (c, right)]
    planner = StubPlanner(slots={(a, left): 1, (b, left): 1, (b, middle): 1, (c, right): 1},
                          inside={left: [b]})
    ver = ScriptVerifier()
    blocked_world = seated_in(world, b, left)
    tp, plan = _plan_for(pairs, blocked_world, verifier=ver, planner=planner)
    return (tp, ver, planner, pairs, blocked_world,
            dict(a=a, b=b, c=c, left=left, middle=middle, right=right), plan)


# ------------------------------------------------ the seven lists §5.3 asks for ----


def test_every_list_5_3_names_is_populated_by_one_ordinary_episode(real, ids, regions):
    """A record type nothing writes is a field list, not a capability.

    One scripted round that produces each of the seven: statuses and the unknowns from the
    plan, a promise, a suspended relation, a failed attempt, an invalidated assumption, the
    plan summary. `unknown` is a list of its own because §11's uncertainty row is counted
    there, not in progress.
    """
    tp, ver, planner, pairs, blocked_world, n, plan = _shape2(real, ids, regions)
    wm = WorkingMemory(episode_id="ep_1")
    st = wm.sync(plan, blocked_world, verifier=ver, round_index=1)
    assert st.subgoal_status and st.plan_summary
    assert set(st.subgoal_status.values()) <= set(Status), "the list holds statuses, not prose"
    recover = _row(plan, placement_predicate(n["b"], n["left"]))
    assert recover.kind == "recover"

    # an acted-on claim no instrument had answered, and a failure to follow it with
    blind = ScriptVerifier(default=PredicateVerdict.unknown)
    _, blind_plan = _plan_for(pairs, blocked_world, verifier=blind, planner=planner)
    wm.sync(blind_plan, blocked_world, verifier=blind, round_index=1)
    assert wm.state.unknown_facts, "every row of that plan is a fact nobody measured"
    owed_left = _row(blind_plan, placement_predicate(n["a"], n["left"]))
    assert owed_left.depends_on, "a is owed the tray b is standing in, and waits on the move"
    wm.commit(decide(args={"object_id": n["a"], "target_id": n["left"]},
                     subgoal=predicate_of(owed_left),
                     expected=f"{n['a']} is seated in {n['left']}"),
              blind_plan, blocked_world)
    assert len(wm.state.assumptions) == 2, "the placement stood on two unanswered claims: that " \
                                           "the blocker had been moved out, and that it would " \
                                           "come back"
    assert {a.provenance["predicate_id"] for a in wm.state.assumptions} == {
        placement_predicate(n["b"], n["middle"]), placement_predicate(n["b"], n["left"])}
    assert {a.provenance["worked_row_key"] for a in wm.state.assumptions} == {row_key(owed_left)}
    wm.sync(blind_plan, blocked_world, verifier=ver, round_index=1)
    assert all(a.invalidated_at_round == 1 for a in wm.state.assumptions), \
        "the instrument that could answer said neither had happened: the beliefs are refuted, " \
        "and refuted is not deleted"

    ver.flip(n["c"], n["right"], PredicateVerdict.unknown)
    moved_world = vacated(blocked_world, n["b"], n["left"])
    moved_plan = tp.refresh(plan, moved_world, verifier=ver, planner=planner,
                            task=task_for(), goal=goal_for(pairs),
                            progress=progress_for(pairs)).plan
    wm.settle(None, feedback(skill="pick", status="failure", code="GripSlip",
                             post="obs_0002"), moved_world,
              plan=moved_plan, verifier=ver)
    wm.sync(moved_plan, moved_world, verifier=ver, round_index=2)
    wm.commit(decide(skill="place", args={"object_id": n["b"], "target_id": n["middle"]},
                     subgoal=predicate_of(_row(moved_plan, placement_predicate(n["b"],
                                                                                n["left"])))),
              moved_plan, moved_world)

    assert [f.skill for f in wm.state.failed_attempts] == ["pick"]
    assert len(wm.state.suspended_relations) == 1, "the move is measured, so the relation is " \
                                                   "now false and owes its return"
    assert wm.state.commitments
    assert wm.state.unknown_facts, "one row the instrument still cannot answer is its own list"
    assert wm.state.current_subgoal_id == _row(moved_plan, placement_predicate(n["b"],
                                                                               n["left"])).subgoal_id
    check = wm.obligation_check(status="timeout", plan=moved_plan, final_round=2)
    assert check["unrestored_relations"] and check["open_commitments"]
    assert [x["assumption_id"] for x in check["invalid_assumptions_acted_on"]] == \
        [a.assumption_id for a in wm.state.assumptions]
    assert check["unknown_facts"] == wm.state.unknown_facts
    assert check["failures_never_answered"] == [], "round 1's failure was followed by a decision"


# ------------------------------------------------- a debt closes on evidence only ----


def test_a_promise_closes_on_the_measurement_it_named_not_on_the_skills_success(real, ids,
                                                                               regions):
    """§5.8's layers kept apart: `status="success"` is what happened, not what is true.

    The easier implementation is to discharge on the feedback the executor returns, and it
    would look right on every success and wrong on the one case that matters — a `place` that
    reported success and left the object leaning on the tray rim.
    """
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    row = _row(plan, placement_predicate(a, left))
    wm = WorkingMemory()
    wm.sync(plan, real[2], verifier=ver, round_index=1)
    dec = decide(args={"object_id": a, "target_id": left}, subgoal=row.subgoal_id,
                 expected=f"{a} is seated in {left}", goal_ref=plan.goal_ref)
    wm.commit(dec, plan, real[2], subgoal=row)
    wm.settle(dec, feedback(decision_id=dec.decision_id, post=real[2].observation_ref),
              real[2], plan=plan, verifier=ver)
    c = wm.state.commitments[0]
    assert c.discharged.value is False, "the skill succeeded and the predicate did not"
    assert c.discharged.evidence_refs == [f"obs_script:{placement_predicate(a, left)}"]
    assert wm.state.unresolved_obligations() == 1

    ver.flip(a, left, PredicateVerdict.true)
    wm.sync(plan, real[2], verifier=ver, round_index=2)
    assert c.discharged.value is True and c.discharged.evidence_refs
    assert wm.state.unresolved_obligations() == 0
    assert wm.obligation_check(status="success", plan=plan)["open_commitments"] == []


def test_a_promise_no_instrument_can_keep_stays_unknown_and_is_named_at_the_end(real, ids,
                                                                                regions):
    """A reference that resolves to no row is reported, not dropped and not guessed.

    The decision said `subgoal="tray_left"` where the plan's rows are keyed to predicates; the
    nearest row is *almost* it, and "almost" is what this test forbids.
    """
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    wm = WorkingMemory()
    wm.sync(plan, real[2], verifier=ver, round_index=1)
    dec = decide(subgoal="the tray on the left", expected="everything is finished tidily")
    wm.commit(dec, plan, real[2])
    c = wm.state.commitments[0]
    assert c.subgoal_id is None and predicate_of(None) == ""
    wm.sync(plan, real[2], verifier=ver, round_index=2)
    assert c.discharged.value == "unknown" and c.discharged.unmeasured
    check = wm.obligation_check(status="timeout", plan=plan)
    assert [x["commitment_id"] for x in check["open_commitments"]] == [c.commitment_id]
    assert "no row this plan still carries" in check["open_commitments"][0]["unmeasured"][0], \
        "an unresolvable reference is reported with the reason it could not be closed, not " \
        "quietly dropped"
    assert wm.state.unresolved_obligations() == 1, "unknown counts as owed"


def test_the_row_recorded_is_the_row_the_decision_named_not_the_one_the_planner_offered(
        real, ids, regions):
    """Rule 2: working memory reports what the agent is doing, not what it should be doing.

    `ready()` is a set; the first element of it is an artefact of sorting ids, not a queue. A
    ledger that stored the planner's preference would turn §11's `dependency_violations` into a
    tautology, because every decision would be recorded against a legal row.
    """
    a, b = ids[0], ids[1]
    left, middle = regions[0], regions[1]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left), (b, middle)], real[2], verifier=ver, planner=real[3])
    tp = TaskPlanner()
    ready = tp.ready(plan)
    assert len(ready) == 2
    named = [s for s in ready if s.target_region_id == middle][0]
    wm = WorkingMemory()
    wm.commit(decide(subgoal=named.subgoal_id), plan, real[2])
    assert wm.state.current_subgoal_id == named.subgoal_id
    assert wm.state.commitments[0].subgoal_id == named.subgoal_id

    wm.commit(decide(subgoal=""), plan, real[2])
    assert wm.state.current_subgoal_id is None, "a decision that names nothing is recorded as " \
                                                "one that named nothing"


# ------------------------------------------------------------- the follow-up field ----


def _fail_then(wm, plan, world, verifier, failed_decision, next_decision):
    """One round's §7 steps 9-10 then 6-7: the attempt that failed, then the decision after it.

    The failed decision is passed in because `followed_by` is compared against what was *tried*
    — its skill, its arguments, its slot — and a ledger that had not been told which decision
    failed could only guess, which is the thing §12.3 forbids.
    """
    wm.settle(failed_decision,
              feedback(skill=failed_decision.execute.skill if failed_decision.execute else "",
                       status="failure", code="GripSlip"),
              world, plan=plan, verifier=verifier)
    wm.commit(next_decision, plan, world)
    return wm.state.failed_attempts[-1].followed_by


def test_what_the_agent_did_about_a_failure_is_read_from_its_payload_not_its_prose(
        real, ids, regions):
    """§12.3 (以实际动作和最终状态为准), applied to the ledger's own summary field.

    Each case carries a `rationale` that says the opposite of what the arguments do, because a
    model can write "let me try a different slot" and re-send the identical call — and §11's
    repeated-failure rate would then be measuring the prose.
    """
    a, left = ids[0], regions[0]
    args = {"object_id": a, "target_id": left}
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    row = _row(plan, placement_predicate(a, left))
    world = real[2]

    attempt = decide(args=args, subgoal=row.subgoal_id, candidate="cand_1",
                     rationale="the call that just failed")
    cases = [
        ("retry", decide(args=args, subgoal=row.subgoal_id, candidate="cand_1",
                         rationale="let me try a different slot")),
        ("change_candidate", decide(args=args, subgoal=row.subgoal_id, candidate="cand_2",
                                    rationale="the same slot again")),
        ("re_observe", decide(skill="observe", args={"view": "front_left"},
                              rationale="push it again")),
        ("abandon", decide(skill="pick", args={"object_id": a},
                           rationale="I will keep using place")),
        ("give_up", decide(action="blocked", missing="the tray is bolted down",
                           rationale="one more try")),
    ]
    for expected, dec in cases:
        wm = WorkingMemory()
        got = _fail_then(wm, plan, world, ver, attempt, dec)
        assert got == expected, f"{dec.rationale!r} -> {got}, wanted {expected}"
        assert wm.state.failed_attempts[-1].provenance["candidate_id"] == "cand_1", \
            "the slot that failed is recorded, so the comparison above is not a coincidence"

    wm = WorkingMemory()
    wm.sync(plan, world, verifier=ver, round_index=1,
            revision=Refresh(plan=plan, version_bumped=True, added=[row_key(row)]))
    assert wm.state.pending_replan
    got = _fail_then(wm, plan, world, ver, attempt,
                     decide(args=args, subgoal=row.subgoal_id, candidate="cand_1"))
    assert got == "replan", "the plan changed under the failure and the agent worked the new graph"


def test_a_failure_the_episode_ended_on_is_not_counted_as_one_the_agent_ignored(real, ids,
                                                                               regions):
    """`followed_by="nothing"` means no later decision, which is not the same as no recovery."""
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    wm = WorkingMemory()
    wm.sync(plan, real[2], verifier=ver, round_index=7)
    wm.settle(None, feedback(skill="place", status="failure", code="GripSlip"), real[2],
              plan=plan, verifier=ver)
    assert wm.state.failed_attempts[-1].followed_by == "nothing"
    assert wm.obligation_check(status="timeout", plan=plan, final_round=7)[
        "failures_never_answered"] == [], "the truncating round cannot also be an ignored failure"
    assert wm.obligation_check(status="timeout", plan=plan, final_round=8)[
        "failures_never_answered"], "a failure with three rounds left and no follow-up is one"


def test_the_failure_ledger_speaks_the_status_the_loop_actually_sends(real, ids, regions):
    """§11's `repeated failures` is read off this list, so an entry for a command that worked
    is not a conservative mistake — it is the metric measuring the layer.

    Measured, not reasoned: with the accepted-status list holding only strings `SkillStatus`
    never emits, a text batch (12 episodes) and a desktop batch (16 episodes) each filed exactly
    one `FailedAttempt` per round, including in episodes whose every feedback was a clean
    `completed` with no rejection. The three shapes below are the ones `Runtime` can hand back
    for work that was not a defeat; `finish_accepted` is the control outcome of a completion
    claim, which `acquisition/gap.py:72` already refuses to count as a move.
    """
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    wm = WorkingMemory()
    wm.sync(plan, real[2], verifier=ver, round_index=1)
    for status in ("completed", SkillStatus.completed, "finish_accepted"):
        wm.settle(None, feedback(skill="place", status=status), real[2], plan=plan, verifier=ver)
    assert wm.state.failed_attempts == [], "three rounds of work that the loop reported as " \
                                           "working, and the ledger has to stay empty"
    assert wm.render()["counts"]["failed_attempts"] == 0

    wm.settle(None, feedback(skill="place", status="failure", code="GripSlip"), real[2],
              plan=plan, verifier=ver)
    wm.settle(None, feedback(skill="pick", executed=False, status="rejected",
                             reasons=("INVALID_DECISION",)), real[2], plan=plan, verifier=ver)
    assert [(f.skill, f.provenance["rejected_by_runtime"]) for f in wm.state.failed_attempts] == \
        [("place", False), ("pick", True)], "the fix must not swallow the two kinds it is " \
                                            "supposed to keep apart (§5.8)"


# ------------------------------------------------------- recovery obligations (§8) ----


def test_a_recover_debt_becomes_a_suspended_relation_only_once_the_move_is_measured(real, ids,
                                                                                   regions):
    """The trigger is the occupancy, not the intention.

    The plan had the debt from round 1 (`recover` rows are authored from an instrument's
    refusal); working memory records the relation as *currently false* only when a snapshot
    shows the object gone, because before that the agent has displaced nothing and owes nothing
    back yet.
    """
    tp, ver, planner, pairs, blocked_world, n, plan = _shape2(real, ids, regions)
    recover = _row(plan, placement_predicate(n["b"], n["left"]))
    wm = WorkingMemory()
    wm.sync(plan, blocked_world, verifier=ver, round_index=1)
    assert wm.state.suspended_relations == [], "nothing has been moved in this snapshot"

    moved_world = vacated(blocked_world, n["b"], n["left"])
    moved_plan = tp.refresh(plan, moved_world, verifier=ver, planner=planner, task=task_for(),
                            goal=goal_for(pairs), progress=progress_for(pairs)).plan
    assert _row(moved_plan, placement_predicate(n["b"], n["left"])).kind == "recover"
    wm.settle(None, feedback(skill="place", status="success", post=moved_world.observation_ref),
              moved_world, plan=moved_plan, verifier=ver)
    rel, = wm.state.suspended_relations
    assert (rel.subject_id, rel.related_id, rel.was, rel.now) == \
        (n["b"], n["left"], "true", "false")
    assert recover.subgoal_id in rel.restore_by, "the debt names the row that has to pay it"
    assert rel.restored.value == "unknown"
    assert wm.state.unresolved_obligations() == 1
    assert "tray" in rel.reason or "table" in rel.reason, "the reason quotes the snapshot"


def test_a_suspended_relation_is_restored_by_the_measurement_not_by_the_plans_hope(
        real, ids, regions):
    tp, ver, planner, pairs, blocked_world, n, plan = _shape2(real, ids, regions)
    moved_world = vacated(blocked_world, n["b"], n["left"])
    moved_plan = tp.refresh(plan, moved_world, verifier=ver, planner=planner, task=task_for(),
                            goal=goal_for(pairs), progress=progress_for(pairs)).plan
    wm = WorkingMemory()
    wm.settle(None, feedback(skill="place"), moved_world, plan=moved_plan, verifier=ver)
    rel, = wm.state.suspended_relations
    assert rel.restored.value == "unknown"

    ver.flip(n["b"], n["left"], PredicateVerdict.true)
    wm.sync(moved_plan, moved_world, verifier=ver, round_index=3)
    assert rel.restored.value is True
    assert rel.restored.evidence_refs == [f"obs_script:{placement_predicate(n['b'], n['left'])}"]
    assert wm.state.unresolved_obligations() == 0
    assert wm.obligation_check(status="success", plan=moved_plan)["unrestored_relations"] == []

    ver.flip(n["b"], n["left"], PredicateVerdict.false)
    wm.sync(moved_plan, moved_world, verifier=ver, round_index=4)
    assert rel.restored.value is True, "a relation once restored is not un-restored by a later " \
                                       "look; a regression is a new debt, and the plan reopens " \
                                       "the row that carries it"


def test_an_unrestored_relation_is_the_reason_a_completion_claim_is_invalid(real, ids, regions):
    """§11's `invalid completion`, computed rather than asserted."""
    tp, ver, planner, pairs, blocked_world, n, plan = _shape2(real, ids, regions)
    moved_world = vacated(blocked_world, n["b"], n["left"])
    moved_plan = tp.refresh(plan, moved_world, verifier=ver, planner=planner, task=task_for(),
                            goal=goal_for(pairs), progress=progress_for(pairs)).plan
    wm = WorkingMemory()
    wm.sync(moved_plan, moved_world, verifier=ver, round_index=2)
    wm.settle(None, feedback(skill="place"), moved_world, plan=moved_plan, verifier=ver)
    wm.commit(decide(action="finish", evidence=["obs_0002"]), moved_plan, moved_world)
    check = wm.obligation_check(status="success", plan=moved_plan)
    assert check["claimed_complete"] and check["invalid_completion"]
    assert check["claimed_complete_round"] == 2
    assert "still measures false" in check["unrestored_relations"][0]["unrestored_because"][0], \
        "the report says which measurement the debt is waiting on"
    assert wm.state.commitments == [], "a completion claim is not a debt with a predicate: the " \
                                       "thing that can judge it is §5.8's independent evaluator, " \
                                       "whose input must stay isolated"
    # The other side of the same rule needs the *plan* to have nothing owed, and only the
    # planner's own instrument can say that: working memory reads rows, it does not re-answer
    # them. So this is a second derivation against a world in which every row — including the
    # blocker's temporary home, the row a "the task is done" reading of `pairs` alone would have
    # missed — measures true.
    ver_all = ScriptVerifier({pid: PredicateVerdict.true for pid in
                              {s.predicate_id for s in plan.subgoals if s.predicate_id}})
    done_plan = tp.refresh(plan, moved_world, verifier=ver_all, planner=planner,
                           task=task_for(), goal=goal_for(pairs),
                           progress=progress_for(pairs, PredicateVerdict.true)).plan
    assert all(s.satisfied.value is True for s in done_plan.subgoals)
    clean = WorkingMemory()
    clean.sync(done_plan, moved_world, verifier=ver_all, round_index=3)
    clean.commit(decide(action="finish"), done_plan, moved_world)
    nothing_owed = clean.obligation_check(status="success", plan=done_plan)
    assert nothing_owed["owed_subgoals"] == [] and nothing_owed["claimed_complete"]
    assert nothing_owed["invalid_completion"] is False


# ------------------------------------------------------------------ assumptions ----


def test_an_assumption_is_a_prerequisite_the_agent_had_no_answer_for_not_the_work_it_was_doing(
        blocked_case):
    """Not "everything the ledger is unsure about" — the belief one act was standing on.

    §11's stale-memory row has to say something the agent *did* on the strength of something
    nobody measured. The obvious implementation — record an assumption whenever the row worked
    is unmeasured — is a row behind every action in the episode, because a subgoal you have not
    done yet is by definition not satisfied. Here the tray's clarity is the unanswered question
    and the placement is the act standing on it.
    """
    _, bworld, bplanner = blocked_case
    stray, target, eid = "obj_blocker_1", "tray_middle", "obj_red_1"
    pairs = [(eid, target)]
    blind_world = unlocated(bworld, stray)
    ver = ScriptVerifier()
    _, plan = _plan_for(pairs, blind_world, verifier=ver, planner=bplanner)
    clear = _row(plan, clear_predicate(stray, target))
    place = _row(plan, placement_predicate(eid, target))
    assert clear.satisfied.value == "unknown", "the frame did not see the blocker at all"
    assert place.depends_on == [clear.subgoal_id]

    wm = WorkingMemory()
    wm.sync(plan, blind_world, verifier=ver, round_index=1)
    wm.commit(decide(args={"object_id": eid, "target_id": target}, subgoal=place.subgoal_id),
              plan, blind_world)
    asm, = wm.state.assumptions
    assert asm.provenance["predicate_id"] == clear_predicate(stray, target)
    assert asm.provenance["worked_row_key"] == row_key(place)
    assert "no instrument had answered it" in asm.text

    doing_the_work = WorkingMemory()
    doing_the_work.sync(plan, blind_world, verifier=ver, round_index=1)
    doing_the_work.commit(decide(skill="pick", args={"object_id": stray},
                                 subgoal=clear.subgoal_id), plan, blind_world)
    assert doing_the_work.state.assumptions == [], "acting to make a predicate true is not " \
                                                   "assuming it was already true"


def test_a_refuted_assumption_stays_in_the_ledger_naming_the_round_it_died(blocked_case):
    """Invalidated, not deleted: §5.3 asks for 已失效假设, and a report needs the count."""
    _, bworld, bplanner = blocked_case
    stray, target, eid = "obj_blocker_1", "tray_middle", "obj_red_1"
    pairs = [(eid, target)]
    blind_world = unlocated(bworld, stray)
    ver = ScriptVerifier()
    _, plan = _plan_for(pairs, blind_world, verifier=ver, planner=bplanner)
    clear = _row(plan, clear_predicate(stray, target))
    place = _row(plan, placement_predicate(eid, target))
    wm = WorkingMemory()
    wm.sync(plan, blind_world, verifier=ver, round_index=1)
    wm.commit(decide(args={"object_id": eid, "target_id": target}, subgoal=place.subgoal_id),
              plan, blind_world)
    asm, = wm.state.assumptions
    assert asm.holds.value == "unknown"

    wm.sync(plan, bworld, verifier=ver, round_index=5)
    assert asm.invalidated_at_round == 5 and asm.holds.value is False, \
        "the frame that could see the blocker says the tray was not clear"
    assert asm.invalidated_by_ref == bworld.observation_ref
    assert len(wm.state.assumptions) == 1
    named = wm.obligation_check(status="timeout", plan=plan)[
        "invalid_assumptions_acted_on"]
    assert [x["assumption_id"] for x in named] == [asm.assumption_id]
    assert named[0]["refuted_by"] == f"{clear_predicate(stray, target)} measured false"
    assert wm.state.unresolved_obligations() == 1, "what is owed is the open commitment; a " \
                                                   "refuted assumption is reported by " \
                                                   "`invalid_assumptions_acted_on`, counted once, " \
                                                   "and never re-believed"

    wm.sync(plan, vacated(bworld, stray, target), verifier=ver, round_index=6)
    assert asm.invalidated_at_round == 5, "a later agreement does not resurrect the belief"


# --------------------------------------------------------------- online + bounded ----


def test_the_ledger_outlives_the_feedback_window_and_the_budget_sheds_detail_not_counts(
        real, ids, regions):
    """The whole point of §5.3, and the reason the render is not just `str(state)`.

    v0.1's payload carried the last few feedbacks: a promise made on round 1 was gone by round
    12, which is how an agent with a perfect memory for objects and none for obligations fails
    §8's task. Here the round-1 promise is still on the page at round 25, and when the budget
    cuts lines it says how many it cut.
    """
    a = ids[0]
    tp = TaskPlanner()
    ver = ScriptVerifier()
    pairs = [(e, t) for e, t in zip(ids, regions)]
    task = task_for(["objects not part of the task keep their place"])
    goal = goal_for(pairs)
    plan = tp.initial(task, goal, real[2], verifier=ver, planner=real[3],
                      progress=progress_for(pairs))
    wm = WorkingMemory()
    first = None
    for r in range(1, 26):
        plan = tp.refresh(plan, real[2], verifier=ver, planner=real[3], task=task, goal=goal,
                          progress=progress_for(pairs)).plan
        wm.sync(plan, real[2], verifier=ver, round_index=r)
        if r == 1:
            dec = decide(args={"object_id": a}, subgoal=plan.subgoals[0].subgoal_id,
                         expected="the first promise of the episode")
            wm.commit(dec, plan, real[2])
            first = wm.state.commitments[0]
    assert first in wm.state.commitments and first.discharged.value is False
    out = wm.render()
    assert len(out["text"]) <= RENDER_BUDGET_CHARS
    assert "the first promise of the episode" in out["text"]
    assert out["counts"]["commitments_open"] == 1
    assert not out["counts"]["over_budget"]

    # A budget too small for even the counts line cannot be honoured, and says so instead of
    # truncating the number it exists to protect.
    tight = WorkingMemory(render_budget=120)
    tight.state = wm.state
    small = tight.render()
    assert small["lines_dropped"] > 0 and small["counts"]["over_budget"]
    assert small["header"] in small["text"] and "not one of them" in small["text"]
    assert small["counts"]["uncuttable_chars"] > 120

    # Anything larger is honoured exactly, apology included — which is why the apology is paid
    # for out of the budget before a single detail line is admitted.
    roomy = WorkingMemory(render_budget=small["counts"]["uncuttable_chars"] + 200)
    roomy.state = wm.state
    cut = roomy.render()
    assert cut["lines_dropped"] > 0 and not cut["counts"]["over_budget"]
    assert len(cut["text"]) <= roomy.render_budget
    assert cut["header"] == out["header"], "the same counts at a quarter of the budget"
    assert cut["text"] != out["text"]


def test_the_recall_pool_is_a_second_pool_the_debt_block_never_notices(real, ids, regions):
    """§5.4's rows share the *page* with §5.3's debts and are charged to nothing else.

    Three renders of one ledger: no episodic arm looked, an arm looked and found nothing relevant,
    an arm showed a row. The first two are byte-identical — reserving room for an apology that has
    nothing to apologize for is how a cold start quietly shrank the pool P2 measured — and the third
    keeps the first two as a literal prefix, because a memory able to renumber a debt line is a
    memory able to hide one. The `starved` render at the end is the same claim from the other side:
    a §5.4 pool too small to show anything says so and still leaves the debts alone.
    """
    tp, ver = TaskPlanner(), ScriptVerifier()
    pairs = list(zip(ids, regions))
    task, goal = task_for([]), goal_for(pairs)
    plan = tp.initial(task, goal, real[2], verifier=ver, planner=real[3],
                      progress=progress_for(pairs))
    wm = WorkingMemory()
    for r in range(1, 4):
        plan = tp.refresh(plan, real[2], verifier=ver, planner=real[3], task=task, goal=goal,
                          progress=progress_for(pairs)).plan
        wm.sync(plan, real[2], verifier=ver, round_index=r)
    wm.commit(decide(args={"object_id": ids[0]}, subgoal=plan.subgoals[0].subgoal_id,
                     expected="return the blocker to its tray"), plan, real[2])
    row = RecalledExperience(experience_id="exp_x", relevance=4.0,
                             match_terms=["task_kind:dev"], injected_into_round=3)
    line = "recall[rec_exp_x] dev/dev_c1: an episode that worked\n  did: pick obj_a"

    blind = wm.render()
    cold = wm.render(recalled=[])
    warm = wm.render(recalled=[(row, line)])
    assert blind["text"] == cold["text"] and blind["lines"] == cold["lines"]
    assert blind["recalled"] is None and cold["recalled"] == []
    assert blind["counts"]["recalled_offered"] is None
    assert (cold["counts"]["recalled_offered"] == cold["counts"]["recalled_shown"]
            == cold["counts"]["recalled_dropped"] == 0)
    assert warm["text"].startswith(cold["text"]), "a recall reached into the debt block"
    assert line in warm["text"]
    assert warm["counts"]["recalled_chars"] == len(line) + 1
    assert warm["counts"]["recalled_shown"] == 1 == warm["counts"]["recalled_offered"]
    for name, out in (("none", blind), ("cold", cold), ("warm", warm)):
        assert out["counts"]["debt_chars"] == cold["counts"]["debt_chars"], name
        assert not out["counts"]["over_budget"], name
        assert not out["counts"]["page_over_budget"], name
        assert out["counts"]["recall_budget"] == RECALL_BUDGET_CHARS, name
    # The structured copy `memory_view` publishes beside the line is counted apart from it, so no
    # reader mistakes the text pool for the section's whole price.
    assert cold["counts"]["recalled_json_chars"] == 0
    assert warm["counts"]["recalled_json_chars"] > 0

    starved = WorkingMemory(recall_budget=20)
    starved.state = wm.state
    thin = starved.render(recalled=[(row, line)])
    assert thin["counts"]["recalled_shown"] == 0
    assert thin["counts"]["recalled_dropped"] == 1 == thin["counts"]["recalled_offered"]
    assert thin["recalled"] == [] and thin["counts"]["recalled_json_chars"] == 0
    assert "recalled experience line(s) left out" in thin["text"]
    assert thin["text"].startswith(cold["text"]), "the cut reached into the debt block"
    assert len(thin["text"]) <= RENDER_BUDGET_CHARS + 20


def test_working_memory_writes_nothing_into_the_plan_or_the_world(real, ids, regions):
    """Rule 2, checked structurally: the ledger is a reader.

    If a memory module could edit a plan row, `plan v{n}` would stop meaning "what the planner
    authored" and start meaning "what the ledger last liked".
    """
    _, _, world, planner, verifier = real
    pairs = [(e, t) for e, t in zip(ids, regions)]
    _, plan = _plan_for(pairs, world, verifier=verifier, planner=planner)
    before_plan, before_world = plan.model_dump(), world.model_dump()
    wm = WorkingMemory()
    wm.sync(plan, world, verifier=verifier, round_index=1)
    for sub in plan.subgoals[:2]:
        wm.commit(decide(args={"object_id": sub.target_entity_ids[0]}, subgoal=sub.subgoal_id),
                  plan, world, subgoal=sub)
        wm.settle(None, feedback(skill="place", status="failure", code="NoFixture"), world,
                  plan=plan, verifier=verifier)
    wm.obligation_check(status="timeout", plan=plan)
    assert plan.model_dump() == before_plan
    assert world.model_dump() == before_world


def test_the_state_round_trips_through_the_log_and_recomputes_its_own_counts(real, ids, regions):
    """§6's envelope rule, and the reason the ledger holds records rather than counters."""
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    wm = WorkingMemory(episode_id="ep_x")
    wm.sync(plan, real[2], verifier=ver, round_index=4)
    row = _row(plan, placement_predicate(a, left))
    wm.commit(decide(args={"object_id": a}, subgoal=row.subgoal_id), plan, real[2], subgoal=row)
    wm.settle(None, feedback(skill="place", status="failure", code="GripSlip"), real[2],
              plan=plan, verifier=ver)
    rebuilt = WorkingMemoryState.model_validate(wm.state.model_dump())
    assert rebuilt.episode_id == "ep_x" and rebuilt.round_index == 4
    assert rebuilt.unresolved_obligations() == wm.state.unresolved_obligations() == 1
    later = WorkingMemory()
    later.state = rebuilt
    assert later.render()["counts"] == wm.render()["counts"]
    assert summarize_memory([wm.state, rebuilt])["commitments"] == 1
    assert summarize_memory([]) == {"snapshots": 0}


def test_an_unanswered_question_the_plan_asked_is_carried_as_one(real, ids, regions):
    """The `observe` rows P2-a authors have to surface somewhere the model reads."""
    a, b = ids[0], ids[1]
    left = regions[0]
    from embodied_agent.planning.task_planner import observe_subgoal
    row = observe_subgoal(placement_predicate(b, left), f"is {b} seated in {left}?",
                          resolves=["front_left"], entity_id=b, region_id=left)
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    plan = TaskPlanner().refresh(plan, real[2], verifier=ver, planner=real[3],
                                 new_obligations=[row]).plan
    wm = WorkingMemory()
    wm.sync(plan, real[2], verifier=ver, round_index=2)
    assert wm.state.open_questions and "answered by no frame yet" in wm.state.open_questions[0]
    check = wm.obligation_check(status="timeout", plan=plan)
    assert check["open_questions"] == wm.state.open_questions
    assert check["owed_subgoals"], "an unanswered question is also an owed row"


def test_a_clear_row_promise_is_kept_by_the_occupancy_rule_not_by_negation(blocked_case):
    """The debt and the plan must be answered by the same instrument, or they disagree.

    `clear:` is checked against occupancy (`task_planner.evidence_for`), so a promise to get a
    blocker out of a tray closes exactly when the plan's own `clear:` row closes. It is never the
    negation of `placed:`: that predicate reads false for a body pinned against the tray wall as
    readily as for one that left, and a debt closed on it would have been closed while the
    descent envelope was still occupied.
    """
    _, bworld, bplanner = blocked_case
    stray, tid = "obj_blocker_1", "tray_middle"
    pairs = [("obj_red_1", tid)]
    placement_says = PredicateVerdict.false       # "not placed in there" — the wrong question
    ver = ScriptVerifier({placement_predicate(stray, tid): placement_says})
    tp, plan = _plan_for(pairs, bworld, verifier=ver, planner=bplanner)
    row = _row(plan, clear_predicate(stray, tid))

    wm = WorkingMemory()
    dec = decide(skill="pick", args={"object_id": stray}, subgoal=row.subgoal_id,
                 expected=f"{stray} is no longer inside {tid}")
    wm.commit(dec, plan, bworld, subgoal=row)
    c = wm.state.commitments[0]
    assert c.provenance["predicate_id"] == clear_predicate(stray, tid)
    wm.sync(plan, bworld, verifier=ver, round_index=2)
    assert wm.state.assumptions == [], "the row was measured, so nothing was assumed about it"
    assert c.discharged.value is False
    assert c.discharged.evidence_refs == [bworld.observation_ref], \
        "the evidence is the frame that read the occupancy, not a placement verdict"

    gone = vacated(bworld, stray, tid)
    wm.settle(dec, feedback(skill="pick", status="success", post=gone.observation_ref), gone,
              plan=plan, verifier=ver)
    assert c.discharged.value is True
    assert c.discharged.evidence_refs == [gone.observation_ref]
    check = wm.obligation_check(status="timeout", plan=plan)
    assert check["open_commitments"] == [] and wm.state.unresolved_obligations() == 0
    assert len(check["owed_subgoals"]) == 2, "the rows still say what the last refresh measured: " \
                                             "closing a debt in the ledger does not re-answer " \
                                             "the plan, and does not pretend to"
    gone_plan = tp.refresh(plan, gone, verifier=ver, planner=bplanner, task=task_for(),
                           goal=goal_for(pairs), progress=progress_for(pairs)).plan
    assert _row(gone_plan, clear_predicate(stray, tid)).satisfied.value is True
    check = wm.obligation_check(status="timeout", plan=gone_plan)
    assert [r["row_key"] for r in check["owed_subgoals"]] == \
        [row_key(_row(gone_plan, placement_predicate("obj_red_1", tid)))], \
        "the tray the blocker left is owed to obj_red_1, and that is the plan's report"


# ------------------------------------------------------------------ the ablation gate ----


def test_both_new_event_types_are_attributed_to_working_memory():
    """§9: `w/o Working Memory` has to be a switch, and a switch needs a denominator.

    `Ablation.violations()` reads `EVENT_MODULE`, so an event type the registry does not know
    raises rather than passing quietly — which is why this test exists next to the planner's, and
    why the runtime wiring (P2-c) must emit these two types and no others for this module.
    """
    arm = ablation("wo_working_memory")
    assert arm.violations(["working_memory", "obligation_check"]) == \
        ["obligation_check", "working_memory"]
    assert arm.violations(["plan", "task_understanding"]) == []
    assert EVENT_MODULE["working_memory"] == "working_memory"
    assert EVENT_MODULE["obligation_check"] == "working_memory"
    assert not arm.enabled("working_memory")


def test_row_for_reads_a_reference_and_does_not_choose_one(real, ids, regions):
    """`row_for` resolves the four names a decision may use, and invents none.

    The last assertion is the one that matters: a reference that matches no row returns None, so
    the ledger records "this decision worked on nothing the plan knows about" instead of
    quietly filing it under the nearest statement.
    """
    a, left = ids[0], regions[0]
    ver = ScriptVerifier()
    _, plan = _plan_for([(a, left)], real[2], verifier=ver, planner=real[3])
    row = _row(plan, placement_predicate(a, left))
    for ref in (row.subgoal_id, row.predicate_id, row.statement, row_key(row)):
        assert row_for(plan, ref).subgoal_id == row.subgoal_id
    assert row_for(plan, "") is None
    assert row_for(plan, placement_predicate(a, regions[1])) is None
    assert row_for(plan, "tray") is None, "a substring match would be the planner guessing " \
                                          "what the model meant"
