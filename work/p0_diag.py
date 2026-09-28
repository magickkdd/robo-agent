"""P0 diagnosis: why does place fail, and what does WorldState actually report?"""
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import build_world_state
from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.placement_planner import PlacementPlanner

LAYOUT = [
    {"entity_id": "obj_red_1", "shape": "cube", "dims": [0.021] * 3, "xy": [0.44, 0.0],
     "attributes": {"color": "red", "shape": "cube"}},
    {"entity_id": "obj_blue_2", "shape": "cylinder", "dims": [0.023, 0.042], "xy": [0.68, -0.16],
     "attributes": {"color": "blue", "shape": "cylinder"}},
]

sc = PhysicsScene(seed=1, object_layout=LAYOUT)
w0 = build_world_state(sc, 0, "o0")
for e in w0.entities:
    print("WS", e.entity_id, [round(v, 3) for v in (e.pose.position.x, e.pose.position.y, e.pose.position.z)],
          "held=", e.held, "supported_by=", e.supported_by)
print("held_object:", w0.held_object)

pp = PlacementPlanner(sc)
print("footprint red:", pp.compute_footprint("obj_red_1").half_extents,
      " real dims:", sc.objects["obj_red_1"]["dims"])
print("slots tray_left for red:", [(round(float(s.position[0]), 3), round(float(s.position[1]), 3))
                                   for s in pp.generate_candidate_slots("tray_left", "obj_red_1", [])])

ex = SkillExecutor(sc, lambda: build_world_state(sc, 0, "oX"))
r = ex.execute(SkillCall(plan_id="p", step_id="s", skill="pick", args={"object_id": "obj_red_1"}))
print("pick:", r.status.value, r.failure_code, {k: round(v, 4) for k, v in r.measurements.items()})
print("  after pick, held bodies ->", [sc.entity_by_body(b) for b in sc.held_bodies()])
r = ex.execute(SkillCall(plan_id="p", step_id="s2", skill="place",
                         args={"object_id": "obj_red_1", "target_id": "tray_left"}))
print("place:", r.status.value, r.failure_code)
print("  meas:", {k: round(v, 4) for k, v in r.measurements.items()})
r = ex.execute(SkillCall(plan_id="p", step_id="s3", skill="observe", args={}))
print("observe:", r.status.value, r.failure_code, r.remaining_action_hint)
sc.close()
