"""BST-1.0 Phase 0/2: task enumeration and the pre-declared run order.

Two things happen here and nothing else: the pinned ALFWorld text data is
enumerated through the official collector, and a *frozen* schedule of
(task, repeat) slots is derived from it. Selection is `SHA-256(selection_seed |
task_id)` per task type (SPEC 5.2); execution order is a second SHA-256 key, so
the sequence is fixed before any episode runs and no class is concentrated at one
end of the run.

Run:
  /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.tasks \
      --out /tmp/bst_v01/task_manifest_draft.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from embodied_agent.benchmark.envcheck import (filesystem_enumeration, official_collection,
                                               textworld_base_config)

SELECTION_SEED = "20260919"
RUN_SEEDS = {0: "20260919", 1: "20260920"}
ORDER_SEED = "20260919"
REPEATS = 2
# SPEC-BST 5.2/12 Phase 2: the train set is 12 *distinct tasks*, one episode each —
# an interface and budget precheck, not a second measurement sample.
DEV_REPEATS = 1
SCREEN_PER_TYPE = 4
DEV_PER_TYPE = 2
SPLIT_TO_EVAL = {"train": "train", "valid_seen": "eval_in_distribution",
                 "valid_unseen": "eval_out_of_distribution"}
TYPE_IDS = {1: "pick_and_place_simple", 2: "look_at_obj_in_light",
            3: "pick_clean_then_place_in_recep", 4: "pick_heat_then_place_in_recep",
            5: "pick_cool_then_place_in_recep", 6: "pick_two_obj_and_place"}
NAME_TO_TYPE_ID = {v: k for k, v in TYPE_IDS.items()}


def _h(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def game_id(gamefile: str, data_dir: str) -> str:
    """Task identity = path of the game directory under its split, e.g.
    pick_heat_then_place_in_recep-Potato-None-GarbageCan-10/trial_T2019...

    One such folder is one official game instance: fixed initial state, fixed
    instruction, fixed success test. That is the unit `task_id` means in SPEC 5.2
    and the cluster unit in SPEC 8.4."""
    rel = os.path.relpath(os.path.dirname(os.path.abspath(gamefile)), os.path.abspath(data_dir))
    parts = Path(rel).parts
    split = parts[1] if len(parts) > 1 and parts[0] == "json_2.1.1" else parts[0]
    return "/".join(parts[2:]) if split else rel, split


def load_games(data_dir: str, split: str) -> dict:
    fs = filesystem_enumeration(data_dir, [split])[split]
    games = fs.get("games", {})
    out = {}
    for gid, meta in games.items():
        out[gid] = {"task_id": gid, "split": split, "task_type": meta["task_type"],
                    "type_id": NAME_TO_TYPE_ID.get(meta["task_type"]),
                    "game_tw_pddl_sha256": meta["game_tw_pddl_sha256"],
                    "traj_data_sha256": meta["traj_data_sha256"],
                    "gamefile": os.path.join(data_dir, "json_2.1.1", split, gid, "game.tw-pddl"),
                    "selection_key": _h(SELECTION_SEED, gid)}
    return out


def select_per_type(games: dict, per_type: int) -> dict:
    by_type = defaultdict(list)
    for g in games.values():
        by_type[g["task_type"]].append(g)
    chosen, shortfall = {}, {}
    for ttype, lst in by_type.items():
        lst.sort(key=lambda g: (g["selection_key"], g["task_id"]))
        take = lst[:per_type]
        chosen[ttype] = [g["task_id"] for g in take]
        if len(lst) < per_type:
            shortfall[ttype] = {"available": len(lst), "requested": per_type}
    return {"per_type": chosen, "n_types": len(chosen),
            "n_tasks": sum(len(v) for v in chosen.values()), "shortfall": shortfall}


def slots_for(task_ids: list[str], games: dict, repeats: int = REPEATS) -> list[dict]:
    """Fixed pseudo-random execution order over (task, repeat) slots, interleaved
    across types because the order key depends only on the identity, not on any
    grouping (SPEC 5.2: 按 task_type、repeat 交错执行，固定洗牌顺序)."""
    out = []
    for tid in task_ids:
        g = games[tid]
        for r in range(repeats):
            out.append({
                "task_id": tid, "split": g.get("split"), "repeat": r,
                "run_seed": RUN_SEEDS[r],
                "task_type": g["task_type"], "type_id": g["type_id"],
                "gamefile": g["gamefile"], "game_tw_pddl_sha256": g["game_tw_pddl_sha256"],
                "order_key": _h(ORDER_SEED, "order", tid, f"repeat{r}"),
            })
    out.sort(key=lambda s: (s["order_key"], s["task_id"], s["repeat"]))
    for i, s in enumerate(out):
        s["slot_index"] = i
    return out


def build(data_dir: str, out: str, max_steps: int = 60) -> dict:
    t0 = time.time()
    config = textworld_base_config(data_dir, max_steps)
    coll = official_collection(config)
    uf = {k: {kk: vv for kk, vv in v.items() if kk != "game_files"} for k, v in coll.items()}
    files = {k: v["game_files"] for k, v in coll.items()}

    unseen = load_games(data_dir, "valid_unseen")
    train = load_games(data_dir, "train")

    # cross-check: the official collector and the on-disk enumeration must agree
    official_unseen_ids = {game_id(f, data_dir)[0] for f in files["eval_out_of_distribution"]}
    disk_unseen_ids = set(unseen)
    screen = select_per_type(unseen, SCREEN_PER_TYPE)
    dev = select_per_type(train, DEV_PER_TYPE)

    screen_tasks = [t for v in screen["per_type"].values() for t in v]
    screening_slots = slots_for(screen_tasks, unseen)
    rest_tasks = [t for t in unseen if t not in set(screen_tasks)]
    remainder_slots = slots_for(rest_tasks, unseen)
    dev_tasks = [t for v in dev["per_type"].values() for t in v]
    dev_slots = slots_for(dev_tasks, train, repeats=DEV_REPEATS)

    manifest = {
        "bst_spec": "BST-1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_dir": data_dir,
        "selection_seed": SELECTION_SEED, "order_seed": ORDER_SEED, "run_seeds": RUN_SEEDS,
        "repeats": REPEATS,
        "id_definition": "path of the game directory under its split "
                         "(json_2.1.1/<split>/<task-type>-<Object>-None-<Recep>-<scene>/<trial>)",
        "official_collector": uf,
        "cross_check": {
            "official_valid_unseen_games": uf["eval_out_of_distribution"]["num_games"],
            "disk_solvable_valid_unseen_games": len(disk_unseen_ids),
            "ids_match": official_unseen_ids == disk_unseen_ids,
            "only_in_official": sorted(official_unseen_ids - disk_unseen_ids)[:10],
            "only_on_disk": sorted(disk_unseen_ids - official_unseen_ids)[:10]},
        "dev_train": {"per_type": dev["per_type"], "n_tasks": dev["n_tasks"],
                      "repeats": DEV_REPEATS, "n_slots": len(dev_slots),
                      "types_present": sorted(dev["per_type"]),
                      "slots": dev_slots},
        "screening": {"tasks_per_type": SCREEN_PER_TYPE, "per_type": screen["per_type"],
                      "n_tasks": screen["n_tasks"], "n_slots": len(screening_slots),
                      "shortfall": screen["shortfall"],
                      "by_type": {t: uf["eval_out_of_distribution"]["by_task_type"][t]
                                  for t in screen["per_type"]}},
        "full": {"n_valid_unseen_games": len(unseen), "n_slots": len(screening_slots)
                 + len(remainder_slots),
                 "planned_total_slots": len(unseen) * REPEATS},
        "order": {"rule": "SHA-256(order_seed|order|task_id|repeat<n>) ascending, screening "
                          "segment first, remainder second; the 48 screening slots are "
                          "reused, not re-run (SPEC 5.2)",
                  "screening_slots": screening_slots, "remainder_slots": remainder_slots},
        "segments": {
            "dev_train": {"slots": "dev_train.slots", "n_slots": len(dev_slots),
                          "purpose": "SPEC 12 Phase 2 接口测试与预算预检；train 任务，不是测量样本"},
            "screening": {"slots": "order.screening_slots", "n_slots": len(screening_slots),
                          "purpose": "SPEC 12 Phase 3 每类 4 任务 x 2 repeat = 48 集，8,000,000 token 上限"},
            "full": {"slots": ["order.screening_slots", "order.remainder_slots"],
                     "n_slots": len(screening_slots) + len(remainder_slots),
                     "purpose": "SPEC 12 Phase 3 valid_unseen 134 任务 x 2 = 268 集，40,000,000 token "
                                "上限；已完成的筛查槽位续跑而不是重跑"}},
        "games_valid_unseen": {k: {kk: vv for kk, vv in v.items() if kk != "gamefile"}
                               for k, v in sorted(unseen.items())},
        "build_seconds": round(time.time() - t0, 1),
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in
                      ("selection_seed", "order_seed", "run_seeds", "repeats", "cross_check",
                       "dev_train", "screening", "full")}, indent=1))
    print("official split sizes:",
          {k: v["num_games"] for k, v in uf.items()})
    print("wrote", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("ALFWORLD_DATA")
                    or os.path.expanduser("~/.cache/alfworld"))
    ap.add_argument("--out", default="/tmp/bst_v01/task_manifest_draft.json")
    ap.add_argument("--max-steps", type=int, default=60)
    a = ap.parse_args()
    os.environ.setdefault("ALFWORLD_DATA", a.data_dir)
    return build(a.data_dir, a.out, a.max_steps)


if __name__ == "__main__":
    raise SystemExit(main())
