"""Observation channel and agent-side verification (SPEC 5.1, 7).

Two responsibilities, deliberately kept apart from scoring:

* `build_world_state` is the *measurement* step: it reads physics once and
  produces one versioned `WorldState` snapshot. Support, hold and rest facts
  come from contact and velocity measurements; anything not measured this
  snapshot stays `unknown`.
* `RuntimeVerifier` is a *pure* step over that snapshot: it answers the agent's
  own GoalSpec with public `VerifyConfig` parameters and never re-reads the
  simulator. A verdict therefore always names the observation it came from, and
  a state that changed after the snapshot cannot leak into it (SPEC 5.1).

`RuntimeVerifier` takes no `EvalSpec`, so hidden scoring truth cannot reach
feedback or action selection (SPEC 4, 12.1.1). The geometry core is shared with
the evaluator, whose independence is one of *information source*, not of code.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .contracts import (
    EntityState,
    GeometrySpec,
    GoalProgressItem,
    GoalSpec,
    OccupancyRecord,
    Pose,
    PredicateReport,
    PredicateVerdict,
    Source,
    SupportEvidence,
    TargetRegion,
    Unknown,
    VerificationReport,
    VerifyConfig,
    Vec3,
    WorldState,
    footprint_half_xy_from_quat,
    orientation_invariant_footprint_half_xy,
    rotation_rows_from_quat_xyzw,
    vertical_half_extent,
)
from .scene import PhysicsScene


def _verdict_from(ok: bool | None) -> PredicateVerdict:
    if ok is None:
        return PredicateVerdict.unknown
    return PredicateVerdict.true if ok else PredicateVerdict.false


def yaw_from_quat_xyzw(q) -> float:
    """Heading of the body's own x axis projected on the ground plane.

    The old `2*atan2(w, z)` form is exact only for a yaw-only quaternion; a body
    that tipped over has a pitch or roll, and every footprint derived from it was
    then measured along the wrong axes (SPEC 8: geometry truth)."""
    rows = rotation_rows_from_quat_xyzw(q)
    return float(math.atan2(rows[1][0], rows[0][0]))


# ---------- pure geometry core, shared with the evaluator ----------


def footprint_inside_region(center_xy, half_xy, region: TargetRegion, margin: float) -> tuple[bool, dict]:
    """The *whole footprint* in its measured orientation must clear the inner wall
    by `margin` (SPEC 8 verify: corrected footprint evidence). A centre point
    inside the tray is not enough: an overhanging box still touches the table, not
    the tray floor."""
    dx = float(center_xy[0]) - region.center.x
    dy = float(center_xy[1]) - region.center.y
    limit_x = region.inner_half - margin
    limit_y = region.inner_half - margin
    inside = bool(abs(dx) + half_xy[0] <= limit_x and abs(dy) + half_xy[1] <= limit_y)
    return inside, {
        "xy_err_x": abs(dx), "xy_err_y": abs(dy),
        "footprint_half_x": float(half_xy[0]), "footprint_half_y": float(half_xy[1]),
        "allow_x": limit_x, "allow_y": limit_y,
    }


def rest_z_for(geom: GeometrySpec, region: TargetRegion) -> float:
    """The *nominal* seated height a plan may claim: upright, on the region floor.

    A candidate is generated before the object is in the gripper's hands at that
    slot, so this is a promise about the intended outcome, not a measurement.
    Verification uses `seated_rest_z`, which reads the orientation actually
    measured (SPEC 5.1)."""
    return float(region.floor_top_z + geom.half_h_vertical)


def seated_rest_z(geom: GeometrySpec, region: TargetRegion, quaternion_xyzw) -> float:
    """Height of the body centre when it is at rest on this region's floor in the
    orientation it was actually measured in."""
    return float(region.floor_top_z + vertical_half_extent(geom, quaternion_xyzw))


def entity_footprint_half_xy(entity: EntityState) -> tuple[float, float]:
    """Horizontal half-extents to check this entity against, honouring what the
    snapshot actually measured.

    With an unmeasured orientation there is no heading to multiply the geometry by, and
    the upright pair is the optimistic answer — the one that lets a box standing on a
    corner read as inside a tray wall it crosses. So such an entity gets the bound that
    is true at any orientation, and a placement verdict can only be reached *despite*
    the missing fact, never because of it."""
    if entity.geometry is None:
        raise ValueError(f"entity {entity.entity_id} has no geometry in this snapshot")
    if not entity.orientation_measured:
        return orientation_invariant_footprint_half_xy(entity.geometry)
    if entity.pose is None:
        raise ValueError(f"entity {entity.entity_id} claims a measured orientation with no pose")
    return footprint_half_xy_from_quat(entity.geometry, entity.pose.quaternion_xyzw)


def entity_position(entity: EntityState) -> np.ndarray:
    if entity.pose is None:
        raise ValueError(f"entity {entity.entity_id} has no measured position in this snapshot")
    return np.array([entity.pose.position.x, entity.pose.position.y, entity.pose.position.z])


# ---------- measurement (physics -> snapshot) ----------


def object_in_region(scene: PhysicsScene, eid: str, region: TargetRegion, margin: float) -> tuple[bool, dict]:
    """Measurement twin of the pure predicate: same geometry, live simulator."""
    pos, orn = scene.object_pose(eid)
    half_xy = footprint_half_xy_from_quat(scene.geometry(eid), tuple(orn))
    return footprint_inside_region(pos[:2], half_xy, region, margin)


def object_supported(scene: PhysicsScene, eid: str, region: TargetRegion, tol: float) -> tuple[bool, dict]:
    """Resting on this region's floor, at the height its measured orientation
    implies, and in contact with it."""
    pos, orn = scene.object_pose(eid)
    rest = seated_rest_z(scene.geometry(eid), region, tuple(orn))
    height_err = float(abs(pos[2] - rest))
    touching_tray = region.target_id in scene.contact_partners(eid)
    return bool(height_err < tol and touching_tray), {
        "height_err_m": height_err, "tray_contacts": float(touching_tray), "rest_z": rest,
    }


def build_world_state(scene: PhysicsScene, state_version: int, obs_ref: str,
                      config: VerifyConfig | None = None) -> WorldState:
    """Privileged observation channel. The version and ref are assigned by the
    runtime's monotonic snapshot ledger, never by the caller's guesswork.

    Every entity field is measured exactly once here; nothing downstream
    re-reads the simulator, so a snapshot cannot describe a different instant
    than the one it claims."""
    config = config or VerifyConfig()
    held_bodies = scene.held_bodies()
    held_ids = sorted({scene.entity_by_body(b) for b in held_bodies} - {None})
    unbound_bodies = {b for b in held_bodies if scene.entity_by_body(b) is None}
    # an empty contact set is a *measurement* ("the gripper holds nothing"), not a
    # missing one; only two objects at once, or a body that cannot be named, is
    # ambiguous. Treating "empty" as unknown marked every resting object
    # unmeasured, so no placement could ever verify and the loop never terminated.
    held_known = len(held_ids) <= 1 and not unbound_bodies
    targets = [
        TargetRegion(
            target_id=t["target_id"], label=t["target_id"],
            center=Vec3(x=t["center"][0], y=t["center"][1], z=t["floor_top"]),
            inner_half=t["inner_half"], floor_top_z=t["floor_top"], wall_top_z=t["wall_top"],
        )
        for t in scene.trays.values()
    ]
    entities = []
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        lin, ang = scene.object_velocity(eid)
        geom: GeometrySpec = d["geometry"]
        partners = scene.contact_partners(eid)
        is_held: bool | Unknown = ("unknown" if not held_known else (eid in held_ids))
        if not held_known:
            support = SupportEvidence(support_id="unknown", contact_count=len(partners))
        elif is_held is True:
            support = SupportEvidence(support_id=None, contact_count=len(partners))
        elif partners:
            support = SupportEvidence(
                support_id=partners[0] if len(partners) == 1 else "+".join(partners),
                contact_count=len(partners))
        else:
            support = SupportEvidence(support_id=None, contact_count=0)
        speed, ang_speed = float(np.linalg.norm(lin)), float(np.linalg.norm(ang))
        at_rest: bool | Unknown = (
            "unknown" if not held_known
            else (False if is_held is True else bool(speed < config.speed_tol_mps
                                                     and ang_speed < config.ang_speed_tol_rps)))
        entities.append(
            EntityState(
                entity_id=eid,
                body_id=int(d["body"]),
                pose=Pose(
                    position=Vec3(x=float(pos[0]), y=float(pos[1]), z=float(pos[2])),
                    quaternion_xyzw=(float(orn[0]), float(orn[1]), float(orn[2]), float(orn[3])),
                    frame_id="world",
                ),
                geometry=geom,
                held=is_held,
                support=support,
                supported_by=support.support_id,
                at_rest=at_rest,
                linear_speed_mps=speed,
                angular_speed_rps=ang_speed,
                visible=True,
                attributes=dict(d["attributes"]),
                source=Source.privileged,
            )
        )
    return WorldState(
        state_version=state_version,
        sim_time=scene.sim_time,
        wall_time=time.time(),
        entities=entities,
        targets=targets,
        held_object=(held_ids[0] if held_ids else (
            "unknown" if held_bodies else None)),
        observation_ref=obs_ref,
        occupancy=measure_occupancy(entities, targets, config),
        source=Source.privileged,
    )


def measure_occupancy(entities: list[EntityState], targets: list[TargetRegion],
                      config: VerifyConfig | None = None) -> list[OccupancyRecord]:
    """Which entities measurably rest on which target, derived from the same
    snapshot fields the model sees. Occupancy is evidence, not a reservation
    (SPEC 5.1: slot reservations are not world facts)."""
    config = config or VerifyConfig()
    by_target = {t.target_id: t for t in targets}
    records = []
    for e in entities:
        partner_ids = [] if e.supported_by in (None, "unknown") else str(e.supported_by).split("+")
        for pid in partner_ids:
            region = by_target.get(pid)
            if region is None:
                continue
            if e.pose is None:
                # a reader can name a surface it was told about without measuring a
                # position for the body on it; an occupancy row is a statement about where
                # something is, so there is nothing to record and no row is the answer
                continue
            # relaxed boundary: an object jammed against a wall still occupies
            # the region it physically blocks
            inside, _ = footprint_inside_region(
                entity_position(e), entity_footprint_half_xy(e), region, -config.footprint_margin_m)
            half = entity_footprint_half_xy(e)
            records.append(
                OccupancyRecord(
                    target_id=region.target_id, entity_id=e.entity_id,
                    rest_xy=(float(e.pose.position.x), float(e.pose.position.y)),
                    footprint_half_xy=(float(half[0]), float(half[1])),
                    fully_inside=bool(inside),
                )
            )
    return records


# ---------- pure predicates (snapshot -> verdict) ----------


def state_inside(entity: EntityState, region: TargetRegion, margin: float) -> tuple[bool, dict]:
    return footprint_inside_region(entity_position(entity), entity_footprint_half_xy(entity), region, margin)


def state_supported_on(entity: EntityState, region: TargetRegion, tol: float) -> tuple[bool | None, dict]:
    """Seated on this region's floor at the height the *measured* orientation
    implies. `rest_z` is that seating height; `rest_z_nominal` is the upright
    claim a plan made, kept in the evidence so a tipped-over object is legible."""
    rest = seated_rest_z(entity.geometry, region, entity.pose.quaternion_xyzw)
    height_err = float(abs(entity.pose.position.z - rest))
    if entity.supported_by == "unknown":
        # no contact was measured this snapshot: the sub-fact is *absent*, not 0.0
        return None, {"height_err_m": height_err, "rest_z": rest,
                      "rest_z_nominal": rest_z_for(entity.geometry, region)}
    touching = entity.supported_by == region.target_id or region.target_id in str(entity.supported_by).split("+")
    return bool(height_err < tol and touching), {
        "height_err_m": height_err, "tray_contacts": float(touching), "rest_z": rest,
        "rest_z_nominal": rest_z_for(entity.geometry, region),
    }


def state_at_rest(entity: EntityState, config: VerifyConfig) -> tuple[bool | None, dict]:
    ev = {"rest_z_measured": float(entity.pose.position.z)}
    if entity.linear_speed_mps is not None:
        ev["speed_mps"] = float(entity.linear_speed_mps)
    if entity.angular_speed_rps is not None:
        ev["ang_speed_rps"] = float(entity.angular_speed_rps)
    if entity.at_rest == "unknown":
        return None, ev
    return bool(entity.at_rest), ev


def state_not_held(entity: EntityState) -> tuple[bool | None, dict]:
    if entity.held == "unknown":
        return None, {}
    return bool(not entity.held), {"in_gripper_contact": float(bool(entity.held))}


# ---------- agent-side verification ----------


class RuntimeVerifier:
    """Answers the agent's GoalSpec from one snapshot with public parameters.

    `unknown` is a real answer: an unmeasurable entity, an absent target or an
    unconfirmed hold state report unknown instead of true or false (SPEC 5.1,
    6.1). The verifier never steps the simulator and never sees scoring truth.

    `source` names the channel the snapshot came from, and every report this object
    produces carries it. It is a constructor argument rather than a literal because a
    percept-derived snapshot is not privileged evidence — and a report that says
    otherwise is the one mistake §5.8's three-layer separation exists to prevent.
    `perception.verify_percept.PerceptVerifier` is the channel that passes something
    else."""

    def __init__(self, world: WorldState, config: VerifyConfig | None = None, *,
                 source: Source = Source.privileged):
        self.world = world
        self.config = config or VerifyConfig()
        self.source = source

    def _unknown(self, predicate_id: str, why: str,
                 unmeasured: tuple[str, ...] = ()) -> PredicateReport:
        return PredicateReport(
            predicate_id=predicate_id, description=why, value=PredicateVerdict.unknown,
            evidence={"measurable": 0.0}, evidence_refs=[self.world.observation_ref or ""],
            unmeasured=list(unmeasured) or ["measurement"],
            source=self.source,
        )

    def outcome_status(self, result, reports: list[PredicateReport]):
        """What the loop is told the action *achieved*, given what it could verify.

        Layer 1 (`result.status`) says the actuation finished; layer 2 (these reports)
        says whether current evidence supports the postcondition. The base class
        declines to second-guess layer 1 and returns None: a privileged snapshot
        measures the predicate directly, so a `completed` here is a claim the evidence
        does support, and v0.1's sealed status vocabulary stays exactly as it was.
        A sensor channel that cannot measure the postcondition overrides this —
        `uncertain` is produced where the inability to verify actually lives."""
        return None

    # -- single predicates --
    def verify_grasp(self, eid: str, before: WorldState | None = None,
                     support_z_before: float | None = None) -> PredicateReport:
        """Lift above the previous support plus confirmed contact and hold."""
        if not self.world.has_entity(eid):
            return self._unknown(f"grasp:{eid}", "entity not in observation", ("entity",))
        entity = self.world.entity(eid)
        if entity.pose is None:
            return self._unknown(f"grasp:{eid}", "this snapshot has no measured position for it",
                                 ("position",))
        z = float(entity.pose.position.z)
        if support_z_before is None:
            if before is not None and before.has_entity(eid) and before.entity(eid).pose is not None:
                support_z_before = float(before.entity(eid).pose.position.z)
            else:
                return self._unknown(f"grasp:{eid}", "no earlier snapshot: lift gain is unmeasurable",
                                     ("lift_gain",))
        lift = z - float(support_z_before)
        hold_unmeasured = entity.held == "unknown" or self.world.held_object == "unknown"
        parts = {
            "lift_gain_m": round(lift, 4),
            "lift_gain_min_m": self.config.lift_gain_min_m,
            "support_z_before_m": round(float(support_z_before), 4),
        }
        if not hold_unmeasured:
            parts["in_contact"] = float(entity.held is True)
            parts["held_object_is_entity"] = float(self.world.held_object == eid)
        if hold_unmeasured:
            return PredicateReport(
                predicate_id=f"grasp:{eid}", description="hold state unmeasured at this snapshot",
                value=PredicateVerdict.unknown, evidence=parts, unmeasured=["hold_state"],
                evidence_refs=[self.world.observation_ref or ""], source=self.source)
        ok = bool(lift > self.config.lift_gain_min_m and entity.held is True
                  and self.world.held_object == eid)
        return PredicateReport(
            predicate_id=f"grasp:{eid}",
            description="object lifted above its previous support and confirmed held",
            value=_verdict_from(ok), evidence=parts,
            evidence_refs=[self.world.observation_ref or ""], source=self.source,
        )

    def verify_placement(self, eid: str, target_id: str) -> PredicateReport:
        region = self.world.target(target_id)
        if region is None:
            return self._unknown(f"placed:{eid}:{target_id}", "target not present in observation",
                                 ("target",))
        if not self.world.has_entity(eid):
            return self._unknown(f"placed:{eid}:{target_id}", "entity not present in observation",
                                 ("entity",))
        entity = self.world.entity(eid)
        if entity.pose is None:
            # a body this snapshot located nowhere can be neither inside nor outside; the
            # geometric sub-facts below would raise, and raising is not an answer either
            return self._unknown(f"placed:{eid}:{target_id}", "no measured position in this snapshot",
                                 ("position",))
        subs = {"inside": state_inside(entity, region, self.config.footprint_margin_m),
                "supported": state_supported_on(entity, region, self.config.support_height_tol_m),
                "not_held": state_not_held(entity),
                "at_rest": state_at_rest(entity, self.config)}
        evidence = {k: v for _, (_, ev) in subs.items() for k, v in ev.items()}
        evidence["rest_z_expected"] = subs["supported"][1].get("rest_z_nominal", 0.0)
        unmeasured = [name for name, (sub_value, _) in subs.items() if sub_value is None]
        if unmeasured:
            value = PredicateVerdict.unknown
            evidence["measurable"] = 0.0
        else:
            value = _verdict_from(bool(subs["inside"][0] and all(v is True for v, _ in subs.values())))
        return PredicateReport(
            predicate_id=f"placed:{eid}:{target_id}",
            description="whole footprint inside target, resting on its floor, at rest, not held",
            value=value, evidence=evidence, unmeasured=unmeasured,
            evidence_refs=[self.world.observation_ref or ""], source=self.source,
        )

    # -- goal progress --
    def bind(self, assignment) -> str | None:
        return assignment.entity.entity_id or bind_attributes(assignment.entity.attributes, self.world)

    def progress(self, goal: GoalSpec) -> list[GoalProgressItem]:
        items = []
        for a in goal.assignments:
            eid = self.bind(a)
            if eid is None:
                items.append(GoalProgressItem(
                    entity_id=str(a.entity.attributes or a.entity.entity_id), target_id=a.target_id,
                    value=PredicateVerdict.unknown,
                    evidence={"entity_bound": 0.0},
                    evidence_refs=[self.world.observation_ref or ""],
                    unmeasured=["entity_binding"],
                    predicate_id=f"placed:?{a.entity.entity_id or a.entity.attributes}:{a.target_id}",
                ))
                continue
            rep = self.verify_placement(eid, a.target_id)
            items.append(GoalProgressItem(
                entity_id=eid, target_id=a.target_id, value=rep.value,
                evidence=rep.evidence, evidence_refs=rep.evidence_refs,
                unmeasured=list(rep.unmeasured), predicate_id=rep.predicate_id,
            ))
        return items

    def verify_goals(self, goal: GoalSpec) -> "VerificationReport":
        from .contracts import VerificationReport

        items = self.progress(goal)
        return VerificationReport(
            reports=[
                PredicateReport(predicate_id=i.predicate_id, description="goal assignment predicate",
                                value=i.value, evidence=i.evidence, evidence_refs=i.evidence_refs,
                                unmeasured=list(i.unmeasured),
                                source=self.source)
                for i in items
            ],
            source=self.source, tolerance_version=self.config.tolerance_version,
            world_observation_ref=self.world.observation_ref, state_version=self.world.state_version,
        )


def bind_attributes(attrs: dict[str, str], world: WorldState) -> str | None:
    if not attrs:
        return None
    matches = [e.entity_id for e in world.entities if all(e.attributes.get(k) == v for k, v in attrs.items())]
    return matches[0] if len(matches) == 1 else None


def diff_states(before: WorldState, after: WorldState, move_tol: float = 0.01) -> dict:
    """What actually changed between two snapshots, expressed per entity.
    Version numbers alone are not evidence of a physical change (SPEC 5.1).

    A body one of the two snapshots located nowhere is reported in `position_unmeasured`
    and is left out of `moved`: "I cannot tell whether it moved" and "it did not move" are
    different answers, and the second one is what a missing row would be read as by
    anything that counts `moved` alone. The non-geometric sub-facts are still compared,
    because a hold or support change needs no metres to be a change."""
    moved, changed, unlocated = [], [], []
    for e in after.entities:
        try:
            b = before.entity(e.entity_id)
        except KeyError:
            changed.append({"entity_id": e.entity_id, "change": "appeared"})
            continue
        if e.pose is None or b.pose is None:
            unlocated.append(e.entity_id)
            entry = {"entity_id": e.entity_id, "position": "unmeasured"}
        else:
            dx = e.pose.position.x - b.pose.position.x
            dy = e.pose.position.y - b.pose.position.y
            dz = e.pose.position.z - b.pose.position.z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            entry = {"entity_id": e.entity_id, "dxy": [round(dx, 4), round(dy, 4)],
                     "dz": round(dz, 4), "dist_m": round(dist, 4)}
            if dist > move_tol:
                moved.append(entry)
        if b.held != e.held or b.supported_by != e.supported_by:
            entry2 = dict(entry)
            entry2["hold_before_after"] = [str(b.held), str(e.held)]
            entry2["support_before_after"] = [str(b.supported_by), str(e.supported_by)]
            changed.append(entry2)
    return {"moved": moved, "state_changed": changed,
            "position_unmeasured": unlocated,
            "held_before": str(before.held_object), "held_after": str(after.held_object)}
