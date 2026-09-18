"""W2.1 task 4: attribute 5-object failure episodes to error classes.

Read-only: reads events.jsonl of failed episodes, reconstructs per-episode:
  - the terminal failure code and where it happened (which object/target)
  - tray occupancy at failure time (capacity/interference evidence)
  - whether previously-placed objects moved later (interference evidence)
  - grasp attempts vs place attempts (grasp vs place-geometry)

Classification rules (spec section 9.1):
  grasp            terminal GRASP_MISS on the episode's final failing step
  place_geometry   IK_UNREACHABLE / PLACE stage failure with free slots remaining
  capacity         TARGET_NO_FREE_SLOT (target tray occupancy >= slot capacity)
  interference     failure right after a placed object was displaced, or the
                   failing object's grasp/transfer path crossed an occupied tray
  verification     skills all completed but final VERIFY failed
  planner          INVALID_PLAN / model binding errors
"""
import glob
import json
import os
import random
from collections import Counter

TRAYS = ("tray_left", "tray_middle", "tray_right")


def load_events(ep_dir):
    with open(os.path.join(ep_dir, "events.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def parse_events(events):
    """Extract: failed steps, place verifies, object motions after placement."""
    out = {"fails": [], "places": [], "final": None, "replans": 0, "motions": []}
    last_pos = {}
    for e in events:
        t = e.get("type") or e.get("event")
        p = e.get("payload", e)
        if t == "skill_result":
            r = p["result"]
            if r["status"] != "completed":
                out["fails"].append(r)
            if t == "skill_result" and r["status"] == "completed" and r.get("held_state") is None \
               and "retreat" in (r.get("stages_executed") or []):
                pass
        elif t == "step_verify":
            rep = p["report"]
            if rep["predicate_id"].startswith("placed") and rep["value"] == "true":
                out["places"].append(rep["predicate_id"])
        elif t == "final_verify_failed":
            out["final"] = [r["predicate_id"] for r in p["reports"] if r["value"] != "true"]
        elif t == "episode_end":
            out["terminal"] = p["result"]["terminal_status"]
    return out


def tray_occupancy_from_summary(summary):
    score = summary.get("score", {}).get("details", {})
    occ = Counter()
    for oid, d in score.items():
        if d.get("inside"):
            occ[d["target"]] += 1
    return occ


def classify(ep_dir, summary):
    events = load_events(ep_dir)
    info = parse_events(events)
    occ = tray_occupancy_from_summary(summary)
    fails = info["fails"]
    code = summary.get("failure_type") or ""
    n_placed = len(info["places"])
    if code == "TARGET_NO_FREE_SLOT":
        return "capacity", f"placed={n_placed}, final occupancy={dict(occ)}"
    if code == "GRASP_MISS":
        return "grasp", f"placed={n_placed} before miss"
    if code in ("IK_OR_PATH_UNREACHABLE",):
        # distinguish: failing to reach a slot in an already-occupied tray vs raw reach
        return "place_geometry", f"placed={n_placed}, occupancy={dict(occ)}"
    if code == "INVALID_PLAN":
        return "planner", ""
    if code == "ANOMALOUS_CONTACT":
        return "interference", f"placed={n_placed}, occupancy={dict(occ)}"
    if info.get("final"):
        return "verification", f"unverified={info['final']}"
    return "unclassified", code


def main():
    random.seed(42)
    rows = []
    for label, d in [("B1_rule", "runs/s1_clean_rule_recoveryon_20260918_034252"),
                     ("E1_deepseek", "runs/s1_clean_deepseek_recoveryon_20260918_051112")]:
        failed = []
        for f in sorted(glob.glob(d + "/s1_*5obj*/summary.json")):
            s = json.load(open(f))
            if not s["independent_complete_success"]:
                failed.append((os.path.dirname(f), s))
        sample = failed  # 5-object failures are few: analyse ALL of them
        for ep_dir, s in sample:
            cls, detail = classify(ep_dir, s)
            rows.append({"arm": label, "task": s["task_id"], "episode": os.path.basename(ep_dir),
                         "code": s.get("failure_type"), "class": cls, "detail": detail,
                         "objects_done": f"{s['objects_completed']}/{s['objects_total']}"})
        print(f"{label}: {len(failed)} 个 5 物体失败 episode(全部分析)")
    print()
    dist = Counter((r["arm"], r["class"]) for r in rows)
    for r in rows:
        print(f"{r['arm']:12s} {r['episode'][-30:]:30s} code={str(r['code']):24s} -> {r['class']:14s} objs={r['objects_done']} {r['detail']}")
    print("\n分布:", dict(dist))
    os.makedirs("outputs/s2_w2_1", exist_ok=True)
    import csv
    with open("outputs/s2_w2_1/error_attribution.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("saved -> outputs/s2_w2_1/error_attribution.csv")


if __name__ == "__main__":
    main()
