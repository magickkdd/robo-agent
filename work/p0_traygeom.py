"""Candidate tray geometries: is every generated slot inside the strict reach
claim, and can two objects share a region?"""
import sys, os, math, itertools
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pybullet as p
import embodied_agent.core.scene as S
from embodied_agent.core.contracts import VerifyConfig
from embodied_agent.core.placement_planner import PlacementPlanner
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import build_world_state

ORN = p.getQuaternionFromEuler([math.pi, 0, 0])
CFG = VerifyConfig()
# worst real object in the set: cylinder r=0.023 hh=0.042 -> circumscribed 0.042
R_WORST = 0.042
R_CUBE = 0.0297


def probe(tray_x, tray_y, inner):
    """Slot feasibility for a hypothetical tray, measured in a plain scene."""
    sc = PhysicsScene(seed=3, object_layout=[])
    sc.trays["probe"] = {"target_id": "probe", "center": [tray_x, tray_y],
                         "inner_half": inner, "floor_top": S.TABLE_TOP_Z + S.TRAY_WALL_H,
                         "wall_top": S.TABLE_TOP_Z + 2 * S.TRAY_WALL_H, "body": None}
    pl = PlacementPlanner(sc, CFG)
    room = inner - CFG.footprint_margin_m - R_WORST - pl.CLEARANCE_M
    n = max(0, int(room // pl.GRID_STEP_M))
    slots = [(tray_x + i * pl.GRID_STEP_M, tray_y + j * pl.GRID_STEP_M)
             for i in range(-n, n + 1) for j in range(-n, n + 1)]
    rel = [(x, y) for x, y in slots
           if sc.reachable([x, y, pl.TRANSFER_Z], ORN)
           and sc.reachable([x, y, S.TABLE_TOP_Z + S.TRAY_WALL_H + R_WORST + pl.GRIPPER_CLEARANCE_M],
                            ORN)]
    # how many worst-case objects (separation 2*R_WORST+CLEARANCE) fit in `rel`?
    sep = 2 * R_WORST + pl.CLEARANCE_M
    best, chosen = 0, []
    for k in (1, 2, 3):
        for combo in itertools.combinations(rel, k):
            if all(math.hypot(a[0] - b[0], a[1] - b[1]) >= sep for a, b in itertools.combinations(combo, 2)):
                best, chosen = k, combo
                break
        if best == k:
            continue
    # a cube-sized partner needs a smaller separation
    sep2 = R_WORST + R_CUBE + pl.CLEARANCE_M
    pair2 = any(math.hypot(a[0] - b[0], a[1] - b[1]) >= sep2
                for a, b in itertools.combinations(rel, 2))
    sc.close()
    return dict(inner=inner, n=n, slots=len(slots), reachable=len(rel),
                worst_fit=best, cube_pair=pair2, slots_reach=rel[:3])


for tx, ty, inner in ((0.66, 0.32, 0.135), (0.66, 0.26, 0.135), (0.63, 0.26, 0.110),
                      (0.62, 0.24, 0.105), (0.60, 0.24, 0.105)):
    print(f"tray x={tx} y=+-{ty} inner={inner}: {probe(tx, ty, inner)}")
