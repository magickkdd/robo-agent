"""Is the transfer failure a time budget or a non-converged servo?"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
from embodied_agent.core.scene import PhysicsScene
import embodied_agent.core.scene as S

sc = PhysicsScene(seed=1, object_layout=[])
orn = p.getQuaternionFromEuler([math.pi, 0, 0])
tgt = [0.70, -0.28, 0.80]

sc.move_ee([0.66, -0.32, 0.767], orn, timeout_s=3.0)
print("start pose", np.round(sc.ee_pose()[0], 4))
sol = sc.ik(tgt, orn)
print("ik sol   ", np.round(sol[:7], 3))
for t in (1.0, 3.0, 6.0, 12.0):
    sc.move_joints(sol, timeout_s=t, tol=8e-3)
    pos, _ = sc.ee_pose()
    err = float(np.linalg.norm(pos - np.asarray(tgt)))
    cur = np.array([p.getJointState(sc.robot, j)[0] for j in S.ARM_JOINTS])
    print(f"t={t:>4}: tcp_err={err * 1000:6.2f}mm joint_gap={np.max(np.abs(cur - np.array(sol[:7]))):.4f}rad "
          f"pose={np.round(pos, 4)} limits={np.round(np.abs(cur), 3)}")
sc.move_ee(tgt, orn, timeout_s=1.5)
print("move_ee direct:", np.round(sc.ee_pose()[0], 4), getattr(sc, "_last_tcp_err", None))
sc.close()
