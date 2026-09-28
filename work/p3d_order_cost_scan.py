"""Does any em-shaped case have an *order-dependent cost*? Read-only, one scene per order.

The four frozen pairs measured a memory that changes which object is picked first and costs nothing
(7 rounds in both arms). A costed negative transfer — the thing §11's `negative transfer` row and
§13's RQ3 actually ask for — needs an order that fails where another succeeds, or one that costs
extra calls where another does not. In a flat "place each object" task the only such channel is
region capacity: two objects owed to one tray, where the first placement takes space the second one
needs, or lands in a way the second one undoes.

So this walks **every** placement order of each contested case (3 objects ⇒ 6 orders) on a real
scene, driving `SkillExecutor` directly so the verdicts are the engine's own, and records which
orders complete and how many calls were refused. Nothing here is written to an artifact, a store or
a prompt, and no policy is involved: this asks a question about the hardware, not about memory.

    PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python work/p3d_order_cost_scan.py
"""
from __future__ import annotations

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.contracts import SkillCall  # noqa: E402
from embodied_agent.core.runtime import build_world_state  # noqa: E402
from embodied_agent.core.scene import PhysicsScene  # noqa: E402
from embodied_agent.core.skills import SkillExecutor  # noqa: E402
from embodied_agent.evaluation.episodic_tasks import EM_CONFIGS, build_case  # noqa: E402
from embodied_agent.evaluation.tasks import assign_targets, make_objects  # noqa: E402

# The set's own contested readers, plus a small grid of `shared=True` three-object cases: every
# em-shaped case with two objects owed to one region is a candidate for an order-dependent cost,
# and only those cases have such a pair. `shared=False` cases are excluded because their targets are
# all distinct, which leaves nothing for one placement to take away from another.
CASES: list[tuple[str, int, list[dict], list[tuple[str, str]]]] = []

for cfg in EM_CONFIGS:
    case = build_case(cfg)
    CASES.append((cfg["name"], case.seed, case.objects,
                  [(a.entity_id, a.target_id) for a in case.eval_spec.assignments]))

for seed in (415, 416, 417, 418, 419, 425, 435):
    objs = make_objects(3, seed)
    targets = assign_targets(objs, seed, shared=True)
    CASES.append((f"shared3_seed{seed}", seed, objs,
                  sorted((o["entity_id"], targets[o["entity_id"]]) for o in objs)))


def run_order(pairs, seed, objects) -> dict:
    scene = PhysicsScene(seed=seed, object_layout=objects, gui=False)
    steps, refusals = [], []
    try:
        executor = SkillExecutor(scene, world_provider=lambda: None)
        version = 1
        for eid, region in pairs:
            for skill, args in (("pick", {"object_id": eid}),
                                ("place", {"object_id": eid, "target_id": region})):
                version += 1
                world = build_world_state(scene, version, f"obs_{version:04d}")
                executor.world_provider = lambda w=world: w
                result = executor.execute(SkillCall(plan_id="probe", step_id=f"s{len(steps)}",
                                                    skill=skill, args=args))
                status = str(result.status).rsplit(".", 1)[-1]
                steps.append(f"{skill}>{eid}:{status}")
                if status != "completed":
                    refusals.append(f"{skill}>{eid}:{status}"
                                    f":{getattr(result, 'failure_code', '') or ''}")
        occupancy = build_world_state(scene, version + 1, "obs_final").occupancy
        inside = {(o.entity_id, o.target_id) for o in occupancy if o.fully_inside}
        return {"calls": len(steps), "refusals": refusals,
                "all_in_target": all((e, t) in inside for e, t in pairs),
                "inside": sorted(inside)}
    finally:
        scene.close()


def main() -> None:
    for name, seed, objects, pairs in CASES:
        regions: dict[str, list[str]] = {}
        for eid, region in pairs:
            regions.setdefault(region, []).append(eid)
        contested = {r: v for r, v in regions.items() if len(v) > 1}
        print(f"== {name} seed={seed} contested={contested or 'none'}")
        results = []
        for order in itertools.permutations(pairs):
            r = run_order(list(order), seed, objects)
            results.append(r)
            print(f"   {[e for e, _ in order]} ok={r['all_in_target']} "
                  f"refused={len(r['refusals'])} {r['refusals'][:2]}")
        outcomes = {(r["all_in_target"], len(r["refusals"])) for r in results}
        print(f"   distinct_outcomes={len(outcomes)} {sorted(outcomes)}")


if __name__ == "__main__":
    main()
