"""What did E5's four episodes actually record, read by slot rather than by directory order.

The first version of the E5 driver took `sorted(glob(...))[0]`, which is the *seed* episode's
directory (slot000 sorts before slot001), so its "read episode" row was the seed's numbers a
second time — and the read episode, which exited 1, went unmeasured. That is a reader bug, and
the kind that produces a plausible-looking criterion 1 out of the wrong episode.
"""
import glob
import json
import os

ROOT = "/home/czx/embodied-agent-batches/v03/e5"
for cell in sorted(glob.glob(os.path.join(ROOT, "p*_*"))):
    name = os.path.basename(cell)
    if name.endswith("_full") or name.endswith("_wo_episodic_memory"):
        print(f"\n========== {name}")
    store = os.path.join(cell, "store.jsonl")
    if os.path.exists(store):
        rows = [l for l in open(store, encoding="utf-8") if l.strip()]
        print(f"  store rows: {len(rows)}")
    for ep in sorted(glob.glob(os.path.join(cell, "episodes", "*"))):
        p = os.path.join(ep, "episode_summary.json")
        if not os.path.exists(p):
            print(f"  {os.path.basename(ep)}: NO episode_summary.json")
            continue
        s = json.load(open(p, encoding="utf-8"))
        types = {}
        ev = os.path.join(ep, "events.jsonl")
        if os.path.exists(ev):
            for line in open(ev, encoding="utf-8"):
                if line.strip():
                    try:
                        types[json.loads(line).get("type")] = types.get(
                            json.loads(line).get("type"), 0) + 1
                    except json.JSONDecodeError:
                        pass
        mem = {k: v for k, v in types.items() if str(k).startswith("memory_")}
        blk = s.get("episodic") or {}
        print(f"  {os.path.basename(ep)}")
        print(f"    slot={s.get('slot_index')} task_type={s.get('task_type')} "
              f"outcome={s.get('outcome')} official_won="
              f"{(s.get('evaluation') or {}).get('official_won')}")
        print(f"    memory events: {json.dumps(mem)}")
        print(f"    episodic: arm={blk.get('arm')} before={blk.get('store_size_before')} "
              f"after={blk.get('store_size_after')} retrievals={blk.get('retrievals')} "
              f"rows_retrieved={blk.get('rows_retrieved')} rows_used={blk.get('rows_used')} "
              f"written={blk.get('written_experience_id')}")
        if s.get("error"):
            print(f"    error: {str(s.get('error'))[:300]}")
        if s.get("where"):
            print(f"    where: {s.get('where')}")
