"""What does a disturbance have to do for the state-change case to measure a decision?

SPEC 10.1 (line 287): after one object of a shared pair is done, a frozen short
external force pushes the *completed* object out of its valid region while keeping it
operable. Two facts decide what that can physically mean here:

1. A placement is valid while `|offset| + footprint <= inner_half - margin` (0.126 m),
   and the tray's inner wall stops a sliding box at exactly 0.131 m. So *any* in-plane
   shove inside a tray invalidates the relation by exactly `footprint_margin_m` = 5 mm
   and never more: the object is pinned against the wall. The size of the excursion is
   therefore not a design variable.
2. What IS a design variable is (a) whether the object reaches that wall whatever slot
   it was placed in, and (b) whether the pinned pose is still reachable for the
   recovery pick. Direction decides (b): -x is toward the robot (radial 0.50-0.62 m,
   well inside the ~0.75 m workspace) while +y carries a side-tray object to radial
   0.75 m and out of IK reach -- which is what killed sc_c1 (0/3) and sc_c3 (1/4) with
   the frozen `[0, 0.05]` (work/p0_diag_dev.py).

Magnitude decides (a), and it is where a shape asymmetry bites: the cube (35 mm tall,
centre of mass *below* the 25 mm wall top) slides to the wall and stays there at every
J from 0.05 to 0.28 N.s, while the taller cuboid hits the wall and rebounds, and does
so non-monotonically (travel 102 mm at J=0.06, 91 mm at J=0.11, 62 mm at J=0.18,
192 mm - out of the tray, lying on its side - at J=0.22). A mechanism whose declared
effect appears or vanishes with the impulse is not a protocol, so the disturbance is
calibrated on the shape whose behaviour is a geometric consequence: the cube.

Procedure: place the object on each *candidate the planner actually offers* (the eight
nearest lattice slots plus the farthest feasible one, which needs the longest slide),
apply the declared impulse, then run the real recovery - pick it and place it back -
and report the whole loop. A trial passes only if the object ends OUTSIDE its region,
at rest, and the loop closes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.scene import PhysicsScene, TABLE_TOP_Z
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import (build_world_state, entity_position,
                                        state_at_rest, state_inside, state_supported_on)
from embodied_agent.evaluation.tasks import DIMS, SLOTS, TRAYS, _object

SHAPES = ["cube", "cuboid", "cylinder"]
DIRS = {"-x": (-1.0, 0.0), "+x": (1.0, 0.0), "+y": (0.0, 1.0),
        "-x+y": (-0.7071, 0.7071), "-x-y": (-0.7071, -0.7071)}
MAGS = [0.05, 0.06, 0.07, 0.08, 0.10]
SEEDS = [7, 17, 51, 103]
BASE_XY = (0.05, 0.0)


def _call(ex, skill, args):
    return ex.execute(SkillCall(call_id="c", plan_id="p", step_id="s", skill=skill,
                                args=args, timeout_s=60.0))


def entry_slots(ex, tray, oid, world):
    """The slots a placement decision can actually name: the 8 nearest lattice
    candidates, plus the feasible one farthest from the named wall (longest slide)."""
    cands = ex.placement_planner.generate(tray, oid, world, limit=8)
    feas = [c for c in cands if ex.placement_planner.recheck(c, world).ok]
    cx = ex.scene.trays[tray]["center"][0]
    far = sorted(feas, key=lambda c: -c.position_xy[0])[:1]
    return feas + [c for c in far if c not in feas]


def trial(tray, shape, d, J, slot_xy, seed):
    oid = f"obj_{shape}_1"
    scene = PhysicsScene(seed=seed, object_layout=[_object(oid, shape, "yellow", SLOTS[seed % 6])])
    v = [0]

    def world_of():
        v[0] += 1
        return build_world_state(scene, v[0], f"o{v[0]}", None)

    try:
        ex = SkillExecutor(scene, world_provider=world_of)
        world = world_of()
        cand = ex.placement_planner._make(tray, oid, slot_xy[0], slot_xy[1], world)
        _call(ex, "pick", {"object_id": oid})
        p2 = _call(ex, "place", {"object_id": oid, "target_id": tray,
                                  "candidate_id": cand.candidate_id})
        if p2.status.value != "completed":
            return False, f"  setup place={p2.status.value}/{p2.failure_code}"
        seat, _ = scene.object_pose(oid)
        scene.apply_environment_impulse(oid, [d[0] * J, d[1] * J], 0.05)
        world = world_of()
        ent, reg = world.entity(oid), world.target(tray)
        pos = entity_position(ent)
        inside, ev_in = state_inside(ent, reg, 0.005)
        sup = state_supported_on(ent, reg, 0.012)[0]
        rest = state_at_rest(ent, ex.config)[0]
        travel = ((pos[0] - seat[0]) ** 2 + (pos[1] - seat[1]) ** 2) ** 0.5
        radial = ((pos[0] - BASE_XY[0]) ** 2 + (pos[1] - BASE_XY[1]) ** 2) ** 0.5
        over = 1000 * max(ev_in["xy_err_x"] + ev_in["footprint_half_x"] - ev_in["allow_x"],
                          ev_in["xy_err_y"] + ev_in["footprint_half_y"] - ev_in["allow_y"])
        pk = _call(ex, "pick", {"object_id": oid})
        plc, back = None, None
        if pk.status.value == "completed":
            c2 = ex.placement_planner.best_feasible(tray, oid, world_of())[0]
            plc = _call(ex, "place", {"object_id": oid, "target_id": tray,
                                      "candidate_id": c2.candidate_id if c2 else None})
            w2 = world_of()
            back = state_inside(w2.entity(oid), w2.target(tray), 0.005)[0]
        ok = (not inside) and rest and pk.status.value == "completed" \
            and plc is not None and plc.status.value == "completed" and back
        return ok, ("  {tray:11s} {shape:8s} J={J:5.3f} slot=({sx:.3f},{sy:.3f}) sd={seed:3d} "
                    "travel={travel:6.0f}mm -> ({x:.3f},{y:.3f}) radial={radial:.3f} "
                    "outside_by={over:+5.1f}mm inside={inside!s:5} sup={sup!s:5} "
                    "rest={rest!s:5} by={by:11s} pick={pk:9s} place2={plc:9s}/{fc:24s} "
                    "back={back!s:5} {verdict}").format(
            tray=tray, shape=shape, J=J, sx=slot_xy[0], sy=slot_xy[1], seed=seed,
            travel=travel * 1000, x=pos[0], y=pos[1], radial=radial, over=over,
            inside=inside, sup=sup, rest=rest, by=str(ent.supported_by),
            pk=pk.status.value, plc=(plc.status.value if plc else "-"),
            fc=str((plc.failure_code if plc else "") or ""), back=back,
            verdict="PASS" if ok else "FAIL")
    finally:
        scene.close()


print(f"cube dims={DIMS['cube']} cuboid dims={DIMS['cuboid']}  table z={TABLE_TOP_Z}")
only_dirs = sys.argv[1].split(",") if len(sys.argv) > 1 else ["-x"]
only_trays = sys.argv[2].split(",") if len(sys.argv) > 2 else TRAYS
if len(sys.argv) > 3:
    MAGS = [float(v) for v in sys.argv[3].split(",")]
shapes = sys.argv[4].split(",") if len(sys.argv) > 4 else ["cube"]

for name in only_dirs:
    d = DIRS[name]
    for J in MAGS:
        lines, fails, total = [], 0, 0
        for shape in shapes:
            for tray in only_trays:
                # candidate slots are a property of the tray + object, not of J
                probe = PhysicsScene(seed=SEEDS[0],
                                      object_layout=[_object(f"obj_{shape}_1", shape, "yellow",
                                                             SLOTS[0])])
                w0 = build_world_state(probe, 1, "o", None)
                slots = [c.position_xy for c in
                         entry_slots(SkillExecutor(probe, world_provider=lambda: w0), tray,
                                     f"obj_{shape}_1", w0)]
                probe.close()
                for slot in slots:
                    for seed in SEEDS:
                        total += 1
                        try:
                            ok, line = trial(tray, shape, d, J, slot, seed)
                        except Exception as e:  # noqa: BLE001
                            ok = False
                            line = (f"  {tray:11s} {shape:8s} J={J:5.3f} "
                                    f"slot=({slot[0]:.3f},{slot[1]:.3f}) sd={seed} "
                                    f"ERROR {type(e).__name__}: {e}")
                        if not ok:
                            fails += 1
                            lines.append(line)
        print(f"\n=== direction {name}: ({d[0] * J:+.4f}, {d[1] * J:+.4f}) N.s over 0.05 s"
              f" -> {total - fails}/{total} PASS ===", flush=True)
        for line in lines[:14]:
            print(line, flush=True)
        if fails > 14:
            print(f"  ... {fails - 14} more failures not shown", flush=True)
