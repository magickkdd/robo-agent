import json

p = ("/home/cxz/vlm.json")
V03 = ("/home/czx/embodied-agent-batches/v03/e1/vlm_contrast/"
       "episode_contrast_all.json")
V02 = "/tmp/v02_p1f_all47_p6/contrast_all.json"
import os

v = json.load(open(V03, encoding="utf-8"))
print("definition_id:", v.get("definition_id"), "| definition_version:", v.get("definition_version"))
print("paired_cases:", v.get("paired_cases"), "| not_comparable:", v.get("not_comparable"))
print("planner:", v.get("planner"), "| modes:", v.get("modes"))
print("arms:", json.dumps(v.get("arms"), ensure_ascii=False)[:200])
print("categories:", json.dumps(v.get("categories"), ensure_ascii=False)[:300])
print("cost:", json.dumps(v.get("cost"), ensure_ascii=False)[:300])
pd = v.get("paired_diff_sensing_minus_privileged")
print("paired_diff keys:", sorted(pd) if isinstance(pd, dict) else type(pd))
if isinstance(pd, dict):
    for k in sorted(pd):
        print(f"  {k}: {json.dumps(pd[k], ensure_ascii=False)[:180]}")

# the control: this group is stub-based, so an unchanged instrument must reproduce v0.2's values
if os.path.exists(V02):
    o = json.load(open(V02, encoding="utf-8"))
    opd = o.get("paired_diff_sensing_minus_privileged")
    print()
    print("=== v0.2's product, same rows, for a control ===")
    print("v0.2 definition_id:", o.get("definition_id"), "paired_cases:", o.get("paired_cases"))
    if isinstance(opd, dict) and isinstance(pd, dict):
        same = diff = 0
        for k in sorted(set(opd) & set(pd)):
            if opd[k] == pd[k]:
                same += 1
            else:
                diff += 1
                print(f"  DIFFERS {k}: v0.2={json.dumps(opd[k], ensure_ascii=False)[:110]} "
                      f"v0.3={json.dumps(pd[k], ensure_ascii=False)[:110]}")
        print(f"  identical rows: {same} | differing: {diff} | only in one: "
              f"{sorted(set(opd) ^ set(pd))}")
