"""Measured holding envelope: max reachable x per |y| at transfer and release heights."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pybullet as p
from embodied_agent.core.scene import PhysicsScene

sc = PhysicsScene(seed=1, object_layout=[])
orn = p.getQuaternionFromEuler([math.pi, 0, 0])
print(f"{'|y|':>6} " + " ".join(f"z={z:.2f}" for z in (0.80, 0.74, 0.70)))
for ay in (0.0, 0.08, 0.16, 0.24, 0.32, 0.40):
    row = []
    for z in (0.80, 0.74, 0.70):
        best = None
        for x in np.arange(0.50, 0.86, 0.01):
            sc.reset_arm()
            if not sc.move_ee([float(x), ay, z], orn, timeout_s=1.5):
                break
            best = float(x)
        row.append(best)
    print(f"{ay:>6.2f} " + " ".join(f"{(v if v else 0.0):.3f}" for v in row))
sc.close()
