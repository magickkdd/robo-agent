import json
import os

d = json.load(open("/tmp/p5c/matrix.json"))
print("matrix.json is a list of", len(d), "rows; keys:", sorted(d[0]) if d else None)
for row in d:
    print(json.dumps({k: row[k] for k in sorted(row)
                      if k in ("set", "ablation", "arm", "condition", "rc", "out_root",
                               "episodes", "planner", "perceive", "policy", "modes",
                               "repeats", "run_dir", "dir")},
                     ensure_ascii=False))
print()
print("--- cell directories actually on disk ---")
for sub in ("long_horizon", "em"):
    p = os.path.join("/tmp/p5c", sub)
    if os.path.isdir(p):
        print(sub, "->", sorted(os.listdir(p)))
