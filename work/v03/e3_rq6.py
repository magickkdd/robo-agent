"""E3's answer to RQ6, written from the batch rather than from a summary of it.

SPEC-v0.3 §3 RQ6: does the episodic-memory seed/readout pair produce measurable reuse on the
continuous-control channel? SPEC §6 E3 sets four criteria; this records each one with its
denominator, and then says what the reading does and does not license.

The honest limit, stated first because it is the part that would be easy to overstate: on this
channel the memory is retrieved and used, and the round's judgement against it is filed — but
the two skills the loop executed in every read episode are the two it would have executed
anyway. So retrieval, use and judgement are measured; a *benefit* is not. That is the same
limit MEM-1 hit on the desktop (`followed_where_control_chose_the_same 0.4286`), reached here
by a different route, and SPEC §3 RQ6 is explicit that the desktop conclusion does not
transfer automatically: different channel, different predicate shape, different world-reset
semantics. This batch is the evidence for that sentence.
"""
import glob
import json
import os

ROOT = "/home/czx/embodied-agent-batches/v03/e3"


def events_of(ep_dir):
    out = []
    for line in open(os.path.join(ep_dir, "events.jsonl"), encoding="utf-8"):
        if line.strip():
            out.append(json.loads(line))
    return out


report = {
    "spec": "Continuous-Decision-Embodied-Agent-SPEC-v0.3.md",
    "phase": "P1'-E3", "experiment": "E3", "rq": "RQ6", "billed_calls": 0,
    "channel": "benchmark_mujoco / peg-insert-side-v3 (privileged, --planner rule)",
    "design": {
        "pairs": 4, "arms": ["full", "wo_episodic_memory"], "episodes": 16,
        "near_transfer_pairs": ["p1 (layout 0->1)", "p2 (layout 2->3)", "p4 (layout 4->5)"],
        "replication_pair": "p3 (layout 0, episode seed 0->1; the seed is --seed + task_index, "
                            "benchmark_mujoco/cli.py:238, so the pair names the flag)",
        "store_isolation": "one store file per pair per arm. The pre-registration said "
                           "`store_size` grows 'with pair order'; that assumed a shared store, "
                           "and per-pair isolation is the right design — it is what makes a "
                           "pair a pair. The growth reading is therefore within a pair "
                           "(0 at the seed episode's first look, 1 at the read episode's) and "
                           "the store ends each pair at 2 rows. Corrected here rather than "
                           "left as a criterion that could not be read.",
    },
    "criteria": {},
    "rq6_answer": {},
    "limits": [],
}

c1 = {}
for pair_dir in sorted(glob.glob(os.path.join(ROOT, "p*_full"))):
    pid = os.path.basename(pair_dir).replace("_full", "")
    row = {}
    for role in ("seed", "read"):
        eps = sorted(glob.glob(os.path.join(pair_dir, role, "episodes", "*")))
        if not eps:
            continue
        evs = events_of(eps[0])
        retr = [e["payload"] for e in evs if e["type"] == "memory_retrieval"]
        use = [e["payload"] for e in evs if e["type"] == "memory_use"]
        write = [e["payload"] for e in evs if e["type"] == "memory_write"]
        row[role] = {
            "episode_id": os.path.basename(eps[0]),
            "retrieval_records": len(retr),
            "retrievals_with_rows": sum(1 for p in retr if p.get("retrieved")),
            "store_size_first_look": retr[0]["store_size"] if retr else None,
            "store_size_last_look": retr[-1]["store_size"] if retr else None,
            "use_records": len(use),
            "uses_with_a_row": sum(1 for p in use if p.get("retrieved")),
            "judgements": sorted({p.get("judgements", {}).get(
                (p.get("retrieved") or [None])[0], {}).get("value")
                for p in use if p.get("retrieved")} - {None}) if use else [],
            "write_records": len(write),
        }
    c1[pid] = row
report["criteria"]["1_full_arm_read_episodes_retrieve"] = c1

c2 = {}
for pair_dir in sorted(glob.glob(os.path.join(ROOT, "p*_wo_episodic_memory"))):
    pid = os.path.basename(pair_dir).replace("_wo_episodic_memory", "")
    counts = {}
    for role in ("seed", "read"):
        eps = sorted(glob.glob(os.path.join(pair_dir, role, "episodes", "*")))
        if not eps:
            continue
        evs = events_of(eps[0])
        counts[role] = {t: sum(1 for e in evs if e["type"] == t)
                        for t in ("memory_retrieval", "memory_use", "memory_write")}
    store = os.path.join(pair_dir, "store.jsonl")
    counts["store_rows_at_end"] = sum(1 for l in open(store, encoding="utf-8") if l.strip()) \
        if os.path.exists(store) else None
    c2[pid] = counts
report["criteria"]["2_control_arm_memory_is_silent"] = c2

c4 = json.load(open(os.path.join(ROOT, "e3_criterion4.json"), encoding="utf-8"))
report["criteria"]["4_offline_rederivation_equals_online"] = c4["summary"]
report["criteria"]["4_method"] = c4["method"]

# criterion 3 was 16/16 in the batch report; carry it with its denominator
c3 = json.load(open(os.path.join(ROOT, "e3_report.json"), encoding="utf-8"))["criteria"][
    "3_every_episode_files_the_episodic_block"]
report["criteria"]["3_every_episode_files_the_episodic_block"] = {
    "episodes": len(c3), "with_block": sum(1 for v in c3.values() if v),
    "missing": [k for k, v in c3.items() if not v]}

reads = [c1[p]["read"] for p in c1 if "read" in c1[p]]
seeds = [c1[p]["seed"] for p in c1 if "seed" in c1[p]]
report["rq6_answer"] = {
    "question": "does the seed/readout pair produce measurable reuse on this channel?",
    "answer": "Retrieval and use: YES, and they are measurable. Benefit: NOT SHOWN.",
    "read_episodes": len(reads),
    "read_episodes_that_retrieved_a_row": sum(1 for r in reads
                                              if r["retrievals_with_rows"] > 0),
    "read_episodes_that_used_a_row": sum(1 for r in reads if r["uses_with_a_row"] > 0),
    "store_size_at_seed_episode_looks": sorted({r["store_size_first_look"] for r in seeds}),
    "store_size_at_read_episode_looks": sorted({r["store_size_first_look"] for r in reads}),
    "judgement_values_seen": sorted({v for r in reads for v in r["judgements"]}),
    "control_arm_episodes_with_any_memory_record": sum(
        1 for p in c2.values() for role in ("seed", "read")
        if p.get(role) and sum(p[role].values()) > 0),
    "control_arm_episodes": sum(1 for p in c2.values() for role in ("seed", "read") if p.get(role)),
}
report["limits"] = [
    "Retrieval, use and the per-round judgement are measured; a *benefit* is not. Every read "
    "episode executed the same two skills the loop would have executed with the memory absent, "
    "so `full` vs `wo_episodic_memory` differs in what the page said and not in what the loop "
    "did. This is the desktop MEM-1 limit reached by a different route "
    "(`followed_where_control_chose_the_same 0.4286`, `outcome_improvement 0/4`), and it is why "
    "SPEC §3 RQ6 refuses to let the desktop conclusion transfer automatically.",
    "The finish round's judgement is `unknown` with `unmeasured: ['the round produced no "
    "executed skill to compare against']` — the mechanism declining to judge a round it cannot "
    "compare. That is the correct shape, and it is also why only 2 of 3 rounds per read episode "
    "carry a True judgement.",
    "This channel ships one task vocabulary (`TASKS` holds `peg-insert-side-v3` alone) and 35 "
    "layouts, so 'a later similar task' is expressed as a different layout. That is a narrower "
    "transfer claim than MEM-1's 4 distinct task pairs.",
    "K=4 pairs, n=4: an existence proof of the mechanism on this channel, not a rate.",
    "Zero spend throughout: `--planner rule --perceive privileged` consults no model. Nothing "
    "here says anything about memory under a model decision source.",
]

path = os.path.join(ROOT, "e3_rq6_answer.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")

print("RQ6:", report["rq6_answer"]["answer"])
print(json.dumps(report["rq6_answer"], ensure_ascii=False, indent=1))
print()
print("criterion 1 (full arm):")
for pid, row in c1.items():
    print(f"  {pid}: seed store={row['seed']['store_size_first_look']}->"
          f"{row['seed']['store_size_last_look']} retrieved={row['seed']['retrievals_with_rows']}"
          f" | read store={row['read']['store_size_first_look']}->"
          f"{row['read']['store_size_last_look']} retrieved={row['read']['retrievals_with_rows']}"
          f" used={row['read']['uses_with_a_row']} judgements={row['read']['judgements']}")
print()
print("criterion 2 (control):", json.dumps(c2, ensure_ascii=False))
print("criterion 3:", json.dumps(report["criteria"][
    "3_every_episode_files_the_episodic_block"], ensure_ascii=False))
print("criterion 4:", json.dumps(report["criteria"][
    "4_offline_rederivation_equals_online"], ensure_ascii=False))
print("artefact:", path)
