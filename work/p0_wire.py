import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.sources import RulePolicySource
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
tmp = tempfile.mkdtemp()
rt = Runtime(scene, ex, tmp, case.budgets, "wire1", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
w0 = rt.observe()
goal = interpret(task, w0)
res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
print("terminal", res.terminal_status.value, res.failure_type, "rounds", res.decision_rounds,
      "skills", res.skill_calls, "objs", res.objects_completed, "/", res.objects_total)
from embodied_agent.evaluation.evaluator import IndependentEvaluator
sc = IndependentEvaluator(rt.terminal_snapshot, case.eval_spec).score(res)
print("independent", sc["complete_success"], sc["objects_completed"], "/", sc["objects_total"], sc["protocol_notes"])
scene.close()
