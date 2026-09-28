"""What slot does the fallback pick for the second object, and can the arm hold it?"""
import sys, os, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.contracts import SkillCall, TaskInput
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "slotprobe", config=case.verify)
ex.world_provider = rt.observe
orn = p.getQuaternionFromEuler([math.pi, 0, 0])


def call(skill, **args):
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
    print(f"  {skill} {args} -> {r.status.value} {r.failure_code} {(r.notes or [])[:3]}")
    return r


call("pick", object_id="obj_green_1")
call("place", object_id="obj_green_1", target_id="tray_left")

w = rt.observe()
pl = ex.placement_planner
cand, notes = pl.best_feasible("tray_left", "obj_yellow_3", w)
print("chosen:", cand.candidate_id if cand else None, "xy", cand.position_xy if cand else None)
print("notes (rejected ahead of it):")
for n in notes[:8]:
    print("   ", n)
for x, y, z in ((cand.position_xy[0], cand.position_xy[1], 0.80),
                (cand.position_xy[0], cand.position_xy[1], cand.rest_z + pl.GRIPPER_CLEARANCE_M)):
    print(f"  probe ({x:.3f},{y:.3f},{z:.3f}): reachable_claim={scene.reachable([x, y, z], orn)}"
          f" probe_from={np.round(scene.ee_pose()[0], 3)}")

call("pick", object_id="obj_yellow_3")
call("place", object_id="obj_yellow_3", target_id="tray_left")
w2 = rt.observe()
for eid in ("obj_green_1", "obj_yellow_3"):
    e = w2.entity(eid)
    print(f"  {eid} ({e.pose.position.x:.4f},{e.pose.position.y:.4f},{e.pose.position.z:.4f}) "
          f"sup={e.supported_by}")
scene.close()
