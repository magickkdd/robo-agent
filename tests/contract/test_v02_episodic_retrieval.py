"""SPEC-v0.2 §5.4 contract tests for retrieval: how a memory is chosen, and what it may not do.

§5.4's second sentence is the whole design constraint — 检索结果作为模型参考，不直接改写
WorldState — and it splits into four testable claims:

1. **Relevance is a declared formula over recorded fields.** The weights are published as a table,
   every indexed field has one, and a score is recomputable from the matched terms. "This memory is
   relevant" has to be an arithmetic claim a reader can check, not a similarity vibe, because §11's
   `retrieval relevance` row is defined *by* this table.
2. **Nothing relevant is an empty list.** A zero-scoring row is not returned and annotated; it is
   absent. Otherwise "retrieved something and ignored it" and "had nothing to say" are the same
   record, and RQ3 cannot be answered.
3. **Order is deterministic.** Relevance, then the stored id, then a derived recall id — no
   `hash()`, no draw. P2 measured what a hash-derived key does to reproducibility, and a memory
   that reorders its own ties makes the same episode prompt two different things in two processes.
4. **The guard measures or says nothing, and never hides what it flagged.** An unmeasured predicate
   does not refute a memory (that would talk the agent out of a correct experience — the other half
   of negative transfer), and a refuted one is still rendered, labelled, because an agent that
   cannot see "your memory says X, the world says not-X" cannot learn which of the two was the
   problem.

Plus the rule that makes this a research module rather than a lookup: `used_by_model` is judged from
the executed action, never from the explanation text (§12.3: 以实际动作和最终状态为准), and a round
that decided nothing is recorded as `unknown` rather than `False`.

And a constraint §5.4 does not state but P3-c measured: a recall has to fit the page to be a recall.
The renderer's lists are capped (`STEP_CAP`, `MATCH_TERM_CAP`, `CLASH_CAP`), the remainders are
counted out loud, and the whole line stays inside the per-row share of `RECALL_BUDGET_CHARS` — the
first version did not, and the budget silently dropped every memory on every round of a `full` run.

The rows here are hand-built `EpisodicExperience`s, not episodes: this file is about which row is
chosen and what is asserted about it, and `test_v02_episodic_experience.py` already pins where a row
may come from.
"""
from __future__ import annotations

import pytest

from embodied_agent.core.v02 import (ABLATION_CONDITIONS, EVENT_MODULE, V02_EVENT_TYPES,
                                     EpisodicExperience, ExperienceStep, OutcomeLabel, Record,
                                     RecalledExperience, Source, WorkingMemoryState)
from embodied_agent.episodic.retrieval import (CLASH_CAP, DEFAULT_LIMIT, MATCH_TERM_CAP,
                                               MIN_RELEVANCE, RELEVANCE_WEIGHTS, STEP_CAP,
                                               _clash_clause, contradicted, indexed_terms,
                                               mark_used, query_from, render_rows, retrieve, score)
from embodied_agent.planning.working_memory import RECALL_BUDGET_CHARS


def _row(experience_id="exp_a", *, episode="lh_c1", task_kind="long_horizon",
         kinds=("cube:blue", "cylinder:red"), pattern=("placed:obj_blue_1:tray_left=true",
                                                       "placed:obj_red_2:tray_right=false"),
         steps=(("pick", "obj_blue_1", None), ("place", "obj_blue_1", "tray_left")),
         outcome=OutcomeLabel.success, codes=(), official=True, run_ref="/tmp/frozen/episodes/x"):
    return EpisodicExperience(
        experience_id=experience_id, episode_id=episode, task_context=f"{task_kind}/{episode}",
        task_kind=task_kind, scene_id="lh_c1", object_kinds=list(kinds),
        state_pattern=list(pattern),
        steps=[ExperienceStep(skill=s, args={k: v for k, v in (("object_id", o), ("target_id", t))
                                             if v}, round_index=i + 1, effect="completed")
               for i, (s, o, t) in enumerate(steps)],
        outcome=outcome, official_success=official,
        failure_reason="; ".join(codes),
        applicability=[f"task_kind:{task_kind}", "regions:tray_left|tray_right",
                       f"kinds:{'|'.join(kinds)}", "objects:2"],
        transfer_caution="a single layout is not a rule", run_ref=run_ref,
        source=Source.privileged)


QUERY = query_from(task_kind="long_horizon", regions=["tray_left"], kinds=["cube:blue"],
                   predicates=["placed:obj_blue_1:tray_left"], failure_codes=["GRASP_MISS"])


# ------------------------------------------------------------------- relevance is arithmetic ----

def test_every_indexed_field_has_a_published_weight_and_vice_versa():
    """The table *is* §11's `retrieval relevance` definition: a field matched without a weight, or
    a weight over nothing, would change the metric while the metric's name stayed the same."""
    assert set(RELEVANCE_WEIGHTS) == set(indexed_terms(_row()))
    assert set(RELEVANCE_WEIGHTS) == {"task_kind", "region", "kind", "predicate", "failure_code"}
    assert all(w > 0 for w in RELEVANCE_WEIGHTS.values())


def test_a_score_is_the_sum_of_the_declared_weights_over_its_matched_terms():
    row = _row()
    total, matched = score(row, QUERY)
    index = indexed_terms(row)
    recomputed = sum(RELEVANCE_WEIGHTS[field] for field, values in index.items()
                     for term in matched if term in values)
    assert total == recomputed
    assert len(matched) == len(set(matched))
    assert all(term in QUERY for term in matched), matched


def test_a_term_cannot_cross_from_one_field_to_another_by_spelling():
    """`tray_left` as a region and `placed:...:tray_left` as a predicate are different terms; a
    substring match would make a score depend on how an id happens to be spelled."""
    as_region = query_from(regions=["tray_left"])
    as_predicate = query_from(predicates=["tray_left"])
    row = _row()
    assert score(row, as_region)[0] > 0
    assert score(row, as_predicate)[0] == 0.0
    kinds_only = query_from(kinds=["cube:blue"])
    assert score(row, kinds_only)[0] == RELEVANCE_WEIGHTS["kind"]


def test_a_failure_code_is_indexed_from_the_verdict_the_environment_returned():
    row = _row(steps=(("pick", "obj_blue_1", None),), codes=("GRASP_MISS",))
    index = indexed_terms(row)
    assert index["failure_code"] == set()
    row_with_code = _row(steps=(("pick", "obj_blue_1", None),))
    row_with_code.steps[0].effect = "failed:GRASP_MISS"
    assert indexed_terms(row_with_code)["failure_code"] == {"failure_code:GRASP_MISS"}


# ------------------------------------------------------------------------ nothing to recall ----

def test_a_row_that_matches_nothing_is_absent_rather_than_present_and_ignored():
    assert retrieve(query_from(task_kind="another_set"), [_row()], limit=3,
                    round_index=1) == []
    assert retrieve(set(), [_row()], limit=3, round_index=1) == []


def test_the_relevance_floor_is_the_published_one():
    one_weight = RELEVANCE_WEIGHTS["predicate"]
    assert MIN_RELEVANCE == 1.0
    assert one_weight >= MIN_RELEVANCE
    below = _row(pattern=["placed:obj_zzz:tray_zzz=false"])
    assert score(below, query_from(predicates=["placed:obj_zzz:tray_zzz"]))[0] == one_weight
    assert retrieve(query_from(task_kind="long_horizon"), [_row()], limit=3,
                    round_index=1)[0].relevance == RELEVANCE_WEIGHTS["task_kind"]


def test_the_limit_keeps_the_most_relevant_rows_not_the_first_ones_read():
    rows = [_row(experience_id="exp_low", kinds=("cube:blue",),
                 pattern=("placed:obj_blue_1:tray_left=true",)),
            _row(experience_id="exp_high", kinds=("cube:blue", "cylinder:red"),
                 pattern=("placed:obj_blue_1:tray_left=true",
                          "placed:obj_red_2:tray_right=false"))]
    hits = retrieve(set().union(query_from(kinds=["cube:blue", "cylinder:red"]), QUERY),
                    rows, limit=1, round_index=4)
    assert [h.experience_id for h in hits] == ["exp_high"]
    assert hits[0].injected_into_round == 4
    assert DEFAULT_LIMIT == 3


# -------------------------------------------------------------------------- a stable ordering ----

def test_order_is_relevance_then_id_and_does_not_depend_on_read_order():
    rows = [_row(experience_id=f"exp_{i}", kinds=("cube:blue", "cylinder:red")) for i in range(4)]
    rows.append(_row(experience_id="exp_aaa", kinds=("cube:blue",)))
    query = query_from(kinds=["cube:blue", "cylinder:red"], task_kind="long_horizon")
    forward = [(h.relevance, h.experience_id) for h in retrieve(query, rows, limit=99,
                                                                round_index=1)]
    backward = [(h.relevance, h.experience_id) for h in retrieve(query, list(reversed(rows)),
                                                                limit=99, round_index=1)]
    assert forward == sorted(forward, key=lambda r: (-r[0], r[1]))
    assert forward == backward


def test_a_recall_id_is_derived_from_the_row_and_the_round_it_was_shown_in():
    """`RecalledExperience.recall_id` has a random default, and these ids appear in the rendered
    lines a model is prompted with: a drawn id would make one episode's prompt differ from another
    run of the same store over the same memory."""
    first = retrieve(QUERY, [_row()], limit=1, round_index=7)[0]
    again = retrieve(QUERY, [_row()], limit=1, round_index=7)[0]
    assert first.recall_id == again.recall_id == f"rec_exp_a_round7"
    assert retrieve(QUERY, [_row()], limit=1, round_index=8)[0].recall_id != first.recall_id
    assert retrieve(QUERY, [_row()], limit=1)[0].recall_id == "rec_exp_a"


def test_a_recall_row_is_the_frozen_record_and_carries_its_own_provenance():
    hit = retrieve(QUERY, [_row()], limit=1, round_index=2)[0]
    assert isinstance(hit, RecalledExperience) and isinstance(hit, Record)
    assert hit.provenance["run_ref"] == "/tmp/frozen/episodes/x"
    assert hit.provenance["outcome"] == "success"
    assert "a single layout is not a rule" in hit.provenance["transfer_caution"]
    assert hit.match_terms and hit.used_by_model.value == "unknown"


# --------------------------------------------------------------------- the negative-transfer guard

@pytest.mark.parametrize("measured, expect_clash", [
    ({}, False),
    (None, False),
    ({"placed:obj_blue_1:tray_left": "unknown"}, False),
    ({"placed:obj_blue_1:tray_left": "true"}, False),
    ({"placed:obj_blue_1:tray_left": "false"}, True),
    ({"placed:obj_blue_1:tray_left": "FALSE"}, True),
    ({"placed:obj_red_2:tray_right": "true"}, True),
    ({"placed:obj_never_mentioned:tray_x": "true"}, False),
])
def test_a_memory_is_refuted_only_by_a_measurement_that_opposes_it(measured, expect_clash):
    """Silence is not refutation. Reading "we did not look" as "your memory was wrong" would talk
    the agent out of correct experiences — the half of negative transfer this guard must not
    cause."""
    clashes = contradicted(_row(), measured)
    assert bool(clashes) is expect_clash
    for line in clashes:
        assert "recalled" in line and "measures" in line


def test_a_refuted_row_is_still_retrieved_and_still_shown():
    row = _row()
    hits = retrieve(QUERY, [row], measured={"placed:obj_blue_1:tray_left": "false"}, limit=3,
                    round_index=1)
    assert len(hits) == 1
    assert hits[0].contradicted_current_state is True
    assert hits[0].provenance["contradictions"]
    lines = render_rows(hits, {row.experience_id: row})
    assert any("CONTRADICTED" in line for line in lines)
    assert any("recall[rec_exp_a_round1]" in line for line in lines)


def test_an_unmeasured_memory_is_retrieved_without_a_contradiction_flag():
    hits = retrieve(QUERY, [_row()], measured={"placed:obj_blue_1:tray_left": "true"},
                    limit=3, round_index=1)
    assert hits[0].contradicted_current_state is False
    assert hits[0].provenance["contradictions"] == []


# ------------------------------------------------------------------------------ what counts as used

def _decision(skill="pick", obj="obj_blue_1", target=None, rationale=""):
    return {"skill": skill, "args": {"object_id": obj, "target_id": target},
            "rationale": rationale}


def test_a_recall_counts_as_used_only_when_the_action_repeats_a_step_of_that_row():
    row = _row()
    hits = retrieve(QUERY, [row], limit=1, round_index=1)
    mark_used(hits, {row.experience_id: row}, [_decision()])
    assert hits[0].used_by_model.value is True
    assert hits[0].used_by_model.evidence_refs == ["pick obj_blue_1 -> "]


def test_saying_you_followed_a_memory_is_not_evidence_that_you_did():
    """§12.3 in one assertion: 以实际动作和最终状态为准."""
    row = _row()
    hits = retrieve(QUERY, [row], limit=1, round_index=1)
    mark_used(hits, {row.experience_id: row},
              [_decision(skill="place", obj="obj_green_9", target="tray_middle",
                         rationale="following my earlier experience of this tray")])
    assert hits[0].used_by_model.value is False
    assert hits[0].used_by_model.evidence_refs == []


def test_a_round_that_decided_nothing_leaves_the_question_open_rather_than_answering_no():
    row = _row()
    hits = retrieve(QUERY, [row], limit=1, round_index=1)
    mark_used(hits, {row.experience_id: row}, [])
    assert hits[0].used_by_model.value == "unknown"
    assert hits[0].used_by_model.unmeasured


def test_a_recall_whose_row_is_no_longer_stored_cannot_be_judged():
    """The store is append-only, so this should not happen — and when it does (a store rebuilt from
    a different seed file) the honest answer is that no step of that row can be compared."""
    row = _row()
    hits = retrieve(QUERY, [row], limit=1, round_index=1)
    mark_used(hits, {}, [_decision()])
    assert hits[0].used_by_model.value == "unknown"
    assert "not in the store" in hits[0].used_by_model.unmeasured[0]


def test_only_the_rows_a_round_was_shown_are_marked():
    first, second = _row(experience_id="exp_a"), _row(experience_id="exp_b", episode="lh_c2")
    shown = retrieve(QUERY, [first, second], limit=1, round_index=1)
    other = retrieve(QUERY, [first, second], limit=1, round_index=2)
    mark_used(shown, {r.experience_id: r for r in (first, second)}, [_decision()])
    assert any(h.used_by_model.value is True for h in shown)
    assert all(h.used_by_model.value == "unknown" for h in other if h not in shown)


# ------------------------------------------------------------------------- what a recall may not do

def test_a_recall_is_text_a_model_reads_and_touches_nothing_else():
    """§5.4: 检索结果作为模型参考，不直接改写 WorldState. `WorkingMemoryState.recalled` is the one
    place a retrieval result may live, and writing there must not move a commitment, a subgoal
    status or a plan version."""
    row = _row()
    hits = retrieve(QUERY, [row], limit=3, round_index=1)
    mark_used(hits, {row.experience_id: row}, [_decision()])
    state = WorkingMemoryState(round_index=5, plan_version=2, current_subgoal_id="sub_1",
                               subgoal_status={"placed:obj_blue_1:tray_left": "done"},
                               commitments=[])
    before = state.model_dump(exclude={"recalled"})
    state.recalled = hits
    assert state.model_dump(exclude={"recalled"}) == before
    assert [r.experience_id for r in state.recalled] == ["exp_a"]


def test_the_hidden_verdict_stays_out_of_the_lines_a_model_is_shown():
    """§3.2: the third-layer verdict may be stored for retrieval-time context, and it must not
    reach a prompt — so the rendered reference names the outcome the loop saw, never the key's
    answer."""
    row = _row(official=False)
    lines = render_rows(retrieve(QUERY, [row], limit=1, round_index=1),
                        {row.experience_id: row})
    joined = "\n".join(lines)
    assert "official_success" not in joined and "False" not in joined
    assert "outcome=success" in joined and "relevance=" in joined
    assert row.official_success is False


def test_a_recall_pointing_at_no_row_says_so_instead_of_quietly_disappearing():
    hits = retrieve(QUERY, [_row()], limit=1, round_index=3)
    lines = render_rows(hits, {})
    assert len(lines) == 1
    assert "not in the store any more" in lines[0]


# --------------------------------------------------------------------- how long a memory may be
#
# §5.4's lines share the working-memory page with P2's debt block, and the first version of
# `render_rows` lost that contest: an uncapped line from a real six-step dev episode cost more than
# the page had, so the budget dropped every memory on every round of a `full` run and RQ3 could not
# be asked of the system that had one. The cap is what this section pins. A line that can never be
# admitted is not a conservative design — it is a switch nobody read.

def _nasty(steps=12, clashes=6, kinds=8):
    """A long episode in a cluttered scene: every list the renderer walks is over its cap."""
    pattern = tuple(f"placed:obj_{i}:tray_{i}" for i in range(1, clashes + 1))
    measured = {p: "false" for p in pattern}
    row = _row(kinds=tuple(f"cube:k{i}" for i in range(kinds)),
               pattern=tuple(f"{p}=true" for p in pattern),
               steps=tuple(("pick", f"obj_{i}", None) for i in range(1, steps + 1)),
               codes=("GRASP_MISS",))
    query = query_from(task_kind=row.task_kind, regions=[f"tray_{i}" for i in
                                                         range(1, clashes + 1)],
                       kinds=list(row.object_kinds), predicates=list(pattern),
                       failure_codes=["GRASP_MISS"])
    hits = retrieve(query, [row], measured=measured, limit=3, round_index=1)
    return row, hits, render_rows(hits, {row.experience_id: row})[0]


def _section(line: str, marker: str) -> str:
    """The one physical line of a rendered recall that starts with `marker`, run-ref suffix removed.

    Splitting on the newline rather than on the sentence's own words: the counted remainder this
    section is checking contains " in ", and a test that cut the line there would be reading its own
    helper's prose instead of the renderer's list."""
    body = [row for row in line.split("\n") if row.startswith(f"  {marker}")][0]
    return body[len(f"  {marker}"):]


def _listed(section: str) -> list[str]:
    return section.rsplit(" in ", 1)[0].split(" | ")


def test_a_listed_sequence_is_cut_at_the_cap_and_the_rest_is_counted_not_hidden():
    row, hits, line = _nasty(steps=12, clashes=1, kinds=1)
    hit = hits[0]
    assert STEP_CAP == 8
    listed = _listed(_section(line, "did: "))
    assert len(listed) == STEP_CAP + 1, listed
    assert listed[-1] == f"(+{12 - STEP_CAP} more, all of them in provenance.steps)"
    # Cutting the line must not cut the record: `episodic.policy` reads provenance.steps, and a
    # renderer that trimmed the payload would change what a memory says depending on who shows it.
    assert len(hit.provenance["steps"]) == 12
    assert len(row.steps) == 12


def test_the_terms_that_chose_a_row_are_named_up_to_a_cap_and_the_line_says_where_the_rest_is():
    _, hits, line = _nasty(steps=1, clashes=1, kinds=8)
    hit = hits[0]
    assert MATCH_TERM_CAP == 3
    assert len(hit.match_terms) > MATCH_TERM_CAP
    head = line.split("\n", 1)[0]
    named, _, counted = head.split("term(s): ", 1)[1].split(" —")[0].partition(", +")
    assert len(named.split(", ")) == MATCH_TERM_CAP
    assert f"matched {len(hit.match_terms)} term(s)" in head
    assert counted == f"{len(hit.match_terms) - MATCH_TERM_CAP} more"
    assert "all of them in match_terms" in head
    # What the line drops is the *duplicate*, never the fact: the row beside it still carries all of
    # them, which is the sentence "all of them in match_terms" is pointing at.
    assert set(hit.match_terms) >= set(named.split(", "))
    assert len(hit.match_terms) == len(set(hit.match_terms))
    assert hit.model_dump(mode="json")["match_terms"] == list(hit.match_terms)


def test_a_row_with_more_refuted_claims_than_the_cap_lists_three_and_names_the_count():
    _, hits, line = _nasty(steps=1, clashes=6, kinds=1)
    clashes = hits[0].provenance["contradictions"]
    assert CLASH_CAP == 3 and len(clashes) > CLASH_CAP
    listed = _section(line, "CONTRADICTED by the current snapshot: ").split(" | ")
    assert len(listed) == CLASH_CAP + 1, listed
    assert listed[-1] == f"(+{len(clashes) - CLASH_CAP} more, in provenance.contradictions)"
    assert len(clashes) == 6


@pytest.mark.parametrize("steps, clashes, kinds", [(12, 1, 1), (1, 1, 8), (1, 6, 1),
                                                   (24, 12, 16), (3, 0, 2)])
def test_no_rendered_line_can_grow_past_the_room_the_recall_pool_gives_each_row(steps, clashes,
                                                                               kinds):
    """The bound the pool is sized by: `RECALL_BUDGET_CHARS` admits `DEFAULT_LIMIT` rows, so a row
    that costs more than the per-row share makes the limit a lie. Measured against the real store in
    `work/p3c_line_budget.py` (572 quiet, 748 refuted on `dev_c1`), which is where 2400 came from —
    not the other way round."""
    assert RECALL_BUDGET_CHARS % DEFAULT_LIMIT == 0
    _, _, line = _nasty(steps=steps, clashes=clashes, kinds=kinds)
    assert len(line) <= RECALL_BUDGET_CHARS // DEFAULT_LIMIT, line


def test_a_refuted_claim_is_rendered_as_the_guards_own_sentence_compacted():
    """`_clash_clause` reads strings this module writes, so the round trip is against the producer:
    the predicate and the two verdicts survive, and anything that is not the guard's shape is
    passed through rather than guessed at — a future edit to `contradicted()` must not make the
    renderer print a fragment of someone else's sentence as a predicate id."""
    row = _row()
    clashes = contradicted(row, {"placed:obj_blue_1:tray_left": "false"})
    assert clashes == ["placed:obj_blue_1:tray_left: recalled true, this snapshot measures false"]
    assert _clash_clause(clashes[0]) == "placed:obj_blue_1:tray_left (true->false)"
    for message in ("", "no separator here", "placed:x:tray_y: ",
                    "placed:x:tray_y: remembered true, this snapshot measures false",
                    "placed:x:tray_y: recalled true, this snapshot measures "):
        assert _clash_clause(message) == message


def test_the_three_memory_record_types_are_attributed_to_this_module_and_gateable_as_one():
    """§9's ablation registry already names `episodic_memory`; the wiring (P3-c) is only honest if
    the records it files are the ones the gate owns, and a row attributed to `None` would be filed
    on every arm including this one's own control."""
    for event_type in ("memory_write", "memory_retrieval", "memory_use"):
        assert event_type in V02_EVENT_TYPES
        assert EVENT_MODULE[event_type] == "episodic_memory"
    assert ABLATION_CONDITIONS["wo_episodic_memory"] == ("episodic_memory",)
    turned_off = [m for m in EVENT_MODULE
                  if EVENT_MODULE[m] == "episodic_memory"]
    assert sorted(turned_off) == ["memory_retrieval", "memory_use", "memory_write"]
