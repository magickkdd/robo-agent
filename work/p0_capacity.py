"""How many objects can one tray actually hold? (measured reach x measured sweep)

A slot is usable only if the hand can get there *and* it clears every neighbour
already in the tray. Both bounds are measured, not assumed:

* reachability: `PhysicsScene.reachable` at the transfer height and at the
  hand-off height, on the same 20 mm lattice `PlacementPlanner.generate` offers;
* neighbour clearance: `PlacementPlanner.HAND_SWEEP_RADIUS_M` plus the radius of
  the largest object the set uses (the worst case, so the answer is a floor).

The maximum number of mutually clear usable slots per tray is then a maximum
independent set of the "too close" graph. This is the capacity the case
generator must respect: a task that asks for more is not a hard decision, it is
an impossible one.
"""
import itertools
import math
import os
import sys

import pybullet as p

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.placement_planner import PlacementPlanner
from embodied_agent.evaluation.run import build_scene
from embodied_agent.evaluation.tasks import TRAYS, build_set

CASE = next(c for c in build_set("dev") if c.task_id == "dev_c6")
scene = build_scene(CASE)
planner = PlacementPlanner(scene, CASE.verify)

max_r = max(planner.circumscribed_radius(e) for e in scene.objects)
min_sep = planner.HAND_SWEEP_RADIUS_M + max_r
limit = scene.trays[TRAYS[0]]["inner_half"] - CASE.verify.footprint_margin_m
room = limit - max_r - planner.CLEARANCE_M
n = int(room // planner.GRID_STEP_M)
print(f"largest object r={max_r:.3f} m -> slots must be >= {min_sep*1000:.0f} mm apart; "
      f"lattice +-{n} steps of {planner.GRID_STEP_M*1000:.0f} mm")


def max_pack(points, sep):
    """Exact maximum set of points pairwise >= sep, branch and bound."""
    pts = list(points)
    best = [0, []]

    def conflict(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1]) < sep

    def rec(start, chosen):
        if len(chosen) + (len(pts) - start) <= best[0]:
            return
        if start == len(pts):
            if len(chosen) > best[0]:
                best[0], best[1] = len(chosen), list(chosen)
            return
        if all(not conflict(pts[start], c) for c in chosen):
            chosen.append(pts[start])
            rec(start + 1, chosen)
            chosen.pop()
        rec(start + 1, chosen)

    rec(0, [])
    return best[0], best[1]


for tray in TRAYS:
    t = scene.trays[tray]
    cx, cy = t["center"]
    orn = p.getQuaternionFromEuler([math.pi, 0, 0])
    usable = []
    unreachable = 0
    for i, j in itertools.product(range(-n, n + 1), repeat=2):
        x, y = cx + i * planner.GRID_STEP_M, cy + j * planner.GRID_STEP_M
        rel = (i * planner.GRID_STEP_M, j * planner.GRID_STEP_M)
        if max(abs(rel[0]), abs(rel[1])) > limit - max_r:
            continue
        reach = all(scene.reachable([x, y, z], orn)
                    for z in (planner.TRANSFER_Z,
                              t["floor_top"] + max_r + planner.HAND_OFF_CLEARANCE_M))
        if reach:
            usable.append(rel)
        else:
            unreachable += 1
    k, pack = max_pack(usable, min_sep)
    print(f"  {tray:12} usable lattice slots={len(usable):3}  unreachable={unreachable:3}  "
          f"max mutually clear={k}  example={[f'({p[0]*1000:+.0f},{p[1]*1000:+.0f})' for p in pack]}")
scene.close()
