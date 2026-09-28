"""Release calibration: hand-off height vs. where the object comes to rest.

Runs the new `place` protocol (interpolated carry, descent to measured touchdown,
wait for the fingers to clear) at three hand-off clearances and reports the
distance from the named candidate, whether the object ends upright, and whether it
is at rest when the skill returns.
"""
import sys, os, math, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import SkillCall, vertical_half_extent
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import RuntimeVerifier
from embodied_agent.evaluation.tasks import TRAYS, _object, SLOTS

FLOOR = 0.628
LAYOUT = [_object("obj_yellow_1", "cylinder", "yellow", SLOTS[0]),
          _object("obj_green_2", "cube", "green", SLOTS[4]),
          _object("obj_blue_3", "cuboid", "blue", SLOTS[2])]
NOMINAL = {"obj_yellow_1": 0.042, "obj_green_2": 0.021, "obj_blue_3": 0.035}


def run(clearance, plan, shared):
    scene = PhysicsScene(seed=51, object_layout=LAYOUT)
    ex = SkillExecutor(scene, world_provider=lambda: None)
    rt = Runtime(scene, ex, tempfile.mkdtemp(), plan.budgets, "cal", config=plan.verify)
    ex.world_provider = rt.observe
    ex.placement_planner.HAND_OFF_CLEARANCE_M = clearance
    out = []
    for i, oid in enumerate(("obj_yellow_1", "obj_green_2", "obj_blue_3")):
        tray = "tray_left" if shared else TRAYS[i % 3]
        for skill, args in (("pick", {"object_id": oid}),
                            ("place", {"object_id": oid, "target_id": tray})):
            r = ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill, args=args))
            if skill != "place":
                continue
            w = rt.observe()
            e = w.entity(oid)
            ext = vertical_half_extent(e.geometry, e.pose.quaternion_xyzw)
            c = ex.last_candidate
            drift = math.hypot(e.pose.position.x - c.position_xy[0],
                               e.pose.position.y - c.position_xy[1]) if c else float("nan")
            rep = RuntimeVerifier(w, plan.verify).verify_placement(oid, tray)
            out.append(f"{oid:14s} {r.status.value:9s} sim={r.sim_seconds_used:5.2f}s "
                       f"tilted={'Y' if ext < NOMINAL[oid] - 0.004 else 'n'} "
                       f"drift={drift * 1000:5.1f}mm rest={e.at_rest} place={rep.value.value:7s} "
                       f"touchdown={r.measurements.get('descent_touchdown')} "
                       f"gap={r.measurements.get('release_gap_above_seat_m', 0) * 1000:+5.1f}mm "
                       f"clear={r.measurements.get('fingers_clear_after_open')} "
                       f"carry=({r.measurements.get('carry_offset_x_m', 0) * 1000:+5.1f},"
                       f"{r.measurements.get('carry_offset_y_m', 0) * 1000:+5.1f})mm")
    scene.close()
    return out


case = __import__("embodied_agent.evaluation.tasks", fromlist=["build_set"]).build_set("smoke")[0]
for shared in (False, True):
    print(f"### {'three objects share tray_left' if shared else 'one object per tray'}")
    for cl in (0.004, 0.012, 0.022):
        print(f"== HAND_OFF_CLEARANCE_M={cl}")
        for line in run(cl, case, shared):
            print("  ", line)
