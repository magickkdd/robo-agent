"""Protocol probes (SPEC 11.1 boundary cases, 12.1.2).

A boundary case is not scored by success rate; it is scored by whether the
episode did what the protocol requires *and nothing worse*. Each probe therefore
reads the episode's own evidence — decisions, skill statuses, measured feedback,
budget usage, terminal record — and states a verdict with a reason.

`bounded_no_blind_place` is the one that matters most: an agent may fail, may be
refused, may run out of budget, but it may not complete a placement that its own
measurement contradicts. That is the definition of a false success.
"""
from __future__ import annotations

from typing import Any

BOUNDED_FAILURES = {"BUDGET_EXHAUSTED", "TIMEOUT", "TARGET_NO_FREE_SLOT", "GRASP_MISS",
                    "IK_OR_PATH_UNREACHABLE", "REPEATED_INVALID", "INVALID_DECISION",
                    "MODEL_ERROR", "FINISH_REJECTED", "OBJECT_RELEASE_UNCONFIRMED",
                    "PLACE_UNSTABLE", "PROVIDER_ERROR", "STATE_UNCERTAIN", None}


def _skill_events(events: list[dict]) -> list[dict]:
    return [e["payload"]["feedback"] for e in events if e.get("type") == "execution_feedback"]


def _executed_places(events: list[dict]) -> list[dict]:
    return [fb for fb in _skill_events(events) if fb.get("skill") == "place" and fb.get("executed")]


def _calls(events: list[dict]) -> list[dict]:
    return [e["payload"]["call"] for e in events if e.get("type") == "skill_call"]


def _decisions(events: list[dict]) -> list[dict]:
    return [e["payload"]["decision"] for e in events if e.get("type") == "decision"]


def clarifies(case, result, score, events, log) -> tuple[bool, str]:
    """An unresolvable reference must end as a question, not as an action."""
    calls = [e["payload"] for e in events if e.get("type") == "skill_call"]
    ok = result.terminal_status.value == "needs_clarification" and not calls
    return ok, (f"terminal={result.terminal_status.value}, skill_calls={len(calls)}, "
                f"clarification={log.get('needs_clarification', '')!r}")


def bounded_no_blind_place(case, result, score, events, log) -> tuple[bool, str]:
    places = _executed_places(events)
    lies = []
    for p in places:
        measurements = p.get("measurements") or {}
        inside = measurements.get("inside_target")
        if inside is not None and inside < 0.5 and not p.get("failure_code"):
            lies.append(p)
    runaway = result.decision_rounds > case.budgets.max_decision_rounds or \
        result.skill_calls > case.budgets.max_skill_calls
    ok = (not lies) and (not runaway) and result.terminal_status.value != "success"
    return ok, (f"executed_places={len(places)} unreported_outside={len(lies)} "
                f"rounds={result.decision_rounds} calls={result.skill_calls} "
                f"terminal={result.terminal_status.value}/{result.failure_type}")


def bounded_budget_exhausted(case, result, score, events, log) -> tuple[bool, str]:
    ok = (result.terminal_status.value == "failed"
          and result.failure_type is not None
          and result.failure_type.value == "BUDGET_EXHAUSTED"
          and result.skill_calls <= case.budgets.max_skill_calls
          and result.decision_rounds <= case.budgets.max_decision_rounds)
    return ok, (f"terminal={result.terminal_status.value}/{result.failure_type} "
                f"rounds={result.decision_rounds}/{case.budgets.max_decision_rounds} "
                f"calls={result.skill_calls}/{case.budgets.max_skill_calls}")


def bounded_wall_clock(case, result, score, events, log) -> tuple[bool, str]:
    ok = (result.terminal_status.value == "failed"
          and result.failure_type is not None
          and result.failure_type.value == "TIMEOUT")
    return ok, f"terminal={result.terminal_status.value}/{result.failure_type} wall={result.wall_time_s}s"


def bounded_before_action(case, result, score, events, log) -> tuple[bool, str]:
    calls = [e for e in events if e.get("type") == "skill_call"]
    executed = [e for e in events if e.get("type") == "execution_feedback" and e["payload"].get("executed")]
    ok = not calls and not executed and result.terminal_status.value == "failed"
    return ok, (f"skill_call_events={len(calls)} executed={len(executed)} "
                f"terminal={result.terminal_status.value}/{result.failure_type}")


def independent_score_zero(case, result, score, events, log) -> tuple[bool, str]:
    """The agent may believe it finished; belief buys nothing."""
    claims = result.terminal_status.value == "success"
    ok = claims and not score["complete_success"]
    return ok, (f"agent_claims_success={claims} independent_complete="
                f"{score['complete_success']} objects={score['objects_completed']}/"
                f"{score['objects_total']}")


PROBES: dict[str, Any] = {
    "clarifies": clarifies,
    "bounded_no_blind_place": bounded_no_blind_place,
    "bounded_budget_exhausted": bounded_budget_exhausted,
    "bounded_wall_clock": bounded_wall_clock,
    "bounded_before_action": bounded_before_action,
    "independent_score_zero": independent_score_zero,
}


def run_probe(case, result, score: dict, events: list[dict], summary: dict) -> dict:
    probe = PROBES.get(case.probe)
    if probe is None:
        return {"probe": case.probe or "none", "met": expected_only(case, result, score),
                "detail": "no probe: generic expectation check"}
    met, detail = probe(case, result, score, events, summary)
    return {"probe": case.probe, "met": met, "detail": detail}


def expected_only(case, result, score) -> bool:
    if case.expected == "success":
        return result.terminal_status.value == "success" and score["complete_success"]
    if case.expected == "needs_clarification":
        return result.terminal_status.value == "needs_clarification"
    if case.expected == "bounded_exit":
        return result.terminal_status.value in ("failed", "needs_clarification")
    if case.expected == "goal_truth_mismatch":
        return result.terminal_status.value == "success" and not score["complete_success"]
    return False
