"""P2-d dev probe #4: why does a lifted occupant have nowhere to go?

`work/p2d_block_matrix.py` phase 2 got every candidate §8 layout to the same stop: the occupant is
lifted out of the blocked tray, and then neither of the other two regions measures feasible for it,
so the temporary move has no destination and the sequence ends. That is the fact the whole task set
turns on — an occupant that cannot be *put* anywhere else is not a dependency, it is a dead end — and
"no clear slot" can mean three different things in the re-check: the footprint crossing a wall, the
hand's descent envelope, or the IK/reach claim. This probe prints which of those it is, for the same
body in the same scene at the two moments that matter: before the pick (the region empty, as probe #1
measured it feasible at 17 clear slots) and while the hand is holding it (which is what the sequence
actually asks).

Read-only against the repository, writes nothing, feeds nothing to a model.
"""
import json

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import DIMS, PALETTE, TRAYS
from embodied_agent.planning.subgoals import Instruments

WIDE = [0.040, 0.040, 0.020]
TRAY_Y = {"tray_left": -0.32, "tray_middle": 0.0, "tray_right": 0.32}


def layout(with_second):
    out = [{"entity_id": "obj_red_1", "shape": "cuboid", "dims": list(WIDE),
            "xy": [0.66, TRAY_Y["tray_middle"] - 0.045], "color": PALETTE["red"],
            "attributes": {"color": "red", "shape": "cuboid"}},
           {"entity_id": "obj_victim", "shape": "cube", "dims": list(DIMS["cube"]),
            "xy": [0.38, -0.16], "color": PALETTE["purple"],
            "attributes": {"color": "purple", "shape": "cube"}}]
    if with_second:
        out.insert(1, {"entity_id": "obj_green_2", "shape": "cuboid", "dims": list(WIDE),
                       "xy": [0.66, TRAY_Y["tray_middle"] + 0.045], "color": PALETTE["green"],
                       "attributes": {"color": "green", "shape": "cuboid"}})
    return out


def every_reason(planner, world, eid, tid):
    """What the re-check says about *each* candidate, not just the first rejection."""
    cands = planner.generate(tid, eid, world, limit=None)
    tally: dict[str, int] = {}
    clear = 0
    for c in cands:
        chk = planner.recheck(c, world)
        if chk.ok:
            clear += 1
            continue
        for r in chk.reasons:
            key = r.split("(")[0].strip()[:70]
            tally[key] = tally.get(key, 0) + 1
    return {"offered": len(cands), "clear": clear, "reasons": tally}


def run(with_second):
    scene = PhysicsScene(seed=7, object_layout=layout(with_second), gui=False)
    row = {"second_occupant": with_second}
    try:
        # a live provider, so the executor's own candidate re-check sees the state the previous
        # action left behind; and `.value`, because `str(SkillStatus.completed)` is the qualified
        # name and the first draft of this probe read a *successful* pick as a failed one
        box = {"n": 1}
        provider = lambda: build_world_state(scene, box["n"], f"obs_{box['n']:04d}")  # noqa: E731
        executor = SkillExecutor(scene, world_provider=provider)
        world = provider()
        planner = executor.placement_planner
        row["v1_held_on_table"] = {f"{t}<-victim": every_reason(planner, world, "obj_victim", t)
                                   for t in TRAYS}
        row["v1_blocker"] = {f"{t}<-blocker": every_reason(planner, world, "obj_red_1", t)
                             for t in TRAYS}
        pick = executor.execute(SkillCall(plan_id="p", step_id="1", skill="pick",
                                         args={"object_id": "obj_red_1"}))
        row["pick"] = pick.status.value
        if pick.status.value != "completed":
            return row
        box["n"] = 2
        held = build_world_state(scene, 2, "obs_0002")
        row["while_held"] = {f"{t}<-blocker": every_reason(planner, held, "obj_red_1", t)
                             for t in TRAYS}
        place = executor.execute(SkillCall(plan_id="p", step_id="2", skill="place",
                                          args={"object_id": "obj_red_1",
                                                "target_id": "tray_left"}))
        row["place_tray_left"] = {"status": place.status.value, "code": place.failure_code,
                                  "notes": [str(n)[:110] for n in (place.notes or [])][:3]}
        box["n"] = 3
        after = build_world_state(scene, 3, "obs_0003")
        row["victim_after_move"] = every_reason(planner, after, "obj_victim", "tray_middle")
        row["victim_still_blocked"] = row["victim_after_move"]["clear"] == 0
        return row
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    finally:
        scene.close()


if __name__ == "__main__":
    for second in (False, True):
        print(json.dumps(run(second), ensure_ascii=False, sort_keys=True), flush=True)
