import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.sources import RulePolicySource
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[2] if len(build_set("smoke")) > 2 else build_set("smoke")[0]
case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
tmp = tempfile.mkdtemp()
rt = Runtime(scene, ex, tmp, case.budgets, "wire4", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
goal = interpret(task, rt.observe())
res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
print("terminal", res.terminal_status.value, res.failure_type, "objs", res.objects_completed, "/", res.objects_total)
for line in open(os.path.join(tmp, "events.jsonl"), encoding="utf-8"):
    e = json.loads(line); t = e.get("type"); p = e.get("payload") or {}
    if t == "decision":
        print(f"r{p.get('round_index')} decide {p.get('action')} {json.dumps(p.get('execute'), ensure_ascii=False)} :: {(p.get('rationale') or '')[:70]}")
    elif t == "execution_feedback":
        f = p.get("feedback") or {}
        print(f"   fb {f.get('skill')} {f.get('entity_id')}->{f.get('target_id')} {f.get('status')} {f.get('failure_code')} {(f.get('message') or '')[:90]}")
    elif t in ("action_rejected", "rejection", "decision_rejected", "bounded_exit", "episode_end", "goal_progress", "semantic_repair"):
        print(f"   {t} {json.dumps({k: v for k, v in p.items() if k not in ('world','verification')}, ensure_ascii=False)[:300]}")
scene.close()
