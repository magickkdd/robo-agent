"""A rule policy that reads the *payload* and nothing else (SPEC-v0.2 §5.2's offer, §3.4).

Three things this module is for, and one thing it is deliberately not.

**It is the zero-spend control for the planning arm.** P2's dev episodes must run without a model
request (the standing rule that no P2 work bills anything), so the arm needs a decision source. A
scripted source would prove nothing about the view; this one proves the view is *sufficient to act
on*. If a deterministic policy written against `ctx.model_payload()` can carry a blocked-placement
task to completion, then `plan_view()` delivered the information the decision needed — and where
it cannot, the gap is in the view and surfaces as a `blocked` decision naming the row it could not
actuate, which is a finding rather than a silent fallback.

**It is the reason `wo_planning` ablates the *view* and not the decision maker.** One object
answers in every arm: given a payload with a `plan` section it works the plan's ready rows; given
one without, it falls back to the pending goals in `progress`, which is what the v0.1 rule policy
does. So the arms differ by what is on the page and not by who is reading it — the only way a
success-rate difference between them means anything.

**It never receives the ablation record.** There is no `if ablation` here because there is no
`ablation` name here: the gate belongs to the runtime arm, and a policy that could see which
experiment it was in would be a second, undocumented independent variable.
`test_the_policy_has_no_ablation_branch` pins it by reading this module's own source.

What it is *not* is a planner. It authors no subgoals, revises nothing, and never ranks two options
by anything a reader cannot see in the payload: row order is the row's own `row_key` — the identical
order `plan_view` publishes `ready` in, and never the random `subgoal_id` suffix — region order is
the order `world.targets` lists, and the only history consulted is `attempts`, which the loop
published. Choosing a destination for a blocker it must move out of the way *is* a strategy choice
— no row names one — and that is permitted here and nowhere else in this package, because a
decision source is exactly the thing §3.4 allows to have a strategy. It proposes; the executor
disposes, and an `(object, region)` pair already refused in this episode is not proposed again,
which is the whole of its learning.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.contracts import Decision, DecisionContext, DecisionExecute


class PlanPolicy:
    """One decision per round, made from the payload text a model would have read.

    `http_requests`, `prompt_tokens` and `completion_tokens` are class attributes fixed at zero so
    the ledger's delta accounting sees a source that cannot spend: an episode measured against
    this policy has an HTTP bill of exactly the perception it did (or did not) do.
    """

    provider = "rule_plan"
    http_requests = 0
    prompt_tokens = 0
    completion_tokens = 0

    def __init__(self, *, feedback_depth: int = 3):
        self.feedback_depth = int(feedback_depth)
        self.decisions = 0
        #: every context it was asked to answer, in order. The trace says what was chosen; this
        # says what the choice was made from, which is the half a contract test needs.
        self.seen: list[DecisionContext] = []
        #: ready rows this round that no action in the view could actuate, in the order seen
        self.unactuated: list[str] = []
        #: one entry per answer: what was offered, what was chosen, for which row
        self.trace: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ one round ----
    def decide(self, ctx: DecisionContext) -> Decision:
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        self.decisions += 1
        self.seen.append(ctx)
        self.unactuated = []
        decision = self._choose(payload)
        self.trace.append({
            "round_index": payload["round_index"],
            "state_version": payload["state_version"],
            "views": [key for key in ("plan", "working_memory") if key in payload],
            "action": decision.action,
            "skill": decision.execute.skill if decision.execute else None,
            "args": dict(decision.execute.args or {}) if decision.execute else None,
            "subgoal": decision.execute.subgoal if decision.execute else None,
            "unactuated": list(self.unactuated),
            "rationale": decision.rationale})
        return decision

    # ---------------------------------------------------------------- the decision ----
    def _choose(self, payload: dict[str, Any]) -> Decision:
        held = payload["world"].get("held_object")
        rejected = _refused_placements(payload)
        self.unactuated = []
        plan = payload.get("plan")
        if plan is not None:
            decision = self._from_plan(payload, plan, held, rejected)
            if decision is not None:
                return decision
        return self._from_progress(payload, held, rejected)

    def _from_plan(self, payload: dict, plan: dict, held: Any,
                   rejected: set[tuple[str, str]]) -> Optional[Decision]:
        """Work the first ready row this policy can actuate.

        Rows it cannot actuate are reported in `self.unactuated` and named in the rationale of
        whatever was chosen instead. A row silently passed over is the failure mode of every plan
        renderer ever written — the plan looks done because the agent stopped being seen.
        """
        by_id = {row["subgoal_id"]: row for row in plan.get("rows") or []}
        for subgoal_id in plan.get("ready") or []:
            row = by_id.get(subgoal_id)
            before = len(self.unactuated)
            if row is None:
                self.unactuated.append(f"{subgoal_id}:ready-but-not-a-row-of-this-plan")
                continue
            decision = self._for_row(payload, row, held, rejected)
            if decision is not None:
                return (decision.model_copy(update={"rationale": decision.rationale + "; owed "
                        "and not actuated by this view: " + "; ".join(self.unactuated)})
                        if self.unactuated else decision)
            if len(self.unactuated) == before:
                self.unactuated.append(f"{row['row_key']}:no-action-in-the-view")
        return None

    def _for_row(self, payload: dict, row: dict, held: Any,
                 rejected: set[tuple[str, str]]) -> Optional[Decision]:
        predicate = str(row.get("predicate_id") or "")
        parts = predicate.split(":")
        if predicate.startswith("placed:") and len(parts) == 3:
            return self._seating(payload, row, parts[1], parts[2], held, rejected)
        if predicate.startswith("clear:") and len(parts) == 3:
            return self._clearing(payload, row, parts[1], parts[2], held, rejected)
        if row.get("kind") == "observe":
            # a look the plan asked for because an answer is missing. `observe` is the one skill
            # with no geometric precondition to refuse, and the desktop arm has no instrument for
            # it, so the row simply stays open there — which is what §11's unknown-handling row
            # is for measuring.
            return _execute(payload, "observe", {}, row,
                            f"the ready row {row['row_key']} asks for a look that would answer "
                            f"{predicate or row.get('statement')}",
                            expected=f"{predicate or 'the asked question'} is measured")
        return None

    def _seating(self, payload: dict, row: dict, entity: str, target: str, held: Any,
                 rejected: set[tuple[str, str]]) -> Optional[Decision]:
        if held == "unknown":
            return _unknown_hold(payload, row)
        if held == entity:
            return _execute(payload, "place", {"object_id": entity, "target_id": target}, row,
                            f"{row['row_key']} is ready, {entity} is in hand, so it is seated in "
                            f"{target}", expected=f"{entity} is seated in {target}")
        if held is None:
            return _execute(payload, "pick", {"object_id": entity}, row,
                            f"{row['row_key']} is ready and needs {entity} carried into {target}; "
                            f"the hand is empty, so {entity} is taken up first",
                            expected=f"the gripper holds {entity}")
        # The hand is occupied by something else, so the hand's own debt is the work. Which row
        # accounts for `held` is looked up by *entity*, not by what the planner would prefer: an
        # object carried with no row behind it is a fact the log should not be allowed to hide.
        other = _row_for_held(payload, held)
        if other is None:
            self.unactuated.append(f"{row['row_key']}:holding {held}, which no open row accounts "
                                   f"for")
            return None
        nxt = self._for_row(payload, other, held, rejected)
        return (nxt.model_copy(update={"rationale": f"holding {held}: {nxt.rationale}"})
                if nxt is not None else None)

    def _clearing(self, payload: dict, row: dict, entity: str, region: str, held: Any,
                  rejected: set[tuple[str, str]]) -> Optional[Decision]:
        """Get `entity` out of `region`, which is all the row claims.

        The row names no destination and the plan may not invent one, so the destination is this
        policy's own choice: the first region the snapshot lists that is neither the one being
        cleared nor already refused for this object. That is a proposal to the executor, not a
        claim about feasibility — `PlanValidator` answers it, and `attempts` keeps a refusal from
        being asked twice.
        """
        if held == "unknown":
            return _unknown_hold(payload, row)
        if held is None:
            return _execute(payload, "pick", {"object_id": entity}, row,
                            f"{row['row_key']} requires {entity} to be clear of {region} and it is "
                            f"measured inside it, so it is taken up",
                            expected=f"the gripper holds {entity}")
        if held != entity:
            other = _row_for_held(payload, held)
            if other is None:
                self.unactuated.append(f"{row['row_key']}:holding {held}, which no open row "
                                       f"accounts for")
                return None
            nxt = self._for_row(payload, other, held, rejected)
            return (nxt.model_copy(update={"rationale": f"holding {held}: {nxt.rationale}"})
                    if nxt is not None else None)
        for candidate in _regions(payload):
            if candidate == region or (entity, candidate) in rejected:
                continue
            return _execute(payload, "place", {"object_id": entity, "target_id": candidate}, row,
                            f"{entity} must leave {region}; {candidate} is the first region this "
                            f"snapshot names that is not {region} and has not refused {entity}",
                            expected=f"{entity} is not seated in {region}")
        self.unactuated.append(f"{row['row_key']}:every region except {region} has refused "
                               f"{entity}")
        return None

    # ------------------------------------------------------- the v0.1 fallback ----
    def _from_progress(self, payload: dict, held: Any,
                       rejected: set[tuple[str, str]]) -> Decision:
        """No plan on the page: the pending goals decide, as they did in v0.1.

        `unknown` counts as pending. An unmeasured predicate is not a satisfied one, and a policy
        that read silence as completion would score the sensor's blindness as the agent's success.
        """
        pending = sorted((str(p["entity_id"]), str(p["target_id"]))
                         for p in payload.get("progress") or []
                         if str(p.get("value")) != "true" and p.get("entity_id") and p.get("target_id"))
        note = ("; rows the plan offered that this view cannot actuate: "
                + "; ".join(self.unactuated)) if self.unactuated else ""
        if held == "unknown":
            return _bare(payload, "observe", {}, "the hold state is unmeasured and no placement "
                         "is checkable until it is")
        if isinstance(held, str) and held:
            for entity, target in pending:
                if entity == held:
                    return _bare(payload, "place",
                                 {"object_id": held, "target_id": target},
                                 f"holding {held}, whose goal is {target}" + note,
                                 expected=f"{held} is seated in {target}")
            return _control(payload, "blocked",
                            f"holding {held} and no pending goal in this payload names it" + note)
        if not pending:
            return _control(payload, "finish",
                            "every goal in this payload measures satisfied" + note)
        entity = pending[0][0]
        return _bare(payload, "pick", {"object_id": entity},
                     f"{entity} is owed a placement and the hand is empty" + note,
                     expected=f"the gripper holds {entity}")


# ------------------------------------------------------------------ payload helpers ----
def _refused_placements(payload: dict) -> set[tuple[str, str]]:
    """The `(object, region)` pairs this episode already tried to place and did not.

    A runtime rejection and an executed failure both count: the world said no either way. A pair
    whose `place` completed is not excluded — its object is in its region and no open row will ask
    for it again.
    """
    out: set[tuple[str, str]] = set()
    for attempt in payload.get("attempts") or []:
        if str(attempt.get("skill")) != "place":
            continue
        entity, target = attempt.get("entity_id"), attempt.get("target_id")
        if entity and target:
            out.add((str(entity), str(target)))
    return out


def _regions(payload: dict) -> list[str]:
    """The regions this snapshot names, in the order the world lists them."""
    return [str(t["target_id"]) for t in payload["world"].get("targets") or []
            if t.get("target_id")]


def _rows(payload: dict) -> list[dict]:
    return list((payload.get("plan") or {}).get("rows") or [])


def _row_for_held(payload: dict, held: str) -> Optional[dict]:
    """The first unsatisfied row that is about the object in the hand.

    Unsatisfied rows only, in `row_key` order — the same order the view publishes `ready` in, and
    for the same reason: `subgoal_id` is a random suffix, so an id tie-break would visit the rows in
    one sequence in this process and another in the next. A `done` row about the held object is the
    state the agent is *leaving*, not a debt it owes, and `recover` rows sort alongside everything
    else — the plan marks them owed by leaving them unsatisfied, and this policy reads that mark
    rather than a kind.
    """
    for row in sorted(_rows(payload), key=lambda r: (str(r.get("row_key") or ""),
                                                     str(r.get("subgoal_id") or ""))):
        predicate = str(row.get("predicate_id") or "")
        if not (predicate.startswith("placed:") or predicate.startswith("clear:")):
            continue
        if str(row.get("satisfied")) == "true":
            continue
        if predicate.split(":")[1] != held:
            continue
        return row
    return None


def _unknown_hold(payload: dict, row: dict) -> Decision:
    """The gripper cannot say what it holds, and `PlanValidator` refuses every pick and place
    until it can — so the only action that is not a rejection is a measurement.

    The row is still named, so the commitment the memory books stays attached to the work the look
    was for instead of floating as an unbound action.
    """
    return _execute(payload, "observe", {}, row,
                    "the hold state is unmeasured, so no placement is checkable: this snapshot "
                    f"must answer whether the gripper holds {str(row.get('predicate_id') or '')}",
                    expected="the gripper can name what it holds")


def _execute(payload: dict, skill: str, args: dict[str, str], row: Optional[dict],
             why: str, expected: str = "") -> Decision:
    """An action that names the row it works.

    `subgoal` is the row's own id and `expected_effect` is the row's own statement, so the
    commitment the memory books is the agent's claim in the plan's words — which is what lets
    `WorkingMemory._discharge` close it against an instrument rather than against a paraphrase. No
    `candidate_id` is ever named: choosing a slot from the offered geometry is the runtime's
    documented fallback, and the v0.1 rule baseline pays the same `slot_resolution_fallbacks` cost,
    so the two arms stay comparable on that counter.
    """
    return Decision(
        context_id=payload["context_id"], based_on_state_version=payload["state_version"],
        goal_ref=payload["goal"]["goal_id"], action="execute",
        execute=DecisionExecute(skill=skill, args=dict(args),
                                subgoal=(row or {}).get("subgoal_id")),
        rationale=why, expected_effect=expected,
        evidence_refs=[payload["observation_ref"]])


def _bare(payload: dict, skill: str, args: dict[str, str], why: str,
          expected: str = "") -> Decision:
    return _execute(payload, skill, args, None, why, expected=expected)


def _control(payload: dict, action: str, why: str) -> Decision:
    return Decision(
        context_id=payload["context_id"], based_on_state_version=payload["state_version"],
        goal_ref=payload["goal"]["goal_id"], action=action, rationale=why,
        evidence_refs=[payload["observation_ref"]],
        missing_information=why if action == "clarify" else None)


__all__ = ["PlanPolicy"]
