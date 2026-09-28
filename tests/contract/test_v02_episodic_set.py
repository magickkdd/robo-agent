"""SPEC-v0.2 §5.4/§8 contract tests for the `em` pair set (P3-d).

This set exists to be believed about one thing: that a memory changed what a later episode did. That
claim is not checkable inside a single `TaskCase` — the residue is written by one episode and read by
another — so the unit here is a pair, and everything the set publishes is a claim about the relation
between two cases. Which makes four failures available, three of them quiet:

* the pair could be **inconsistent with its own arithmetic**, and `preflight` refuses it — the
  alternative is measuring something nobody pre-registered;
* the pair could be **consistent and still empty**: `retrieved_not_attributable` (p3) and
  `duplicate_agrees` (p4) are that shape on purpose, and they are the control on the two pairs that
  move. An arm that diverged on p4 would be diverging on nothing;
* the set could **restate an expectation the first run falsified** as though it had been right all
  along. p2 and p4 carry a `preregistration` block naming the original claim, the artifact that refuted
  it and why; the tests below require the block to stay, so a later edit that quietly deletes the
  correction is a test failure rather than a lost finding;
* and the whole thing could be **reachable only because relevance lets everything through**: inside a
  batch the query's `task_kind` term is the set name, worth 3.0 against `MIN_RELEVANCE` 1.0, so every
  stored row of the batch matches every query. That is pinned here as a measurement rather than as a
  remark, because a reader who mistakes "recalled" for "relevant" would read p3's page as a decision.

The separation claim is `long_horizon`'s, and it is why these tests may touch the published 47: `em`
resolves by name so the runner can drive it, and is absent from `_REGISTERED_SETS`,
`build_set("all")` and `frozen_tasks_v1.json`.

No episode is run here and no model is called. `preflight` opens a scene and asks the same placement
instrument the runtime asks before it claims a slot is free; everything else is case arithmetic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from embodied_agent.core.v02 import ABLATION_CONDITIONS, EpisodicExperience, ExperienceStep
from embodied_agent.episodic.retrieval import (MIN_RELEVANCE, RELEVANCE_WEIGHTS,
                                               query_from, retrieve)
from embodied_agent.evaluation import episodic_tasks as em
from embodied_agent.evaluation.episodic_runs import EM_ROLES
from embodied_agent.evaluation.tasks import (ALL_SETS, FROZEN_PATH, SET_NAMES, build_case,
                                             build_set, check_frozen, frozen_manifest)

em_root = Path(__file__).resolve().parents[2]

EXPECTATION_FIELDS = {"rows_per_round", "declined_when_measured", "stale_memory_governs",
                      "control_first_pick", "treatment_first_pick_round1",
                      "treatment_first_pick_after_guard", "trajectory_changes"}


@pytest.fixture(scope="module")
def by_id():
    return {c.task_id: c for c in em.build_episodic_set()}


@pytest.fixture(scope="module")
def claims():
    return {c["pair_id"]: c for c in em.pair_claims()}


@pytest.fixture(scope="module")
def on_disk():
    """The frozen file, as any reader of the deliverable sees it (no scene built to get here)."""
    with open(em.EM_FROZEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def _truth(case):
    return {a.entity_id: a.target_id for a in case.eval_spec.assignments}


def _pair(pair_id: str) -> dict:
    return next(p for p in em.EM_PAIRS if p["pair_id"] == pair_id)


# ------------------------------------------------------- the pair is the unit ----
def test_the_set_declares_pairs_not_cases(by_id):
    """Every case is declared exactly once, in a pair, as a writer or a reader.

    A case in two pairs would be read against two different residues while the store on disk held one
    of them; a case in no pair would be built by `build_episodic_set` and measured by nobody.
    """
    assert [c.task_id for c in em.build_episodic_set()] == [cfg["name"] for cfg in em.EM_CONFIGS]
    ids = set(by_id)
    declared = [p["writer"] for p in em.EM_PAIRS] + [p["reader"] for p in em.EM_PAIRS]
    assert len(declared) == len(set(declared)) == len(ids) == 8
    assert set(declared) == ids
    assert len({p["pair_id"] for p in em.EM_PAIRS}) == len(em.EM_PAIRS) == 4
    for pair in em.EM_PAIRS:
        assert pair["writer"] != pair["reader"]
        owed_reader = set(_truth(by_id[pair["reader"]]))
        owed_writer = set(_truth(by_id[pair["writer"]]))
        if pair["condition"] == "retrieved_not_attributable":
            # p3 is the pair whose memory is on the page and about nothing this plan owes; the
            # disjointness is the whole point, and it is asserted here so the pair cannot quietly
            # acquire an object in common and start moving the trajectory for the wrong reason.
            assert owed_writer.isdisjoint(owed_reader), f"{pair['pair_id']}: p3's objects overlap"
        else:
            assert owed_writer <= owed_reader, (
                f"{pair['pair_id']}: the memory acted on an object the reader has no row for, which "
                f"is p3's structure claimed by a pair that predicts something else")


def test_the_roles_run_in_the_order_the_store_requires():
    """Writer before reader, into one store: the reverse order measures an empty memory."""
    assert EM_ROLES == ("writer", "reader")
    assert em.EM_ARMS == (em.EM_TREATMENT_ARM, em.EM_CONTROL_ARM) == ("full", "wo_episodic_memory")
    assert {p["condition"] for p in em.EM_PAIRS} == {
        "promotion", "uncheckable_claims_govern", "retrieved_not_attributable", "duplicate_agrees"}
    for pair in em.EM_PAIRS:
        assert set(pair["expectation"]) == EXPECTATION_FIELDS, pair["pair_id"]


def test_every_pair_records_what_its_expectation_used_to_say():
    """The amendment blocks are part of the deliverable, so they are tested as data.

    p1 and p3 measured what they predicted. p2 and p4 did not, and the file keeps the original claim
    beside the reason it failed — which is what distinguishes a corrected pre-registration from a
    rewritten one.
    """
    amended = {p["pair_id"]: p.get("preregistration") or {} for p in em.EM_PAIRS}
    assert amended["em_p1"] == {} and amended["em_p3"] == {}, (
        "an amendment where nothing was falsified makes the record worthless")
    for pair_id in ("em_p2", "em_p4"):
        block, current = amended[pair_id], _pair(pair_id)["expectation"]
        assert block["filed"] == "before any em episode ran"
        assert block["reason"].strip()
        assert block["falsified_by"], f"{pair_id}: a falsified claim with no artifact is an opinion"
        for citation in block["falsified_by"]:
            if citation.startswith("work/"):
                assert (em_root / citation).exists(), f"{citation} is cited and is not there"
        assert block["expectation"] and all(
            field not in current or value != current[field]
            for field, value in block["expectation"].items()), (
            f"{pair_id}: the recorded claim is the current one, so nothing was amended")
    # p2's original name survives, and it is the reason §11's negative-transfer row is reported as an
    # absence rather than as a zero: the pair was built to test for it.
    assert amended["em_p2"]["condition"] == "negative_transfer"
    assert amended["em_p4"]["expectation"] == {"memory_followed": False}


# ------------------------------------------------------ the claims themselves ----
def test_the_arithmetic_supports_every_frozen_expectation(claims):
    """The pre-registration check, on all four pairs, from case data alone.

    `control_first_pick` is the first row of the reader's own `row_key` order, `treatment_first_pick_*`
    is the remembered order with the guard's decline rule applied, `stale_memory_governs` is whether
    any remembered claim is one this plan can look up. If any of those stopped following from the case
    data, the pair would be measuring something other than what it says it measures.
    """
    for pair_id, claim in claims.items():
        expected = _pair(pair_id)["expectation"]
        control = expected["control_first_pick"] or claim["reader_plan_first"]
        round1 = expected["treatment_first_pick_round1"] or claim["predicted_first_pick_round1"]
        after = (expected["treatment_first_pick_after_guard"]
                 or claim["predicted_first_pick_after_guard"])
        assert control == claim["reader_plan_first"], json.dumps(claim, default=str)
        assert round1 == claim["predicted_first_pick_round1"]
        assert after == claim["predicted_first_pick_after_guard"]
        assert sorted(expected["declined_when_measured"]) == claim["refuted_and_not_owed"]
        assert expected["stale_memory_governs"] == bool(claim["claims_the_reader_can_check"])
        assert expected["trajectory_changes"] == claim["trajectory_will_change"]
        # The two `None` predictions are not a hole in the check: an unnamed expectation defers to the
        # arithmetic, so the field it defers to is where the claim actually lives.
        if expected["control_first_pick"] is None:
            assert expected["trajectory_changes"] is False


def test_only_the_two_moving_pairs_name_a_first_pick(claims):
    """p3 and p4 predict *equality*, so they must not name an object either arm could hit by luck.

    `None` in an expectation means "whatever the computed control value is", and it is the only honest
    way to pre-register a null: a pair that named an id and predicted no divergence would be satisfied
    by a broken memory arm and by a working one alike.
    """
    moving = {p["pair_id"] for p in em.EM_PAIRS if p["expectation"]["trajectory_changes"]}
    assert moving == {"em_p1", "em_p2"}
    for pair_id, pair in ((p["pair_id"], p) for p in em.EM_PAIRS):
        unnamed = pair["expectation"]["control_first_pick"] is None
        assert unnamed == (not pair["expectation"]["trajectory_changes"]), pair_id
        if not unnamed:
            claim = claims[pair_id]
            # The lever is an object the writer never acted on, and it has to sort first in the
            # reader's own order — otherwise the memory would be pushing against a row already ahead.
            assert claim["first_unremembered"] is True, pair_id
            assert pair["expectation"]["control_first_pick"] == claim["reader_plan_first"]
            assert pair["expectation"]["treatment_first_pick_round1"] == \
                claim["remembered_order"][0], pair_id


def test_the_decline_branch_is_unreachable_on_this_store_and_says_so():
    """The set's own negative result, pinned.

    Every writer here is frozen `expected="success"`, so every remembered claim is a `true` assertion,
    and a `true` assertion is refuted only while its row is unsatisfied — which is exactly when the
    guard calls it owed and promotes it. So no pair of this shape reaches `policy.py`'s decline
    branch, which is why that branch is exercised in `test_v02_episodic_arm.py` on a hand-built recall
    instead. The arithmetic is not a constant typed in place of a derivation: hand it a failed writer's
    residue and it returns the decline.
    """
    for claim in em.pair_claims():
        assert claim["decline_branch_reachable"] is False, json.dumps(claim, default=str)
        assert claim["refuted_and_not_owed"] == []
        assert claim["reader_rows_satisfied_at_first_decision"] == []
        # p3 remembers nothing at all, so its emptiness is a different emptiness and is not evidence
        # about the guard; the claim below is only made about a pair with a row on the page.
        if claim["recorded_truths"]:
            assert set(claim["recorded_truths"].values()) == {"true"}
    assert em._declined({"obj_a": "placed:obj_a:tray_left"}, {"obj_a": "false"},
                        {"placed:obj_a:tray_left"}) == ["obj_a"]
    assert em._declined({"obj_a": "placed:obj_a:tray_left"}, {"obj_a": "true"},
                        {"placed:obj_a:tray_left"}) == [], (
        "a confirmed claim was reported as declined — the guard would then be braking a memory that "
        "the world agrees with")


def test_the_two_blindings_are_different_and_both_are_carried(by_id, claims):
    """One gap lasts a round, the other lasts the episode, and they need different fixes.

    (a) *In time*: a freshly derived plan row measures `unknown`, so the reader's first pick is made
    with nothing to check. (b) *In vocabulary*: `arm.py::_memory_query` builds `measured` from this
    episode's own plan rows, so a claim about a region the plan never names is never looked up at any
    round. p1 is the pair where (b) does not apply and (a) costs one round; p2 is built so that (b)
    applies to every claim it carries.
    """
    p1, p2 = claims["em_p1"], claims["em_p2"]
    assert p1["claims_the_reader_can_check"] and not p1["claims_the_reader_cannot_check"]
    assert not p2["claims_the_reader_can_check"] and len(p2["claims_the_reader_cannot_check"]) == 2
    assert p1["predicted_first_pick_after_guard"] == p1["predicted_first_pick_round1"], (
        "p1's own expectation is that waiting does not undo the promotion: a refuted claim the plan "
        "still owes is what policy.py calls positive transfer")
    for pair_id in ("em_p1", "em_p2"):
        pair, claim = _pair(pair_id), claims[pair_id]
        writer_truth, reader_truth = _truth(by_id[pair["writer"]]), _truth(by_id[pair["reader"]])
        agreed = {e: claim["writer_claims"][e] for e in claim["writer_objects"]
                  if reader_truth.get(e) == writer_truth.get(e)}
        assert set(claim["claims_the_reader_can_check"]) == set(agreed), (
            f"{pair_id}: what the guard can look up is not the objects the two cases place alike — "
            f"{claim['claims_the_reader_can_check']} vs {agreed}")
        assert (claim["claims_the_reader_can_check"] != {}) == \
            pair["expectation"]["stale_memory_governs"]


@pytest.mark.parametrize("task_id", [cfg["name"] for cfg in em.EM_CONFIGS])
def test_every_case_is_speakable_physically_open_and_consistent(task_id):
    """The three refusals `preflight` makes, reported separately so a failure names itself."""
    verdict = em.preflight(build_case(next(c for c in em.EM_CONFIGS if c["name"] == task_id)))
    assert verdict["ok"] is True, json.dumps(verdict, default=str)
    assert verdict["understanding"]["ok"] is True, json.dumps(verdict["understanding"], default=str)
    assert verdict["geometry"]["ok"] is True, json.dumps(verdict["geometry"], default=str)
    assert verdict["pair"]["consistent"] is True, json.dumps(verdict["pair"], default=str)
    assert verdict["geometry"]["targets_already_in_a_region"] == [], (
        "a target that starts inside a region makes the writer's remembered claim a claim about a "
        "layout the scene already contradicts, which is not the structure any pair here tests")
    for eid, per in verdict["geometry"]["per_object"].items():
        assert per["grasp_candidates"] >= 1, f"{task_id}/{eid}: nothing to grasp"
        assert min(per["clear_slots"].values()) >= 1, f"{task_id}/{eid}: no slot where the truth wants one"


# ------------------------------------------------------------ negative controls ----
def test_preflight_refuses_a_pair_whose_expectation_the_arithmetic_no_longer_supports(by_id,
                                                                                      monkeypatch):
    """The check that makes this a set rather than a list, and it has to be able to fail.

    Retarget the claim, not the scene: `obj_blue_3` becomes the predicted first pick, which is the
    prediction a *memoryless* reader would support. The refusal must come from the pair block — if
    the geometry or the phrasing refused instead, this test would pass for the wrong reason.
    """
    writer = by_id["em_p1_write_two"]
    assert em.preflight(writer)["pair"]["consistent"] is True
    monkeypatch.setitem(_pair("em_p1")["expectation"], "control_first_pick", "obj_green_2")
    verdict = em.preflight(writer)
    assert verdict["ok"] is False and verdict["pair"]["consistent"] is False
    assert verdict["understanding"]["ok"] is True and verdict["geometry"]["ok"] is True, (
        "the scene is fine and the sentence is sayable; only the claim moved")


def test_preflight_refuses_a_pair_whose_cases_no_longer_bear_the_claim(by_id, monkeypatch):
    """The other direction of the same drift: the cases move under a frozen expectation.

    p1's expectation is a promotion out of two shared objects. Re-declare its reader as p3's — no
    object in common, so nothing remembered — and the arithmetic predicts the plan's own order while
    `EM_PAIRS` still predicts `obj_green_2`. This is what would happen if someone edited a case's
    seed or its hidden truth and left the claim in place.
    """
    monkeypatch.setitem(_pair("em_p1"), "reader", "em_p3_read_three")
    verdict = em.preflight(by_id["em_p1_write_two"])
    assert verdict["ok"] is False
    pair = verdict["pair"]
    assert pair["shared_objects"] == [] and pair["remembered_order"] == []
    assert pair["predicted_first_pick_round1"] != _pair("em_p1")["expectation"][
        "treatment_first_pick_round1"]
    assert pair["consistent"] is False


# ---------------------------------------------------------------- separation ----
def test_the_new_set_is_reachable_by_name_and_absent_from_the_frozen_47():
    cases = em.build_episodic_set()
    assert "em" in SET_NAMES, "the runner must be able to ask for the set"
    assert len(build_set("em")) == len(cases) == 8
    assert em.EM_SET_NAME not in ALL_SETS, "registered means frozen-47 membership"
    assert len(build_set("all")) == 47, "the episodic batch must not enlarge `all`"
    ids = {c.task_id for c in cases}
    assert ids.isdisjoint({c.task_id for c in build_set("all")}), "a case in both sets is two truths"
    assert ids.isdisjoint({t["task_id"] for s in frozen_manifest()["sets"].values() for t in s})
    ok, msg = check_frozen(FROZEN_PATH)
    assert ok, f"the published 47-case hash moved: {msg}"


def test_the_set_builds_to_the_same_bytes_twice():
    """Nothing in the authoring reads a clock, a random draw or a model."""
    cases, again = em.build_episodic_set(), em.build_episodic_set()
    assert [c.task_id for c in again] == [c.task_id for c in cases]
    for a, b in zip(cases, again):
        assert a.objects == b.objects and a.utterance == b.utterance
        assert a.eval_spec == b.eval_spec and a.event_specs() == b.event_specs()
        assert a.budgets == b.budgets


def test_one_budget_for_both_arms(by_id):
    """§8's reason, inherited: arms that differ in allowance measure the allowance.

    The em contrast is one module switch, so the two arms share everything else — the budget
    included, which the manifest publishes once for the set rather than per arm.
    """
    for cfg in em.EM_CONFIGS:
        got = by_id[cfg["name"]].budgets.model_dump(mode="json")
        assert {k: got[k] for k in em.EM_BUDGET} == em.EM_BUDGET, cfg["name"]
    assert em.EM_BUDGET == {"max_decision_rounds": 14, "max_skill_calls": 20}
    nominal_calls = max(len(c.targets) for c in by_id.values()) * 2
    assert em.EM_BUDGET["max_skill_calls"] >= 3 * nominal_calls, (
        "the allowance is no longer ~2x nominal work, so a pair could be censored by it")
    assert em.EM_BUDGET["max_decision_rounds"] > 13, (
        "13 rounds is the top of the 9-13 range measured for the 3-object episodes that exist")


# ------------------------------------------------------------------- the freeze ----
def test_the_manifest_freezes_to_its_own_file_and_agrees_with_the_code(on_disk):
    ok, msg = em.check_em_frozen()
    assert ok, msg
    assert em.EM_FROZEN_PATH != FROZEN_PATH
    assert on_disk["set"] == em.EM_SET_NAME == "em"
    assert [c["task_id"] for c in on_disk["cases"]] == [c.task_id for c in em.build_episodic_set()]
    assert [p["pair_id"] for p in on_disk["pairs"]] == [p["pair_id"] for p in em.EM_PAIRS]
    assert on_disk["budgets"] == em.EM_BUDGET
    assert on_disk["ablation_registered"] == sorted(ABLATION_CONDITIONS), (
        "§9's registry is the only source for this list; a second one drifts")
    assert on_disk["arms"]["treatment"] == f"ablation={em.EM_TREATMENT_ARM} + policy=memory"
    assert on_disk["arms"]["control"] == f"ablation={em.EM_CONTROL_ARM} + policy=memory"
    from embodied_agent.episodic.retrieval import (CLASH_CAP, DEFAULT_LIMIT, MATCH_TERM_CAP,
                                                   STEP_CAP)
    from embodied_agent.planning.working_memory import RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS
    assert on_disk["page_constants"] == {
        "RECALL_BUDGET_CHARS": RECALL_BUDGET_CHARS, "RENDER_BUDGET_CHARS": RENDER_BUDGET_CHARS,
        "DEFAULT_LIMIT": DEFAULT_LIMIT, "STEP_CAP": STEP_CAP, "MATCH_TERM_CAP": MATCH_TERM_CAP,
        "CLASH_CAP": CLASH_CAP}
    assert on_disk["relevance_formula"] == {"weights": RELEVANCE_WEIGHTS,
                                            "MIN_RELEVANCE": MIN_RELEVANCE}
    assert RECALL_BUDGET_CHARS % DEFAULT_LIMIT == 0, (
        "the page budget stops dividing evenly across rows, so one row's share now depends on which "
        "row is last")
    for recorded, live in zip(on_disk["pairs"], em.pair_claims()):
        assert recorded == json.loads(json.dumps(live)), recorded["pair_id"]


def test_the_manifest_carries_its_numbers_as_re_runnable_citations(on_disk):
    """A measurement with no script behind it is an assertion, and this one has four.

    The counts are the set's justification for its own shape: why the order lever had to be seeded (0
    of 370 rounds took a non-first row), why negative transfer is reported as an absence (78 of 78
    orders completed), why the decline branch is a record rather than a brake (0 declines in 12 repeat
    episodes on a store that does hold two `false` claims).
    """
    lever, cost, decline = (on_disk["order_lever_measurement"],
                            on_disk["negative_transfer_constructibility"],
                            on_disk["decline_branch"])
    assert lever["rounds_that_took_a_row_other_than_the_first_published"] == 0
    assert lever["rows_that_were_not_placed_predicates"] == 0
    assert lever["rounds_with_more_than_one_ready_row"] == 254 < lever["rounds"] == 370
    assert cost["orders_completed"] == cost["orders_tested"] == 78 and cost["refused_calls"] == 0
    assert decline["measured"]["em_declines"] == 0
    assert decline["measured"]["lh_pass1_stored_false_claims"] == 2
    assert decline["measured"]["lh_pass2_declines"] == 0
    assert all(v is False for v in decline["reachable_on_this_store"].values())
    cited = (lever["source"] + [cost["probe"], decline["measured"]["lh_repeat_probe"]]
             + [c for p in on_disk["pairs"] for c in p["preregistration"].get("falsified_by", [])
                if c.startswith("work/")])
    assert cited
    for rel in cited:
        assert (em_root / rel).exists(), f"{rel} is cited as the source of a number and is not there"


def test_a_hand_edited_pair_fails_its_own_hash_before_it_fails_the_code(tmp_path, on_disk):
    """The hash is a field *of the file it describes*, so an edited claim that left the hash alone
    would still be believed by anything comparing only the file to the code."""
    for field, value in (("decline_branch_reachable", True), ("condition", "promotion")):
        edited = json.loads(json.dumps(on_disk))
        edited["pairs"][1][field] = value
        path = str(tmp_path / f"em_{field}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(edited, f, ensure_ascii=False)
        ok, msg = em.check_em_frozen(path)
        assert not ok and "does not hash to its own" in msg, (field, msg)


def test_the_check_distinguishes_a_missing_file_from_a_drifted_one(tmp_path, on_disk):
    """Two refusals, two messages: "not frozen yet" must not be readable as "the code moved"."""
    ok, msg = em.check_em_frozen(str(tmp_path / "absent.json"))
    assert not ok and "no episodic manifest" in msg
    drifted = json.loads(json.dumps(on_disk))
    drifted["sha256"] = "0" * 64
    path = str(tmp_path / "drifted.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(drifted, f, ensure_ascii=False)
    ok, msg = em.check_em_frozen(path)
    assert not ok and "does not hash to its own" in msg


# --------------------------------------------- relevance cannot exclude in a batch ----
def _foreign_row() -> EpisodicExperience:
    """A row from this set that shares no object, region, kind, predicate or code with the query."""
    return EpisodicExperience(
        experience_id="exp_foreign", episode_id="em_p3_write_two", task_context="em/em_p3_write_two",
        task_kind=em.EM_SET_NAME, scene_id="em_p3_write_two", object_kinds=["sphere:orange"],
        state_pattern=["placed:obj_unheard_of:tray_nowhere=true"],
        steps=[ExperienceStep(skill="pick", args={"object_id": "obj_unheard_of"}, round_index=1,
                              effect="completed"),
               ExperienceStep(skill="place", args={"object_id": "obj_unheard_of",
                                                   "target_id": "tray_nowhere"},
                              round_index=2, effect="completed")],
        outcome="success", official_success=True,
        run_ref="/tmp/em_p3/full/writer/episodes/em_p3_write_two.B.r0")


def test_task_kind_alone_makes_a_foreign_row_retrievable():
    """The property p3 exists to stop from being read as a decision.

    `arm.py::_memory_query` takes the query's `task_kind` from the batch's set name and every row the
    batch writes carries that same name, so one term is worth `RELEVANCE_WEIGHTS["task_kind"]` against
    `MIN_RELEVANCE`. A row with nothing else in common with the query is therefore still *relevant*:
    inside a batch relevance orders and does not exclude. `episodic_runs` measures `recalled` and
    `memory_order` separately for exactly this reason.
    """
    assert RELEVANCE_WEIGHTS["task_kind"] > MIN_RELEVANCE
    row = _foreign_row()
    query = query_from(task_kind=em.EM_SET_NAME, regions=["tray_left"], kinds=["cube:blue"],
                       predicates=["placed:obj_blue_3:tray_left"])
    hits = retrieve(query, [row])
    assert [h.experience_id for h in hits] == ["exp_foreign"], (
        "a row with nothing in common with the query was filtered out — the manifest's "
        "relevance_within_a_batch claim is now false, and so is p3's purpose")
    assert hits[0].relevance == RELEVANCE_WEIGHTS["task_kind"]
    assert hits[0].match_terms == [f"task_kind:{em.EM_SET_NAME}"]
    assert hits[0].contradicted_current_state is False, (
        "nothing was measured, so the guard may not call the row refuted")
    assert retrieve(query_from(task_kind="long_horizon"), [row]) == [], (
        "the term that carried this row is gone and the row is still retrievable")


def test_an_unmeasured_claim_is_never_a_refutation_but_a_refuted_one_is_still_shown():
    """The guard's two halves, on the exact row shape p2's reader is shown.

    §5.4's `measured` map is what decides whether a recalled claim contradicts the world. Silence is
    not refutation — reading it as such would talk the agent out of a correct memory — and a real
    refutation is rendered and labelled rather than dropped, because an agent that cannot see "your
    memory says X, the world says not-X" cannot learn which of the two was the problem.
    """
    from embodied_agent.episodic.retrieval import contradicted

    row = _foreign_row()
    predicate = "placed:obj_unheard_of:tray_nowhere"
    assert contradicted(row, None) == []
    assert contradicted(row, {}) == []
    assert contradicted(row, {predicate: "unknown"}) == []
    assert contradicted(row, {predicate: "true"}) == []
    clashes = contradicted(row, {predicate: "false"})
    assert len(clashes) == 1 and predicate in clashes[0]
    hits = retrieve(query_from(task_kind=em.EM_SET_NAME), [row], measured={predicate: "false"})
    assert hits and hits[0].contradicted_current_state is True
    assert hits[0].provenance["contradictions"] == clashes, (
        "the flag was set and the reason not carried: the rendered page would show a refuted memory "
        "with nothing saying what refuted it")
