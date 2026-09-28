"""Dev probe (read-only): what a real percept snapshot can say about placement.

Prints, per view for dev_c5: entity id -> (supported_by, confidence, pose?, geometry)
and every occupancy row, then the two verifiers' placement verdicts for every
(entity, target) pair. Nothing here is written into a prompt, feedback or decision
context; it exists to choose the cases the P1-d tests are allowed to assert.
"""
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from embodied_agent.core.contracts import Source
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.verify import RuntimeVerifier, build_world_state
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.frames import camera_for, capture
from embodied_agent.perception.observe import PerceptionAssembler, StubReader, TaskContext
from embodied_agent.perception.verify_percept import PerceptVerifier
from embodied_agent.perception.world_state import PerceptWorldStateTracker

MAIN, OVERHEAD, FRONT_HIGH = "main", "overhead", "front_high"
UTTERANCE = "把红色方块放进中间盘"
CASE = sys.argv[1] if len(sys.argv) > 1 else "dev_c5"

case = find_case(CASE)
scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
catalog = cell_catalog(scene.trays.values())
assembler = PerceptionAssembler(catalog)
tracker = PerceptWorldStateTracker(catalog, episode_id="probe")
task = TaskContext(utterance=UTTERANCE, items=["红色方块"], regions=["中间盘"])
reader = StubReader()

print(f"== {CASE}: trays {sorted(scene.trays)}")
priv = build_world_state(scene, 1, "obs_priv")
print("-- privileged truth (graded against, never read by the channel)")
for e in sorted(priv.entities, key=lambda x: x.entity_id):
    print(f"   {e.entity_id:14s} support={str(e.supported_by):16s} geom={e.geometry.shape if e.geometry else None}"
          f" held={e.held} rest={e.at_rest}")
for o in sorted(priv.occupancy, key=lambda x: (x.entity_id, x.target_id)):
    print(f"   occ {o.entity_id:14s} {o.target_id:14s} inside={o.fully_inside} "
          f"half=({o.footprint_half_xy[0]:.4f},{o.footprint_half_xy[1]:.4f}) "
          f"xy=({o.rest_xy[0]:.4f},{o.rest_xy[1]:.4f})")

for view in (MAIN, OVERHEAD, FRONT_HIGH):
    frame = capture(scene, "/tmp/v02_p1d_probe", camera_for(view), view,
                    sim_time=scene.sim_time)[0]
    reading = reader.read(frame, catalog=catalog, task=task)
    state = tracker.perceive(assembler, frame, reading)[1]
    print(f"== view {view}: source={state.source.value} held_object={state.held_object!r} "
          f"targets={sorted(t.target_id for t in state.targets)}")
    for e in sorted(state.entities, key=lambda x: x.entity_id):
        print(f"   {e.entity_id:14s} support={str(e.supported_by):16s} conf={e.confidence:.3f} "
              f"pose={'yes' if e.pose else 'NO'} geom={e.geometry.shape if e.geometry else None}"
              f" attrs={sorted(k for k in e.attributes if k.startswith(('mask_', 'support_', 'orientation'))) }")
    for o in sorted(state.occupancy, key=lambda x: (x.entity_id, x.target_id)):
        print(f"   occ {o.entity_id:14s} {o.target_id:14s} inside={o.fully_inside} "
              f"half=({o.footprint_half_xy[0]:.4f},{o.footprint_half_xy[1]:.4f}) "
              f"xy=({o.rest_xy[0]:.4f},{o.rest_xy[1]:.4f})")
    verifier = PerceptVerifier(state, None, view=view,
                               alt_views=(MAIN, OVERHEAD, FRONT_HIGH))
    ids = sorted(e.entity_id for e in state.entities)
    for eid in ids:
        for tid in sorted(t.target_id for t in state.targets):
            r = verifier.verify_placement(eid, tid)
            print(f"   [{eid} -> {tid}] percept={r.value.value:7s} unmeasured={len(r.unmeasured)} "
                  f"keys={sorted(r.evidence)}")
    print(f"   absent-entity case: ", end="")
    r = verifier.verify_placement("seen:orange", sorted(t.target_id for t in state.targets)[0])
    print(f"{r.value.value} {r.unmeasured}")
    r = verifier.verify_placement(ids[0], "tray_does_not_exist")
    print(f"   no-target case: {r.value.value} {r.unmeasured}")
    print(f"   would_resolve(not_in_this_frame)={verifier.would_resolve('not_in_this_frame')}")
    print(f"   would_resolve(hold_state)={verifier.would_resolve('hold_state')}")

print("== same pairs, privileged channel (control: the circular key exists there)")
pv = build_world_state(scene, 9, "obs_priv9")
rv = RuntimeVerifier(pv)
for e in sorted(pv.entities, key=lambda x: x.entity_id)[:2]:
    for t in sorted(x.target_id for x in pv.targets):
        r = rv.verify_placement(e.entity_id, t)
        print(f"   [{e.entity_id} -> {t}] privileged={r.value.value:7s} keys={sorted(r.evidence)}")
scene.close()
