"""The text attachment on the *real* ALFWorld engine (SPEC-BST 13.2, 13.3, 13.6).

`tests/contract/test_bst_text_backend.py` proves the loop does nothing for the model
over a transcript. This file proves the other half, which a transcript cannot: that
a real reset is the official observation, that one legal skill call moves a real
game exactly one step, that the engine's own `done` — not the agent's word — ends an
episode and is scored as the flag says, and that the official handcoded expert's own
winning trajectories replay through this same path and come out won.

The last group is a plumbing fixture, not a result: it drives `ScriptedSource`, so no
model is involved and nothing here is evidence about one (SPEC-BST 10). The games are
pinned by the sha256 of their `game.tw-pddl` and resolved against `$ALFWORLD_DATA`, so
an install that moved fails with a named reason instead of silently testing something
else.

Run:
  /home/czx/bstvenv/bin/python -m pytest tests/integration/test_bst_alfworld_live.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from embodied_agent.benchmark.parser import parse
from embodied_agent.benchmark.prompts import BANNED_IN_PAYLOAD
from embodied_agent.benchmark.runtime_alfred import AlfredRuntime, bst_budgets
from embodied_agent.benchmark.state import goal_from_instruction, parse_status_of
from embodied_agent.core.contracts import TaskInput
from embodied_agent.evaluation.sources import ScriptedSource

try:
    from embodied_agent.benchmark.backend import AlfredTextBackend
    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - the reason is the skip message
    AlfredTextBackend = None
    _IMPORT_ERROR = f"{type(e).__name__}: {e}"

DATA_DIR = os.environ.get("ALFWORLD_DATA") or os.path.expanduser("~/.cache/alfworld")
FIXTURE = Path(__file__).parent.parent / "fixtures" / "bst_alfworld_expert_replay.json"

# one game of the enumeration Phase 0 pinned: a pick-and-place whose text the parser
# was derived from, in the split the benchmark's full set is drawn from
GAME_REL = ("json_2.1.1/valid_unseen/pick_and_place_simple-SaltShaker-None-Drawer-10/"
            "trial_T20190909_021728_339782/game.tw-pddl")
GAME_SHA256 = "3d72c30feb708eb29d68e439619d187a15e6d12e2b6aacec46749eb6243adc13"

_MISSING = _IMPORT_ERROR or not Path(DATA_DIR, GAME_REL).is_file() or not FIXTURE.is_file()
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"text backend or pinned data unavailable: {_MISSING or 'ok'}")


def _game(rel: str, sha: str | None = None) -> str:
    """A pinned game file, or a named skip: the *data* moving is infrastructure."""
    path = os.path.join(DATA_DIR, rel)
    digest = _sha(rel)
    if sha and digest != sha:
        pytest.skip(f"{rel}: sha256 {digest[:12]} is not the pinned {sha[:12]} — the ALFWorld "
                    f"install is not the one the trace was recorded on")
    return path


def _sha(rel: str) -> str:
    return hashlib.sha256(Path(os.path.join(DATA_DIR, rel)).read_bytes()).hexdigest()


def _episode(tmp_path, name, rel, script, *, env_actions: int = 60, rounds: int = 80,
             sha: str | None = None, backend=None):
    """One episode through the production path: real backend, real runtime, real loop."""
    backend = backend or AlfredTextBackend(_game(rel, sha or (GAME_SHA256 if rel == GAME_REL
                                                              else None)),
                                           max_env_actions=env_actions)
    raw = backend.start()
    instruction = parse(raw).instruction or ""
    goal = goal_from_instruction(instruction, state_version=0)
    task = TaskInput(task_id=f"live/{name}", utterance=instruction, language="en",
                     declared_constraints=[])
    run_dir = tmp_path / name
    shutil.rmtree(run_dir, ignore_errors=True)
    rt = AlfredRuntime(backend, str(run_dir),
                       bst_budgets(env_actions=env_actions, decision_rounds=rounds),
                       f"ep_{name}")
    src = ScriptedSource(script, on_exhausted="repeat_last")
    result = rt.run_episode(task, goal, src, mode="B")
    view = backend.eval_view()
    fx = type("Fixture", (), {"rt": rt, "src": src, "backend": backend, "result": result,
                              "view": view, "goal": goal, "raw": raw, "instruction": instruction,
                              "events": rt.store.read_all(),
                              "payload": json.dumps([c.model_payload() for c in src.seen],
                                                    ensure_ascii=False)})()
    backend.close()
    return fx


def _receptacles(raw: str) -> list[str]:
    return sorted(e for e, a in parse(raw).entities.items() if a.get("role") == "receptacle")


# ------------------------------------------------------- 13.3: the real observation ----


def test_a_real_reset_is_the_official_observation_and_nothing_more():
    backend = AlfredTextBackend(_game(GAME_REL, GAME_SHA256))
    raw = backend.start()
    p = parse(raw)
    view = backend.eval_view()
    try:
        assert raw.startswith("-= Welcome to TextWorld, ALFRED! =-")
        assert p.instruction == "put some saltshaker on drawer"
        assert goal_from_instruction(p.instruction, state_version=0).well_formed
        # Phase 0 measured 0 sentences left unread across 1,202 complete observations
        # (296 expert + 852 probe + 54 engine refusals); what a rule does not read
        # stays verbatim in `unparsed`, which is how a future gap stays checkable
        assert p.facts and parse_status_of(p.facts, list(p.unparsed)) == "parsed"
        # the engine keeps a command list for its own parser; this process never asks
        # for it, so it is not in the response or in the evaluation view either
        assert "Available commands" not in raw
        assert not any(k in view for k in ("admissible_commands", "expert_plan", "task_type"))
        assert set(view) >= {"env_done", "official_won", "env_steps", "max_env_actions",
                             "reward", "error"}
        assert view["env_steps"] == 0 and view["env_done"] is False
    finally:
        backend.close()


# ---------------------------------------------- 13.2: one action, one real step --------


def test_one_legal_skill_call_is_exactly_one_real_environment_step(tmp_path):
    probe = AlfredTextBackend(_game(GAME_REL, GAME_SHA256))
    recs = _receptacles(probe.start())
    probe.close()
    script = ([("execute", ("alfred_go", {"target_id": recs[i % len(recs)]})) for i in range(3)]
              + [("finish", None)])
    ep = _episode(tmp_path, "one_step", GAME_REL, script)
    assert ep.view["env_steps"] == 3
    assert (ep.result.skill_calls, ep.result.decision_rounds) == (3, 4)
    versions = [o.state_version for o in ep.rt.observations]
    refs = [o.observation_ref for o in ep.rt.observations]
    assert versions == sorted(set(versions))                 # every step is a new version
    assert len(refs) == len(set(refs))                        # and a new ref
    assert ep.result.termination_reason == "AGENT_FINISH"


def test_a_refusal_sends_nothing_and_costs_no_environment_step(tmp_path):
    """The engine is never consulted to find out whether a name was real: inventing a
    target would be the adapter's choice, and asking the engine is the answer sheet."""
    ep = _episode(tmp_path, "refusal", GAME_REL,
                  [("execute", ("alfred_take", {"object_id": "saltshaker",
                                                "target_id": "drawer"}))])
    assert ep.view["env_steps"] == 0
    assert ep.result.skill_calls == 0
    assert ep.result.rejected_decisions == ep.result.decision_rounds
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    reasons = [r for fb in ep.rt.feedback_history for r in fb.rejection_reasons]
    assert all("instance reference" in r for r in reasons) and reasons
    assert ep.result.artifacts["note"] == "semantic repair budget"      # the loop's bound


# ------------------------------------- 13.6: who ended it, and what it is worth --------


def test_the_engine_truncates_at_its_own_action_budget(tmp_path):
    probe = AlfredTextBackend(_game(GAME_REL, GAME_SHA256))
    recs = _receptacles(probe.start())
    probe.close()
    ep = _episode(tmp_path, "truncated", GAME_REL,
                  [("execute", ("alfred_go", {"target_id": recs[i % len(recs)]}))
                   for i in range(70)], env_actions=60)
    assert ep.view["env_done"] is True and ep.view["env_steps"] == 60
    assert ep.result.termination_reason == "ENV_TERMINATED"
    # `done` here is truncation: the official flag says the task was not solved, and
    # that is an evaluation result rather than a claim the model was shown
    assert ep.result.score_complete_success is False
    assert ep.result.terminal_status.value == "failed"
    assert ep.result.false_finish_attempts == 0
    official = [r["payload"] for r in ep.events if r["type"] == "episode_official"]
    assert official[0]["false_finish"] is False
    assert official[0]["official_success"] is False
    assert len([r for r in ep.events if r["type"] == "termination"]) == 1


def test_an_agent_finish_on_a_real_game_is_the_agent_s_own_word(tmp_path):
    probe = AlfredTextBackend(_game(GAME_REL, GAME_SHA256))
    recs = _receptacles(probe.start())
    probe.close()
    ep = _episode(tmp_path, "finish", GAME_REL,
                  [("execute", ("alfred_go", {"target_id": recs[0]})), ("finish", None)])
    assert ep.src.calls == 2                        # nothing was asked after the finish
    assert ep.result.termination_reason == "AGENT_FINISH"
    assert ep.result.terminal_status.value == "failed"      # the game was not won
    official = [r["payload"] for r in ep.events if r["type"] == "episode_official"][0]
    assert official["false_finish"] is True
    # the loop calls its own finish branch `success`; the text backend says so and
    # restates the status from the official flag rather than the agent's claim
    restated = [r["payload"] for r in ep.events if r["type"] == "terminal_status_restated"]
    assert restated and restated[0]["from_status"] == "success"
    assert restated[0]["to_status"] == "failed"


@pytest.mark.parametrize("action", ["blocked", "clarify"])
def test_a_control_decision_leaves_the_real_game_untouched(tmp_path, action):
    ep = _episode(tmp_path, f"control_{action}", GAME_REL,
                  [(action, None if action == "blocked" else "which salt shaker?")])
    assert (ep.view["env_steps"], ep.result.skill_calls) == (0, 0)
    assert ep.result.termination_reason == ("AGENT_BLOCKED" if action == "blocked"
                                            else "NEEDS_CLARIFICATION")
    assert ep.src.calls == 1


# --------------------------------------------- 13.3: information isolation, real game --


def test_a_real_payload_carries_no_privileged_and_no_geometry_key(tmp_path):
    probe = AlfredTextBackend(_game(GAME_REL, GAME_SHA256))
    recs = _receptacles(probe.start())
    probe.close()
    ep = _episode(tmp_path, "isolation", GAME_REL,
                  [("execute", ("alfred_go", {"target_id": rec})) for rec in recs[:3]]
                  + [("finish", None)])
    for banned in BANNED_IN_PAYLOAD:
        assert f'"{banned}"' not in ep.payload, banned
    for geom in ("position", "yaw_rad", "inner_half", "occupancy", "center", "pose",
                 "geometry"):
        assert f'"{geom}"' not in ep.payload, geom
    for word in ("official_won", "env_done", "reward", "expert"):
        assert word not in ep.payload
    # the instruction is the whole goal, and it is the environment's own sentence
    assert json.loads('"%s"' % ep.instruction.replace("\n", " ")) in ep.payload


# ---------------------------------- 13.2/13.6: the official expert's own wins replay --


def _replays():
    if not FIXTURE.is_file():
        return []
    data = json.loads(FIXTURE.read_text())
    return [(r["fixture"], r) for r in data["replays"]]


REPLAYS = _replays()


@pytest.mark.parametrize("fixture,record", REPLAYS, ids=[name for name, _ in REPLAYS])
def test_an_official_expert_win_replays_through_the_loop(tmp_path, fixture, record):
    """Every command the official solver actually issued becomes one skill call, so a
    win it reached inside the action budget must reach `official_won` here too. What
    this rules out is the attachment eating, adding or reordering an action; it says
    nothing about a model, and no model was asked."""
    script = [("execute", (s["skill"], s["args"])) for s in record["skills"]]
    # the game is pinned by sha256 through `_episode`, so an install that moved skips
    # with a named reason instead of failing as if the attachment had
    ep = _episode(tmp_path, f"expert_{fixture.replace('|', '_')}", record["game_rel"], script,
                  sha=record["game_tw_pddl_sha256"])
    assert ep.instruction == record["instruction"]
    assert ep.view["env_steps"] == record["env_actions_to_done"]
    assert ep.result.skill_calls == len(record["skills"])
    assert ep.result.rejected_decisions == 0                # nothing the loop refused
    # the round whose action ended the game never asks again: `done` stops the loop
    assert ep.result.decision_rounds == len(record["skills"])
    assert ep.result.termination_reason == "ENV_TERMINATED"
    assert ep.result.score_complete_success is record["official_won"] is True
    assert ep.result.terminal_status.value == "success"
    official = [r["payload"] for r in ep.events if r["type"] == "episode_official"][0]
    assert official["false_finish"] is False


def test_the_expert_traces_that_did_not_finish_in_the_budget_are_recorded_not_run():
    """3 of the 12 development traces the official solver never finished inside 60
    actions. They are counted, not quietly dropped: the cap is a property of the run
    (SPEC-BST 5.3), so this is the measurement of how often the reference solver
    itself needs more than it."""
    data = json.loads(FIXTURE.read_text())
    assert {s["fixture"] for s in data["skipped"]} == {
        "pick_heat_then_place_in_recep|0", "pick_two_obj_and_place|0",
        "pick_two_obj_and_place|1"}
    assert all(s["steps_recorded"] == 60 for s in data["skipped"])
    assert len(data["replays"]) + len(data["skipped"]) == 12
