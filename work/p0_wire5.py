"""Round-by-round diagnostic: why does the rule policy keep re-picking obj_yellow_3?"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier, entity_footprint_half_xy
from embodied_agent.evaluation.sources import RulePolicySource
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
tmp = tempfile.mkdtemp()
rt = Runtime(scene, ex, tmp, case.budgets, "wire5", config=case.verify)
ex.world_provider = rt.observe
task = TaskInput(task_id=case.task_id, utterance=case.utterance)
w0 = rt.observe()
goal = interpret(task, w0)

_orig_progress = RuntimeVerifier.progress
_seen_ref = [None]

def progress(self, g):
    items = _orig_progress(self, g)
    if self.world.observation_ref != _seen_ref[0]:
        _seen_ref[0] = self.world.observation_ref
        print(f"--- {self.world.observation_ref} sim_t={self.world.sim_time:.3f} "
              f"held={self.world.held_object}", flush=True)
        for it in items:
            if it.value.value != "true":
                rep = self.verify_placement(it.entity_id, it.target_id) if self.world.has_entity(it.entity_id) else None
                ev = rep.evidence if rep else {}
                un = rep.unmeasured if rep else []
                ent = self.world.entity(it.entity_id)
                print(f"   PENDING {it.entity_id}->{it.target_id} {it.value.value} unmeasured={un} "
                      f"pos=({ent.pose.position.x:.4f},{ent.pose.position.y:.4f},{ent.pose.position.z:.4f}) "
                      f"foot=({entity_footprint_half_xy(ent)[0]:.4f},{entity_footprint_half_xy(ent)[1]:.4f}) "
                      + " ".join(f"{k}={v}" for k, v in ev.items()), flush=True)
    return items

RuntimeVerifier.progress = progress

_orig_rec = Runtime._record_feedback

def rec(self, fb):
    print(f">>> {fb.skill} {fb.entity_id}->{fb.target_id} cand={fb.candidate_id} "
          f"status={fb.status} fail={fb.failure_code} exec={fb.executed}", flush=True)
    return _orig_rec(self, fb)

Runtime._record_feedback = rec

res = rt.run_episode(task, goal, RulePolicySource(case.verify), mode="B")
print("terminal", res.terminal_status.value, res.failure_type, "rounds", res.decision_rounds,
      "skills", res.skill_calls, "objs", res.objects_completed, "/", res.objects_total)
scene.close()
