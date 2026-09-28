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
print("case", case.task_id, case.utterance, [o["entity_id"] for o in case.objects])
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
tmp = tempfile.mkdtemp()
rt = Runtime(scene, ex, tmp, case.budgets, "wire2", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
w0 = rt.observe()
goal = interpret(task, w0)
print("goal assignments", [(a.entity.entity_id or a.entity.attributes, a.target_id) for a in goal.assignments],
      "ambiguous", goal.ambiguous)
res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
print("terminal", res.terminal_status.value, res.failure_type)
for line in open(os.path.join(tmp, "events.jsonl"), encoding="utf-8"):
    e = json.loads(line)
    t = e.get("type")
    if t == "skill_result":
        p = e["payload"]
        print("  skill", p.get("skill"), p.get("status"), p.get("failure_code"),
              {k: v for k, v in (p.get("measured") or {}).items() if k in ("object_id","target_id","placed_at","candidate_id")})
    elif t == "decision":
        p = e["payload"]
        print("decide", p.get("action"), p.get("skill"), p.get("args"), (p.get("reason") or "")[:60])
    elif t == "execution_feedback":
        p = e["payload"]["feedback"]
        print("  fb", p.get("status"), p.get("failure_code"), (p.get("message") or "")[:120])
    elif t in ("rejection","bounded_exit","anomaly","goal_verification","plan_step"):
        print("  ", t, {k: v for k, v in e["payload"].items() if k in ("reason","reasons","status","code","message","note")} if isinstance(e.get("payload"), dict) else "")
scene.close()
