"""§11's Long-horizon group, six rows, read off LH-2's own product.

The point of printing this now rather than at report time is comparability: SPEC §7.3 requires
E1's rows to sit **beside** P5-c's rule rows and forbids merging them, so both have to be in
one place with their denominators visible, and the side-by-side is the finding (v0.2 §6.2:
on the privileged channel five of six switches did not move outcome, because the instruments
could not tell — a model decision source is what makes them able to).
"""
import json
import os

V03 = "/home/czx/embodied-agent-batches/v03/e1/lh_score/long_horizon_v0.json"
V02 = "/tmp/p5c/lh_score/long_horizon_v0.json"

new = json.load(open(V03, encoding="utf-8"))
old = json.load(open(V02, encoding="utf-8")) if os.path.exists(V02) else None

print("=== v0.3 (model decision source) ===")
print("top keys:", sorted(new)[:12])
metrics = new.get("metrics") or new.get("readings") or new.get("rows") or {}
if isinstance(metrics, dict):
    print("n metric groups:", len(metrics))
    for k in sorted(metrics)[:40]:
        v = metrics[k]
        if isinstance(v, dict):
            bits = {kk: v[kk] for kk in ("value", "numerator", "denominator", "not_measured",
                                          "not_measured_reason") if kk in v}
            print(f"  {k}: {json.dumps(bits, ensure_ascii=False)[:170]}")
        else:
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:170]}")
print()
print("comparisons present:", len(new.get("comparisons") or []))
for c in (new.get("comparisons") or [])[:20]:
    if isinstance(c, dict):
        bits = {kk: c[kk] for kk in ("metric", "reference", "arm", "reference_value",
                                      "arm_value", "changed", "comparable") if kk in c}
        print("  ", json.dumps(bits, ensure_ascii=False)[:190])

if old:
    print()
    print("=== the v0.2 rule rows, for the side-by-side ===")
    om = old.get("metrics") or old.get("readings") or old.get("rows") or {}
    changed = [c for c in (old.get("comparisons") or []) if isinstance(c, dict) and c.get("changed")]
    print("v0.2 metrics:", len(om), "| comparisons:", len(old.get("comparisons") or []),
          "| changed:", len(changed))
    for c in changed[:10]:
        print("   v0.2 changed:", json.dumps({k: c[k] for k in list(c)[:8]}, ensure_ascii=False)[:190])
    new_changed = [c for c in (new.get("comparisons") or [])
                   if isinstance(c, dict) and c.get("changed")]
    print("v0.3 comparisons:", len(new.get("comparisons") or []),
          "| changed:", len(new_changed))
    for c in new_changed[:20]:
        print("   v0.3 changed:", json.dumps({k: c[k] for k in list(c)[:8]}, ensure_ascii=False)[:190])
