"""P2-d dev probe: is a temporary-move-and-restore task reachable by this hardware at all?

Read-only against the repository; writes nothing. §8's canonical long-horizon case asks the agent
to lift an object *out of a tray*, put it somewhere else so a second object can go in, and bring it
back before the episode ends. Every one of those three verbs is a claim about the simulator, not
about the planner, and none of them has been measured in this project:

* the tray centres are at x=0.66 while the frozen spawn slots are at x<=0.445, so the *grasp*
  reachability of an object that starts inside a tray is an open question (`PlacementPlanner
  ._reachability` guards placements, and a pick has its own envelope);
* a tray that already holds a body may or may not be infeasible for a second one — the answer is a
  property of the lattice, the footprint rule and the 50 mm hand-sweep radius together, and it
  cannot be read off any of them separately;
* and if the pair fits only after the blocker returns to a *different* slot than the one it started
  in, the task is still reachable, but "restore" then means "back in the region", not "back where
  it was" — which is what `placed(blocker, region)` measures and what §11's row counts.

The probe answers with the production instruments: `SkillExecutor` for the actions,
`PlacementPlanner.generate/recheck` through `planning.subgoals.Instruments` — the same call the
planner makes before it claims a region is blocked — and `build_world_state` for the facts. Each
stage is printed as it is measured, so a crash later in the sequence cannot hide the fact that
settled earlier. Nothing here feeds a prompt or a DecisionContext.
"""
import json
import sys

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.runtime import build_world_state
from embodied_agent.core.scene import PhysicsScene
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.evaluation.tasks import DIMS, PALETTE, TRAYS
from embodied_agent.planning.subgoals import Instruments

# which blocker size makes a tray infeasible is the measurement, not an assumption, so the size is
# swept; `pr_target_full`'s prop is included as the control that is known to block
BLOCKER_DIMS = {
    "cube21": [0.021, 0.021, 0.021],
    "cuboid": [0.027, 0.019, 0.035],
    "wide40": [0.040, 0.040, 0.020],
    "wide50": [0.050, 0.050, 0.020],
    "pr_target_full_60": [0.060, 0.060, 0.020],
}
TABLE = [(0.38, -0.16), (0.38, 0.0), (0.445, 0.16)]
TRAY_Y = {"tray_left": -0.32, "tray_middle": 0.0, "tray_right": 0.32}
BLOCKER, VICTIM, SPARE = "obj_green_2", "obj_red_1", "obj_blue_3"


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


def layout(dims, tray_xy):
    def obj(eid, shape, color, xy, d):
        return {"entity_id": eid, "shape": shape, "dims": list(d), "xy": list(xy),
                "color": PALETTE[color], "attributes": {"color": color, "shape": shape}}
    return [obj(VICTIM, "cube", "red", TABLE[0], DIMS["cube"]),
            obj(BLOCKER, "cuboid", "green", tray_xy, dims),
            obj(SPARE, "cube", "blue", TABLE[1], DIMS["cube"])]


def feas(instruments, eid, tid):
    f = instruments.feasible(eid, tid)
    return {"feasible": f.feasible, "offered": f.offered, "clear": len(f.ok),
            "first_reason": (f.first_reason or "")[:150]}


def outcome(r):
    return {"status": r.status.value, "code": r.failure_code,
            "notes": [str(n)[:110] for n in (r.notes or [])][:3]}


def probe(name, dims, tray="tray_middle"):
    scene = PhysicsScene(seed=7, object_layout=layout(dims, [0.66, TRAY_Y[tray]]), gui=False)
    row = {"name": name, "dims": list(dims), "tray": tray}
    try:
        executor = SkillExecutor(scene, world_provider=lambda: None)
        world = build_world_state(scene, 1, "obs_0001")
        executor.world_provider = lambda: world
        ins = Instruments(executor.placement_planner, world)
        pose = scene.object_pose(BLOCKER)
        row["blocker_pose"] = [round(float(v), 4) for v in list(pose[0]) + list(pose[1])]
        row["occupants"] = {t: sorted(o.entity_id for o in world.occupancy if o.target_id == t)
                            for t in TRAYS}
        row["v1"] = {f"{t}<-{e}": feas(ins, e, t) for e, t in
                     ((VICTIM, tray), (BLOCKER, "tray_left"), (BLOCKER, "tray_right"))}
        say(row)
        if BLOCKER not in row["occupants"][tray]:
            row["stop"] = "the authored body is not measured inside the region, so no blocker exists"
            return say(row) or row
        if row["v1"][f"{tray}<-{VICTIM}"]["feasible"]:
            row["stop"] = "the region is not blocked at v1: nothing to move, so not a §8 case"
            return say(row) or row

        # --- stage 2: can a body be lifted out of a tray at all? ---------------
        picked = executor.execute(SkillCall(plan_id="p", step_id="s1", skill="pick",
                                           args={"object_id": BLOCKER}))
        row["pick_out_of_tray"] = outcome(picked)
        say(row)
        if picked.status.value != "completed":
            row["stop"] = "the blocker cannot be lifted out of the tray: §8 is not reachable"
            return row
        moved = executor.execute(SkillCall(plan_id="p", step_id="s2", skill="place",
                                          args={"object_id": BLOCKER, "target_id": "tray_left"}))
        row["move_to_tray_left"] = outcome(moved)
        world = build_world_state(scene, 2, "obs_0002")
        ins.world = world
        row["after_move_out"] = {"occupants": {t: sorted(
            o.entity_id for o in world.occupancy if o.target_id == t) for t in TRAYS},
            f"{tray}<-{VICTIM}": feas(ins, VICTIM, tray)}
        say(row)
        if row["after_move_out"][f"{tray}<-{VICTIM}"]["feasible"] is not True:
            row["stop"] = "the region stayed infeasible after the blocker left: not a §8 case"
            return row

        # --- stage 3: the blocked placement, then the restore ------------------
        executor.execute(SkillCall(plan_id="p", step_id="s3", skill="pick",
                                   args={"object_id": VICTIM}))
        placed = executor.execute(SkillCall(plan_id="p", step_id="s4", skill="place",
                                            args={"object_id": VICTIM, "target_id": tray}))
        row["victim_placed"] = outcome(placed)
        world = build_world_state(scene, 3, "obs_0003")
        ins.world = world
        row["restore_feasible"] = feas(ins, BLOCKER, tray)
        say(row)
        if not row["restore_feasible"]["feasible"]:
            row["stop"] = "the restore is unreachable: the pair does not fit, so never author it"
            return row
        executor.execute(SkillCall(plan_id="p", step_id="s5", skill="safe_retreat", args={}))
        regrab = executor.execute(SkillCall(plan_id="p", step_id="s6", skill="pick",
                                            args={"object_id": BLOCKER}))
        row["restore_pick"] = outcome(regrab)
        if regrab.status.value != "completed":
            row["stop"] = "the blocker could not be re-grasped from its temporary region"
            say(row)
            return row
        back = executor.execute(SkillCall(plan_id="p", step_id="s7", skill="place",
                                         args={"object_id": BLOCKER, "target_id": tray}))
        row["restored"] = outcome(back)
        world = build_world_state(scene, 4, "obs_0004")
        ins.world = world
        row["final"] = {"occupants": {t: sorted(o.entity_id for o in world.occupancy
                                                if o.target_id == t) for t in TRAYS},
                        "victim_still": feas(ins, VICTIM, tray),
                        "spare_still_on_table": [round(float(v), 4)
                                                 for v in scene.object_pose(SPARE)[0]]}
        row["reachable"] = (back.status.value == "completed"
                            and len(row["final"]["occupants"][tray]) == 2)
        say(row)
        return row
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        say(row)
        return row
    finally:
        scene.close()


if __name__ == "__main__":
    rows = [probe(f"blocker_{k}", v) for k, v in BLOCKER_DIMS.items()]
    print("\n== one line per layout ==")
    for r in rows:
        print(f"{r['name']:24} blocked@v1={not r.get('v1', {}).get('tray_middle<-obj_red_1', {}).get('feasible', True)}"
              f" lifted={r.get('pick_out_of_tray', {}).get('status')}"
              f" unblocked={r.get('after_move_out', {}).get('tray_middle<-obj_red_1', {}).get('feasible')}"
              f" restore={r.get('restore_feasible', {}).get('feasible')}"
              f" reachable={r.get('reachable')}"
              f" stop={r.get('stop') or r.get('error')}")
    sys.exit(0)
