"""The arithmetic behind the §5.4 render caps, on a *real* stored row. Zero spend; reads /tmp.

`RECALL_BUDGET_CHARS`, `STEP_CAP`, `MATCH_TERM_CAP` and `CLASH_CAP` were set from measurement, so
the measurement has to be re-runnable and has to say which cap is doing the work. Takes a probe root
(the directory `work/p3c_render_probe.py` prints) holding `store.jsonl` from a seed episode and
`read/events.jsonl` from a read episode against it.

For each of the three lists a recall line carries, it prints the clause the shipped renderer writes
and the clause an uncapped renderer would have written — the delta is what that cap buys on a memory
the real extractor produced, not on a fixture. Nothing here is a test and nothing feeds a prompt.
"""
from __future__ import annotations

import json
import os
import sys

from embodied_agent.core.v02 import EpisodicExperience
from embodied_agent.episodic.retrieval import (CLASH_CAP, MATCH_TERM_CAP, STEP_CAP, _clash_clause,
                                               _step_text, indexed_terms, query_from, render_rows,
                                               retrieve)
from embodied_agent.planning.working_memory import RECALL_BUDGET_CHARS

ROOT = sys.argv[1]

store: dict[str, EpisodicExperience] = {}
with open(os.path.join(ROOT, "store.jsonl"), encoding="utf-8") as fh:
    for line in fh:
        record = EpisodicExperience.model_validate(json.loads(line))
        store[record.experience_id] = record

# Contradictions are world-dependent — they are what the verifier measured at that round — so take
# the worst round any episode saw for this row rather than an average one.
worst: dict[str, list[str]] = {}
for event in (json.loads(l) for l in open(os.path.join(ROOT, "read", "events.jsonl"),
                                          encoding="utf-8") if l.strip()):
    if event["type"] != "memory_retrieval":
        continue
    for row in event["payload"]["retrieved"]:
        clashes = row["provenance"].get("contradictions") or []
        if len(clashes) > len(worst.get(row["experience_id"], [])):
            worst[row["experience_id"]] = clashes

print(f"RECALL_BUDGET_CHARS={RECALL_BUDGET_CHARS}  per-row room at DEFAULT_LIMIT=3 is "
      f"{RECALL_BUDGET_CHARS // 3}")
print(f"caps: STEP_CAP={STEP_CAP} MATCH_TERM_CAP={MATCH_TERM_CAP} CLASH_CAP={CLASH_CAP}\n")

for eid, record in sorted(store.items()):
    index = indexed_terms(record)
    # `indexed_terms` returns *prefixed* terms and `query_from` prefixes what it is given, so the
    # field values have to come off the index with their prefix stripped — otherwise this probe's
    # own query double-prefixes and reports a match set no real round ever saw.
    query = query_from(task_kind=record.task_kind,
                       regions=[t.split(":", 1)[1] for t in index["region"]],
                       kinds=[t.split(":", 1)[1] for t in index["kind"]],
                       predicates=[t.split(":", 1)[1] for t in index["predicate"]],
                       failure_codes=[t.split(":", 1)[1] for t in index["failure_code"]])
    clashes = worst.get(eid, [])
    # Re-read the worst round's measurements out of the sentences the guard wrote for it, so the
    # line printed below is the line that round rendered, not a hand-made approximation of it.
    measured = {message.partition(": ")[0]: message.rpartition(" ")[2].strip()
                for message in clashes}
    hits = retrieve(query, [record], measured=measured, limit=3, round_index=1)
    terms, steps = list(hits[0].match_terms), list(record.steps)
    line = render_rows(hits, {eid: record})[0]
    print(f"--- {eid}: {len(terms)} match term(s), {len(steps)} step(s), {len(clashes)} "
          f"refuted claim(s); line as shipped = {len(line)} chars")
    if len(terms) > MATCH_TERM_CAP:
        # The same line with the whole match list spelled out, which is what `render_rows` wrote
        # before the cap: swapping only the clause keeps every other section at its shipped shape,
        # so the number printed is the cap's price and nothing else's.
        widened = line.replace(f", matched {len(terms)} term(s): "
                               f"{', '.join(terms[:MATCH_TERM_CAP])}, +{len(terms) - MATCH_TERM_CAP} "
                               f"more — all of them in match_terms)",
                               f", matched {len(terms)} term(s): {', '.join(terms)}")
        print(f"    with all {len(terms)} terms named: {len(widened)} chars "
              f"(+{len(widened) - len(line)})")

    capped_terms = ", ".join(terms[:MATCH_TERM_CAP])
    extra_terms = len(terms) - MATCH_TERM_CAP
    print(f"  match_terms: capped clause {len(capped_terms)} chars naming "
          f"{min(len(terms), MATCH_TERM_CAP)} of {len(terms)}; "
          f"all of them would cost {len(', '.join(terms))} chars "
          f"(+{len(', '.join(terms)) - len(capped_terms)})")

    step_texts = [_step_text({"skill": s.skill, "args": dict(s.args or {})}) for s in steps]
    capped_steps = " | ".join(step_texts[:STEP_CAP])
    print(f"  steps:       capped clause {len(capped_steps)} chars listing "
          f"{min(len(steps), STEP_CAP)} of {len(steps)}; all of them would cost "
          f"{len(' | '.join(step_texts))} chars "
          f"(+{len(' | '.join(step_texts)) - len(capped_steps)}), "
          f"cap binds: {len(steps) > STEP_CAP}")

    compacted = [_clash_clause(c) for c in clashes[:CLASH_CAP]]
    print(f"  clashes:     capped clause {len(' | '.join(compacted))} chars listing "
          f"{min(len(clashes), CLASH_CAP)} of {len(clashes)}; all of them in the guard's own "
          f"sentence would cost {len(' | '.join(clashes))} chars "
          f"(+{len(' | '.join(clashes)) - len(' | '.join(compacted))}), "
          f"cap binds: {len(clashes) > CLASH_CAP}")
    if clashes:
        print(f"    sentence : {clashes[0]}")
        print(f"    compacted: {compacted[0]}")
        widened = line.replace(" | ".join(compacted), " | ".join(clashes))
        print(f"    line with the guard's full sentences: {len(widened)} chars "
              f"(+{len(widened) - len(line)})")
