"""State-utilization diagnostic (SPEC 11.3) — the 12 frozen state pairs, scored.

The question this answers is narrow and behavioural: *given two contexts that
differ in exactly one task-relevant respect, does a policy's asked-for action fit
the state it was shown?* It is not a capability claim and not a physics score —
nothing here executes, so no placement, no verification and no episode outcome is
influenced by it (SPEC 11.3: 伪造状态只用于明确标记的离线诊断，不进入物理执行成绩).

How a context is obtained, and why that order matters:

1. **Real rounds first.** One rule-policy episode of the pair's `base_case` is
   walked, and every `DecisionContext` it passes through is captured. Those
   contexts are produced by real skills on real physics, so a fork selected from
   them cannot be an artefact of a hand-written state dict. The episode-level
   version of the same idea — the same case run with the declared disturbance
   armed and disarmed — is `tests/unit/test_state_fork.py`.
2. **Marked transforms second.** Six arms are not reachable inside a single real
   episode (the region that starts full never becomes free here, a held state
   that measures ambiguous needs two bodies in contact, and the round where an
   object has drifted has no twin at its old pose). Those are built by copying a
   real context — `WorldState` containers are shared by reference, so the copy is
   deep — and changing exactly the one fact the pair is about. Each is recorded
   as `synthetic` with the transform named.
3. **Derived fields are re-derived, not edited.** A transform that moves a body
   calls the same `RuntimeVerifier.progress` the runtime calls, so the arm's
   progress is what that world reports rather than what a hand-written verdict
   claimed. Candidate check verdicts are the one thing a transform writes
   directly, because they are offers about a future action rather than a reading
   of the current snapshot; each such candidate carries a `synthetic:` note.

The acceptable action set comes from the frozen `STATE_PAIR_SPECS` and is a set of
*labels*, each a predicate over the decision and the public facts of the context —
never an answer string. `classify` below is therefore the whole measurement, and
it is deliberately small enough to be read against SPEC 5.2's information boundary:
every fact it uses is one the policy was itself shown.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.contracts import (
    CheckVerdict,
    Decision,
    DecisionContext,
    GoalProgressItem,
    PredicateVerdict,
)
from ..core.interpreter import interpret
from ..core.runtime import Runtime
from ..core.skills import SkillExecutor
from ..core.verify import RuntimeVerifier, build_world_state
from ..core.events import git_state
from ..core.fault_injection import EnvironmentController
from .run import build_scene, task_input
from .sources import RulePolicySource
from .tasks import STATE_PAIR_SPECS, find_case

CHECK_FIELDS = ("params_ok", "boundary_ok", "occupancy_ok", "reachability_ok")

# What each frozen difference is a check of, and whether the two arms are
# expected to draw *different* actions. Presentation controls are not: a policy
# that ignores the order candidates or goal entries are listed in is the good
# result there. `grasp_point_moved` is the third case — a state fork whose
# acceptable action is the same on both sides, because what changed is the
# geometry the pick has to be re-aimed at, not which action is correct.
DIFFERENCE_KINDS: dict[str, tuple[str, bool]] = {
    "held_vs_released": ("state fork", True),
    "satisfied_vs_displaced": ("state fork", True),
    "candidate_free_vs_occupied": ("state fork", True),
    "candidate_order_permuted": ("control (presentation only)", False),
    "grasp_point_moved": ("state fork (same action still legal)", False),
    "region_free_vs_full": ("state fork", True),
    "unknown_vs_known_hold": ("state fork", True),
    "failed_vs_completed_feedback": ("state fork", True),
    "pending_order": ("control (presentation only)", False),
    "all_satisfied_vs_one_pending": ("state fork", True),
}


def _unknown(value) -> bool:
    """`held_object` is `str | None | "unknown"`: two of those are not a fact about
    the gripper, and only the named third one is a known hold."""
    return value == "unknown"


def _pending(ctx: DecisionContext) -> list[GoalProgressItem]:
    return [p for p in ctx.progress if p.value != PredicateVerdict.true]


def _satisfied(ctx: DecisionContext) -> list[GoalProgressItem]:
    return [p for p in ctx.progress if p.value == PredicateVerdict.true]


def _fails(cand) -> bool:
    return any(getattr(cand, f) == CheckVerdict.fail for f in CHECK_FIELDS)


def _entity(world, entity_id):
    """`WorldState.entity` raises for an id it does not hold; a selector must be
    able to ask about an id without the diagnostic dying on it."""
    try:
        return world.entity(entity_id)
    except KeyError:
        return None


def _feasible(cand) -> bool:
    return all(getattr(cand, f) == CheckVerdict.ok for f in CHECK_FIELDS)


# ------------------------------------------------------------------ walking ----


class _Recorder:
    """Wrap a source: keep every context it was asked about, decide as before."""

    def __init__(self, inner):
        self.inner = inner
        self.seen: list[DecisionContext] = []

    def decide(self, ctx: DecisionContext) -> Decision:
        self.seen.append(ctx)
        return self.inner.decide(ctx)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def walk_case(case_id: str, root: str | None = None) -> list[DecisionContext]:
    """Every DecisionContext one rule-policy episode of `case_id` passes through.

    Real scene, real runtime, real skills: the forks below are selected from what
    an episode actually saw, not from a description of what it might see.
    """
    case = find_case(case_id)
    scene = build_scene(case)
    try:
        controller = EnvironmentController(scene, case.fresh_events())
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        runtime = Runtime(scene, executor, root or tempfile.mkdtemp(prefix="state_util_"),
                          case.budgets, f"{case_id}.stateutil", config=case.verify,
                          environment=controller)
        executor.world_provider = runtime.observe
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        rec = _Recorder(RulePolicySource(case.verify))
        runtime.run_episode(task, interpret(task, world), rec, mode="B")
        return rec.seen
    finally:
        scene.close()


# ------------------------------------------------------------------- forks -----


@dataclass
class Fork:
    """One arm of a pair: one context, one request. The `focus_*` fields say which
    entity, target or candidate the fork turns on, and the labels below are defined
    relative to them."""

    pair_id: str
    arm: str
    case_id: str
    difference: str
    context: DecisionContext
    provenance: str                      # real round, or the named transform
    synthetic: bool = False
    focus_entity: Optional[str] = None
    focus_target: Optional[str] = None
    focus_candidate: Optional[str] = None
    note: str = ""


def _first(contexts, pred) -> Optional[DecisionContext]:
    for c in contexts:
        if pred(c):
            return c
    return None


def _flip(contexts, want: str):
    """An assignment whose measured value changes between two real rounds.

    Returns (entity, target, context_before, context_after) for the first such
    change, so a pair built on it is a pair about one named relation."""
    previous: dict[str, str] = {}
    for c in contexts:
        now = {f"{p.entity_id}->{p.target_id}": p.value.value for p in c.progress}
        for key, value in now.items():
            old = previous.get(key)
            if old and old != value and ((want == "t2f" and old == "true" and value != "true")
                                         or (want == "f2t" and old != "true" and value == "true")):
                entity, target = key.split("->")
                before = _first(contexts[:contexts.index(c)],
                                lambda x, k=key, v=old: _prog(x, k) == v)
                return entity, target, before, c
        previous = now
    return None


def _prog(ctx: DecisionContext, key: str) -> Optional[str]:
    for p in ctx.progress:
        if f"{p.entity_id}->{p.target_id}" == key:
            return p.value.value
    return None


def _deep(ctx: DecisionContext) -> DecisionContext:
    """A transform must not reach back into the walked episode's own contexts."""
    return copy.deepcopy(ctx)


def _rederived(ctx: DecisionContext, config) -> DecisionContext:
    """Recompute what the runtime derives from the world after a world edit.

    A synthetic arm has to be a state the machinery would really have reported,
    not a hand-edited one: `progress` is produced by the same
    `RuntimeVerifier.progress` the runtime calls, so the pair's two arms stay
    comparable by construction. Candidate verdicts are not derived from
    `progress`, so the two candidate-only transforms skip this step."""
    ctx.progress = RuntimeVerifier(ctx.world, config).progress(ctx.goal)
    return ctx


# Each selector builds both arms of one pair from the base case's real rounds.
# Returning None is a loud result, not a skip: `build_forks` records it as
# `unreachable` and the report counts it against the diagnostic's own coverage.
# Every selector gets the base case's own VerifyConfig: the parameters that turn
# a snapshot into a verdict are part of the case, and a transform that recomputed
# progress with defaults would be reporting a different task's thresholds.
def _pair_held_vs_released(spec, contexts, config):
    held = _first(contexts, lambda c: c.world.held_object and not _unknown(c.world.held_object))
    if held is None:
        return None
    released = _first(contexts, lambda c: c.world.held_object is None and _pending(c))
    if released is None:
        return None
    entity = held.world.held_object
    target = next((p.target_id for p in _pending(released) if p.entity_id == entity), None)
    return [Fork(spec["pair_id"], "held", spec["base_case"], spec["difference"], held,
                 f"real_round:r{held.round_index}", focus_entity=entity, focus_target=target),
            Fork(spec["pair_id"], "released", spec["base_case"], spec["difference"], released,
                 f"real_round:r{released.round_index}", focus_entity=entity, focus_target=target)]


def _pair_satisfied_vs_displaced(spec, contexts, config):
    flip = _flip(contexts, "t2f")
    if not flip:
        return None
    entity, target, before, after = flip
    if before is None:
        return None
    return [Fork(spec["pair_id"], "satisfied", spec["base_case"], spec["difference"], before,
                 f"real_round:r{before.round_index}", focus_entity=entity, focus_target=target,
                 note=f"{entity}->{target} measured true"),
            Fork(spec["pair_id"], "displaced", spec["base_case"], spec["difference"], after,
                 f"real_round:r{after.round_index}", focus_entity=entity, focus_target=target,
                 note=f"{entity}->{target} no longer true in the same episode")]


def _pair_candidate_free_vs_occupied(spec, contexts, config):
    """The free arm is real; the occupied arm is the same candidate id offered after
    an occupant took its slot — a marked transform, because that is the one state
    the generator's own re-check would otherwise have to be faked to reach."""
    def with_pref(c):
        return [x for x in c.candidates if _feasible(x) and c.world.held_object == x.entity_id]
    free = _first(contexts, with_pref)
    if free is None:
        return None
    cand = with_pref(free)[0]
    busy = _deep(free)
    other = next((e for e in busy.world.entities if e.entity_id != cand.entity_id), None)
    for c in busy.candidates:
        if c.candidate_id == cand.candidate_id:
            c.occupancy_ok = CheckVerdict.fail
            c.notes = [*c.notes, "synthetic: another entity now occupies this slot"]
    return [Fork(spec["pair_id"], "free", spec["base_case"], spec["difference"], free,
                 f"real_round:r{free.round_index}", focus_entity=cand.entity_id,
                 focus_target=cand.target_id, focus_candidate=cand.candidate_id),
            Fork(spec["pair_id"], "occupied", spec["base_case"], spec["difference"], busy,
                 "transform:candidate_occupancy_failed", synthetic=True,
                 focus_entity=cand.entity_id, focus_target=cand.target_id,
                 focus_candidate=cand.candidate_id,
                 note=f"occupant={'-' if other is None else other.entity_id}")]


def _pair_candidate_order_permuted(spec, contexts, config):
    """Identical state, candidate list reversed: the control that catches a policy
    reading the first entry instead of the checks."""
    def two_for_one_entity(c):
        held = c.world.held_object
        return held and [x for x in c.candidates if x.entity_id == held] or None
    base = _first(contexts, two_for_one_entity)
    if base is None:
        return None
    held_ids = [c.candidate_id for c in base.candidates if c.entity_id == base.world.held_object]
    if len(held_ids) < 2:
        return None
    flipped = _deep(base)
    flipped.candidates = list(reversed(flipped.candidates))
    return [Fork(spec["pair_id"], "order_as_offered", spec["base_case"], spec["difference"], base,
                 f"real_round:r{base.round_index}", focus_entity=base.world.held_object,
                 focus_candidate=held_ids[0], note=f"{len(held_ids)} candidates for the held object"),
            Fork(spec["pair_id"], "order_reversed", spec["base_case"], spec["difference"], flipped,
                 "transform:candidate_list_reversed", synthetic=True,
                 focus_entity=base.world.held_object, focus_candidate=held_ids[-1],
                 note="same state, opposite presentation order")]


def _drift(world_a, world_b, entity_id) -> Optional[float]:
    """How far one measured body moved between two snapshots, or None when either
    snapshot does not contain it."""
    a, b = _entity(world_a, entity_id), _entity(world_b, entity_id)
    if a is None or b is None:
        return None
    return max(abs(a.pose.position.x - b.pose.position.x),
               abs(a.pose.position.y - b.pose.position.y))


def _pair_grasp_point_moved(spec, contexts, config):
    """One real round where a pending object has drifted from where the goal was
    parsed, and its twin with that one fact put back.

    Both arms need an empty gripper. The twin is a marked transform rather than an
    earlier round because two rounds of one episode differ in more than the pose —
    what has been placed since, what the candidate list offers — and a pair that
    differs in two respects tests neither (SPEC 11.3).
    """
    first = contexts[0]
    moved = None
    for c in contexts[1:]:
        if c.world.held_object is not None or not _pending(c):
            continue
        # the *next* open goal, not any open one: a pair about reaching for an
        # object that the policy was never going to reach for next measures the
        # order the goals were listed in instead of the geometry
        p = _pending(c)[0]
        d = _drift(first.world, c.world, p.entity_id)
        if d is not None and d > 0.005:
            moved = (p.entity_id, round(d, 4), c)
            break
    if not moved:
        return None
    entity, distance, c = moved
    as_planned = _deep(c)
    _sent_back(as_planned.world, first.world, [entity])
    _rederived(as_planned, config)
    return [Fork(spec["pair_id"], "as_planned", spec["base_case"], spec["difference"], as_planned,
                 "transform:entity_sent_back_to_parse_time_pose", synthetic=True,
                 focus_entity=entity,
                 note=f"same round, {entity} back at the pose the goal was parsed from"),
            Fork(spec["pair_id"], "moved", spec["base_case"], spec["difference"], c,
                 f"real_round:r{c.round_index}", focus_entity=entity,
                 note=f"{entity} is {distance} m from where the goal was parsed, gripper free")]


def _sent_back(world, snapshot, eids) -> list[str]:
    """Put the named bodies where a reference snapshot measured them, and drop the
    occupancy records that no longer describe them.

    The pair has to differ in one *fact*, and a body's position is a bundle: pose,
    what it is resting on, whether it is at rest. Moving only the xy would leave a
    snapshot claiming the object still touches the tray."""
    for e in world.entities:
        if e.entity_id not in eids:
            continue
        h = _entity(snapshot, e.entity_id)
        if h is None:
            continue
        e.pose = h.pose.model_copy(deep=True)
        e.support = h.support.model_copy(deep=True)
        e.supported_by = h.supported_by
        e.at_rest = h.at_rest
    world.occupancy = [o for o in world.occupancy if o.entity_id not in eids]


def _pair_region_free_vs_full(spec, contexts, config):
    """The full arm is real. Its twin is the same round with the region's measured
    occupants sent back to where the episode started them — the one fact the pair
    is about, changed as a bundle rather than by editing a verdict.

    Only the occupancy verdicts are re-offered as clearable; any other check the
    geometry still fails is left failing and counted in the note, so the synthetic
    arm cannot look better than the transform made it."""
    full = _first(contexts, lambda c: c.world.held_object and c.candidates
                  and all(_fails(x) for x in c.candidates))
    if full is None:
        return None
    target = full.candidates[0].target_id
    entity = full.world.held_object
    free = _deep(full)
    occupants = sorted({o.entity_id for o in free.world.occupancy
                        if o.target_id == target and o.entity_id != entity})
    _sent_back(free.world, contexts[0].world, occupants)
    _rederived(free, config)
    on_target = [c for c in free.candidates if c.target_id == target]
    for c in on_target:
        c.occupancy_ok = CheckVerdict.ok
        c.notes = [*c.notes, f"synthetic: {occupants} are no longer measured in {target}"]
    still = [c.candidate_id for c in on_target if _fails(c)]
    return [Fork(spec["pair_id"], "full", spec["base_case"], spec["difference"], full,
                 f"real_round:r{full.round_index}", focus_entity=entity, focus_target=target,
                 note=f"every offered candidate fails a check; {occupants} measured in {target}"),
            Fork(spec["pair_id"], "free", spec["base_case"], spec["difference"], free,
                 "transform:region_occupants_sent_back", synthetic=True,
                 focus_entity=entity, focus_target=target,
                 note=f"same round without {occupants} in {target}; {len(still)}/"
                      f"{len(on_target)} candidates still fail another check")]


def _pair_unknown_vs_known_hold(spec, contexts, config):
    known = _first(contexts, lambda c: c.world.held_object
                   and not _unknown(c.world.held_object))
    if known is None:
        return None
    entity = known.world.held_object
    blind = _deep(known)
    blind.world.held_object = "unknown"
    _rederived(blind, config)
    return [Fork(spec["pair_id"], "known", spec["base_case"], spec["difference"], known,
                 f"real_round:r{known.round_index}", focus_entity=entity,
                 note=f"gripper measurably holds {entity}"),
            Fork(spec["pair_id"], "unknown", spec["base_case"], spec["difference"], blind,
                 "transform:held_object_set_unknown", synthetic=True, focus_entity=entity,
                 note="same world, holder not identifiable: releasing would be blind")]


def _pair_failed_vs_completed_feedback(spec, contexts, config):
    failed = _first(contexts, lambda c: c.last_feedback and c.last_feedback.status == "failed")
    if failed is None:
        return None
    fb = failed.last_feedback
    done = _first(contexts, lambda c: c.last_feedback and c.last_feedback.status == "completed"
                  and c.last_feedback.skill == fb.skill
                  and c.last_feedback.entity_id == fb.entity_id)
    if done is None:
        return None
    return [Fork(spec["pair_id"], "failed", spec["base_case"], spec["difference"], failed,
                 f"real_round:r{failed.round_index}", focus_entity=fb.entity_id,
                 focus_target=fb.target_id, note=f"last {fb.skill} -> {fb.failure_code}"),
            Fork(spec["pair_id"], "completed", spec["base_case"], spec["difference"], done,
                 f"real_round:r{done.round_index}", focus_entity=fb.entity_id,
                 focus_target=fb.target_id, note=f"last {fb.skill} completed")]


def _pair_pending_order(spec, contexts, config):
    base = _first(contexts, lambda c: len(_pending(c)) >= 2 and c.world.held_object is None)
    if base is None:
        return None
    swapped = _deep(base)
    pending = [i for i, p in enumerate(swapped.progress) if p.value != PredicateVerdict.true]
    swapped.progress[pending[0]], swapped.progress[pending[1]] = (
        swapped.progress[pending[1]], swapped.progress[pending[0]])
    names = [base.progress[pending[0]].entity_id, base.progress[pending[1]].entity_id]
    return [Fork(spec["pair_id"], "order_as_listed", spec["base_case"], spec["difference"], base,
                 f"real_round:r{base.round_index}", focus_entity=names[0],
                 note=f"pending order as the runtime listed it: {names}"),
            Fork(spec["pair_id"], "order_swapped", spec["base_case"], spec["difference"], swapped,
                 "transform:two_pending_progress_entries_swapped", synthetic=True,
                 focus_entity=names[1], note="same state, the two pending goals exchanged places")]


def _pair_all_satisfied_vs_one_pending(spec, contexts, config):
    all_done = _first(contexts, lambda c: c.progress and not _pending(c))
    one_left = _first(contexts, lambda c: len(_pending(c)) == 1 and c.world.held_object is None)
    if all_done is None or one_left is None:
        return None
    return [Fork(spec["pair_id"], "all_satisfied", spec["base_case"], spec["difference"], all_done,
                 f"real_round:r{all_done.round_index}"),
            Fork(spec["pair_id"], "one_pending", spec["base_case"], spec["difference"], one_left,
                 f"real_round:r{one_left.round_index}",
                 focus_entity=_pending(one_left)[0].entity_id,
                 focus_target=_pending(one_left)[0].target_id)]


SELECTORS: dict[str, Callable] = {
    "held_vs_released": _pair_held_vs_released,
    "satisfied_vs_displaced": _pair_satisfied_vs_displaced,
    "candidate_free_vs_occupied": _pair_candidate_free_vs_occupied,
    "candidate_order_permuted": _pair_candidate_order_permuted,
    "grasp_point_moved": _pair_grasp_point_moved,
    "region_free_vs_full": _pair_region_free_vs_full,
    "unknown_vs_known_hold": _pair_unknown_vs_known_hold,
    "failed_vs_completed_feedback": _pair_failed_vs_completed_feedback,
    "pending_order": _pair_pending_order,
    "all_satisfied_vs_one_pending": _pair_all_satisfied_vs_one_pending,
}


def build_forks(only: Optional[list[str]] = None,
                walked: Optional[dict] = None) -> tuple[list[Fork], list[dict]]:
    """Both arms of every frozen pair, or an explicit account of why not."""
    forks: list[Fork] = []
    missing: list[dict] = []
    cache = walked if walked is not None else {}
    for spec in STATE_PAIR_SPECS:
        if only and spec["pair_id"] not in only:
            continue
        selector = SELECTORS.get(spec["difference"])
        if selector is None:
            missing.append({"pair_id": spec["pair_id"], "reason": "no selector implemented"})
            continue
        if spec["base_case"] not in cache:
            cache[spec["base_case"]] = walk_case(spec["base_case"])
        built = selector(spec, cache[spec["base_case"]],
                         find_case(spec["base_case"]).verify)
        if not built:
            missing.append({"pair_id": spec["pair_id"], "base_case": spec["base_case"],
                            "reason": f"no real round of {spec['base_case']} shows "
                                      f"{spec['difference']}"})
            continue
        forks += built
    return forks, missing


# --------------------------------------------------------------- labels --------
# Each label is a predicate over the asked-for action and the public facts of the
# context. Nothing here reads the hidden EvalSpec, the fault config or the verifier.

@dataclass
class Ask:
    """What the policy asked for, reduced to the facts the labels need."""

    action: str
    skill: Optional[str] = None
    object_id: Optional[str] = None
    target_id: Optional[str] = None
    candidate_id: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def is_skill(self) -> bool:
        return self.action == "execute" and self.skill is not None


def ask_of(decision: Decision) -> Ask:
    ex = decision.execute
    if decision.action != "execute" or ex is None:
        return Ask(action=decision.action, raw=decision.model_dump(mode="json"))
    args = dict(ex.args)
    return Ask(action="execute", skill=ex.skill, object_id=args.get("object_id"),
               target_id=args.get("target_id"), candidate_id=ex.candidate_id,
               raw=decision.model_dump(mode="json"))


def _progress_of(ctx, entity) -> Optional[str]:
    for p in ctx.progress:
        if p.entity_id == entity:
            return p.value.value
    return None


def _cand(ctx, cid):
    return next((c for c in ctx.candidates if c.candidate_id == cid), None)


# The three candidate labels below all start from "which entry did the policy
# name", so that step gets one helper rather than three near-identical expressions.
def _named(ask: Ask, fork: "Fork"):
    return _cand(fork.context, ask.candidate_id) if ask.candidate_id else None


def _known_named(ask, fork) -> bool:
    return _named(ask, fork) is not None


def _fails_named(ask, fork) -> bool:
    c = _named(ask, fork)
    return c is not None and _fails(c)


def _feasible_named(ask, fork) -> bool:
    c = _named(ask, fork)
    return c is not None and _feasible(c)


def _same_as_last_feedback(fork: Fork, ask: Ask, status: str) -> bool:
    fb = fork.context.last_feedback
    return bool(fb and fb.status == status and fb.skill == ask.skill
                and fb.entity_id == ask.object_id)


def _place_still_blocked(fork: Fork, ask: Ask) -> bool:
    """Every slot offered for that hand-off is one the checks already refused.

    Trying it again is the same blocked attempt, not new work, so it cannot count
    as advancing: the policy was shown the failing verdicts.
    """
    if ask.skill != "place":
        return False
    pool = [c for c in fork.context.candidates if c.entity_id == ask.object_id
            and (ask.target_id is None or c.target_id == ask.target_id)]
    return bool(pool) and all(_fails(c) for c in pool)


LABELS: dict[str, Callable[[Ask, Fork], bool]] = {
    # ---- the verbs themselves
    "observe": lambda a, f: a.action == "execute" and a.skill == "observe",
    "safe_retreat": lambda a, f: a.action == "execute" and a.skill == "safe_retreat",
    "finish": lambda a, f: a.action == "finish",
    "clarify": lambda a, f: a.action == "clarify",
    "blocked": lambda a, f: a.action == "blocked",
    "pick": lambda a, f: a.skill == "pick",
    "place": lambda a, f: a.skill == "place",
    # ---- picks, distinguished by what the gripper and the progress list say
    "pick_other": lambda a, f: a.skill == "pick" and f.context.world.held_object is not None
    and a.object_id != f.context.world.held_object,
    "pick_already_satisfied": lambda a, f: a.skill == "pick"
    and _progress_of(f.context, a.object_id) == "true",
    "pick_any_pending": lambda a, f: a.skill == "pick"
    and _progress_of(f.context, a.object_id) not in ("true", None),
    "re_pick_satisfied": lambda a, f: a.skill == "pick" and a.object_id == f.focus_entity
    and _progress_of(f.context, f.focus_entity) == "true",
    # ---- placements, distinguished by whose candidate (if any) was named
    "place_with_named_candidate": lambda a, f: a.skill == "place" and _known_named(a, f)
    and not _fails_named(a, f),
    "place_with_any_feasible_candidate": lambda a, f: a.skill == "place" and _feasible_named(a, f),
    "place_with_infeasible_candidate": lambda a, f: a.skill == "place" and _fails_named(a, f),
    "place_with_occupied_candidate": lambda a, f: a.skill == "place"
    and bool(a.candidate_id) and a.candidate_id == f.focus_candidate,
    "place_with_other_candidate": lambda a, f: a.skill == "place" and bool(a.candidate_id)
    and a.candidate_id != f.focus_candidate,
    "place_with_named_candidate_rejected_last_round": lambda a, f: a.skill == "place"
    and bool(a.candidate_id) and f.context.last_feedback is not None
    and f.context.last_feedback.candidate_id == a.candidate_id
    and f.context.last_feedback.status == "rejected",
    # ---- work on the open part of the task instead of standing still. Naming the
    # same object as the last completed pick is not a repeat: placing it *is* the
    # next step of that assignment, so the comparison is with the last action.
    "advance_to_next_pending": lambda a, f: a.skill in ("pick", "place")
    and not _same_as_last_feedback(f, a, "completed")
    and not _place_still_blocked(f, a)
    and _progress_of(f.context, a.object_id) not in ("true", None),
    # ---- what the last feedback says about repeating itself
    "retry_same": lambda a, f: a.is_skill and _same_as_last_feedback(f, a, "failed"),
    "repeat_just_completed_pick": lambda a, f: a.skill == "pick"
    and _same_as_last_feedback(f, a, "completed"),
}


def classify(ask: Ask, fork: Fork) -> list[str]:
    """Every frozen label this answer carries. More than one is normal: the sets
    are nested (`place` and `place_with_any_feasible_candidate` both hold when a
    feasible id is named), which is why a label on the rejected list wins.

    A label that raises is not caught here: a predicate that cannot be evaluated
    on a real context is a broken measurement, and swallowing it would turn "not
    assessed" into "did not apply" — the two must not share a code path.
    `run_state_utilization` records the error against the context instead."""
    return sorted(name for name, pred in LABELS.items() if bool(pred(ask, fork)))


def score_fork(fork: Fork, decision: Decision, feedback_depth: int = 3) -> dict:
    spec = next(s for s in STATE_PAIR_SPECS if s["pair_id"] == fork.pair_id)
    ask = ask_of(decision)
    labels = classify(ask, fork)
    accept = list(spec["accept"].get(fork.arm, [])) + list(spec["accept"].get("any", []))
    reject = list(spec["reject"].get(fork.arm, [])) + list(spec["reject"].get("any", []))
    accepted_hit = [x for x in labels if x in accept]
    rejected_hit = [x for x in labels if x in reject]
    fit = bool(accepted_hit) and not rejected_hit
    return {
        **score_record_shell(fork, feedback_depth),
        "asked": ask.raw,
        "named_a_candidate": bool(ask.candidate_id),
        "labels": labels,
        "acceptable_labels": accept, "rejected_labels": reject,
        "accepted_because": accepted_hit, "rejected_because": rejected_hit,
        "fit": fit,
        "miss_kind": None if fit else "wrong_choice",
        "error": None,
    }


# Why a context did not fit is not one fact. A policy that chose badly is the thing
# being measured; a payload no schema accepted, or a request that never landed, is
# a fact about this run — kept in the denominator (SPEC 11.5) but named separately
# so a reader can see how much of the rate is machinery.
_REQUEST_FAILURES = ("LLMError", "TimeoutError", "OSError", "URLError", "HTTPError",
                     "ConnectionError", "JSONDecodeError")


def _miss_kind(error: str) -> str:
    head = error.split(":", 1)[0].strip()
    if head == "DecisionSchemaError":
        return "schema_invalid"
    if head in _REQUEST_FAILURES:
        return "request_failed"
    return "source_error"


def _score_error(fork: Fork, error: str, feedback_depth: int = 3) -> dict:
    base = score_record_shell(fork, feedback_depth)
    base.update({"fit": False, "miss_kind": _miss_kind(error), "error": error})
    return base


def score_record_shell(fork: Fork, feedback_depth: int = 3) -> dict:
    """The fields every row carries, scored or not.

    `shown_to_the_policy` is the payload the policy was actually asked about, in
    the same thinned form the adapter sends: a row that only records *which*
    frozen labels matched cannot be audited, and the SPEC 11.4 blind review needs
    the view rather than a description of it (the ids in here are this fork's, so
    re-walking the episode later would not reproduce them)."""
    return {"pair_id": fork.pair_id, "arm": fork.arm, "case_id": fork.case_id,
            "difference": fork.difference, "provenance": fork.provenance,
            "synthetic": fork.synthetic, "note": fork.note,
            "context_id": fork.context.context_id,
            "state_version": fork.context.state_version,
            "observation_ref": fork.context.observation_ref,
            "focus": {"entity": fork.focus_entity, "target": fork.focus_target,
                      "candidate": fork.focus_candidate},
            "shown_to_the_policy": fork.context.model_payload(feedback_depth),
            "named_a_candidate": False, "asked": None, "labels": [],
            "acceptable_labels": [], "rejected_labels": [],
            "accepted_because": [], "rejected_because": [],
            "miss_kind": None, "error": None}


def _behaviour(ask: Ask) -> tuple:
    """The action stripped of its rationale: what a fork comparison can differ in."""
    return (ask.action, ask.skill, ask.object_id, ask.target_id, ask.candidate_id)


def aggregate(records: list[dict], forks: list[Fork]) -> dict:
    """Per-pair verdict plus the headline rate, each with its denominator stated."""
    by_pair: dict[str, list[dict]] = {}
    for r in records:
        by_pair.setdefault(r["pair_id"], []).append(r)
    pairs = {}
    for pair_id, rows in sorted(by_pair.items()):
        difference = rows[0]["difference"]
        kind, needs_difference = DIFFERENCE_KINDS[difference]
        entry = {
            "arms": sorted(r["arm"] for r in rows),
            "both_arms_fit": all(r["fit"] for r in rows) and len(rows) == 2,
            "fit_by_arm": {r["arm"]: r["fit"] for r in rows},
            "synthetic_arms": [r["arm"] for r in rows if r["synthetic"]],
            # SPEC 11.3 asks for a behaviour difference, not a stated reason: two
            # forks that draw the same action did not use the distinction at all.
            "kind_of_check": kind, "a_difference_is_required": needs_difference,
        }
        behaviours = {_behaviour_of(r) for r in rows}
        entry["changed_under_reordering" if kind.startswith("control")
              else "behaviour_differs"] = len(behaviours) > 1
        # A pair about *which slot* cannot be settled by an answer that names no
        # slot: a bare `place` is on the frozen acceptable list of both arms, so
        # the rate would read as a success while testing no geometry choice at all.
        spec = _spec_of(pair_id)
        if spec is not None and _pair_names_candidates(spec):
            entry["arms_naming_a_candidate"] = sum(1 for r in rows if r["named_a_candidate"])
            if entry["arms_naming_a_candidate"] == 0:
                entry["candidate_choice_not_tested"] = True
        pairs[pair_id] = entry
    fitted = sum(1 for r in records if r["fit"])
    # an answer that never arrived is not a policy's choice, even though it stays
    # in the denominator: the two have to be separable by a reader (SPEC 11.5)
    misses: dict[str, int] = {}
    for r in records:
        if r["fit"]:
            continue
        misses[r.get("miss_kind") or "wrong_choice"] = misses.get(r.get("miss_kind")
                                                                  or "wrong_choice", 0) + 1
    answered = [r for r in records
                if r["fit"] or r.get("miss_kind") == "wrong_choice"]
    untested = sorted(pid for pid, p in pairs.items() if p.get("candidate_choice_not_tested"))
    return {
        "contexts_requested": len(records),
        "contexts_fitted": fitted,
        "action_fit_rate": round(fitted / len(records), 4) if records else None,
        "misses_by_kind": dict(sorted(misses.items())),
        "contexts_answered": len(answered),
        "action_fit_rate_of_answers": round(fitted / len(answered), 4) if answered else None,
        "contexts_naming_a_candidate": sum(1 for r in records if r["named_a_candidate"]),
        "pairs_assessed": len(pairs),
        "pairs_with_both_arms_fitting": sum(1 for p in pairs.values() if p["both_arms_fit"]),
        "pairs_where_state_changed_behaviour": sum(
            1 for p in pairs.values() if p.get("behaviour_differs")),
        "pairs_requiring_a_difference": sum(1 for p in pairs.values() if p["a_difference_is_required"]),
        "pairs_that_did_not_use_the_distinction": sorted(
            pid for pid, p in pairs.items()
            if p["a_difference_is_required"] and not p.get("behaviour_differs")),
        "pairs_where_candidate_choice_went_untested": untested,
        "synthetic_arms": sum(1 for r in records if r["synthetic"]),
        "pairs": pairs,
    }


def _behaviour_of(record: dict) -> tuple:
    return _behaviour(ask_of_raw(record))


def _spec_of(pair_id: str) -> Optional[dict]:
    return next((s for s in STATE_PAIR_SPECS if s["pair_id"] == pair_id), None)


def _pair_names_candidates(spec: dict) -> bool:
    return any("candidate" in label
               for group in ("accept", "reject")
               for labels in spec[group].values() for label in labels)


def ask_of_raw(record: dict) -> Ask:
    asked = record.get("asked")
    if not asked:
        return Ask(action="none")
    return ask_of(Decision(**asked))


# ------------------------------------------------------------------ driver -----


COUNTER_KEYS = ("http_requests", "prompt_tokens", "completion_tokens",
                "api_errors", "transport_retries", "format_repairs")


def _counters(source) -> dict:
    return {k: int(getattr(source, k, 0) or 0) for k in COUNTER_KEYS}


def run_state_utilization(source_factory: Callable[[], object], *, out_root: str,
                          label: str = "source", only: Optional[list[str]] = None,
                          forks: Optional[list[Fork]] = None) -> dict:
    """One request per frozen fork context, scored, and written down.

    `source_factory` is called once per context: a policy that carries state from
    one fork to the next would make the pair meaningless (SPEC 11.3 asks for the
    contexts to be judged independently).

    `forks` scores a second policy against an already-built fork set. Passing it is
    the normal thing when comparing policies: the arms two policies are asked
    about have to be the same arms, and re-walking the episodes can only be equal
    by luck of determinism, which is not a property worth relying on.
    """
    os.makedirs(out_root, exist_ok=True)
    if forks is None:
        forks, missing = build_forks(only=only)
    else:
        # the pairs a supplied fork set does not contain are exactly the ones it
        # cannot speak about; `only` says which pairs were asked for at all, so a
        # partial run reports the pairs it skipped rather than the ten it omitted
        built = {f.pair_id for f in forks}
        asked = only or [s["pair_id"] for s in STATE_PAIR_SPECS]
        missing = [{"pair_id": s["pair_id"], "reason": "not in the supplied fork set"}
                   for s in STATE_PAIR_SPECS
                   if s["pair_id"] in asked and s["pair_id"] not in built]
    records = []
    cost: list[dict] = []
    provider = "unknown"
    for index, fork in enumerate(forks):
        source = source_factory()
        if index == 0:
            provider = str(getattr(source, "provider", "unknown"))
        # bill this context's delta, not the source's lifetime total: the 24
        # sources normally share one adapter, so summing their counters would
        # charge every context for all the traffic before it — the accounting rule
        # the Runtime itself uses (SPEC 6.2: 网络重试不能藏在请求适配器内部)
        before = _counters(source)
        depth = int(getattr(source, "feedback_depth", 3))
        try:
            decision = source.decide(fork.context)
            record = score_fork(fork, decision, depth)
        except Exception as e:  # noqa: BLE001 - an unusable answer is a result, not a crash
            record = _score_error(fork, f"{type(e).__name__}: {e}", depth)
        spent = {k: max(0, v - before[k]) for k, v in _counters(source).items()}
        record["provider_cost"] = spent
        cost.append(spent)
        records.append(record)
    counters = {k: sum(c[k] for c in cost) for k in COUNTER_KEYS}
    summary = {
        "kind": "state_utilization", "policy_label": label,
        "policy_provider": provider,
        "diagnostic_only_no_physics_executed": True,
        "code": git_state(),
        **aggregate(records, forks),
        "provider_counters": counters,
        "unreachable_pairs": missing,
        "definitions": {
            "action_fit_rate": "contexts whose asked-for action carries one of the labels the "
                               "pair froze as acceptable and none it froze as rejected; the "
                               "denominator is every context reached, and nothing was executed",
            "misses_by_kind": "of the contexts that did not fit: `wrong_choice` answered with a "
                              "valid Decision that the pair rejects, `schema_invalid` answered "
                              "with a payload the decision schema refused, `request_failed` was "
                              "never answered at all. Only the first is a measurement of a "
                              "policy; the others are facts about this run, kept in the "
                              "denominator rather than dropped (SPEC 11.5)",
            "action_fit_rate_of_answers": "the same numerator over the contexts that produced a "
                                          "decision at all — shown so the size of the machinery "
                                          "problem is visible, not so it can be excluded",
            "behaviour_differs": "the two arms of a pair drew different (action, skill, object, "
                                "target, candidate) tuples — the evidence SPEC 11.3 wants, since "
                                "a stated reason is not one",
            "changed_under_reordering": "presentation controls only: reordering candidates or the "
                                        "pending list moved the action, which is the failure the "
                                        "control exists to detect, not a requirement",
            "a_difference_is_required": "the pair froze different acceptable actions for its two "
                                        "arms, so drawing the same one in both did not use the "
                                        "distinction; pairs like the order controls and the "
                                        "re-aimed grasp do not require one",
            "synthetic": "this arm's context was built from a real one by the named transform "
                         "because no real round of the base case shows that state (SPEC 11.3 "
                         "allows it for a marked diagnostic, and bars it from physics scores)",
            "candidate_choice_not_tested": "a pair whose frozen acceptable labels are about "
                                           "which slot to use, answered in both arms without "
                                           "naming a slot at all: the fit it reports says "
                                           "nothing about geometry, and the rate must be read "
                                           "with that pair set aside",
            "provider_counters": "the diagnostic's own cost (SPEC 11.4): the deltas of the "
                                 "requests it made, summed. A source is built per context so the "
                                 "forks stay independent, but the sources of one run normally "
                                 "share one adapter whose counters are cumulative, so a lifetime "
                                 "total is never added twice. An offline control policy makes no "
                                 "requests and the counters read 0 — that is the difference "
                                 "between this line and an empty one",
        },
    }
    payload = {"summary": summary, "contexts": records}
    with open(os.path.join(out_root, "state_utilization.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    # the same object that lands on disk: a caller that scored a supplied fork set
    # needs the per-context rows to compare two policies arm by arm, and re-reading
    # the file would only mean the returned result depends on the write succeeding
    return payload
