"""Is the calibrated disturbance stable, or only lucky at one seed?

Reports the signed boundary margin (limit - footprint extent): negative = the
placement predicate is false.  Also measures the table-slide case, where a
*pending* object must stay on the table and stay operable.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene, TABLE_TOP_Z
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import (build_world_state, entity_footprint_half_xy,
                                        entity_position, seated_rest_z)
from embodied_agent.evaluation.tasks import _object, SLOTS

SHAPES = ["cylinder", "cube", "cuboid"]
SEEDS = [3, 11, 27, 51]


def world_of(scene, ctr):
    return build_world_state(scene, ctr.__setitem__(0, ctr[0] + 1) or ctr[0], f"o{ctr[0]}", None)


def margin(scene, eid, region, margin_m=0.005):
    w = world_of(scene, [0])
    e = w.entity(eid)
    pos = entity_position(e)
    half = entity_footprint_half_xy(e)
    limit = region.inner_half - margin_m
    dx = abs(float(pos[0]) - region.center.x) - limit
    dy = abs(float(pos[1]) - region.center.y) - limit
    over_x = (abs(float(pos[0]) - region.center.x) + half[0]) - limit
    over_y = (abs(float(pos[1]) - region.center.y) + half[1]) - limit
    inside = over_x <= 0 and over_y <= 0
    return inside, max(over_x, over_y), pos, e


def in_tray(shape, J, seed):
    oid = f"obj_{shape}_1"
    scene = PhysicsScene(seed=seed, object_layout=[_object(oid, shape, "yellow", SLOTS[0])])
    ex = SkillExecutor(scene, world_provider=lambda: world_of(scene, [0]))
    ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="pick", args={"object_id": oid}))
    ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill="place",
                         args={"object_id": oid, "target_id": "tray_left"}))
    seat, _ = scene.object_pose(oid)
    scene.apply_environment_impulse(oid, [0.0, J], 0.05)
    region = world_of(scene, [1]).target("tray_left")
    inside, over, pos, e = margin(scene, oid, region)
    seated = abs(pos[2] - seated_rest_z(e.geometry, region, e.pose.quaternion_xyzw)) < 0.012
    print(f"  {shape:8s} J={J:5.3f} seed={seed:3d} drift={(abs(pos[1]-seat[1]))*1000:6.1f}mm "
          f"{'inside ' if inside else 'OUTSIDE'} over={over*1000:+6.1f}mm "
          f"{'seated' if seated else 'NOTseat'} sup={e.supported_by} rest={e.at_rest}")
    scene.close()
    return inside and seated


def on_table(J, seed):
    oid = "obj_cube_1"
    scene = PhysicsScene(seed=seed, object_layout=[_object(oid, "cube", "green", SLOTS[3])])
    scene.settle(1.0)
    seat, _ = scene.object_pose(oid)
    scene.apply_environment_impulse(oid, [0.0, J], 0.05)
    pos, _ = scene.object_pose(oid)
    w = world_of(scene, [0])
    e = w.entity(oid)
    print(f"  table  J={J:5.3f} seed={seed:3d} drift={((pos[1]-seat[1])**2+(pos[0]-seat[0])**2)**0.5*1000:6.1f}mm "
          f"final=({pos[0]:.3f},{pos[1]:.3f}) z={pos[2]:.3f} sup={e.supported_by} rest={e.at_rest} "
          f"on_table={pos[2] > TABLE_TOP_Z - 0.05}")
    scene.close()


for J in (0.045, 0.05, 0.055):
    print(f"== placed object, impulse +y, J={J}")
    for shape in SHAPES:
        for seed in SEEDS:
            in_tray(shape, J, seed)

for J in (0.04, 0.055, 0.07):
    print(f"== pending object on the table, J={J}")
    for seed in SEEDS:
        on_table(J, seed)
