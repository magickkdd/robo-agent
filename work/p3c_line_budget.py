"""How long is one rendered recall line when the guard is actually firing? Zero spend.

Reads the artifacts a previous probe run left on disk (`store.jsonl` plus the read episode's
`memory_retrieval` records) so every number below comes from a row the real extractor wrote and a
contradiction set the real verifier measured, not from a hand-made fixture.
"""
from __future__ import annotations

import json
import os
import sys

from embodied_agent.core.v02 import EpisodicExperience, RecalledExperience
from embodied_agent.episodic.retrieval import render_rows

ROOT = sys.argv[1]

store = {}
with open(os.path.join(ROOT, "store.jsonl"), encoding="utf-8") as fh:
    for line in fh:
        record = EpisodicExperience.model_validate(json.loads(line))
        store[record.experience_id] = record

path = os.path.join(ROOT, "read", "events.jsonl")
events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

print(f"store rows: {sorted(store)}")
plain = render_rows([RecalledExperience(experience_id=eid, relevance=3.0,
                                        match_terms=["task_kind:dev"],
                                        injected_into_round=0,
                                        provenance={"steps": r.model_dump()["steps"],
                                                    "run_ref": r.run_ref})
                     for eid, r in sorted(store.items())], store)
for line in plain:
    print(f"  no-clash line len={len(line)}")

buckets: dict[int, list[int]] = {}
for event in events:
    if event["type"] != "memory_retrieval":
        continue
    rows = [RecalledExperience.model_validate(d) for d in event["payload"]["retrieved"]]
    if not rows:
        continue
    lines = render_rows(rows, {r.experience_id: store[r.experience_id] for r in rows})
    for row, line in zip(rows, lines):
        n = len(row.provenance.get("contradictions") or [])
        buckets.setdefault(n, []).append(len(line))
        clashes = row.provenance.get("contradictions") or []
        if clashes:
            print(f"round {event['payload']['round_index']}: clashes={n} "
                  f"line_len={len(line)} clash_chars={sum(len(c) for c in clashes)} "
                  f"one_clash_example_len={len(clashes[0])}")

print("\nline length by number of clashes:")
for n in sorted(buckets):
    print(f"  {n} clash(es): min={min(buckets[n])} max={max(buckets[n])} n={len(buckets[n])}")

print("\nfull text of each distinct line:")
seen = set()
for event in events:
    if event["type"] != "memory_retrieval":
        continue
    rows = [RecalledExperience.model_validate(d) for d in event["payload"]["retrieved"]]
    for row in rows:
        line = render_rows([row], {row.experience_id: store[row.experience_id]})[0]
        if line in seen:
            continue
        seen.add(line)
        print(f"--- len={len(line)} clashes={len(row.provenance.get('contradictions') or [])}")
        print(line)
