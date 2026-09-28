"""SPEC-v0.2 §5.4/§7/§9 contract tests: the episodic arm wired into the loop, and unpluggable.

Everything here is a question about *the seam*, not about whether memory helps — that is P3-e's
measurement. The seams are three (the recall, the judgement, the residue) and each one is a place
where a wrong wiring would produce a result that looks like a finding:

* a recall that reached the log but not the page would show up as "memory had no effect", which is
  the null result the phase was built to avoid reporting;
* a recall that reached the page on *some* arms and not others, without the record saying which,
  would make `full` vs `wo_episodic_memory` a comparison of two loops;
* a judgement read from the rationale instead of the action would report a model's story about its
  motives as §11's use-rate, which §12.3 forbids in one clause;
* and a write that differs from the row an offline reader rebuilds from the same archive would
  make the stored past uncheckable, which is the reason the store exists at all.

Two of the tests are about what is *not* on the page. `"official_success"` must not appear in any
payload (§3.2: the hidden third-layer verdict never reaches a prompt), and a `wo_episodic_memory`
round must carry no `recalled` key at all rather than a `null` one — the same distinction P2 drew
between an absent section and an empty one, extended to the arm's denominator (`[]` means the store
was queried and had nothing relevant, which is a measurement; no key means no store was queried,
which is an arm).

The seed rows are produced by real episodes through `run_one_episode`, not written by hand: a
fixture the writer cannot produce would test the reader against a shape that never occurs.
"""
from __future__ import annotations

import inspect
import json
import os
from collections import namedtuple

import pytest

from embodied_agent.core.contracts import (
    Decision,
    DecisionExecute,
    ExecutionFeedback,
    TerminalStatus,
)
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime, build_world_state
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.v02 import (
    ABLATION_CONDITIONS,
    EVENT_MODULE,
    SCHEMA_VERSION,
    V02_EVENT_TYPES,
    RecalledExperience,
    ablation,
)
from embodied_agent.episodic.arm import (
    EPISODIC_ARMS,
    EpisodicMixin,
    ExperiencedPlannedRuntime,
    build_episodic_arm,
)
from embodied_agent.episodic.experience import experience_from_episode
from embodied_agent.episodic.policy import MemoryPolicy
from embodied_agent.episodic.store import ExperienceStore
from embodied_agent.evaluation.run import build_scene, resolve_goal, run_one_episode, task_input
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.planning.arm import PLANNING_ARMS, PlanningMixin
from embodied_agent.planning.policy import PlanPolicy

CASE = "dev_c1"
#: the batch's §5.4 任务上下文 kind term. `run_one_episode` passes the frozen set name and nothing
# else can, so a test that wanted a different word would have to change the runner.
KIND = "dev"
#: which records §9's registry says the memory module owns
OWNED = tuple(t for t, owner in EVENT_MODULE.items() if owner == "episodic_memory")
#: the columns a `MemoryPolicy` trace shares with `PlanPolicy`'s. `MemoryPolicy.decide` adds three
# of its own (`recalled`, `memory_order`, `memory_followed`), and comparing whole dicts would
# compare the reporting as well as the deciding.
TRACE_KEYS = ("round_index", "state_version", "views", "action", "skill", "args", "subgoal",
              "unactuated", "rationale")
Episode = namedtuple("Episode", "arm runtime policy result events s4 types by_type summaries")


# ------------------------------------------------------------------- the two entries ----
def _arm(condition: str, root: str, experience_store: ExperienceStore | None, *,
         memory_render_budget: int | None = None,
         memory_recall_budget: int | None = None, tag: str = "") -> Episode:
    """One real episode through the one production memory builder, at zero spend.

    `build_episodic_arm` rather than `run_one_episode` because the payload each round was answered
    from, and the working-memory state at the end of the run, are the objects under test — and
    `run_one_episode` hands neither back.
    """
    case = find_case(CASE)
    scene = build_scene(case)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, world)
        episode_id = f"{CASE}.{condition}{tag}"
        ep_dir = os.path.join(root, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        budgets: dict[str, int] = {}
        if memory_render_budget is not None:
            budgets["memory_render_budget"] = memory_render_budget
        if memory_recall_budget is not None:
            budgets["memory_recall_budget"] = memory_recall_budget
        runtime = build_episodic_arm(
            case=case, scene=scene, executor=executor, store=EpisodeStore(ep_dir, episode_id),
            run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets, perceive="privileged",
            ablation=None if condition == "unarmed" else ablation(condition),
            environment=EnvironmentController(scene, case.fresh_events()),
            experience_store=experience_store, task_kind=KIND, **budgets)
        executor.world_provider = runtime.observe
        policy = MemoryPolicy()
        result = runtime.run_episode(task, goal, policy, mode="B")
        events = runtime.store.read_all()
        s4 = runtime.store.read_all(SCHEMA_VERSION)
        by_type: dict[str, list[dict]] = {}
        for e in events:
            by_type.setdefault(str(e["type"]), []).append(e)
        return Episode(condition, runtime, policy, result, events, s4,
                       sorted({str(e["type"]) for e in s4}), by_type,
                       [ctx.model_payload() for ctx in policy.seen])
    finally:
        scene.close()


def _seed(root: str, repeats: int = 2) -> ExperienceStore:
    """Store rows written by real episodes, through the entry a batch writes them from.

    Two repeats rather than one because the render-budget test needs a round offered more recalls
    than the page can carry, and one row cannot be dropped by a budget that showed it.
    """
    experience_store = ExperienceStore.load(os.path.join(root, "seed_store.jsonl"))
    case = find_case(CASE)
    resolution = resolve_goal(case, 0, None, root)
    assert resolution.error is None, resolution.error
    for repeat in range(repeats):
        summary = run_one_episode(case, repeat, "B", resolution, None, root, KIND, frames=False,
                                  perceive="privileged", ablation=ablation("full"),
                                  experience_store=experience_store, policy="memory")
        assert summary["episodic"]["written_experience_id"], summary["episodic"]
    return experience_store


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("episodic_seed"))


@pytest.fixture(scope="module")
def store(seeded) -> ExperienceStore:
    experience_store = _seed(seeded)
    assert len(experience_store) == 2, [r.experience_id for r in experience_store.all()]
    return experience_store


@pytest.fixture(scope="module")
def cold(tmp_path_factory) -> ExperienceStore:
    """A store that exists and is empty: a cold start, which §5.4 measures rather than errors on."""
    return ExperienceStore.load(os.path.join(str(tmp_path_factory.mktemp("episodic_cold")),
                                             "empty.jsonl"))


@pytest.fixture(scope="module")
def episodes(seeded, store, cold) -> dict[str, Episode]:
    root = os.path.join(seeded, "arms")
    os.makedirs(root, exist_ok=True)
    return {"full": _arm("full", root, store),
            "wo_episodic_memory": _arm("wo_episodic_memory", root, store),
            "wo_working_memory": _arm("wo_working_memory", root, store),
            "cold": _arm("full", root, cold, tag=".cold"),
            "tight": _arm("full", root, _seed(os.path.join(root, "tight"), repeats=3),
                          tag=".tight", memory_recall_budget=1300)}


def _recall_line(text: str, recall_id: str) -> str:
    """One recall's rendered line, continuations included, out of the page's text.

    A rendered row is not one `\\n`-free string: `render_rows` puts the step sequence and the
    guard's verdict on indented continuation lines. Reading the block line by line and stopping at
    the first would let a test assert "the verdict is on the page" while the page carried it two
    lines below the row the test looked at.
    """
    out: list[str] = []
    keep = False
    for line in str(text or "").split("\n"):
        if line.startswith("recall["):
            keep = line.startswith(f"recall[{recall_id}]")
        if keep:
            out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------ the mixture ----
def test_the_mixture_overrides_two_existing_seams_and_adds_four_names():
    """The arm's whole footprint on the loop, read off the class rather than the docstring.

    Two of the mixin's functions override something `Runtime` already had (`_begin_episode`,
    `_record_feedback`); four are names nothing else in the loop owns. A fifth new name would be a
    fourth seam the phase log does not describe, and a third override would mean the arm reaches
    somewhere §7 does not route memory through.
    """
    own = {n for n, v in vars(EpisodicMixin).items() if inspect.isfunction(v)}
    assert own & set(dir(Runtime)) == {"_begin_episode", "_record_feedback"}
    assert own == {"_begin_episode", "_record_feedback", "_offer_memories", "_memory_query",
                   "_executed", "write_experience"}
    assert ExperiencedPlannedRuntime.__mro__[:4] == (ExperiencedPlannedRuntime, EpisodicMixin,
                                                     PlanningMixin, Runtime)
    for name in ("run_episode", "observe", "initial_context"):
        assert getattr(ExperiencedPlannedRuntime, name) is getattr(Runtime, name), name


def test_the_registry_condition_is_the_only_memory_arm_and_switches_one_module():
    assert "wo_episodic_memory" not in PLANNING_ARMS
    assert EPISODIC_ARMS == PLANNING_ARMS + ("wo_episodic_memory",)
    assert list(ablation("wo_episodic_memory").modules_off) == list(
        ABLATION_CONDITIONS["wo_episodic_memory"])
    assert set(OWNED) == {"memory_write", "memory_retrieval", "memory_use"}


# ---------------------------------------------------------- the recall reaches the page ----
def test_a_row_written_by_an_earlier_episode_reaches_a_later_rounds_page(episodes, store):
    """The one test that says the wiring works at all: a store with rows in it changes a payload.

    `cold` is its control — same case, same arm, same builder, no rows — so the difference below
    cannot be attributed to the scene, the seed or the policy object.
    """
    warm, chilled = episodes["full"], episodes["cold"]
    known = {r.experience_id for r in store.all()}
    warm_recalled = [(p["working_memory"].get("recalled") or []) for p in warm.summaries]
    cold_recalled = [(p["working_memory"].get("recalled") or []) for p in chilled.summaries]
    assert any(warm_recalled), "no recall ever reached the page"
    assert not any(cold_recalled), cold_recalled[:1]
    for row in [row for rows in warm_recalled for row in rows]:
        assert row["experience_id"] in known
        assert row["provenance"]["steps"], "a recall with no actions cannot be followed"
    assert all(row["injected_into_round"] == payload["round_index"]
               for payload, rows in zip(warm.summaries, warm_recalled) for row in rows)
    text = json.dumps(warm.summaries[-1], ensure_ascii=False)
    assert "recall[" in text and "did: " in text, "the line the model reads is not in the block"


def test_a_store_queried_and_silent_is_not_the_same_fact_as_no_store(episodes):
    """`recalled: []` and no `recalled` key at all: §11's relevance denominator needs the pair.

    The cold arm retrieved nothing because the store was empty, and must report zero rows against a
    non-zero number of queries. The ablated arm must report no query and no key — a `null` here
    would be a hint about the experiment on the page a decision is made from, the same mistake P2
    refused for an absent `plan` section.
    """
    chilled = episodes["cold"]
    blocks = [p["working_memory"] for p in chilled.summaries if "working_memory" in p]
    assert blocks and all("recalled" in b for b in blocks)
    assert all(b["recalled"] == [] for b in blocks)
    assert all(b["counts"]["recalled_offered"] == 0 for b in blocks)
    assert chilled.runtime.retrievals == len(chilled.by_type["memory_retrieval"]) > 0
    assert chilled.runtime.retrieved_rows == 0 and chilled.runtime.used_rows == 0
    assert all(e["payload"]["retrieved"] == [] for e in chilled.by_type["memory_retrieval"])

    ablated = episodes["wo_episodic_memory"]
    assert all("recalled" not in p.get("working_memory", {}) for p in ablated.summaries)
    assert ablated.runtime.retrievals == 0


def test_the_ablated_arm_is_p2s_arm_with_a_store_it_never_reads(episodes, store):
    """The control has to have the *information* unavailable, not merely decline to use it.

    A `wo_episodic_memory` run against a populated store is the strongest form of the arm: the rows
    exist, the store's fingerprint is unchanged when the episode ends, no `memory_*` record appears
    in the log, and `Ablation.violations()` — the referee the registry enforces itself with —
    agrees. The P2 equivalence is then checked the only way it can be: the payload's key set is
    exactly what the planning arm's was.
    """
    ep = episodes["wo_episodic_memory"]
    assert [t for t in ep.types if t in OWNED] == []
    assert ExperienceStore.load(store.path).fingerprint() == store.fingerprint()
    assert ep.runtime.experience_store is not None
    assert ep.runtime.write_experience({"episode_id": "x"}, run_ref="y") is None
    assert ep.runtime.retrievals == 0 and ep.runtime.written == 0
    arm = ablation("wo_episodic_memory")
    assert arm.violations(ep.types) == []
    assert set(ep.types) <= set(V02_EVENT_TYPES)
    # no section appears or vanishes mid-episode: a key that came and went would mean the arm's
    # own state, not the registry, was deciding what the page carried
    assert all(set(payload) == set(ep.summaries[0]) for payload in ep.summaries)


def test_a_ledger_with_no_page_offers_the_recall_to_no_one_but_still_asks(episodes):
    """§5.4's rows ride `WorkingMemoryState.recalled`, so with working memory off there is nothing
    for them to ride and the round is measurably offered nothing.

    This arm stays honest in the opposite direction from `wo_episodic_memory`: retrieval still
    happens and is still recorded, because the module that looks is not the module that was
    switched off. The finding is that the lookup was wasted, and the log has to be able to say so.
    """
    ep = episodes["wo_working_memory"]
    assert ep.by_type["memory_retrieval"], "the lookup was skipped, which is a different arm"
    assert all("recalled" not in p.get("working_memory", {}) for p in ep.summaries)
    assert ep.runtime.retrieved_rows > 0
    assert ep.runtime.memory.state.recalled == []


def test_only_the_budget_admitted_rows_ride_the_state_and_the_view(episodes):
    """Three numbers per round that a careless arm would collapse into one.

    offered / shown / dropped, with `shown + dropped == offered`, and the two places a row can be
    seen — `WorkingMemoryState.recalled` and the payload's `working_memory.recalled` — holding the
    same objects. `memory_recall_budget` is set below `3 x` the measured recall line so the drop is
    a fact about the §5.4 pool rather than about retrieval: under the P2 shape — one shared
    900-character pool — this test could not be written, because nothing was ever shown.
    """
    ep = episodes["tight"]
    blocks = [p["working_memory"] for p in ep.summaries if "recalled" in p["working_memory"]]
    assert max(b["counts"]["recalled_offered"] for b in blocks) >= 2
    for block in blocks:
        counts = block["counts"]
        assert counts["recalled_offered"] == counts["recalled_shown"] + counts["recalled_dropped"]
        assert len(block["recalled"]) == counts["recalled_shown"]
        assert counts["recalled_shown"] <= counts["recalled_offered"]
    assert any(b["counts"]["recalled_dropped"] for b in blocks), "the budget never cut a recall"
    assert any("recalled experience line(s) left out" in b["text"] for b in blocks)
    assert not any(b["counts"]["over_budget"] for b in blocks), "the debt pool was overrun"
    assert not any(b["counts"]["recalled_chars"] > b["counts"]["recall_budget"] for b in blocks)
    last = blocks[-1]
    assert [r["recall_id"] for r in (last["recalled"] or [])] == [
        r.recall_id for r in ep.runtime.memory.state.recalled]
    admitted = [r["recall_id"] for r in (last["recalled"] or [])]
    queried = [row["recall_id"]
               for row in ep.by_type["memory_retrieval"][-1]["payload"]["retrieved"]]
    # Admission is rank-preserving, not rank-selecting: what the budget cuts is a suffix of the
    # arm's own relevance order, so a row is never hidden *because* the guard flagged it. Which
    # rows carried `contradicted_current_state` is a separate fact and is checked below.
    assert queried[:len(admitted)] == admitted
    # The guard's verdict travels with the row it belongs to, on the page rather than only in the
    # log — and on whichever rounds it fired, which is not every round: by the last round of
    # `dev_c1` the plan's own goals are all satisfied, the memory's claims measure true, and a test
    # that demanded a verdict there would be demanding a wrong one.
    fired = 0
    for block in blocks:
        for row in block["recalled"] or []:
            if not row["contradicted_current_state"]:
                continue
            fired += 1
            assert row["provenance"]["contradictions"], row
            assert "CONTRADICTED by the current snapshot" in _recall_line(block["text"],
                                                                         row["recall_id"]), row
    assert fired, "the guard never fired on a reset world, so this arm proved nothing about it"


# ------------------------------------------------------------- the payload's contents ----
def test_the_hidden_verdict_is_on_no_page_a_decision_is_made_from(episodes):
    """§3.2: 隐藏真值不进 prompt — including the one field the *store* carries it in.

    `retrieve()`'s provenance is copied into the payload near-verbatim, so a field added there for
    the reader of a log appears on the page a policy reads. `official_success` is the third layer's
    answer and an agent that saw it would be shown the exam key; the row's own `outcome` is the
    loop's claim and is allowed, which is why the test looks for the field names and not for words.
    """
    for name, ep in episodes.items():
        rows = [row for payload in ep.summaries
                for row in (payload.get("working_memory") or {}).get("recalled") or []]
        for payload in ep.summaries:
            text = json.dumps(payload, ensure_ascii=False, default=str)
            for leak in ("official_success", "counter_examples", "state_pattern", "applicability"):
                assert leak not in text, (name, leak)
        for row in rows:
            assert set(row["provenance"]) == {"run_ref", "outcome", "steps", "failure_reason",
                                              "contradictions", "transfer_caution"}, row
            # The contract's own field list, read from the contract rather than restated: a
            # `RecalledExperience` is a `Record`, so the bookkeeping fields ride along, and what the
            # test has to hold fixed is that nothing *else* does. A field added to the payload
            # outside the schema would be a new channel to the page that no version number covers.
            assert set(row) == set(RecalledExperience.model_fields), row
            assert {"recall_id", "experience_id", "relevance", "match_terms",
                    "injected_into_round", "used_by_model",
                    "contradicted_current_state"} <= set(row), row


# ---------------------------------------------------------------- the judgement ----
def test_every_judgement_names_the_action_it_was_made_from(episodes):
    """§12.3's 以实际动作和最终状态为准, as a record shape rather than as an intention.

    `used ⊆ shown ⊆ retrieved` is the whole of the honesty here: a row that never reached the page
    cannot be counted as a miss the agent earned, and a row counted as used has to cite the step
    that repeated it. A `True` with no evidence reference is the shape this test exists to catch.
    """
    filed = [e["payload"] for e in episodes["full"].by_type["memory_use"]]
    assert filed, "no round was ever judged"
    for payload in filed:
        assert set(payload["used"]) <= set(payload["shown"]) <= set(payload["retrieved"])
        assert payload["retrieved"] and payload["judgements"]
        for experience_id, judgement in payload["judgements"].items():
            assert str(judgement["value"]) in ("True", "False", "unknown"), judgement
            if judgement["value"] is True:
                assert judgement["evidence_refs"], judgement
            if experience_id not in payload["shown"]:
                assert judgement["value"] is not True, judgement
        for step in payload["executed"]:
            assert step["skill"] and set(step) == {"skill", "args"}
    assert any(payload["status"] for payload in filed)
    runtime = episodes["full"].runtime
    assert runtime.used_rows == sum(len(p["used"]) for p in filed)


def test_a_refused_decision_contributes_no_action_to_the_judgement(episodes):
    """`_executed` is the judge's only input, and a rejection never became an action.

    Read off the runtime with a hand-made feedback rather than by hunting for an episode that
    happened to be refused: the rule is about the function, and a test that depended on a scene
    producing a rejection would pass or fail for the wrong reason.
    """
    runtime = episodes["full"].runtime
    runtime.decision_by_id["d_hand"] = Decision(
        context_id="ctx", based_on_state_version=1, goal_ref="g", action="execute",
        execute=DecisionExecute(skill="place", args={"object_id": "a", "target_id": "b"}))
    assert runtime._executed(ExecutionFeedback(decision_id="d_hand", status="rejected",
                                               post_observation_ref="obs_y")) == []
    assert runtime._executed(ExecutionFeedback(decision_id="no_such_decision",
                                               status="completed",
                                               post_observation_ref="obs_y")) == []
    assert runtime._executed(ExecutionFeedback(decision_id="d_hand", status="completed",
                                               post_observation_ref="obs_y")) == [
        {"skill": "place", "args": {"object_id": "a", "target_id": "b"}}]


# ---------------------------------------------------------------- the residue ----
def _run_batch_episode(root: str, experience_store, condition: str, *, policy: str | None,
                       perceive: str = "privileged"):
    case = find_case(CASE)
    resolution = resolve_goal(case, 0, None, root)
    return run_one_episode(case, 0, "B", resolution, None, root, KIND, frames=False,
                           perceive=perceive, ablation=ablation(condition),
                           experience_store=experience_store, policy=policy)


def test_the_residue_is_written_after_the_episode_said_it_was_over(tmp_path):
    """`memory_write` postdates `episode_end`, and the row it files is the row in the store.

    The summary is built first and the store appended second for the same reason, so the assertion
    that matters is the sequence one: a reader who pools the records *inside* an episode has to be
    able to exclude the residue by sequence number, and the payload says so in words as well.
    """
    root = str(tmp_path)
    experience_store = ExperienceStore.load(os.path.join(root, "store.jsonl"))
    summary = _run_batch_episode(root, experience_store, "full", policy="memory")
    with open(os.path.join(summary["artifacts"]["episode_dir"], "events.jsonl"),
              encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    writes = [e for e in events if e["type"] == "memory_write"]
    ends = [e for e in events if e["type"] == "episode_end"]
    assert len(writes) == 1 and len(ends) == 1
    assert writes[0]["sequence"] > ends[0]["sequence"]
    assert writes[0]["payload"]["written_after"] == "episode_end"

    block = summary["episodic"]
    assert block["store_size_before"] == 0 and block["store_size_after"] == 1
    assert block["written_experience_id"] == writes[0]["payload"]["experience_id"]
    assert block["arm"] == "full" and block["memory_events"]["memory_write"] == 1
    # The first episode on an empty store still *asks*, seven times, and is answered with nothing.
    # `retrievals` counts queries and `rows_retrieved` counts rows, and the pair is the cold-start
    # reading: an arm that never looked has to be tellable from one that looked and found nothing,
    # which is the same distinction the payload keeps between `recalled: []` and no `recalled` key.
    assert block["retrievals"] > 0 and block["rows_retrieved"] == 0
    assert ExperienceStore.load(experience_store.path).ids() == [block["written_experience_id"]]

    # One extractor, one answer: the row the batch acted on, the row the event filed, and the row
    # rebuilt from the archive afterwards are the same object. The archive re-read carries the
    # `episodic` key the write added, which the extractor must ignore like any key it does not name.
    stored = experience_store.get(block["written_experience_id"])
    assert writes[0]["payload"]["experience"] == stored.model_dump(mode="json")
    with open(os.path.join(summary["artifacts"]["episode_dir"], "episode_summary.json"),
              encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert "episodic" in on_disk
    rebuilt = experience_from_episode(on_disk, events,
                                      run_ref=summary["artifacts"]["episode_dir"])
    assert rebuilt.experience_id == stored.experience_id

    def _without_clock(row: dict) -> dict:
        """The dump minus the two fields a `Record` stamps with the wall clock as it is built.

        `experience_id` is derived from the content and is asserted equal above, so what is left to
        compare is content; a rebuild two hundred milliseconds later is the same memory, and a test
        that compared the stamps would be testing the clock.
        """
        return {key: value for key, value in row.items()
                if key not in ("created_at", "updated_at")}

    assert _without_clock(rebuilt.model_dump(mode="json")) == \
           _without_clock(stored.model_dump(mode="json"))


def test_a_second_batch_episode_appends_rather_than_rewriting(tmp_path):
    """The store is append-only across episodes, and a run that repeated an episode id must crash
    on the append rather than quietly replace what an earlier retrieval row cited."""
    root = str(tmp_path)
    experience_store = ExperienceStore.load(os.path.join(root, "store.jsonl"))
    _run_batch_episode(root, experience_store, "full", policy="memory")
    fingerprint = experience_store.fingerprint()
    second = _run_batch_episode(os.path.join(root, "second"), experience_store, "full",
                                policy="memory")
    assert second["episodic"]["store_size_before"] == 1
    assert second["episodic"]["retrievals"] > 0, "the second episode never asked the store"
    assert second["episodic"]["rows_retrieved"] > 0
    assert len(experience_store) == 2 and experience_store.fingerprint() != fingerprint


def test_an_unwired_run_creates_no_store_and_an_ablated_run_leaves_one_alone(tmp_path):
    """Two ways a batch can report a memory it did not have, refused at the write.

    The v0.1 path (`experience_store=None`) must not leave a file behind — a later cold-start read
    of an empty file is indistinguishable from an empty file a run *did* write to. And a
    `wo_episodic_memory` run must add nothing to the store it was handed.
    """
    root = str(tmp_path)
    untouched = ExperienceStore.load(os.path.join(root, "v01.jsonl"))
    plain = _run_batch_episode(os.path.join(root, "plain"), None, "full", policy=None)
    assert "episodic" not in plain
    assert not os.path.exists(untouched.path)

    experience_store = ExperienceStore.load(os.path.join(root, "store.jsonl"))
    _run_batch_episode(os.path.join(root, "warm"), experience_store, "full", policy="memory")
    fingerprint = experience_store.fingerprint()
    summary = _run_batch_episode(os.path.join(root, "ablated"), experience_store,
                                 "wo_episodic_memory", policy="memory")
    assert summary["episodic"]["written_experience_id"] is None
    assert summary["episodic"]["store_size_after"] == 1
    assert summary["episodic"]["retrievals"] == 0
    assert experience_store.fingerprint() == fingerprint


# ------------------------------------------------------------------ the policy ----
def test_the_memory_policy_reduces_to_the_plan_policy_when_the_page_says_nothing(episodes):
    """Line for line, not "close enough": the contrast P3-e measures needs the two trajectories to
    be identical when nothing is remembered, or a difference in a rate is a difference in tools.

    Three pages say nothing in three different ways — a cold store, a module switched off, and a
    ledger that never received the rows — and all three have to land on `PlanPolicy`'s answers,
    replayed on the *same* contexts the memory arm was given. The warm arm is excluded on purpose:
    there the two policies are supposed to disagree, which is the next test's subject.
    """
    for name in ("cold", "wo_episodic_memory", "wo_working_memory"):
        ep = episodes[name]
        replay = PlanPolicy()
        for ctx in ep.policy.seen:
            replay.decide(ctx)
        assert [{k: row[k] for k in TRACE_KEYS} for row in replay.trace] == \
               [{k: row[k] for k in TRACE_KEYS} for row in ep.policy.trace], name
        assert all(not entry["memory_followed"] for entry in ep.policy.trace), name


def test_a_recall_can_only_reorder_the_rows_it_was_given():
    """The one thing `MemoryPolicy` may do, and the two things it may not.

    A synthetic payload rather than a scene, because the case this test needs is *two ready rows
    whose order the memory disagrees with*, and no scene will be rebuilt to supply one. The rows are
    `PlanPolicy`'s own shape and the recall is `memory_view`'s own shape. What is asserted is that
    the remembered object's row wins, that a refuted claim the plan does not share wins nothing,
    that a refuted claim the plan *does* share still wins (the reset-world case, which is the whole
    of RQ3's positive transfer), and that an object no recall mentions leaves the order where the
    plan put it.
    """
    def payload(recalled, ready, *, targets=None):
        targets = targets or {}
        rows = [{"subgoal_id": f"s_{o}", "row_key": f"{i}_{o}", "kind": "achieve",
                 "predicate_id": f"placed:{o}:{targets.get(o, 'tray_b')}",
                 "statement": f"{o} in {targets.get(o, 'tray_b')}", "satisfied": "false"}
                for i, o in enumerate(("cube_a", "cyl_b"))]
        return {"context_id": "ctx", "state_version": 3, "round_index": 4,
                "observation_ref": "obs_0004", "goal": {"goal_id": "g1"},
                "world": {"held_object": None, "targets": [{"target_id": "tray_a"},
                                                           {"target_id": "tray_b"}]},
                "progress": [], "attempts": [],
                "plan": {"version": 1, "rows": rows, "ready": list(ready)},
                "working_memory": {"text": "", "recalled": list(recalled)}}

    def clash(object_id, target_id):
        return (f"placed:{object_id}:{target_id}: recalled true, "
                f"this snapshot measures false")

    def recall(*object_ids, contradicted=None, experience_id="exp_1"):
        contradictions = list(contradicted or [])
        return {"experience_id": experience_id, "recall_id": f"rc_{experience_id}",
                "relevance": 0.8, "match_terms": ["task_kind:dev"], "injected_into_round": 4,
                "contradicted_current_state": bool(contradictions),
                "used_by_model": {"value": None, "evidence_refs": [], "unmeasured": []},
                "provenance": {"run_ref": "/tmp/other", "outcome": "success",
                               "steps": [{"skill": "pick", "args": {"object_id": o}}
                                         for o in object_ids],
                               "failure_reason": "", "contradictions": contradictions,
                               "transfer_caution": ""}}

    ready = ("s_cube_a", "s_cyl_b")
    rule = PlanPolicy()._choose(payload([], ready))
    assert rule.execute.args["object_id"] == "cube_a", "row order is not the plan's own"

    policy = MemoryPolicy()
    moved = policy._choose(payload([recall("cyl_b")], ready))
    assert moved.execute.args["object_id"] == "cyl_b"
    assert "followed a recalled sequence" in moved.rationale
    assert policy.memory_order == {"cyl_b": 0} and policy.memory_declined == {}

    # Refuted, and the plan owes that very predicate: the memory is describing the work this
    # episode still has to do, so the order stands. This is the case a reset world puts in front of
    # every repeat of a similar task.
    congruent = MemoryPolicy()
    still = congruent._choose(payload([recall("cyl_b", contradicted=[clash("cyl_b", "tray_b")])],
                                      ready))
    assert still.execute.args["object_id"] == "cyl_b", "a congruent refutation vetoed the memory"
    assert congruent.memory_order == {"cyl_b": 0} and congruent.memory_declined == {}

    # Refuted, and the plan wants the object somewhere the memory never put it: following the
    # remembered step would act on a layout the world has refuted for a reason this task does not
    # share. This is §11's negative transfer, and the plan's own order survives it.
    declined = MemoryPolicy()
    refuted = declined._choose(payload(
        [recall("cyl_b", contradicted=[clash("cyl_b", "tray_b")])], ready,
        targets={"cyl_b": "tray_a"}))
    assert refuted.execute.args["object_id"] == "cube_a", "a refuted memory still decided"
    assert "followed a recalled" not in refuted.rationale
    assert declined.memory_order == {} and declined.memory_declined == {
        "cyl_b": ["placed:cyl_b:tray_b"]}
    # One row's claims split by object, and the remembered order is [cyl_b, cube_a]: the step about
    # `cube_a` is congruent with what this plan owes and survives, the one about `cyl_b` is refuted
    # for a target this task never asked for and drops out. A row-level veto could only have been
    # wrong about one of them, and this is the shape §11's negative-transfer row has to be able to
    # count — the first object in the memory is not the one the agent works.
    mixed = MemoryPolicy()
    both = mixed._choose(payload(
        [recall("cyl_b", "cube_a", contradicted=[clash("cyl_b", "tray_b"),
                                                 clash("cube_a", "tray_b")])],
        ready, targets={"cyl_b": "tray_a"}))
    assert mixed.memory_order == {"cube_a": 0} and list(mixed.memory_declined) == ["cyl_b"]
    assert both.execute.args["object_id"] == "cube_a"
    assert "followed a recalled" in both.rationale

    nothing = MemoryPolicy()
    untouched = nothing._choose(payload([recall("wrench_z")], ready))
    assert untouched.execute.args["object_id"] == "cube_a"
    assert nothing.recalled_ids == ["exp_1"] and nothing.memory_order == {"wrench_z": 0}
    assert "followed a recalled" not in untouched.rationale
    # and the row still names its own plan promise: reordering changed which debt is worked, never
    # what the action for a debt is
    assert moved.execute.skill == refuted.execute.skill == "pick"
    assert moved.execute.subgoal == "s_cyl_b" and refuted.execute.subgoal == "s_cube_a"


def test_the_followed_order_is_reported_in_the_trace_the_log_is_read_from(episodes):
    """The trace says what was on the page and whether it was followed, so a `full` vs
    `wo_episodic_memory` difference can be attributed to a recall rather than to a mood.

    `memory_followed` is only ever true when the row the agent acted on is one the recall named, and
    that implication *is* the assertion: a rationale claiming to have followed a memory that
    promoted nothing is the failure this column exists to catch in a batch.
    """
    for name, ep in episodes.items():
        for entry, payload in zip(ep.policy.trace, ep.summaries):
            rows = (payload.get("working_memory") or {}).get("recalled") or []
            assert entry["recalled"] == [row["experience_id"] for row in rows], name
            owed = {str(row.get("predicate_id") or "")
                    for row in (payload.get("plan") or {}).get("rows") or []
                    if str(row.get("satisfied")) != "true"}
            remembered = {str((step.get("args") or {}).get("object_id"))
                          for row in rows for step in row["provenance"].get("steps") or []}
            # The two columns are a partition of what the recalls named, and each one is justified
            # by the claim it was made from rather than by the row's boolean.
            assert set(entry["memory_order"]) | set(entry["memory_declined"]) <= remembered, \
                (name, entry)
            assert not set(entry["memory_order"]) & set(entry["memory_declined"]), (name, entry)
            for object_id, claims in entry["memory_declined"].items():
                assert claims and not any(claim in owed for claim in claims), (name, entry)
            if entry["memory_followed"]:
                assert "followed a recalled" in entry["rationale"], (name, entry)
                assert entry["args"] and entry["args"]["object_id"] in entry["memory_order"], \
                    (name, entry)
            else:
                assert "followed a recalled" not in entry["rationale"], (name, entry)


def test_the_memory_policy_costs_nothing_in_any_arm(episodes):
    """P3's dev loop is measurable before a request is billed, exactly as P2's was."""
    for name, ep in episodes.items():
        assert ep.result.http_requests == 0, name
        assert ep.policy.http_requests == 0, name
        assert ep.policy.decisions == len(ep.policy.seen) == len(ep.policy.trace), name
        assert ep.result.terminal_status in (TerminalStatus.success, TerminalStatus.failed), name
        assert ep.result.decision_rounds > 0 and ep.result.skill_calls > 0, name


# ------------------------------------------------------------------ the joined pair ----
def test_the_camera_channel_and_the_store_join_without_losing_either(tmp_path):
    """§9's memory arm on a camera channel: one episode, two modules, neither reported as the other.

    Two things have to hold at once, and the second is the reason the first was not free. The
    builder still refuses a camera it was not handed — `build_episodic_arm` will not invent a
    perceiver, because a second opinion about the segmentation catalogue is a second experiment.
    Given one, the runner's joined branch builds the camera through
    `perception/arm.py:build_perceiver`, and the episode files `perception`-channel records *and*
    the store's records, retrieves from the store on every round, and writes its residue once.
    `wo_vlm` is what a `stub` channel can coherently claim (it consults no vision model), so the
    arm this row runs is §9's "limited semantic baseline" over the whole system rather than over
    the planning loop alone.

    The old name of this test said the pair was refused, and it was: before §13's closed loop the
    runner raised `not wired to channel 'stub'` rather than half-build a perceiver. The refusal was
    the right shape of caution in the wrong place — a memory arm that cannot run on a camera cannot
    produce §9's `full` row at all.
    """
    root = str(tmp_path)
    experience_store = ExperienceStore.load(os.path.join(root, "s2.jsonl"))
    summary = _run_batch_episode(root, experience_store, "wo_vlm", policy="memory",
                                 perceive="stub")

    # The channel is a camera, and it is *recorded* as one: the run's own arm says `wo_vlm` and its
    # `modules_off` names the model that this channel never consulted.
    assert summary["perception"]["channel"] == "stub"
    assert summary["episodic"]["arm"] == "wo_vlm"
    # The store was queried on every round and written once, which is the memory arm's whole
    # footprint, and it is the same footprint the privileged episode leaves.
    block = summary["episodic"]
    assert block["retrievals"] == block["memory_events"]["memory_retrieval"] > 0, block
    assert block["written_experience_id"] and block["store_size_after"] == \
           block["store_size_before"] + 1, block
    assert ExperienceStore.load(experience_store.path).ids() == [block["written_experience_id"]]
    with open(os.path.join(summary["artifacts"]["episode_dir"], "events.jsonl"),
              encoding="utf-8") as fh:
        types = sorted({json.loads(line)["type"] for line in fh if line.strip()})
    assert "memory_write" in types and "memory_retrieval" in types and "ablation" in types
    # A `stub` read is a zero-cost look: frames on disk, no request anywhere.
    assert summary["result"]["http_requests"] == 0
    assert os.path.isdir(os.path.join(summary["artifacts"]["episode_dir"], "perception"))
    assert summary["result"]["decision_rounds"] > 0 and summary["result"]["skill_calls"] > 0

    # And the builder still refuses the half-connected loop it cannot assemble by itself.
    case = find_case(CASE)
    with pytest.raises(ValueError, match="perceiver"):
        build_episodic_arm(case=case, scene=None, executor=None, store=None,
                           run_dir=root, episode_id="x", budgets=case.budgets,
                           perceive="stub",
                           experience_store=ExperienceStore.load(
                               os.path.join(str(tmp_path), "s.jsonl")))
    assert not os.path.exists(os.path.join(str(tmp_path), "s.jsonl"))
