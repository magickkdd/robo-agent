"""Settle which case, and which reader, the duplicate-colour refusal actually belongs to.

What is already known and contradicts the delivered residual:

* the error names `obj_yellow_1` and `obj_yellow_6`, and the episodes carrying the error are
  `lh_c1_shared_pair_restore` — the residual blames `lh_c3_capacity_and_shift`;
* the message continues "... a frame na...", which is about a **frame**, i.e. the camera — yet the
  stub arm records `offline: true`, so a stub reader never renders a frame.

Either the stub arm was not actually on the stub reader, or the refusal is reachable without a
frame. Both are worth knowing: the first means the arms are not what the manifest says they are,
the second means the cause is shared and the fix is not camera-local. The per-episode `perception`
block is the record that settles it, and the manifest is not (`perceive` is absent from all three
manifests — that is residual #3, the `offline`-only-sees-the-planner defect).
"""
import glob
import json
import os

ARCHIVE = "/home/czx/embodied-agent-batches/v03/e2"


def rows(arm):
    out = []
    for p in sorted(glob.glob(os.path.join(ARCHIVE, arm, "**", "episode_summary.json"),
                              recursive=True)):
        d = json.load(open(p, encoding="utf-8"))
        r = d.get("result") or {}
        perc = d.get("perception") or {}
        out.append({
            "file": os.path.relpath(p, ARCHIVE),
            "case": d.get("case_id"),
            "repeat": d.get("repeat"),
            "episode_id": d.get("episode_id"),
            "terminal_status": r.get("terminal_status"),
            "decision_rounds": r.get("decision_rounds"),
            "perception_keys": sorted(perc.keys()),
            "reader": perc.get("reader") or perc.get("perceiver") or perc.get("arm"),
            "perception": json.dumps(perc, ensure_ascii=False)[:220],
        })
    return out


print("=" * 78)
print("the full refusal text")
print("=" * 78)
for p in sorted(glob.glob(os.path.join(ARCHIVE, "**", "errors.jsonl"), recursive=True)):
    arm = p.split(os.sep)[len(ARCHIVE.split(os.sep))]
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        print(f"  [{arm}] {json.dumps(d, ensure_ascii=False)[:400]}")
    print()

print("=" * 78)
print("per episode, with the reader the summary itself records")
print("=" * 78)
for arm in ("long_horizon_vlm", "long_horizon_stub", "long_horizon_privileged"):
    label = arm.replace("long_horizon_", "")
    print(f"  --- {label} ---")
    for r in rows(arm):
        print(f"    {str(r['case']):32s} r{r['repeat']} status={r['terminal_status']} "
              f"rounds={r['decision_rounds']}")
        print(f"        perception: {r['perception']}")
    print()

print("=" * 78)
print("the two cases' declared colours, straight from the frozen set")
print("=" * 78)
FROZEN = ("/home/czx/embodied-agent-robot-agent-embodied-agent-4/configs/experiment/"
          "frozen_tasks_v1.json")
tasks = json.load(open(FROZEN, encoding="utf-8"))


def case_block(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("lh_c1_shared_pair_restore", "lh_c3_capacity_and_shift"):
                return v
            got = case_block(v)
            if got is not None:
                return got
    elif isinstance(node, list):
        for item in node:
            got = case_block(item)
            if got is not None:
                return got
    return None


for cid in ("lh_c1_shared_pair_restore", "lh_c3_capacity_and_shift"):
    block = case_block(tasks.get("dev"))
    print(f"  {cid}: found={block is not None}")
print()
print("  (the colour table the refusal reads is built at run time from the scene, so the")
print("   authoritative statement is the error itself: two objects in the SAME case both")
print("   declare yellow, and the guard refuses rather than pick one.)")
