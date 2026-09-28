"""Can §5.4's decline branch ever fire on production artifacts? Three passes, zero writes to the repo.

`episodic/policy.py` declines a remembered object when a predicate the recall asserted is refuted by
this snapshot *and* absent from the plan's unsatisfied rows. The em set measured 0 declines in 4
pairs × 2 arms, and the arithmetic says why (a successful writer's `true` claim is refuted only while
its row is owed). This probe goes looking for the one store shape that breaks that argument: a
*failed* episode's residue, which records `placed:X:R=false`.

    pass 1  the long-horizon set once, `full` + policy=memory, one accumulating store — dump what
            each written experience actually asserts, because `false` rows have to exist first
    pass 2  the same six cases again against that store, both arms, so every case reads its own
            earlier residue — count declines, followed rounds and trajectory divergence

    PYTHONPATH=. /home/czx/miniforge3/envs/embodied/bin/python work/p3d_decline_reachability.py [ROOT]

Zero spend: rule policy, privileged perception, `StubReader` by default.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.v02 import ablation as ablation_record  # noqa: E402
from embodied_agent.episodic.store import ExperienceStore  # noqa: E402
from embodied_agent.evaluation.run import run_group  # noqa: E402
from embodied_agent.evaluation.tasks import build_set  # noqa: E402

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/p3d_decline_reach"
SET = "long_horizon"
CASES = [c.task_id for c in build_set(SET)]


def batch(out_root: str, arm: str, store_path: str) -> dict:
    os.makedirs(out_root, exist_ok=True)
    store = ExperienceStore.load(store_path)
    return run_group(SET, modes=("B",), planner_kind="rule", repeats=1, out_root=out_root,
                     case_ids=CASES, perceive="privileged", ablation=ablation_record(arm),
                     policy="memory", experience_store=store)


def episodes(run_root: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for directory in sorted(glob.glob(os.path.join(run_root, "episodes", "*.B.r0"))):
        with open(os.path.join(directory, "episode_summary.json"), encoding="utf-8") as f:
            summary = json.load(f)
        policy = summary.get("policy") or {}
        result, score = summary.get("result") or {}, summary.get("score") or {}
        case = os.path.basename(directory).split(".B.r0")[0]
        out[case] = {
            "rounds": result.get("decision_rounds"), "outcome": result.get("terminal_status"),
            "complete_success": score.get("complete_success"),
            "declines": policy.get("declines") or [],
            "followed_rounds": policy.get("followed_rounds") or [],
            "recalled": policy.get("recalled_ids") or [],
            "trajectory": [((e.get("args") or {}).get("object_id"), str(e.get("skill")))
                           for e in policy.get("trace") or []]}
    return out


def main() -> None:
    first = os.path.join(ROOT, "pass1")
    os.makedirs(first, exist_ok=True)
    first_store = os.path.join(first, "experiences.jsonl")
    if os.path.exists(first_store):
        os.remove(first_store)
    stats = batch(first, "full", first_store)
    print(f"pass 1 root={stats['root']}")
    store = ExperienceStore.load(first_store)
    falses = 0
    for experience in store:
        pattern = list(experience.state_pattern)
        false_rows = [p for p in pattern if p.endswith("=false")]
        falses += len(false_rows)
        print(f"  {experience.episode_id.split('.')[0]:32s} outcome={experience.outcome.value:8s} "
              f"true={sum(p.endswith('=true') for p in pattern)} "
              f"false={len(false_rows)} unknown={sum(p.endswith('=unknown') for p in pattern)}")
        for row in false_rows:
            print(f"      refutable-when-redone: {row}")
    print(f"pass 1 stored_false_claims={falses}")

    arms: dict[str, dict] = {}
    for arm in ("full", "wo_episodic_memory"):
        out_root = os.path.join(ROOT, "pass2", arm)
        os.makedirs(out_root, exist_ok=True)
        copied = os.path.join(out_root, "experiences.jsonl")
        shutil.copyfile(first_store, copied)
        second = batch(out_root, arm, copied)
        arms[arm] = episodes(second["root"])
        print(f"pass 2 {arm} root={second['root']} outcomes="
              f"{json.dumps(second.get('outcomes'), sort_keys=True)}")

    print(f"{'case':34s} {'declines':22s} {'followed':18s} {'rnd c/t':9s} "
          f"{'out c/t':16s} split")
    total_declines = 0
    for case in CASES:
        treated, control = arms["full"][case], arms["wo_episodic_memory"][case]
        total_declines += len(treated["declines"])
        split = next((i for i, (a, b) in enumerate(zip(treated["trajectory"], control["trajectory"]))
                      if a != b), None)
        print(f"{case:34s} {str(treated['declines'])[:22]:22s} "
              f"{str(treated['followed_rounds'])[:18]:18s} "
              f"{str(control['rounds']) + '/' + str(treated['rounds']):>9s} "
              f"{(str(control['outcome']) + '/' + str(treated['outcome']))[:16]:>16s} {split}")
    print(f"pass 2 episodes={len(CASES) * 2} declines={total_declines} "
          f"divergent_cases="
          f"{sum(1 for c in CASES if [x for x in arms['full'][c]['trajectory']] != [x for x in arms['wo_episodic_memory'][c]['trajectory']])}")


if __name__ == "__main__":
    main()
