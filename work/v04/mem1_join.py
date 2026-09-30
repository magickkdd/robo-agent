"""v0.4 P5 instrument: the four-pair MEM-1 reading, joined by reference.

P6's model-seat batch could not finish pairs em_p1/em_p2's control arm (rate limiting);
v0.4's MEM-1' batch ran exactly those two pair-arms into its own root. The pre-registered
reading rule (v04_mem1_amendment_1.json) says the completed table joins the two roots by
reference and re-runs no paid episode — this instrument is that join, using the shipped
`--memory-metrics` machinery (never a re-implementation of M1-M4):

  1. load the v0.3 root's pairs_run.json (all four pairs; p1/p2 control roles without
     episodes);
  2. replace em_p1/em_p2's `wo_episodic_memory` entries with the v0.4 root's, which hold
     the missing episodes on the same seat;
  3. write the merged ledger into a joined root with a provenance block naming both
     sources, and run the shipped measure over it.

Everything the measure reads is the originals' bytes; nothing is re-run, nothing copied.
The join adds one caveat the printed seat_note does not know: the p1/p2 control episodes
ran a day after their treatment episodes, so a within-pair difference there carries a
time split the within-pair memory attribution must be read against.
"""
import json
import os
import subprocess
import sys

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
PY = "/home/czx/miniforge3/envs/embodied/bin/python"
V03 = "/home/czx/embodied-agent-batches/v03/mem1_model"
V04 = "/home/czx/embodied-agent-batches/v04/mem1_model"
JOINED = "/home/czx/embodied-agent-batches/v04/mem1_joined"
ARMS = ("full", "wo_episodic_memory")
JOIN_PAIRS = ("em_p1", "em_p2")


def main() -> int:
    with open(os.path.join(V03, "pairs_run.json"), encoding="utf-8") as f:
        v03 = json.load(f)
    with open(os.path.join(V04, "pairs_run.json"), encoding="utf-8") as f:
        v04 = json.load(f)

    v04_entries = {(row["pair_id"], row["arm"]): row for row in v04["batches"]}
    merged, replaced = [], []
    for row in v03["batches"]:
        key = (row["pair_id"], row["arm"])
        if row["pair_id"] in JOIN_PAIRS and row["arm"] == "wo_episodic_memory":
            fresh = v04_entries.get(key)
            if fresh is None:
                print(f"refusing to join: {key} has no v0.4 replacement")
                return 1
            replaced.append(key)
            merged.append({**fresh, "joined_from": {
                "treatment_side": "v0.3 control entries replaced wholesale",
                "why": "the v0.3 root's control reader produced no episode (P6's halt); "
                       "the v0.4 root ran the pair-arm on the same seat"}})
        else:
            merged.append(row)

    joined = {**v03,
              "batches": merged,
              "joined": {"provenance": {
                  "primary_root": V03,
                  "donor_root": V04,
                  "replaced_entries": [f"{p}/{a}" for p, a in replaced],
                  "rule": "join by reference; no paid episode re-run "
                          "(v04_mem1_amendment_1.json)",
                  "caveat": "the p1/p2 control episodes ran 2026-09-30, a day after their "
                            "treatment episodes: a within-pair difference there carries a "
                            "time split alongside the draw split the seat_note already names"}}}
    os.makedirs(JOINED, exist_ok=True)
    with open(os.path.join(JOINED, "pairs_run.json"), "w", encoding="utf-8") as f:
        json.dump(joined, f, ensure_ascii=False, indent=1)

    env = {**os.environ, "PYTHONPATH": "."}
    r = subprocess.run([PY, "-m", "embodied_agent.cli", "em-pairs",
                        "--memory-metrics", JOINED],
                       capture_output=True, text=True, cwd=REPO, env=env)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "pybullet" not in ln]
    print("\n".join(tail[-30:]))
    print(f"rc={r.returncode}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
