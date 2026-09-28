"""SPEC-v0.2 §5.2/§5.3/§7/§9 contract tests for the planning arm (P2-c).

`planning/arm.py` puts the plan and the memory into the decision loop, `planning/view.py` renders
them into the payload, and `planning/policy.py` answers from that payload. Three things had to be
shown before any of P2's numbers mean anything, and this file shows them:

* **the arm is wiring, not a strategist** — it moves six named seams, chooses no action, ranks no
  row, and adds no rejection reason to the validator's verdict, so the only difference between two
  arms is what was on the page;
* **the gate is the registry, not a branch** — with a module off, no record that module owns is
  filed and no section of the payload appears; `Ablation.violations()` is the referee and it raises
  on a type the registry does not know, so an invented record would fail every episode it touched;
* **the view is sufficient to act on** — a deterministic policy written against
  `ctx.model_payload()` carries a real episode to its end at zero spend in every planning arm, in a
  reproducible row order, which is what lets §9's planning contrast be measured before a model is
  billed for it.

One fixture runs five real episodes (the unarmed loop plus §9's four planning settings) on
`dev_c1` and the tests read that artifact. They assert structure and accounting, never a rate: a
threshold tuned until an arm-shaped test passes would be the number P2-e exists to measure.
"""
from __future__ import annotations

import ast
import inspect
import os
from collections import namedtuple

import pytest

from embodied_agent.core.contracts import (
    AttemptRecord,
    Decision,
    DecisionExecute,
    TerminalStatus,
)
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime, build_world_state
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.v02 import (
    ABLATION_CONDITIONS,
    EVENT_MODULE,
    SCHEMA_VERSION,
    V02_EVENT_TYPES,
    ablation,
)
from embodied_agent.evaluation.run import build_scene, task_input
from embodied_agent.evaluation.tasks import SET_NAMES, find_case
from embodied_agent.planning import policy as policy_module
from embodied_agent.planning.arm import (
    PLANNING_ARMS,
    PlannedPerceptRuntime,
    PlannedRuntime,
    PlanningMixin,
    build_planning_arm,
)
from embodied_agent.planning.policy import PlanPolicy
from embodied_agent.planning.view import (
    VIEW_KEYS,
    V02DecisionContext,
    plan_view,
    planning_coherence,
)
from embodied_agent.planning.subgoals import row_key

CASE = "dev_c1"
#: the modules each §9 planning arm switches off
MODULES_OFF = {"full": (), "wo_planning": ("planning",), "wo_working_memory": ("working_memory",),
               "wo_replanning": ("replanning",)}
#: the four `choice` values §5.7's record can carry. The wider list in the section — abandon,
# revise, replan, clarify — is a change of *goal or plan*, and each of those is its own record
# elsewhere; a recovery here is a follow-up action against the same row.
FILED_CHOICES = {"restore", "retry", "change_candidate", "re_observe"}
#: keys that would turn a record of a choice into a judgement of it. Absent by design (§12.3): the
# outcome is joined offline from the `decision_id`, never written alongside the intention.
OUTCOME_KEYS = {"outcome", "result", "success", "failed", "failure", "verified", "score", "reward",
                "useful", "progress_after", "feedback"}
#: which records each module owns, read out of the registry rather than restated from memory
OWNED = {m: tuple(t for t, owner in EVENT_MODULE.items() if owner == m) for m in
         ("planning", "working_memory", "replanning")}

Episode = namedtuple("Episode", "arm runtime policy result events s4 types by_type")


def _one(condition: str | None, root: str, task_case: str = CASE) -> Episode:
    """One real episode through the one production assembly point, at zero spend."""
    case = find_case(task_case, SET_NAMES)
    scene = build_scene(case)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, world)
        arm = None if condition is None else ablation(condition)
        episode_id = f"{task_case}.{condition or 'unarmed'}"
        ep_dir = os.path.join(root, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        store = EpisodeStore(ep_dir, episode_id)
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        runtime = build_planning_arm(case=case, scene=scene, executor=executor, store=store,
                                     run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets,
                                     perceive="privileged", ablation=arm,
                                     environment=EnvironmentController(scene, case.fresh_events()))
        executor.world_provider = runtime.observe
        policy = PlanPolicy()
        result = runtime.run_episode(task, goal, policy, mode="B")
        events = store.read_all()
        s4 = store.read_all(SCHEMA_VERSION)
        by_type: dict[str, list[dict]] = {}
        for e in events:
            by_type.setdefault(str(e["type"]), []).append(e)
        return Episode(condition, runtime, policy, result, events, s4,
                       sorted({str(e["type"]) for e in s4}), by_type)
    finally:
        scene.close()


@pytest.fixture(scope="module")
def episodes(tmp_path_factory) -> dict[str | None, Episode]:
    root = str(tmp_path_factory.mktemp("planning_arm"))
    return {c: _one(c, root) for c in (None, *PLANNING_ARMS)}


#: §8's shape with something in it that goes wrong: the P5-c batch read 5 `recovery_action` records
# and 1 `plan_revision` out of this case in every arm that is allowed to file them. `dev_c1` files
# none — nothing disturbs it — so it cannot tell a tally from a zero.
DISTURBING_CASE = "lh_c4_failed_grasp_recovery"


@pytest.fixture(scope="module")
def disturbed(tmp_path_factory) -> dict[str, Episode]:
    root = str(tmp_path_factory.mktemp("planning_arm_disturbed"))
    return {c: _one(c, root, DISTURBING_CASE) for c in ("full", "wo_replanning")}


# ------------------------------------------------------------- what the arm moves ----
def test_the_arm_moves_six_seams_and_names_the_rest_of_its_surface():
    """The docstring claims six seams. A seventh that the docstring does not mention is a finding.

    `context_class` is a declaration rather than a seam, and `files_ablation_claim` is a fact about
    which loop owns the `ablation` record; everything else the mixin defines that also exists on
    `Runtime` must be on the named list.
    """
    named = {"_begin_episode", "_build_context", "_validate_decision", "_build_feedback",
             "_record_feedback", "_finalize"}
    declared = {n for n, v in vars(PlanningMixin).items()
                if inspect.isfunction(v) and not n.startswith("__") and hasattr(Runtime, n)}
    assert declared == named, f"the arm overrides {sorted(declared)}"
    assert PlanningMixin.context_class is V02DecisionContext
    assert PlanningMixin.files_ablation_claim is True
    assert PlannedPerceptRuntime.files_ablation_claim is False, \
        "PerceptRuntime already files the arm claim, with the channel in it"
    # the mixin's own helpers are new names, not shadowed loop internals
    new = {n for n in vars(PlanningMixin) if not n.startswith("__")} - declared
    assert not {n for n in new if hasattr(Runtime, n) and n != "context_class"}, \
        "a name the loop already has must not be redefined by an arm"


def test_the_planned_loop_is_a_runtime_subclass_and_the_base_loop_is_untouched(episodes):
    """`Runtime` itself gains nothing: no v0.2 section, no context class, no plan attribute.

    The v0.1 regression is the claim that the untouched class still produces the untouched payload,
    so this is checked on `Runtime` and not only on the armed subclass.
    """
    assert issubclass(PlannedRuntime, Runtime) and PlanningMixin not in Runtime.__mro__
    assert Runtime.context_class is not V02DecisionContext
    assert not hasattr(Runtime, "plan") and not hasattr(Runtime, "memory")
    ctx = episodes[None].policy.seen[-1]
    assert isinstance(ctx, V02DecisionContext) and ctx.view_keys == VIEW_KEYS
    base_keys = set(Runtime.context_class.model_payload(ctx).keys())
    assert base_keys == set(ctx.model_payload()) - set(VIEW_KEYS)


# --------------------------------------------------------------- the payload gate ----
def test_an_absent_module_leaves_no_key_in_the_payload(episodes):
    """Which sections reached the page, per arm, with no `null` standing in for an off module.

    `"plan": null` and a sentence saying planning is switched off are both hints about the
    experiment; what `wo_planning` answers to is the v0.1 key set, so the assertion is a set
    relation and not a string match.
    """
    expect = {None: {"plan", "working_memory"}, "full": {"plan", "working_memory"},
              "wo_planning": {"working_memory"}, "wo_working_memory": {"plan"},
              "wo_replanning": {"plan", "working_memory"}}
    for condition, ep in episodes.items():
        seen = {tuple(sorted(row["sections"])) for row in ep.runtime.rounds}
        assert seen == {tuple(sorted(expect[condition]))}, f"{condition}: {seen}"
        payload = ep.policy.seen[-1].model_payload()
        for key in VIEW_KEYS:
            assert (key in payload) == (key in expect[condition]), f"{condition}/{key}"
    full_keys = set(episodes["full"].policy.seen[-1].model_payload())
    for condition in ("wo_planning", "wo_working_memory", "wo_replanning"):
        assert set(episodes[condition].policy.seen[-1].model_payload()) <= full_keys, condition


def test_the_two_sections_are_the_only_addition_to_the_round(episodes):
    """Strip both views and the v0.2 payload is byte-identical to the v0.1 one, key for key."""
    ctx = episodes["full"].policy.seen[-1]
    bare = ctx.model_copy(update={"planning_view": None, "memory_view": None})
    assert isinstance(bare, V02DecisionContext)
    assert set(bare.model_payload()) == set(ctx.model_payload()) - set(VIEW_KEYS)
    assert list(bare.model_payload()) == [k for k in ctx.model_payload() if k not in VIEW_KEYS], \
        "the sections are inserted at one named anchor, not appended where they happened to land"


# --------------------------------------------------------------- the record gate ----
def _recoveries(runtime) -> list[dict]:
    """Every `recovery_action` this runtime has filed, oldest first, read off the log."""
    return [e["payload"] for e in runtime.store.read_all(SCHEMA_VERSION)
            if e["type"] == "recovery_action"]


def test_no_record_is_filed_for_a_module_that_is_off(episodes):
    """The registry owns the gate, and `Ablation.violations()` is the referee."""
    for condition, ep in episodes.items():
        assert set(ep.types) <= set(V02_EVENT_TYPES), f"{condition} invented an event type"
        if condition is None:
            continue
        arm = ablation(condition)
        assert arm.violations(ep.types) == [], f"{condition}: {arm.violations(ep.types)}"
        for module in set(arm.modules_off):
            filed = [t for t in OWNED[module] if t in ep.types]
            assert not filed, f"{condition} filed {filed} while {module} was off"


def test_the_arm_that_loses_planning_loses_the_debts_only_a_plan_can_hold(episodes):
    """`wo_planning` is not "a plan that is not shown": no graph is authored at all.

    What the memory can still book — promises, failures, unanswered questions — is booked, and what
    only a row can hold (§8's displaced object that must be put back) is measurably absent. That is
    the honest reading of removing the module, and it is the reason the empty plan is a real
    `TaskPlan` rather than a `None`.
    """
    ep = episodes["wo_planning"]
    assert ep.runtime.plan is not None and not ep.runtime.plan.subgoals
    assert ep.runtime.plan.provenance["planning_ablated"] is True
    assert "plan" not in ep.types and "task_understanding" not in ep.types
    memory = ep.by_type.get("working_memory", [])
    assert memory, "the memory keeps its own lists with planning off"
    assert not any((r.get("payload") or {}).get("suspended_relations") for r in memory), \
        "no row can carry a suspended relation, so none may be claimed"


def test_the_log_agrees_with_itself_in_every_arm(episodes):
    """No version the model saw is missing from disk, one audit per episode, a row per recovery."""
    for condition, ep in episodes.items():
        findings = planning_coherence(ep.events)["findings"]
        assert findings == [], f"{condition}: {findings}"


def test_the_obligation_audit_precedes_the_result_it_qualifies(episodes):
    """§11's `obligation_check` is on the record before `episode_end` claims a status."""
    ep = episodes["full"]
    seq = {str(e["type"]): int(e["sequence"]) for e in
           (ep.by_type.get("obligation_check", []) + ep.by_type.get("episode_end", []))}
    assert seq["obligation_check"] < seq["episode_end"]
    payload = ep.by_type["obligation_check"][0]["payload"]
    assert payload["arm"] == "full"
    assert payload["terminal_status"] == TerminalStatus.success.value
    for key in ("open_commitments", "unrestored_relations", "owed_subgoals",
                "invalid_completion", "unresolved_obligations"):
        assert key in payload, f"§11's row is missing {key}"


def test_the_memory_arm_files_the_audit_once_and_the_ablated_arm_files_none(episodes):
    for condition in ("full", "wo_planning", "wo_replanning"):
        assert len(episodes[condition].by_type.get("obligation_check", [])) == 1, condition
    assert "obligation_check" not in episodes["wo_working_memory"].by_type
    assert "working_memory" not in episodes["wo_working_memory"].types


def test_the_result_reports_the_tallies_the_log_holds(episodes, disturbed):
    """The result object and `events.jsonl` are one account, not two that happen to be compared.

    P5-d found this was not true: `_finalize` left `recovery_events` and `replan_events` at their
    declared zeros while the same directory held 28 `recovery_action` and 4 `plan_revision` records
    per cell, so a reader of `EpisodeResult` alone concluded the loop never recovered. What the
    contract pins is the equality per arm, on a case that files both records and on one that files
    neither; `wo_replanning` is the arm where the two sides agree on a zero because the gate closed,
    which is the difference between a wiring gap and a switched-off module. `retry_events` is
    deliberately left at its declared zero: a retry is the `choice` field of a `recovery_action`
    record, not its own event, so nothing counts it.
    """
    assert not hasattr(Runtime, "recovery_events") and not hasattr(Runtime, "revision_events"), \
        "the base loop grew a tally, so the getattr fallback no longer means 'no planning arm'"
    for condition, ep in list(episodes.items()) + list(disturbed.items()):
        recoveries = sum(1 for e in ep.s4 if e["type"] == "recovery_action")
        revisions = sum(1 for e in ep.s4 if e["type"] == "plan_revision")
        assert ep.result.recovery_events == recoveries, f"{condition}: log {recoveries}"
        assert ep.result.replan_events == revisions, f"{condition}: log {revisions}"
        end = (ep.by_type["episode_end"][-1].get("payload") or {})["result"]
        assert end["recovery_events"] == recoveries and end["replan_events"] == revisions, condition
        assert end["retry_events"] == 0, f"{condition}: nothing counts a retry, so none may be claimed"
    full = disturbed["full"]
    assert full.result.recovery_events > 0 and full.result.replan_events > 0, \
        f"{DISTURBING_CASE} filed neither record, so the equality above would pass on two zeros"
    assert disturbed["wo_replanning"].result.recovery_events == 0, \
        "the recovery record is the replanning module's own; with it off neither side may claim one"


# ---------------------------------------------------------------- the validator ----
def test_the_arm_adds_no_rejection_reason(episodes):
    """The same decision, the same world: the arm's verdict is the base loop's verdict.

    If the gate could refuse, `wo_planning` would be "planning off **and** a different
    validator", and the success-rate difference would have two causes.
    """
    ep = episodes["full"]
    ctx = ep.policy.seen[-1]
    world = ctx.world
    good = Decision(context_id=ctx.context_id, based_on_state_version=world.state_version,
                    goal_ref=ctx.goal.goal_id, action="finish", rationale="test")
    bad = Decision(context_id="not-this-context",
                   based_on_state_version=world.state_version + 9,
                   goal_ref=ctx.goal.goal_id, action="finish", rationale="test")
    for decision in (good, bad):
        base = Runtime._validate_decision(ep.runtime, decision, ctx, world)
        armed = ep.runtime._validate_decision(decision, ctx, world)
        assert armed == base, f"{decision.context_id}: {base} != {armed}"


def test_a_refused_decision_books_no_promise(episodes):
    """§12.3: 以实际动作和最终状态为准. An action the executor never ran cannot open a debt.

    The attempt is still recorded by the feedback path — what is refused is the *commitment*, so
    the ledger cannot be padded by decisions that were rejected on the way in.
    """
    ep = episodes["full"]
    ctx = ep.policy.seen[-1]
    memory = ep.runtime.memory
    before = len(memory.state.commitments)
    bad = Decision(context_id="stale", based_on_state_version=ctx.state_version,
                   goal_ref=ctx.goal.goal_id, action="execute",
                   execute=DecisionExecute(skill="pick", args={"object_id": "obj_blue_3"}))
    ok, _ = ep.runtime._validate_decision(bad, ctx, ctx.world)
    assert not ok
    assert len(memory.state.commitments) == before, "a rejected decision opened a commitment"


# ------------------------------------------------------------------ the policy ----
def test_the_policy_has_no_ablation_branch():
    """The gate lives in the runtime arm. A policy that could read it is a second variable.

    Read off the AST rather than off the text: this module's own docstring has to *say* which
    experiment it is exempt from, and a check that fails on prose is a check nobody will keep. What
    must be absent is the name — no attribute, no argument, no local called `ablation`.
    """
    tree = ast.parse(inspect.getsource(policy_module))
    read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
        {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {k.arg for k in ast.walk(tree) if isinstance(k, ast.keyword) and k.arg}
    for arm_word in ("ablation", "condition", "modules_off", "arm"):
        assert arm_word not in read, f"the decision source reads {arm_word!r}: {sorted(read)}"


def test_the_policy_answers_from_the_payload_alone(episodes):
    """The same payload gets the same answer, and the plan's presence changes it.

    Both halves matter: an answer that moved between two calls on one context would make an arm
    comparison a measurement of the policy's mood, and an answer that did not move when the plan
    section appeared would mean the view is decoration.
    """
    full = episodes["full"]
    policy = PlanPolicy()
    ctx = full.policy.seen[-1]
    a, b = policy.decide(ctx), policy.decide(ctx)
    assert (a.action, a.execute and a.execute.skill, a.execute and a.execute.args,
            a.execute and a.execute.subgoal) == \
           (b.action, b.execute and b.execute.skill, b.execute and b.execute.args,
            b.execute and b.execute.subgoal)
    stripped = ctx.model_copy(update={"planning_view": None})
    c = policy.decide(stripped)
    assert (a.action, a.rationale) != (c.action, c.rationale) or a.action == c.action == "finish", \
        "the plan section changed nothing, so the view proves nothing"
    for row in policy.trace[-1]["views"]:
        assert row in VIEW_KEYS


def test_the_rule_policy_costs_nothing_in_any_arm(episodes):
    """P2's dev loop is measurable before a request is billed: the ledger's HTTP total is zero."""
    for condition, ep in episodes.items():
        assert ep.result.http_requests == 0, condition
        assert ep.result.prompt_tokens == 0 and ep.result.completion_tokens == 0, condition
        assert ep.policy.http_requests == 0 and ep.policy.decisions == ep.result.decision_rounds


def test_every_planning_arm_still_finishes_the_task_it_was_given(episodes):
    """No arm is allowed to make the loop unable to act at all.

    This is not a claim that planning helps: `wo_planning` completing `dev_c1` says the v0.1
    payload was enough for *this* task, which is the baseline P2-d's dependent task set is built to
    break. Rates are P2-e's measurement.
    """
    for condition, ep in episodes.items():
        assert ep.result.terminal_status in (TerminalStatus.success, TerminalStatus.failed), \
            f"{condition}: {ep.result.terminal_status}"
        assert ep.result.decision_rounds > 0 and ep.result.skill_calls > 0, condition


# -------------------------------------------------------------------- the view ----
def test_the_view_publishes_ready_in_row_key_order(episodes):
    """`ready` is a set whose order survives a redraw: `subgoal_id` is a random uuid4 suffix.

    An id-ordered offer visits the same rows in a different sequence in the next process, and every
    downstream "take the first ready row" then measures the draw instead of the plan.
    """
    plan = episodes["full"].runtime.plan
    keys = {s.subgoal_id: row_key(s) for s in plan.subgoals}
    ready = plan_view(plan, ready=plan.subgoals)["ready"]
    assert ready == sorted(ready, key=lambda sid: (keys[sid], sid))
    assert ready == plan_view(plan, ready=list(reversed(plan.subgoals)))["ready"], \
        "the published order is the rows' own, not the order the caller happened to pass"


def test_the_view_carries_the_row_the_agent_is_answerable_from(episodes):
    """Every field a later round has to reason about is in the view, not only a summary.

    `row_key`, `kind`, `status`, `satisfied`, `unmeasured`, `depends_on`, `attempts` and
    `completion_evidence` are what §11's dependency, unknown-handling and rework rows are computed
    from; a view that showed only sentences would leave the arithmetic to the reader.
    """
    plan = episodes["full"].runtime.plan
    view = plan_view(plan, ready=plan.subgoals)
    assert view["rows"], "an empty view proves nothing about sufficiency"
    for row in view["rows"]:
        assert set(row) >= {"subgoal_id", "row_key", "kind", "status", "statement", "satisfied",
                            "unmeasured", "depends_on", "attempts", "completion_evidence"}
    assert set(view) >= {"version", "ready", "rows", "dependencies", "changed", "violated",
                         "summary", "open_obligations", "provenance"}
    assert view["provenance"]["renderer_sha256"]


# -------------------------------------------------------------------- the arms ----
def test_the_planning_arms_are_registry_conditions_and_only_planning_ones():
    """§9's planning contrast, read off the frozen registry.

    `wo_vlm`, `wo_episodic_memory`, `wo_skill_acquisition` are other phases' claims and no planning
    setting turns them off; conversely every condition here must switch off exactly one module and
    it must be a module this arm can gate.
    """
    assert list(PLANNING_ARMS) == ["full", "wo_planning", "wo_working_memory", "wo_replanning"]
    assert set(PLANNING_ARMS) <= set(ABLATION_CONDITIONS)
    for condition in PLANNING_ARMS:
        assert tuple(ablation(condition).modules_off) == MODULES_OFF[condition], condition
    for module in {m for off in MODULES_OFF.values() for m in off}:
        assert module in EVENT_MODULE.values(), f"{module} has no record to gate"


def test_the_arm_claim_is_on_the_record_with_what_it_did_not_use(episodes):
    """A privileged planning arm is not §9's full system, and the record has to say so.

    `arm_coherence` refuses a `full` claim on a channel that never consults a vision model; the
    privileged world has no channel to be checked against, so the disclosure goes on the record —
    which is never rendered into a payload, so it informs the reader without telling the decision
    maker which experiment it is in.
    """
    for condition, ep in episodes.items():
        rows = ep.by_type.get("ablation", [])
        if condition is None:
            assert not rows, "an unclaimed episode files no claim"
            continue
        assert len(rows) == 1, condition
        notes = rows[0]["payload"]["notes"]
        assert "no vision model" in notes and condition in notes, notes
        assert rows[0]["payload"]["registry_sha256"]


def test_the_camera_arm_refuses_a_perceiver_it_was_not_given():
    """`build_arm` makes a perceiver out of a catalogue and a grounding map the caller owns.

    Reconstructing them here, or reading them off a finished runtime, would be a second definition
    of what a look costs; the named refusal sends the caller to the one builder instead.
    """
    case = find_case(CASE)
    with pytest.raises(ValueError, match="perceiver.*gmap|gmap.*perceiver"):
        build_planning_arm(case=case, scene=None, executor=None, store=None, run_dir="/tmp",
                           episode_id="x", budgets=case.budgets, perceive="stub")


def test_the_recovery_record_files_a_choice_and_never_its_outcome(episodes):
    """§5.7 as data: the record is about the decision, and grading it is a separate act.

    A repeat against the same object, region and geometry is a `retry`; the same work at another
    geometry is a `change_candidate`; a look after a failure is `re_observe`; and a row the plan
    marked `recover` is a `restore` whatever the skill was. What any of them *produced* is not
    written here — it is joined offline from the `decision_id` the execution feedback already
    carries, because announcing a recovery and scoring it in one breath is how a system starts
    grading its own intentions (§12.3).
    """
    ep = episodes["full"]
    runtime, ctx, plan = ep.runtime, ep.policy.seen[-1], ep.runtime.plan
    assert plan is not None and plan.subgoals, "the full arm was offered a plan"
    row = next((s for s in plan.subgoals if s.target_entity_ids and s.target_region_id), None)
    assert row is not None, "an arm whose plan has no (object, region) row tests nothing here"
    entity, region = row.target_entity_ids[0], row.target_region_id
    place = {"object_id": entity, "target_id": region}

    def attempt(**kw) -> AttemptRecord:
        return AttemptRecord(at_state_version=ctx.state_version, **kw)

    def claim(skill: str, args: dict, candidate: str | None = None) -> Decision:
        return Decision(context_id=ctx.context_id, based_on_state_version=ctx.state_version,
                        goal_ref=ctx.goal.goal_id, action="execute",
                        execute=DecisionExecute(skill=skill, args=args, subgoal=row.subgoal_id,
                                                candidate_id=candidate),
                        rationale="what the agent said it was about to do",
                        expected_effect="the effect it expected, written before the world answered")

    def filed() -> list[dict]:
        return [e["payload"] for e in runtime.store.read_all(SCHEMA_VERSION)
                if e["type"] == "recovery_action"]

    def classify(kind: str, decision: Decision, attempts) -> list[tuple[str, str]]:
        """One `_file_recovery` call against a row of `kind`; returns what it put on the log.

        The plan is replaced rather than mutated and restored in a `finally`, because the arm reads
        `self.plan` and a test that left a fabricated `recover` row on a module-scoped runtime would
        become a fact the later tests in this file inherited.
        """
        before = len(filed())
        runtime.plan = plan.model_copy(update={"subgoals": [
            row.model_copy(update={"kind": kind}), *plan.subgoals[1:]]})
        try:
            runtime._file_recovery(decision, ctx.model_copy(update={"attempts": list(attempts)}),
                                   ctx.world)
        finally:
            runtime.plan = plan
        return [(r["choice"], r["recovery_kind"]) for r in filed()[before:]]

    repeat = attempt(skill="place", entity_id=entity, target_id=region, candidate_id="cand_1")
    assert classify("recover", claim("place", place), []) == \
        [("restore", "temporary-move-restore")], "the row decides it, not the skill"
    assert classify("achieve", claim("place", place, "cand_1"), [repeat]) == \
        [("retry", "follow-up:retry")]
    assert classify("achieve", claim("place", place, "cand_2"), [repeat]) == \
        [("change_candidate", "follow-up:change_candidate")]
    assert classify("achieve", claim("observe", {"view": "main"}),
                    [attempt(skill="observe", entity_id=entity)]) == \
        [("re_observe", "look-for-evidence")]
    # a forward step never attempted is not a recovery, and a first look is not a re-look
    assert classify("achieve", claim("pick", {"object_id": entity}), []) == []
    assert classify("achieve", claim("observe", {"view": "main"}), []) == []
    assert runtime.plan is plan, "the fabricated row is gone, so no later test inherits it"

    rows = filed()
    assert rows, "the episode filed recoveries of its own, and this test added more"
    for r in rows:
        assert r["decision_id"], "without the join key the outcome can never be attached offline"
        assert r["arm"] == "full" and r["choice"] in FILED_CHOICES, r
        assert not (set(r) & OUTCOME_KEYS), f"a recovery record must not state its result: {r}"


def test_the_replanning_gate_owns_the_recovery_record_and_arms_agree_on_its_shape(episodes):
    """`wo_replanning` files no `recovery_action`, and the other arms file one from the same row.

    Driven rather than observed: `dev_c1`'s five rounds contain no repeated action, so a test that
    waited for an episode to happen to retry would pass by vacuity in one direction and fail by
    luck in the other. Re-validating a decision the loop already accepted, against a context whose
    attempt history names the same (skill, object, region, geometry), is the minimum that makes the
    classification fire — and the gate in `_validate_decision` is what this is measuring.

    The key set is compared across arms in the same breath because §9's contrast is only meaningful
    if the four arms' records join to each other; an arm that filed a narrower row would otherwise
    show up as a difference in behaviour when it is a difference in schema.
    """
    assert EVENT_MODULE["recovery_action"] == "replanning"
    keysets: dict[str, list[str]] = {}
    for condition, ep in episodes.items():
        runtime = ep.runtime
        by_ctx = {c.context_id: c for c in ep.policy.seen}
        chosen = next((d for d in runtime.decision_by_id.values() if d.action == "execute"), None)
        assert chosen is not None, f"{condition}: an arm that never executed proves nothing"
        ctx = by_ctx[chosen.context_id]
        args = dict(chosen.execute.args or {})
        repeat = AttemptRecord(skill=chosen.execute.skill, entity_id=args.get("object_id"),
                              target_id=args.get("target_id"),
                              candidate_id=chosen.execute.candidate_id,
                              at_state_version=ctx.state_version)
        before = len(_recoveries(runtime))
        ok, errs = runtime._validate_decision(chosen,
                                             ctx.model_copy(update={"attempts": [repeat]}),
                                             ctx.world)
        assert ok, f"{condition}: the loop refused its own accepted decision: {errs}"
        filed = _recoveries(runtime)[before:]
        on = True if runtime.ablation is None else bool(runtime.ablation.enabled("replanning"))
        assert len(filed) == int(on), f"{condition}: replanning on={on}, filed={len(filed)}"
        if filed:
            assert filed[0]["choice"] == "retry", filed[0]
            keysets[str(condition)] = sorted(filed[0])
    assert keysets, "no arm filed a record even with its gate open"
    assert len({tuple(v) for v in keysets.values()}) == 1, \
        f"the record's key set differs by arm: {keysets}"
    assert not set(keysets[next(iter(keysets))]) & OUTCOME_KEYS
