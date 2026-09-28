"""Compare the kinematic probe (resetJointState residual) with the servoed
residual (gravity droop) across tray slots."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pybullet as p
from embodied_agent.core.scene import PhysicsScene

sc = PhysicsScene(seed=1, object_layout=[])
orn = p.getQuaternionFromEuler([math.pi, 0, 0])

def ik_residual(pt):
    sol = sc.ik(pt, orn)
    if not sc._within_limits(sol):
        return None
    saved = [p.getJointState(sc.robot, j)[0] for j in sc.ARM_JOINTS if False]
    import embodied_agent.core.scene as S
    saved = [p.getJointState(sc.robot, j)[0] for j in S.ARM_JOINTS + S.FINGER_JOINTS]
    for j, v in zip(S.ARM_JOINTS, sol):
        p.resetJointState(sc.robot, j, float(v), physicsClientId=sc.cid)
    pos, _ = sc.ee_pose()
    for j, v in zip(S.ARM_JOINTS + S.FINGER_JOINTS, saved):
        p.resetJointState(sc.robot, j, v, physicsClientId=sc.cid)
    sc._engage_current_motors()
    return float(np.linalg.norm(pos - np.asarray(pt)))

print(f"{'slot':>18} {'ik_res':>8} {'servo_res':>10} {'servo_ok':>8}")
for tid in ("tray_left", "tray_middle"):
    cx, cy = sc.trays[tid]["center"]
    for dx in (0.0, 0.02, 0.04, 0.06, 0.08):
        for z in (0.80,):
            pt = [cx + dx, cy, z]
            r1 = ik_residual(pt)
            sc.reset_arm()
            ok = sc.move_ee(pt, orn, timeout_s=2.0)
            pos, _ = sc.ee_pose()
            r2 = float(np.linalg.norm(pos - np.asarray(pt)))
            print(f"{tid[:4]}+x{dx:.2f} z={z:.2f} {('n/a' if r1 is None else f'{r1*1000:7.2f}')}mm"
                  f" {r2*1000:8.2f}mm {str(ok):>8}")
sc.close()
