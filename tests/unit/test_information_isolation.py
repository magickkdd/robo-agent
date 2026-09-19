"""SPEC 5.2 / 7 / 10.1 / 10.3 information isolation, on a real episode.

The rule this experiment depends on is asymmetric, and a test that only checks
half of it proves nothing:

* what the *environment* knows — which disturbance was declared, when it fired,
  with what magnitude — must never reach the decision chain;
* what the *physics did* must reach it in full, because a model that cannot
  measure a consequence cannot reason about one (SPEC 10.2: the request stays,
  the outcome differs, and the difference is visible).

So every test here runs the production loop and asserts both halves at once: the
evidence is present under the labels a policy may read, and the cause is absent
under every label it owns.
"""
from __future__ import annotations

import json

import pytest

from embodied_agent.core.contracts import SkillCall
from embodied_agent.core.fault_injection import EnvironmentController

# The environment's own vocabulary. None of these key names may appear anywhere
# in what a decision source is handed; a measured value that happens to be
# numerically close to a declared one is not a leak, a borrowed key name is.
PRIVATE_KEYS = {"event_id", "injection", "impulse_xy", "duration_s", "before_xy",
                "after_xy", "displacement_m", "offset", "trigger", "at_match",
                "matches_seen", "fired", "fired_at", "when_entity_id",
                "when_target_id", "offset_source", "eval_spec", "hidden"}


def _keys(node, out: set[str] | None = None) -> set[str]:
    """Every mapping key in a decoded JSON tree, nested ones included."""
    out = set() if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _keys(v, out)
    return out


def _agent_tree(ep) -> list:
    """The agent-visible records, decoded, with the real contexts appended."""
    keep = ("decision_context", "decision", "skill_call", "observation",
            "episode_start", "execution_feedback", "finish_check", "state_version_stale")
    tree = [{"type": e["type"], **e["payload"]} for e in ep.events if e.get("type") in keep]
    tree += [c.model_payload() for c in ep.contexts]
    return tree


def test_the_recorded_evidence_is_not_the_agent_surface(run_case, recorder):
    """The scan itself has to be shown to cover something: an empty agent-visible
    set would pass every 'no leak' assertion below by construction."""
    ep = run_case("dev_c2", mode="B", source_factory=recorder().wrap)
    keys = _keys(_agent_tree(ep))
    assert {"world", "measurements", "state_diff", "progress_changes"} <= keys
    assert ep.contexts, "the decision source was never handed a context to look at"


def test_a_declared_disturbance_leaves_its_label_out_but_its_effect_in(run_case, recorder):
    ep = run_case("dev_c2", mode="B", source_factory=recorder().wrap)
    fired = [p["injection"] for p in ep.payloads("environment_event")]
    assert len(fired) == 1, "the scenario declares one impulse; a second would mean " \
                            "the per-episode event copy is shared again"
    evidence = fired[0]
    assert evidence["action"] == "impulse"
    victim = evidence["entity_id"]
    declared = {k for e in ep.case.event_specs() for k in (e["event_id"],)}

    blob = ep.agent_visible()
    assert not declared & set(blob.split('"')), "the event id reached the decision chain"
    for word in ("impulse", "calibration", "injection"):
        assert word not in blob, f"the environment's own word {word!r} is in the agent surface"
    assert not PRIVATE_KEYS & _keys(_agent_tree(ep)), \
        f"borrowed keys: {sorted(PRIVATE_KEYS & _keys(_agent_tree(ep)))}"

    # ... and the consequence, in full, where a policy can read it
    undone = [fb for fb in ep.feedbacks() if victim in (fb.get("affected_entities") or [])]
    assert undone, "nothing measurable recorded that the object moved"
    fb = undone[0]
    changes = {c["assignment"]: (c["from"], c["to"]) for c in fb["progress_changes"]}
    assert changes.get(f"{victim}->{fb['target_id']}") == ("true", "false"), \
        "the agent may not be told a shove happened, but it must be told a " \
        "satisfied goal stopped being satisfied"
    moved = {m["entity_id"]: m["dist_m"] for m in fb["state_diff"]["moved"]}
    assert moved[victim] > 0.05, f"the displacement is not measurable: {moved}"
    # what the environment measured and what the agent could measure agree
    assert abs(moved[victim] - evidence["displacement_m"]) < 5e-3


def test_an_actuator_error_is_reported_as_a_missed_grasp_not_as_an_injection(run_case,
                                                                            recorder):
    ep = run_case("dev_c3", mode="B", source_factory=recorder().wrap)
    fired = [p["injection"] for p in ep.payloads("environment_event")]
    assert [e["action"] for e in fired] == ["grasp_calibration"]
    assert fired[0]["offset"] == [0.05, 0.05, 0.0]

    blob = ep.agent_visible()
    assert "calibration" not in blob and fired[0]["event_id"] not in blob
    assert "offset" not in _keys(_agent_tree(ep)), \
        "the offset is the environment's number; only a measurement of the hand's " \
        "own carrying position may appear, under its own name"
    assert {k for k in _keys(_agent_tree(ep)) if "offset" in k} <= \
        {"carry_offset_x_m", "carry_offset_y_m"}

    first = ep.feedbacks()[0]
    assert first["status"] == "failed" and first["failure_code"] == "GRASP_MISS"
    assert first["measurements"]["lift_gain_m"] == 0.0
    # SPEC 10.2: the request is what the model asked for, unchanged
    calls = [p["call"] for p in ep.payloads("skill_call")]
    assert calls[0]["args"] == calls[1]["args"], \
        "the retry named a different target: the runtime chose the recovery, not the model"
    assert ep.terminal_status == "success", "a bounded disturbance must stay survivable"


def test_the_controller_arms_the_actuator_and_edits_nothing_else(scene_for):
    """SPEC 8, 10.2 at the object level: an event is a physical action, never a
    write to a request or a returned result.

    The release side is the sharpest case, because there the environment's error
    and the model's own choice look alike from the outside — so the *report* has
    to keep saying what was asked, and only the measured height may differ."""
    from embodied_agent.core.skills import SkillExecutor
    from embodied_agent.core.verify import build_world_state
    from embodied_agent.evaluation.tasks import find_case

    case = find_case("dev_c4")
    scene = scene_for("dev_c4")
    version = 0

    def observe():
        nonlocal version
        version += 1
        return build_world_state(scene, version, f"obs_{version:04d}", case.verify)

    executor = SkillExecutor(scene, world_provider=observe, config=case.verify)
    truth = {a.entity_id: a.target_id for a in case.eval_spec.assignments}
    tray = next(t for t in set(truth.values())
                if sum(1 for v in truth.values() if v == t) > 1)
    eid = next(e for e, t in truth.items() if t == tray)
    cand = executor.placement_planner.best_feasible(tray, eid, observe())[0]
    assert cand is not None
    assert executor.execute(SkillCall(plan_id="t", step_id="s0", skill="pick",
                                      args={"object_id": eid})).status.value == "completed"

    call = SkillCall(plan_id="t", step_id="s1", skill="place",
                     args={"object_id": eid, "target_id": tray,
                           "candidate_id": cand.candidate_id})
    controller = EnvironmentController(scene, case.fresh_events())
    request = call.model_dump(mode="json")
    armed = controller.before_skill(call)
    assert [a["action"] for a in armed] == ["place_calibration"]
    assert scene.place_calibration_offset == armed[0]["offset"]
    assert call.model_dump(mode="json") == request, "the controller rewrote the request"

    result = executor.execute(call)
    snapshot = result.model_dump(mode="json")
    assert controller.after_skill(call, result) == []
    assert result.model_dump(mode="json") == snapshot, \
        "a skill result was edited after the fact: the log and the model would " \
        "then be reading two different episodes"

    m = result.measurements
    assert m["candidate_x"] == pytest.approx(cand.position_xy[0], abs=1e-4)
    assert m["candidate_y"] == pytest.approx(cand.position_xy[1], abs=1e-4)
    assert m["release_gap_above_seat_m"] > 0.03, \
        f"the armed release error did not happen: {m}"
    joined = " ".join(result.notes)
    assert "calibration" not in joined and "offset" not in joined, result.notes
    assert f"candidate={cand.candidate_id}" in joined, result.notes
    scene.place_calibration_offset = None
    controller.disarm()


def test_the_case_itself_declares_what_a_policy_may_be_given():
    """`public_payload` is the whole hand-out, so its shape is the boundary."""
    from embodied_agent.evaluation.tasks import find_case

    for cid in ("dev_c2", "dev_c3", "dev_c4", "pr_skill_budget", "sc_c1"):
        case = find_case(cid)
        payload = case.public_payload()
        assert set(payload) == {"budgets", "verify"}, payload.keys()
        blob = json.dumps(payload, default=str)
        for e in case.event_specs():
            assert e["event_id"] not in blob
        for forbidden in ("eval_spec", "assignment", "impulse", "calibration", "truth"):
            assert forbidden not in blob, f"{forbidden} leaked into the public payload"
        assert "tolerance_version" in blob and "max_skill_calls" in blob


def test_no_runtime_module_needs_the_hidden_answer():
    """Reference-level isolation. A runtime that can *name* `EvalSpec` is one
    refactor away from reading it.

    Docstrings may say the word — the point of SPEC 5.2 is that the boundary is
    documented — so this parses the modules and looks at the identifiers, not the
    prose."""
    import ast
    import inspect

    import embodied_agent.core.contracts as contracts
    import embodied_agent.core.events as events
    import embodied_agent.core.fault_injection as fault_injection
    import embodied_agent.core.interpreter as interpreter
    import embodied_agent.core.placement_planner as placement_planner
    import embodied_agent.core.planner as planner
    import embodied_agent.core.runtime as runtime
    import embodied_agent.core.skills as skills
    import embodied_agent.core.verify as verify
    import embodied_agent.evaluation.sources as sources

    def used_names(module) -> set[str]:
        tree = ast.parse(inspect.getsource(module))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                out.add(node.id)
            elif isinstance(node, ast.Attribute):
                out.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                out |= {a.asname or a.name for a in node.names}
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                out.add(node.name)
        return out

    for module in (runtime, skills, verify, planner, placement_planner, interpreter,
                   events, fault_injection, sources):
        assert "EvalSpec" not in used_names(module), f"{module.__name__} refers to the hidden truth"
    assert "EvalSpec" in used_names(contracts)


def _resolve(ref, world) -> str | None:
    """An agent-side entity reference, as its owner must read it: an id when the
    utterance carried one, otherwise the attribute description, matched against
    the snapshot the context itself was built from."""
    if getattr(ref, "entity_id", None):
        return ref.entity_id
    want = dict(ref.attributes or {})
    hits = [e.entity_id for e in world.entities
            if all((e.attributes or {}).get(k) == v for k, v in want.items())]
    return hits[0] if len(hits) == 1 else None


def test_the_hidden_answer_never_appears_as_a_pair_the_agent_can_read(run_case, recorder):
    """SPEC 12.1.1: agent success may not buy score, and the reverse must hold too
    — the graded truth may not be smuggled into the goal, the progress list or any
    candidate, because then a policy that guessed *it* would be graded on hints."""
    ep = run_case("pr_goal_truth_mismatch", mode="B", source_factory=recorder().wrap)
    truth = {a.entity_id: a.target_id for a in ep.case.eval_spec.assignments}
    eid, true_target = next(iter(truth.items()))
    assert ep.contexts, "no decision was ever asked for"

    claims: set[tuple[str | None, str]] = set()
    for ctx in ep.contexts:
        claims |= {(_resolve(a.entity, ctx.world), a.target_id) for a in ctx.goal.assignments}
        claims |= {(p.entity_id, p.target_id) for p in ctx.progress}
        claims |= {(c.entity_id, c.target_id) for c in ctx.candidates}
        claims |= {(f.entity_id, f.target_id) for f in ctx.recent_feedbacks}
    assert (eid, true_target) not in claims, "the graded pairing reached the decision chain"
    mine = {t for (e, t) in claims if e == eid}
    assert mine and mine != {true_target}, \
        f"the agent was given no reading of its own (claims {mine}, truth {true_target})"
    assert ep.score["complete_success"] is False, \
        "an agent that satisfied the utterance, not the truth, scored as if it had"


def test_the_disturbance_is_the_same_one_whatever_order_the_modes_choose(run_case, recorder):
    """SPEC 10.3: the trigger is a scene condition and the victim is named by the
    scenario, so no mode's *order* decides who is disturbed.

    Two episodes of the same case with the same policy must therefore see the same
    event fire — and `fresh_events` must be what keeps a second episode from
    inheriting the first one's `fired` flag."""
    victims = []
    for _ in range(2):
        ep = run_case("dev_c2", mode="B", source_factory=recorder().wrap)
        fired = [p["injection"] for p in ep.payloads("environment_event")]
        assert len(fired) == 1, fired
        victims.append(fired[0]["entity_id"])
    assert victims[0] == victims[1], victims
    assert ep.case.events[0].fired is False, \
        "the frozen declaration carries episode state: the first run retired it"
