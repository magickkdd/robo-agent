"""Subgoals and dependencies derived from what the instruments already measured (SPEC-v0.2 §5.2).

SPEC §5.2 gives the Task Planner five jobs — 任务理解、Goal/Subgoal 生成、前置条件与依赖、
长程计划摘要、根据反馈修订剩余计划. This module does the second and third, and it does them
**only** out of records the rest of the system produced for other reasons:

* one ``achieve`` subgoal per :class:`GoalAssignment`, keyed to the *same* predicate id the
  verifier writes (``placed:<entity>:<target>``, ``verify.py:418``), so a subgoal's status and
  a progress row can never disagree by being computed from different questions;
* dependencies found with the placement instrument the executor itself uses
  (:class:`~..core.placement_planner.PlacementPlanner`). A region is **blocked** for an object
  when every candidate the generator offers fails a re-check of the *current* snapshot — that
  is a measurement, not a guess about tray capacity, and the reason strings the check returns
  are copied into the subgoal's ``preconditions`` so a reader can see which fact the plan
  rests on;
* ``maintain`` subgoals from ``TaskInput.declared_constraints``, which are public text and
  arrive with no evidence at all: their status is ``unknown`` forever unless something
  measures them, and §11 counts an unanswered obligation rather than closing it silently
  (§5.3: 恢复义务 must survive being inconvenient).

What is deliberately *not* here: any choice of what to work on next. ``subgoals`` come out
sorted by entity id, which is a stable listing order and not a priority — SPEC 11.1's
``sp11`` pair exists precisely to catch a reader who mistakes one for the other. The ready
set (``task_planner.TaskPlanner.ready``) is published as a *set*; picking from it is the
decision source's job (SPEC-v0.2 §3.4, §5.7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..core.contracts import (
    GoalSpec,
    PlacementCandidate,
    PredicateVerdict,
    TaskInput,
    WorldState,
)
from ..core.v02 import Dependency, DependencyKind, Status, Subgoal, Unknownable


def placement_predicate(entity_id: str, target_id: str) -> str:
    """The verifier's own id for "this object is seated in that region".

    One definition shared with `verify.py`; a plan that renamed the predicate would be a plan
    whose satisfaction nothing can check."""
    return f"placed:{entity_id}:{target_id}"


def clear_predicate(entity_id: str, target_id: str) -> str:
    """The negative twin: "this object is measurably *not* seated in that region".

    A blocker has to leave a tray before anything can be put into it, and that is a goal the
    agent can satisfy and measure — but it is not a `placed:` predicate, so it gets its own id.
    It is *not* answered by negating `placed:` either: an object pinned against the tray wall
    reads `placed: false` and still occupies the descent envelope, so `evidence_for` asks the
    same question the planner asks before proposing a slot — who is `occupants()` of that region
    — and leaves the row unmeasured when the object's own pose is not in the snapshot."""
    return f"clear:{entity_id}:{target_id}"


def row_key(sub: Subgoal) -> str:
    """The identity a plan row carries between rounds.

    A `maintain` row has no predicate — it *is* a sentence — so it is keyed by its text. The
    hash is `sha256`, not `hash()`: Python salts string hashing per process, and a row identity
    that changed between two runs of one snapshot would make the plan's version history
    unreproducible (§12.1).
    """
    if sub.predicate_id:
        return sub.predicate_id
    import hashlib

    return "text:" + hashlib.sha256(sub.statement.encode("utf-8")).hexdigest()[:10]


@dataclass
class Feasibility:
    """What the placement instrument said about one (object, region) pair right now."""

    entity_id: str
    target_id: str
    ok: list[PlacementCandidate] = field(default_factory=list)
    offered: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return bool(self.ok)

    @property
    def first_reason(self) -> str:
        """The first refusal reason the instrument gave, or why nothing was offered at all."""
        for reason in self.reasons:
            if reason:
                return reason
        return ("the generator offered no slot in this region for this object (it does not fit "
                "inside the margin)")


@dataclass
class Derivation:
    subgoals: list[Subgoal]
    dependencies: list[Dependency]
    notes: list[str]


class Instruments:
    """The measurements a plan is allowed to rest on, bundled so every helper takes one.

    `planner=None` is a real state, not a placeholder: an arm without a placement instrument
    (a text backend, a unit test) then authoritatively *has no* blocking evidence, and the
    graph it authors says so in a note instead of inventing a dependency or silently omitting
    one. `feasible()` never raises for it.
    """

    def __init__(self, planner=None, world: Optional[WorldState] = None):
        self.planner = planner
        self.world = world

    @property
    def have_placement(self) -> bool:
        return self.planner is not None and self.world is not None

    def feasible(self, entity_id: str, target_id: str) -> Feasibility:
        if not self.have_placement:
            return Feasibility(entity_id=entity_id, target_id=target_id)
        world = self.world
        cands = self.planner.generate(target_id, entity_id, world, limit=None)
        out = Feasibility(entity_id=entity_id, target_id=target_id, offered=len(cands))
        for cand in cands:
            chk = self.planner.recheck(cand, world)
            if chk.ok:
                out.ok.append(chk.candidate)
            else:
                out.reasons.extend(chk.reasons)
        return out


def feasibility(planner, entity_id: str, target_id: str, world: WorldState) -> Feasibility:
    """Ask the executor's own generator and re-checker about one pair.

    `limit=None` matters: the loop offers a bounded *sample* to the model
    (`Runtime.PER_GOAL_CANDIDATES`), while a plan that concluded "blocked" from three sampled
    slots and a fourth feasible one would be a plan that fabricated an obstacle.
    """
    return Instruments(planner, world).feasible(entity_id, target_id)


def occupants(world: WorldState, target_id: str) -> list[str]:
    """Entities this snapshot says are resting in / inside a region.

    The union of `WorldState.occupancy` (footprint fully inside) and `supported_by`, which is
    how `PlacementPlanner.recheck` itself defines an occupant (`placement_planner.py:205-209`):
    a plan that used a narrower rule would believe a slot was free that the descent envelope
    says is taken.
    """
    inside = {o.entity_id for o in world.occupancy if o.target_id == target_id}
    inside |= {e.entity_id for e in world.entities if e.supported_by == target_id}
    return sorted(inside)


def _achieve(entity_id: str, target_id: str, *, predicate_id: str, statement: str,
             preconditions: Sequence[str] = (), depends_on: Sequence[str] = (),
             kind: str = "achieve") -> Subgoal:
    return Subgoal(
        statement=statement, kind=kind, status=Status.pending,
        target_entity_ids=[entity_id], target_region_id=target_id, predicate_id=predicate_id,
        preconditions=list(preconditions), depends_on=list(depends_on))


def derive(goal: GoalSpec, world: WorldState, task: Optional[TaskInput] = None, *,
           planner=None, progress=None) -> Derivation:
    """Turn one goal and one snapshot into the subgoal/dependency graph of a plan.

    `progress` is the verifier's own list (`RuntimeVerifier.progress` / `SensingVerifier`), and
    it is what binds an attribute reference to an entity id: an assignment whose entity the
    snapshot cannot resolve still gets a subgoal — with `satisfied: unknown` and the binding
    failure in `preconditions` — because dropping it would remove the very obligation the task
    is ambiguous about (SPEC-v0.2 §5.2's plan is a *work object*, not a summary of the easy
    parts).
    """
    from ..core.verify import RuntimeVerifier, bind_attributes

    if not goal.well_formed:
        return Derivation(subgoals=[], dependencies=[],
                          notes=["the goal is not well-formed; no plan was authored for an "
                                 "ambiguous or empty assignment list"])

    items = list(progress if progress is not None else RuntimeVerifier(world).progress(goal))
    verdicts = {str(i.predicate_id or placement_predicate(i.entity_id, i.target_id)): i.value
                for i in items}
    assignment_target = {i.entity_id: i.target_id for i in items if i.entity_id}
    instruments = Instruments(planner, world)

    subgoals: list[Subgoal] = []
    dependencies: list[Dependency] = []
    notes: list[str] = []
    by_key: dict[tuple[str, str], Subgoal] = {}
    # Every assignment is asked for a row, and the binding is resolved here with the *same*
    # attribute-unique rule the verifier uses (`bind_attributes`), so a plan can never be shorter
    # than the task simply because the progress list handed in was empty or refused a clause.
    # What progress supplies and what the assignments supply are then one set, in one order.
    wanted = {(i.entity_id, i.target_id) for i in items if i.entity_id and world.has_entity(
        i.entity_id)}
    for a in goal.assignments:
        eid = a.entity.entity_id or bind_attributes(dict(a.entity.attributes or {}), world)
        if eid and world.has_entity(eid):
            wanted.add((eid, a.target_id))
            assignment_target.setdefault(eid, a.target_id)
    for eid, tid in sorted(wanted):
        pid = placement_predicate(eid, tid)
        sub = _achieve(eid, tid, predicate_id=pid, statement=f"{eid} is seated in {tid}",
                       preconditions=([] if pid in verdicts else
                                      [f"{pid} was never measured in this snapshot"]))
        subgoals.append(sub)
        by_key[(eid, tid)] = sub

    for a in goal.assignments:
        eid = a.entity.entity_id or bind_attributes(dict(a.entity.attributes or {}), world)
        if eid and not world.has_entity(eid):
            sub = _achieve(eid, a.target_id, predicate_id=placement_predicate(eid, a.target_id),
                           statement=f"{eid} is seated in {a.target_id}",
                           preconditions=[f"{eid} is named by the task but is not in this "
                                          f"snapshot: the binding is public, the object is "
                                          f"unmeasured"])
            sub.satisfied = Unknownable()
            subgoals.append(sub)
            by_key[(eid, a.target_id)] = sub
        elif not eid:
            attrs = dict(a.entity.attributes or {})
            matches = [e.entity_id for e in world.entities
                       if attrs and all(e.attributes.get(k) == v for k, v in attrs.items())]
            notes.append(f"the reference {attrs or a.entity.selector or '(empty)'} for "
                         f"{a.target_id} binds to {len(matches)} entities in "
                         f"v{world.state_version}, so no subgoal was authored for it: an "
                         f"unbound clause is reported as a missing obligation, never guessed "
                         f"into one")

    if instruments.have_placement:
        for sub in [s for s in subgoals if s.kind == "achieve" and s.target_region_id]:
            eid, tid = sub.target_entity_ids[0], sub.target_region_id
            if verdicts.get(placement_predicate(eid, tid)) is PredicateVerdict.true:
                continue
            feas = instruments.feasible(eid, tid)
            if feas.feasible:
                continue
            sub.preconditions = list(sub.preconditions) + [
                f"no placement candidate for {eid} in {tid} passes re-check at v"
                f"{world.state_version}: {feas.first_reason}"]
            for blocker in [o for o in occupants(world, tid) if o != eid]:
                dep = _blocker_dependency(instruments, blocker, tid, sub, by_key, subgoals,
                                          assignment_target, notes)
                if dep is not None:
                    dependencies.append(dep)
        dependencies.extend(_capacity_dependencies(instruments, subgoals, verdicts))
    else:
        notes.append("no placement instrument was given, so no blocking or capacity dependency "
                     "is claimed; the graph is the flat assignment list")

    for text in list(task.declared_constraints if task is not None else []):
        subgoals.append(Subgoal(
            statement=text, kind="maintain", status=Status.pending,
            preconditions=["stated by the user and answerable by no instrument this arm has; it "
                           "stays an open obligation until something measures it"]))

    return Derivation(subgoals=subgoals, dependencies=dependencies,
                      notes=list(dict.fromkeys(notes)))


def _blocker_dependency(instruments: Instruments, blocker: str, region: str, blocked: Subgoal,
                        by_key: dict, subgoals: list[Subgoal],
                        assignment_target: dict[str, str],
                        notes: list[str]) -> Optional[Dependency]:
    """One blocked region + one occupant → the dependency that has to be settled first.

    Three shapes, and the difference between them is what §11's `dependency_violations` counts:

    1. the occupant has its own goal in **another** region → that work already has a subgoal,
       so the edge points at it and nothing new is invented;
    2. the occupant's goal is **this same** region → it has to leave and come back. The
       temporary move gets an `achieve` subgoal and the return a **`recover`** one
       (`DependencyKind.recovery`), ordered after the blocked placement — which is exactly the
       obligation a flat progress list cannot express, and the reason the §8 example task says
       任务结束前恢复必须保持的关系;
    3. the occupant is not part of the task at all → "get it out of the region" is still a goal
       the agent must satisfy, so it gets a `clear:` subgoal answered by the *negative* of the
       placement predicate, and no restore obligation is invented for it.
    """
    own = assignment_target.get(blocker)
    if own and own != region:
        prior = by_key.get((blocker, own))
        if prior is not None:
            return Dependency(
                from_subgoal_id=prior.subgoal_id, on_subgoal_id=blocked.subgoal_id,
                kind=DependencyKind.state,
                note=f"{blocked.target_entity_ids[0]} cannot go into {region} while {blocker} is "
                     f"measured inside it; moving {blocker} to {own} is already a goal")
    if own == region:
        # The debt and the goal are the *same* predicate: `blocker` owes a region it already
        # satisfies. Authoring a second row for one fact would put two claims on it, so the
        # existing row is re-labelled `recover` and the temporary move becomes its prerequisite.
        back = by_key.get((blocker, region)) or next(
            (s for s in subgoals if s.predicate_id == placement_predicate(blocker, region)), None)
        temp = _temp_region(instruments, blocker, region, by_key)
        if back is None or temp is None:
            notes.append(f"{blocker} blocks {region} and its own goal is {region}, but no other "
                         f"region can currently receive it: the recovery obligation is recorded "
                         f"as unanswerable rather than dropped")
            blocked.preconditions = list(blocked.preconditions) + [
                f"{blocker} must leave {region} and return, and no region can receive it in this "
                f"snapshot"]
            return None
        away = _achieve(blocker, temp, predicate_id=placement_predicate(blocker, temp),
                        statement=f"{blocker} is seated in {temp}, temporarily, to clear "
                                  f"{region}",
                        preconditions=[f"{blocker} is inside {region}, which has no feasible slot "
                                       f"for another object until it moves"])
        back.kind = "recover"
        back.statement = (f"{back.statement}; {blocker} was moved to {temp} only to clear "
                          f"{region} and must come back")
        subgoals.append(away)
        by_key[(blocker, temp)] = away
        blocked.depends_on = list(blocked.depends_on) + [away.subgoal_id]
        return Dependency(
            from_subgoal_id=back.subgoal_id, on_subgoal_id=blocked.subgoal_id,
            kind=DependencyKind.recovery,
            note=f"{blocker} was displaced out of {region} to let "
                 f"{blocked.target_entity_ids[0]} in and must be restored before the task ends")
    if own is None:
        clear = next((s for s in subgoals
                      if s.predicate_id == clear_predicate(blocker, region)), None)
        if clear is None:
            clear = _achieve(
                blocker, region, predicate_id=clear_predicate(blocker, region),
                statement=f"{blocker} is clear of {region}",
                preconditions=[f"{blocker} is not part of the task, but it occupies volume in "
                               f"{region} in this snapshot"])
            subgoals.append(clear)
        blocked.depends_on = list(blocked.depends_on) + [clear.subgoal_id]
        return Dependency(
            from_subgoal_id=clear.subgoal_id, on_subgoal_id=blocked.subgoal_id,
            kind=DependencyKind.state,
            note=f"{region} has no feasible slot for {blocked.target_entity_ids[0]} while "
                 f"{blocker} is inside it")
    return None


def _temp_region(instruments: Instruments, blocker: str, region: str, by_key: dict):
    """Where a temporarily-displaced object can go, chosen by measurement.

    A region is eligible when (a) it is not the blocked one, (b) some candidate for the blocker
    passes re-check there, and (c) no object already in the plan is destined for it — a temp
    move that fills the tray the task still needs would trade one blocker for another. Ties
    break on the region name so two runs over one snapshot author one plan (SPEC §12.1).
    """
    world = instruments.world
    needed = {s.target_region_id for s in by_key.values() if s.target_region_id}
    for tid in sorted({t.target_id for t in world.targets} - {region} - needed):
        if instruments.feasible(blocker, tid).feasible:
            return tid
    return None


def _capacity_dependencies(instruments: Instruments, subgoals: list[Subgoal],
                           verdicts: dict) -> list[Dependency]:
    """`resource` edges between objects competing for one region.

    Deliberately narrow: an edge appears only when a region's *own* feasible slot count is
    smaller than the number of objects still owed to it, which is the one reading of "the tray
    is the constraint" this framework can measure. No order is implied — the edge is recorded
    once per contested pair, from the alphabetically earlier object to the later one, so the
    graph stays a DAG and the count stays one per pair rather than two.
    """
    pending: dict[str, list[Subgoal]] = {}
    for sub in subgoals:
        if sub.kind != "achieve" or not sub.target_region_id:
            continue
        if not (sub.predicate_id or "").startswith("placed:"):
            # a `clear:` row wants the region *emptied*; counting it as an object owed there
            # would report the opposite of the competition it names
            continue
        eid = sub.target_entity_ids[0]
        if verdicts.get(placement_predicate(eid, sub.target_region_id)) is PredicateVerdict.true:
            continue
        pending.setdefault(sub.target_region_id, []).append(sub)
    out: list[Dependency] = []
    for region, subs in sorted(pending.items()):
        if len(subs) < 2:
            continue
        slots = min(len(instruments.feasible(s.target_entity_ids[0], region).ok) for s in subs)
        if slots >= len(subs):
            continue
        ordered = sorted(subs, key=lambda s: s.target_entity_ids[0])
        first, rest = ordered[0], ordered[1:]
        for other in rest:
            out.append(Dependency(
                from_subgoal_id=first.subgoal_id, on_subgoal_id=other.subgoal_id,
                kind=DependencyKind.resource,
                note=f"{region} offers {slots} feasible slot(s) for {len(subs)} objects still "
                     f"owed there; the region, not an order, is the constraint"))
    return out


__all__ = ["Derivation", "Feasibility", "Instruments", "clear_predicate", "derive",
           "feasibility", "occupants", "placement_predicate", "row_key"]
