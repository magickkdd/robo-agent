"""The plan and the working memory as a *view the model acts on* (SPEC-v0.2 §5.2, §5.3, §7 step 2).

`task_planner` and `working_memory` own objects; this module owns the only thing that makes
them a capability rather than a data structure: **what the decision sees**. Three rules, each
of which is a test:

1. **The view is a rendering of the records, never a second source of facts.** Every field here
   comes off a `TaskPlan` or a `WorkingMemoryState` that the loop already has, and the ids it
   names (`row_key`, `subgoal_id`, `predicate_id`, `observation_ref`) are the ids those records
   carry. A view that summarised, softened or added a fact would put a claim in front of the
   model that no record supports, and §12.3's "以实际动作和最终状态为准" would then have two
   truths to choose between.
2. **An absent module leaves no key.** `wo_planning` and `wo_working_memory` drop their section
   from the payload entirely rather than writing `"plan": null` or a sentence saying planning is
   off. A marker is a *hint* about the experiment, and a payload that tells the model which arm
   it is in makes the arm's own effect unattributable. What remains is exactly the v0.1 payload,
   so the payload key set is a *subset* relation between arms and a superset relation to the
   baseline — checkable as sets, which is why `VIEW_KEYS` is a constant rather than an inline
   literal.
3. **Placement inside the payload is fixed.** The two sections go immediately before
   `current_subgoal`/`todo_summary`, the fields the v0.1 loop already used for the model's *own*
   intent. Ordered before them because the plan is what the loop is offering and the intent
   fields are what the agent answered last; after `world`/`progress`, because a claim about the
   world must be read before a claim about what to do about it.

`view_sha256()` is the identity of that text — the same role `prompts_sha256()` plays for the
desktop prompts and for the same reason: the payload is part of the mechanism under test, so a
change to the renderer must be visible as a change of hash and not as a silently different
experiment.

`planning_coherence()` reads the log back. It exists because "the closed loop ran" is a claim
about *which records exist and whether they agree* (SPEC-v0.2 §15), and an arm whose plan events
and memory events disagree about a version number is not a weaker arm — it is a broken record.
"""
from __future__ import annotations

import hashlib
import inspect
from typing import Any, Iterable, Optional, Sequence

from ..core.contracts import DecisionContext
from ..core.v02 import Subgoal, TaskPlan
from .subgoals import row_key
from .task_planner import HISTORY_PREFIX
from .working_memory import WorkingMemory

#: the two keys this layer adds to the payload a v0.1 round would have produced
VIEW_KEYS = ("plan", "working_memory")
#: the existing field they are inserted before, so the order is stable across rounds
VIEW_ANCHOR = "current_subgoal"
#: which ablatable module owns which section (the registry in `core/v02.py` uses the same names)
VIEW_MODULE = {"plan": "planning", "working_memory": "working_memory"}


# ------------------------------------------------------------------ the renderers ----
def _row(sub: Subgoal) -> dict[str, Any]:
    """One plan row, in the plan's own vocabulary.

    `status` and `satisfied` are both published because they are different claims: the first is
    what the agent has done about the row, the second is what an instrument said about the
    predicate. Collapsing them is how a `done` row with a `false` measurement disappears from
    view, which is the failure `TaskPlanner.obligations()` was written against.
    """
    return {
        "subgoal_id": sub.subgoal_id,
        "row_key": row_key(sub),
        "kind": sub.kind,
        "status": str(sub.status.value),
        "statement": sub.statement,
        "predicate_id": sub.predicate_id,
        "satisfied": str(sub.satisfied.value),
        "unmeasured": list(sub.satisfied.unmeasured),
        "completion_evidence": list(sub.completion_evidence_refs),
        "depends_on": list(sub.depends_on),
        "attempts": int(sub.attempts),
        "target_entity_ids": list(sub.target_entity_ids),
        "target_region_id": sub.target_region_id,
        "open_questions": [p for p in sub.preconditions if not p.startswith(HISTORY_PREFIX)],
        "history": [p[len(HISTORY_PREFIX):] for p in sub.preconditions
                    if p.startswith(HISTORY_PREFIX)],
    }


def plan_view(plan: TaskPlan, *, ready: Sequence[Subgoal] | Sequence[str] = (),
              changed: Optional[dict[str, Any]] = None, violations: Iterable[str] = (),
              revision_note: str = "") -> dict[str, Any]:
    """What the plan offers this round: the rows, what nothing prevents, and what moved.

    `ready` is a *set*, published in an order derived from the row's identity rather than from its
    handle: `subgoal_id` is a random `uuid4` suffix (`core/contracts.py:new_id`), so sorting by it
    gives one order in one process and another in the next, and an arm comparison whose rows are
    visited in a different sequence would be measuring the draw. `row_key` is a `sha256` of the
    row's predicate or text — the same key `TaskPlanner.refresh` matches rows by — so this order is
    the same in every round and in every process (§12.1). The planner does not rank it and the view
    does not either.
    """
    ids = [s.subgoal_id if isinstance(s, Subgoal) else str(s) for s in ready]
    order = {s.subgoal_id: row_key(s) for s in plan.subgoals}
    ranked = sorted(ids, key=lambda sid: (order.get(sid, ""), sid))
    return {
        "version": int(plan.version),
        "goal_ref": plan.goal_ref,
        "authored_by": plan.authored_by,
        "summary": plan.summary,
        "open_obligations": int(plan.open_obligation_count),
        "ready": ranked,
        "rows": [_row(s) for s in plan.subgoals],
        "dependencies": [
            {"dep_id": d.dep_id, "from": d.from_subgoal_id, "on": d.on_subgoal_id,
             "kind": str(d.kind.value), "satisfied": str(d.satisfied.value),
             "violated": str(d.violated.value), "note": d.note}
            for d in plan.dependencies],
        "changed": dict(changed or {}),
        "violated": sorted(set(violations)),
        "revision_note": revision_note,
        "provenance": {
            "plan_id": plan.plan_id,
            "author": "planning/view.py:plan_view",
            "based_on_state_version": plan.based_on_state_version,
            "based_on_observation_ref": plan.based_on_observation_ref,
            "renderer_sha256": view_sha256()},
    }


def memory_view(rendered: dict[str, Any]) -> dict[str, Any]:
    """The working-memory block, with the counts kept and the duplicate rendering dropped.

    `WorkingMemory.render()` returns `text`, `lines` and `header`; only `text` goes into the
    payload, because `lines` is the same content split apart and a payload that carried both
    would double the cost of every debt line while adding no fact. The counts and the drop
    announcement stay: a reader of the payload must be able to see that something was left out.

    `recalled` is the one part of the block that is *not* duplicated by the text: it names which
    stored experiences the lines above came from, with their relevance and their contradiction
    flags, so a reader can tell an agent that ignored a memory from one that was never shown it.
    It is absent, not empty, on an arm with no episodic module — `render()` returns `None` for a
    loop that never looked, and `[]` for one that looked and found nothing relevant (§11's
    denominator is the difference).
    """
    counts = dict(rendered.get("counts") or {})
    out: dict[str, Any] = {
        "text": rendered.get("text") or "",
        "counts": counts,
        "lines_dropped": int(rendered.get("lines_dropped") or 0),
        "provenance": {"author": "planning/view.py:memory_view",
                       "renderer_sha256": view_sha256()},
    }
    rows = rendered.get("recalled")
    if rows is not None:
        out["recalled"] = [row.model_dump(mode="json") for row in rows]
    return out


# ------------------------------------------------------------------ the context ----
class V02DecisionContext(DecisionContext):
    """The round's context with the two v0.2 sections in the payload.

    Only `model_payload` differs from `DecisionContext`, and only by insertion: what the loop
    records, what the runtime validates and how the budget is charged are the same mechanism.
    This is the same move `benchmark/prompts.py:TextDecisionContext` makes for a backend that
    must not show geometry — the *rendering* is a property of the arm, the loop is not.

    The two views are set by the arm with `model_copy(update=...)`, which is why they are
    ordinary fields with defaults rather than constructor arguments: `_build_context` in the
    base loop builds the context before the plan exists for this round, and a subclass that
    demanded them would have to rebuild the candidate list to fill them in.
    """

    planning_view: Optional[dict[str, Any]] = None
    memory_view: Optional[dict[str, Any]] = None

    def model_payload(self, feedback_depth: int = 3) -> dict[str, Any]:
        base = super().model_payload(feedback_depth=feedback_depth)
        if self.planning_view is None and self.memory_view is None:
            # an arm with neither module answers to the byte-identical v0.1 payload
            return base
        out: dict[str, Any] = {}
        for key, value in base.items():
            if key == VIEW_ANCHOR:
                if self.planning_view is not None:
                    out["plan"] = self.planning_view
                if self.memory_view is not None:
                    out["working_memory"] = self.memory_view
            out[key] = value
        return out

    @property
    def view_keys(self) -> tuple[str, ...]:
        """Which of the two sections this round actually carries — the arm's footprint."""
        return tuple(k for k, v in zip(VIEW_KEYS,
                                       (self.planning_view, self.memory_view)) if v is not None)


# ------------------------------------------------------------------ identity ----
def view_sha256() -> str:
    """Hash of the code that renders the model-facing view, exactly once per component.

    `plan_view` + `memory_view` + the payload assembly in `V02DecisionContext.model_payload` +
    the block the memory hands them: those four texts *are* the view. `inspect.getsource` is the
    same instrument `understanding.interpreter_sha256()` uses, so a reworded docstring changes
    the hash and a reworded *rendering* changes it too — which is the conservative direction:
    an experiment that cannot tell two views apart must not claim they are the same one.
    """
    payload = "\n".join(inspect.getsource(obj) for obj in
                        (plan_view, memory_view, V02DecisionContext.model_payload,
                         WorkingMemory.render))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ log coherence ----
def _published_versions(events: Sequence[dict]) -> dict[int, str]:
    """Which plan versions this log says the model was offered, and by what record."""
    out: dict[int, str] = {}
    for ev in events:
        payload = ev.get("payload") or {}
        kind = str(ev.get("type"))
        if kind == "plan":
            view = payload.get("view") or {}
            if view.get("version") is not None:
                out[int(view["version"])] = str(ev.get("event_id") or kind)
        elif kind == "plan_revision":
            to_version = payload.get("to_version")
            if to_version is not None:
                out[int(to_version)] = str(ev.get("event_id") or kind)
    return out


def planning_coherence(events: Sequence[dict]) -> dict[str, Any]:
    """What the schema-4 record stream says about whether the planning loop closed.

    Six checks, all of them about *agreement between records* rather than about whether the
    agent did well:

    * `unpublished-version` — a round was rendered from a plan version no `plan`/`plan_revision`
      event ever put on disk. The model saw something the log cannot show.
    * `revision-chain` — a `plan_revision` whose `from_version` is not the highest version
      published so far. Two writers, or a lost event.
    * `revision-without-event` — a round whose `plan_version` jumped past a version that was
      never announced. (The bump and the event are emitted in one place, so a jump with no
      event means the version came from somewhere else.)
    * `memory-after-plan` — a `working_memory` event stamped with a state version the plan it
      syncs was not read from: the memory would be answering debts with an older world.
    * `recovery-without-row` — a `recovery_action` whose choice has no row behind it in the plan
      version it was filed under. §5.7 makes recovery a *decision* with several shapes, so the rule
      is per choice: only `restore` owes a row of kind `recover` (that is what "put it back" means
      in §8's words), while `retry`, `change_candidate` and `re_observe` owe a row the published
      plan carries at all. A restoring decision against a plain `achieve` row is a claim the plan
      cannot support; a retry against a row no published version carries is a claim about nothing.
      An episode that published no plan at all is not judged by this rule: `wo_planning` leaves the
      `replanning` module on, so its recovery decisions exist and have no row to name — that is the
      arm's shape, not a broken record.
    * `obligation-check` — not exactly one `obligation_check` per episode that ran with working
      memory on. §11's row has no denominator otherwise.

    An empty `findings` list is the pass condition, and `arms` reports the key sets so a reader
    can see that `wo_*` payloads are subsets of `full` rather than being told.
    """
    published = _published_versions(events)
    findings: list[str] = []
    memory_events = [e for e in events if str(e.get("type")) == "working_memory"]
    revisions = [e for e in events if str(e.get("type")) == "plan_revision"]
    recoveries = [e for e in events if str(e.get("type")) == "recovery_action"]
    checks = [e for e in events if str(e.get("type")) == "obligation_check"]

    # A version can only have gone unpublished if a plan was on the page to be published. Under
    # `wo_planning` the memory still stamps the empty ledger's version on its own rounds, and a
    # checker that read that as a hidden publication would call the arm a defect. A round with no
    # recorded `sections` is treated as having shown one, so the check stays strict for any writer
    # that does not say what it rendered.
    shown = sorted({int((e.get("payload") or {}).get("plan_version") or 0)
                    for e in memory_events
                    if "plan" in ((e.get("payload") or {}).get("sections") or ["plan"])})
    for version in shown:
        if version not in published:
            findings.append(f"unpublished-version:{version}")

    for ev in revisions:
        payload = ev.get("payload") or {}
        from_v, to_v = payload.get("from_version"), payload.get("to_version")
        if from_v is None or to_v is None:
            findings.append(f"revision-chain:{ev.get('event_id')}:no-versions")
        elif int(to_v) != int(from_v) + 1:
            findings.append(f"revision-chain:{from_v}->{to_v}")

    ordered = sorted(published)
    for a, b in zip(ordered, ordered[1:]):
        if b - a > 1:
            findings.append(f"revision-without-event:v{a}->v{b}")

    for ev in memory_events:
        payload = ev.get("payload") or {}
        state_version = payload.get("state_version")
        plan_version = payload.get("plan_version")
        record = payload.get("based_on_state_version")
        if state_version is not None and record is not None and int(state_version) != int(record):
            findings.append(f"memory-after-plan:v{state_version}!=plan-read-v{record}")

    kinds: dict[int, dict[str, str]] = {}
    for ev in events:
        if str(ev.get("type")) not in ("plan", "plan_revision"):
            continue
        payload = ev.get("payload") or {}
        view = payload.get("view") or {}
        version = view.get("version")
        if version is None:
            continue
        kinds[int(version)] = {row.get("row_key"): str(row.get("kind"))
                               for row in (view.get("rows") or [])}
    latest = max(kinds) if kinds else None
    for ev in recoveries:
        payload = ev.get("payload") or {}
        key = str(payload.get("row_key") or "")
        choice = str(payload.get("choice") or "")
        version = payload.get("plan_version")
        table = kinds.get(int(version)) if version is not None else kinds.get(latest or -1)
        if not kinds:
            # `recovery_action` belongs to the *replanning* module, which `wo_planning` leaves on,
            # and with no plan ever published there is no row for it to name. The event is the
            # arm's own shape, so the checker stays quiet — the choice and the skill it was made
            # with are still on the record, which is what §5.7's row counts.
            continue
        if not key:
            findings.append(f"recovery-without-row:{ev.get('event_id')}:no-row-named")
        elif table is None:
            findings.append(f"recovery-without-row:v{version} never published")
        elif choice == "restore" and table.get(key) != "recover":
            findings.append(f"recovery-without-row:{key} is "
                            f"{table.get(key) or 'absent'} in the plan, and only a `recover` row "
                            f"entitles a restore")
        elif choice != "restore" and key not in table:
            findings.append(f"recovery-without-row:{key} is in no plan version this log published")

    if memory_events and len(checks) != 1:
        findings.append(f"obligation-check:{len(checks)} for an episode that filed "
                        f"{len(memory_events)} memory rounds")

    return {
        "rounds": len(memory_events),
        "plan_events": sum(1 for e in events if str(e.get("type")) == "plan"),
        "revisions": len(revisions),
        "versions_published": sorted(published),
        "versions_shown": shown,
        "recovery_actions": len(recoveries),
        "obligation_checks": len(checks),
        "arms": sorted({str((e.get("payload") or {}).get("arm"))
                        for e in events if (e.get("payload") or {}).get("arm")}),
        "findings": sorted(set(findings)),
        "coherent": not findings,
    }


__all__ = ["VIEW_ANCHOR", "VIEW_KEYS", "VIEW_MODULE", "V02DecisionContext",
           "memory_view", "plan_view", "planning_coherence", "view_sha256"]
