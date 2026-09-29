import glob
import json
import os

root = "/home/czx/embodied-agent-batches/v03/e3"
for pair in ("p1", "p3"):
    for arm in ("full", "wo_episodic_memory"):
        cell = os.path.join(root, f"{pair}_{arm}")
        print(f"\n========== {pair} / {arm}")
        store = os.path.join(cell, "store.jsonl")
        if os.path.exists(store):
            rows = [json.loads(l) for l in open(store, encoding="utf-8") if l.strip()]
            print(f"  store rows: {len(rows)}")
            for r in rows:
                keys = {k: r[k] for k in list(r)[:14]}
                print("   ", json.dumps(keys, ensure_ascii=False)[:400])
        for role in ("seed", "read"):
            eps = sorted(glob.glob(os.path.join(cell, role, "episodes", "*")))
            if not eps:
                print(f"  {role}: (no episode dir)")
                continue
            f = os.path.join(eps[0], "events.jsonl")
            print(f"  --- {role} ({os.path.basename(eps[0])})")
            for line in open(f, encoding="utf-8"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                t = rec.get("type", "")
                if t.startswith("memory_"):
                    print(f"    {t}: {json.dumps(rec.get('payload'), ensure_ascii=False)[:600]}")
