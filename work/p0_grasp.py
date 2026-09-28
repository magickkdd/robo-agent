"""Where does the held object end up 19 mm off the palm axis?

Replays the pick primitives and reports the object-minus-palm offset in the hand
frame (x along the approach-perpendicular, y along the finger separation axis) and
the finger joint positions after each stage.
"""
import sys, os, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene, FINGER_JOINTS
from embodied_agent.core.skills import SkillExecutor, _down_orn, APPROACH_CLEARANCE, GRASP_Z_OFFSET, LIFT_HEIGHT
from embodied_agent.evaluation.tasks import build_set

case = build_set("smoke")[0]
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
ex = SkillExecutor(scene, world_provider=lambda: None)
tmp = tempfile.mkdtemp()
from embodied_agent.core.runtime import Runtime
rt = Runtime(scene, ex, tmp, case.budgets, "grasp", config=case.verify)
ex.world_provider = rt.observe

r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick",
                         args={"object_id": "obj_yellow_3"}))
print("pick ->", r.status.value, {k: round(v, 4) for k, v in r.measurements.items()})
# the object is now held; re-run the primitive sequence to log each stage
scene.start_action(30.0, "diag")
scene.begin_execution()


def report(tag):
    ee, orn = scene.ee_pose()
    pos, o2 = scene.object_pose("obj_yellow_3")
    d = np.asarray(pos[:2]) - np.asarray(ee[:2])
    # with orn = [pi,0,0] the hand y axis maps to world -y, hand x to world x
    fingers = [p.getJointState(scene.robot, j)[0] for j in FINGER_JOINTS]
    lin, ang = scene.object_velocity("obj_yellow_3")
    print(f"{tag:26s} off_in_hand=(x={d[0]*1000:+6.1f} y={-d[1]*1000:+6.1f})mm "
          f"fingers=({fingers[0]:.4f},{fingers[1]:.4f}) obj_z={pos[2]:.4f} "
          f"|v|={np.linalg.norm(lin):.3f} |w|={np.linalg.norm(ang):.3f}", flush=True)


report("after pick skill")
# put it back down and re-grasp, logging every stage
ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="place",
                     args={"object_id": "obj_yellow_3", "target_id": "tray_middle"}))
scene.end_action()

oid = "obj_yellow_3"
scene.start_action(30.0, "diag2")
scene.begin_execution()
center = np.array(scene.grasp_candidates(oid)[0], dtype=float)
grasp = center + GRASP_Z_OFFSET * np.array([0.0, 0.0, 1.0])
orn = _down_orn()
scene.open_gripper()
stage_z = max(center[2] + APPROACH_CLEARANCE, 0.78 + 0.04)
print("approach", scene.move_ee([center[0], center[1], stage_z], orn, timeout_s=2.5))
report("staged above")
print("hover", scene.move_ee([center[0], center[1], grasp[2] + APPROACH_CLEARANCE], orn, timeout_s=1.5))
report("pre-grasp hover")
z = grasp[2] + APPROACH_CLEARANCE
floor_z = grasp[2] + 0.002
while z > floor_z:
    z = max(z - 0.01, floor_z)
    blocked = not scene.move_ee([grasp[0], grasp[1], z], orn, timeout_s=1.0, tol=8e-3, tcp_tol=0.03)
    if scene.held_bodies() or blocked:
        break
report("descended")
scene.close_gripper()
scene.settle(0.25)
report("after close+settle")
scene.move_ee([grasp[0], grasp[1], grasp[2] + LIFT_HEIGHT], orn, timeout_s=2.0)
report("after lift")
hx, hy, hz = scene.ee_pose()[0]
scene.move_ee([hx + 0.05, hy - 0.04, hz], orn, timeout_s=1.2)
report("after lateral drag test")
scene.end_action()
scene.close()
