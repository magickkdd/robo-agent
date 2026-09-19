"""Placement candidate generation with traceable identities (SPEC 5.5, 8).

A candidate is a *named geometry*, not a slot index that silently moves. The id
embeds the rounded position, so regenerating the same option yields the same id
and an id can never be quietly re-pointed at another location. Each check
(parameter legality, tray boundary, occupancy, reachability) is reported
separately and an unchecked dimension is never claimed as proven.

Horizontal size and pose come from the real collision geometry: the footprint is
the yaw-rotated box's axis-aligned bound, not the vertical half-height that the
first implementation mistakenly used as a radius.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .contracts import (
    CheckVerdict,
    GeometrySpec,
    PlacementCandidate,
    TargetRegion,
    VerifyConfig,
    WorldState,
    oriented_footprint_half_xy,
)

TRAY_INNER = 0.135


def candidate_id_for(target_id: str, entity_id: str, x: float, y: float) -> str:
    return f"cand-{target_id}-{entity_id}-x{int(round(x * 1000)):d}y{int(round(y * 1000)):d}"


@dataclass
class CandidateCheck:
    candidate: PlacementCandidate
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons

    def why_not(self) -> list[str]:
        return list(self.reasons)


class CandidateRegistry:
    """Remembers every candidate id ever offered in an episode so that a choice
    can be resolved, re-checked and traced back to its generation version."""

    def __init__(self):
        self._by_id: dict[str, PlacementCandidate] = {}

    def remember(self, cand: PlacementCandidate) -> PlacementCandidate:
        prev = self._by_id.get(cand.candidate_id)
        if prev is not None:
            same = (abs(prev.position_xy[0] - cand.position_xy[0]) < 1e-6
                    and abs(prev.position_xy[1] - cand.position_xy[1]) < 1e-6)
            if not same:
                raise ValueError(
                    f"candidate id {cand.candidate_id} would name a different geometry; "
                    "ids must be stable (SPEC 5.5)")
        self._by_id[cand.candidate_id] = cand
        return cand

    def get(self, candidate_id: str) -> Optional[PlacementCandidate]:
        return self._by_id.get(candidate_id)

    def all(self) -> list[PlacementCandidate]:
        return list(self._by_id.values())


class PlacementPlanner:
    CLEARANCE_M = 0.010
    # How the hand-off is actuated. Both numbers are experiment-environment
    # calibration (SPEC 10.3: fixed in the scenario, identical for every mode),
    # recorded in the run manifest, and they decide where an object is *released*
    # rather than where it ends up: too high and a cylinder drops onto its side
    # and rolls tens of millimetres from the slot the decision named
    # (measured in work/p0_release.py and work/p0_handoff.py).
    HAND_OFF_CLEARANCE_M = 0.004   # palm height above the object's seated centre
    TRANSFER_Z = 0.80
    GRID_STEP_M = 0.020
    # Half-width of the volume the hand sweeps on the way down, measured from the
    # descent axis: at 65 mm centre-to-centre from a standing cylinder a placement
    # shoved it 69-94 mm and tipped it 19 mm over; at 75 mm the neighbour moved
    # <= 8 mm and never tipped (work/p0_sweep.py, 3 object pairs x 2 directions x
    # 5 separations). Two objects' footprints can legally touch 40 mm closer than
    # this, so a slot chosen on the footprint rule alone destroys a placement the
    # agent had already made true — an unrequested world change no mode can undo,
    # because an object lying at the bottom of a tray cannot be grasped.
    HAND_SWEEP_RADIUS_M = 0.050

    def __init__(self, scene, config: VerifyConfig | None = None,
                 registry: CandidateRegistry | None = None):
        self.scene = scene
        self.config = config or VerifyConfig()
        self.registry = registry or CandidateRegistry()
        self._reach_cache: dict[tuple[int, int], bool] = {}

    # ---------- geometry ----------
    def circumscribed_radius(self, entity_id: str) -> float:
        """Worst-case horizontal radius over any *yaw* of an upright body.

        This is the claim a slot is chosen with, so it is the upright-rest claim:
        the release is calibrated to hand the object off standing (measured in
        work/p0_release.py), and if it does not, the verifier — which uses the
        measured orientation instead — reports the footprint crossing the wall or
        the wrong seating height. Making this bound orientation-blind too (a
        cylinder on its side is `half_h` long) would reject slots that exist and
        turn a measured outcome into a planning artefact (SPEC 8: geometry truth).
        """
        geom: GeometrySpec = self.scene.geometry(entity_id)
        if geom.shape == "cylinder":
            return geom.radius
        return float(math.hypot(geom.half_extents.x, geom.half_extents.y))

    def footprint_at(self, entity_id: str, yaw_rad: float) -> tuple[float, float]:
        return oriented_footprint_half_xy(self.scene.geometry(entity_id), yaw_rad)

    def rest_z(self, entity_id: str, target_id: str) -> float:
        tray = self.scene.trays[target_id]
        return tray["floor_top"] + self.scene.geometry(entity_id).half_h_vertical

    def hand_off_z(self, entity_id: str, target_id: str) -> float:
        """Palm height at which the skill lets go.

        One definition, used by the reachability claim *and* by the descent, so a
        slot is never offered on the strength of a pose the action does not have to
        reach (SPEC 5.5: a claim must be checkable against the same criterion)."""
        return self.rest_z(entity_id, target_id) + self.HAND_OFF_CLEARANCE_M

    # ---------- generation ----------
    def generate(self, target_id: str, entity_id: str, world: WorldState,
                 limit: int | None = 8) -> list[PlacementCandidate]:
        """Lattice of interior positions that fit the object's own footprint.

        Occupancy and reachability are *reported* on each candidate, not used to
        silently drop options: the model must be able to see that a slot exists
        but is currently blocked, and decide accordingly (SPEC 5.5)."""
        if target_id not in self.scene.trays or entity_id not in self.scene.objects:
            return []
        tray = self.scene.trays[target_id]
        region = world.target(target_id)
        if region is None:
            return []
        cx, cy = tray["center"]
        r = self.circumscribed_radius(entity_id)
        room = tray["inner_half"] - self.config.footprint_margin_m - r - self.CLEARANCE_M
        if room <= 0:
            return []
        n = max(0, int(room // self.GRID_STEP_M))
        offsets = [(i * self.GRID_STEP_M, j * self.GRID_STEP_M)
                   for i in range(-n, n + 1) for j in range(-n, n + 1)]
        out = []
        for dx, dy in offsets:
            x, y = cx + dx, cy + dy
            out.append(self._make(target_id, entity_id, x, y, world))
        out.sort(key=lambda c: (math.hypot(c.position_xy[0] - cx, c.position_xy[1] - cy), c.candidate_id))
        return out[:limit]

    def _make(self, target_id: str, entity_id: str, x: float, y: float,
              world: WorldState) -> PlacementCandidate:
        return self.registry.remember(PlacementCandidate(
            candidate_id=candidate_id_for(target_id, entity_id, x, y),
            target_id=target_id, entity_id=entity_id,
            position_xy=(round(x, 4), round(y, 4)),
            rest_z=round(self.rest_z(entity_id, target_id), 4),
            footprint_half_xy=tuple(round(v, 4) for v in self.footprint_at(entity_id, 0.0)),
            generated_at_state_version=world.state_version,
        ))

    # ---------- checking ----------
    def recheck(self, cand: PlacementCandidate, world: WorldState,
                hold_entity: bool = True) -> CandidateCheck:
        """Re-run every check against the *latest* snapshot. Returns a fresh
        candidate copy; the stored one keeps its original claim so the record of
        what was offered stays honest."""
        c = cand.model_copy(deep=True)
        c.checked_at_state_version = world.state_version
        reasons: list[str] = []
        tray = self.scene.trays.get(c.target_id)
        region = world.target(c.target_id)
        if tray is None or region is None:
            c.params_ok = CheckVerdict.fail
            return CandidateCheck(c, [f"unknown target {c.target_id}"])
        if c.entity_id not in self.scene.objects:
            c.params_ok = CheckVerdict.fail
            return CandidateCheck(c, [f"unknown entity {c.entity_id}"])

        geom = self.scene.geometry(c.entity_id)
        r = self.circumscribed_radius(c.entity_id)
        cx, cy = tray["center"]
        limit = tray["inner_half"] - self.config.footprint_margin_m
        inside = abs(c.position_xy[0] - cx) + r <= limit and abs(c.position_xy[1] - cy) + r <= limit
        c.boundary_ok = CheckVerdict.ok if inside else CheckVerdict.fail
        if not inside:
            reasons.append("footprint crosses the tray wall or margin")
        c.params_ok = CheckVerdict.ok if (geom is not None and r > 0) else CheckVerdict.fail

        occupants = {o.entity_id for o in world.occupancy
                     if o.target_id == c.target_id and o.entity_id != c.entity_id}
        # an object merely dropped near/inside the tray still occupies volume
        occupants |= {e.entity_id for e in world.entities
                      if e.supported_by == c.target_id and e.entity_id != c.entity_id}
        clash = []
        for oid in sorted(occupants):
            other = world.entity(oid)
            dist = math.hypot(other.pose.position.x - c.position_xy[0],
                              other.pose.position.y - c.position_xy[1])
            need = self.HAND_SWEEP_RADIUS_M + self.circumscribed_radius(oid)
            if dist < need:
                clash.append(f"{oid} ({dist * 1000:.0f} mm from the descent axis, "
                             f"{need * 1000:.0f} mm needed to clear its hand sweep)")
        c.occupancy_ok = CheckVerdict.ok if not clash else CheckVerdict.fail
        if clash:
            reasons.append("slot is inside the hand's descent envelope of " + "; ".join(clash))

        c.reachability_ok = self._reachability(c, reasons)
        if c.reachability_ok == CheckVerdict.fail:
            reasons.append("end-effector path not provably reachable at this slot")
        c.notes = reasons
        return CandidateCheck(c, reasons)

    def _reachability(self, c: PlacementCandidate, reasons: list[str]) -> CheckVerdict:
        import pybullet as p

        orn = p.getQuaternionFromEuler([math.pi, 0, 0])
        release_z = self.hand_off_z(c.entity_id, c.target_id)
        for x, y, z in ((c.position_xy[0], c.position_xy[1], self.TRANSFER_Z),
                        (c.position_xy[0], c.position_xy[1], release_z)):
            key = (int(round(x * 200)), int(round(y * 200)), int(round(z * 1000)))
            if key not in self._reach_cache:
                self._reach_cache[key] = self.scene.reachable([x, y, z], orn)
            if not self._reach_cache[key]:
                return CheckVerdict.fail
        return CheckVerdict.ok

    # ---------- resolution ----------
    def resolve(self, candidate_id: str) -> Optional[PlacementCandidate]:
        return self.registry.get(candidate_id)

    def best_feasible(self, target_id: str, entity_id: str,
                      world: WorldState) -> tuple[Optional[PlacementCandidate], list[str]]:
        """Deterministic fallback used identically by both planning modes when a
        decision names no candidate id (SPEC 5.5: the same resolution rule for
        A and B, and it is recorded).

        The search is untruncated: answering "no slot is free" from the eight
        nearest grid points would be a property of the prompt budget, not of the
        tray, and would hand both modes a fake TARGET_NO_FREE_SLOT.
        """
        notes: list[str] = []
        for cand in self.generate(target_id, entity_id, world, limit=None):
            chk = self.recheck(cand, world)
            if chk.ok:
                return chk.candidate, notes
            notes.append(f"{cand.candidate_id}: {'; '.join(chk.reasons)}")
        return None, notes

    # ---------- legacy-compatible helpers ----------
    def get_capacity(self, target_id: str, object_ids: list[str], world: WorldState) -> int:
        if target_id not in self.scene.trays or not object_ids:
            return 0
        tray = self.scene.trays[target_id]
        r = max(self.circumscribed_radius(oid) for oid in object_ids)
        step = 2 * (r + self.CLEARANCE_M)
        side = 2 * (tray["inner_half"] - self.config.footprint_margin_m - r - self.CLEARANCE_M)
        if side <= 0:
            return 0
        return int((side // step + 1) ** 2)
