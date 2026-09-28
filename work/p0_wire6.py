"""Does the same P0 base carry all three comparison modes?

A: one frozen plan, fixed repeats. B: continuous decisions. C: B with the model's
own feedback channel removed. Offline sources only, so a green run here says the
*base* works, not that a provider works.
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.planner import RulePlanner, OneShotPlanSource
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.sources import RulePolicySource, AblatedFeedbackSource
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]


def run(mode, build_source):
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    ex = SkillExecutor(scene, world_provider=lambda: None)
    rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, f"modes-{mode}", config=case.verify)
    ex.world_provider = rt.observe
    task = TaskInput(task_id=case.task_id, utterance=case.utterance)
    goal = interpret(task, rt.observe())
    source = build_source(rt, goal)
    res = rt.run_episode(task, goal, source, mode=mode)
    sc = IndependentEvaluator(rt.terminal_snapshot, case.eval_spec).score(res)
    kinds = sorted({e["type"] for e in rt.store.read_all()})
    print(f"mode {mode}: terminal={res.terminal_status.value} fail={res.failure_type} "
          f"rounds={res.decision_rounds} skills={res.skill_calls} "
          f"agent_objs={res.objects_completed}/{res.objects_total} "
          f"indep={sc['complete_success']} {sc['objects_completed']}/{sc['objects_total']} "
          f"http={res.http_requests} notes={sc['protocol_notes']}")
    print(f"   event kinds: {kinds}")
    scene.close()
    return res


def a_source(rt, goal):
    rt.initial_context(TaskInput(task_id=case.task_id, utterance=case.utterance), goal)
    plan = RulePlanner(case.verify).plan(goal, rt.observe(), plan_id="offline_a")
    return OneShotPlanSource(plan)


run("A", a_source)
run("B", lambda rt, goal: RulePolicySource(case.verify))
run("C", lambda rt, goal: AblatedFeedbackSource(RulePolicySource(case.verify)))
