"""P3-a/b dev probe: build §5.4 experiences out of P2's archived episodes, then retrieve.

Read-only against the repository; writes only under /tmp. Nothing here feeds a prompt, a
DecisionContext, or a score: it is the check that `memory/` can turn real records into real rows.

Questions it answers, in order:
* can an episode's own artifacts produce an `EpisodicExperience` whose seven §5.4 fields are all
  populated from disk, with no invented text?
* does the store survive a round trip (write, load, refuse a duplicate, refuse a foreign schema)?
* does retrieval match on the fields it claims to match on, order deterministically, and stay
  silent about a predicate it has not measured while flagging one it has refuted?
"""
import json
import os
import sys

from embodied_agent.episodic import ExperienceStore, experience_from_episode, read_episode_dir
from embodied_agent.episodic.retrieval import (RELEVANCE_WEIGHTS, contradicted, indexed_terms,
                                             mark_used, query_from, render_rows, retrieve, score)

RUN = sys.argv[1] if len(sys.argv) > 1 else "/tmp/p2e_full/runs/long_horizon_rule_full_20260921_170156"
OUT = "/tmp/p3a_probe"
os.makedirs(OUT, exist_ok=True)


def say(row):
    print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)


episodes = sorted(os.listdir(os.path.join(RUN, "episodes")))
store = ExperienceStore(os.path.join(OUT, "experiences.jsonl"))
rows = []
for name in episodes:
    summary, events = read_episode_dir(os.path.join(RUN, "episodes", name))
    exp = experience_from_episode(summary, events)
    store.append(exp)
    rows.append(exp)
    say({"episode": name, "outcome": exp.outcome.value, "official": exp.official_success,
         "steps": len(exp.steps), "state_pattern": len(exp.state_pattern),
         "counter_examples": len(exp.counter_examples), "kinds": exp.object_kinds,
         "applicability": exp.applicability, "reason": exp.failure_reason[:90]})

say({"round_trip": ExperienceStore.load(store.path).ids() == store.ids(),
     "fingerprint": store.fingerprint()[:16], "n": len(store)})
try:
    store.append(rows[0])
    say({"duplicate_refused": "NO -- accepted twice", "n_after": len(store)})
except ValueError as exc:
    say({"duplicate_refused": str(exc)[:80]})
bad_path = os.path.join(OUT, "foreign_generation.jsonl")
with open(bad_path, "w", encoding="utf-8") as fh:
    payload = json.loads(json.dumps(rows[0].model_dump(mode="json")))
    payload["schema_version"] = "3"
    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
try:
    ExperienceStore.load(bad_path)
    say({"v01_schema_refused": "NO -- a foreign generation parsed"})
except ValueError as exc:
    say({"v01_schema_refused": str(exc)[:90]})
try:
    ExperienceStore(None, [rows[0].model_copy(update={"run_ref": ""})])
    say({"no_provenance_refused": "NO"})
except ValueError as exc:
    say({"no_provenance_refused": str(exc)[:60]})
with open(bad_path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(rows[0].model_dump(mode="json"), ensure_ascii=False) + "\n")
    fh.write(json.dumps(rows[1].model_dump(mode="json"), ensure_ascii=False) + "\n")
say({"load_preserves_line_order": ExperienceStore.load(bad_path).ids() ==
     [rows[0].experience_id, rows[1].experience_id]})

def definite(row):
    return [p for p in row.state_pattern if p.endswith("=true") or p.endswith("=false")]


target = max(rows, key=lambda r: (len(definite(r)), r.experience_id))
say({"target": target.episode_id, "state_pattern": target.state_pattern,
     "pattern_from": target.provenance["state_pattern_from"],
     "pattern_ref": target.provenance["state_pattern_ref"]})
query = query_from(task_kind=target.task_kind,
                   regions=[p.rsplit(":", 1)[-1] for p in target.state_pattern],
                   kinds=target.object_kinds,
                   predicates=[p.split("=")[0] for p in target.state_pattern])
hits = retrieve(query, store.all(), limit=4, round_index=1)
say({"query_terms": len(query), "weights": RELEVANCE_WEIGHTS,
     "hits": [{"exp": h.experience_id, "rel": h.relevance, "matched": h.match_terms[:4],
               "recall_id": h.recall_id,
               "contradicted": h.contradicted_current_state} for h in hits]})
say({"order_is_deterministic": [(h.experience_id, h.relevance) for h in hits] ==
     [(h.experience_id, h.relevance) for h in retrieve(query, list(reversed(store.all())),
                                                       limit=4, round_index=1)],
     "ids_are_reusable_across_calls": [h.recall_id for h in hits] ==
     [h.recall_id for h in retrieve(query, store.all(), limit=4, round_index=1)]})

measured = {p.split("=")[0]: ("false" if p.endswith("=true") else "true")
            for p in definite(target)[:2]}
say({"measured_that_should_clash": measured,
     "contradictions_against_an_inverted_world": contradicted(target, measured)})
say({"contradictions_against_silence": contradicted(target, {})})
say({"contradictions_against_an_unknown_measurement":
     contradicted(target, {definite(target)[0].split("=")[0]: "unknown"} if definite(target) else {})})
say({"contradictions_against_an_agreeing_world":
     contradicted(target, {p.split("=")[0]: p.rsplit("=", 1)[1] for p in definite(target)})})
say({"index_of_one_row": {k: sorted(v)[:4] for k, v in indexed_terms(target).items()}})
say({"score_of_self": score(target, query)})
say({"nothing_relevant_is_an_empty_answer":
     retrieve(query_from(task_kind="no_such_set"), store.all(), limit=4, round_index=1)})
for line in render_rows(hits, {r.experience_id: r for r in store.all()}):
    say({"rendered": line[:150]})

decisions = [{"skill": target.steps[0].skill, "args": dict(target.steps[0].args)}] \
    if target.steps else []
mark_used(hits, {r.experience_id: r for r in store.all()}, decisions)
say({"used_by_model": [{h.experience_id: [str(h.used_by_model.value), h.used_by_model.evidence_refs[:2]]}
                       for h in hits]})
say({"used_when_the_round_decided_nothing":
     (lambda fresh: (mark_used(fresh, {r.experience_id: r for r in store.all()}, []),
                     [str(f.used_by_model.value) for f in fresh])[-1])(
         retrieve(query, store.all(), limit=2, round_index=2))})
reextracted = experience_from_episode(*read_episode_dir(
    os.path.join(RUN, "episodes", episodes[0])))
say({"re_extract_is_the_same_row": reextracted.experience_id == rows[0].experience_id})
try:
    store.append(reextracted)
    say({"same_episode_re_stored": "NO -- accepted a second row for one episode"})
except ValueError as exc:
    say({"same_episode_re_stored": "refused: " + str(exc)[:50]})
print("PROBE DONE", flush=True)
