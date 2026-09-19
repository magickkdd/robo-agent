"""State forks: two DecisionContexts that differ in exactly one declared fact
(SPEC 10.3, 11.3).

SPEC 11.3 asks for frozen *pairs* of contexts whose only difference is a state axis,
so a behavioural difference can be attributed to the state rather than to the
instruction. The cheapest honest way to get such a pair is the one the benchmark
already has: run the same case twice with the same policy, once with the declared
disturbance armed and once without. Then everything the two arms show the policy
until the event must be *identical*, and the first difference must be the object the
case declared — measured by the simulator, not written by a test.

A fabricated context is legal only as a marked offline diagnostic, so nothing here
hands a policy a hand-typed world.
"""
from __future__ import annotations

import pytest

from embodied_agent.evaluation.sources import _strip_feedback

CASE = "dev_c2"

# Fields that differ between two episodes of the same case by construction: ids the
# store/feedback layer issues per episode, and the wall-clock allowance, which counts
# down in real time. Nothing about the world or the instruction is in this set.
VOLATILE = {
    "episode_id", "context_id", "state_version", "observation_ref", "sim_time", "goal_id",
    "decision_id", "call_id", "feedback_id", "pre_observation_ref", "post_observation_ref",
    "wall_time", "t_start", "t_end", "duration_s", "based_on_state_version", "latency_s",
    "wall_clock_remaining_s",
}


def _diff(x, y, path: str = "") -> list[str]:
    """Leaf paths where two payload trees disagree, ignoring `VOLATILE`."""
    out: list[str] = []
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            if k in VOLATILE:
                continue
            if k not in x or k not in y:
                out.append(f"{path}.{k}=<present in only one arm>")
            else:
                out += _diff(x[k], y[k], f"{path}.{k}")
    elif isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        if len(x) != len(y):
            out.append(f"{path}=<length {len(x)} vs {len(y)}>")
        for i, (xi, yi) in enumerate(zip(x, y)):
            out += _diff(xi, yi, f"{path}[{i}]")
    elif x != y:
        out.append(f"{path}: {x!r} vs {y!r}")
    return out


def _positions(payload: dict) -> dict[str, list[float]]:
    return {e["entity_id"]: e["position"] for e in payload["world"]["entities"]
            if "position" in e}


def _verdicts(payload: dict) -> dict[str, str]:
    return {p["entity_id"]: p["value"] for p in payload["progress"]}


_PAIR: dict[str, tuple] = {}


def _pair(run_case, recorder) -> tuple:
    """The same case, same policy, disturbance armed and disarmed. Built once per
    module: each arm is a full physical episode, and the two are read-only after the
    run (only their recorded contexts and logs are used)."""
    if not _PAIR:
        control = run_case(CASE, mode="A", with_environment=False,
                           source_factory=recorder().wrap)
        forked = run_case(CASE, mode="A", with_environment=True,
                          source_factory=recorder().wrap)
        assert control.contexts and forked.contexts, "the arms recorded no contexts to compare"
        _PAIR["arms"] = (control, forked)
    return _PAIR["arms"]


@pytest.fixture
def pair(run_case, recorder):
    return _pair(run_case, recorder)


def test_until_the_fork_the_two_arms_hand_the_policy_the_same_round(pair):
    control, forked = pair
    cp = [c.model_payload() for c in control.contexts]
    fp = [c.model_payload() for c in forked.contexts]

    # the instruction surface never differs, in either arm, at any round
    limits = {k: v for k, v in cp[0]["budget"].items() if k.endswith("_total")}
    assert limits, "the budget block names no totals to compare"
    for p in cp + fp:
        assert p["task"] == cp[0]["task"]
        assert p["goal"]["assignments"] == cp[0]["goal"]["assignments"]
        assert {k: v for k, v in p["budget"].items() if k.endswith("_total")} == limits
    # what does differ per round is what has been spent, and it only ever grows
    for arm in (cp, fp):
        skills = [p["budget"]["skill_calls_used"] for p in arm]
        assert skills == sorted(skills) and skills[0] == 0, skills
        rounds = [p["budget"]["decision_rounds_used"] for p in arm]
        assert rounds == list(range(1, len(rounds) + 1)), \
            f"one context per decision round is what makes a pair addressable: {rounds}"

    disagree = [i for i in range(min(len(cp), len(fp))) if _diff(cp[i], fp[i])]
    assert disagree, "the disturbance changed nothing: this is not a pair"
    first = disagree[0]
    assert first >= 1, "the arms differed before anything happened"
    for i in range(first):
        assert _diff(cp[i], fp[i]) == [], f"round {i} differed ahead of the fork"
    # and they stay different afterwards: a fork is not a transient
    assert all(_diff(cp[i], fp[i]) for i in disagree)


def test_the_first_difference_is_the_declared_object_and_nothing_else(pair):
    control, forked = pair
    records = forked.payloads("environment_event")
    assert len(records) == 1, [r.get("injection", {}).get("event_id") for r in records]
    assert control.payloads("environment_event") == [], "the control arm injected something"
    injection = records[0]["injection"]
    victim = injection["entity_id"]

    cp = [c.model_payload() for c in control.contexts]
    fp = [c.model_payload() for c in forked.contexts]
    first = next(i for i in range(min(len(cp), len(fp))) if _diff(cp[i], fp[i]))

    moved = {eid for eid, pos in _positions(fp[first]).items()
             if _positions(cp[first]).get(eid) != pos}
    assert moved == {victim}, (first, sorted(moved), victim)

    # the two readings are the same physical facts the environment measured: the fork
    # point is where the impulse was applied, not a number the test chose
    assert _positions(cp[first])[victim][:2] == pytest.approx(injection["before_xy"], abs=2e-3)
    assert _positions(fp[first])[victim][:2] == pytest.approx(injection["after_xy"], abs=2e-3)
    assert injection["displacement_m"] > 0.05

    # and the assignment the pair is about flips verdict between the arms
    assert _verdicts(cp[first]).get(victim) != _verdicts(fp[first]).get(victim), \
        "the state changed but the progress the policy reads did not: not a usable pair"


_ARMS: dict[str, list] = {}


def test_the_fork_survives_a_second_run_of_the_same_arm(run_case, recorder):
    """SPEC 11.3 freezes contexts so they can be re-requested; a pair whose second
    member lands elsewhere each time cannot be frozen."""
    if not _ARMS:
        _ARMS["runs"] = [run_case(CASE, mode="A", with_environment=True,
                                  source_factory=recorder().wrap) for _ in range(2)]
    arms = _ARMS["runs"]
    views = [[_positions(c.model_payload()) for c in a.contexts] for a in arms]
    shared = min(len(v) for v in views)
    assert shared >= 4, views
    for i in range(shared):
        assert views[0][i] == views[1][i], f"round {i} differed between two runs of one arm"
    finals = [{eid: d["pos"] for eid, d in a.runtime.terminal_snapshot.entities.items()}
              for a in arms]
    assert finals[0].keys() == finals[1].keys()
    for eid, pos in finals[0].items():
        assert pos == pytest.approx(finals[1][eid], abs=1e-3), eid


def test_the_ablated_view_of_one_round_changes_only_the_feedback_channel(request):
    """Mode C is the other fork the harness makes: same round, own feedback removed.
    It must subtract, and it must not edit the live context (SPEC 11.2)."""
    run_case = request.getfixturevalue("run_case")
    recorder = request.getfixturevalue("recorder")
    ep = run_case(CASE, mode="B", source_factory=recorder().wrap)
    ctx = next(c for c in reversed(ep.contexts) if c.recent_feedbacks)
    live = ctx.model_payload()

    ablated = _strip_feedback(ctx).model_payload()
    assert set(ablated) <= set(live), set(ablated) - set(live)
    assert ctx.model_payload() == live, "the ablation edited the live context in place"

    for key in set(live) - {"last_feedback", "recent_feedbacks", "attempts"}:
        assert ablated[key] == live[key], f"mode C also removed {key}, which is not its brief"
    assert ablated["attempts"] == []
    # `short()` omits an empty section, so an ablated entry shows none of them at all
    detail = {"measurements", "state_diff", "stages_executed", "progress_changes",
              "affected_entities", "verification"}
    shown = [{k for k in fb if k in detail} for fb in live["recent_feedbacks"]]
    assert any(shown), f"there was nothing to ablate: {shown}"
    for fb in ablated["recent_feedbacks"]:
        assert not detail & set(fb), sorted(detail & set(fb))
    assert len(ablated["recent_feedbacks"]) == len(live["recent_feedbacks"]), \
        "the ablation dropped the history instead of thinning it"
    assert ablated["last_feedback"] is None or \
        not detail & set(ablated["last_feedback"])
    # the state still shows what happened: that is the ablation's known limit
    assert ablated["world"] == live["world"]


def test_a_shallow_copy_shares_the_world_so_a_fork_must_be_deep(pair):
    """The hazard the pair builder has to avoid: `model_copy(update=...)` is shallow,
    so a second member of a pair would keep reading the first one's world objects."""
    ctx = pair[0].contexts[-1]
    shallow = ctx.model_copy(update={"round_index": ctx.round_index + 1})
    assert shallow.world.entities[0] is ctx.world.entities[0], \
        "pydantic stopped aliasing: this guard needs revisiting"

    deep = ctx.model_copy(deep=True)
    assert deep.world.entities[0] is not ctx.world.entities[0]
    assert deep.model_payload() == ctx.model_payload()
    deep.world.entities[0].pose.position.x = 0.123
    assert _positions(ctx.model_payload())[deep.world.entities[0].entity_id][0] != 0.123, \
        "the fork still shared its world with the original"
