"""WorldState, goal and verification for the MuJoCo channel (SPEC-v0.2 §5.1, §10).

The one design choice worth stating up front: this channel *can* verify, and does.
The ALFWorld text backend had to answer `unknown` for the task goal because no public
sentence states that an instruction is satisfied. Here the world reports positions, so
`placed:<object>:<target>` is a measured predicate with a measured tolerance, and the
loop's finish check and the plan's one `achieve` row get a real answer — from the same
id the derivation wrote, which is what lets the working memory discharge it.

That verification is computed from **poses**, never from the benchmark's score. The
official `success` flag is `obj_to_target <= 0.07`, a number owned by the evaluator;
this module's tolerance is two modelled sizes added together — the target site's own
half-size and the object's measured radius — and it is labelled with its own
`tolerance_version`. The two are compared after the episode, never during it — otherwise
the agent's verifier would be the scorer wearing a different hat (§10: 不注入 hidden
state / expert answer).

The `privileged` label is honest, not a fallback: `measured()` reads site poses out of
`data`, which is exactly what the desktop PyBullet channel does and what
`--perceive privileged` means. A camera-only channel is reachable through
`perception/arm.py` and is *not* what this module produces; nothing here pretends a
number came from pixels.
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional, Union

from ..core.contracts import (
    EntityState,
    GeometrySpec,
    GoalProgressItem,
    GoalSpec,
    NotSeen,
    Pose,
    PredicateReport,
    PredicateVerdict,
    Source,
    SupportEvidence,
    TargetRegion,
    TextFact,
    Vec3,
    VerificationReport,
    VerifyConfig,
    WorldState,
)
from ..planning.subgoals import placement_predicate

LOCATION_PREFIX = "bench-mujoco"
#: what this channel calls the robot's own hand, so `held` has a subject to name
HAND_ENTITY = "hand 1"
#: how far above its rest height an object must be to be called carried rather than resting
CARRY_LIFT_M = 0.06
#: the placement tolerance this channel's verifier uses, and its own identity
TOLERANCE_VERSION = "mw-site-and-object-size-v1"


def _identity_quat() -> tuple[float, float, float, float]:
    return (0.0, 0.0, 0.0, 1.0)


def site_size(backend: Any, site_name: str, default: float = 0.03) -> float:
    """The modelled radius of one site — a property of the scene file, not of the score."""
    try:
        import mujoco
        m = backend.env.model
        sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE.value, site_name)
        if sid >= 0:
            return round(float(max(m.site_size[sid][:2])) or default, 4)
    except Exception:  # noqa: BLE001 - a missing site is a fact, not a crash
        pass
    return default


def carried(object_xyz: tuple[float, float, float], hand_xyz: tuple[float, float, float],
            init_z: float) -> Union[bool, str]:
    """`held` from two measured positions. Deliberately not the evaluator's
    `grasp_success`, which also consults an internal aperture."""
    dx = [object_xyz[i] - hand_xyz[i] for i in range(3)]
    gap = math.sqrt(sum(v * v for v in dx))
    if gap > 0.12:
        return False
    return bool(object_xyz[2] - init_z > CARRY_LIFT_M)


def mw_world_state(backend: Any, state_version: int, observation_ref: str, *,
                   init_z: Optional[dict[str, float]] = None) -> WorldState:
    """One measurement of the scene: the poses this instant, and what follows from them.

    Nothing is carried over from a previous snapshot (SPEC-BST 4.2): an object that was
    measured held and is now measured elsewhere is simply measured elsewhere.
    """
    m = backend.measured()
    sites = dict(m["sites"])
    hand = m["hand"]
    init_z = dict(init_z or {})
    entities: list[EntityState] = []
    facts: list[TextFact] = []
    held_object: Union[str, None, Any] = None

    entities.append(EntityState(
        entity_id=HAND_ENTITY, body_id=-1,
        pose=Pose(position=Vec3(x=hand[0], y=hand[1], z=hand[2]),
                  quaternion_xyzw=_identity_quat()),
        orientation_measured=False, geometry=None, held=False,
        support=SupportEvidence(support_id=None, contact_count=0, source=Source.privileged),
        supported_by=None, at_rest="unknown", visible=True,
        attributes={"role": "manipulator", "gripper_aperture_m": str(m["gripper_open_m"])},
        source=Source.privileged))
    facts.append(TextFact(kind="pose", subject=HAND_ENTITY, related="world",
                          state=f"({hand[0]:.3f}, {hand[1]:.3f}, {hand[2]:.3f})",
                          observation_ref=observation_ref,
                          text="hand pose, measured from the simulator state"))

    for eid in sorted(sites):
        xyz = sites[eid]
        is_peg = eid.startswith("peg")
        held = carried(xyz, hand, init_z.get(eid, xyz[2])) if is_peg else False
        if held is True and held_object is None:
            held_object = eid
        extent = backend.leading_tip(eid, hand) if is_peg else None
        entities.append(EntityState(
            entity_id=eid, body_id=-1,
            pose=Pose(position=Vec3(x=xyz[0], y=xyz[1], z=xyz[2]),
                      quaternion_xyzw=_identity_quat()),
            orientation_measured=False,
            geometry=(GeometrySpec(shape="box", half_extents=Vec3(
                x=extent["half_length_m"], y=extent["radius_m"],
                z=extent["radius_m"])) if extent else None),
            held=held,
            # What this channel measures is positions and a gap, never a contact: MuJoCo's
            # contact list is not read here, so `support_id` names the hand only when the
            # measured gap and lift say the object is on it, and everything else is
            # `unknown`. An earlier version wrote "table" for every peg that was not held —
            # a word this same snapshot contradicts the moment the peg is seated, and the
            # working memory reads `supported_by` as an object's home.
            support=SupportEvidence(support_id=("hand" if held else None),
                                    contact_count=0, source=Source.privileged),
            supported_by=("hand" if held else "unknown"),
            at_rest="unknown", visible=True,
            attributes=({"kind": "peg"} if is_peg else {"kind": "socket"}),
            source=Source.privileged))
        facts.append(TextFact(kind="pose", subject=eid, related="world",
                              state=f"({xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f})",
                              observation_ref=observation_ref,
                              text=f"{eid} measured at this position"))
        if extent:
            # The object's own two ends, as measured facts of this snapshot. Without them
            # the verifier could only ever ask where the *named point* of a 0.24 m rod is,
            # and would call a fully inserted peg false because the point it watches is by
            # construction 0.13 m outside the socket (`env.py:leading_tip`).
            for end in extent["ends"]:
                facts.append(TextFact(kind="extent", subject=eid, related="end",
                                      state=f"({end[0]:.3f}, {end[1]:.3f}, {end[2]:.3f})",
                                      observation_ref=observation_ref,
                                      text=f"{eid} is measured to reach ({end[0]:.3f}, "
                                           f"{end[1]:.3f}, {end[2]:.3f})"))
        if is_peg:
            facts.append(TextFact(kind="held", subject=HAND_ENTITY, related=eid,
                                  state=("yes" if held else "no"),
                                  observation_ref=observation_ref,
                                  text=f"{eid} is {'carried' if held else 'not carried'} "
                                       f"by the measured gap and lift"))

    targets = []
    for eid in sorted(sites):
        if not eid.startswith("socket"):
            continue
        xyz = sites[eid]
        targets.append(TargetRegion(target_id=eid, label="the target site of this benchmark task",
                                    center=Vec3(x=xyz[0], y=xyz[1], z=xyz[2]),
                                    inner_half=site_size(backend, "goal"),
                                    floor_top_z=xyz[2], frame_id="world"))

    return WorldState(
        state_version=state_version, sim_time=float(m["sim_time"]), wall_time=time.time(),
        entities=entities, targets=targets, held_object=held_object,
        observation_ref=observation_ref, occupancy=[], source=Source.privileged,
        raw_observation=backend.public_text(), unparsed=[], facts=facts,
        location=f"{LOCATION_PREFIX}/{backend.env_name}", parse_status="parsed")


def fact_key(f: TextFact) -> str:
    return f"{f.kind}:{f.subject}->{f.related}={f.state}"


def mw_fingerprint(world: WorldState) -> tuple:
    """Positions rounded to the millimetre, with no ref and no version attached: two
    actions taken under the same fingerprint got no new information."""
    return (str(world.location), str(world.held_object),
            tuple(sorted((e.entity_id,
                          tuple(round(v, 3) for v in (e.pose.position.x, e.pose.position.y,
                                                       e.pose.position.z)) if e.pose else None,
                          str(e.held)) for e in world.entities)),
            tuple(sorted(fact_key(f) for f in world.facts)))


def mw_state_diff(pre: WorldState, post: WorldState) -> dict:
    def pos(w: WorldState, eid: str) -> Optional[tuple]:
        e = next((e for e in w.entities if e.entity_id == eid), None)
        if e is None or e.pose is None:
            return None
        return (e.pose.position.x, e.pose.position.y, e.pose.position.z)

    ids = sorted({e.entity_id for e in pre.entities} | {e.entity_id for e in post.entities})
    moved, changed = [], []
    for eid in ids:
        a, b = pos(pre, eid), pos(post, eid)
        if a and b:
            d = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))
            if d > 1e-3:
                moved.append({"entity_id": eid, "displacement_m": round(d, 4),
                              "from": [round(v, 4) for v in a], "to": [round(v, 4) for v in b]})
        ea = next((e for e in pre.entities if e.entity_id == eid), None)
        eb = next((e for e in post.entities if e.entity_id == eid), None)
        if ea and eb and str(ea.held) != str(eb.held):
            changed.append({"entity_id": eid, "held_before": str(ea.held),
                            "held_after": str(eb.held)})
    return {"moved": moved, "state_changed": changed,
            "held_before": str(pre.held_object), "held_after": str(post.held_object),
            "location_before": str(pre.location), "location_after": str(post.location),
            "note": "a displacement here is two measured poses of the same named entity; "
                    "nothing in it came from the benchmark's score"}


# ---------------------------------------------------------------------- goal -----


class MujocoGoalSpec(GoalSpec):
    """A desktop-shaped goal: entity/target pairs, so `well_formed` and the frozen
    placement machinery mean what they already mean."""

    instruction: str = ""


def goal_from_assignment(object_id: str, target_id: str, instruction: str, *,
                         state_version: int) -> MujocoGoalSpec:
    from ..core.contracts import EntityRef, GoalAssignment
    return MujocoGoalSpec(
        instruction=instruction,
        assignments=[GoalAssignment(entity=EntityRef(entity_id=object_id), target_id=target_id)],
        based_on_state_version=state_version)


# ---------------------------------------------------------------- verification --


def parse_vec(state: str) -> Optional[tuple[float, float, float]]:
    """Read one measured `(x, y, z)` string back out of a fact.

    The fact is the record and the number is its content: the verifier is allowed to
    use what a snapshot stated, in the same form the log shows it."""
    try:
        parts = str(state).strip().lstrip("(").rstrip(")").split(",")
        if len(parts) != 3:
            return None
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return None


def _point_segment_distance_m(p: tuple[float, float, float],
                              a: tuple[float, float, float],
                              b: tuple[float, float, float]) -> float:
    """Distance from a point to the measured long axis of an object: the standard
    nearest-point-on-a-segment test, so a target lying *between* the two ends of a rod
    is 0 m from it and a target beside its middle is one perpendicular away."""
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    len2 = sum(v * v for v in ab)
    t = 0.0 if len2 == 0.0 else max(0.0, min(1.0, sum(ap[i] * ab[i] for i in range(3)) / len2))
    d = [ap[i] - t * ab[i] for i in range(3)]
    return math.sqrt(sum(v * v for v in d))


def ends_of(world: WorldState, eid: str) -> list[tuple[float, float, float]]:
    """The two measured ends of this entity, from the extent facts of this snapshot."""
    return [v for f in world.facts if f.kind == "extent" and f.subject == eid
            and (v := parse_vec(f.state)) is not None]


def distance_m(a: WorldState, eid: str, tid: str) -> Optional[float]:
    """How far this entity is from the target, measured to the part of it that can reach.

    An entity with a measured extent is judged on its own axis, because that is the
    claim "the peg is in the socket" makes: the named point a text would use for it sits
    on the shaft, 0.13 m outside the hole even when the peg is fully seated. An entity
    with no extent facts falls back to the distance between the two measured points.
    """
    pb = a.target(tid)
    e = next((e for e in a.entities if e.entity_id == eid), None)
    if pb is None or e is None or e.pose is None:
        return None
    p = (pb.center.x, pb.center.y, pb.center.z)
    ends = ends_of(a, eid)
    if len(ends) == 2:
        return round(_point_segment_distance_m(p, ends[0], ends[1]), 4)
    return round(math.sqrt((e.pose.position.x - p[0]) ** 2 + (e.pose.position.y - p[1]) ** 2
                           + (e.pose.position.z - p[2]) ** 2), 4)


def unmeasured_distance(world: WorldState, eid: str, tid: str) -> str:
    """Which of `distance_m`'s three tests this snapshot fails, in the words the model acts on.

    Called only where `distance_m` returned None, so the three cases are exhaustive: no region
    under that id, no body under that name, or a body with no measured position. The caller used
    to fold all three into "X or Y is not in this snapshot", and on the camera channel that claim
    is false in every recorded case: over the 64 looks of the camera episodes that scored, the rod
    blob is present and `socket 1` is a declared region in all of them (`/tmp/mw102/branch103.py`),
    while the look after each of the 10 places in them gives the rod no position at all
    (`/tmp/mw102/pos112.py`). That is the reading that matters, and this channel has exactly two
    sources for it: `state_attributes.position_xyz_m` on the detection, which
    `perception/world_state.py:149,191` turns into `EntityState.pose`, and the fitted axis midpoint
    that `benchmark_mujoco/perceive.py:2022-2044` then overwrites it with. `detections[].pose` is
    neither of them and is null in every camera detection ever filed — reading it instead of these
    two is how this file was nearly "refuted" once. The sentence is the advice the model is handed
    with the `unknown`, and "reconcile the names" is a different act from "look again from a side
    that can see under its feet". `perception/verify_percept.py:verify_placement` has named these
    three apart since it was written; this module is the channel's other verifier catching up.
    """
    if world.target(tid) is None:
        return f"{tid} is not a region in this snapshot"
    if not world.has_entity(eid):
        return f"{eid} is not a body in this snapshot"
    return (f"{eid} and {tid} are both in this snapshot; what is missing is a position: {eid} "
            f"was reported without measured metres, so there is no distance to {tid} to compare")


class MujocoVerifier:
    """`progress` / `verify_goals` / `verify_action` / `evidence_for`, over measured poses.

    Duck-typed the way `TextVerifier` is; the desktop `RuntimeVerifier` is not reused
    because it reasons about trays, slots and footprints this world does not have."""

    def __init__(self, world: WorldState, config: Optional[VerifyConfig] = None):
        self.world = world
        self.config = config or VerifyConfig()

    def _report(self, pid: str, description: str, value: PredicateVerdict, *,
                evidence: Optional[dict[str, float]] = None,
                unmeasured: Optional[list[str]] = None) -> PredicateReport:
        return PredicateReport(
            predicate_id=pid, description=description, value=value,
            evidence=dict(evidence or {}),
            evidence_refs=[self.world.observation_ref] if self.world.observation_ref else [],
            unmeasured=list(unmeasured or []), source=Source.privileged)

    def progress(self, goal: GoalSpec) -> list[GoalProgressItem]:
        out = []
        for a in list(getattr(goal, "assignments", []) or []):
            eid, tid = a.entity.entity_id, a.target_id
            d = distance_m(self.world, eid, tid)
            tol = self._tolerance(eid, tid)
            value = (PredicateVerdict.unknown if d is None
                     else PredicateVerdict.true if d <= tol else PredicateVerdict.false)
            out.append(GoalProgressItem(
                entity_id=eid, target_id=tid, value=value,
                evidence_refs=[self.world.observation_ref] if self.world.observation_ref else [],
                unmeasured=[] if d is not None else [unmeasured_distance(self.world, eid, tid)],
                predicate_id=self.predicate_id(eid, tid)))
        return out

    @staticmethod
    def predicate_id(eid: str, tid: str) -> str:
        """The shared placement id, not a channel-private one.

        `planning/subgoals.py:placement_predicate` is the id the derivation writes into a
        plan row, so a channel that answers a different string has an instrument that can
        never be asked. An earlier version of this method returned `in2:<eid>:<tid>` — the
        *text* channel's counting vocabulary — and every `full` episode here reported its
        one `achieve` row as owed forever, which reads as an arm failure and is a naming
        mistake."""
        return placement_predicate(eid, tid)

    def _tolerance(self, eid: str, tid: str) -> float:
        """How far the object's measured axis may sit from the target point and still
        occupy it: the target's own modelled half-size plus the object's modelled radius.

        Two different measured facts are added because the test is about a *body* covering
        a *region* — `distance_m` measures to the axis down the middle of the object, so a
        point anywhere inside the object's surface is one radius away from what the test
        looked at. Neither number is the benchmark's: the official test compares the object
        to the target at 0.07 m, which this module never reads (§10).
        """
        t = self.world.target(tid)
        if t is None:
            return float("inf")
        half = float(t.inner_half)
        e = next((e for e in self.world.entities if e.entity_id == eid), None)
        radius = 0.0
        if (e is not None and e.geometry is not None and e.geometry.half_extents is not None):
            g = e.geometry.half_extents
            radius = float(min(g.y, g.z))
        return round(half + radius, 4)

    def _seated(self, eid: str, tid: str, d: Optional[float]) -> str:
        """The claim in the id, spelled out as the test that was actually run. The
        desktop's `placed:` row answers a four-part conjunction (footprint inside, resting
        on the floor, at rest, not held); this channel's instrument answers the first part
        and only the first part, from two measured poses and two modelled sizes, so the
        description says that rather than borrowing the longer sentence.

        When the test could not be run at all the sentence is the *test*, not a claim about it.
        Both doors send it: `verify_goals` onto the decision page and `verify_action` onto
        `execution_feedback`. An "X is seated in Y" lead clause reads as a verdict; the tolerance is left out of that
        branch because `self._tolerance` answers `inf` when no region carries the name, and
        "within a modelled tolerance" is what survives both kinds of absence.
        """
        tol = self._tolerance(eid, tid)
        if d is None:
            return (f"whether {eid} is seated in {tid} was not tested here: the test is that "
                    f"{tid} lie inside the body of {eid} within a modelled tolerance, and this "
                    f"snapshot has no distance to compare")
        return (f"{eid} is seated in {tid}: the measured point {tid} lies inside the body "
                f"of {eid} to within {tol:.3f} m (measured {d:.4f} m)")

    def verify_goals(self, goal: GoalSpec) -> VerificationReport:
        reports = []
        for item in self.progress(goal):
            d = distance_m(self.world, item.entity_id, item.target_id)
            reports.append(self._report(
                item.predicate_id,
                self._seated(item.entity_id, item.target_id, d), item.value,
                evidence=({"distance_m": d} if d is not None else {}),
                unmeasured=list(item.unmeasured)))
        return VerificationReport(reports=reports, source=Source.privileged,
                                  tolerance_version=TOLERANCE_VERSION,
                                  world_observation_ref=self.world.observation_ref,
                                  state_version=self.world.state_version)

    def verify_action(self, skill: str, args: dict[str, str]) -> list[PredicateReport]:
        if skill == "pick":
            eid = args.get("object_id") or ""
            e = next((e for e in self.world.entities if e.entity_id == eid), None)
            if e is None:
                return [self._report(f"held:{eid}", f"{eid} is carried", PredicateVerdict.unknown,
                                     unmeasured=["the object is not in this snapshot"])]
            return [self._report(f"held:{eid}", f"{eid} is carried",
                                 PredicateVerdict.true if e.held is True else
                                 (PredicateVerdict.false if e.held is False
                                  else PredicateVerdict.unknown),
                                 evidence={"held_measured": 1.0 if e.held is True else 0.0})]
        if skill == "place":
            eid, tid = args.get("object_id") or "", args.get("target_id") or ""
            d = distance_m(self.world, eid, tid)
            if d is None:
                return [self._report(self.predicate_id(eid, tid), self._seated(eid, tid, None),
                                     PredicateVerdict.unknown,
                                     unmeasured=[unmeasured_distance(self.world, eid, tid)])]
            tol = self._tolerance(eid, tid)
            return [self._report(self.predicate_id(eid, tid), self._seated(eid, tid, d),
                                 PredicateVerdict.true if d <= tol else PredicateVerdict.false,
                                 evidence={"distance_m": d, "tolerance_m": tol})]
        if skill == "safe_retreat":
            return [self._report("retreat:hand", "the hand is at a retreat pose",
                                 PredicateVerdict.true,
                                 evidence={"retreat_issued": 1.0})]
        return [self._report(f"observe:{self.world.observation_ref}",
                             "a fresh measurement was taken", PredicateVerdict.true,
                             evidence={"state_version": float(self.world.state_version)})]

    def evidence_for(self, sub: Any, world: WorldState):
        """(verdict, refs, unmeasured) for one plan row, or None when this snapshot has no
        answer. Same contract as the text channel's `obligations.evidence_for`, so
        `PlanningMixin` sees one instrument per channel.

        The row's own fields are what the question is asked with: a `Subgoal` names its
        participants `target_entity_ids` / `target_region_id`, and an earlier version of
        this method read `object_id` / `target_id` — attributes no `Subgoal` has — so it
        returned None for every row the arm ever asked about and the plan's satisfaction
        was unmeasurable by construction. Only `placed:` rows are answered: a `maintain`
        row is a sentence, not a geometry, and this channel has no instrument for it.
        """
        pid = getattr(sub, "predicate_id", "") or ""
        eids = list(getattr(sub, "target_entity_ids", None) or [])
        tid = getattr(sub, "target_region_id", None) or ""
        if not pid.startswith("placed:") or not eids or not tid:
            return None
        eid = eids[0]
        if not world.has_entity(eid) or world.target(tid) is None:
            return None
        d = distance_m(world, eid, tid)
        if d is None:
            return None
        return (PredicateVerdict.true if d <= self._tolerance(eid, tid)
                else PredicateVerdict.false,
                [world.observation_ref] if world.observation_ref else [], [])


__all__ = ["MujocoVerifier", "MujocoGoalSpec", "mw_world_state", "mw_fingerprint",
           "mw_state_diff", "goal_from_assignment", "distance_m", "carried",
           "HAND_ENTITY", "TOLERANCE_VERSION", "fact_key"]
