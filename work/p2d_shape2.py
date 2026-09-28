"""P2-d dev probe #6: run the one §8 shape-2 layout that scan #5 says closes, with the real arm.

`p2d_slot_sweep.py` scanned eight arrangements and returned exactly one where the victim has a clear
slot after removing some occupant *and* that occupant still has a clear slot to come back to:

    tray_middle blockers  cuboid (0.640, -0.045)  cylinder (0.680, -0.005)  cuboid (0.640, +0.035)
    removing obj_p3 -> 1 clear victim slot; with the victim seated there, 7 clear slots for obj_p3.

The victim in that scan was an 80 mm square, which probe #2 measured as ungraspable *on the table*
and graspable *inside a tray* (the floor seats it 8 mm higher, and the fingers close beside it). So
the authored scene starts it in `tray_left`: the task is then a transfer between regions, which is
what §8's example utterance describes anyway.

Geometry is not the open question any more — the arm is. Every step here is `SkillExecutor.execute`
with the production planner's own candidate ids, and every reported number is measured after the
action. Read-only against the repository.
"""
import json
import math

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import PALETTE

BLOCKERS = [("obj_p1", "cuboid", [0.027, 0.019, 0.035], [0.640, -0.045], "red"),
            ("obj_p2", "cylinder", [0.023, 0.042], [0.680, -0.005], "green"),
            ("obj_p3", "cuboid", [0.027, 0.019, 0.035], [0.640, 0.035], "blue")]
VICTIM = ("obj_victim", "cuboid", [0.040, 0.040, 0.020], [0.66, -0.32], "purple")
TEMP = "tray_right"
_SCENES: list = []


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def main():
    try:
        return run()
    finally:
        for s in _SCENES:
            s.close()


def run():
    layout = [{"entity_id": e, "shape": s, "dims": list(d), "xy": list(xy), "color": PALETTE[c],
               "attributes": {"color": c, "shape": s}} for e, s, d, xy, c in BLOCKERS]
    e, s, d, xy, c = VICTIM
    layout.append({"entity_id": e, "shape": s, "dims": list(d), "xy": list(xy), "color": PALETTE[c],
                   "attributes": {"color": c, "shape": s}})
    scene = PhysicsScene(seed=7, object_layout=layout, gui=False)
    _SCENES.append(scene)
    box = {"n": 1}
    provider = lambda: build_world_state(scene, box["n"], f"obs_{box['n']:04d}")  # noqa: E731
    executor = SkillExecutor(scene, world_provider=provider)
    planner = executor.placement_planner
    step = {"n": 0}

    def do(skill, **args):
        step["n"] += 1
        r = executor.execute(SkillCall(plan_id="p6", step_id=str(step["n"]), skill=skill,
                                       args=args))
        box["n"] += 1
        return r

    def seats():
        w = provider()
        return {t: sorted(o.entity_id for o in w.occupancy if o.target_id == t)
                for t in ("tray_left", "tray_middle", "tray_right")}

    row = {"start": seats()}
    row["victim_slot_0"] = [c.candidate_id for c in
                            planner.generate("tray_middle", "obj_victim", provider(), limit=None)
                            if planner.recheck(c, provider()).ok]
    r = do("pick", object_id="obj_p3")
    row["1_lift_blocker"] = {"status": r.status.value, "code": r.failure_code,
                             "held": r.held_state}
    if r.status.value != "completed":
        return row
    ok = [c for c in planner.generate(TEMP, "obj_p3", provider(), limit=None)
          if planner.recheck(c, provider()).ok]
    row["2_temp_slots"] = len(ok)
    r = do("place", object_id="obj_p3", target_id=TEMP)
    row["2_temp_place"] = {"status": r.status.value, "code": r.failure_code,
                           "candidate": r.candidate_resolution}
    if r.status.value != "completed":
        return row
    row["after_temp"] = seats()
    cand = [(c.candidate_id, list(c.position_xy))
            for c in planner.generate("tray_middle", "obj_victim", provider(), limit=None)
            if planner.recheck(c, provider()).ok]
    cand.sort(key=lambda kv: math.hypot(kv[1][0] - 0.66, kv[1][1] - 0.0))
    row["3_victim_slots"] = [cid for cid, _ in cand]
    if not cand:
        return row
    attempts, seated = [], None
    for cid, xy in cand:
        re = do("pick", object_id="obj_victim")  # legal while already held: re-verifies the grasp
        rp = do("place", object_id="obj_victim", target_id="tray_middle", candidate_id=cid)
        attempts.append({"candidate": cid, "xy": [round(v, 3) for v in xy],
                         "pick": re.status.value, "place": rp.status.value,
                         "code": rp.failure_code,
                         "notes": [str(n)[:150] for n in (rp.notes or [])][:3]})
        if rp.status.value == "completed":
            seated = cid
            break
    row["4_victim_attempts"] = attempts
    row["victim_seated_in"] = [round(float(v), 4) for v in scene.object_pose("obj_victim")[0]]
    if seated is None:
        return row
    r = do("safe_retreat")
    row["5_retreat"] = r.status.value
    back = [(c.candidate_id, list(c.position_xy))
            for c in planner.generate("tray_middle", "obj_p3", provider(), limit=None)
            if planner.recheck(c, provider()).ok]
    back.sort(key=lambda kv: math.hypot(kv[1][0] - 0.66, kv[1][1] - 0.0))
    row["6_return_slots"] = len(back)
    r = do("pick", object_id="obj_p3")
    row["7_return_pick"] = {"status": r.status.value, "code": r.failure_code}
    if r.status.value != "completed":
        return row
    placed, restore = None, []
    for cid, xy in (back or [(None, [0.66, 0.0])]):
        rp = do("place", object_id="obj_p3", target_id="tray_middle", candidate_id=cid)
        restore.append({"candidate": cid, "xy": [round(v, 3) for v in xy],
                        "status": rp.status.value, "code": rp.failure_code})
        restore[-1]["notes"] = [str(n)[:150] for n in (rp.notes or [])][:3]
        if rp.status.value == "completed":
            placed = cid
            break
        if do("pick", object_id="obj_p3").status.value != "completed":
            break
    row["8_return_attempts"] = restore
    row["final"] = seats()
    row["reachable"] = placed is not None and row["final"]["tray_middle"] == [
        "obj_p1", "obj_p2", "obj_p3", "obj_victim"]
    return row


if __name__ == "__main__":
    import sys
    out = main()
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), file=sys.stderr)
    with open("/tmp/qoder-remote-worker-nSv81L/p2d_seq8.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
