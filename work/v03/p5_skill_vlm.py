"""Two more §11 groups read off their own products, and one honest gap.

SKILL-1 (5 rows) and the VLM group (3 rows) both ran on this code, so the readings below are
re-measurements rather than copies — which is what SPEC §7.2 asks for ("六组 27 行原样重取").

The Memory group (4 rows) is the gap, and it is a *code* gap rather than a budget one:
`cli em-pairs` has no `--planner`, so the only way to put a model in the decision seat for the
pair set is to add the flag. That is a product change and it is not authorised, so the Memory
group is reported as not re-taken with the reason named, per SPEC §13.2 ("not-measured 只允许
来自'该臂该通道的结构性不可达'，不允许来自'没跑'" — and here the honest word is that the path
does not exist, which is the structural kind).
"""
import json

SKILL = ("/home/czx/embodied-agent-batches/v03/e1/skill_score/"
         "skill_acquisition_SKILL_1.json")
VLM = ("/home/czx/embodied-agent-batches/v03/e1/vlm_contrast/"
       "episode_contrast_all.json")

s = json.load(open(SKILL, encoding="utf-8"))
print("=== SKILL-1 ===")
print("top keys:", sorted(s)[:16])
arm_audit = s.get("arm_audit") or s.get("arm_enforcement") or {}
print("arm_audit:", json.dumps(arm_audit, ensure_ascii=False)[:300])
for key in ("metrics", "pools", "spec_rows", "definitions"):
    if key in s:
        v = s[key]
        print(f"{key}: {type(v).__name__} len={len(v)}")
metrics = s.get("metrics") or s.get("pools") or {}
if isinstance(metrics, dict):
    for k in sorted(metrics)[:40]:
        m = metrics[k]
        if isinstance(m, dict):
            bits = {kk: m[kk] for kk in ("value", "numerator", "denominator",
                                         "not_measured_reason") if kk in m}
            print(f"  {k:36s} {json.dumps(bits, ensure_ascii=False)[:160]}")
print()
v = json.load(open(VLM, encoding="utf-8"))
print("=== VLM group (V1/V2/V3) ===")
print("top keys:", sorted(v)[:16])
for key in ("metrics", "batteries", "coverage", "rows"):
    if key in v:
        val = v[key]
        print(f"{key}: {type(val).__name__} len={len(val) if hasattr(val, '__len__') else '-'}")
m = v.get("metrics") or v.get("rows") or {}
if isinstance(m, dict):
    for k in sorted(m)[:30]:
        val = m[k]
        if isinstance(val, dict):
            bits = {kk: val[kk] for kk in ("value", "numerator", "denominator",
                                           "not_measured_reason") if kk in val}
            print(f"  {k:34s} {json.dumps(bits, ensure_ascii=False)[:150]}")
elif isinstance(m, list):
    for x in m[:20]:
        print("  ", json.dumps(x, ensure_ascii=False)[:180])
