"""What exactly kicks the cylinder into a sustained roll at hand-off?

Replays the place skill's own motion primitives with instrumentation: descent,
open, retreat, wait_until_rest.
"""
import sys, os, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene, SIM_DT
from embodied_agent.core.skills import SkillExecutor, _down_orn
from embodied_agent.evaluation.tasks import build_set
print("place_cal", None)

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
rt = Runtime(scene, ex, tempfile.mkdtemp(), case.budgets, "handoff", config=case.verify)
ex.world_provider = rt.observe

for skill, args in (("pick", {"object_id": "obj_green_1"}),
                    ("place", {"object_id": "obj_green_1", "target_id": "tray_left"}),
                    ("pick", {"object_id": "obj_blue_2"}),
                    ("place", {"object_id": "obj_blue_2", "target_id": "tray_right"}),
                    ("pick", {"object_id": "obj_yellow_3"})):
    r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
    print(f"  {skill} {args} -> {r.status.value}")

cand, notes = ex.placement_planner.best_feasible("tray_left", "obj_yellow_3", rt.observe())
print("candidate:", cand.candidate_id, cand.position_xy, "rest_z", cand.rest_z)
cx, cy = cand.position_xy
orn = _down_orn()
TR = ex.placement_planner
release_z = cand.rest_z + TR.GRIPPER_CLEARANCE_M
print(f"release_z={release_z:.4f} seat_break={TR.SEAT_BREAK_M}")

scene.start_action(30.0, "diag")
scene.begin_execution()


def st(tag):
    pos, _ = scene.object_pose("obj_yellow_3")
    lin, ang = scene.object_velocity("obj_yellow_3")
    ee, _ = scene.ee_pose()
    print(f"{tag:22s} ee=({ee[0]:.3f},{ee[1]:.3f},{ee[2]:.3f}) obj=({pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f}) "
          f"|v|={np.linalg.norm(lin):.4f} |w|={np.linalg.norm(ang):.4f}", flush=True)


cur, _ = scene.ee_pose()
scene.move_ee([cur[0], cur[1], TR.TRANSFER_Z], orn, timeout_s=2.0)
scene.move_ee([cx, cy, TR.TRANSFER_Z], orn, timeout_s=3.0)
st("over target")
z = TR.TRANSFER_Z
while z > release_z - 0.004:
    z = max(z - 0.005, release_z - 0.004)
    ok = scene.move_ee([cx, cy, z], orn, timeout_s=0.8, tol=6e-3, tcp_tol=0.02)
    pos, _ = scene.object_pose("obj_yellow_3")
    print(f"  descend z->{z:.4f} ok={ok} obj_z={pos[2]:.4f} "
          f"seated={pos[2] < cand.rest_z + TR.SEAT_BREAK_M}", flush=True)
st("pre-open")
scene.open_gripper()
for i in range(6):
    scene.settle(0.02)
    st(f"open+{(i + 1) * 20}ms")
ok = scene.move_ee([cx, cy, z + 0.10], orn, timeout_s=1.5)
st(f"retreat ok={ok}")
for i in range(10):
    scene.settle(0.1)
    st(f"retreat+{(i + 1) * 100}ms")
rest = scene.wait_until_rest("obj_yellow_3", max_s=5.0)
st(f"wait_until_rest={rest}")
scene.end_action()
scene.close()
