"""A rule policy that *orders* by the recalled experiences and nothing else (SPEC-v0.2 §5.4, §3.4).

Why this exists and how narrow it is.

§9's memory arm has to be measurable without a model, for the same reason P2 wrote
`planning/policy.py`: an arm nobody can run at zero spend is an arm nobody can check. The check
that matters here is not "can a policy finish a task while a store is connected" — it is **does the
recall change what happens**. So this policy reads exactly one extra thing out of the payload,
`working_memory.recalled[].provenance.steps`, and uses it for exactly one decision: *which of the
ready rows to work first*. Everything else is `PlanPolicy`'s, unchanged and inherited.

That is deliberately the smallest capability that can show an effect, and it is worth stating what
it therefore cannot claim. It never adds a step the plan does not already ask for, never picks an
action a row does not imply, and never rejects a row: a memory that says "on the last task I
started with the red cylinder" can only promote the red cylinder's row among rows that were already
ready. A policy that could act on a memory with no row behind it would be measuring
memory-plus-planning, and the plan is a different module with its own arm.

Three constraints that keep it honest as an instrument:

* **It reads the payload, not the store.** No import of `ExperienceStore`, no `retrieve()` call:
  the same rule that makes `PlanPolicy` a test of the *view* applies here, so a memory that never
  reached the page cannot influence the run, and one that reached it under a `CONTRADICTED` label
  is treated as what §5.4 calls it — a claim the current world refutes.
* **A refuted claim is declined per object, not per row.** `contradicted_current_state` is the
  negative-transfer guard, and a policy that acted as if a refuted *layout* still held would be a
  worse agent for having a memory. But the flag is row-level, and the first connected run of this
  arm measured why that cannot be the whole rule: on a reset world every `placed:X:R` claim a past
  episode ended with measures false, so a row-level skip contradicted 100% of recalls and this
  policy became `PlanPolicy` on every round — a memory that was retrieved, displayed and then, by
  construction, unable to change anything. So the guard is applied to the claim rather than to the
  row: an object is declined when the snapshot refutes a predicate about it that **this plan does
  not also owe**, because then the memory is describing a layout the world has changed for a reason
  this episode does not want. A refuted predicate the plan still owes is the work this episode is
  doing, and the remembered order for it stands. Both outcomes are recorded — `memory_order` and
  `memory_declined` — so §11 reads "retrieved three, followed none of them" as a choice rather
  than as an absence. This is the policy's own strategy, permitted because §3.4 allows a decision
  source to have one and nowhere else in the package.
* **The unremembered order is preserved underneath.** Ties break on `row_key`, which is the order
  `plan_view` publishes `ready` in and the order `PlanPolicy` would have visited. So on an empty
  store, on `wo_episodic_memory`, or when no recalled object is among the ready rows, this policy's
  trajectory is `PlanPolicy`'s line for line — which is what makes the contrast a contrast, and is
  asserted as such (`test_the_memory_policy_reduces_to_the_plan_policy_when_the_page_says_nothing`).

`provider = "rule_memory"` is the ledger's word for who answered the round, and the trace entry
says which experiences were on the page, which order was used and whether the chosen row was in it.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.contracts import Decision, DecisionContext
from ..planning.policy import PlanPolicy

#: a ready row whose entity is not named by any recall sorts after every row that is, at this
# finite rank rather than at `math.inf` so the key stays an int tuple a reader can print.
_UNREMEMBERED = 1_000_000


class MemoryPolicy(PlanPolicy):
    """`PlanPolicy` with one extra input to row order: what a previous episode did first."""

    provider = "rule_memory"

    def __init__(self, *, feedback_depth: int = 3):
        super().__init__(feedback_depth=feedback_depth)
        #: object id -> position in the remembered sequence, rebuilt every round from the page
        self.memory_order: dict[str, int] = {}
        #: object id -> the refuted claims that kept it out of `memory_order` this round
        self.memory_declined: dict[str, list[str]] = {}
        self.recalled_ids: list[str] = []

    # ------------------------------------------------------------------ one round ----
    def decide(self, ctx: DecisionContext) -> Decision:
        self.memory_order = {}
        self.memory_declined = {}
        self.recalled_ids = []
        decision = super().decide(ctx)
        entry = self.trace[-1]
        entry["recalled"] = list(self.recalled_ids)
        entry["memory_order"] = sorted(self.memory_order, key=lambda k: self.memory_order[k])
        entry["memory_declined"] = {k: list(self.memory_declined[k])
                                    for k in sorted(self.memory_declined)}
        entry["memory_followed"] = "followed a recalled" in str(entry.get("rationale") or "")
        return decision

    # ------------------------------------------------- the one thing it changes ----
    def _from_plan(self, payload: dict, plan: dict, held: Any,
                   rejected: set[tuple[str, str]]) -> Optional[Decision]:
        order = self._remembered_order(payload, plan)
        by_id = {row.get("subgoal_id"): row for row in plan.get("rows") or []}
        entities = {sid: _entity_of(by_id.get(sid)) for sid in plan.get("ready") or []}
        if order:
            self.memory_order = dict(order)
        if not order or not all(entities.values()):
            # Nothing to order by, or a ready row this policy cannot attribute to an object: in
            # both cases the plan's own order stands, and the memory is reported as present but
            # unused rather than silently deciding.
            return super()._from_plan(payload, plan, held, rejected)
        ranked = sorted(plan.get("ready") or [],
                        key=lambda sid: (order.get(entities.get(sid) or "", _UNREMEMBERED),
                                         str((by_id.get(sid) or {}).get("row_key") or ""),
                                         str(sid)))
        decision = super()._from_plan(payload, {**plan, "ready": ranked}, held, rejected)
        if decision is None:
            return None
        chosen = _entity_of(by_id.get(str(decision.execute.subgoal or ""))) if (
            decision.execute is not None) else ""
        if chosen not in order:
            # The row that won was not promoted by anything on the page, so the rationale must not
            # say it was. A model's explanation of its own motives is not evidence here either.
            return decision
        return decision.model_copy(update={
            "rationale": f"{decision.rationale}; followed a recalled sequence: {chosen} is the "
                         f"first object a recalled step acted on that this snapshot does not "
                         f"refute for a reason this plan does not owe "
                         f"({'|'.join(self.recalled_ids)})"})

    # --------------------------------------------------------------- reading it ----
    def _remembered_order(self, payload: dict, plan: dict) -> dict[str, int]:
        """The object ids the recalls acted on, in the order the experiences recorded acting.

        First mention wins: an experience that moved the same blocker twice says once.

        A refuted claim is declined **per object, not per row**, and the reason is a measurement
        from the first connected run of this arm. On a reset world every `placed:X:R` claim of a
        completed past episode measures false, so a row-level skip put `contradicted = true` on
        100% of recalls and this policy reduced to `PlanPolicy` on every round of every episode —
        the memory was connected, retrieved, displayed, judged refuted and then, by construction,
        could not influence anything. §13's RQ3 was unaskable.

        The row-level flag is also not what the guard is for. `contradicted()` compares
        `state_pattern` — *the layout the past episode ended in* — with this snapshot, and a layout
        that no longer holds says nothing about whether the *order of work* was wrong. So the two
        readings separate here:

        * the refuted claim is a predicate this plan still owes (`placed:obj_blue_2:tray_right`
        while that row is unsatisfied): the memory is describing the very work this episode has to
        do, which is positive transfer, and the object is promoted;
        * the refuted claim is one this plan does **not** owe (the task now wants the object
        somewhere else, or nothing about it): following the remembered step would act on a layout
        the world has refuted for no reason this episode gives, and the object is declined, with
        the claim recorded in `memory_declined` and the trace.

        That second case is §11's negative transfer, and the first case is why the guard cannot be
        a whole-row veto without making the arm measure nothing.
        """
        rows = ((payload.get("working_memory") or {}).get("recalled")) or []
        owed = {str(r.get("predicate_id") or "") for r in (plan.get("rows") or [])
                if str(r.get("satisfied")) != "true"}
        order: dict[str, int] = {}
        for row in rows:
            experience_id = str(row.get("experience_id") or "")
            if experience_id:
                self.recalled_ids.append(experience_id)
            refuted = {_clash_predicate(str(m))
                       for m in (row.get("provenance") or {}).get("contradictions") or []}
            for step in (row.get("provenance") or {}).get("steps") or []:
                args = dict(step.get("args") or {})
                entity = str(args.get("object_id") or "")
                if not entity or str(step.get("skill") or "") not in ("pick", "place"):
                    continue
                claims = sorted(p for p in refuted if _about(p, entity))
                if claims and not (set(claims) & owed):
                    self.memory_declined[entity] = sorted(
                        set(self.memory_declined.get(entity, [])) | set(claims))
                    continue
                order.setdefault(entity, len(order))
        return order


def _clash_predicate(message: str) -> str:
    """`placed:obj_blue_2:tray_right` out of the sentence §5.4's guard writes.

    The ids inside `provenance.contradictions` are this plan's own predicate vocabulary
    (`planning/subgoals.py:placement_predicate`), and the sentence is shaped `f"{predicate}:
    recalled ..."`, so the first `": "` is the separator — an entity id containing a colon cannot
    confuse it, because the id comes before the separator. A message with no separator names
    nothing this policy can match and is returned as it came.
    """
    return message.partition(": ")[0].strip() or message


def _about(predicate: str, entity: str) -> bool:
    """Whether `placed:X:R` / `clear:X:R` is a claim about X and about no other object."""
    if not predicate.startswith(("placed:", "clear:")):
        return False
    parts = predicate.split(":")
    return len(parts) == 3 and parts[1] == entity


def _entity_of(row: Optional[dict]) -> str:
    """The object a `placed:`/`clear:` row is about, or "" for a row that is about a sentence.

    The predicate's own field order, not a name match: `placed:obj:region` and
    `clear:obj:region` are the two shapes `plan_view` publishes, and a row of any other shape has
    no entity for a memory to promote.
    """
    predicate = str((row or {}).get("predicate_id") or "")
    if not predicate.startswith(("placed:", "clear:")):
        return ""
    parts = predicate.split(":")
    return parts[1] if len(parts) == 3 else ""


__all__ = ["MemoryPolicy"]
