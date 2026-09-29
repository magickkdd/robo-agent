import json

p = "/home/czx/embodied-agent-batches/v03/e1/lh_score/long_horizon_v0.json"
d = json.load(open(p, encoding="utf-8"))
for k in sorted(d):
    v = d[k]
    if isinstance(v, (dict, list)):
        print(f"{k}: {type(v).__name__} len={len(v)}")
    else:
        print(f"{k}: {json.dumps(v, ensure_ascii=False)[:120]}")
print()
print("=== spec_rows ===")
sr = d.get("spec_rows")
print(json.dumps(sr, ensure_ascii=False, indent=1)[:3000])
print()
print("=== contrast (first 2) ===")
c = d.get("contrast")
if isinstance(c, dict):
    for k in list(c)[:6]:
        print(" ", k, "->", json.dumps(c[k], ensure_ascii=False)[:400])
elif isinstance(c, list):
    for x in c[:2]:
        print(" ", json.dumps(x, ensure_ascii=False)[:400])
print()
print("=== runs: first run's metric keys ===")
runs = d.get("runs")
if isinstance(runs, list) and runs:
    r0 = runs[0]
    print(" run keys:", sorted(r0)[:20] if isinstance(r0, dict) else type(r0))
    if isinstance(r0, dict):
        for k, v in r0.items():
            if isinstance(v, dict):
                print(f"  {k}: dict len={len(v)}")
                if v:
                    kk = sorted(v)[0]
                    print(f"    e.g. {kk} -> {json.dumps(v[kk], ensure_ascii=False)[:220]}")
            elif isinstance(v, list):
                print(f"  {k}: list len={len(v)}")
            else:
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:120]}")
