"""Runtime verification and independent evaluation predicates (spec 11).

Both compute evidence from the physics engine's actual geometry / velocity /
contact state. Neither reads any flag written by a skill: "skill completed" and
"predicate satisfied" are different facts (spec 8.4).
"""
from __future__ import annotations

import numpy as np

from .contracts import (
    EvalSpec,
    EntityState,
    Pose,
    PredicateReport,
    PredicateVerdict,
    Source,
    TargetRegion,
    Vec3,
    WorldState,
)
from .scene import PhysicsScene


def _rest_z(scene, eid: str, floor_top: float) -> float:
    return floor_top + scene.objects[eid]["half_h"]


def object_in_region(scene, eid: str, region: TargetRegion, margin: float) -> tuple[bool, dict]:
    pos, _ = scene.object_pose(eid)
    inside = bool(
        abs(pos[0] - region.center.x) < region.inner_half - margin
        and abs(pos[1] - region.center.y) < region.inner_half - margin
    )
    return inside, {"xy_err_x": float(abs(pos[0] - region.center.x)), "xy_err_y": float(abs(pos[1] - region.center.y))}


def object_supported(scene, eid: str, region: TargetRegion, tol: float) -> tuple[bool, dict]:
    pos, _ = scene.object_pose(eid)
    rest = _rest_z(scene, eid, region.floor_top_z)
    return bool(abs(pos[2] - rest) < tol), {"height_err_m": float(abs(pos[2] - rest))}


def object_at_rest(scene, eid: str, speed_tol: float, ang_tol: float) -> tuple[bool, dict]:
    lin, ang = scene.object_velocity(eid)
    return bool(np.linalg.norm(lin) < speed_tol and np.linalg.norm(ang) < ang_tol), {
        "speed_mps": float(np.linalg.norm(lin)),
        "ang_speed_rps": float(np.linalg.norm(ang)),
    }


def object_not_held(scene, eid: str) -> tuple[bool, dict]:
    body = scene.objects[eid]["body"]
    return body not in scene.held_bodies(), {"in_gripper_contact": float(body in scene.held_bodies())}


class RuntimeVerifier:
    """Reads privileged physics in S1 (allowed, spec 11.1) but always from the
    simulation's real state. S3+ replaces the observation channel, not the code."""

    def __init__(self, scene: PhysicsScene, world: WorldState, tolerance_version: str = "v1"):
        self.scene = scene
        self.world = world
        self.tolerance_version = tolerance_version

    def verify_grasp(self, eid: str) -> PredicateReport:
        """Object lifted >3cm above its previous support and following the gripper."""
        pos, _ = self.scene.object_pose(eid)
        support = self.scene.objects[eid]["support"]
        lift = float(pos[2] - support)
        held = self.scene.objects[eid]["body"] in self.scene.held_bodies()
        ok = lift > 0.03 and held
        return PredicateReport(
            predicate_id=f"grasp:{eid}",
            description="object lifted >3cm and in gripper contact",
            value=PredicateVerdict.true if ok else PredicateVerdict.false,
            evidence={"lift_gain_m": lift, "in_contact": float(held)},
            source=Source.privileged,
        )

    def verify_place_step(self, eid: str, target_id: str, spec: EvalSpec) -> PredicateReport:
        region = next(t for t in self.world.targets if t.target_id == target_id)
        inside, e1 = object_in_region(self.scene, eid, region, spec.footprint_margin_m)
        supported, e2 = object_supported(self.scene, eid, region, spec.support_height_tol_m)
        not_held, e3 = object_not_held(self.scene, eid)
        at_rest, e4 = object_at_rest(self.scene, eid, spec.speed_tol_mps, spec.ang_speed_tol_rps)
        ok = inside and supported and not_held and at_rest
        return PredicateReport(
            predicate_id=f"placed:{eid}:{target_id}",
            description="object inside target, supported, at rest, not held",
            value=PredicateVerdict.true if ok else PredicateVerdict.false,
            evidence={**e1, **e2, **e3, **e4},
            source=Source.privileged,
        )

    def verify_goals(self, spec: EvalSpec) -> list[PredicateReport]:
        reports = []
        for a in spec.assignments:
            reports.append(self.verify_place_step(a.entity_id, a.target_id, spec))
        return reports


def build_world_state(scene: PhysicsScene, state_version: int, obs_ref: str) -> WorldState:
    """Privileged observation channel (S1 mode, spec 8.1). S3 replaces this with
    a sensor channel; the WorldState schema stays identical."""
    entities = []
    held_bodies = scene.held_bodies()
    held_ids = [scene.entity_by_body(b) for b in held_bodies]
    held_ids = [h for h in held_ids if h]
    for eid, d in scene.objects.items():
        pos, orn = scene.object_pose(eid)
        entities.append(
            EntityState(
                entity_id=eid,
                pose=Pose(
                    position={"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])},
                    quaternion_xyzw=(float(orn[0]), float(orn[1]), float(orn[2]), float(orn[3])),
                ),
                held=(eid in held_ids) if held_ids else False,
                supported_by="table",
                visible=True,
                attributes=dict(d["attributes"]),
                source=Source.privileged,
            )
        )
    targets = [
        TargetRegion(
            target_id=t["target_id"],
            label=t["target_id"],
            center=Vec3(x=t["center"][0], y=t["center"][1], z=t["floor_top"]),
            inner_half=t["inner_half"],
            floor_top_z=t["floor_top"],
        )
        for t in scene.trays.values()
    ]
    return WorldState(
        state_version=state_version,
        sim_time=scene.sim_time,
        wall_time=0.0,
        entities=entities,
        targets=targets,
        held_object=(held_ids[0] if len(held_ids) == 1 else ("unknown" if len(held_ids) > 1 else None)),
        observation_ref=obs_ref,
    )
