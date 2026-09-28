"""How much does carrying an object add to the TCP residual? (calibration)"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pybullet as p
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.contracts import SkillCall

LAYOUT = [{"entity_id": "obj_green_1", "shape": "cube", "dims": [0.021]*3, "xy": [0.38, -0.16],
           "attributes": {"color": "green", "shape": "cube"}},
          {"entity_id": "obj_yellow_3", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.38, 0.16],
           "attributes": {"color": "yellow", "shape": "cylinder"}}]
sc = PhysicsScene(seed=1, object_layout=LAYOUT)
ex = SkillExecutor(sc, world_provider=lambda: None)
orn = p.getQuaternionFromEuler([math.pi, 0, 0])
print(ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick",
                           args={"object_id": "obj_yellow_3"})).status.value)
cx, cy = sc.trays["tray_left"]["center"]
print(f"{'slot':>16} {'empty_ik':>9} {'held_res':>9} {'held_ok':>8}")
for dx in (0.0, 0.02, 0.04, 0.06):
    for dy in (0.0, 0.04, -0.04, 0.08):
        pt = [cx + dx, cy + dy, 0.80]
        r_ik = sc.reachable(pt, orn, tcp_tol=1.0) and math.inf
        # measure the IK residual directly
        sol = sc.ik(pt, orn)
        import embodied_agent.core.scene as S
        saved = [p.getJointState(sc.robot, j)[0] for j in S.ARM_JOINTS + S.FINGER_JOINTS]
        for j, v in zip(S.ARM_JOINTS, sol):
            p.resetJointState(sc.robot, j, float(v), physicsClientId=sc.cid)
        pos, _ = sc.ee_pose()
        rik = float(np.linalg.norm(pos - np.asarray(pt)))
        for j, v in zip(S.ARM_JOINTS + S.FINGER_JOINTS, saved):
            p.resetJointState(sc.robot, j, v, physicsClientId=sc.cid)
        sc._engage_current_motors()
        ok = sc.move_ee(pt, orn, timeout_s=2.0)
        pos2, _ = sc.ee_pose()
        rh = float(np.linalg.norm(pos2 - np.asarray(pt)))
        print(f"  dx={dx:+.2f} dy={dy:+.2f} {rik*1000:8.2f} {rh*1000:8.2f} {str(ok):>8}")
sc.close()
