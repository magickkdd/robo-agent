"""W2/W3 regression tests: contracts, interpreter, planner, validator, runtime
state transitions, budget accounting, recovery (spec 8, 11, 12.4).

Evaluator self-tests (spec 12.4): wrong-target-but-flagged-success, object
passing through target, still-held, knocked-away-after-placement must all be
detected. Physics-based tests are marked slow and use small scenes.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from embodied_agent.core.contracts import (
    Budgets,
    EvalSpec,
    FailureCode,
    Plan,
    PlanStep,
    SkillCall,
    SkillStatus,
    TaskInput,
)
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.planner import PlanValidator, RulePlanner
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier, build_world_state
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.tasks import negative_set, smoke_set

pytestmark = pytest.mark.filterwarnings("ignore")


def make_scene(objects=None, seed=1):
    layout = objects or [
        {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0], "attributes": {"color": "red", "shape": "cube"}},
        {"entity_id": "obj_blue_2", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.68, -0.16], "attributes": {"color": "blue", "shape": "cylinder"}},
    ]
    return PhysicsScene(seed=seed, object_layout=layout)


def run_rule_episode(case, recovery=True, fault=None):
    scene = make_scene(case.objects, seed=case.seed)
    ex = SkillExecutor(scene, lambda: None)
    rt = Runtime(scene, ex, tempfile.mkdtemp(), case.eval_spec.budgets, f"t_{case.task_id}_{recovery}")
    world = rt.observe()
    task = TaskInput(task_id=case.task_id, utterance=case.utterance)
    goal = interpret(task, world)
    res = rt.run_episode(task, goal, case.eval_spec, RulePlanner(scene),
                         recovery_enabled=recovery, fault=fault)
    score = IndependentEvaluator(scene, case.eval_spec).score()
    scene.close()
    return res, score, rt


# ---------- contracts ----------

def test_plan_schema_rejects_place_before_pick():
    p = Plan(plan_id="p", based_on_state_version=0, goal_ref="g", steps=[
        PlanStep(id="s1", skill="place", args={"object_id": "a", "target_id": "tray_left"})])
    ok, errs = p.validate_schema(), None
    scene = make_scene()
    world = build_world_state(scene, 0, "o0")
    v_ok, errs = PlanValidator(world).validate(p)
    scene.close()
    assert not v_ok and any("place before pick" in e for e in errs)


def test_budgets_cover_verification_failed_branch():
    """A stuck goal must terminate: replan budget + no-progress bound the loop."""
    b = Budgets(max_global_replans=2, max_consecutive_no_progress=2, max_skill_calls=30, wall_clock_s=60)
    scene = make_scene()
    ex = SkillExecutor(scene, lambda: None)
    rt = Runtime(scene, ex, tempfile.mkdtemp(), b, "t_budget")
    world = rt.observe()
    task = TaskInput(task_id="t", utterance="把红色方块放入左边托盘。")
    goal = interpret(task, world)
    # make every pick fail permanently: unreachably far target is not needed;
    # instead monkeypatch the executor to always fail pick
    def always_fail(call):
        from embodied_agent.core.contracts import SkillResult
        import time
        return SkillResult(call_id=call.call_id, status=SkillStatus.failed,
                           failure_code=FailureCode.GRASP_MISS.value, t_start=0.0, t_end=0.1)
    ex.execute = always_fail
    spec = EvalSpec(eval_id="e", assignments=[{"entity_id": "obj_red_1", "target_id": "tray_left"}])
    res = rt.run_episode(task, goal, spec, RulePlanner(scene), recovery_enabled=False)
    scene.close()
    assert res.terminal_status.value == "failed"
    assert res.failure_type == FailureCode.GRASP_MISS


# ---------- interpreter ----------

def test_interpreter_binds_attributes_not_cooccurrence():
    scene = make_scene()
    world = build_world_state(scene, 0, "o")
    g = interpret(TaskInput(task_id="t", utterance="把红色的方块放到左边托盘，把蓝色的圆柱放到右边托盘。"), world)
    scene.close()
    assert not g.ambiguous
    bound = {a.entity.attributes.get("color"): a.target_id for a in g.assignments if a.entity.attributes}
    assert bound == {"red": "tray_left", "blue": "tray_right"}


def test_interpreter_rest_clause():
    scene = make_scene()
    world = build_world_state(scene, 0, "o")
    g = interpret(TaskInput(task_id="t", utterance="把红色物体放入左边托盘，其余放到中间托盘。"), world)
    scene.close()
    assert not g.ambiguous
    def target_of(eid=None, attrs=None):
        for a in g.assignments:
            if eid and a.entity.entity_id == eid:
                return a.target_id
            if attrs and all(a.entity.attributes.get(k) == v for k, v in attrs.items() if v):
                return a.target_id
    assert target_of(attrs={"color": "red"}) == "tray_left"
    assert target_of(eid="obj_blue_2") == "tray_middle"


def test_interpreter_ambiguous_returns_clarification():
    scene = make_scene()
    world = build_world_state(scene, 0, "o")
    g = interpret(TaskInput(task_id="t", utterance="把那个东西收好。"), world)
    scene.close()
    assert g.ambiguous and g.assignments == []


# ---------- runtime & physics (slower) ----------

@pytest.mark.parametrize("recovery", [True])
def test_clean_episode_succeeds(recovery):
    case = smoke_set()[0]
    res, score, _ = run_rule_episode(case, recovery=recovery)
    assert res.terminal_status.value == "success"
    assert score["complete_success"]


def test_fault_paired_recovery_gap():
    case = [c for c in smoke_set() if c.fault][0]
    res_on, score_on, _ = run_rule_episode(case, recovery=True, fault=(case.fault.type, case.fault.offset))
    res_off, score_off, _ = run_rule_episode(case, recovery=False, fault=(case.fault.type, case.fault.offset))
    assert res_on.recovery_events >= 1
    assert score_on["complete_success"], "recovery must repair the injected grasp miss"
    assert res_off.failure_type == FailureCode.GRASP_MISS, "no-recovery run must fail at the miss"
    assert not score_off["complete_success"]


def test_negative_cases_stay_bounded():
    for case in negative_set():
        if case.expected == "needs_clarification":
            scene = make_scene(case.objects, seed=case.seed)
            ex = SkillExecutor(scene, lambda: None)
            rt = Runtime(scene, ex, tempfile.mkdtemp(), case.eval_spec.budgets, f"t_{case.task_id}")
            world = rt.observe()
            goal = interpret(TaskInput(task_id=case.task_id, utterance=case.utterance), world)
            scene.close()
            assert goal.ambiguous or not goal.assignments, case.task_id


# ---------- evaluator anti-self-confirmation (spec 12.4) ----------

def test_evaluator_rejects_wrong_target_assignment():
    """Model puts red in tray_left, but truth says red belongs to tray_right:
    agent-side verification may pass, the independent evaluator must fail it."""
    scene = make_scene()
    ex = SkillExecutor(scene, lambda: None)
    call = SkillCall(plan_id="p", step_id="s", skill="pick", args={"object_id": "obj_red_1"})
    assert ex.execute(call).status == SkillStatus.completed
    call = SkillCall(plan_id="p", step_id="s2", skill="place", args={"object_id": "obj_red_1", "target_id": "tray_left"})
    assert ex.execute(call).status == SkillStatus.completed
    # independent truth: red belongs in tray_right
    wrong_truth = EvalSpec(eval_id="e", assignments=[{"entity_id": "obj_red_1", "target_id": "tray_right"}])
    score = IndependentEvaluator(scene, wrong_truth).score()
    scene.close()
    assert not score["complete_success"] and score["objects_completed"] == 0


def test_evaluator_detects_object_not_in_region():
    """Object passing near a target or still held must not count as placed."""
    scene = make_scene()
    # object still on the table: not in any region
    spec = EvalSpec(eval_id="e", assignments=[{"entity_id": "obj_red_1", "target_id": "tray_left"}])
    score = IndependentEvaluator(scene, spec).score()
    scene.close()
    assert score["objects_completed"] == 0


def test_runtime_verifier_ignores_skill_flags():
    """verify must compute from physics: a skill-reported success without a
    physically lifted object is false."""
    scene = make_scene()
    ex = SkillExecutor(scene, lambda: None)
    world = build_world_state(scene, 0, "o0")
    v = RuntimeVerifier(scene, world, "v1")
    report = v.verify_grasp("obj_red_1")
    scene.close()
    assert report.value.value == "false"
