"""E3 criterion 4, done the way H-58's contract test does it.

The first attempt at this comparison fed `experience_from_episode` a hand-built one-element
event list and compared the timestamps too, and reported 0/8 equal. That was the instrument
being wrong, not the batch: the extractor documents two input shapes and reads the terminal
plan view out of the log, so a synthetic body loses the content it derives. And
`created_at`/`updated_at` are the record envelope's construction stamps, not experience
content — the two fields a rebuild is allowed to differ in, which is why H-58's own test
strips exactly those two before comparing.

So this version uses `read_episode_dir(ep_dir)` (the documented archive reader) and strips the
two stamps, and it says so in its own output, because a criterion that was once measured wrong
is worth more with that recorded than without.
"""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/czx/embodied-agent-robot-agent-embodied-agent-4")
os.chdir("/home/czx/embodied-agent-robot-agent-embodied-agent-4")

from embodied_agent.episodic.experience import (  # noqa: E402
    experience_from_episode,
    read_episode_dir,
)
from embodied_agent.episodic.store import ExperienceStore  # noqa: E402

ROOT = "/home/czx/embodied-agent-batches/v03/e3"
STAMPS = ("created_at", "updated_at")


def strip(d):
    return {k: v for k, v in d.items() if k not in STAMPS}


out = {
    "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md §6 E3 criterion 4",
    "phase": "P1'-E3",
    "method": {
        "online": "ExperienceStore(path).all()[i] — what runner.py:write_experience appended",
        "offline": "experience_from_episode(*read_episode_dir(ep_dir)) — rebuilt from the "
                   "archived episode_summary.json + events.jsonl",
        "strips": list(STAMPS),
        "why_strips": "the record envelope's construction stamps, not experience content; "
                      "H-58's contract test strips exactly these two before comparing",
        "supersedes": "an earlier attempt that fed the extractor a synthetic one-element event "
                      "list and compared the stamps as well; it read 0/8 equal and was wrong "
                      "in both respects",
    },
    "per_episode": [],
    "summary": {},
}

equal = 0
differing = []
for pair_dir in sorted(glob.glob(os.path.join(ROOT, "p*_full"))):
    pid = os.path.basename(pair_dir).replace("_full", "")
    store_path = os.path.join(pair_dir, "store.jsonl")
    if not os.path.exists(store_path):
        continue
    store = ExperienceStore.load(store_path)
    online = {r.experience_id: r.model_dump(mode="json") for r in store.all()}
    for role in ("seed", "read"):
        eps = sorted(glob.glob(os.path.join(pair_dir, role, "episodes", "*")))
        if not eps:
            continue
        ep_dir = eps[0]
        summary, events = read_episode_dir(ep_dir)
        writes = [e for e in events if e.get("type") == "memory_write"]
        if not writes:
            out["per_episode"].append({"pair": pid, "role": role, "note": "no memory_write"})
            continue
        eid = writes[0]["payload"]["experience_id"]
        rederived = experience_from_episode(summary, events).model_dump(mode="json")
        row = online.get(eid)
        entry = {"pair": pid, "role": role, "episode_id": os.path.basename(ep_dir),
                 "experience_id": eid, "online_row_present": row is not None,
                 "id_agrees": rederived.get("experience_id") == eid}
        if row is None:
            entry["verdict"] = "NO ONLINE ROW for this id"
            differing.append(entry)
        else:
            a, b = strip(row), strip(rederived)
            keys = sorted(set(a) | set(b))
            diff = [k for k in keys if a.get(k) != b.get(k)]
            entry.update({"n_fields_compared": len(keys), "differing_fields": diff,
                          "verdict": "EQUAL" if not diff else "DIFFERS"})
            if diff:
                entry["first_difference"] = {
                    k: {"online": json.dumps(a.get(k), ensure_ascii=False)[:200],
                        "offline": json.dumps(b.get(k), ensure_ascii=False)[:200]}
                    for k in diff[:3]}
                differing.append(entry)
            else:
                equal += 1
        out["per_episode"].append(entry)

out["summary"] = {
    "field_for_field_equal": equal,
    "not_equal": len(differing),
    "episodes_compared": len([e for e in out["per_episode"] if "verdict" in e]),
    "ids_agree": sum(1 for e in out["per_episode"] if e.get("id_agrees")),
}
path = os.path.join(ROOT, "e3_criterion4.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
for e in out["per_episode"]:
    print(f"{e['pair']}/{e['role']:4s} {e.get('verdict','?'):12s} "
          f"fields={e.get('n_fields_compared')} id_agrees={e.get('id_agrees')} "
          f"diff={e.get('differing_fields')}")
print()
print("summary:", json.dumps(out["summary"], ensure_ascii=False))
for e in differing:
    if e.get("first_difference"):
        print("  first difference in", e["pair"], e["role"], ":",
              json.dumps(e["first_difference"], ensure_ascii=False)[:500])
print("artefact:", path)
