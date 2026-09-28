"""The MuJoCo channel's §10 invariants, plus two episodes that run them on the real loop.

What this file is for. The claim `embodied_agent/benchmark_mujoco` makes is not "the
benchmark can be solved" — that is one assertion below, and it is checked against the
benchmark's own flag, not this channel's. The claim is that a second environment can be
attached to the production loop *without* the attachment doing the agent's thinking: no
plan, no retry, no composition, and above all no route from the evaluator's `success`
number into anything the agent is shown. Each test below closes one of those routes by
attacking it — most of them by *poisoning* the score and checking that a verdict does
not move, which is the only kind of independence test that can fail.

Two substitutions only, both inherited from the rest of the repository: the decision
source is `MujocoPlanPolicy` (a rule, so the file costs nothing and is reproducible),
and the world is the real MetaWorld instance, because a fake world cannot prove anything
about a benchmark adapter. Everything else is production code — the real `Runtime`, the
real `PlanningMixin`, the real `EpisodeStore`, the real `write_manifest`.

Skipped, not failed, on an interpreter without metaworld: this file needs
`/home/czx/mwvenv/bin/python`, and the desktop suite must stay runnable without it.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

# MuJoCo picks its OpenGL platform at import time, and `import metaworld` below imports
# mujoco through gymnasium. Without this line the offscreen renderer is dead and every
# frame in this file comes back None — measured, not assumed.
os.environ.setdefault("MUJOCO_GL", "osmesa")

import pytest  # noqa: E402

# `_ScriptedSurvey` stands in for the transport half of a real adapter, and
# `perceive._request_errors` finds the failure type on `type(adapter).__module__` -- so a double
# that does not expose `LLMError` here makes the survey's `except` match nothing but `OSError`,
# and a refused survey escapes as a bare `LLMError:` string instead of a `PerceptionUnavailable`
# with its evidence. Imported at module level for exactly that reason, not for convenience.
from embodied_agent.adapters.deepseek import LLMError  # noqa: E402, F401

pytest.importorskip("metaworld", reason="the MuJoCo channel needs /home/czx/mwvenv")

from embodied_agent.benchmark_mujoco.cli import build_parser, main  # noqa: E402
from embodied_agent.benchmark_mujoco.env import (  # noqa: E402
    BackendError,
    MuJoCoBackend,
    TASKS,
)
from embodied_agent.benchmark_mujoco.gui import GuiSink  # noqa: E402
from embodied_agent.benchmark_mujoco.planned_runtime import (  # noqa: E402
    mw_arm_coherence,
)
from embodied_agent.benchmark_mujoco.runtime import MujocoRuntime  # noqa: E402
from embodied_agent.benchmark_mujoco.runner import run_one_episode  # noqa: E402
from embodied_agent.benchmark_mujoco.skills import CATALOGUE, render  # noqa: E402
from embodied_agent.benchmark_mujoco.state import (  # noqa: E402
    TOLERANCE_VERSION,
    MujocoVerifier,
    carried,
    distance_m,
    goal_from_assignment,
    mw_state_diff,
    mw_world_state,
)
from embodied_agent.core.contracts import (  # noqa: E402
    Decision,
    DecisionExecute,
    PredicateVerdict,
    VerifyConfig,
)
from embodied_agent.core.v02 import Ablation, Status, Subgoal  # noqa: E402
from embodied_agent.core.v02 import ablation as make_ablation  # noqa: E402
from embodied_agent.planning.subgoals import placement_predicate  # noqa: E402

ENV = "peg-insert-side-v3"
OBJECT_ID, TARGET_ID = "peg 1", "socket 1"
#: the evaluator's own words. Wherever one of these appears is a place the benchmark's
#: score was read, so the count and the location of those places *is* the §10 claim.
SCORE_WORDS = ("official_success", "reward_total", "obj_to_target", "grasp_success")
#: the two records a runner writes *after* the loop, which are therefore allowed to read
#: the evaluator; every other record is something the agent was shown while deciding.
POST_LOOP_EVENTS = ("termination", "episode_official")


@pytest.fixture()
def backend():
    """One started instance, with the reset-height measurement the runner takes.

    Function-scoped on purpose: several tests below move the world, and a shared one
    would make each test's answer depend on the order they ran in."""
    b = MuJoCoBackend(ENV, task_index=0, seed=0, max_env_actions=500, views=("corner2",))
    b.start()
    b.init_z = {eid: float(xyz[2]) for eid, xyz in b.measured()["sites"].items()}
    yield b
    b.close()


def _world(b: MuJoCoBackend, version: int = 1):
    return mw_world_state(b, version, f"obs_{version:04d}", init_z=b.init_z)


def _goal():
    return goal_from_assignment(OBJECT_ID, TARGET_ID, TASKS[ENV], state_version=0)


# ------------------------------------------------------- the score stays outside --

def test_measured_channel_names_no_score(backend):
    """`measured()` is the perception channel and `eval_view()` is the scoreboard; the
    two functions are separate because the facts they return have separate owners."""
    m = backend.measured()
    assert set(m) == {"hand", "gripper_open_m", "sites", "workspace", "sim_time"}
    text = backend.public_text()
    for word in ("success", "reward", "obj_to_target", "grasp_success"):
        assert word not in text
    v = backend.eval_view()
    assert "official_success" in v and "reward_total" in v


def test_a_poisoned_score_cannot_move_a_verdict(backend, monkeypatch):
    """The independence test. Every official field is flipped to a winning value while
    the peg is still lying on the table; the verifier, the world state and the goal
    progress must not notice, because none of them reads that dict."""
    world = _world(backend)
    before = MujocoVerifier(world, VerifyConfig()).progress(_goal())
    assert before[0].value is not PredicateVerdict.true

    def poisoned():
        return {"env_steps": int(backend.steps), "max_env_actions": int(backend.max_env_actions),
                "env_done": True, "official_success": True, "reward_total": 99.0,
                "obj_to_target_m": 0.0, "grasp_success_official": 1.0, "error": None,
                "reset_seconds": 0.0, "primitive_seconds": 0.0,
                "evaluator": "poisoned for a test"}
    monkeypatch.setattr(backend, "eval_view", poisoned)
    after = MujocoVerifier(_world(backend, 2), VerifyConfig()).progress(_goal())
    assert after[0].value is before[0].value
    assert "99" not in json.dumps(after[0].model_dump(mode="json"), default=str)
    assert "success" not in json.dumps([e.model_dump(mode="json")
                                        for e in _world(backend, 3).entities], default=str)


def test_the_horizon_is_a_budget_and_the_flag_is_not_an_observation(backend):
    """`env_steps` may be shown (it is a budget the agent is told about); `success` may
    not. Both come out of the same dict, so the split has to be made at the reader."""
    view = backend.eval_view()
    assert {"env_steps", "max_env_actions"} <= set(view)
    assert view["official_success"] is False


# ------------------------------------------------------------- geometry, honestly --

def test_tolerance_is_derived_from_modelled_sizes_not_the_official_threshold(backend):
    world = _world(backend)
    v = MujocoVerifier(world, VerifyConfig())
    tol = v._tolerance(OBJECT_ID, TARGET_ID)
    assert tol != 0.07, "this channel's tolerance must not be the benchmark's threshold"
    target = world.target(TARGET_ID)
    peg = next(e for e in world.entities if e.entity_id == OBJECT_ID)
    radius = min(peg.geometry.half_extents.y, peg.geometry.half_extents.z)
    assert tol == round(target.inner_half + radius, 4)
    assert TOLERANCE_VERSION == "mw-site-and-object-size-v1"


def test_carried_needs_both_a_gap_and_a_lift():
    """A peg beside the hand at its rest height is not being carried: the two measured
    facts are the claim, and neither one is the evaluator's `grasp_success`."""
    assert carried((0.0, 0.0, 0.20), (0.0, 0.0, 0.19), init_z=0.03) is True
    assert carried((0.0, 0.0, 0.03), (0.0, 0.0, 0.19), init_z=0.03) is False
    assert carried((0.0, 0.0, 0.30), (0.9, 0.9, 0.9), init_z=0.03) is False


def test_a_resting_object_is_not_claimed_to_be_supported(backend):
    """Nothing here reads MuJoCo's contact list, so the only support this channel may
    name is the one it measures as a lift. "table" was a guess about a peg that had not
    been picked up, and a false one the moment the same peg was seated in the socket."""
    world = _world(backend)
    peg = next(e for e in world.entities if e.entity_id == OBJECT_ID)
    assert peg.supported_by in ("hand", "unknown")
    assert peg.support.support_id in ("hand", None)


def test_state_diff_measures_a_displacement_between_two_snapshots(backend):
    pre = _world(backend, 1)
    h = backend.hand()
    backend.servo((h[0], h[1] + 0.15, h[2]), -1.0, budget=60)
    post = _world(backend, 2)
    diff = mw_state_diff(pre, post)
    assert diff["moved"], "a servo that moved the hand must move a measured entity"
    assert not any(word in json.dumps(diff) for word in SCORE_WORDS)


# ------------------------------------------------- one vocabulary for plan and row --

def test_the_verifier_answers_the_id_the_derivation_writes():
    """The defect this guards: the plan row is authored as `placed:<object>:<target>` by
    `planning/subgoals.py`, and this channel once answered `in2:...`. Two names for one
    claim means the row can never be discharged, which reads as an arm failure."""
    pid = MujocoVerifier.predicate_id(OBJECT_ID, TARGET_ID)
    assert pid == placement_predicate(OBJECT_ID, TARGET_ID) == f"placed:{OBJECT_ID}:{TARGET_ID}"


def test_evidence_for_answers_a_placement_row_and_no_other_kind(backend):
    world = _world(backend)
    v = MujocoVerifier(world, VerifyConfig())
    row = Subgoal(subgoal_id="s1", statement=f"{OBJECT_ID} is seated in {TARGET_ID}",
                  predicate_id=placement_predicate(OBJECT_ID, TARGET_ID),
                  target_entity_ids=[OBJECT_ID], target_region_id=TARGET_ID)
    answer = v.evidence_for(row, world)
    assert answer is not None, "a placement row this snapshot measures must be answered"
    verdict, refs, unmeasured = answer
    assert verdict in (PredicateVerdict.true, PredicateVerdict.false)
    assert refs == [world.observation_ref] and unmeasured == []
    assert v.evidence_for(Subgoal(subgoal_id="s2", kind="maintain", statement="a sentence"),
                          world) is None
    assert v.evidence_for(row, world.model_copy(update={"entities": []})) is None


def test_distance_is_measured_to_the_body_that_reaches_the_target(backend):
    """The named point of a 0.24 m rod sits outside the hole even when the rod is
    seated, so a point-to-point distance would call a finished task false."""
    world = _world(backend)
    ends = [f for f in world.facts if f.kind == "extent" and f.subject == OBJECT_ID]
    assert len(ends) == 2, "the object's two measured ends are facts of this snapshot"
    assert distance_m(world, OBJECT_ID, TARGET_ID) is not None


# ------------------------------------------------------------------- the actuator --

def test_a_frame_this_channel_cannot_render_is_recorded_not_raised(backend):
    """The one failure mode that could take a whole batch with it: a renderer that
    aborts the process instead of raising. `capture()` has no windowed fallback for
    exactly that reason, so a camera this host cannot draw comes back as a recorded None
    and the episode carries on."""
    assert backend.capture(backend.views[0]) is not None
    assert backend.capture_error is None
    assert backend.capture("no-such-camera") is None
    assert backend.capture_error, "the reason has to be written down somewhere"
    assert backend.capture(backend.views[0]) is not None, "one bad name does not kill the camera"


def test_the_adapter_stops_at_whichever_horizon_is_smaller():
    """Two horizons exist: the bench's own (`max_path_length`, 500 steps, and `env.step`
    faults past it) and the one this run asks for. `done` is the minimum, and `servo`
    checks it every step — measured here rather than read off the code, because the
    obvious assumption ("the adapter would crash on the 501st step") is false."""
    b = MuJoCoBackend(ENV, task_index=0, seed=0, max_env_actions=1000, views=("corner2",))
    b.start()
    result = b.servo((0.45, 0.9, 0.45), -1.0, budget=900, tol=1e-9)
    assert b.done and result["converged"] is False
    assert b.steps == 500, "the bench's horizon, not the 1000 this run asked for"
    assert b.eval_view()["error"] is None, "stopping at a horizon is not a fault"
    b.close()


def test_stepping_past_the_bench_horizon_is_an_outcome_not_a_traceback():
    """The backstop behind the backstop: if anything ever does call `env.step` past
    `max_path_length`, `_one_step` turns the resulting `ValueError` into a `BackendError`
    with a phase and a type, which is what `_backend_termination` reports as
    infrastructure instead of blaming the model (SPEC-BST 6.3)."""
    import numpy as np
    b = MuJoCoBackend(ENV, task_index=0, seed=0, max_env_actions=1000, views=("corner2",))
    b.start()
    while not b.done:
        b._one_step(np.zeros(4))
    with pytest.raises(BackendError):
        b._one_step(np.zeros(4))
    assert b.eval_view()["error"]["type"] == "horizon"
    b.close()


def test_the_adapter_cap_is_a_stop_and_not_a_fault():
    b = MuJoCoBackend(ENV, task_index=0, seed=0, max_env_actions=6, views=("corner2",))
    b.start()
    result = b.servo((0.4, 0.4, 0.4), -1.0, budget=50)
    assert b.steps == 6 and result["converged"] is False
    assert b.eval_view()["error"] is None
    b.close()


# ------------------------------------------------------------------- two episodes --

def _events(ep_dir: str) -> list[dict]:
    with open(os.path.join(ep_dir, "events.jsonl"), encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_unarmed_episode_files_a_clean_artifact(tmp_path):
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=False, ablation=None, gui="gui", gui_every=50)
    assert s["episode_id"].endswith("__unarmed")
    assert s["ablation"] is None and s["armed"] is False
    assert s["official_success"] is True and s["termination_reason"] == "AGENT_FINISH"
    assert s["decisions"] == 3 and s["skill_calls_executed"] == 2
    assert s["env_steps"] < s["horizon"] and s["run_error"] is None
    assert s["frame_capture_error"] is None, "the viewer's camera worked, and says so"
    ep = os.path.join(str(tmp_path), "episodes", s["episode_id"])
    # one manifest per episode: a run may hold several layouts, and a run-root copy can
    # name only one of them (measured: `--layouts 0-1` filed the whole run as layout 1)
    manifest = json.load(open(os.path.join(ep, "manifest.json"), encoding="utf-8"))
    assert manifest["ablation"] == "unarmed"
    assert manifest["episode_id"] == s["episode_id"] and manifest["task_index"] == 0
    assert manifest["model"] == "rule" and manifest["prompt_version"] == "no-prompt"
    # `write_manifest` runs its fields through `_redact`, and a field named "credentials"
    # is redacted even though the text it carries already said it records nothing. The
    # assertion is about the guarantee that survived: no credential in the artifact.
    assert manifest["credentials"] == "***redacted***"
    assert "not recorded" in manifest["environment_variables"]
    assert not any(word in json.dumps(manifest) for word in ("sk-", "api_key"))
    events = _events(ep)
    types = {e["type"] for e in events}
    assert {"observation", "decision", "skill_call", "execution_feedback",
            "finish_check", "termination"} <= types
    for e in events:
        if e["type"] in POST_LOOP_EVENTS:
            continue
        hit = [w for w in SCORE_WORDS if w in json.dumps(e)]
        assert not hit, f"{e['type']} carries {hit}: a record the agent was shown holds a score"


def test_armed_episode_discharges_its_own_achieve_row(tmp_path):
    """The end-to-end half of the vocabulary claim: on a `full` episode the plan's one
    `achieve` row must reach `done` and leave the owed list, from this channel's own
    measurement — and the episode must still be won by the benchmark's flag."""
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="gui",
                        gui_every=50)
    assert s["official_success"] is True and s["run_error"] is None
    ep = os.path.join(str(tmp_path), "episodes", s["episode_id"])
    events = _events(ep)
    plans = [e for e in events if e["type"] == "plan"]
    assert plans, "a full episode must file its plan"
    assert any(e["type"] in ("working_memory", "obligation_check") for e in events)
    page = json.load(open(os.path.join(ep, "gui", "state.json"), encoding="utf-8"))
    achieve = next(r for r in page["plan"]["subgoals"] if r["kind"] == "achieve")
    assert achieve["status"] == Status.done.value, page["plan"]["subgoals"]
    owed = next(e["payload"]["owed_subgoals"] for e in events
                if e["type"] == "obligation_check")
    assert not any(o["kind"] == "achieve" for o in owed), \
        f"the placement row is still owed: {owed}"
    # A promise is linked to a row only through the decision's own `execute.subgoal`. While
    # this channel put its rationale text there, every episode ended owing two commitments
    # that no instrument could settle — the debt was an artefact of the policy's silence.
    check = [e["payload"] for e in events if e["type"] == "obligation_check"][-1]
    assert check["open_commitments"] == [], check["open_commitments"]
    assert check["unresolved_obligations"] == 0, check["unresolved_obligations"]
    named = [t.get("row") for t in s["decision_trace"] if t.get("action") == "execute"]
    assert named and all(named), f"a round that acted named no row: {named}"


def test_wo_planning_owes_what_only_a_plan_could_name(tmp_path):
    """The two arms differ where the instrument differs, and not by accident.

    With planning off there is no plan on the page, so this channel's policy names no row
    for its round (`policy._row_aimed` returns None) and the commitments its own skill calls
    wrote can never be settled. The episode is still won by the benchmark's flag — which is
    exactly why the ledger row and the score row are reported separately rather than merged
    into one "success" number (SPEC-v0.2 §11, §10).
    """
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=make_ablation("wo_planning"), gui="none")
    assert s["official_success"] is True and s["run_error"] is None
    events = _events(os.path.join(str(tmp_path), "episodes", s["episode_id"]))
    assert not [e for e in events if e["type"] == "plan"], "the arm that is off files nothing"
    check = [e["payload"] for e in events if e["type"] == "obligation_check"][-1]
    assert check["plan_events"] == 0
    assert len(check["open_commitments"]) == 2, check["open_commitments"]
    assert all("names no row" in " ".join(c["unmeasured"]) for c in check["open_commitments"])
    assert [t.get("row") for t in s["decision_trace"] if t.get("action") == "execute"] == [None, None]


def test_three_layouts_are_three_episodes_not_one_replay(tmp_path):
    """Different layouts start the peg at different measured poses; the loop is
    unchanged, so what differs is the world."""
    zs = []
    for layout in (0, 7, 23):
        b = MuJoCoBackend(ENV, task_index=layout, seed=layout, max_env_actions=500)
        b.start()
        zs.append(tuple(b.measured()["sites"][OBJECT_ID]))
        b.close()
    assert len(set(zs)) == 3, f"layouts must differ in the measured pose, got {zs}"


def test_each_layout_in_a_multi_layout_run_files_its_own_provenance(tmp_path):
    """A batch run holds N episodes, so the manifest that names `task_index` has to be
    one of N. Filed at the run root it was the last episode's, and a reader of
    `mw_probe`-shaped evidence would have believed a two-layout run was a one-layout run.
    """
    for layout in (0, 2):
        s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=layout,
                            seed=layout, horizon=120, armed=False, ablation=None)
        ep = os.path.join(str(tmp_path), "episodes", s["episode_id"])
        m = json.load(open(os.path.join(ep, "manifest.json"), encoding="utf-8"))
        assert m["task_index"] == layout and m["seed"] == layout
        assert m["run_id"] == os.path.basename(os.path.normpath(str(tmp_path)))
    assert not os.path.exists(os.path.join(str(tmp_path), "manifest.json")), \
        "no run-root copy left behind to disagree with the episodes"


# --------------------------------------------------------------------- the viewer --

def test_the_gui_page_carries_the_agents_own_columns(backend, tmp_path):
    sink = GuiSink(os.path.join(str(tmp_path), "gui"), every_steps=1, view="corner2",
                   live=lambda: {"round": 1, "decisions": ["r1 execute pick"], "events": []})
    sink.publish(backend)
    assert os.path.exists(os.path.join(str(tmp_path), "gui", "latest_corner2.png"))
    state = json.load(open(os.path.join(str(tmp_path), "gui", "state.json"), encoding="utf-8"))
    assert state["frames_published"] == 1 and state["decisions"] == ["r1 execute pick"]
    assert state["measured"][OBJECT_ID] == list(backend.measured()["sites"][OBJECT_ID])
    for word in ("official", "success", "reward"):
        assert word not in json.dumps(state), "the live page must not carry the scoreboard"


def test_the_cli_refuses_to_label_an_unarmed_run_as_a_condition():
    """A v0.1 loop has no arm, so naming a condition on it would file the same episode
    twice under two words."""
    ns = build_parser().parse_args(["run", "--unarmed", "--ablation", "no_goal",
                                    "--out", "/tmp/never-written"])
    assert ns.fn(ns) == 2
    assert not os.path.exists("/tmp/never-written"), "a refusal must not leave a batch behind"


def test_the_cli_names_an_arm_the_registry_can_check(monkeypatch, tmp_path):
    """A condition is a pair (name, modules), and `Ablation` rejects a half of one.

    The CLI built `Ablation(condition="wo_planning")` and so met a value error it then
    reported as "not a registered arm" — which sent the reader to the registry, where the
    condition is. Three of the seven arms were unrunnable through this door while `full`
    looked fine. The probe patches the episode out, so what is under test is the CLI's own
    construction and gate, not the simulator.
    """
    import embodied_agent.benchmark_mujoco.runner as runner_mod
    seen: list[dict] = []
    monkeypatch.setattr(runner_mod, "run_one_episode",
                        lambda **kw: seen.append(kw) or {
                            "episode_id": "probe", "official_success": False, "env_steps": 0,
                            "skill_calls_executed": 0, "decisions": 0,
                            "termination_reason": None, "wall_clock_s": 0.0,
                            "run_error": None, "obj_to_target_m": None,
                            # the CLI prints this key, so the stand-in has to carry it: a
                            # stub missing a field the reader prints tests a shape the
                            # runner never produces
                            "model_usage": {"http_requests": 0, "prompt_tokens": 0,
                                            "completion_tokens": 0}})
    for condition, modules in (("wo_planning", ["planning"]),
                               ("wo_working_memory", ["working_memory"]),
                               ("wo_replanning", ["replanning"])):
        rc = main(["run", "--ablation", condition, "--layouts", "0",
                   "--out", os.path.join(str(tmp_path), condition)])
        assert rc == 0, f"{condition} was refused by the CLI"
        arm = seen[-1]["ablation"]
        assert arm.condition == condition and arm.modules_off == modules
        assert seen[-1]["armed"] is True
        assert seen[-1]["planner"] == "rule", "the default seat is the zero-spend one"
    # the CLI's job stops at naming the arm honestly; refusing one this loop cannot show
    # is the gate's, and that half is measured in the next test with no episode stubbed


def _stub_episodes(monkeypatch) -> list[dict]:
    """Replace the episode with a note of the call, so a gate can be tested at the door.

    Every test below that uses it is about what the CLI refuses *before* a backend exists,
    which a real episode cannot show: by the time `run_one_episode` raises, the simulator is
    up, the store is open and the layouts after this one have already been paid for.
    """
    import embodied_agent.benchmark_mujoco.runner as runner_mod
    seen: list[dict] = []
    monkeypatch.setattr(runner_mod, "run_one_episode", lambda **kw: seen.append(kw) or {
        "episode_id": "probe", "official_success": False, "env_steps": 0,
        "skill_calls_executed": 0, "decisions": 0, "termination_reason": None,
        "wall_clock_s": 0.0, "run_error": None, "obj_to_target_m": None,
        "model_usage": {"http_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}})
    return seen


def test_the_perceive_flag_offers_exactly_the_channels_the_perception_package_names():
    """A second environment must not invent a third sensor while pretending to reuse one.

    `perception/grounding.py` is the authority on what a channel means — it is where
    `arm_coherence` and `channel_readiness` live — and this bench's `--perceive` choices are
    a copy of its list. The copy exists so `--help` does not import the renderer; what the
    copy makes necessary is this assertion.
    """
    from embodied_agent.benchmark_mujoco.runner import PERCEIVE_CHANNELS
    from embodied_agent.perception.grounding import PERCEIVE_CHANNELS as AUTHORITY

    assert set(PERCEIVE_CHANNELS) == set(AUTHORITY)
    assert build_parser().parse_args(["run", "--out", "/tmp/never-written",
                                      "--perceive", "vlm"]).perceive == "vlm"
    assert build_parser().parse_args(["run", "--out", "/tmp/never-written"]
                                     ).perceive == "privileged", \
        "the default has to stay the control arm: 25 archived episodes were run on it"


def test_a_config_has_to_say_its_endpoint_can_see(tmp_path):
    """The capability is a fact about a provider, so it is read from a file or not at all.

    Measured against the configs that exist on disk: the one written for the camera declares
    `vision`, and the ones that predate it declare nothing — which is not the same as
    declaring *text-only*, and is precisely why `agnes.yaml` cannot be promoted into the
    camera seat by inference. A missing file is refused by name rather than answered by the
    `deepseek.yaml` fallback `DeepSeekPlanner.from_env` would reach for.
    """
    import yaml

    from embodied_agent.benchmark_mujoco.runner import vision_capability

    root = os.path.join(os.path.dirname(
        __import__("embodied_agent").__file__), "..", "configs", "models")
    able, why = vision_capability(os.path.join(root, "agnes-vision.yaml"))
    assert able, why
    for text_only in ("agnes.yaml", "deepseek.yaml"):
        able, why = vision_capability(os.path.join(root, text_only))
        assert not able and "capabilities" in why, (text_only, why)

    quiet = tmp_path / "quiet.yaml"
    quiet.write_text(yaml.safe_dump({"provider": "test", "model": "m",
                                     "capabilities": ["text"]}), encoding="utf-8")
    able, why = vision_capability(str(quiet))
    assert not able and "['text']" in why, "a declared text-only list is not an absence"
    assert [vision_capability(os.path.join(root, "no-such.yaml"))[0]] == [False]


def test_the_cli_refuses_a_camera_it_cannot_bill_or_show(monkeypatch, tmp_path):
    """Three refusals at the door, each about a spend the loop would not have counted.

    A rule decision source reports no HTTP counter, and `core/runtime.py` meters requests by
    reading the *source* — so `--perceive vlm --planner rule` would spend vision requests
    against a ceiling nothing was watching and file `model_usage` zeros beside a real bill.
    A config that declares no `capabilities` is the same category of failure one request
    later: the survey is the episode's first request, so an endpoint that cannot be shown a
    frame answers it *after* the backend is up and the store is open, once per layout.

    The zero-spend camera the first caller probably means already exists — `--perceive stub`,
    §9's `wo_vlm` baseline — and the last two lines show both accepted doors open.
    """
    configs = os.path.join(os.path.dirname(__import__("embodied_agent").__file__),
                           "..", "configs", "models")
    seen = _stub_episodes(monkeypatch)

    out = os.path.join(str(tmp_path), "refused")
    assert main(["run", "--perceive", "vlm", "--out", out]) == 2
    assert not seen and not os.path.exists(out), "a refusal must not leave a batch behind"

    assert main(["run", "--perceive", "vlm", "--planner", "model",
                 "--model-config", os.path.join(configs, "agnes.yaml"),
                 "--out", os.path.join(str(tmp_path), "blind")]) == 2
    assert not seen, "the default config declares no vision, and nothing was asked for"

    assert main(["run", "--perceive", "vlm", "--planner", "model",
                 "--model-config", os.path.join(configs, "agnes-vision.yaml"),
                 "--out", os.path.join(str(tmp_path), "ok")]) == 0
    assert seen[-1]["perceive"] == "vlm", "the channel asked for is the channel built"
    assert seen[-1]["planner"] == "model"

    assert main(["run", "--perceive", "stub",
                 "--out", os.path.join(str(tmp_path), "stub")]) == 0
    assert seen[-1]["perceive"] == "stub"


def test_the_three_arms_this_loop_cannot_show_are_refused_with_a_reason():
    """The other half of the door: a condition this channel has no instrument for would
    file records identical to `full` under a different word."""
    reasons = {c: mw_arm_coherence(make_ablation(c)) for c in
               ("wo_vlm", "wo_episodic_memory", "wo_skill_acquisition")}
    assert all(reasons.values()), f"expected a refusal for each, got {reasons}"
    assert all("duplicate" in reasons[c][0] for c in reasons)
    for c in ("full", "wo_planning", "wo_working_memory", "wo_replanning"):
        assert mw_arm_coherence(make_ablation(c)) == [], c


class _ScriptedEndpoint:
    """A stand-in for the HTTP half, so the seat can be tested without a network.

    Deliberately not a mock of `MujocoModelPolicy`: it is the adapter's own surface
    (`chat_json` + the six cumulative counters the loop bills), which is what
    `model_policy.py` is written against. Everything below the class is the real runtime,
    the real verifier and the real simulator.
    """

    provider = "probe"
    model = "probe-vlm"
    base_url = "https://probe.invalid/v1"
    pricing: dict = {}

    def __init__(self, replies: list[dict], *, retry_on: Optional[int] = None):
        self.replies = replies
        self.retry_on = retry_on
        self.http_requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.api_errors = 0
        self.transport_retries = 0
        self.format_repairs = 0
        self.asked: list[dict[str, Any]] = []

    def chat_json(self, system, user, *, kind, prompt_version, max_tokens=None):
        self.http_requests += 1
        attempts = 1
        if self.retry_on is not None and len(self.asked) == self.retry_on:
            # two timeouts before the answer that finally came back: the attempts are
            # billed, and only `transport_retries` says which round they belonged to
            self.http_requests += 2
            self.transport_retries += 2
            attempts = 3
        self.prompt_tokens += len(user) // 4
        self.completion_tokens += 32
        # the last reply repeats: a scripted run must survive an unexpected extra round
        reply = self.replies[min(len(self.asked), len(self.replies) - 1)]
        self.asked.append({"kind": kind, "prompt_version": prompt_version,
                           "system": system, "user": user})
        return reply, {"kind": kind, "prompt_version": prompt_version,
                       "raw_response": json.dumps(reply), "http_requests_total": self.http_requests,
                       "transport_attempts": attempts, "transport_retries": attempts - 1,
                       "usage": {"prompt_tokens": 10,
                                 "completion_tokens": 32}}


def _decide(skill, args, why, expect, subgoal=None):
    return {"action": "execute",
            "execute": {"skill": skill, "args": args, "candidate_id": None, "subgoal": subgoal},
            "missing_information": None, "evidence_refs": ["obs_probe"], "rationale": why,
            "expected_effect": expect}


#: `action=finish` carries no `execute` and no `expected_effect`: the contract's own
#: validator refuses both, and a scripted reply that trips it is a broken fixture, not a
#: finding. (Measured the hard way: the first version of this constant cost three extra
#: rounds of rejections and made the run end on `BUDGET_EXHAUSTED`.)
FINISH = {"action": "finish", "evidence_refs": ["obs_probe"],
          "rationale": "the peg is measured inside the socket"}


def test_a_model_in_the_seat_owns_every_round_without_a_network(tmp_path):
    """`--planner model` means the round's action came from the source, not from here.

    The scripted answers are the same three moves the rule makes, so the difference the
    assertions can be about is the accounting: three requests charged, three rows in the
    episode's own call log, and a manifest that names the endpoint. The payload assertion
    is §10's, and it is the one that has to hold on the model path in particular — a
    context that leaked `success` would let a model look like it manipulated well.
    """
    from embodied_agent.benchmark_mujoco.prompts import BANNED_IN_PAYLOAD, MW_DECISION_SYSTEM

    endpoint = _ScriptedEndpoint([
        _decide("pick", {"object_id": "peg 1"}, "the peg lies at the far side of the table",
                "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    assert s["run_error"] is None
    assert s["official_success"] is True and s["decisions"] == 3
    assert endpoint.http_requests == 3
    billed = s["episode_result"]
    assert billed["http_requests"] == 3 and billed["decision_rounds"] == 3
    assert billed["rejected_decisions"] == 0 and billed["model_errors"] == 0
    assert billed["prompt_tokens"] > 3000, "the page it was shown is a real page"
    assert billed["api_cost_estimate"] is None, "no price is configured, so none is invented"
    assert [t["skill"] for t in s["decision_trace"]] == ["pick", "place", None]
    assert all(t["action"] == "execute" for t in s["decision_trace"][:2])

    ep = os.path.join(str(tmp_path), "episodes", s["episode_id"])
    calls = [json.loads(ln) for ln in
             open(os.path.join(ep, "model_calls.jsonl"), encoding="utf-8") if ln.strip()]
    assert len(calls) == 3 and all(c["kind"] == "decision" for c in calls)
    assert calls[0]["raw_chars"] > 0, "the reply is sized, never pasted into the ledger"
    assert not any("base64" in json.dumps(c) for c in calls)

    manifest = json.load(open(os.path.join(ep, "manifest.json"), encoding="utf-8"))
    assert manifest["planner"] == "model" and manifest["provider"] == "probe"
    assert manifest["model"] == "probe-vlm" and manifest["prompt_version"] == "mw-decide-v1"
    assert "none: the round's action was the model's" in manifest["rule_interpreter"]

    assert endpoint.asked[0]["system"] == MW_DECISION_SYSTEM
    for ask in endpoint.asked:
        for banned in BANNED_IN_PAYLOAD:
            assert f'"{banned}"' not in ask["user"], f"the page leaked {banned}"


def test_an_illegal_skill_is_rejected_and_charged_and_nothing_is_substituted(tmp_path):
    """SPEC 6.1 on this channel: a bad answer costs the round that made it.

    The alternative implementations all look reasonable and are all wrong: retry until
    the model complies (an unbounded spend), map `fly_to` to the nearest verb (the runtime
    choosing the action), or fall back to the rule (a model run with rule-shaped successes).
    What has to happen is one rejected round, one charged request, and the next ask
    carrying the field-level reason.
    """
    endpoint = _ScriptedEndpoint([
        _decide("fly_to", {"object_id": "peg 1"}, "I will move the arm by wishing",
                "peg 1 held"),
        _decide("pick", {"object_id": "peg 1"}, "understood; measure first", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    assert s["run_error"] is None, "a rejected decision is an outcome, not a crash"
    assert endpoint.http_requests == 4, "the illegal round was asked for, and paid for"
    assert s["skill_calls_executed"] == 2, "fly_to executed nothing"
    assert s["episode_result"]["rejected_decisions"] >= 1

    events = _events(os.path.join(str(tmp_path), "episodes", s["episode_id"]))
    lost = [e for e in events if e["type"] == "execution_feedback"
            and "execute.skill" in json.dumps(e, ensure_ascii=False)]
    assert lost, "the field-level reason has to reach the record the model is shown next"
    assert not [e for e in events if e["type"] == "skill_call"
                and "fly_to" in json.dumps(e, ensure_ascii=False)], \
        "nothing executed for a verb this world does not have"
    assert len([e for e in events if e["type"] == "decision"]) == 3, \
        "three lawful rounds were filed; the illegal one was not dressed up as one"


def test_a_motion_that_refused_its_own_aim_is_a_refusal_on_the_model_page(tmp_path):
    """The other half of #107: the status is what the model is told about its own last action.

    `test_a_motion_that_refused_to_move_is_not_filed_as_one_that_ran` pins the executor's filing.
    This one walks the whole loop, because the consequence that matters is downstream:
    `core/runtime.py:703` fills `rejection_reasons` from a skill's notes **only** for a rejected
    call, and `ExecutionFeedback.short()` publishes `executed`, `status`, `failure_code`,
    `stages_executed` and `rejection_reasons` — so while a refusal was filed `completed`, the
    round after it was shown a page saying the `place` had worked. Nothing here makes the runtime
    choose an action: the arm declined to move before this test existed and declines in exactly
    the same way now; only the report changed (§10).

    The refusal is reached on the privileged arm by asking for a `place` before anything is in
    the hand: the carry check then declines on a measured offset, which is deterministic. Naming a
    target this world has no site for does not work — the round is rejected as an illegal
    argument before the executor is ever asked — and a lost camera body would make the case the
    intermittent #110 is about instead of a filing check.
    """
    from embodied_agent.core.contracts import SkillStatus

    endpoint = _ScriptedEndpoint([
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "put it in, then", "peg 1 in socket 1"),
        _decide("pick", {"object_id": "peg 1"}, "understood; it has to be in the hand first",
                "peg 1 held"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    assert s["run_error"] is None, "a refused motion is an outcome, not a crash"

    motions = s["perception"]["motions"]
    assert [m["skill"] for m in motions] == ["place", "pick"], motions
    assert [m["status"] for m in motions] == ["rejected", "completed"], motions
    assert motions[0]["failure_code"] == "AIM_UNMEASURED"
    assert motions[0]["stages"] == ["carry_check"] and motions[0]["env_steps_used"] == 0.0
    assert s["skill_calls_executed"] == 1, \
        "the count a reviewer reads as work done must not include the motion that moved nothing"
    assert len(motions) == 2, "the refusal is still filed, just not as a success"

    events = _events(os.path.join(str(tmp_path), "episodes", s["episode_id"]))
    fb = [e["payload"]["feedback"] for e in events if e["type"] == "execution_feedback"]
    refused = [p for p in fb if p.get("failure_code") == "AIM_UNMEASURED"]
    assert len(refused) == 1, [(p.get("skill"), p.get("status")) for p in fb]
    assert refused[0]["skill"] == "place" and refused[0]["executed"] is False
    assert refused[0]["status"] == SkillStatus.rejected.value
    assert refused[0]["stages_executed"] == ["carry_check"]
    assert any("not measured close enough to the hand" in r
               for r in refused[0]["rejection_reasons"]), refused[0]

    assert len(endpoint.asked) == 5, len(endpoint.asked)
    # Keyed on the round *after* the refusal, not the last one, and the choice is measured rather
    # than assumed: the page a model decides from carries the refusal only while
    # `recent_feedbacks` still holds it, and this episode keeps answering after that window has
    # moved on. Both ends are asserted, so a change to `feedback_depth` fails here instead of
    # quietly leaving this test reading a page that no longer shows what it claims to show.
    page = endpoint.asked[1]["user"]
    assert "not measured close enough to the hand" in page and "AIM_UNMEASURED" in page, \
        "the reason has to be on the next page the model is asked to decide from, unedited"
    assert '"executed": false' in page, \
        "and it has to be filed as a refusal, not as a step that ran"

    assert "not measured close enough to the hand" not in endpoint.asked[-1]["user"], \
        "the window `page` above keys on is the one that still holds the refusal; if the last ask \
        holds it too, `asked[1]` is not the reason this test passes and the keying needs re-reading"


def test_a_retried_attempt_is_billed_and_named_by_the_round_that_paid_for_it(tmp_path):
    """Every counter `SOURCE_COUNTERS` names has to be readable off the source.

    Measured, not hypothetical: the first version of `MujocoModelPolicy` exposed four of
    the six, and `BudgetLedger._accumulate_usage` falls back to the episode baseline when
    an attribute is missing — so five timed-out attempts across a 25-episode Agnes sweep
    were billed into `http_requests` and filed in each row's `transport_retries`, while
    `episode_result.provider_counters.transport_retries` reported 0 for all 25. The extra
    money was visible; the reason was not.
    """
    from embodied_agent.core.runtime import SOURCE_COUNTERS

    endpoint = _ScriptedEndpoint([
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH], retry_on=1)
    for counter in SOURCE_COUNTERS:
        assert hasattr(endpoint, counter)
    from embodied_agent.benchmark_mujoco.model_policy import MujocoModelPolicy
    probe = MujocoModelPolicy(endpoint)
    assert all(hasattr(probe, c) for c in SOURCE_COUNTERS), \
        "a counter the loop cannot read is a counter that reads zero forever"

    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    billed = s["episode_result"]
    assert s["official_success"] is True and s["decisions"] == 3
    assert endpoint.http_requests == 5, "three answers, five opened requests"
    assert billed["http_requests"] == 5
    assert billed["provider_counters"]["transport_retries"] == 2
    assert s["model_usage"]["transport_retries"] == 2

    ep = os.path.join(str(tmp_path), "episodes", s["episode_id"])
    calls = [json.loads(ln) for ln in
             open(os.path.join(ep, "model_calls.jsonl"), encoding="utf-8") if ln.strip()]
    assert [c["http_requests_this_call"] for c in calls] == [1, 3, 1], \
        "the retry has to be attributable to a round, not just to the episode"
    assert calls[1]["transport_retries"] == 2 and calls[0]["transport_retries"] == 0
    assert sum(c["prompt_tokens_this_call"] for c in calls) == s["model_usage"]["prompt_tokens"]


class _ScriptedSurvey(_ScriptedEndpoint):
    """The other half of the endpoint: `chat_vision`, which the survey's question goes out on.

    Replies are raw *text* because that is what the transport returns; the parsing and the one
    format repair are the adapter's own job (`chat_vision_json`), which this double delegates to
    rather than restating -- see its docstring.
    """

    def __init__(self, replies: list[dict], surveys: list[str], **kw):
        super().__init__(replies, **kw)
        self.surveys = surveys
        self.survey_asked: list[dict[str, Any]] = []

    def chat_vision(self, system, user, images, *, max_tokens=None, modality="rgb"):
        self.http_requests += 1
        self.prompt_tokens += 500
        self.completion_tokens += 24
        raw = self.surveys[min(len(self.survey_asked), len(self.surveys) - 1)]
        self.survey_asked.append({"system": system, "user": user, "images": list(images),
                                  "modality": modality})
        # `transport_attempts` is not decoration: `chat_vision_json`'s failure path adds the two
        # attempts together to report `requests_made`, so a double that omits it turns an honest
        # refusal into a KeyError.
        return raw, {"completion_id": "probe-survey", "usage": {"prompt_tokens": 500,
                                                                "completion_tokens": 24},
                     "http_requests_total": self.http_requests, "latency_s": 0.5,
                     "transport_attempts": 1, "raw_response": raw}

    def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                         max_tokens=None, modality="rgb"):
        """The production parse-and-repair body, run against this double's transport.

        Called unbound on purpose. `DeepSeekAdapter.chat_vision_json` touches `self.chat_vision`,
        `self.format_repairs` and two module helpers and nothing else, so this line is what keeps
        the *real* repair under test while the bytes being parsed stay scripted. A hand-written
        double of the repair would have tested the test.
        """
        from embodied_agent.adapters.deepseek import DeepSeekAdapter

        return DeepSeekAdapter.chat_vision_json(self, system, user, images, kind=kind,
                                               prompt_version=prompt_version,
                                               max_tokens=max_tokens, modality=modality)


#: The answer that ended four of the five episodes in the first camera batch: a box whose
#: right edge sits at 584 px of a 480 px frame. Quoted from the artifact, not invented. It is
#: also the box this channel now *uses*, because it contains the hole — see
#: `test_a_survey_box_that_overhangs_the_frame_is_used_and_the_overhang_is_filed`.
SURVEY_OFF_FRAME = '{"box":{"found":true,"bbox":[96,0,584,360],"confidence":0.9,' \
                   '"notes":"the wooden box"}}'

#: A box with no part in the picture. No episode of either batch ever answered this way; this
#: is the case the frame gate is still for, and the two numbers are chosen so that the
#: overhang is exact arithmetic rather than a value to be remembered.
SURVEY_WHOLLY_OUTSIDE = '{"box":{"found":true,"bbox":[600,600,720,712],"confidence":0.9,' \
                        '"notes":"not in this frame"}}'

#: The defect the second decision endpoint produced on its first survey of this bench: a
#: *complete* answer followed by one `}` too many. Measured as a shape on
#: `/tmp/mw_sensenova_smoke` — `survey_raw` 109 chars, 2 `{` and 3 `}`, last byte `}`, and
#: `json` named the offset (`Extra data: line 3 column 107`). The `notes` text is mine: the
#: finding is a brace, not a sentence. Neither `json.loads` nor `_loads_lenient` takes it, which
#: is what the second test is for — the lenient loader slices the first `{` to the last `}`, so
#: the junk is inside the slice it re-parses.
#:
#: The last `}` is the whole fixture. Written one byte short it was byte-identical to
#: `SURVEY_OFF_FRAME` two lines above, and the test that used it passed its survey assertions by
#: reading a reply that had never needed a repair; the numbers that caught that are the ones two
#: tests down (`http_requests_this_call == 2`, `format_repairs == 1`).
SURVEY_STRAY_CLOSING_BRACE = '{"box":{"found":true,"bbox":[96,0,584,360],"confidence":0.9,' \
                             '"notes":"the wooden box"}}}'

#: A look that names nothing. `VlmReading` defaults every list to empty, so this is the
#: smallest reply the perceiver accepts -- it is here because the test below has to get *past*
#: the survey, and every question after it goes through the same scripted reply queue.
EMPTY_PERCEPT = '{"objects":[],"absent":[],"regions":[],"notes":""}'

#: The answer the second decision endpoint gave on its second survey of the same frame, with its
#: `notes` in the one place the prompt asks for it -- a sibling of `box` (`SURVEY_SYSTEM` line 344).
#: The `notes` text is quoted verbatim from
#: `/tmp/mw_vlm_camera_batch11_retry/episodes/*/episode_summary.json`'s `survey_raw`, where the
#: shipped reader filed it as `""`; the box is the archived overhang answer above, chosen because a
#: box this channel already accepts is the smallest carrier for a sentence, not because 460,0,480,170
#: could not have held one.
SURVEY_TOP_LEVEL_NOTES = (
    '{"box":{"found":true,"bbox":[96,0,584,360],"confidence":0.9},'
    '"notes":"The target is the large dark grey fixed structure in the background '
    '(upper right), which features a visible rectangular hole/cutout on its face."}')

_TOP_LEVEL_NOTE_TEXT = ("The target is the large dark grey fixed structure in the background "
                        "(upper right), which features a visible rectangular hole/cutout on its "
                        "face.")


def test_a_survey_answer_with_a_stray_brace_is_asked_again_and_the_episode_survives(tmp_path):
    """One unreadable answer is a format failure, not a dead arm.

    The survey was the only VLM call site on this channel with no format repair: a look gets one
    (`perception/observe.py:304` calls `chat_vision_json`), and the survey parsed its own reply and
    refused the episode if the parse failed. On the archived endpoint that never happened — 0 shape
    refusals in 58 episodes — so the asymmetry cost nothing until a second endpoint was wired up
    and died at step 0 on a trailing brace. This test is the difference between the two behaviors:
    the frame is asked again, the episode continues, and both requests are attributable.
    """
    endpoint = _ScriptedSurvey([FINISH], [SURVEY_STRAY_CLOSING_BRACE, SURVEY_OFF_FRAME,
                                          EMPTY_PERCEPT])
    assert (SURVEY_STRAY_CLOSING_BRACE.count("{"), SURVEY_STRAY_CLOSING_BRACE.count("}")) == (2, 3) \
        and json.loads(SURVEY_STRAY_CLOSING_BRACE[:-1]) == json.loads(SURVEY_OFF_FRAME), \
        "the fixture has to BE the defect: strip one byte and it is a plain valid answer, which " \
        "would let the survey pass on try 1 and the test below prove nothing about a repair"
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")
    assert s["run_error"] is None, \
        f"a repairable first answer must not end the episode: {s['run_error']}"
    assert s["decisions"] >= 1, "the loop got far enough to ask the decider"

    ev = s["perception"]["survey"]
    # The double records every vision question it is asked, looks included -- the same cumulative-
    # counter trap that made `http_requests_this_call` a delta. Scope to the survey by the frame it
    # is the only caller of.
    survey_asks = [a for a in endpoint.survey_asked
                   if a["images"] and a["images"][0].endswith("survey_box.png")]
    assert len(survey_asks) == 2, "one question, then the repair re-ask"
    assert ev["http_requests_this_call"] == 2, \
        "a repaired survey costs two requests and files two"
    assert endpoint.format_repairs == 1
    assert ev["survey_raw"] == SURVEY_OFF_FRAME, \
        "the answer filed is the answer acted on, not the one that tripped"
    assert ev["survey_format_repair"]["first_parse_error"], "the rejected reply is named, not lost"
    assert ev["answered_kind"] == "survey_format_repair", \
        "`prompt_sha256` names the question as first asked; the answer filed here came from the " \
        "repair prompt, so a reader needs a field that says which call produced it"
    assert ev["bbox"] == [96.0, 0.0, 584.0, 360.0]

    second = survey_asks[1]
    assert "上一次输出无法解析为 JSON" in second["user"], \
        "the re-ask says what failed; it is the shipped repair prompt, not a paraphrase"
    assert second["images"] == survey_asks[0]["images"], \
        "a picture cannot be described in words: the repair re-sends the same frame"
    assert s["model_usage"]["format_repairs"] == 0 and endpoint.format_repairs == 1, \
        "the ledger's baseline is taken after the survey, so a survey repair -- like the survey " \
        "request itself -- stays out of `model_usage`, whose meaning the archive depends on. " \
        "Since #108 it is no longer out of the bill, which is the assertion below."
    assert s["prologue"]["format_repairs"] == 1 and \
        s["model_usage_billed"]["format_repairs"] == 1, \
        "two requests, one repair, one survey: all three are in the episode's bill now, and the " \
        "first five-layout batch billed none of them (`H-23`, #108)"


def test_a_survey_reads_the_notes_the_prompt_asked_for(tmp_path):
    """`notes` is a sibling of `box` in the question, and it has to survive into the artifact.

    #131, measured on the batch11 retry: that survey refused the frame (`region_px=0`,
    `plane_modes_tried=0`) and the reply it refused carried a sentence naming what the model actually
    looked at -- "the large dark grey fixed structure in the background (upper right)". `survey()`
    validated `(parsed or {}).get("box")` into `SurveyAnswer`, a model that *declares* `notes`, so
    the field kept its default and the artifact filed `""`. The prompt was answered and the answer
    was dropped: the perception-side shape of #117b, where the diagnosis arrives and the remedy does
    not.

    Every survey fixture in this file spells `notes` INSIDE the box, which is why the suite never
    saw this. The fixture below spells it the way `SURVEY_SYSTEM` does, and it fails on the reader
    that shipped.
    """
    endpoint = _ScriptedSurvey([FINISH], [SURVEY_TOP_LEVEL_NOTES, EMPTY_PERCEPT])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")
    ev = s["perception"]["survey"]
    assert json.loads(SURVEY_TOP_LEVEL_NOTES)["notes"] == _TOP_LEVEL_NOTE_TEXT, \
        "the fixture's sentence is the one the artifact is expected to carry"
    assert ev["survey_raw"].endswith('"}'), "the reply is the top-level spelling, not a box field"
    assert ev["notes"] == _TOP_LEVEL_NOTE_TEXT, \
        f"a survey answered in the shape the prompt asked for has to file what it was told: {ev!r}"
    assert ev["bbox"] == [96.0, 0.0, 584.0, 360.0], "reading the note cannot change the box acted on"


def test_a_survey_still_unreadable_after_the_repair_says_so_rather_than_blaming_the_wire(
        tmp_path):
    """Two unreadable answers is a shape refusal, and the archive keys on that sentence.

    Both `chat_vision_json`'s failure modes arrive as the same exception class from the same
    adapter module, so a single `except` would have filed "could not be asked" — a transport
    diagnosis — for an answer that came back and never parsed. Every census of the archived
    batches counts the two sentences apart, so they have to stay apart, and the reason a reader
    can tell them is the `first_parse_error` the repair leaves on the exception's metadata.
    """
    endpoint = _ScriptedSurvey([FINISH], [SURVEY_STRAY_CLOSING_BRACE])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")
    assert s["run_error"].startswith(
        "PerceptionUnavailable: the target survey answered in a shape that is not an answer")
    assert s["decisions"] == 0
    reasons = " ".join(s["perception"]["refusal_reasons"])
    assert "first_parse_error=" in reasons and "http_requests_spent=2" in reasons, reasons
    assert s["perception"]["survey"]["http_requests_this_call"] == 2
    assert s["perception"]["survey"]["answered_kind"] == "survey_format_repair", \
        "the shape refusal still names the call that answered, so it cannot be read as a wire fault"
    assert "survey_raw" in s["perception"]["survey"], "the unreadable reply is the evidence"


def test_a_refused_survey_files_the_question_it_asked_and_what_it_cost(tmp_path):
    """A refused answer is a spent request, so the spend has to be readable.

    The survey is one question per episode and every refusal in `survey()` happens after
    the reply came back. Two numbers are checked. `http_requests_this_call` must be 1 even
    though this adapter's cumulative counter starts at 7 — the counter is per object and one
    object serves a whole sweep, and the first five-layout batch filed 5 for the fifth
    layout's one-question survey because it read the cumulative value. And the raw reply
    must be in the artifact: without it a batch table shows tokens spent with no answer
    beside them, which is the difference between "the model said 584 px" and "something
    failed". Nothing here clamps the box: the episode still ends at the survey.
    """
    endpoint = _ScriptedSurvey([FINISH], [SURVEY_WHOLLY_OUTSIDE])
    endpoint.http_requests = 7           # four earlier layouts and their looks
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")
    assert s["run_error"].startswith(
        "PerceptionUnavailable: the survey's box is outside this frame")
    assert "against 480x480" in s["run_error"]
    assert s["decisions"] == 0, "the episode ended before the model was asked to decide"

    p = s["perception"]
    assert p["channel"] == "vlm"
    ev = p["survey"]
    assert ev["http_requests_this_call"] == 1, "one question, whatever the adapter has spent"
    assert ev["bbox"] == [600.0, 600.0, 720.0, 712.0], "the answer is filed as given"
    assert ev["out_of_frame_px"] == [0.0, 0.0, 241.0, 233.0]
    assert ev["reported_confidence"] == 0.9 and "584" not in ev["survey_raw"]
    assert ev["tokens"]["prompt_tokens"] == 500
    assert ev["prompt_version"] == "mw-survey-v1" and ev["prompt_sha256"]
    assert ev["answered_kind"] == "survey", \
        "an unrepaired answer is attributable to the prompt whose sha is filed two lines above"
    assert ev["frame_px"] == 480 * 480 and 0 <= ev["frame_surface_px"] <= ev["frame_px"], \
        "the instrument files its own health beside its verdict, whatever the verdict is"
    assert "frame=480x480" in " ".join(p["refusal_reasons"])

    asked = endpoint.survey_asked[0]
    assert asked["images"] and asked["images"][0].endswith("survey_box.png")
    assert ev["bbox_used"] == [600.0, 600.0, 479.0, 479.0], \
        "the intersection of a box and a picture it is not in is an inverted box, and it is filed"
    assert "480x480" in asked["user"], "the question named the frame it was asked about"

    # The two halves of #108/#109 on the arm that ends before the loop. This is the path where
    # `_percept_arm` itself raised: there is no ledger, `runtime` is None, and both the bill and
    # the row have to be filed anyway — the first by the runner's `except`, the second by
    # `survey()` before the refusal propagates.
    assert s["model_usage"] == {} and s["prologue"]["http_requests"] == 1, \
        "no ledger on this path, and the bill still names the request the episode spent"
    assert s["model_usage_billed"] == {"prompt_tokens": 500, "completion_tokens": 24,
                                       "api_errors": 0, "transport_retries": 0,
                                       "format_repairs": 0, "http_requests": 1}, \
        f"`billed` is a union of the two dicts' keys, not of `usage`'s alone: {s}"
    rows = _call_rows(str(tmp_path), s["episode_id"])
    assert [r["kind"] for r in rows] == ["survey"], \
        "one request, one row, and that row is the episode's entire request log"
    assert rows[0]["ok"] is True, \
        "`ok` means answered, not accepted (#114): the endpoint replied and this run refused what " \
        "it said, which is what `run_error` and the assertions above are the record of"
    assert rows[0]["image_sha256"] == ev["image_sha256"], \
        "the row and the evidence describe the same picture, so a reader can join them"


class _FatalEndpoint(_ScriptedEndpoint):
    """A request that does not come back, shaped like the ones the camera batches kept meeting.

    Only the counters and the exception move the way `adapters/deepseek.py` moves them: one
    `http_requests` per attempt opened, `transport_retries` per retry waited out, and
    `api_errors` once on the attempt that was fatal, with `requests_made` on the exception so
    a caller that accounts per call can still see the retries. The message quotes the 429
    because that is what every archived instance of this failure said.
    """

    def __init__(self, replies: list[dict], *, fatal_on: Optional[int] = None):
        super().__init__(replies)
        self.fatal_on = fatal_on
        self.asks = 0

    def _charge_fatal(self):
        from embodied_agent.adapters.deepseek import LLMError
        self.http_requests += 3
        self.transport_retries += 2
        self.api_errors += 1
        raise LLMError("request failed after 3 attempts: HTTPError: HTTP Error 429: "
                       "Too Many Requests", requests_made=3)

    def chat_json(self, system, user, *, kind, prompt_version, max_tokens=None):
        # its own counter, not `len(self.asked)`: an ask that never got an answer does not
        # join `asked`, and a fatal round that forgot this would stay fatal forever
        self.asks += 1
        if self.fatal_on is not None and self.asks == self.fatal_on:
            self._charge_fatal()
        return super().chat_json(system, user, kind=kind, prompt_version=prompt_version,
                                 max_tokens=max_tokens)


def _call_rows(ep_dir: str, episode_id: str) -> list[dict]:
    path = os.path.join(ep_dir, "episodes", episode_id, "model_calls.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]


def test_a_fatal_decision_request_is_a_row_and_a_bill(tmp_path):
    """A request that dies is a spent request, so it has to be in both accountings.

    Before H-23 the call log held only answers: `_file_call` ran after `chat_json` returned,
    so the attempt that raised left no row and the log's own totals could not be tied to the
    bill. Measured on the archived camera batches: 51 episodes, 138 rows, every row a
    `finish_reason: stop`, and 0 rows for the 9 episodes that ended on a fatal `LLMError`.
    `ok` is explicit on both arms of the log, so "no row" is never the same statement as
    "nothing was asked".
    """
    endpoint = _FatalEndpoint([
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH], fatal_on=1)
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)

    rows = _call_rows(str(tmp_path), s["episode_id"])
    dead = [r for r in rows if r["ok"] is False]
    assert len(dead) == 1, f"one fatal request, one row: {[r.get('ok') for r in rows]}"
    assert dead[0]["http_requests_this_call"] == 3 and dead[0]["requests_made"] == 3, \
        "the three attempts belong to this round, and the row is where a reader finds that out"
    assert dead[0]["api_errors_this_call"] == 1
    assert "429" in dead[0]["error"] and dead[0]["raw_chars"] == 0
    assert not {"raw_sha256", "raw_preview", "raw_truncated"} & set(dead[0]), \
        "PR-114-3: a request that never produced an answer gains nothing — the three new keys " \
        "belong to an answer the schema refused, and there is no text to digest here"
    assert all(r["ok"] for r in rows if r is not dead[0])
    assert len(rows) == len(endpoint.asked) + 1, \
        "every ask is a row, so the log's length is the number of requests this episode opened"
    assert s["model_usage"]["api_errors"] == 1, \
        "the row and the ledger have to agree about the same failure"


#: The shape of batch9's two round-3 rejections, rebuilt from the field-level reasons the
#: artifacts kept. Both episodes filed the rejecting `execution_feedback` at `sequence` 27;
#: L4's three locs are `action`, `candidate_id`, `subgoal`, L0's single loc is `action`.
#: **This is not the archived text** — that is #114: the row used to be written before the
#: schema binding and `raw_response` was stripped out of it, so "589 chars" and "375 chars"
#: are all that survives of the two rounds CR-9a was waiting on. A fixture can prove what a
#: row carries *now*; it cannot recover what was said then, and pretending otherwise is how
#: a reconstruction ends up quoted as an artifact.
REJECTED_ANSWER = {
    "action": "observe",                              # a verb where an outcome belongs
    "candidate_id": None,                             # these two belong inside `execute`
    "subgoal": "look at the peg from the wrist",
    "evidence_refs": ["obs_probe"],
    "rationale": "the arm is already at the peg, and the only measurement that could settle "
                 "the placement is one taken from a camera whose axis crosses the socket",
    "expected_effect": "a measurement from gripperPOV",
}


def _row_of_rejected_answer(tmp_path, episode_id):
    """The one row in the log that says a schema refused an answer."""
    rows = _call_rows(str(tmp_path), episode_id)
    rejected = [r for r in rows if "schema_errors" in r]
    assert len(rejected) == 1, f"one rejected answer, one such row: {rows}"
    return rows, rejected[0]


def test_a_schema_rejected_answer_is_a_row_that_still_says_what_it_meant(tmp_path):
    """#114, and PR-114-1/2/6 of the pre-registration in `docs/`.

    A round whose answer does not fit `Decision` is a round the model spent a request on, so
    it has always been a row. What it was not, until now, was a *readable* one: `_file_call`
    ran before the binding, so the row carried the request's shape and a character count and
    nothing of the answer — which is how the one round in the archive that could have shown
    what a model does with a newly-usable camera reached me as `raw_chars: 589`.

    The three new keys are the whole fix, and PR-114-2 is the half that keeps the archive
    comparable: they appear on a row **only** when that row carries `schema_errors`, so every
    successful row is the 23-key row batch9 filed. Reading the answer is what the digest is
    for; reading it *from the model's own bytes* rather than from a re-serialization of the
    row is what makes the digest usable a month from now.
    """
    endpoint = _ScriptedEndpoint([
        REJECTED_ANSWER,
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    assert s["run_error"] is None
    text = json.dumps(REJECTED_ANSWER)

    rows, row = _row_of_rejected_answer(tmp_path, s["episode_id"])
    assert len(rows) == len(endpoint.asked) == 4, \
        "moving the write past the binding must not add or lose a row: one ask, one row"
    assert row["ok"] is True, "the request was answered; the schema is a different failure"
    assert row["raw_chars"] == len(text)
    assert [r.split(":")[0] for r in row["schema_errors"]] == \
        ["action", "candidate_id", "subgoal"], "the locs batch9's L4 carried, in its order"

    # PR-114-6: a later reader learns the intended act from the log line, not from a guess.
    assert "observe" in row["raw_preview"] and "gripperPOV" in row["raw_preview"], \
        "the point of the whole change: the row says what the answer was reaching for"
    assert row["raw_truncated"] is False, "this answer fits inside the bound"
    assert row["raw_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert row["raw_sha256"] != hashlib.sha256(
        json.dumps(REJECTED_ANSWER, separators=(",", ":")).encode("utf-8")).hexdigest(), \
        "the digest covers the bytes the answer arrived as, not a re-serialization"

    # PR-114-2: nothing else in the log changed shape.
    others = [r for r in rows if r is not row]
    assert not [r for r in others if {"schema_errors", "raw_sha256", "raw_preview",
                                      "raw_truncated"} & set(r)], \
        "an answered round that validated files the same row it always did"
    assert s["episode_result"]["rejected_decisions"] == 1


def test_an_answer_longer_than_the_preview_is_digested_wholly_and_cut_here(tmp_path):
    """The bound is a bound on the *preview*, never on the evidence.

    `REJECTED_RAW_PREVIEW_CHARS` is 4096 because the archive's answered rows run 155-589
    chars and one decision page is ~15.5k chars, so no rejected row can outweigh the context
    it was answered from. That argument is only safe if a longer answer still gets a complete
    digest: the cut must be visible (`raw_truncated`), locatable (the preview is a real
    prefix), and re-provable (the digest is of the whole text, so a copy kept elsewhere can
    be checked against the row).
    """
    long_answer = dict(REJECTED_ANSWER,
                       rationale=REJECTED_ANSWER["rationale"] + " " + "unmeasured " * 600)
    from embodied_agent.benchmark_mujoco.model_policy import REJECTED_RAW_PREVIEW_CHARS
    endpoint = _ScriptedEndpoint([
        long_answer,
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    text = json.dumps(long_answer)
    assert len(text) > REJECTED_RAW_PREVIEW_CHARS, "the fixture is the long case"

    _, row = _row_of_rejected_answer(tmp_path, s["episode_id"])
    assert row["raw_chars"] == len(text), "the size is of the answer, not of what was kept"
    assert row["raw_truncated"] is True
    assert len(row["raw_preview"]) == REJECTED_RAW_PREVIEW_CHARS
    assert text.startswith(row["raw_preview"]), "the kept part is a prefix, not a summary"
    assert row["raw_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert row["raw_sha256"] != hashlib.sha256(
        row["raw_preview"].encode("utf-8")).hexdigest(), "the digest is not of the cut text"
    assert len(row["raw_preview"]) < len(endpoint.asked[0]["user"]), \
        "a rejection row cannot outweigh the page it was answered from"


def test_filing_the_answer_changes_nothing_the_model_is_shown_back(tmp_path):
    """PR-114-4/5: this is a change to what is *kept*, not to what is *asked*.

    The two things that must not move are the identity of the page (a moved `prompts_sha256`
    or `catalogue_sha256` would make the next batch an answer to a different question than
    the nine before it) and the content of the feedback the model gets about its own round.
    The reasons are handed over verbatim, exactly as they were before the row grew a preview,
    and the answer text itself goes nowhere near the next page: `Runtime` still does not
    repair, complete or re-quote a model's output (§10), and handing a model its own rejected
    draft would be a payload change wearing an audit costume.
    """
    from embodied_agent.benchmark_mujoco.prompts import prompts_sha256
    from embodied_agent.benchmark_mujoco.skills import catalogue_sha256

    endpoint = _ScriptedEndpoint([
        REJECTED_ANSWER,
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)

    assert prompts_sha256() == "3c39f0f533ace471f8b78ee82d4c07e78e5f7989d6d9c5cbff9d8bb7bdba9cad"
    assert catalogue_sha256() == "076983fb948fa6c7ed131b84e863d35531e0a19558844601921b6717af44fc7a", \
        "both digests are the ones batch9's two manifests carry; a change here is a new question"

    _, row = _row_of_rejected_answer(tmp_path, s["episode_id"])
    events = _events(os.path.join(str(tmp_path), "episodes", s["episode_id"]))
    rejected = [e for e in events if e["type"] == "execution_feedback"
                and e["payload"].get("feedback", {}).get("failure_code") == "INVALID_DECISION"]
    assert len(rejected) == 1
    fb = rejected[0]["payload"]["feedback"]
    assert fb["rejection_reasons"] == row["schema_errors"], \
        "the model is told the same field-level reasons the row keeps, unedited"
    assert fb["decision_id"] is None and fb["executed"] is False
    assert fb["pre_state_version"] == fb["post_state_version"], "nothing moved in the world"
    assert [e["payload"]["call"]["skill"] for e in events if e["type"] == "skill_call"] == \
        ["pick", "place"], "the rejected round executed nothing; the two lawful rounds did"
    assert row["raw_preview"] not in endpoint.asked[1]["user"], \
        "the archived text is evidence for the reader, not material handed back to the model"


def test_a_look_that_dies_still_bills_the_episode_it_was_asked_for(tmp_path):
    """The escape route the camera arm has and the text arm does not: a request inside
    `observe()`.

    `chat_vision_json` is the look's method and, since the survey got its one format repair, the
    survey's too -- so the double below is fatal only on a look's frame, or the episode would end
    at the survey and never reach the request being tested. Nothing in the round guards the call,
    so the exception
    leaves `run_episode` without ever reaching the settle at the end of the round. That is
    why 5 of the 9 archived camera episodes that end on a fatal `LLMError` filed
    `api_errors: 0` — the bill stopped before the attempt that cost the most. The other 4
    filed 1 but lost the fatal attempt's own increment, because `usage` is a maximum over
    settles and no settle ran afterwards.

    What the two accountings now file is the point of the last four assertions. Before #109 the
    request log was written only by the decision source, so a look that died was filed nowhere but
    `run_error`, and before #108 the survey's spend sat in `perception.survey` outside the bill.
    This is the shape that used to lose both: the loop never finalizes, so nothing downstream ever
    looked at either number. They are checked as a pair because the survey's row and the survey's
    bill are the same measurement taken once (`perceive.py:_this_call`).
    """
    class _FatalLook(_ScriptedSurvey):
        def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                             max_tokens=None, modality="rgb"):
            if images and images[0].endswith("survey_box.png"):
                return super().chat_vision_json(system, user, images, kind=kind,
                                                prompt_version=prompt_version,
                                                max_tokens=max_tokens, modality=modality)
            self.http_requests += 3
            self.transport_retries += 2
            self.api_errors += 1
            from embodied_agent.adapters.deepseek import LLMError
            raise LLMError("request failed after 3 attempts: HTTPError: HTTP Error 429: "
                           "Too Many Requests", requests_made=3)

    endpoint = _FatalLook([FINISH], [SURVEY_OFF_FRAME])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")

    assert s["run_error"].startswith("LLMError: request failed after 3 attempts"), s["run_error"]
    assert s["termination_reason"] is None and s["episode_result"] is None, \
        "the loop never finalized, which is the whole reason the ledger was short"
    assert s["model_usage"]["api_errors"] == 1, \
        "the episode that died on a request has to be billed for it"
    assert s["model_usage"]["transport_retries"] == 2
    assert s["model_usage"]["prompt_tokens"] == 0, \
        "`model_usage` is the loop's own span and this loop never ran; keeping that meaning is " \
        "what lets the archived summaries still mean one thing. The survey is billed on top of " \
        "it, in `model_usage_billed` (#108)."
    sv = s["perception"]["survey"]
    assert s["prologue"]["http_requests"] == sv["http_requests_this_call"] == 1, \
        "the survey's one request is charged to the episode ledger, not spent beside it"
    assert s["prologue"]["prompt_tokens"] == sv["counters_this_call"]["prompt_tokens_this_call"] \
        == 500, "the bill and the survey's own evidence are the same measurement taken once"
    assert s["model_usage_billed"]["prompt_tokens"] == \
        s["model_usage"]["prompt_tokens"] + s["prologue"]["prompt_tokens"] == 500
    assert s["model_usage_billed"]["api_errors"] == 1, \
        "the fatal look is in the bill too, so `model_usage_billed` is the episode's whole spend"
    rows = _call_rows(str(tmp_path), s["episode_id"])
    assert [r["kind"] for r in rows] == ["survey", "perceive"], \
        "the episode's two requests are its two rows: since #109 an empty log is not the same " \
        "statement as 'nothing was asked'"
    dead = rows[-1]
    assert dead["ok"] is False and "429" in dead["error"]
    assert dead["http_requests_this_call"] == 3 and dead["requests_made"] == 3, \
        "the three attempts of the look that died belong to this row"
    assert dead["api_errors_this_call"] == 1 and dead["transport_retries_this_call"] == 2
    assert not {"raw_sha256", "raw_preview", "raw_truncated"} & set(dead)
    assert rows[0]["ok"] is True and rows[0]["prompt_tokens_this_call"] == 500, \
        "the survey's row carries the same 500 the ledger bills, so the two accountings of the " \
        "episode's first request cannot drift apart"


def test_a_survey_box_that_overhangs_the_frame_is_used_and_the_overhang_is_filed(tmp_path):
    """The box selects pixels, so the part of it that is in the picture is what is usable.

    Both batches' refusals turned on an edge past 479, and /tmp/mw_survey_hole.py measured that
    every answer those batches filed *does* contain the projected `hole` site. This test is the
    contract that follows: the reported box is kept verbatim, the box handed to the depth stage
    is the part inside the frame, the difference between them is filed as `out_of_frame_px`, and
    the frame gate refuses only what has no part inside at all.

    The geometry assertion is skipped, never relaxed, on a process whose depth renderer has
    already gone stale: `SURVEY_FRAME_SURFACE_FLOOR` and `perceive.py` say why a dead buffer
    makes "there is no hole here" a sentence about the renderer. It is keyed on the instrument's
    own filed health rather than on the refusal's wording, because the wording changes with
    whichever gate fired — and it is checked on *both* frames this test surveys, because the
    buffer was measured to go between the first and the second.
    """
    from embodied_agent.benchmark_mujoco.perceive import (
        SURVEY_FRAME_SURFACE_FLOOR, MuJoCoRig, SURVEY_VIEW, survey)

    backend = MuJoCoBackend(ENV, task_index=0, seed=0, max_env_actions=500,
                            views=(SURVEY_VIEW,))
    backend.start()
    rig = MuJoCoRig(backend, str(tmp_path))

    surfaces: list = []

    def ask(answer: str):
        """One survey, and the health of the frame it was answered from.

        Returns `(region, evidence)`, with `region` None when the survey refused; a refusal that
        carries a sub-floor `frame_surface_px` is a report about this process's depth buffer, so
        it is collected here and the picture-dependent assertions below are dropped, while the
        box arithmetic the refusal still filed is checked."""
        try:
            region, ev = survey(backend, rig, adapter=_ScriptedSurvey([], [answer]),
                                reader_kind="vlm")
        except Exception as e:  # noqa: BLE001 - the evidence is the point, the verdict is not
            assert not isinstance(e, AssertionError)
            assert "outside this frame" not in str(e) and "leaves the frame" not in str(e), \
                "an overhanging box is no longer a frame-gate refusal"
            ev = getattr(e, "evidence", {}) or {}
            assert ev.get("bbox_used"), f"the used region has to be filed either way: {e}"
            surface = ev.get("frame_surface_px")
            if not isinstance(surface, int) or surface >= SURVEY_FRAME_SURFACE_FLOOR:
                raise
            surfaces.append(surface)
            return None, ev
        surfaces.append(ev.get("frame_surface_px"))
        return region, ev

    try:
        region, ev = ask(SURVEY_OFF_FRAME)
        assert ev["bbox"] == [96.0, 0.0, 584.0, 360.0], "the answer stands as it was given"
        assert ev["bbox_used"] == [96.0, 0.0, 479.0, 360.0]
        assert ev["out_of_frame_px"] == [0.0, 0.0, 105.0, 0.0]
        # The size of the region is not the measurement: the same frame surveyed with the whole
        # picture as its region has to return the same aperture, to the millimetre.
        _, whole = ask('{"box":{"found":true,"bbox":[0,0,479,479],"confidence":0.9,"notes":""}}')
        if region is None or any(not isinstance(s, int) or s < SURVEY_FRAME_SURFACE_FLOOR
                                for s in surfaces):
            pytest.skip(f"this process's depth buffer is not a picture: frame_surface_px="
                        f"{surfaces} of 480x480 (floor {SURVEY_FRAME_SURFACE_FLOOR}), "
                        f"see the docstring")
        assert ev["frame_surface_px"] >= SURVEY_FRAME_SURFACE_FLOOR
        assert region.target_id == TARGET_ID
        a, b = ev["aperture"]["centre_m"], whole["aperture"]["centre_m"]
        assert abs(a[0] - b[0]) < 0.005 and abs(a[1] - b[1]) < 0.005, (a, b)
    finally:
        rig.close()
        backend.close()


# -------------------------------- the survey in the ledger, the look in the log --

def test_the_survey_is_billed_inside_the_episode_it_was_asked_for(tmp_path):
    """#108: the camera episode's first request is charged to that episode.

    Measured on the archive before this test existed (`/tmp/mw117/h43_prologue_wall.py` and
    `/tmp/mw117/h42_survey_values.py`, filed at H-42 §D): 57 of 61 camera episodes filed a
    `perception.survey`, each spending 1 or 5 HTTP requests and 558 or 594 prompt tokens before
    `run_episode` took its baseline, and the ledger billed none of it. The archived field is not
    the quantity `survey_prologue` reads now — none of those 57 surveys carries
    `counters_this_call` (appendix H `#122`, whose first draft wrongly read "1-2" and "27-36" off
    a sliced printout). `prologue=` was already the channel for pre-loop work -- `v0.1` used it at
    `evaluation/run.py:462`, and `core/runtime.py` charges its requests against the HTTP budget and
    logs it in `episode_start` -- it was simply never passed on this channel.

    Three documents have to agree now, so each is checked against the other two rather than against
    a remembered figure: the summary's `model_usage_billed`, the `episode_result` the ledger wrote,
    and the survey's own row in `model_calls.jsonl`.
    """
    from embodied_agent.core.runtime import CALLED_COUNTERS

    endpoint = _ScriptedSurvey([FINISH], [SURVEY_OFF_FRAME, EMPTY_PERCEPT])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")
    assert s["run_error"] is None, s["run_error"]
    er = s["episode_result"]
    assert er is not None

    p, um, billed = s["prologue"], s["model_usage"], s["model_usage_billed"]
    assert p["http_requests"] == 1 == s["perception"]["survey"]["http_requests_this_call"], \
        "the prologue and the survey's own evidence are one measurement of one question"
    assert billed == {k: um.get(k, 0) + p.get(k, 0) for k in CALLED_COUNTERS}, \
        "the bill is the loop plus what came before it, key by key — over the six a request " \
        "moves, not the five the ledger tracks: `http_requests` is in `prologue`, so it is in " \
        "`billed`, and a comprehension keyed on `SOURCE_COUNTERS` alone compares a 6-key dict " \
        "with a 5-key one and fails on the difference rather than on the arithmetic"
    assert er["prompt_tokens"] == billed["prompt_tokens"] \
        and er["completion_tokens"] == billed["completion_tokens"], \
        "the result the sweep table reads cannot report a different cost than the summary"
    assert {k: er["provider_counters"][k] for k in er["provider_counters"]} == \
        {k: billed[k] for k in er["provider_counters"]}
    start = [e["payload"] for e in _events(os.path.join(str(tmp_path), "episodes",
                                                        s["episode_id"]))
             if e["type"] == "episode_start"][0]
    assert start["prologue"] == p, \
        "the event log is the document a reader of one episode has, and it has to say that a " \
        "request was spent before the first round"

    rows = _call_rows(str(tmp_path), s["episode_id"])
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "survey" and kinds.count("survey") == 1, \
        "one question per episode, and it is a row now: #109 was that the log began at the " \
        "first decision and the episode's first billed request was behind it with nothing filed"
    assert "perceive" in kinds, "the round's look files its own row too"
    assert all("http_requests_this_call" in r for r in rows)
    assert sum(r["http_requests_this_call"] for r in rows) == er["http_requests"], \
        "the request log's deltas and the ledger's budget counter are the same requests counted " \
        "twice, so a reader of either can tie the episode's spend to the ceiling it stopped at"
    assert set(CALLED_COUNTERS) <= set(
        k[: -len("_this_call")] for k in rows[0]), \
        "every counter a row can bill is on it, or `survey_prologue` would be billing a guess"


def test_the_privileged_arm_bills_no_survey_and_files_no_perception_row(tmp_path):
    """The control arm keeps its accounting, because the archive's baselines are its account.

    `perceive="privileged"` asks nothing of any endpoint: there is no survey to bill and no look to
    file. This test is the non-vacuity proof for the two changes above -- without it, "the log's
    deltas sum to the ledger's counter" would be satisfied by a channel that never spends, and a
    reader could not tell the fix from the accident.
    """
    endpoint = _ScriptedEndpoint([
        _decide("pick", {"object_id": "peg 1"}, "the peg lies on the bench", "peg 1 held"),
        FINISH])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint)
    assert s["run_error"] is None, s["run_error"]
    assert s["prologue"] == {} and s["model_usage_billed"] == s["model_usage"], \
        "no prologue on this arm, so the bill is the loop exactly as the archived summaries read it"
    rows = _call_rows(str(tmp_path), s["episode_id"])
    assert rows and {r["kind"] for r in rows} == {"decision"}, \
        f"decisions only, and the log is not empty: {[r['kind'] for r in rows]}"
    assert sum(r["http_requests_this_call"] for r in rows) == \
        s["episode_result"]["http_requests"]


def test_a_look_the_schema_refuses_is_a_row_that_carries_what_was_said(tmp_path):
    """#109 on the refusal arm: the answer that ended nothing is still the answer that cost two.

    `test_a_schema_rejected_answer_is_a_row_that_still_says_what_it_meant` filed this shape for a
    *decision*; the look had no row at all before #109, which is why an unreadable perception round
    in the archive reads as `run_error` plus a token count and nothing the reader can check. The
    fixture is the same defect `test_v02_perception_observe.py` uses on the reader directly: a
    bbox whose first coordinate is a word.
    """
    LOOK_BAD_BBOX = ('{"objects":[{"color":"red","bbox":["a",200,340,240],"confidence":0.5}]}')
    endpoint = _ScriptedSurvey([FINISH], [SURVEY_OFF_FRAME, LOOK_BAD_BBOX])
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=Ablation(condition="full"), gui="none",
                        planner="model", adapter=endpoint, perceive="vlm")

    rows = _call_rows(str(tmp_path), s["episode_id"])
    looks = [r for r in rows if r["kind"].startswith("perceive")]
    assert len(looks) == 1, \
        f"one logical look, one row, whatever the repair re-asked: {[r['kind'] for r in rows]}"
    r = looks[0]
    assert r["ok"] is False and r["http_requests_this_call"] == 2, \
        "the schema re-ask is folded into the look's row exactly as the adapter's retry is folded " \
        "into a decision's, and the row says it was two requests"
    assert any(e.startswith("objects.0.bbox") for e in r["schema_errors"]), r["schema_errors"]
    assert r["raw_chars"] == len(LOOK_BAD_BBOX)
    assert r["raw_preview"] == LOOK_BAD_BBOX and r["raw_truncated"] is False
    assert r["raw_sha256"] == hashlib.sha256(LOOK_BAD_BBOX.encode("utf-8")).hexdigest(), \
        "the digest is of the whole answer, so a reader can prove which bytes were refused"
    assert r["schema_repairs"] == 1 and r["usage"]["prompt_tokens"] == 1000, \
        "two asks of the same picture are two asks of tokens, billed in this one row"
    assert r["repaired_from"] == LOOK_BAD_BBOX, \
        "the refused answer and the re-ask that failed on it are the same bytes, and the row says so"


# --------------------------------------------------- what a motion itself measured --

def test_a_completed_motion_files_the_aim_it_was_given_where_the_feedback_record_cannot(
        tmp_path):
    """The log exists because an episode's own account of a bad aim was unreadable.

    Two readings of one episode, both in phase-log H-6: batch 4's layout 0 released the rod
    0.161 m from a hole its own archived frame measures 0.0393 m from, and feeding that
    frame back into the real executor lands it. The difference is in what the `place` was
    aimed at, and nothing in the artifact said so: `ExecutionFeedback` has no `notes`
    field, `core/runtime.py:691` forwards notes as rejection reasons only when the call was
    rejected, so a *completed* skill's measurements reached the episode dir nowhere. The
    first episode of batch 5 is the case that settled it: it executed two skills, spent 351
    steps and 21k prompt tokens, died on a 429 at the next round, and its `perception` block
    filed a survey and no motion at all. Phase-log H-9 item 5.

    So this test checks the boundary rather than a number. The aim sentence has to be in
    `episode_summary.json`, whose reader is whoever comes after this run; and it must not
    be in `events.jsonl`, because that file's records are the ones the model is shown, and
    a motor primitive's self-report getting onto that page would be the runtime speaking
    through the agent's eyes. `stub` carries the same primitives with no HTTP and no
    camera answer to be at the mercy of.
    """
    from embodied_agent.benchmark_mujoco.perceive import SURVEY_FRAME_SURFACE_FLOOR

    endpoint = _ScriptedEndpoint([
        _decide("pick", {"object_id": "peg 1"}, "the peg lies at the far side of the table",
                "peg 1 held"),
        _decide("place", {"object_id": "peg 1", "target_id": "socket 1"},
                "carried, so it has to reach the socket", "peg 1 in socket 1"),
        FINISH])
    # `wo_vlm` is the only arm a segmentation channel can coherently claim, and the one the
    # CLI resolves to for it (`perception/arm.py:arm_for_channel`).
    s = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=True, ablation=make_ablation("wo_vlm"), gui="none",
                        planner="model", adapter=endpoint, perceive="stub")
    surface = ((s["perception"].get("survey") or {}).get("frame_surface_px"))
    # The instrument's own health, not the run's: a dead depth buffer makes every picture-dependent
    # assertion below unmeasurable, and keying the skip on `run_error` as well let the same illness
    # read as a finding whenever the run happened to die for another reason. Widening the skip is a
    # real cost -- this test now checks nothing about a sick process -- so the number travels with
    # the verdict in both directions.
    if isinstance(surface, int) and surface < SURVEY_FRAME_SURFACE_FLOOR:
        pytest.skip(f"this process's depth buffer is not a picture: frame_surface_px="
                    f"{surface} of 480x480 (floor {SURVEY_FRAME_SURFACE_FLOOR}), "
                    f"the survey below could not have measured anything")
    assert s["run_error"] is None, f"frame_surface_px={surface}: {s['run_error']}"

    motions = s["perception"]["motions"]
    assert [m["skill"] for m in motions] == ["pick", "place"]
    assert len(motions) == s["skill_calls_executed"] + len(
        [m for m in motions if m["status"] == "rejected"]), \
        "the log holds one entry per motion *asked*; the count holds the ones that ran (#107)"
    assert [m["status"] for m in motions] == ["completed", "completed"]
    assert sum(m["env_steps_used"] for m in motions) <= s["env_steps"], \
        "the steps a motion charges itself are steps the episode spent"
    assert not any(word in json.dumps(motions) for word in SCORE_WORDS), \
        "a primitive's own account carries no scoreboard field"

    place = motions[-1]
    aim = place["notes"][0]
    assert aim.startswith("tip of peg 1 measured at (") and "(sensor)" in aim, \
        "the end the rod was steered by, and which snapshot it came from: " \
        f"frame_surface_px={surface} (floor {SURVEY_FRAME_SURFACE_FLOOR}), note was {aim!r}"
    quoted = [float(x) for x in aim.split("measured at (")[1].split(")")[0].split(",")]
    assert any(all(abs(float(e[i]) - quoted[i]) < 5e-4 for i in range(3))
               for fit in s["perception"]["axis_fits"] for e in fit["ends"]), \
            "the aim is a number the perception log also holds, so it can be audited"

    fits, holds = s["perception"]["axis_fits"], s["perception"]["hold_claims"]
    assert fits and holds, "a look that rendered leaves both instruments' answers behind"
    assert all("view" in r and r["observation_ref"] for r in fits + holds), \
        "each row names the frame and the snapshot it was read for"

    events = _events(os.path.join(str(tmp_path), "episodes", s["episode_id"]))
    feedback = [e for e in events if e["type"] == "execution_feedback"]
    assert feedback, "the calls were reported to the loop"
    assert not [e for e in feedback if "measured at (" in json.dumps(e, ensure_ascii=False)], \
        "the aim note stays off the page the model reads"


# ------------------------------------------------------------------ #117 -----
#: Both halves of one claim: the sentence a camera write-up is allowed to say about a
#: second camera, and the act that would answer it. batch8 measured the gap closed at the
#: decision level (`peg-insert-side-v3__L4__s4`): the model read #103's true sentence,
#: quoted it back, wrote "a re-observation … would be needed to proceed" — and then filed
#: `blocked`, because on this channel `observe` could not name a camera and no page field
#: said one existed. `perception/arm.py` implemented the request path the whole time;
#: `core/planner.py:32-36` lists `view` as observe's optional argument.
def test_observe_may_name_a_camera_and_nothing_else_enters_its_grammar():
    args, reasons = render("observe", {})
    assert args == {} and reasons == [], "a bare observe is still legal, and still means 'again, from where you just looked'"
    args, reasons = render("observe", {"view": "gripperPOV"})
    assert reasons == [], reasons
    assert "view" not in (args or {}), \
        "the cleaned pair carries the arguments the *motion* steers by; the viewpoint is read "\
        "off the call by `PerceptRuntime._before_execute`, which is where it belongs"
    _, reasons = render("observe", {"views": "gripperPOV"})
    assert any("does not take args['views']" in r for r in reasons), reasons


def test_the_page_names_the_cameras_this_run_can_measure_with():
    """A legal argument nobody can name is not an affordance.

    `skills.py` keeps the *shape* static; the run's camera list comes from the rig, so it is
    injected here rather than invented in the catalogue — and the privileged arm, which has
    no rig, gets no list and no permission.
    """
    from types import SimpleNamespace

    camera_arm = SimpleNamespace(perceiver=SimpleNamespace(views=("corner2", "gripperPOV")))
    catalogue = MujocoRuntime._skill_catalogue(camera_arm)
    text = json.dumps(catalogue["observe"]["args"], ensure_ascii=False)
    assert "corner2" in text and "gripperPOV" in text, \
        f"the two cameras have to be on the page the model reads, and the entry said {text!r}"

    state_arm = SimpleNamespace()          # `MujocoRuntime` alone: no perceiver attribute at all
    plain = MujocoRuntime._skill_catalogue(state_arm)
    assert plain["observe"]["args"] == CATALOGUE["observe"]["args"], \
        "the privileged arm measures from the simulator's state, so naming it a viewpoint to choose would be a lie"


def test_a_viewpoint_asked_of_an_arm_with_no_camera_is_refused_with_the_reason():
    """The refusal is legality, not a substitution: nothing moves, and nothing is re-measured
    from the one camera that happens to be there."""
    from types import SimpleNamespace

    from embodied_agent.core.contracts import Decision

    def _ask(args):
        return Decision(action="execute", context_id="ctx_probe",
                        based_on_state_version=1, goal_ref="g_probe",
                        execute=DecisionExecute(skill="observe", args=args, candidate_id=None),
                        rationale="probe", expected_effect="probe")

    world = SimpleNamespace(has_entity=lambda _id: True, target=lambda _id: None)
    no_camera = SimpleNamespace()
    ok, errs = MujocoRuntime._validate_execution(no_camera, _ask({"view": "gripperPOV"}), None, world)
    assert not ok and any("has none to point" in e for e in errs), errs
    ok, errs = MujocoRuntime._validate_execution(no_camera, _ask({}), None, world)
    assert ok and errs == [], "a bare observe stays legal on both arms"
    with_camera = SimpleNamespace(perceiver=SimpleNamespace(views=("corner2", "gripperPOV")))
    ok, _ = MujocoRuntime._validate_execution(with_camera, _ask({"view": "gripperPOV"}), None, world)
    assert ok, "the camera arm's own validator (`perception/arm.py:414-423`) is the one that knows which names are cameras"


def test_the_page_the_model_is_asked_from_carries_those_names_and_no_privileged_page_does(
        tmp_path):
    """The fourth link, tested instead of assumed.

    #117's page half is a claim about a *string*: `core/runtime.py:576` copies
    `_skill_catalogue()` into `ctx.skill_catalogue`, and `prompts.py:121` puts that object into
    `model_payload()["skills"]`, which `model_policy.py:98` concatenates verbatim into the user
    turn. Nothing in that chain filters, and nothing in the three tests above proves it -- so this
    one asks the production prompt-builder what it would have sent, and reads the answer off the
    adapter's own record of the request.

    The context is built with `model_construct` on purpose: the thing under test is payload
    assembly, not contract validation, and a fixture that had to satisfy every validator would be
    a fixture about this file rather than about the page. What is real here is the catalogue, the
    policy, the payload method and the assembled user turn.
    """
    from types import SimpleNamespace

    from embodied_agent.benchmark_mujoco.model_policy import MujocoModelPolicy
    from embodied_agent.benchmark_mujoco.prompts import MujocoDecisionContext
    from embodied_agent.benchmark_mujoco.runtime import MujocoRuntime as _MR

    class _PageEndpoint(_ScriptedEndpoint):
        def chat_json(self, system, user, *, kind, prompt_version, max_tokens=None):
            self.asked.append({"kind": kind, "prompt_version": prompt_version,
                               "system": system, "user": user})
            self.http_requests += 1
            return self.replies[0], {"kind": kind, "usage": {"prompt_tokens": 10,
                                                             "completion_tokens": 10}}

    def page_from(catalogue):
        ctx = MujocoDecisionContext.model_construct(
            schema_version="2", episode_id="probe", context_id="c1", round_index=1,
            state_version=1, observation_ref="per_1", goal_version=1,
            task=SimpleNamespace(utterance="place the peg in the socket",
                                 declared_constraints=[]),
            goal=SimpleNamespace(goal_id="g1", instruction="place the peg in the socket",
                                 assignments=[], prohibitions=[]),
            progress=[],
            world=SimpleNamespace(observation_ref="per_1",
                                  source=SimpleNamespace(value="sensor"),
                                  raw_observation="a table", parse_status="ok",
                                  location="table", held_object=None, entities=[],
                                  targets=[], facts=[]),
            skill_catalogue=catalogue, last_feedback=None, recent_feedbacks=[], attempts=[],
            current_subgoal=None, todo_summary=None, budget={}, feedbacks_included=0)
        endpoint = _PageEndpoint([_decide("observe", {"view": "gripperPOV"},
                                         "the wrist camera is the one that can see under the peg",
                                         "a measurement from gripperPOV")])
        decision = MujocoModelPolicy(endpoint).decide(ctx)
        return endpoint.asked[0]["user"], decision

    camera_page, decision = page_from(
        _MR._skill_catalogue(SimpleNamespace(perceiver=SimpleNamespace(
            views=("corner2", "gripperPOV")))))
    assert '"view": "one of: corner2, gripperPOV' in camera_page, \
        "the camera names never reached the turn the model is asked from"
    #: and the act it answers with is the one #117 made legal: the same `view` string the page
    #: offered comes back out the other side, unedited, into the call
    assert decision.execute.skill == "observe"
    assert dict(decision.execute.args) == {"view": "gripperPOV"}

    privileged_page, _ = page_from(_MR._skill_catalogue(SimpleNamespace()))
    assert "gripperPOV" not in privileged_page, \
        "a page for an arm with no camera must not advertise one"

# ------------------------------------------------------------------ §5.4 on this channel --
# These four sit AFTER every episode-running test above, on purpose. The stub channel's
# depth renderer has a documented illness whose cause is unknown (perceive.py, the
# AXIS_MIN_SPAN_M block: a rig built after earlier ones in the same process can hold a dead
# depth buffer, "the dead fit is not even reproducible, which is the point"). The motion
# test above is the deepest consumer of that pipeline, and its green history was measured
# against the rig history the older tests build; two more backends ahead of it (the
# seed/read pair below) shifted that history and the canonical suite caught the illness
# once where it had never fired before. New tests go last so every delivered test keeps
# exactly the renderer history its verdict was measured against; the memory tests' own
# verdicts are all privileged-channel or gate-level and read no depth at all.

def test_the_memory_arm_widens_exactly_its_own_gate_and_no_other():
    """§5.4 on this channel (H-58): with the episodic module *installed*, a
    `wo_episodic_memory` row is a module switched off rather than a refusal — and the
    widening stops there. The bare classes' answer above is unchanged because their gate
    reads their own `installed_modules`, and the experienced class still refuses the two
    modules this loop has no instrument for: no camera on the privileged side, no skill
    library anywhere on this channel."""
    from embodied_agent.benchmark_mujoco.planned_runtime import (
        MW_ARM_MODULES, MW_EXPERIENCED_ARM_MODULES, MujocoExperiencedRuntime)

    assert MujocoExperiencedRuntime.installed_modules == MW_ARM_MODULES + ("episodic_memory",)
    assert mw_arm_coherence(make_ablation("wo_episodic_memory")) != [], \
        "the bare loop must keep refusing: its gate is its own"
    for c in ("full", "wo_episodic_memory"):
        assert mw_arm_coherence(make_ablation(c), installed=MW_EXPERIENCED_ARM_MODULES) == [], c
    still = mw_arm_coherence(make_ablation("wo_skill_acquisition"),
                             installed=MW_EXPERIENCED_ARM_MODULES)
    assert still and "duplicate" in still[0]
    assert mw_arm_coherence(make_ablation("wo_vlm"), installed=MW_EXPERIENCED_ARM_MODULES), \
        "the privileged experienced loop has no camera to switch off"

    # the camera experienced class widens by the same one module, on top of the camera's own
    from embodied_agent.benchmark_mujoco.perceive import MujocoExperiencedPerceptRuntime
    assert MujocoExperiencedPerceptRuntime.installed_modules[-1] == "episodic_memory"
    assert not (mw_arm_coherence(make_ablation("wo_episodic_memory"),
                                 installed=MujocoExperiencedPerceptRuntime.installed_modules))


def test_the_runner_refuses_the_memory_arm_without_a_store(tmp_path):
    """The two arms of one refusal, both raised before a directory exists.

    A `wo_episodic_memory` episode with no store would measure a cold start and report it
    as an ablation; an unarmed episode with a store would hold an unread object beside a
    record that says no memory module ran. `evaluation/run.py:installed_module_refusals`
    is the desktop wording of the first half; this is the same sentence at this channel's
    other door."""
    from embodied_agent.episodic.store import ExperienceStore

    with pytest.raises(ValueError, match="needs --experience-store"):
        run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        ablation=make_ablation("wo_episodic_memory"))
    assert not os.path.exists(os.path.join(str(tmp_path), "episodes")), \
        "a refusal must not leave a batch behind"

    store = ExperienceStore(os.path.join(str(tmp_path), "experiences.json"))
    with pytest.raises(ValueError, match="silent"):
        run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                        armed=False, ablation=None, experience_store=store)
    assert os.path.exists(str(tmp_path)) and not os.path.exists(os.path.join(str(tmp_path), "episodes")), \
        "the unarmed refusal fires before an episode directory is built"


def test_the_cli_carries_the_same_two_refusals_and_the_one_accepted_door(monkeypatch, tmp_path):
    seen = _stub_episodes(monkeypatch)
    out = os.path.join(str(tmp_path), "refused")
    assert main(["run", "--ablation", "wo_episodic_memory", "--out", out]) == 2
    assert not seen and not os.path.exists(out), "a refusal must not leave a batch behind"

    store_path = os.path.join(str(tmp_path), "experiences.json")
    assert main(["run", "--unarmed", "--experience-store", store_path,
                 "--out", os.path.join(str(tmp_path), "unarmed")]) == 2
    assert not seen
    # an unreadable store refuses at the same door, before any simulator is started
    with open(store_path, "w", encoding="utf-8") as f:
        f.write("{not json at all}\n")
    assert main(["run", "--experience-store", store_path,
                 "--out", os.path.join(str(tmp_path), "broken")]) == 2
    assert not seen
    # and the door that opens: the arm is `full`, the store is the one loaded, and the
    # stub's kw is what a reader would otherwise have to trust the print line about
    with open(store_path, "w", encoding="utf-8") as f:
        f.write("\n")
    assert main(["run", "--experience-store", store_path,
                 "--out", os.path.join(str(tmp_path), "ok")]) == 0
    assert seen[-1]["ablation"].condition == "full"
    assert seen[-1]["experience_store"] is not None
    assert seen[-1]["experience_store"].path == store_path


def test_a_privileged_episode_with_a_store_runs_the_full_memory_loop(tmp_path):
    """The zero-spend end-to-end of §5.4 on the MuJoCo channel, seed and read (RQ3).

    Episode one files `memory_retrieval` against an empty store — a cold start says so in
    the record instead of looking like silence — writes one row *after* `episode_end`, and
    its `episode_summary.json` carries the same `episodic` block the desktop runner files.
    Episode two, same task, retrieves that row: the query side and the index side speak
    one vocabulary (`task_kind` is the environment both episodes ran). The one-extractor
    invariant is asserted, not claimed: an offline read of the archive re-derives the
    stored row field for field."""
    from embodied_agent.episodic.experience import experience_from_episode, read_episode_dir
    from embodied_agent.episodic.store import ExperienceStore

    store = ExperienceStore(os.path.join(str(tmp_path), "experiences.json"))
    s1 = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=0,
                         ablation=make_ablation("full"), gui="none", experience_store=store)
    assert s1["episode_id"].endswith("__memstore") and s1["run_error"] is None
    ep1 = os.path.join(str(tmp_path), "episodes", s1["episode_id"])
    events1 = _events(ep1)
    retrievals = [e for e in events1 if e["type"] == "memory_retrieval"]
    assert retrievals, "the store is queried even when empty: the lookup is the record"
    assert all(e["payload"]["store_size"] == 0 for e in retrievals)
    assert not [e for e in events1 if e["type"] == "memory_use"], \
        "nothing was retrieved, so no round can be judged against a row"
    writes = [e for e in events1 if e["type"] == "memory_write"]
    assert len(writes) == 1 and writes[0]["payload"]["written_after"] == "episode_end"
    end_idx = max(i for i, e in enumerate(events1) if e["type"] == "episode_end")
    assert events1.index(writes[0]) > end_idx, "memory_write lands after episode_end"
    episodic1 = s1["episodic"]
    assert episodic1["arm"] == "full" and episodic1["store_size_before"] == 0
    assert episodic1["store_size_after"] == 1
    assert episodic1["written_experience_id"] == writes[0]["payload"]["experience_id"]
    assert episodic1["memory_events"] == {
        "memory_retrieval": len(retrievals), "memory_use": 0, "memory_write": 1}

    row = store.all()[0]
    assert row.task_kind == ENV and row.episode_id == s1["episode_id"]
    assert row.run_ref == ep1, "the row is traceable to its run without the caller's help"
    # the one-extractor invariant: the online write and an offline rebuild of the same
    # archive produce the same bytes. `created_at`/`updated_at` are the record envelope's
    # construction stamps, not experience content — the two fields a rebuild may differ in.
    offline_summary, offline_events = read_episode_dir(ep1)
    rederived = experience_from_episode(offline_summary, offline_events)
    strip = lambda d: {k: v for k, v in d.items() if k not in ("created_at", "updated_at")}
    assert strip(rederived.model_dump(mode="json")) == strip(row.model_dump(mode="json"))

    s2 = run_one_episode(out_root=str(tmp_path), env_name=ENV, task_index=0, seed=1,
                         ablation=make_ablation("full"), gui="none", experience_store=store)
    assert s2["episode_id"] != s1["episode_id"] and s2["episode_id"].endswith("__memstore")
    ep2 = os.path.join(str(tmp_path), "episodes", s2["episode_id"])
    events2 = _events(ep2)
    retrievals2 = [e for e in events2 if e["type"] == "memory_retrieval"]
    assert retrievals2 and any(e["payload"]["retrieved"] for e in retrievals2), \
        "the seed episode's row is relevant to the read episode's own query"
    assert any(e["payload"]["store_size"] == 1 for e in retrievals2)
    uses = [e for e in events2 if e["type"] == "memory_use"]
    assert uses, "a retrieved row reaches the ledger, so every round's judgement is filed"
    assert all(e["payload"]["retrieved"] for e in uses)
    episodic2 = s2["episodic"]
    assert episodic2["store_size_before"] == 1 and episodic2["store_size_after"] == 2
    assert episodic2["retrievals"] == len(retrievals2) and episodic2["rows_retrieved"] > 0
    assert episodic2["written_experience_id"] != episodic1["written_experience_id"]
    # the read episode wrote its own row, and the two ids are the two episodes
    assert {episodic1["written_experience_id"], episodic2["written_experience_id"]} \
        == set(store.ids())
