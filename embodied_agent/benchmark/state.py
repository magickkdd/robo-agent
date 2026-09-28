"""WorldState and verification read out of public text (SPEC-BST 4.2, 4.6).

Two rules shape this whole module, and both come from what a sentence can and
cannot prove:

* **Only this snapshot.** The returned `WorldState` contains the facts this text
  states, and nothing else. An object that was in an open drawer three steps ago
  is not in these facts; neither is it "not there" — the current sentence simply
  does not say. Earlier statements remain reachable the way v0.1 left them, in
  the bounded recent-feedback window, where each carries its own observation ref
  (SPEC-BST 4.2: 不自动沿用上一轮为"当前事实"; 4.3: 不得在 Adapter 内积累持久对象地图).
* **Missing is not false.** No coordinates, no receptacle list, no holding
  report — each stays `unknown`/`null` rather than becoming a zero, an empty
  list or a closed hand (SPEC-BST 4.2: 无坐标时不填零向量; 看不到物体不等于物体不存在;
  没有持有报告不等于空手).

`TextVerifier` therefore never claims to understand a task sentence. It restates
what the environment said about one action, and returns `unknown` — the honest
answer for a natural-language goal with no public proof — for the goal itself
(SPEC-BST 4.6: 缺少通用自然语言目标验证器是已知事实).
"""
from __future__ import annotations

import time
from typing import Optional, Union

from ..core.contracts import (
    GoalSpec,
    GoalProgressItem,
    PredicateReport,
    PredicateVerdict,
    Source,
    VerificationReport,
    VerifyConfig,
    WorldState,
    EntityState,
    SupportEvidence,
    TextFact,
)
from .native_actions import INSTANCE_RE
from .parser import parse

# What the instruction is worth as a progress item: an identity for the row, not a
# claim about an object. Both names say which backend they came from.
TASK_ENTITY = "task"
TASK_TARGET = "benchmark_instruction"
TASK_PREDICATE = "alfworld_task_instruction_satisfied"
NO_EFFECT = "the environment reported no effect"


def parse_status_of(facts: list[TextFact], unparsed: list[str]) -> str:
    if not unparsed:
        return "parsed"
    return "unparsed" if not facts else "partial"


def text_world_state(raw: str, state_version: int, observation_ref: str, *,
                     sim_time: float = 0.0) -> WorldState:
    """One cached public response -> one snapshot. Reading it twice does not make
    a second measurement (SPEC-BST 4.3), so callers that want that must check the
    text they were handed, not the version number."""
    p = parse(raw)
    facts = [f.model_copy(update={"observation_ref": observation_ref}) for f in p.facts]
    # Hands are the one thing a text world reports exhaustively: once the
    # environment has said what is carried, every other named thing provably is
    # not carried. Without such a sentence nothing about hands is known at all.
    known = p.held_said
    entities = []
    for eid in sorted(p.entities):
        attrs = dict(p.entities[eid])
        held: Union[bool, str] = ("unknown" if not known else (eid in p.held))
        entities.append(EntityState(
            entity_id=eid, body_id=-1, pose=None, geometry=None, held=held,
            support=SupportEvidence(support_id="unknown", contact_count=0,
                                    source=Source.local_text),
            supported_by="unknown", at_rest="unknown",
            visible=True, attributes=attrs, source=Source.local_text))
    held_object: Union[str, None] = "unknown"
    if known:
        held_object = p.held[0] if len(p.held) == 1 else (None if not p.held else "unknown")
    return WorldState(
        state_version=state_version, sim_time=float(sim_time), wall_time=time.time(),
        entities=entities, targets=[], held_object=held_object,
        observation_ref=observation_ref, occupancy=[], source=Source.local_text,
        raw_observation=raw, unparsed=list(p.unparsed), facts=facts,
        location=p.location or "unknown",
        parse_status=parse_status_of(facts, list(p.unparsed)))


def is_instance(name: Optional[str]) -> bool:
    return bool(name) and bool(INSTANCE_RE.match(str(name).strip()))


# ------------------------------------------------------- measurement of change --


def fact_key(f: TextFact) -> str:
    """One fact as one comparable token, so a difference is quotable in a log.

    The observation ref is deliberately absent: what says *when* it was measured
    must not be what says *whether* the world looks different (SPEC-BST 5.3: 重复
    守卫不得用 state_version 抵消无新证据的重复)."""
    return f"{f.kind}:{f.subject}->{f.related}={f.state}"


def text_fingerprint(world: WorldState) -> tuple:
    """What this snapshot says, with nothing attached about when it was said.

    Two actions taken under the same fingerprint got no new information between
    them, whatever their observation refs were."""
    return (str(world.location), str(world.held_object),
            tuple(sorted((e.entity_id, tuple(sorted(e.attributes.items())), str(e.held))
                         for e in world.entities)),
            tuple(sorted(fact_key(f) for f in world.facts)))


def text_state_diff(pre_world: WorldState, post_world: WorldState) -> dict:
    """The difference between two snapshots, stated as the difference between what
    the two texts said. There is no position to move by (SPEC-BST 4.2), so the keys
    the desktop loop reads — `moved`, `state_changed` — carry fact-level changes and
    say so in their own field names."""
    before = {e.entity_id: {fact_key(f) for f in pre_world.facts
                            if e.entity_id in (f.subject, f.related)}
              for e in pre_world.entities}
    after = {e.entity_id: {fact_key(f) for f in post_world.facts
                           if e.entity_id in (f.subject, f.related)}
             for e in post_world.entities}

    def stated_holder(w: WorldState, eid: str) -> list[str]:
        return sorted({str(f.subject) for f in w.facts
                       if f.kind == "contents" and f.related == eid})

    moved: list[dict] = []
    changed: list[dict] = []
    for eid in sorted(after):
        now, was = after[eid], before.get(eid)
        if was is None:
            changed.append({"entity_id": eid, "change": "first named in this snapshot",
                            "facts_now_stated": sorted(now)})
            continue
        if now == was:
            continue
        changed.append({"entity_id": eid,
                        "facts_stopped_being_stated": sorted(was - now),
                        "facts_now_stated": sorted(now - was)})
        if stated_holder(pre_world, eid) != stated_holder(post_world, eid):
            moved.append({"entity_id": eid,
                          "stated_holder_before": stated_holder(pre_world, eid),
                          "stated_holder_after": stated_holder(post_world, eid)})
    return {"moved": moved, "state_changed": changed,
            "held_before": str(pre_world.held_object), "held_after": str(post_world.held_object),
            "location_before": str(pre_world.location), "location_after": str(post_world.location),
            "same_response": bool(pre_world.raw_observation)
            and pre_world.raw_observation == post_world.raw_observation,
            "note": "a text world's difference is a difference of stated facts; nothing here "
                    "measured a position, and a fact that stopped being stated did not become "
                    "false — this snapshot just does not say it"}


# ------------------------------------------------------------------ goal -----


class TextGoalSpec(GoalSpec):
    """The benchmark instruction, kept verbatim, with the desktop assignment list
    marked not applicable instead of being filled with a scoring target
    (SPEC-BST 4.6).

    `well_formed` reads the instruction rather than an assignment count, because
    an empty `assignments` here is honesty about this backend, not an unresolved
    task: a task with no instruction text is the only malformed case."""

    instruction: str = ""
    assignments_not_applicable: str = "no desktop entity/target mapping exists for a text game"

    @property
    def well_formed(self) -> bool:  # type: ignore[override]
        return bool(self.instruction.strip()) and not self.ambiguous


def goal_from_instruction(instruction: str, *, state_version: int) -> TextGoalSpec:
    """The goal of a text episode is the sentence the environment printed, kept
    word for word. `assignments` stays empty: filling it would mean either
    inventing an entity/target pair or copying a scoring target (SPEC-BST 4.5, 4.6)."""
    return TextGoalSpec(instruction=instruction, assignments=[],
                        based_on_state_version=state_version)


# ----------------------------------------------------------- verification ----


def _fact_world(world: WorldState) -> dict[str, list[TextFact]]:
    by_kind: dict[str, list[TextFact]] = {}
    for f in world.facts:
        by_kind.setdefault(f.kind, []).append(f)
    return by_kind


def _effect_claim(skill: str, args: dict[str, str]) -> tuple[str, str]:
    """(predicate id, what would have to appear in this snapshot's text). Named per
    verb because the environment's sentence *is* the effect; there is no separate
    internal state to compare against."""
    obj = (args.get("object_id") or "").strip()
    rec = (args.get("target_id") or "").strip()
    if skill == "alfred_take":
        return (f"held:{obj}", f"the text names {obj} as carried")
    if skill == "alfred_move":
        return (f"in:{rec}:{obj}", f"the text lists {obj} among the contents of {rec}")
    if skill == "alfred_go":
        return (f"at:{rec}", f"the text reports the agent at {rec}")
    if skill in ("alfred_open", "alfred_close", "alfred_use"):
        want = {"alfred_open": "open", "alfred_close": "closed",
                "alfred_use": "on or off"}[skill]
        return (f"{skill}:{rec}", f"the text says {rec} is {want}")
    if skill in ("alfred_heat", "alfred_cool", "alfred_clean"):
        op = skill.replace("alfred_", "")
        return (f"{skill}:{obj}", f"the text reports {obj} was {op}ed by {rec or 'an appliance'}")
    return (f"{skill}:{rec or obj or '-'}", "the environment answered this command")


def _effect_holds(skill: str, args: dict[str, str], by_kind: dict[str, list[TextFact]]) -> bool:
    obj = (args.get("object_id") or "").strip()
    rec = (args.get("target_id") or "").strip()

    def has(kind: str, **want) -> bool:
        for f in by_kind.get(kind, []):
            if all(getattr(f, k) == v for k, v in want.items()):
                return True
        return False

    if skill == "alfred_take":
        return has("held", related=obj)
    if skill == "alfred_move":
        return has("contents", subject=rec, related=obj)
    if skill == "alfred_go":
        return has("location", subject="agent", related=rec)
    if skill == "alfred_open":
        return has("receptacle_state", subject=rec, state="open")
    if skill == "alfred_close":
        return has("receptacle_state", subject=rec, state="closed")
    if skill == "alfred_use":
        return any(f.subject == rec and f.state in ("on", "off")
                   for f in by_kind.get("receptacle_state", []))
    if skill in ("alfred_heat", "alfred_cool", "alfred_clean"):
        op = skill.replace("alfred_", "")
        return any(f.subject == obj and str(f.state or "").startswith(op)
                   for f in by_kind.get("operation", []))
    # look / inventory / examine: a measurement, so the answer itself is the effect
    return bool(by_kind)


class TextVerifier:
    """`progress` / `verify_goals` against the desktop verifier's interface, over
    the facts of one snapshot (duck-typed by `Runtime._verifier`)."""

    def __init__(self, world: WorldState, config: Optional[VerifyConfig] = None):
        self.world = world
        self.config = config or VerifyConfig()

    def _report(self, predicate_id: str, description: str, value: PredicateVerdict,
                *, evidence: Optional[dict[str, float]] = None,
                unmeasured: Optional[list[str]] = None) -> PredicateReport:
        return PredicateReport(
            predicate_id=predicate_id, description=description, value=value,
            evidence=dict(evidence or {}),
            evidence_refs=[self.world.observation_ref] if self.world.observation_ref else [],
            unmeasured=list(unmeasured or []), source=Source.local_text)

    def progress(self, goal: GoalSpec) -> list[GoalProgressItem]:
        """One row, `unknown`, naming why. The instruction is a sentence about the
        world's goal; no public observation in this benchmark states that it is
        satisfied, so nothing here can grade it (SPEC-BST 4.6)."""
        instruction = getattr(goal, "instruction", "") or ""
        if not str(instruction).strip():
            return []
        return [GoalProgressItem(
            entity_id=TASK_ENTITY, target_id=TASK_TARGET, value=PredicateVerdict.unknown,
            evidence_refs=[self.world.observation_ref] if self.world.observation_ref else [],
            unmeasured=["no public text states the task instruction is satisfied",
                        "the official success flag is not an observation (SPEC-BST 4.5)"],
            predicate_id=TASK_PREDICATE)]

    def verify_goals(self, goal: GoalSpec) -> VerificationReport:
        items = self.progress(goal)
        instruction = getattr(goal, "instruction", "") or ""
        report = self._report(
            TASK_PREDICATE,
            f"the task instruction is satisfied: {instruction!r}" if instruction
            else "no instruction text was carried into this goal",
            PredicateVerdict.unknown,
            unmeasured=(items[0].unmeasured if items else ["no instruction text"]))
        return VerificationReport(
            reports=[report], source=Source.local_text,
            tolerance_version=self.config.tolerance_version,
            world_observation_ref=self.world.observation_ref,
            state_version=self.world.state_version)

    def verify_action(self, skill: str, args: dict[str, str]) -> list[PredicateReport]:
        """What the environment's own sentence said about the one command issued.
        `false` is only ever read from a stated refusal; a sentence that says
        nothing about the effect is `unknown` (SPEC-BST 4.6: 不从一句笼统消息生成
        精细失败原因，不把 lack-of-evidence 判成成功)."""
        pid, description = _effect_claim(skill, args)
        by_kind = _fact_world(self.world)
        if by_kind.get("no_effect"):
            return [self._report(pid, description, PredicateVerdict.false,
                                 evidence={"reported_no_effect": 1.0},
                                 unmeasured=[NO_EFFECT])]
        if _effect_holds(skill, args, by_kind):
            return [self._report(pid, description, PredicateVerdict.true)]
        return [self._report(pid, description, PredicateVerdict.unknown,
                             unmeasured=["this snapshot's text does not state the effect"])]
