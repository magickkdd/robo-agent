"""A zero-spend decision source for the MuJoCo channel (SPEC-v0.2 §3.4, §10).

This is the same shape `benchmark/policy.py:TextPlanPolicy` is: a *decision source*, not
a runtime. It reads one page per round and publishes one `Decision`; it cannot step the
world, cannot see the score, and its `http_requests / prompt_tokens / completion_tokens`
are class-level zeros because no request is made. §3.4 is what licenses it to have a
strategy at all — a decision source is exactly the thing that may — and the reason the
strategy lives here rather than in `runtime.py` is that the loop must stay the loop.

The rule is deliberately short and deliberately able to fail:

    measure -> (already at the target? finish) -> (holding it? place) -> (else pick)

with one recovery: two grasps that the next measurement says did not hold, and it gives
up the object and retreats before trying again. Nothing here is a demonstration of what
a good manipulation policy looks like — an expert controller would be `metaworld.
policies`, which this package never imports — and the point of running it is the
*record*: which round asked for what, on which evidence, and what the loop did when the
world's next measurement disagreed.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.contracts import Decision, DecisionContext, DecisionExecute


class MujocoPlanPolicy:
    """One skill per round, made from measured poses and the plan this page carries."""

    provider = "rule_mujoco_plan"
    http_requests = 0
    prompt_tokens = 0
    completion_tokens = 0

    #: how many failed grasps before this policy gives the object up and retreats
    grasp_patience = 2

    def __init__(self, *, feedback_depth: int = 3):
        self.feedback_depth = int(feedback_depth)
        self.decisions = 0
        self.trace: list[dict[str, Any]] = []
        self._failed_grasps = 0
        self._last_skill: Optional[str] = None

    # ------------------------------------------------------------------ one round ----
    @staticmethod
    def _row_aimed(payload: dict[str, Any], object_id: Optional[str]) -> Optional[str]:
        """The row this round's action is about, named the way the ledger names it.

        A commitment is discharged by the measurement its row names
        (`planning/working_memory.py:_discharge`), and a commitment is linked to a row only
        through `Decision.execute.subgoal` — so a decision that names nothing writes a promise
        no instrument can settle, and the episode ends with `unresolved_obligations` that are
        an artefact of this policy's silence rather than of the world. Naming the row is the
        policy's own act, exactly as on the desktop channel (`planning/policy.py`); with
        `wo_planning` there is no plan on the page, and this returns None, which is that arm's
        reading rather than a hole in it.
        """
        plan = payload.get("plan") or {}
        rows = {str(r.get("subgoal_id")): r for r in (plan.get("rows") or [])}
        for ref in plan.get("ready") or []:
            row = rows.get(str(ref)) or {}
            predicate = str(row.get("predicate_id") or "")
            if row.get("kind") == "achieve" and object_id and object_id in predicate:
                return str(ref)
        return None

    def decide(self, ctx: DecisionContext) -> Decision:
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        self.decisions += 1
        world = payload.get("world") or {}
        entities = {e["entity_id"]: e for e in (world.get("entities") or [])}
        targets = {t["target_id"]: t for t in (world.get("targets") or [])}
        goal = (payload.get("goal") or {}).get("assignments") or []
        object_id = goal[0]["entity_id"] if goal else None
        target_id = goal[0]["target_id"] if goal else None
        progress = {p["entity_id"]: p for p in (payload.get("progress") or [])}
        budget = payload.get("budget") or {}
        steps_left = int(budget.get("env_steps_total", 0)) - int(budget.get("env_steps_used", 0))

        def publish(skill: str, args: dict[str, str], why: str, expect: str) -> Decision:
            self._last_skill = skill
            row = self._row_aimed(payload, object_id)
            self.trace.append({"round_index": payload["round_index"],
                               "state_version": payload["state_version"],
                               "views": [k for k in ("plan", "working_memory") if k in payload],
                               "action": "execute", "skill": skill, "args": args,
                               "row": row, "steps_left": steps_left, "rationale": why})
            return Decision(context_id=payload["context_id"],
                            based_on_state_version=payload["state_version"],
                            goal_ref=payload["goal"]["goal_id"], action="execute",
                            execute=DecisionExecute(skill=skill, args=args, candidate_id=None,
                                                    subgoal=row),
                            evidence_refs=[payload["observation_ref"]], rationale=why,
                            expected_effect=expect)

        def done(why: str) -> Decision:
            self.trace.append({"round_index": payload["round_index"], "action": "finish",
                               "rationale": why})
            return Decision(context_id=payload["context_id"],
                            based_on_state_version=payload["state_version"],
                            goal_ref=payload["goal"]["goal_id"], action="finish",
                            evidence_refs=[payload["observation_ref"]], rationale=why)

        def stop(why: str) -> Decision:
            self.trace.append({"round_index": payload["round_index"], "action": "blocked",
                               "rationale": why})
            return Decision(context_id=payload["context_id"],
                            based_on_state_version=payload["state_version"],
                            goal_ref=payload["goal"]["goal_id"], action="blocked",
                            missing_information=why,
                            evidence_refs=[payload["observation_ref"]], rationale=why)

        if not object_id or not target_id:
            return stop("the goal names no object/target pair, so no action of this world "
                        "could be aimed at anything")
        if steps_left <= 0:
            return stop("the benchmark horizon is spent; there are no simulator steps left "
                        "to move anything in")

        row = progress.get(object_id)
        if row and row.get("value") == "true":
            return done(f"{object_id} is measured inside {target_id}'s tolerance on this "
                        f"snapshot's own geometry")

        held = str(world.get("held_object")) == object_id
        if not held and self._last_skill == "pick":
            self._failed_grasps += 1
        elif held:
            self._failed_grasps = 0

        if object_id not in entities or target_id not in targets:
            return publish("observe", {}, "the entity or target the goal names is not in this "
                                          "snapshot, so a measurement is owed before anything "
                                          "is aimed", "a fresh set of measured poses")
        if self._failed_grasps >= self.grasp_patience:
            self._failed_grasps = 0
            return publish("safe_retreat", {"object_id": object_id},
                           f"{self.grasp_patience} grasps in a row were measured as not "
                           f"holding {object_id}; giving it up and clearing the hand before "
                           f"aiming again", "an open gripper raised clear of the workspace")
        if held:
            return publish("place", {"object_id": object_id, "target_id": target_id},
                           f"{object_id} is measured carried, so the outstanding obligation "
                           f"is to get it to {target_id}", f"{object_id} measured at "
                                                           f"{target_id}")
        return publish("pick", {"object_id": object_id},
                       f"{object_id} is measured at "
                       f"{entities[object_id].get('pose')} and is not held",
                       f"{object_id} measured as carried by the hand")


__all__ = ["MujocoPlanPolicy"]
