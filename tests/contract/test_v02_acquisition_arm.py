"""SPEC-v0.2 §5.6/§7/§9/§12.3 contract tests: the acquisition arm's seams, its gate, and the exact
thing a reuse row is allowed to say.

This file is about the *wire*, and the wire has four failure modes that all produce a result that
looks like a finding:

* a gap detected from a source other than the episode's own records would be §9's prohibition on a
  runtime repairing its own capability set, written into the loop and reported as acquisition;
* an offer that appeared and disappeared by some rule other than "a program exists" would make
  `full` vs `wo_skill_acquisition` a comparison of two payload shapes;
* a record filed by the disarmed arm would put an owned event type into a log whose own
  `Ablation.violations()` says none may be there — the registry is the referee, and it is a check on
  the *log*, not on this module's source;
* and a reuse claim read off a rationale instead of the executed actions is the one thing §12.3
  names twice.

So the tests come in three layers. The static ones prove the arm's footprint on the loop (two
overrides, four new names) and prove the two claims the module's own docstring *argues*: that only
the four `skill_*` records are filed here (an AST scan of the `_file`/`_file_record` call sites), and
that no shipped control policy reads the `skills` section at all (an AST scan of the two policy
modules' string constants) — which is why `counts_as_reuse` is withheld on a rule batch rather than
reported as zero. The pure-helper ones pin the readers (`executed_actions`, `measured_predicates`,
`bind_effect`, `match_procedure`, `offer_rows`) against hand-made logs, because the cases they need
— a retried step, an unmeasured predicate, a program the budget never reached — are cases a scene
will not be rebuilt to supply. The episode ones run real physics: a gap, a candidate, a validation,
a refusal, an offer, and the same episode with the library withheld.

Two numbers are deliberately never hard-coded: how many instances a validation runs (P4-c's matrix
answers that, from what the world offers) and whether a given candidate passes. What *is* asserted
about the verdicts is that the frozen rule and the strict reading are reported apart and that each
refusal carries its own reasons, because the pair apart is §11's row and a fused "passed" would be
one claim standing in for two.

The warm library every offer test needs is admitted through the production gate — real sandbox
physics, `SkillMemory.admit`, no hand-written line — because a fixture the writer cannot produce
would test the reader against a shape that never occurs. That is also why the fixture *fails* loudly
instead of skipping if the gate refuses the seed program: an unbuildable warm library means §11's
offer and reuse rows have no evidence, which is a result to report rather than a test to hide.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import shutil
from collections import namedtuple
from typing import get_args

import pytest

from embodied_agent.acquisition import program
from embodied_agent.acquisition import validation as V
from embodied_agent.acquisition.arm import (
    CATALOGUE_KEY,
    OFFER_BUDGET_CHARS,
    OFFER_DISCLAIMER,
    OFFER_DISCLAIMER_KEY,
    OFFER_READ_BY,
    PIPELINE,
    REUSE_NAMING_REASON,
    SKILL_ARMS,
    AcquiredPlannedRuntime,
    AcquiredPerceptRuntime,
    AcquisitionMixin,
    bind_effect,
    build_skill_arm,
    executed_actions,
    match_procedure,
    measured_predicates,
    offer_rows,
    procedure_steps,
)
from embodied_agent.acquisition.memory import SkillMemory
from embodied_agent.acquisition.sandbox import SandboxScene
from embodied_agent.core.contracts import DecisionExecute, SkillName
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime, build_world_state
from embodied_agent.core.skills import SkillExecutor, SkillRegistry
from embodied_agent.core.v02 import (
    ABLATION_CONDITIONS,
    EVENT_MODULE,
    SCHEMA_VERSION,
    V02_EVENT_TYPES,
    SkillCandidate,
    SkillSpec,
    ablation,
)
from embodied_agent.episodic.arm import (
    EPISODIC_ARMS,
    EpisodicMixin,
    ExperiencedPerceptRuntime,
    ExperiencedPlannedRuntime,
)
from embodied_agent.episodic.store import ExperienceStore
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.run import (
    InfraError,
    _row_of,
    build_scene,
    resolve_goal,
    run_group,
    run_one_episode,
    task_input,
)
from embodied_agent.evaluation.tasks import SET_NAMES, build_set, find_case
from embodied_agent.perception.arm import PerceptRuntime, arm_for_channel, build_perceiver
from embodied_agent.perception.grounding import GroundingMap
from embodied_agent.planning.arm import PLANNING_ARMS, PlanningMixin
from embodied_agent.planning.policy import PlanPolicy

#: a case whose own records show a repeated failure, so box 1 fires and every downstream box has
# something to report on. `expected="bounded_exit"`: the episode is *supposed* to struggle.
GATE_CASE, GATE_KIND = "pr_target_full", "protocol"
#: a case that carries out `pick` then `place`, so a stored two-step program can be matched in the
# actual action order and its declared effect measured by this episode's own verifier.
OFFER_CASE, OFFER_KIND = "dev_c1", "dev"
#: the case the seed program is validated on. Not one of the two above: a program offered for the
# very task it was validated on would be a reuse of the episode that made it, and a different claim.
SEED_CASE, SEED_OBJECT, SEED_TARGET = "smoke_clean", "obj_green_1", "tray_left"

OWNED = tuple(t for t, owner in EVENT_MODULE.items() if owner == "skill_acquisition")
MEMORY_OWNED = tuple(t for t, owner in EVENT_MODULE.items() if owner == "episodic_memory")
Episode = namedtuple("Episode", "condition runtime policy result events s4 types by_type pages "
                     "report summary")


# ------------------------------------------------------------------ the two entries ----
def _spec():
    """The seed program: `pick` then `place`, two parameters, one declared effect.

    Written here rather than read from a file so the test says what it is offering; it is validated
    and admitted by the production gate below, never by an insert.
    """
    return program.program(
        "move_object", "pick the object, then place it in the named tray",
        [program.param("object", "entity", description="the object to move"),
         program.param("target", "region", description="the tray to put it in")],
        [program.step("pick", {"object_id": "{object}"}, step_id="s0", on_failure="retry_once"),
         program.step("place", {"object_id": "{object}", "target_id": "{target}"}, step_id="s1")],
        preconditions=["the gripper is empty and the object is reachable"],
        expected_effects=["placed:{object}:{target}"],
        termination_conditions=["the placement predicate measures true, or the budget is spent"],
        applicability=["one object, one tray"])


@pytest.fixture(scope="module")
def library_root(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("acquisition_library"))


@pytest.fixture(scope="module")
def warm_library(library_root) -> str:
    """A store with one admitted program, produced by the gate that guards the store."""
    path = os.path.join(library_root, "warm.jsonl")
    spec = _spec()
    scene = SandboxScene.from_task(SEED_CASE)
    proposal = V.Proposal(scene=scene, params={"object": SEED_OBJECT, "target": SEED_TARGET})
    matrix = V.default_matrix(spec, proposal)
    validation = V.validate_candidate(spec, proposal, matrix, candidate_id=f"cand_{spec.name}")
    if not validation.passed_strict:
        pytest.fail(f"the production gate refused the seed program: frozen="
                    f"{validation.passed_frozen} strict={list(validation.strict)}; there is no "
                    f"honest way to build a warm library for these tests")
    memory = SkillMemory(path)
    candidate = SkillCandidate(spec=spec, proposed_by="test-seed", status="sandbox",
                               candidate_id=f"cand_{spec.name}")
    entry = memory.admit(candidate, validation.report)
    assert entry.admission["passed_frozen_rule"] is True
    assert entry.admission["strict_findings"] == []
    assert memory.names() == ["move_object"] and memory.refusals == []
    return path


def _copy(root: str, warm_library: str, name: str) -> str:
    """A private copy of the seed store, so one test's admission cannot appear in another's page."""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{name}.jsonl")
    shutil.copyfile(warm_library, path)
    if os.path.exists(f"{warm_library}.refusals.jsonl"):
        shutil.copyfile(f"{warm_library}.refusals.jsonl", f"{path}.refusals.jsonl")
    return path


def _store_bytes(path: str) -> dict[str, str]:
    """Every byte the skill memory owns, hashed: the store, its refusals, and nothing else."""
    out = {}
    for candidate in (path, f"{path}.refusals.jsonl"):
        if os.path.exists(candidate):
            with open(candidate, "rb") as fh:
                out[os.path.basename(candidate)] = hashlib.md5(fh.read()).hexdigest()
    return out


def _arm(case_id: str, condition: str, library_path: str, root: str, *,
         kind: str, validation_budget: int = 1) -> Episode:
    """One real episode through the one production acquisition builder, at zero spend.

    `build_skill_arm` rather than `run_one_episode` because the page each round was answered from is
    an object under test and the runner does not hand it back. The cost of that choice is the
    runner's own trigger, so `summary` is assembled here from the three fields the pipeline actually
    reads (`episode_id`, `result`, `artifacts.episode_dir`); the production trigger is exercised by
    the `run_one_episode` tests below.
    """
    case = find_case(case_id)
    scene = build_scene(case)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, world)
        episode_id = f"{case_id}.{condition}"
        ep_dir = os.path.join(root, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        runtime = build_skill_arm(
            case=case, scene=scene, executor=executor, store=EpisodeStore(ep_dir, episode_id),
            run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets, perceive="privileged",
            ablation=ablation(condition),
            environment=EnvironmentController(scene, case.fresh_events()),
            skill_memory=SkillMemory.load(library_path),
            experience_store=ExperienceStore.load(
                os.path.join(root, f"{episode_id}.experiences.jsonl")),
            task_kind=kind, validation_budget=validation_budget)
        executor.world_provider = runtime.observe
        policy = PlanPolicy()
        result = runtime.run_episode(task, goal, policy, mode="B")
        events = runtime.store.read_all()
        s4 = runtime.store.read_all(SCHEMA_VERSION)
        by_type: dict[str, list[dict]] = {}
        for e in events:
            by_type.setdefault(str(e["type"]), []).append(e)
        summary = {"episode_id": episode_id, "artifacts": {"episode_dir": ep_dir},
                   "result": result.model_dump(mode="json")}
        report = runtime.acquire_from_episode(summary, run_ref=ep_dir)
        return Episode(condition, runtime, policy, result, events, s4,
                       sorted({str(e["type"]) for e in s4}), by_type,
                       [ctx.model_payload() for ctx in policy.seen], report, summary)
    finally:
        scene.close()


def _camera_arm(case_id: str, condition: str, library_path: str, root: str, *,
                kind: str, store_path: str) -> Episode:
    """The same episode, assembled by hand on a camera, so the page is an object under test.

    `_arm` builds the privileged loop and this builds the joined one; the only differences are the
    two objects `perception/arm.py:build_perceiver` returns and the channel word. That is the point
    of the pair: a reader can diff the two helpers and see that joining the camera changed which
    runtime class exists and nothing about how the episode is driven.
    """
    case = find_case(case_id)
    scene = build_scene(case)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, world)
        episode_id = f"{case_id}.{condition}.cam"
        ep_dir = os.path.join(root, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        perceiver, gmap = build_perceiver(case=case, scene=scene, run_dir=ep_dir,
                                          episode_id=episode_id, perceive="stub",
                                          catalog=cell_catalog(scene.trays.values()))
        runtime = build_skill_arm(
            case=case, scene=scene, executor=executor, store=EpisodeStore(ep_dir, episode_id),
            run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets, perceive="stub",
            ablation=ablation(condition), perceiver=perceiver, gmap=gmap,
            environment=EnvironmentController(scene, case.fresh_events()),
            skill_memory=SkillMemory.load(library_path),
            experience_store=ExperienceStore.load(store_path), task_kind=kind)
        executor.world_provider = runtime.observe
        policy = PlanPolicy()
        result = runtime.run_episode(task, goal, policy, mode="B")
        events = runtime.store.read_all()
        s4 = runtime.store.read_all(SCHEMA_VERSION)
        by_type: dict[str, list[dict]] = {}
        for e in events:
            by_type.setdefault(str(e["type"]), []).append(e)
        summary = {"episode_id": episode_id, "artifacts": {"episode_dir": ep_dir},
                   "result": result.model_dump(mode="json")}
        report = runtime.acquire_from_episode(summary, run_ref=ep_dir)
        return Episode(condition, runtime, policy, result, events, s4,
                       sorted({str(e["type"]) for e in s4}), by_type,
                       [ctx.model_payload() for ctx in policy.seen], report, summary)
    finally:
        scene.close()


def _batch(case_id: str, condition: str, library_path: str, root: str, *, kind: str,
           validation_budget: int = 1, repeat: int = 0,
           perceive: str = "privileged", experience_store=None) -> dict:
    """One episode through the *runner*, which is the only place the production trigger lives."""
    case = find_case(case_id)
    resolution = resolve_goal(case, repeat, None, root)
    assert resolution.error is None, resolution.error
    return run_one_episode(case, repeat, "B", resolution, None, root, kind, frames=False,
                           perceive=perceive, ablation=ablation(condition), policy="payload",
                           skill_memory=SkillMemory.load(library_path),
                           experience_store=experience_store,
                           validation_budget=validation_budget)


def _summary_at(root: str, episode_id: str) -> dict:
    with open(os.path.join(root, "episodes", episode_id, "episode_summary.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def _log_types(root: str, episode_id: str) -> list[str]:
    with open(os.path.join(root, "episodes", episode_id, "events.jsonl"),
              encoding="utf-8") as fh:
        return [json.loads(line)["type"] for line in fh if line.strip()]


# ============================================================ the arm's footprint ====
def test_the_mixture_overrides_two_existing_seams_and_adds_four_names():
    """The arm's whole footprint on the loop, read off the class rather than the docstring.

    Two of the mixin's functions are names `Runtime` already had (`_begin_episode`, and
    `_skill_catalogue` — the v0.1 seam whose result the base loop already copies into
    `DecisionContext.model_payload()["skills"]`), and four are names nothing else in the loop owns.
    A third override would mean §5.6 reaches somewhere §7 does not route acquisition through; a
    fifth new name would be a seam this file's header does not describe.
    """
    own = {n for n, v in vars(AcquisitionMixin).items() if inspect.isfunction(v)}
    assert own & set(dir(Runtime)) == {"_begin_episode", "_skill_catalogue"}
    assert own == {"_begin_episode", "_skill_catalogue", "acquire_from_episode", "_validate",
                   "_admit", "_reuse_rows"}
    assert AcquiredPlannedRuntime.__mro__[:5] == (AcquiredPlannedRuntime, AcquisitionMixin,
                                                  ExperiencedPlannedRuntime, EpisodicMixin,
                                                  PlanningMixin)
    assert Runtime in AcquiredPlannedRuntime.__mro__
    # The loop itself is untouched: no new round, no new execution path, no new decision.
    for name in ("run_episode", "observe", "initial_context"):
        assert getattr(AcquiredPlannedRuntime, name) is getattr(Runtime, name), name


def test_the_skill_arm_is_the_memory_arm_plus_one_gate():
    """§9's `w/o Skill Acquisition` has to be reachable from the stacked arm by one word.

    The equalities are the claim: every arm P2 and P3 could run is still runnable here and the only
    addition is the acquisition module. A tuple that *replaced* rather than extended would silently
    retire an earlier phase's contrast.
    """
    assert SKILL_ARMS == EPISODIC_ARMS + ("wo_skill_acquisition",)
    assert EPISODIC_ARMS == PLANNING_ARMS + ("wo_episodic_memory",)
    assert "wo_skill_acquisition" not in PLANNING_ARMS and "wo_skill_acquisition" not in EPISODIC_ARMS
    assert "wo_skill_acquisition" in ABLATION_CONDITIONS
    assert list(ablation("wo_skill_acquisition").modules_off) == list(
        ABLATION_CONDITIONS["wo_skill_acquisition"])
    assert list(ablation("full").modules_off) == []
    # one module, and only that one: the plan, the ledger and the memory survive the gate
    assert ablation("wo_skill_acquisition").enabled("skill_acquisition") is False
    assert ablation("wo_skill_acquisition").enabled("episodic_memory") is True
    assert ablation("wo_skill_acquisition").enabled("task_planning") is True
    assert ablation("full").enabled("skill_acquisition") is True


def test_the_registry_owns_exactly_four_records_and_the_vocabulary_is_still_frozen():
    assert set(OWNED) == {"skill_gap", "skill_candidate", "skill_validation", "skill_library"}
    assert all(EVENT_MODULE[t] == "skill_acquisition" for t in OWNED)
    assert set(OWNED) <= set(V02_EVENT_TYPES)
    # 15: the frozen event vocabulary. A new record type is a contract change and a re-freeze, not a
    # module detail, so the count is asserted rather than the set being read back as its own proof.
    assert len(V02_EVENT_TYPES) == 15
    # And the phases do not own each other's records: this arm's gate cannot be blamed for a
    # `memory_*` row, and the memory arm's gate cannot hide a `skill_*` one.
    assert not set(OWNED) & set(MEMORY_OWNED)


def _filed_types(classname: str) -> list[str]:
    """Every literal event type the class passes to `_file`/`_file_record`, read from the AST.

    Read from the source rather than from a run because the claim is about *reachability*: a
    `skill_*` record the arm could file on the disarmed path would show up in one episode's log and
    not in another's, and the log is the weaker evidence of the two.
    """
    path = os.path.join(os.path.dirname(build_skill_arm.__code__.co_filename), "arm.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != classname:
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("_file", "_file_record") and call.args
                    and isinstance(call.args[0], ast.Constant)):
                out.append(str(call.args[0].value))
    return out


def test_the_arm_files_those_four_records_and_nothing_else():
    """The gate's power is exactly `EVENT_MODULE`'s list, proved against the source."""
    assert set(_filed_types("AcquisitionMixin")) == set(OWNED)
    assert _filed_types("AcquiredPlannedRuntime") == []


def test_no_shipped_control_policy_reads_the_skills_section():
    """`OFFER_READ_BY == ()` is a measurement, not a hedge.

    Both control policies that answer from the page on `--policy payload|memory` are scanned for
    every string constant — docstring included — and neither mentions `skills`. So on a rule batch
    the offer changes the page's bytes and not the behaviour, and a zero reuse rate on such a batch
    must not be read as an agent shown a library and ignoring it. The day a policy does read the
    section this test fails, and the constant becomes a list to fill in.
    """
    root = os.path.dirname(os.path.dirname(build_skill_arm.__code__.co_filename))
    for module in ("planning", "episodic"):
        with open(os.path.join(root, module, "policy.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        texts = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        hits = sorted({text for text in texts if "skills" in text})
        assert hits == [], (module, hits)
    assert OFFER_READ_BY == ()


def test_a_decision_cannot_name_an_acquired_program():
    """D49's evidence, read off the frozen contract rather than asserted in prose.

    `SkillName` is a `Literal` of the shipped primitives, so no decision, call or plan step can name
    a library entry and `reuse_named` is structurally 0. That is why the library reaches behaviour
    through the page, and why §11's reuse row is reported as three readings instead of one. Widening
    this Literal would move the schema fingerprint, the registry hash and the v0.1 baseline, so the
    assumption the arm is built on is pinned here rather than restated.
    """
    names = set(get_args(SkillName))
    assert {"observe", "pick", "place", "safe_retreat"} <= names
    assert "move_object" not in names and "pick_and_place" not in names
    assert "move_object" not in SkillRegistry.CATALOGUE
    with pytest.raises(Exception) as excinfo:
        DecisionExecute.model_validate({"skill": "move_object",
                                        "args": {"object_id": SEED_OBJECT}})
    assert "skill" in str(excinfo.value)


# ==================================================================== the pure readers ====
def _fb(event_id: str, **feedback) -> dict:
    return {"type": "execution_feedback", "event_id": event_id,
            "payload": {"feedback": feedback}}


def test_only_actions_that_reached_the_executor_are_actions():
    """§12.3's 以实际动作为准, as a filter over the log.

    A refused decision, a `skill_call` intention and a finish feedback are all in the log and none of
    them is a move. The arguments come back from `entity_id`/`target_id` — what the feedback records
    about the call it answers — so an index the feedback does not corroborate is absent rather than
    assumed.
    """
    events = [
        _fb("e1", executed=True, skill="pick", entity_id="obj_a", status="completed",
            decision_id="d1"),
        _fb("e2", executed=False, skill="place", entity_id="obj_a", target_id="tray_l",
            status="rejected", decision_id="d2"),
        _fb("e3", executed=True, skill=None, status="finish_accepted"),
        {"type": "skill_call", "event_id": "e4", "payload": {"skill": "pick"}},
        _fb("e5", executed=True, skill="place", entity_id="obj_a", target_id="tray_l",
            status="completed", decision_id="d5"),
    ]
    actions = executed_actions(events)
    assert [(a["skill"], a["args"], a["event_id"]) for a in actions] == [
        ("pick", {"object_id": "obj_a"}, "e1"),
        ("place", {"object_id": "obj_a", "target_id": "tray_l"}, "e5")]
    assert actions[0]["decision_id"] == "d1" and actions[1]["status"] == "completed"


def test_an_unmeasured_predicate_is_absent_and_a_late_verdict_wins():
    """Two readings a careless reader would collapse: last-wins, and no report ≠ `false`.

    A placement made, undone and made again ends the episode `true`, which is what §12.3's 终态 asks
    for. A predicate nobody ever asked the verifier about is not in the dict at all, and the effect
    row reports that as `measured: null` with a reason rather than as a failure.
    """
    def report(pid, value):
        return {"predicate_id": pid, "value": value}

    events = [
        _fb("e1", executed=True, skill="place", verification={"reports": [
            report("placed:obj_a:tray_l", "false")]}),
        {"type": "goal_revision", "event_id": "e2", "payload": {"reports": [
            report("placed:obj_a:tray_l", "true")]}},
        {"type": "finish_check", "event_id": "e3", "payload": {"reports": [
            report("placed:obj_a:tray_l", "true"), report("held:obj_a", "unknown")]}},
    ]
    values = measured_predicates(events)
    assert values["placed:obj_a:tray_l"] == "true"
    assert values["held:obj_a"] == "unknown"
    assert "placed:obj_b:tray_l" not in values


def test_an_unbound_effect_is_not_a_verdict():
    """`bind_effect` returns the unbound names *instead of* a predicate.

    §12.4 refuses a verdict on an instance that never ran, and this is the same error one level
    earlier: an effect string still holding `{target}` is not a claim anybody measured, and counting
    it as one would turn an unbound template into a result.
    """
    assert bind_effect("placed:{object}:{target}", {"object": "a", "target": "b"}) == \
        ("placed:a:b", [])
    assert bind_effect("placed:{object}:{target}", {"object": "a"}) == \
        ("placed:a:{target}", ["target"])
    assert bind_effect("held:{object}", {}) == ("held:{object}", ["object"])
    assert bind_effect("no placeholders here", {"object": "a"}) == ("no placeholders here", [])


def test_the_matcher_is_an_ordered_subsequence_under_one_binding():
    """The four things a procedure match may and may not say.

    It is a subsequence, so a step that declares `retry_once` and was carried out twice still
    matches — contiguity would call the program's own recovery a failure to follow it. Every
    parameter binds to one value across the whole program, a literal argument has to be found
    literally, and the *earliest* consistent run wins, because picking a later one would be choosing
    with an eye on the outcome.
    """
    spec = _spec()
    assert procedure_steps(spec) == [
        ("pick", {"object_id": "{object}"}, "retry_once"),
        ("place", {"object_id": "{object}", "target_id": "{target}"}, "abort")]

    def act(skill, event_id, **args):
        return {"skill": skill, "args": dict(args), "event_id": event_id, "status": "completed"}

    retried = [act("pick", "e1", object_id=SEED_OBJECT), act("pick", "e2", object_id=SEED_OBJECT),
               act("place", "e3", object_id=SEED_OBJECT, target_id=SEED_TARGET)]
    hit = match_procedure(spec, retried)
    assert hit["matched"] and hit["binding_complete"] and not hit["unbound_parameters"]
    assert hit["params"] == {"object": SEED_OBJECT, "target": SEED_TARGET}
    # The retry's own event is not part of the run the matcher cites: `e1`+`e3` is the program, and
    # `attempted_from` is where the scan that produced it began.
    assert hit["action_event_ids"] == ["e1", "e3"] and hit["attempted_from"] == 0

    # The retry's first attempt failed and the program was still carried out: the matcher reports
    # the recurrence and leaves the verdict to the effect row.
    assert match_procedure(spec, [dict(r, status="failed") for r in retried])["matched"]

    reordered = [act("place", "e1", object_id=SEED_OBJECT, target_id=SEED_TARGET),
                 act("pick", "e2", object_id=SEED_OBJECT)]
    assert not match_procedure(spec, reordered)["matched"]
    assert "no ordered run" in match_procedure(spec, reordered)["why_not"]

    # Two objects, one program each: the run the first started cannot be finished by the second,
    # because `{object}` already means `obj_green_1`; the matcher restarts and says so.
    two_objects = [act("pick", "e1", object_id=SEED_OBJECT),
                   act("place", "e2", object_id="obj_blue_2", target_id=SEED_TARGET),
                   act("pick", "e3", object_id="obj_blue_2"),
                   act("place", "e4", object_id="obj_blue_2", target_id=SEED_TARGET)]
    second = match_procedure(spec, two_objects)
    assert second["matched"] and second["action_event_ids"] == ["e3", "e4"]
    # The scan that succeeded began at the second action: `attempted_from` is the start of the scan,
    # not the index of the first matched step, and the run it found cannot begin before it.
    assert second["attempted_from"] == 1
    assert second["params"] == {"object": "obj_blue_2", "target": SEED_TARGET}

    # The declared target is a literal in the program, and this episode put it somewhere else.
    literal = program.program(
        "move_to_right", spec.description, [spec.parameters[0]],
        [program.step("pick", {"object_id": "{object}"}, step_id="s0"),
         program.step("place", {"object_id": "{object}", "target_id": "tray_right"}, step_id="s1")],
        preconditions=spec.preconditions, expected_effects=["placed:{object}:tray_right"],
        termination_conditions=spec.termination_conditions, applicability=spec.applicability)
    miss = match_procedure(literal, retried)
    assert not miss["matched"] and miss["params"] == {}

    # A parameter the executed actions never supplied: matched, and honestly *not* complete.
    half_bound = program.program(
        "pick_only", spec.description, list(spec.parameters),
        [program.step("pick", {"object_id": "{object}"}, step_id="s0")],
        preconditions=spec.preconditions, expected_effects=["held:{object}"],
        termination_conditions=spec.termination_conditions, applicability=spec.applicability)
    row = match_procedure(half_bound, [act("pick", "e1", object_id=SEED_OBJECT)])
    assert row["matched"] and not row["binding_complete"]
    assert row["unbound_parameters"] == ["target"]

    empty = SkillSpec(name="nothing", level="acquired")
    assert not match_procedure(empty, retried)["matched"]
    assert match_procedure(empty, retried)["why_not"] == \
        "the program declares no executable step"


def test_the_offer_is_bounded_carries_its_own_disclaimer_and_offers_nothing_when_empty():
    """The page's shape, and the pair §5.1 asks for: an empty section is `{}`, not a key whose value
    is an empty dict.

    `offered + dropped` is the whole library and the budget only ever cuts a suffix, so a dropped
    program is a count a reader can see rather than an absence. Both renderings travel together
    because they answer two different questions — `procedure` is the machine-readable step list,
    `view` is the line a model skims — and neither is a paraphrase of the other: both are read off
    the stored spec.
    """
    assert offer_rows([])["section"] == {}
    assert offer_rows([])["offered"] == [] and offer_rows([])["dropped"] == []

    spec = _spec()
    other = program.program("move_second", "another program", list(spec.parameters),
                            list(spec.procedure), preconditions=spec.preconditions,
                            expected_effects=list(spec.expected_effects),
                            termination_conditions=spec.termination_conditions,
                            applicability=spec.applicability)
    # Measured, and worth the assertion: this program's offer row is ~1.3 kB, so a *two*-program
    # library already does not fit the 2 kB offer. The budget is not a hypothetical limit.
    full = offer_rows([other, spec], budget=4000)
    assert full["offered"] == ["move_object", "move_second"] and full["dropped"] == []
    assert full["budget"] == 4000 and full["chars"] <= full["budget"]
    assert set(full["section"][CATALOGUE_KEY]) == {"move_object", "move_second"}
    assert full["chars"] > OFFER_BUDGET_CHARS
    row = full["section"][CATALOGUE_KEY]["move_object"]
    assert row[OFFER_DISCLAIMER_KEY] == OFFER_DISCLAIMER
    assert [s["skill"] for s in row["procedure"]] == ["pick", "place"]
    assert row["procedure"][0]["args"] == {"object_id": "{object}"}
    assert row["expected_effects"] == ["placed:{object}:{target}"]
    assert row["view"] == program.render(spec) and "move_object" in row["view"]
    assert not {"name", "skill_id"} & set(row), "the offer must not smuggle an addressable id in"

    at_default = offer_rows([other, spec])
    assert at_default["budget"] == OFFER_BUDGET_CHARS
    assert at_default["offered"] == ["move_object"] and at_default["dropped"] == ["move_second"]
    assert set(at_default["section"][CATALOGUE_KEY]) == {"move_object"}
    assert at_default["chars"] <= at_default["budget"]
    # What the budget cuts is a suffix of the alphabetical order, never a program chosen for being
    # the weaker of the two: `offered + dropped` is always the whole library.
    assert sorted(at_default["offered"] + at_default["dropped"]) == ["move_object", "move_second"]


# ================================================ an armed episode, through the runner ----
@pytest.fixture(scope="module")
def batch_root(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("acquisition_batch"))


@pytest.fixture(scope="module")
def armed_cold(batch_root, library_root) -> tuple[dict, str]:
    """`full` on a cold library, through `run_one_episode` — the production trigger."""
    path = os.path.join(library_root, "armed_cold.jsonl")
    summary = _batch(GATE_CASE, "full", path, os.path.join(batch_root, "armed_cold"),
                     kind=GATE_KIND)
    return summary, path


def test_a_real_gap_a_real_candidate_a_real_validation_and_a_refusal_are_all_filed(armed_cold):
    """§5.6's boxes 1-6, all of them, on one episode that struggled for a reason.

    The claim worth the physics is the *agreement*: the arm's own counters, the log the arm wrote and
    the store the arm refused to write into are three separate counts of the same events, and a
    report that disagreed with its own log would be a report about a pipeline that never ran.
    `refusals_in_store` is the one the arm cannot fake — the runner reads it back off
    `SkillMemory.refusals`, and this is the regression test for the day it counted twice per attempt.
    """
    summary, path = armed_cold
    block = summary["skill_acquisition"]
    assert block["arm"] == "full" and block["armed"] is True and block["module_off"] is False
    assert "not_attempted" not in block or block["not_attempted"] is None
    assert block["proposer"] == "rule" and block["detector"] and block["proposer_version"]

    store = EpisodeStore(summary["artifacts"]["episode_dir"], summary["episode_id"])
    events = store.read_all()
    counts = {t: sum(1 for e in events if e["type"] == t) for t in OWNED}
    counts4 = {t: sum(1 for e in store.read_all(SCHEMA_VERSION) if e["type"] == t) for t in OWNED}
    assert block["events_filed"] == counts == counts4 == block["events_filed_in_log"]
    assert all(counts[t] >= 1 for t in OWNED), counts

    # One candidate, one validation, one refusal: the double count this pinned is the one the arm
    # could only have produced by calling `refuse` after `admit` had already recorded the attempt.
    assert len(block["candidates"]) == len(block["validations"]) == len(block["refusals"]) == 1
    assert block["refusals_in_store"] == len(block["refusals"]) == 1
    # Where that count lives is a fact worth naming rather than glossing: it is the *live* store's,
    # and the durable record is one line in the refusals sidecar. `SkillMemory.load` restores
    # entries and not refusals — a reloaded store reports zero for a count that is a per-attempt
    # denominator inside one episode — so an offline reader rebuilds the refusals from the file.
    memory = SkillMemory.load(path)
    assert memory.refusals == [] and memory.names() == [] and block["store_size_after"] == 0
    with open(f"{path}.refusals.jsonl", encoding="utf-8") as fh:
        filed = [json.loads(line) for line in fh if line.strip()]
    assert len(filed) == 1
    assert filed[0]["candidate_id"] == block["refusals"][0]["candidate_id"]
    assert filed[0]["reasons"] == block["refusals"][0]["reasons"]
    assert filed[0]["report_id"] == block["validations"][0]["report_id"]
    assert block["admissions"] == []

    assert block["pipeline"] == list(PIPELINE)
    assert block["gaps"] and block["gaps"][0]["attempts"] >= 2, block["gaps"]
    assert block["stage_failures"] == []

    # The two readings, apart, each with its own reasons; and the gate's decision traced to them.
    row = block["validations"][0]
    assert isinstance(row["passed_frozen_rule"], bool)
    assert isinstance(row["passed_strict_reading"], bool)
    assert not (row["passed_frozen_rule"] and row["passed_strict_reading"]), \
        "a candidate that passed both readings would have been admitted, and the store is empty"
    if not row["passed_frozen_rule"]:
        assert row["frozen_reasons"]
    if not row["passed_strict_reading"]:
        assert row["strict_findings"]
    assert row["kinds_covered"] and row["cost"]["model_calls"] == 0
    assert row["cost"]["wall_s"] and row["cost"]["sim_s"] and row["cost"]["calls"]
    assert row["instances"] >= row["instances_ran"] >= 1
    assert row["matrix_fingerprint"], "no fingerprint means no transfer claim (§11)"
    assert block["refusals"][0]["reasons"]
    assert block["refusals"][0]["candidate_id"] == row["candidate_id"]
    assert block["cost"]["validated_candidates"] == 1
    assert block["cost"]["wall_s"] == round(row["cost"]["wall_s"], 3)
    # Nothing was offered: the library was cold when the episode began.
    assert block["library_at_start"] == [] and block["offer_rounds"] == 0
    assert block["offer"] == {} and block["reuse"] == []
    assert block["reuse_not_measured_reason"] == REUSE_NAMING_REASON

    # The records are the arm's own: schema 4, tagged with the arm that filed them, and they are all
    # in the frozen vocabulary.
    for e in [e for e in store.read_all(SCHEMA_VERSION) if e["type"] in OWNED]:
        assert e["payload"]["arm"] == "full"
        assert e["payload"].get("episode_id", summary["episode_id"]) == summary["episode_id"]
    assert {str(e["type"]) for e in events if e["type"] in OWNED} == set(OWNED)

    # And the acquisition ran after the episode said it was over, like the memory write beside it.
    ends = [e["sequence"] for e in events if e["type"] == "episode_end"]
    skills = [e["sequence"] for e in events if e["type"] in OWNED]
    assert ends and min(skills) > max(ends), (min(skills), max(ends))

    # The report is in the artifact too, so an offline reader gets it without re-running anything.
    with open(os.path.join(summary["artifacts"]["episode_dir"], "episode_summary.json"),
              encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["skill_acquisition"]["events_filed"] == block["events_filed"]


def test_the_acquisition_report_reaches_the_batch_row():
    """`_row_of` is a spread of the summary, so the report survives into `rows.jsonl` by
    construction — and that construction is what this pins. A reader of the run directory then gets
    the gaps, candidates, refusals and costs without opening an episode directory."""
    summary = {"episode_id": "x", "result": {"terminal_status": "success"}}
    row = _row_of(summary)
    assert row["outcome"] == "success" and row["episode_id"] == "x"
    filled = dict(summary, skill_acquisition={"gaps": [{"gap_id": "g"}]})
    assert _row_of(filled)["skill_acquisition"] == filled["skill_acquisition"]


def test_a_validation_budget_of_zero_proposes_and_names_what_it_never_sandboxed(
        batch_root, library_root):
    """The budget is a denominator, not a silence (§5.1).

    With 0 the episode still finds the gap and still proposes, and the candidate it would have
    validated is written out as `not_validated` with its reason. The alternative — an empty
    validations list and nothing else — would make "the module was told not to spend" and "nothing
    needed validating" the same row, which is the pair §11's candidate-generation rate exists to
    separate.
    """
    path = os.path.join(library_root, "budget_zero.jsonl")
    summary = _batch(GATE_CASE, "full", path, os.path.join(batch_root, "budget_zero"),
                     kind=GATE_KIND, validation_budget=0)
    block = summary["skill_acquisition"]
    assert block["validation_budget"] == 0
    assert block["candidates"] and block["candidates_executable"] == \
        len([c for c in block["candidates"] if c["status"] == "sandbox"])
    assert block["validations"] == [] and block["admissions"] == [] and block["refusals"] == []
    assert len(block["not_validated"]) == block["candidates_executable"] >= 1
    assert "budget" in block["not_validated"][0]["why"]
    assert block["cost"]["validated_candidates"] == 0 and block["cost"]["wall_s"] == 0.0
    store = EpisodeStore(summary["artifacts"]["episode_dir"], summary["episode_id"])
    types = [e["type"] for e in store.read_all()]
    assert "skill_candidate" in types and "skill_validation" not in types
    assert not os.path.exists(path), "a refusal is a row; no attempt at all is no file"


# ================================================================ the disarmed arm ----
def test_the_disarmed_arm_files_none_of_the_four_and_changes_no_stored_byte(
        batch_root, warm_library):
    """§9's row, at its strongest: the library is installed and warm, and this arm touches nothing.

    Four separate negatives, because the arm could fail to be honest in four places. It files none
    of the four records — and `Ablation.violations()`, the referee the registry enforces itself with,
    agrees. It adds no line to the store and no line to the refusals file, byte for byte. It proposes
    nothing and validates nothing. And it still *reports*: a row with `module_off`, the five pipeline
    stages listed as `not_attempted`, and the two counts the armed path ends with filed as zeros, so
    the two arms differ by a finding and not by a shape.
    """
    path = _copy(os.path.join(batch_root, "disarmed"), warm_library, "disarmed")
    before = _store_bytes(path)
    assert SkillMemory.load(path).names() == ["move_object"]
    summary = _batch(GATE_CASE, "wo_skill_acquisition", path,
                     os.path.join(batch_root, "disarmed_run"), kind=GATE_KIND)
    block = summary["skill_acquisition"]

    store = EpisodeStore(summary["artifacts"]["episode_dir"], summary["episode_id"])
    types4 = sorted({str(e["type"]) for e in store.read_all(SCHEMA_VERSION)})
    assert [t for t in types4 if t in OWNED] == []
    assert ablation("wo_skill_acquisition").violations(types4) == []
    assert {t: 0 for t in OWNED} == block["events_filed"] == block["events_filed_in_log"]
    assert _store_bytes(path) == before
    assert SkillMemory.load(path).names() == ["move_object"]

    assert block["module_off"] is True and block["armed"] is False
    assert block["arm"] == "wo_skill_acquisition"
    assert block["not_attempted"] == list(PIPELINE)
    assert "switched off" in block["why"]
    assert block["gaps"] == [] and block["candidates"] == [] and block["validations"] == []
    assert block["admissions"] == [] and block["refusals"] == []
    assert block["offer"] == {} and block["offer_rounds"] == 0
    assert block["cost"]["wall_s"] == 0.0 and block["cost"]["validated_candidates"] == 0
    assert block["refusals_in_store"] == len(block["refusals"]) == 0
    # The store's own audit is filed on both arms, so a reader can see what was withheld.
    assert block["skill_memory_audit"]["admitted"] == 1
    assert block["skill_memory_path"] == path
    # `library_at_start` is the *installed* library, not the offered one: this is the only state in
    # which a program is present and withheld, and it is what the reuse rows below are computed over.
    assert block["library_at_start"] == ["move_object"]
    assert [r["library_offered"] for r in block["reuse"]] == [False]
    assert all(r["counts_as_reuse"] is False for r in block["reuse"])
    # and the episode keeps its own verdict: the gate does not decide whether the task ran.
    assert summary["result"]["terminal_status"] in ("success", "failed", "partial")


# ==================================================================== the page ----
def _page_text(payload: dict, episode_id: str, root: str) -> str:
    """The page with the three things an arm cannot control normalised away.

    Generated ids (`ctx_a1b2c3d4`), the episode id that prefixes every event id the page cites, and
    the directory the run wrote to. What is left is the information a decision was made from, which
    is the only part of the page §9's contrast is about.

    The id pattern is not anchored to whole JSON strings: a rationale *quotes* the goal it was read
    off ("read off goal g_101efb1a"), and an id inside that sentence is as uncontrolled as one on its
    own — a test that compared those two strings would be comparing two random draws.

    `wall_clock_remaining_s` is normalised to a name rather than compared, for the same reason and
    with the same limit: it is the episode's own budget countdown from real elapsed time, so two runs
    of one case differ by a decisecond of physics, which is not an arm. The *key* is left in place — a
    page that dropped the budget field would still fail here, as would one whose remaining
    *call* counts or `sim`-derived values differ, and those are the ones the loop controls.
    """
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    text = text.replace(episode_id, "EPISODE").replace(root, "ROOT")
    text = re.sub(r'"wall_clock_remaining_s": [0-9.]+', '"wall_clock_remaining_s": WALL', text)
    return re.sub(r"[a-z][a-z_]*_[0-9a-f]{8}", "ID", text)


def _first_difference(a: str, b: str) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return (f"at {i}:\n  armed    …{a[max(0, i - 90):i + 90]}…\n"
                    f"  disarmed …{b[max(0, i - 90):i + 90]}…")
    return f"lengths {len(a)} vs {len(b)}: …{a[min(len(a), len(b)):min(len(a), len(b)) + 160]}…"


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, Episode]:
    """Two runs of the same case, the same seed and the same decision maker, differing by the gate
    and by nothing else."""
    root = str(tmp_path_factory.mktemp("acquisition_pages"))
    cold = os.path.join(root, "cold.jsonl")
    warm = os.path.join(root, "empty_warm.jsonl")
    return {"armed_cold": _arm(GATE_CASE, "full", cold, os.path.join(root, "a"), kind=GATE_KIND),
            "disarmed_warm": _arm(GATE_CASE, "wo_skill_acquisition", warm,
                                  os.path.join(root, "b"), kind=GATE_KIND)}


def test_an_empty_library_returns_the_primitive_catalogue_itself(pages):
    """The byte-identity claim, checked as object identity where identity is what is on offer.

    With nothing to add, `_skill_catalogue` hands back `SkillRegistry.CATALOGUE` — the very object
    the base loop returns — so a `full` episode before any admission is not a *slightly different*
    payload from the P3 arm's, it is the same one. A program is added only when a program exists to
    add, which is the test after this one.
    """
    armed = pages["armed_cold"]
    assert armed.runtime._skill_catalogue() is SkillRegistry.CATALOGUE
    assert all(CATALOGUE_KEY not in p["skills"] for p in armed.pages)
    assert armed.report["offer_rounds"] == 0 and armed.report["offer"] == {}
    for page in armed.pages:
        assert set(page["skills"]) == set(SkillRegistry.CATALOGUE)


def test_the_disarmed_page_is_the_page_of_an_arm_with_nothing_to_offer(pages):
    """What makes a `full` vs `wo_skill_acquisition` difference attributable to acquisition.

    The same case, the same seed, the same decision maker: armed with a cold library and disarmed
    with a warm one have *identical* pages, round for round. So a difference between those two arms
    in a later batch came from a program that was admitted and offered, not from the loops being two
    loops. Key-set equality alone would not say that — a `null` where the other arm has no key would
    pass it.
    """
    armed, disarmed = pages["armed_cold"], pages["disarmed_warm"]
    assert len(armed.pages) == len(disarmed.pages) > 0
    assert [set(p) for p in armed.pages] == [set(p) for p in disarmed.pages]
    for index, (a, b) in enumerate(zip(armed.pages, disarmed.pages)):
        assert a["skills"] == b["skills"], index
        text_a = _page_text(a, f"{GATE_CASE}.full",
                            os.path.dirname(armed.runtime.store.path))
        text_b = _page_text(b, f"{GATE_CASE}.wo_skill_acquisition",
                            os.path.dirname(disarmed.runtime.store.path))
        assert text_a == text_b, _first_difference(text_a, text_b)
    # the gate is the only difference, and it is visible in the log rather than inferred. The two
    # reads below are deliberately not the same read: `types` is the log as the *loop* left it, and on
    # both arms no `skill_*` record exists while the episode is running. The acquisition trigger fires
    # once the loop is over — the sequence-number check in `test_a_real_gap…` pins that order — so the
    # armed arm's four records are looked for in the file as it stands after the report was written.
    assert [t for t in disarmed.types if t in OWNED] == []
    assert [t for t in armed.types if t in OWNED] == []
    filed = sorted({str(e["type"]) for e in armed.runtime.store.read_all(SCHEMA_VERSION)
                    if e["type"] in OWNED})
    withheld = sorted({str(e["type"]) for e in disarmed.runtime.store.read_all(SCHEMA_VERSION)
                       if e["type"] in OWNED})
    assert filed == sorted(OWNED) and withheld == []


def test_a_stored_program_is_offered_on_every_round(tmp_path_factory, warm_library):
    """The offer, on the page, inside a bounded section, with its disclaimer attached to each program.

    This is §5.6's last box reaching its first: the library is *shown*. It is not executed — no
    decision can name the program (`test_a_decision_cannot_name_an_acquired_program`) and no shipped
    policy reads the section (`test_no_shipped_control_policy_reads_the_skills_section`), so what
    this proves is that the information is on the page and that the page still fits its budget.
    """
    root = str(tmp_path_factory.mktemp("acquisition_offer"))
    warm = _arm(OFFER_CASE, "full", _copy(root, warm_library, "offer"), os.path.join(root, "armed"),
                kind=OFFER_KIND)
    block = warm.report
    assert block["library_at_start"] == ["move_object"]
    assert block["offer"]["offered"] == ["move_object"] and block["offer"]["dropped"] == []
    assert block["offer"]["chars"] <= block["offer"]["budget"] == OFFER_BUDGET_CHARS
    assert block["offer_rounds"] >= 1 and len(warm.pages) >= 1
    assert block["offer_read_by"] == []
    for page in warm.pages:
        offered = page["skills"][CATALOGUE_KEY]
        assert list(offered) == ["move_object"]
        row = offered["move_object"]
        assert [s["skill"] for s in row["procedure"]] == ["pick", "place"]
        assert row[OFFER_DISCLAIMER_KEY] == OFFER_DISCLAIMER
        assert "move_object" in row["view"]
    # and the shipped primitives are still on the page beside it: an offer does not replace them.
    assert set(SkillRegistry.CATALOGUE) < set(warm.pages[0]["skills"])


def test_the_withheld_library_differs_from_the_offered_one_by_one_flag(
        tmp_path_factory, warm_library):
    """The contrast's other half, and the reason `library_offered` exists.

    Same case, same warm library, gate off: nothing is offered, and the reuse rows are still
    computed — over the same installed program, read off the actions the episode really executed and
    this episode's own verifier. The two rows agree field for field except the flag, so a later
    difference between the arms cannot be blamed on the measurement differing as well.
    """
    root = str(tmp_path_factory.mktemp("acquisition_reuse"))
    offered = _arm(OFFER_CASE, "full", _copy(root, warm_library, "offered"),
                   os.path.join(root, "offered"), kind=OFFER_KIND)
    withheld_path = _copy(root, warm_library, "withheld")
    before = _store_bytes(withheld_path)
    withheld = _arm(OFFER_CASE, "wo_skill_acquisition", withheld_path,
                    os.path.join(root, "withheld"), kind=OFFER_KIND)

    assert withheld.report["offer_rounds"] == 0 and withheld.report["offer"] == {}
    assert all(CATALOGUE_KEY not in p["skills"] for p in withheld.pages)
    assert withheld.report["library_at_start"] == ["move_object"]
    assert _store_bytes(withheld_path) == before

    a, b = offered.report["reuse"], withheld.report["reuse"]
    assert len(a) == len(b) == 1 and [r["skill_name"] for r in a] == ["move_object"]
    assert a[0]["library_offered"] is True and b[0]["library_offered"] is False
    volatile = {"library_offered", "matched_action_event_ids"}
    assert {k: v for k, v in a[0].items() if k not in volatile} == \
           {k: v for k, v in b[0].items() if k not in volatile}
    # §12.3: the match is read off the actions that ran and the verifier's own verdict, and the two
    # are reported apart — `procedure_matched` is recurrence, `reuse_effect_achieved` adds the
    # measurement, and `counts_as_reuse` stays withheld on a batch whose decision maker cannot choose.
    assert a[0]["procedure_matched"] is True and a[0]["binding_complete"] is True
    # The binding is this episode's, not the seed's: `dev_c1` moves a blue object into `tray_right`
    # while the program was validated on `obj_green_1` into `tray_left`, so the recurrence this row
    # reports is a cross-instance one and the test says so. A matcher that echoed the parameters it
    # was offered would have produced the seed pair here, and looked exactly as good.
    assert a[0]["bound_parameters"] == {"object": "obj_blue_2", "target": "tray_right"}
    assert a[0]["bound_parameters"] != {"object": SEED_OBJECT, "target": SEED_TARGET}
    assert [e["measured"] for e in a[0]["effects"]] == ["true"]
    assert a[0]["reuse_effect_achieved"] is True
    assert a[0]["reuse_named"] is False and a[0]["counts_as_reuse"] is False
    assert a[0]["not_measured_reason"] == REUSE_NAMING_REASON
    assert a[0]["unbound_parameters"] == [] and a[0]["why_not_matched"] == ""
    assert a[0]["matched_action_event_ids"] and b[0]["matched_action_event_ids"]
    for row in a + b:
        assert set(row) == {"skill_name", "skill_id", "library_offered", "reuse_named",
                            "procedure_matched", "binding_complete", "bound_parameters",
                            "matched_action_event_ids", "unbound_parameters", "why_not_matched",
                            "effects", "reuse_effect_achieved", "counts_as_reuse",
                            "not_measured_reason"}


def test_a_runtime_with_no_library_says_nothing_and_an_in_memory_one_has_no_path():
    """Two absences that must not be confused with a zero.

    `skill_memory=None` is the module never installed: `acquire_from_episode` returns nothing at all,
    and the batch writes no `skill_acquisition` key — which is a different row from the cold-library
    one the armed episode above produces, where the module looked, found a gap, and reported it. The
    first branch is pinned against the function directly because `build_skill_arm` refuses to build
    such a runtime at all (`test_the_arm_and_the_runner_refuse...`), and a runtime constructed by hand
    is the only place it exists.

    The second half is the same distinction one level down: an in-memory store has no filename, and
    `report` must not invent one.
    """
    assert AcquisitionMixin.acquire_from_episode(
        type("NoMemory", (), {"skill_memory": None})(), {}) is None

    memory = SkillMemory(None)
    assert memory.path is None and memory.names() == [] and memory.refusals == []
    assert memory.audit()["admitted"] == 0
    assert memory.audit()["fingerprint"] == "in-memory"
    assert memory.specs() == [] and offer_rows(memory.specs())["section"] == {}
    with pytest.raises(Exception, match="no path"):
        memory.flush()


# ================================================= the joined camera (P5-a) ====
def test_one_camera_episode_carries_the_perceiver_the_store_and_the_library(tmp_path,
                                                                            warm_library):
    """§13's closed loop, on a channel that renders frames: five modules, one episode, no spend.

    This is the configuration §15 asks for and P4 could not run — the offer, the recalled rows, the
    plan and the working-memory ledger all on one page, on a world the agent came to know through a
    camera. It is worth naming what makes it a new fact rather than a relabelling: `full` on a
    `stub` channel is *not* §9's `full`, because the channel consulted no vision model, and the
    arm it can honestly wear is `wo_vlm` — §9's own second reading of that row, "a limited semantic
    baseline". So this row measures the memory and acquisition modules stacked on a semantically
    restricted world, which is the strongest form of the contrast available at zero spend, and the
    runtime says so in its own `ablation` record rather than letting the directory name decide.

    Four things are asserted, and each one is a different way the join could have been fake: the
    class the runner built (a camera runtime with both mixins), the records the camera owns
    (`observation` rows, no `perception` row, because a stub read renders no `PerceptionObservation`),
    the records the two installed modules own (retrieval and write, gap and candidate), and the page
    a round was actually answered from (it carries the offer *beside* the camera-derived plan).
    """
    root = os.path.join(str(tmp_path), "cam")
    library = _copy(root, warm_library, "cam")
    store_path = os.path.join(root, "cam.experiences.jsonl")
    before = _store_bytes(library)
    cam = _camera_arm(OFFER_CASE, "wo_vlm", library, root, kind=OFFER_KIND,
                      store_path=store_path)
    ep_dir = os.path.join(root, cam.summary["episode_id"])
    # The log, read *after* the trigger ran: `acquire_from_episode` is the episode's last act, so a
    # snapshot taken before it cannot show the four records it filed.
    types = [str(e["type"]) for e in
             EpisodeStore(ep_dir, cam.summary["episode_id"]).read_all()]

    # The channel: frames on disk, and the arm the channel can carry. A `stub` read renders no
    # `PerceptionObservation`, so no `perception` record may appear beside one.
    assert isinstance(cam.runtime, AcquiredPerceptRuntime)
    assert cam.runtime.perceiver.channel == "stub"
    assert cam.result.http_requests == 0
    assert os.path.isdir(os.path.join(ep_dir, "perception"))
    assert "perception" not in types and "observation" in types
    assert types.count("ablation") == 1 and cam.types.count("ablation") == 1

    # The store: queried on every round, and the writes are the arm's own records.
    assert "memory_retrieval" in types and "working_memory" in types
    assert len(cam.by_type["memory_retrieval"]) == cam.result.decision_rounds

    # The library: armed, and every record it counts is a record in the log it was written to.
    block = cam.report
    assert block["armed"] is True and block["module_off"] is False
    assert block["pipeline"] == list(PIPELINE)
    assert block["events_filed"] == {"skill_gap": types.count("skill_gap"),
                                     "skill_candidate": types.count("skill_candidate"),
                                     "skill_validation": types.count("skill_validation"),
                                     "skill_library": types.count("skill_library")}
    assert set(OWNED) & set(types), "the acquisition arm filed none of its records on a camera"
    assert [r["library_offered"] for r in block["reuse"]] == [True] * len(block["reuse"])

    # The page each round was answered from: the offer beside the shipped primitives, and the
    # memory section the camera episode still fills. This is §15's loop in one dict.
    assert cam.pages and block["offer_rounds"] == len(cam.pages)
    for page in cam.pages:
        offered = (page.get("skills") or {}).get(CATALOGUE_KEY)
        assert offered and list(offered) == ["move_object"], page.get("skills")
        assert offered["move_object"][OFFER_DISCLAIMER_KEY] == OFFER_DISCLAIMER
        assert set(SkillRegistry.CATALOGUE) <= set(page.get("skills") or {})
        assert "recalled" in (page.get("working_memory") or {})
        assert page.get("plan"), "a camera round reached the decider with no plan section"
    # The library it was *offered* is the library it left. A refusal is a different file, and this
    # episode does produce one: the gate refused the candidate the camera episode proposed, and
    # `SkillMemory.admit` wrote that refusal to its own sidecar before raising. So the assertion is
    # about the admitted set and the store's own bytes, not about "nothing on disk changed".
    assert SkillMemory.load(library).names() == ["move_object"]
    assert {os.path.basename(k) for k in _store_bytes(library)} == {
        os.path.basename(k) for k in before} | {"cam.jsonl.refusals.jsonl"}
    assert _store_bytes(library)["cam.jsonl"] == before["cam.jsonl"]


def test_the_runner_puts_the_same_join_on_disk_as_the_hand_built_arm(tmp_path,
                                                           warm_library):
    """The runner's camera branch and `build_skill_arm` agree, row for row, on one episode.

    A join that only worked when a test assembled it by hand would be a fixture, not a feature. So
    the same case, the same seed library and the same channel go through `run_one_episode`, and the
    two reports are compared on the keys the §11 table reads. The episode id differs (the runner
    names its own) and so does the wall clock; everything about *what the arm found* has to match,
    because both paths call the same builder.
    """
    root = os.path.join(str(tmp_path), "pair")
    hand_library = _copy(root, warm_library, "hand")
    run_library = _copy(root, warm_library, "run")
    hand = _camera_arm(OFFER_CASE, "wo_vlm", hand_library, os.path.join(root, "hand"),
                       kind=OFFER_KIND, store_path=os.path.join(root, "hand.exp.jsonl"))
    summary = _batch(OFFER_CASE, "wo_vlm", run_library, os.path.join(root, "run"),
                     kind=OFFER_KIND, perceive="stub",
                     experience_store=ExperienceStore.load(os.path.join(root, "run.exp.jsonl")))

    assert summary["perception"]["channel"] == "stub"
    assert summary["perception"]["arm"] == "wo_vlm" == hand.report["arm"]
    assert summary["episodic"]["arm"] == "wo_vlm"
    hand_log = EpisodeStore(os.path.join(root, "hand", hand.summary["episode_id"]),
                            hand.summary["episode_id"]).read_all()
    assert summary["episodic"]["retrievals"] == \
        sum(1 for e in hand_log if e["type"] == "memory_retrieval") == \
        summary["result"]["decision_rounds"]
    assert summary["skill_acquisition"]["events_filed"] == hand.report["events_filed"]
    assert summary["skill_acquisition"]["pipeline"] == list(PIPELINE)
    assert summary["skill_acquisition"]["library_at_start"] == ["move_object"]
    assert summary["result"]["http_requests"] == 0
    for key in ("gaps", "candidates", "validations", "admissions", "refusals"):
        assert len(summary["skill_acquisition"][key]) == len(hand.report[key]), (
            key, summary["skill_acquisition"][key], hand.report[key])
    # and the offer is on the runner's own row, not only on the hand-built page
    assert summary["skill_acquisition"]["offer_rounds"] == summary["result"]["decision_rounds"]


def test_the_joined_runtime_is_the_four_mixins_and_the_channel_arm_is_inferred_once_only():
    """Two static halves of the join, each of which a passing episode test could hide.

    The class: `AcquiredPerceptRuntime` has to sit on the camera runtime, not beside it, or the
    episode above would have run a privileged world with a perceiver bolted to the side. The
    ordering of the bases is the claim that the acquisition mixin sees an *experienced* runtime —
    gap detection reads the memory arm's records, so a re-sorted MRO would quietly un-stack them.

    The inferred arm: `arm_for_channel` exists so a camera batch with no operator-supplied `--ablation`
    still carries a record, and it must never fire when one was supplied. An inference that could
    overrule `--ablation full` would silently rewrite which of §9's rows a batch measured.
    """
    assert AcquiredPerceptRuntime.__mro__[:6] == (
        AcquiredPerceptRuntime, AcquisitionMixin, ExperiencedPerceptRuntime, EpisodicMixin,
        PlanningMixin, PerceptRuntime)
    assert issubclass(AcquiredPerceptRuntime, Runtime)
    assert arm_for_channel("stub") is not None and arm_for_channel("stub").condition == "wo_vlm"
    assert arm_for_channel("vlm").condition == "full"
    assert arm_for_channel("privileged") is None
    for channel in ("stub", "vlm", "privileged"):
        for condition in ABLATION_CONDITIONS:
            handed = ablation(condition)
            assert arm_for_channel(channel, handed) is handed, (channel, condition)


def test_a_perceiver_is_built_in_one_place_and_refuses_the_channel_that_has_no_camera(tmp_path):
    """`build_perceiver` is the only sentence in the repo that says how an episode's look is wired.

    Four refusals. Three are about which camera: a privileged world has no camera, and a perceiver
    over simulator state would be a second account of the same facts; an undeclared channel is a
    typo, and `build_arm` used to be the only place that could tell; `vlm` without an adapter is the
    substitution this gate has refused since P1-e — falling back to a stub would report the same
    episode under the name `full`. The fourth is about who supplies the target catalogue: this
    package may not read the scene's receptacle inventory to make one (§5.1's channel does not ask
    the simulator where the trays are), so a caller that wants a camera brings the catalogue the way
    `build_arm`'s callers always have. The three channel refusals are checked *before* it, which is
    why they are called here without one.
    """
    case = find_case(OFFER_CASE)
    scene = build_scene(case)
    try:
        with pytest.raises(ValueError, match="no camera to build"):
            build_perceiver(case=case, scene=scene, run_dir=str(tmp_path), episode_id="x",
                            perceive="privileged")
        with pytest.raises(ValueError, match="unknown perception channel"):
            build_perceiver(case=case, scene=scene, run_dir=str(tmp_path), episode_id="x",
                            perceive="kinect")
        with pytest.raises(ValueError, match="no vision model is configured"):
            build_perceiver(case=case, scene=scene, run_dir=str(tmp_path), episode_id="x",
                            perceive="vlm")
        with pytest.raises(ValueError, match="needs the caller's target catalogue"):
            build_perceiver(case=case, scene=scene, run_dir=str(tmp_path), episode_id="x",
                            perceive="stub")
        perceiver, gmap = build_perceiver(case=case, scene=scene, run_dir=str(tmp_path),
                                          episode_id="x", perceive="stub",
                                          catalog=cell_catalog(scene.trays.values()))
        assert perceiver.channel == "stub" and gmap is not None
        # The map is *returned* because the runtime must hold the same object the perceiver reads
        # through; two `from_objects` calls would agree today and drift tomorrow.
        again = GroundingMap.from_objects(case.objects)
        assert again.sha256() == gmap.sha256()
        with pytest.raises(ValueError, match="gmap"):
            build_skill_arm(case=case, scene=scene, executor=None, store=None,
                            run_dir=str(tmp_path), episode_id="x", budgets=case.budgets,
                            perceive="stub", perceiver=perceiver,
                            skill_memory=SkillMemory(os.path.join(str(tmp_path), "l.jsonl")))
    finally:
        scene.close()


# =================================================================== the refusals ----
def test_the_arm_refuses_a_camera_it_was_not_handed_and_an_uninstalled_library(tmp_path):
    """Two ways to build a half-connected loop, refused at both entries.

    `full` on this arm is plan + memory + library, and the perceiver belongs to
    `perception/arm.py`: `build_skill_arm` takes one it was handed and refuses to invent it, because
    an arm that quietly ignored the camera would report a privileged episode as a camera one. A
    library that is not installed is a separate refusal — `build_skill_arm` makes it because a cold
    start and "no module" are the pair §5.1 refuses to let collapse.

    P5 changed the *third* case this test used to hold. Before §13's closed loop, a camera batch with
    an installed library was refused outright (`not wired to channel 'stub'`); now it runs —
    `test_the_camera_channel_carries_...` below measures it — and what the runner refuses instead is
    the claim the channel cannot carry. That refusal comes from `PerceptRuntime.__init__`, which runs
    `arm_coherence` on the arm it was handed, so it is a `ValueError` from the constructor rather than
    an `InfraError` from `run_group`: a `full` arm on a `stub` channel consulted no vision model, and
    the object that would have reported it refuses to exist. Either way the batch writes nothing.
    """
    case = find_case(OFFER_CASE)
    store_path = os.path.join(str(tmp_path), "s.jsonl")
    with pytest.raises(ValueError, match="perceiver"):
        build_skill_arm(case=case, scene=None, executor=None, store=None,
                        run_dir=str(tmp_path), episode_id="x", budgets=case.budgets,
                        perceive="stub", skill_memory=SkillMemory(store_path))
    assert not os.path.exists(store_path)
    with pytest.raises(ValueError, match="SkillMemory"):
        build_skill_arm(case=case, scene=None, executor=None, store=None,
                        run_dir=str(tmp_path), episode_id="x", budgets=case.budgets,
                        perceive="privileged")
    root = os.path.join(str(tmp_path), "cam")
    with pytest.raises(ValueError, match="cannot run on channel 'stub'"):
        _batch(OFFER_CASE, "full", os.path.join(str(tmp_path), "cam.jsonl"), root,
               kind=OFFER_KIND, perceive="stub")
    assert not os.path.exists(os.path.join(str(tmp_path), "cam.jsonl"))
    # `resolve_goal` has already made the batch root and its `goal_resolutions` by the time the
    # runtime refuses, so the claim that survives is the one that matters: no episode directory, no
    # events, no per-episode artifacts.
    events = [os.path.join(dirpath, name) for dirpath, _, names in os.walk(root)
              for name in names if name in ("events.jsonl", "episode_summary.json")]
    assert not events, events


def test_the_runner_refuses_every_batch_configuration_could_not_mean(tmp_path):
    """Eight refusals, each before a run directory exists.

    These are the configurations a §9 table could be written with and could not be read: an unknown
    proposer, a model proposer with no asker, a multi-mode batch whose library is written by its own
    first half, `wo_skill_acquisition` with no library installed, and — since P5 joined the camera to
    the arms — the two ways a *camera* batch can still misreport an installed module. The last pair
    is not the old `not wired to channel` refusal: a stub channel with a library runs, and what it
    cannot run is the claim its channel contradicts (`full` on a channel that consults no vision
    model) or a gate on a module nobody installed. A refusal after the fact would leave a directory
    of rows that mean something else.
    """
    root = os.path.join(str(tmp_path), "never")
    memory = SkillMemory(os.path.join(str(tmp_path), "lib.jsonl"))

    def expect(message, modes=("B",), **kw):
        with pytest.raises(InfraError, match=message):
            run_group("smoke", modes=modes, out_root=root, limit=1, frames=False, **kw)
        assert not os.path.exists(root), message

    expect(r"unknown proposer 'magic'", skill_memory=memory, propose="magic")
    expect(r"--propose model needs a caller-supplied asker", skill_memory=memory, propose="model")
    expect(r"one mode", skill_memory=memory, modes=("B", "C"))
    expect(r"needs --skill-memory", ablation=ablation("wo_skill_acquisition"))
    expect(r"cannot carry arm 'full'", perceive="stub", ablation=ablation("full"),
           skill_memory=memory)
    expect(r"cannot carry arm 'full'", perceive="stub", ablation=ablation("full"),
           experience_store=ExperienceStore.load(os.path.join(str(tmp_path), "e.jsonl")))
    # The order of the two camera refusals is itself the contract: coherence is checked first, so a
    # stub batch that is *also* missing a module says the channel thing before the module thing. On
    # this channel only `wo_vlm` is ever coherent, so the installed-module gate below is reachable on
    # a camera only through `--perceive vlm`, which `channel_readiness` refuses one line earlier. The
    # gate is therefore asserted where it can be reached — on the privileged batch above.
    expect(r"cannot carry arm 'wo_episodic_memory'", perceive="stub",
           ablation=ablation("wo_episodic_memory"), skill_memory=memory)


def test_the_cli_gates_the_same_requests(tmp_path):
    """`--skill-memory`, `--propose` and `--validation-budget` at the entry point.

    The CLI is the only place a *negative* budget can arrive and the only place `--propose model`
    could turn a zero-spend arm into a billed one by accident, so both are refused before an
    argument object reaches the runner. The accepted case is checked too: a flag nobody can pass is
    the same defect as a flag nobody should pass.
    """
    import argparse

    from embodied_agent import cli

    parser = argparse.ArgumentParser()
    cli._add_perception_flags(parser)
    library = os.path.join(str(tmp_path), "lib.jsonl")

    def ask(*argv):
        return cli._perception_kwargs(parser.parse_args(list(argv)))

    kw, refusals = ask("--ablation", "wo_skill_acquisition", "--skill-memory", library)
    assert refusals == [] and kw["ablation"].condition == "wo_skill_acquisition"
    assert kw["skill_memory"].path == library
    assert kw["propose"] == "rule" and kw["validation_budget"] == 1

    _, refusals = ask("--ablation", "wo_skill_acquisition")
    assert any("--skill-memory" in r for r in refusals), refusals

    _, refusals = ask("--propose", "model", "--skill-memory", library)
    assert any("spends nothing" in r for r in refusals), refusals

    _, refusals = ask("--validation-budget", "-1", "--skill-memory", library)
    assert any("not a budget" in r for r in refusals), refusals

    # P5: a `stub` channel with a library is accepted, because the runner can now build the camera
    # the acquisition arm stacks on. `--ablation` is not needed to say so: the entry resolves a
    # camera to `wo_vlm`, the one claim this channel can make, and reports the arm it resolved to.
    kw, refusals = ask("--perceive", "stub", "--skill-memory", library)
    assert refusals == [], refusals
    assert kw["perceive"] == "stub" and kw["skill_memory"].path == library
    assert kw["ablation"].condition == "wo_vlm" and kw["ablation"].modules_off == ["vlm"]
    assert kw["propose"] == "rule" and kw["validation_budget"] == 1

    # The `vlm` channel is still refused, and refused for its own reason rather than for the
    # library's: §11's acquisition rows on a real vision model wait for a model in the config, and
    # this entry does not silently downgrade it to a stub to make the row appear.
    _, refusals = ask("--perceive", "vlm", "--skill-memory", library)
    assert any("no vision model is configured" in r for r in refusals), refusals

    # What the entry does refuse on a camera is the two incoherences the runner refuses, said the
    # same way: an arm the channel contradicts, and a gate on a module nobody installed.
    _, refusals = ask("--perceive", "stub", "--ablation", "full", "--skill-memory", library)
    assert any("never consults a vision model" in r for r in refusals), refusals

    _, refusals = ask("--perceive", "stub", "--ablation", "wo_skill_acquisition")
    assert any("needs --skill-memory" in r for r in refusals), refusals

    # A library file that exists and is not a store is refused rather than half-read.
    broken = os.path.join(str(tmp_path), "broken.jsonl")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("not json\n")
    _, refusals = ask("--skill-memory", broken)
    assert any("cannot be read" in r for r in refusals), refusals


def test_the_sandbox_can_build_a_case_registered_after_the_freeze():
    """The `lh_c*` and `em_c*` sets were added after the frozen manifest, and the pipeline runs on them.

    `SandboxScene.from_task` originally resolved a task through `find_case`'s default set list, so an
    episode of a post-freeze set could find a gap, propose a program and then be unable to build the
    world to validate it in. The refusal was a `KeyError` naming the sets the sandbox could see — the
    right shape of error in the wrong place: a sandbox that can only reproduce the pre-freeze sets
    cannot validate this phase's episodes at all.
    """
    for case in build_set("long_horizon")[:2]:
        scene = SandboxScene.from_task(case.task_id)
        assert scene.label == case.task_id and scene.origin.startswith("frozen case")
    assert "long_horizon" not in ("smoke", "dev", "formal", "protocol")
    assert "long_horizon" in SET_NAMES
