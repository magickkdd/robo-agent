"""What displacement does a declared impulse actually produce, and where does the
object end up?  (SPEC 10.3: disturbances are calibrated on dev, by measurement.)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene, TABLE_TOP_Z
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import (build_world_state, entity_footprint_half_xy,
                                        entity_position, footprint_inside_region, seated_rest_z)
from embodied_agent.evaluation.tasks import TRAYS, _object, SLOTS

SHAPES = ["cylinder", "cube", "cuboid"]
JS = [0.01, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08]


def run(shape, J):
    layout = [_object(f"obj_{shape}_1", shape, "yellow", SLOTS[0])]
    scene = PhysicsScene(seed=11, object_layout=layout)
    ctr = [0]
    ex = SkillExecutor(scene, world_provider=lambda: build_world_state(
        scene, ctr.__setitem__(0, ctr[0] + 1) or ctr[0], f"o{ctr[0]}", None))
    ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick",
                         args={"object_id": layout[0]["entity_id"]}))
    ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="place",
                         args={"object_id": layout[0]["entity_id"], "target_id": "tray_left"}))
    seat = scene.object_pose(layout[0]["entity_id"])[0]
    before_xy = [seat[0], seat[1]]
    ev = scene.apply_environment_impulse(layout[0]["entity_id"], [0.0, J], 0.05)
    pos, orn = scene.object_pose(layout[0]["entity_id"])
    w = build_world_state(scene, 9, "x", None)
    e = w.entity(layout[0]["entity_id"])
    region = w.target("tray_left")
    xy = entity_position(e)[:2]
    inside, _ = footprint_inside_region(xy, entity_footprint_half_xy(e), region, 0.005)
    seated = abs(pos[2] - seated_rest_z(e.geometry, region, e.pose.quaternion_xyzw)) < 0.012
    on_table = pos[2] > TABLE_TOP_Z - 0.20
    reach = scene.reachable([pos[0], pos[1], pos[2] + 0.10],
                            scene.ee_pose()[1])
    print(f"{shape:8s} J={J:5.3f} drift={ev['displacement_m']*1000:6.1f}mm "
          f"final_xy=({pos[0]:.3f},{pos[1]:.3f}) z={pos[2]:.3f} "
          f"{'seated' if seated else 'NOT-seated'} {'inside ' if inside else 'outside'} "
          f"{'on-table' if on_table else 'OFF-table'} reach={int(reach)} "
          f"sup={e.supported_by} rest={e.at_rest}")
    scene.close()
    return before_xy


for shape in SHAPES:
    print(f"== {shape}")
    for J in JS:
        run(shape, J)
