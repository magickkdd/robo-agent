"""`full`-on-VLM scores 2 of 4 where `privileged` scores 4 of 4. Match the denominators.

The cause is known: `lh_c3_capacity_and_shift` has two yellow objects, the camera arm grounds by
colour, and grounding raises before the episode starts. So the two arms are not measuring the same
four episodes, and a VLM-vs-privileged success-rate comparison across different episode sets is a
comparison of two different things.

There are three ways out, and only two of them are honest:

* drop or swap the case — that edits the **frozen task set**, which is a re-freeze and a decision,
  not something to slip into a report;
* give the camera a body-naming channel that does not go through colour — a product change;
* **report the comparison on the episodes both arms actually ran.**

The third costs nothing and is what §11 should have said. This measures the matched denominator
so the corrected table has real numbers behind it rather than a promise.
"""
import json
import os
from collections import Counter

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e2"


def load(path):
    full = os.path.join(ARCHIVE, path)
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8") as f:
        return json.load(f)


def episodes_by_case(root):
    """case_id -> outcome, from every episode_summary.json under a run root."""
    out = {}
    for dirpath, _dn, filenames in os.walk(root):
        if "episode_summary.json" not in filenames:
            continue
        s = json.load(open(os.path.join(dirpath, "episode_summary.json"), encoding="utf-8"))
        out.setdefault(s.get("case_id"), s.get("outcome"))
    return out


def find_roots(arm):
    """Run roots under one arm directory. The E2 archive keeps one directory per arm at the top
    level (`long_horizon_vlm`, `long_horizon_stub`, `long_horizon_privileged`) with the
    timestamped run root inside it — not `<arm>/<perception>/`, which the first version of this
    assumed and which found nothing."""
    arm_dir = os.path.join(ARCHIVE, arm)
    if not os.path.isdir(arm_dir):
        return []
    return [os.path.join(arm_dir, d) for d in sorted(os.listdir(arm_dir))
            if os.path.isdir(os.path.join(arm_dir, d))]


print("=== what each arm's directories hold ===")
per_arm = {}
for arm in ("long_horizon_vlm", "long_horizon_stub", "long_horizon_privileged"):
    roots = find_roots(arm)
    cases = {}
    errors = Counter()
    for r in roots:
        for c, outcome in episodes_by_case(r).items():
            cases[c] = outcome
        err = os.path.join(r, "errors.jsonl")
        if os.path.exists(err):
            for line in open(err, encoding="utf-8"):
                if line.strip():
                    d = json.loads(line)
                    errors[str(d.get("kind") or d.get("error", "?"))[:70]] += 1
    per_arm[arm] = cases
    label = arm.replace("long_horizon_", "")
    print(f"  {label:12s} roots={len(roots):2d}  episodes={len(cases)}")
    for c in sorted(cases):
        print(f"      {c:34s} {cases[c]}")
    if errors:
        print(f"      errors: {dict(errors)}")
    print()

print("=== the asymmetry, stated exactly ===")
vlm = per_arm.get("long_horizon_vlm", {})
priv = per_arm.get("long_horizon_privileged", {})
print(f"  vlm        ran {len(vlm)}: {sorted(vlm)}")
print(f"  privileged ran {len(priv)}: {sorted(priv)}")
print(f"  vlm success as reported        : "
      f"{sum(1 for v in vlm.values() if v == 'success')}/{len(vlm)}")
print(f"  privileged success as reported: "
      f"{sum(1 for v in priv.values() if v == 'success')}/{len(priv)}")
print()

matched = sorted(set(vlm) & set(priv))
print(f"=== the matched denominator: {len(matched)} episodes both arms ran ===")
for c in matched:
    print(f"  {c:34s} vlm={vlm[c]:8s} privileged={priv[c]}")
mv = sum(1 for c in matched if vlm[c] == "success")
mp = sum(1 for c in matched if priv[c] == "success")
print()
print(f"  matched      vlm {mv}/{len(matched)}   privileged {mp}/{len(matched)}")
unm = sorted(set(priv) - set(vlm))
print(f"  vlm-only missing: {unm}")
if unm:
    print()
    print("  So the headline 'privileged 4/4 -> vlm 0/4' is NOT what the matched set says.")
    print(f"  On the {len(matched)} episodes both arms ran, the comparison is "
          f"{mv}/{len(matched)} vs {mp}/{len(matched)}.")
    print("  Reporting 4/4 against 0/4 compares different episode sets and overstates the drop;")
    print("  the matched number is the one §11 can support, and it is still 0, but for a")
    print("  different and more defensible reason.")
