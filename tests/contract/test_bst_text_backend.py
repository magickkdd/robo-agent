"""SPEC-BST 13.1–13.7 on the text attachment, without a game engine.

The convention the rest of this repository follows applies here: a test substitutes
only the decision source, and everything else is the production path — the real
`AlfredRuntime`, the real `AlfredTextExecutor`, the real `render`, the real
`Runtime.run_episode`. What is faked is the *world*, because an engine is not the
claim under test: these tests must show that the adapter cannot do the agent's work,
that one legal action is one command, and that no evaluation-only byte reaches the
decision chain. A transcript backend makes each of those observable exactly, and
keeps the file runnable in either interpreter.

The live-engine counterparts (real resets, the official win flag, all six task
types) are in `tests/integration/test_bst_alfworld_live.py`.
"""
from __future__ import annotations

import json

import pytest

from embodied_agent.benchmark.parser import parse
from embodied_agent.benchmark.runtime_alfred import AlfredRuntime, bst_budgets
from embodied_agent.benchmark.source import decision_from_payload
from embodied_agent.benchmark.state import goal_from_instruction
from embodied_agent.core.contracts import (
    DecisionSchemaError,
    FailureCode,
    TaskInput,
    TerminalStatus,
)
from embodied_agent.core.runtime import HISTORY_CAP
from embodied_agent.evaluation.sources import ScriptedSource

MARKER = "PRIV-MARKER-7c11"

RESET = ("-= Welcome to TextWorld, ALFRED! =-\n\nYou are in the middle of a room. Looking "
         "quickly around you, you see a cabinet 6, a shelf 2, and a mug 2.\n\nYour task is "
         "to: Put the mug 2 on the shelf 2.")

REPLIES = {
    "go to shelf 2": "You arrive at shelf 2. On the shelf 2, you see a book 1.",
    "go to cabinet 6": "You arrive at cabinet 6. The cabinet 6 is closed.",
    "open cabinet 6": "You open the cabinet 6. In it, you see a mug 4.",
    "take mug 2 from shelf 2": "Nothing happens.",
    "move mug 2 to shelf 2": "You move the mug 2 to the shelf 2.",
    "look": "You are in the middle of a room. Looking quickly around you, you see nothing.",
    "inventory": "You are carrying: nothing.",
}


class TranscriptBackend:
    """`AlfredTextBackend`'s interface over a dict, plus the evaluation-only fields."""

    command_timeout_s = 10.0

    def __init__(self, replies=None, *, reset=RESET, eval_extra=None, fault_on=None):
        self.raw = reset
        self.replies = dict(REPLIES if replies is None else replies)
        self.commands: list[str] = []
        self.env_steps = 0
        self.max_env_actions = 60
        self.reset_seconds = 0.05
        self.last_step_seconds = 0.01
        self.eval_extra = dict(eval_extra or {})
        self.fault_on = set(fault_on or ())
        self.done = False
        self.won = False
        self.error = None

    def step(self, command: str) -> str:
        # recorded before the fault is raised, exactly as the real backend hands the
        # string to `env.step` before it can fail: what a transcript can prove is that
        # this process sent the command once, not what the engine did with it
        self.commands.append(command)
        if command in self.fault_on:
            # an engine that dies mid-command: whether it reached the world is not
            # knowable, and the answer must not be "send it again"
            self.error = {"phase": "step", "type": "RuntimeError",
                          "message": f"{command!r}: the engine process died"}
            raise RuntimeError(self.error["message"])
        if len(self.commands) > self.max_env_actions:
            raise RuntimeError(f"env action budget {self.max_env_actions} exhausted")
        self.env_steps += 1
        self.raw = self.replies.get(command, "Nothing happens.")
        if self.eval_extra.get("done_on") and self.env_steps >= int(self.eval_extra["done_on"]):
            self.done, self.won = True, bool(self.eval_extra.get("won"))
        return self.raw

    def eval_view(self) -> dict:
        return {"env_done": self.done, "official_won": self.won, "env_steps": self.env_steps,
                "max_env_actions": self.max_env_actions, "reward": 1 if self.won else 0,
                "error": dict(self.error) if self.error else None,
                "reset_seconds": self.reset_seconds, "last_step_seconds": self.last_step_seconds,
                "official_note": self.eval_extra.get("note", "")}


class Fixture:
    """The episode, the source that drove it, and the records it left behind."""

    def __init__(self, runtime, source, backend, result):
        self.rt, self.src, self.backend, self.result = runtime, source, backend, result

    @property
    def payloads(self) -> str:
        """Everything the model was shown, as one string to search."""
        return json.dumps([c.model_payload() for c in self.src.seen], ensure_ascii=False)

    @property
    def events(self) -> list[dict]:
        return self.rt.store.read_all()

    def of(self, kind: str) -> list[dict]:
        return [r["payload"] for r in self.events if r["type"] == kind]

    def kinds(self) -> list[str]:
        return [r["type"] for r in self.events]


def run(tmp_path, name, script, *, backend=None, budgets=None, tokens=600_000, source=None):
    backend = backend or TranscriptBackend()
    instruction = parse(backend.raw).instruction or ""
    goal = goal_from_instruction(instruction, state_version=0)
    task = TaskInput(task_id=f"contract/{name}", utterance=instruction, language="en",
                     declared_constraints=[])
    rt = AlfredRuntime(backend, f"{tmp_path}/{name}", budgets or bst_budgets(),
                       f"ep_{name}", max_episode_tokens=tokens)
    src = source or ScriptedSource(script, on_exhausted="repeat_last")
    result = rt.run_episode(task, goal, src, mode="B")
    return Fixture(rt, src, backend, result)


# ------------------------------------------------ 13.1: the action is not done for it --


def test_the_adapter_sends_nothing_the_model_did_not_name(tmp_path):
    """A `take` from a closed receptacle is one command, not a navigation-and-open
    sequence; an argument that is not an instance reference is refused, and the
    refusal costs the model a round rather than buying it a different action."""
    ep = run(tmp_path, "no_substitution",
             [("execute", ("alfred_take", {"object_id": "mug", "target_id": "shelf"}))])
    assert ep.backend.commands == []
    assert ep.result.skill_calls == 0
    reasons = [r for fb in ep.rt.feedback_history for r in fb.rejection_reasons]
    assert any("instance reference" in r for r in reasons), reasons
    # the frozen loop bounds repeated refusals twice: the repair budget (2) binds
    # before the identical-invalid run does, and both are the loop's own budgets
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert ep.result.artifacts["note"] == "semantic repair budget"


def test_a_desktop_verb_has_no_text_equivalent(tmp_path):
    ep = run(tmp_path, "no_desktop_verb",
             [("execute", ("place", {"object_id": "mug 2", "target_id": "shelf 2"}))])
    assert ep.backend.commands == []
    dumped = json.dumps([r for fb in ep.rt.feedback_history for r in fb.rejection_reasons])
    assert "belongs to the desktop backend" in dumped


def test_the_environments_own_help_command_cannot_be_asked_for(tmp_path):
    """`help` prints a game's admissible-command list, so its reply is the one public
    sentence that would carry hidden legal actions into feedback. It is in no catalogue
    here, and the frozen schema refuses the *name* before anything is rendered: asking
    costs a decision and cannot cost a step (SPEC-BST 3.5, 4.5)."""
    ep = run(tmp_path, "help_skill",
             [("execute", ("alfred_go", {"target_id": "shelf 2"})), ("finish", None)])
    with pytest.raises(DecisionSchemaError) as err:
        decision_from_payload({"action": "execute",
                               "execute": {"skill": "help", "args": {}, "candidate_id": None}},
                              ep.src.seen[0], {})
    assert any(r.startswith("execute.skill:") for r in err.value.reasons), err.value.reasons
    assert ep.backend.commands == ["go to shelf 2"]
    assert "Available commands" not in ep.payloads


def test_a_name_the_model_invented_is_answered_by_the_engine_not_repaired(tmp_path):
    """`mug 9` was never observed. It is still sent verbatim: deciding the model meant
    `mug 2` would be the adapter choosing the target of the action."""
    ep = run(tmp_path, "verbatim", [
        ("execute", ("alfred_move", {"object_id": "mug 9", "target_id": "shelf 2"})),
        ("finish", None)])
    assert ep.backend.commands == ["move mug 9 to shelf 2"]
    assert any("Nothing happens." in (fb.environment_text or "") for fb in ep.rt.feedback_history)


def test_no_candidate_list_is_invented(tmp_path):
    ep = run(tmp_path, "no_candidates", [("finish", None)])
    for ctx in ep.src.seen:
        payload = ctx.model_payload()
        assert ctx.candidates == [] and payload["candidates"] == []
        assert payload["candidates_truncated"] == 0
        assert payload["world"]["targets"] == []


# ------------------------------------------------------- 13.2: one action, one step ----


def test_one_legal_execute_is_exactly_one_environment_step(tmp_path):
    ep = run(tmp_path, "one_step", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_inventory", {})),
        ("execute", ("alfred_move", {"object_id": "mug 2", "target_id": "shelf 2"})),
        ("finish", None)])
    assert ep.backend.commands == ["go to shelf 2", "inventory", "move mug 2 to shelf 2"]
    assert (ep.backend.env_steps, ep.result.skill_calls, ep.result.decision_rounds) == (3, 3, 4)
    assert ep.result.termination_reason == "AGENT_FINISH"


def test_a_semantic_failure_still_costs_exactly_one_step(tmp_path):
    ep = run(tmp_path, "refused_step", [
        ("execute", ("alfred_take", {"object_id": "mug 2", "target_id": "shelf 2"})),
        ("finish", None)])
    assert ep.backend.env_steps == 1
    row = ep.rt.feedback_history[0]
    assert row.status == "completed" and row.failure_code is None
    assert any(f.kind == "no_effect" for f in ep.rt.world.facts)


def test_reading_the_cached_response_takes_no_step_and_invents_no_ref(tmp_path):
    ep = run(tmp_path, "cache", [("finish", None)])
    first, again = ep.rt.observe(), ep.rt.observe()
    assert (first.observation_ref, first.state_version) == (again.observation_ref,
                                                            again.state_version)
    assert ep.backend.env_steps == 0
    assert ep.of("observation_cached")


def test_a_repeated_sentence_is_a_new_measurement_but_not_new_facts(tmp_path):
    """SPEC-BST 4.3 and 5.3 are about different things: the version may move because
    a step happened, while the repeat guard must still see that nothing stated
    changed — otherwise a world that answers the same way forever becomes an
    unlimited budget."""
    backend = TranscriptBackend(replies={"go to shelf 2": "Nothing happens."})
    ep = run(tmp_path, "same_text", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "shelf 2"}))], backend=backend)
    versions = [o.state_version for o in ep.rt.observations]
    assert len(set(versions)) == len(versions)
    assert ep.backend.env_steps == 4
    assert ep.result.termination_reason == "REPEAT_GUARD"
    assert ep.result.identical_invalid_attempts == 4


def test_the_environment_action_cap_is_the_skill_call_cap(tmp_path):
    """SPEC-BST 3.5 makes them the same count, so the frozen ledger needs no second
    counter — and no step may be taken past the cap."""
    script = [("execute", ("alfred_go", {"target_id": ["shelf 2", "cabinet 6"][i % 2]}))
              for i in range(70)]
    ep = run(tmp_path, "action_cap", script)
    assert ep.backend.env_steps <= 60 and ep.result.skill_calls <= 60
    assert ep.result.termination_reason in ("BUDGET_EXHAUSTED", "ENV_TERMINATED")


# ---------------------------------------------------------- 13.3: information isolation -


def test_evaluation_only_values_never_reach_the_decision_chain(tmp_path):
    """The marker stands for the official score, the hidden subgoal state and any
    other privileged fact. It may appear in the terminal records, which exist only
    after the loop stopped, and nowhere a model can read."""
    backend = TranscriptBackend(eval_extra={"note": MARKER, "done_on": 2, "won": False})
    ep = run(tmp_path, "isolation", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "cabinet 6"})),
        ("finish", None)], backend=backend)
    assert MARKER not in ep.payloads
    for fb in ep.rt.feedback_history:
        assert MARKER not in json.dumps(fb.model_dump(mode="json"), ensure_ascii=False)
        assert MARKER not in json.dumps(fb.short(), ensure_ascii=False)
    for record in ep.events:
        if record["type"] in ("decision_context", "execution_feedback", "observation",
                              "decision", "skill_call", "episode_start"):
            assert MARKER not in json.dumps(record, ensure_ascii=False), record["type"]
    assert ep.of("episode_official")
    assert ep.result.termination_reason == "ENV_TERMINATED"
    assert ep.result.artifacts.get("note") != MARKER


def test_the_snapshot_carries_no_privileged_and_no_geometry_field(tmp_path):
    ep = run(tmp_path, "geometry", [("finish", None)])
    world = ep.rt.terminal_world
    assert world.targets == [] and world.occupancy == []
    for e in world.entities:
        assert e.pose is None and e.geometry is None
        assert e.supported_by == "unknown" and e.at_rest == "unknown"
    text = json.dumps(world.model_dump(mode="json"), ensure_ascii=False)
    for banned in ("admissible_commands", "expert_plan", "task_type", "reward", "\"won\"",
                   "score", "solution", "position", "privileged"):
        assert banned not in text, banned


# ------------------------------------------------------- 13.4: no automatic memory -----


def test_a_fact_stops_being_current_when_the_text_stops_stating_it(tmp_path):
    """Saw a book on a shelf, walked to another receptacle, got a sentence about
    that one: the new snapshot keeps neither the book's position as a current fact
    nor a claim that it moved. What the agent may still recall is the bounded
    feedback window v0.1 already had, each row under its own observation ref."""
    ep = run(tmp_path, "memory", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "cabinet 6"})),
        ("finish", None)])
    seen, left = ep.src.seen[1].world, ep.src.seen[2].world
    assert any(f.kind == "contents" and f.related == "book 1" for f in seen.facts)
    assert not any("book 1" in (f.subject, f.related) for f in left.facts)
    assert "book 1" not in [e.entity_id for e in left.entities]
    assert all(f.observation_ref == seen.observation_ref for f in seen.facts)
    assert all(f.observation_ref == left.observation_ref for f in left.facts)


def test_the_only_history_is_the_frozen_window(tmp_path):
    ep = run(tmp_path, "window", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "cabinet 6"})),
        ("finish", None)])
    payload = ep.src.seen[-1].model_payload(feedback_depth=3)
    assert len(payload["recent_feedbacks"]) <= 3
    assert len(ep.rt.feedback_history) <= HISTORY_CAP
    assert len(ep.rt.observations) == len({o.observation_ref for o in ep.rt.observations})
    rows = {row.post_observation_ref for row in ep.rt.feedback_history}
    assert all(r for r in rows)


# --------------------------------------------------------- 13.5: unknown vs not-said ---


def test_unread_text_is_kept_verbatim_and_labelled(tmp_path):
    """A sentence no rule reads is a measured fact about the *parser*, not about the
    world: the text stays where it was, whole, and says so."""
    odd = "You contemplated the shelf 2 for a moment."
    backend = TranscriptBackend(reset=f"{RESET}\n\n{odd}")
    ep = run(tmp_path, "unparsed", [("finish", None)], backend=backend)
    world = ep.src.seen[0].world
    assert odd in world.raw_observation and odd in world.unparsed
    assert world.parse_status == "partial"
    assert ep.src.seen[0].model_payload()["observation"]["parse_status"] == "partial"
    assert ep.src.seen[0].model_payload()["observation"]["text"] == world.raw_observation


def test_a_response_no_rule_reads_is_unparsed_not_empty(tmp_path):
    """`unparsed` and "there are no facts" are the same claim here, and the episode
    still shows the model the sentence rather than an empty world. A reset with no
    instruction at all is a different thing: a malformed task, clarified at once,
    not a world to act in."""
    gibberish = "Zzz. Blah blah 42."
    backend = TranscriptBackend(replies={**REPLIES, "go to shelf 2": gibberish})
    ep = run(tmp_path, "all_unparsed",
             [("execute", ("alfred_go", {"target_id": "shelf 2"})), ("finish", None)],
             backend=backend)
    world = ep.src.seen[1].world
    assert world.parse_status == "unparsed"
    # kept per sentence, each one verbatim: nothing is merged, dropped or rewritten
    assert world.unparsed == ["Zzz.", "Blah blah 42."] and world.facts == []
    assert world.raw_observation == gibberish
    assert ep.src.seen[1].model_payload()["observation"]["parse_status"] == "unparsed"

    bare = run(tmp_path, "no_instruction", [("finish", None)],
               backend=TranscriptBackend(reset=gibberish))
    assert (bare.src.calls, bare.backend.env_steps) == (0, 0)
    assert bare.result.termination_reason == "NEEDS_CLARIFICATION"


def test_no_holding_report_is_not_an_empty_hand(tmp_path):
    backend = TranscriptBackend(replies={"look": "You are in the middle of a room. Looking "
                                                 "quickly around you, you see a shelf 2."})
    ep = run(tmp_path, "hands", [("execute", ("observe", {})), ("finish", None)],
             backend=backend)
    after_look = ep.src.seen[1].world
    assert after_look.held_object == "unknown"
    assert all(str(e.held) == "unknown" for e in after_look.entities)
    again = run(tmp_path, "hands_reported",
                [("execute", ("alfred_inventory", {})), ("finish", None)])
    reported = again.src.seen[1].world
    assert reported.held_object is None                  # "carrying: nothing."
    assert all(e.held is False for e in reported.entities)


def test_the_verifier_of_a_text_task_always_answers_unknown(tmp_path):
    """SPEC-BST 4.6: there is no natural-language goal verifier here, and the hidden
    official flag is not one either. The progress row the model sees says so, and
    the score still comes from the official flag at the end."""
    backend = TranscriptBackend(eval_extra={"done_on": 1, "won": True})
    ep = run(tmp_path, "unknown_progress",
             [("execute", ("alfred_go", {"target_id": "shelf 2"}))], backend=backend)
    assert [p.value.value for p in ep.src.seen[0].progress] == ["unknown"]
    assert ep.src.seen[0].progress[0].unmeasured
    row = ep.src.seen[0].model_payload()["progress"][0]
    assert row["value"] == "unknown" and row["unmeasured"]
    assert ep.result.score_complete_success is True
    assert ep.result.terminal_status == TerminalStatus.success


# ----------------------------------------------------------- 13.6: termination ---------


def test_an_agent_finish_ends_the_episode_without_another_request(tmp_path):
    ep = run(tmp_path, "finish_stops", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})), ("finish", None)])
    assert ep.src.calls == 2                             # no round after the finish
    assert (ep.result.rejected_decisions, ep.result.false_finish_attempts) == (0, 0)
    assert ep.result.termination_reason == "AGENT_FINISH"
    assert ep.result.terminal_status == TerminalStatus.failed    # never verified, so never won
    assert ep.result.score_complete_success is False
    assert not ep.of("finish_check") or ep.of("finish_check")[0]["verifier"] == "unknown"


def test_the_environment_done_and_the_agent_finish_are_different_records(tmp_path):
    backend = TranscriptBackend(eval_extra={"done_on": 2, "won": True})
    ep = run(tmp_path, "env_done", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_go", {"target_id": "cabinet 6"}))], backend=backend)
    assert ep.result.termination_reason == "ENV_TERMINATED"
    assert ep.result.score_complete_success is True
    assert ep.src.calls == 2                             # done was not fed back as a prompt
    assert ep.kinds().count("termination") == 1 and ep.kinds().count("episode_official") == 1
    official = ep.of("episode_official")[0]
    assert official["termination_reason"] == "ENV_TERMINATED" and official["false_finish"] is False


def test_a_finished_episode_the_environment_did_not_win_is_a_false_finish(tmp_path):
    ep = run(tmp_path, "false_finish", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})), ("finish", None)])
    official = ep.of("episode_official")[0]
    assert official["false_finish"] is True
    assert ep.result.termination_reason == "AGENT_FINISH"
    assert ep.result.score_complete_success is False


@pytest.mark.parametrize("action,reason", [("blocked", "AGENT_BLOCKED"),
                                            ("clarify", "NEEDS_CLARIFICATION")])
def test_control_outcomes_seal_the_episode_at_once(tmp_path, action, reason):
    ep = run(tmp_path, f"control_{action}",
             [(action, None if action == "blocked" else "which of the two mugs?")])
    assert ep.result.termination_reason == reason and ep.src.calls == 1
    assert ep.backend.env_steps == 0


# ------------------------------------------------------------- 13.7: the ledger --------


class SchemaFailingSource:
    """A payload that is not a Decision: real subject behaviour, charged to the
    decision and repair budgets with zero environment steps."""

    provider = "fixture"
    prompt_tokens = 0
    completion_tokens = 0
    api_errors = 0
    transport_retries = 0
    format_repairs = 0

    def __init__(self):
        self.http_requests = 0
        self.calls = 0
        self.seen: list = []

    def decide(self, ctx):
        self.calls += 1
        self.http_requests += 1
        self.seen.append(ctx)
        raise DecisionSchemaError(["action must be one of execute|finish|blocked|clarify"],
                                  {"raw_response": '{"action":"jump"}'})


def test_a_schema_error_costs_a_decision_and_no_environment_step(tmp_path):
    ep = run(tmp_path, "schema", [], source=SchemaFailingSource())
    assert ep.backend.env_steps == 0
    assert ep.result.rejected_decisions == 3
    assert ep.result.http_requests == 3
    assert ep.result.identical_invalid_attempts == 0     # a shape error has no signature
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert ep.result.artifacts["note"] == "semantic repair budget"


class FailingSource:
    provider = "fixture"
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, error):
        self.error = error
        self.http_requests = 0
        self.calls = 0
        self.seen: list = []

    def decide(self, ctx):
        self.calls += 1
        self.http_requests += 1
        self.seen.append(ctx)
        raise self.error


def test_a_transport_error_is_a_model_error_and_is_billed(tmp_path):
    ep = run(tmp_path, "transport", [], source=FailingSource(RuntimeError("upstream 503")))
    assert (ep.result.model_errors, ep.result.http_requests) == (3, 3)
    assert ep.backend.env_steps == 0
    assert ep.result.termination_reason == "MODEL_ERROR"
    assert ep.result.failure_type == FailureCode.MODEL_ERROR


def test_an_environment_fault_ends_the_episode_without_replaying_the_command(tmp_path):
    backend = TranscriptBackend(fault_on={"go to shelf 2"})
    ep = run(tmp_path, "env_fault", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})), ("finish", None)],
        backend=backend)
    assert ep.backend.commands.count("go to shelf 2") == 1       # sent once, never retried
    assert ep.result.failure_type == FailureCode.ENVIRONMENT_ERROR
    assert ep.result.termination_reason == "ENVIRONMENT_ERROR"
    assert ep.src.calls == 1                                    # stopped, did not continue
    row = ep.rt.feedback_history[0]
    assert (row.status, str(row.failure_code)) == ("failed", "ENVIRONMENT_ERROR")
    assert row.stages_executed == ["command_issued"]             # never a confirmed effect
    fault = ep.of("backend_fault_detail")
    assert fault and any("never replayed" in n for n in fault[0]["notes"])
    assert "never replayed" in ep.result.artifacts["note"]


def test_a_rejected_call_is_bounded_by_the_repair_budget(tmp_path):
    budgets = bst_budgets()
    ep = run(tmp_path, "repair_cap",
             [("execute", ("alfred_heat", {"object_id": "mug 2"}))], budgets=budgets)
    assert ep.backend.env_steps == 0
    assert ep.result.semantic_repairs == budgets.max_semantic_repairs + 1
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert ep.result.artifacts["note"] == "semantic repair budget"


class RetryingSource(ScriptedSource):
    """A model call whose adapter retried: the extra requests are visible in the
    cumulative counter, which is the only reason the cap can bind at all."""

    def decide(self, ctx):
        self.http_requests += 5
        return super().decide(ctx)


def test_the_http_cap_binds_before_the_request_that_would_pass_it(tmp_path):
    """The cap is checked before the request is sent, so the overshoot is one
    round's own retries and no more; the number reported is the measured one, not
    the cap (SPEC-BST 5.3: 保守估计 + 实际超额)"""
    src = RetryingSource([("execute", ("alfred_go", {"target_id": "shelf 2"})),
                          ("execute", ("alfred_go", {"target_id": "cabinet 6"}))],
                         http_requests_per_call=0)
    ep = run(tmp_path, "http_cap", [], source=src, budgets=bst_budgets(http_requests=6))
    assert src.calls == 2                                # the third request was not sent
    assert ep.result.http_requests == 10                 # measured, and over the cap
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert "http request budget" in ep.result.artifacts["note"]
    assert ep.backend.env_steps == 2                     # the two rounds did act


class HeavySource(ScriptedSource):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def decide(self, ctx):
        self.prompt_tokens += 40_000
        self.completion_tokens += 1_000
        return super().decide(ctx)


def test_the_episode_token_cap_uses_a_pre_request_estimate(tmp_path):
    src = HeavySource([("execute", ("alfred_go", {"target_id": "shelf 2"}))],
                      on_exhausted="repeat_last")
    ep = run(tmp_path, "token_cap", [], source=src, tokens=60_000)
    assert ep.rt.tokens_used() == 41_000                 # nothing over the cap was spent
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert "token cap" in ep.result.artifacts["note"]
    assert ep.src.calls == 1                              # the second request was not sent


def test_the_decision_round_cap_is_the_frozen_one(tmp_path):
    budgets = bst_budgets(decision_rounds=3)
    ep = run(tmp_path, "round_cap",
             [("execute", ("alfred_go", {"target_id": ["shelf 2", "cabinet 6"][i % 2]}))
              for i in range(20)], budgets=budgets)
    assert ep.result.decision_rounds == 3
    assert ep.result.termination_reason == "BUDGET_EXHAUSTED"
    assert ep.result.artifacts["note"] == "decision round budget"


def test_every_cost_counter_survives_into_the_result(tmp_path):
    ep = run(tmp_path, "ledger", [
        ("execute", ("alfred_go", {"target_id": "shelf 2"})),
        ("execute", ("alfred_take", {"object_id": "mug 2", "target_id": "shelf 2"})),
        ("finish", None)])
    dumped = ep.result.model_dump(mode="json")
    for key in ("decision_rounds", "skill_calls", "http_requests", "rejected_decisions",
                "identical_invalid_attempts", "identical_repeats_total", "prompt_tokens",
                "completion_tokens", "wall_time_s", "termination_reason", "artifacts"):
        assert key in dumped
    assert dumped["artifacts"]["terminal_observation_ref"].startswith("obs_")
    assert dumped["artifacts"]["unknown_goals"] == "1"
    assert dumped["provider_counters"].keys() <= {"api_errors", "transport_retries",
                                                  "format_repairs"}
