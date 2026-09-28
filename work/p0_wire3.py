import os, sys, json, tempfile
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
rt = Runtime(scene, ex, tmp, case.budgets, "wire3", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
goal = interpret(task, rt.observe())
res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
for line in open(os.path.join(tmp, "events.jsonl"), encoding="utf-8"):
    e = json.loads(line)
    if e.get("type") in ("decision", "skill_call", "skill_result", "execution_feedback",
                         "goal_progress", "rejection", "observation"):
        print(json.dumps({k: v for k, v in e.items() if k != "ts"}, ensure_ascii=False)[:700])
        print("--")
scene.close()
