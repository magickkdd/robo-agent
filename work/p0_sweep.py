"""How wide is the hand's descent envelope inside a tray? (measured, not guessed)

A second placement into an occupied tray shoved a standing cylinder 94 mm and
tipped it over in dev_c3 — a world change no mode asked for and no mode can undo
(an object lying at the bottom of a tray is not graspable). The slot rule
separates *footprints* only (r_a + r_b + CLEARANCE_M = 65 mm there), so something
wider than the two objects is descending into that gap.

Procedure: place a neighbour first, then place a second object at a measured
centre-to-centre separation `d`, and report how far the neighbour moved and
whether it is still seated. Swept over tall/short neighbour, short/tall intruder,
and the straight and diagonal directions (the diagonal is what bit dev_c3).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import entity_footprint_half_xy, footprint_inside_region
from embodied_agent.evaluation.run import build_scene
from embodied_agent.evaluation.tasks import build_set

CASE = next(c for c in build_set("dev") if c.task_id == "dev_c3")
TRAY = "tray_left"
TALL, SHORT, CUBOID = "obj_purple_2", "obj_red_3", "obj_yellow_1"
PAIRS = [(TALL, SHORT), (SHORT, TALL), (TALL, CUBOID)]
DIRS = {"+y": (0.0, 1.0), "diag": (-0.3, 0.95)}
DISTANCES = [0.065, 0.075, 0.085, 0.095, 0.105]


def _pick_place(ex, rt, eid, x, y):
    world = rt.observe()
    cand = ex.placement_planner._make(TRAY, eid, x, y, world)
    outs = []
    for call in (SkillCall(call_id="c1", skill="pick", args={"object_id": eid},
                           plan_id="p", step_id="s", timeout_s=30.0),
                 SkillCall(call_id="c2", skill="place",
                           args={"object_id": eid, "target_id": TRAY,
                                 "candidate_id": cand.candidate_id},
                           plan_id="p", step_id="s", timeout_s=30.0)):
        r = ex.execute(call)
        outs.append((r.status.value, r.failure_code))
    return outs


def trial(neighbour, intruder, direction, dist):
    scene = build_scene(CASE)
    try:
        ex = SkillExecutor(scene, world_provider=lambda: None, config=CASE.verify)
        rt = Runtime(scene, ex, "/tmp/diag_sweep", CASE.budgets, "sweep", config=CASE.verify,
                     environment=EnvironmentController(scene, []))
        ex.world_provider = rt.observe
        cx, cy = scene.trays[TRAY]["center"]
        _pick_place(ex, rt, neighbour, cx, cy)
        pos0, _ = scene.object_pose(neighbour)
        scale = dist / ((direction[0] ** 2 + direction[1] ** 2) ** 0.5)
        second = _pick_place(ex, rt, intruder, cx + direction[0] * scale, cy + direction[1] * scale)
        scene.settle(CASE.verify.settle_time_s)
        pos1, _ = scene.object_pose(neighbour)
        world = rt.observe()
        reg = world.target(TRAY)
        inside, _ = footprint_inside_region(pos1[:2],
                                            entity_footprint_half_xy(world.entity(neighbour)),
                                            reg, CASE.verify.footprint_margin_m)
        moved = ((pos1[0] - pos0[0]) ** 2 + (pos1[1] - pos0[1]) ** 2) ** 0.5 * 1000
        return {"moved_mm": round(moved, 1), "dz_mm": round((pos1[2] - pos0[2]) * 1000, 1),
                "inside": inside, "second": second[1], "sep_mm": round(dist * 1000)}
    finally:
        scene.close()


for neighbour, intruder in PAIRS:
    for dname, dvec in DIRS.items():
        print(f"\n=== neighbour={neighbour} intruder={intruder} dir={dname} ===")
        for dist in DISTANCES:
            r = trial(neighbour, intruder, dvec, dist)
            print(f"  sep={r['sep_mm']:3d} mm  moved={r['moved_mm']:6.1f} mm  dz={r['dz_mm']:+6.1f} mm  "
                  f"inside={str(r['inside']):5}  place2={r['second']}")
