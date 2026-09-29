import glob
import json
import os

root = sorted(glob.glob("/home/czx/embodied-agent-batches/v03/e2_probe/vlm_full/*"))[0]
print("run:", root)
for ep in sorted(glob.glob(os.path.join(root, "episodes", "*"))):
    print("\n===", os.path.basename(ep))
    types = {}
    for line in open(os.path.join(ep, "events.jsonl"), encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            types[r.get("type")] = types.get(r.get("type"), 0) + 1
    print("  events:", json.dumps(types, sort_keys=True))
    mc = os.path.join(ep, "model_calls.jsonl")
    if os.path.exists(mc):
        rows = [json.loads(l) for l in open(mc, encoding="utf-8") if l.strip()]
        print(f"  model_calls.jsonl: {len(rows)} rows")
        tot_p = tot_c = 0
        for r in rows:
            tp = r.get("prompt_tokens_this_call") or 0
            tc = r.get("completion_tokens_this_call") or 0
            tot_p += tp
            tot_c += tc
            print(f"    ok={r.get('ok')} kind={r.get('kind')} finish={r.get('finish_reason')} "
                  f"raw={r.get('raw_chars')} p={tp} c={tc} lat={r.get('latency_s')} "
                  f"err={(r.get('error') or '')[:60]}")
        print(f"    TOTAL prompt={tot_p} completion={tot_c} requests={len(rows)}")
    else:
        print("  model_calls.jsonl: ABSENT")
    s = os.path.join(ep, "episode_summary.json")
    print("  episode_summary.json:", "present" if os.path.exists(s) else "ABSENT")
    per = sorted(glob.glob(os.path.join(ep, "perception", "*.png")))
    print(f"  frames rendered: {len(per)}")
