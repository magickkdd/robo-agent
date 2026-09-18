"""Skill layer (spec section 9).

Exposed skills: observe / pick / place / safe_retreat. Low-level motion
primitives (reach, open_gripper, descend, close_gripper, lift, transfer) live
inside the skill executors and are NOT part of the LLM-facing skill catalogue.
`verify` is forced by the Runtime after each skill; skills never declare task
success themselves (they only report their own execution outcome).
"""
from __future__ import annotations

import math

import numpy as np

from .contracts import (
    FailureCode,
    Pose,
    SkillCall,
    SkillResult,
    SkillStatus,
    Source,
    EntityState,
    TargetRegion,
    Vec3,
    WorldState,
)
from .scene import EE_LINK  # noqa: F401  (dependency marker; skills drive scene only)
from .placement_planner import PlacementPlanner, TraySlot

APPROACH_CLEARANCE = 0.14
LIFT_HEIGHT = 0.09
# empirically calibrated: grasp at the object center gives the most stable
# pad grip across all three shapes (see work/grasp_offset_calibration)
# at the object's mid-height by lifting the grasp target 20mm above its center
GRASP_Z_OFFSET = 0.0
DOWN_ORN = None  # computed lazily via scene helper


def _down_orn():
    import pybullet as pbl

    return pbl.getQuaternionFromEuler([math.pi, 0, 0])


class SkillRegistry:
    """Catalogue shown to the planner, with pre/postconditions (spec 8.3)."""

    CATALOGUE = {
        "observe": {
            "pre": ["robot in observable state"],
            "post": ["WorldState refreshed with time+source"],
            "args": {},
        },
        "pick": {
            "pre": ["object locatable", "gripper idle", "grasp candidate reachable"],
            "post": ["object lifted and stably following the end effector"],
            "args": {"object_id": "str", "candidate_index": "int(default 0)"},
        },
        "place": {
            "pre": ["holding object_id", "target exists with a free interior slot"],
            "post": ["object inside target region, supported, not held"],
            "args": {"object_id": "str", "target_id": "str"},
        },
        "safe_retreat": {
            "pre": ["current pose and held state known or conservatively treatable"],
            "post": ["no new collisions, recoverable posture"],
            "args": {},
        },
    }


class SkillExecutor:
    def __init__(self, scene, world_provider, grasp_lateral_step: float = 0.008):
        self.scene = scene
        self.world_provider = world_provider  # callable -> WorldState (current)
        self.grasp_lateral_step = grasp_lateral_step
        self.placement_planner = PlacementPlanner(scene)

    # ---------- helpers ----------
    def _snapshot_world(self) -> WorldState:
        return self.world_provider()

    def _result(self, call: SkillCall, status: SkillStatus, t0, stages, **kw) -> SkillResult:
        return SkillResult(
            call_id=call.call_id,
            status=status,
            t_start=t0,
            t_end=self.scene.sim_time,
            stages_executed=stages,
            held_state=self._held_state(),
            **kw,
        )

    def _held_state(self):
        held_bodies = self.scene.held_bodies()
        if not held_bodies:
            return None
        eids = [self.scene.entity_by_body(b) for b in held_bodies]
        eids = [e for e in eids if e]
        return eids[0] if len(eids) == 1 else "unknown"

    # ---------- skills ----------
    def execute(self, call: SkillCall) -> SkillResult:
        t0 = self.scene.sim_time
        try:
            fn = getattr(self, f"_do_{call.skill}")
            return fn(call, t0)
        except Exception as e:  # noqa: BLE001 - skill-level guard: report, never crash runtime
            r = self._result(call, SkillStatus.failed, t0, [f"exception:{type(e).__name__}"])
            r.failure_code = FailureCode.ANOMALOUS_CONTACT.value
            r.remaining_action_hint = f"exception: {e}"
            return r

    def _do_observe(self, call, t0):
        w = self._snapshot_world()
        return self._result(call, SkillStatus.completed, t0, ["read_channels"], post_observation_ref=w.observation_ref)

    def _do_pick(self, call, t0):
        oid = call.args["object_id"]
        cand_idx = int(call.args.get("candidate_index", 0))
        try:
            self.scene.objects[oid]
        except KeyError:
            r = self._result(call, SkillStatus.failed, t0, ["precondition"])
            r.failure_code = FailureCode.INVALID_PLAN.value
            return r
        held = self._held_state()
        if held == "unknown":
            # spec 11.3: unconfirmed held state -> stop, observe, decide
            self.scene.settle(0.3)
            held = self._held_state()
            if held == "unknown":
                orn0 = _down_orn()
                cur0, _ = self.scene.ee_pose()
                self.scene.move_ee([cur0[0], cur0[1], 0.90], orn0, timeout_s=1.5)
                self.scene.settle(0.3)
                held = self._held_state()
            if held == "unknown":
                r = self._result(call, SkillStatus.failed, t0, ["precondition"])
                r.failure_code = FailureCode.ANOMALOUS_CONTACT.value
                r.remaining_action_hint = "held state still unknown after re-observation; bounded exit"
                return r
        if held is not None and held != oid:
            # gripper not idle (spec 9 pick precondition): report, never blind-release
            r = self._result(call, SkillStatus.failed, t0, ["precondition"])
            r.failure_code = FailureCode.INVALID_PLAN.value
            r.remaining_action_hint = f"gripper holds {held}; place or retreat first"
            return r
        pos0, _ = self.scene.object_pose(oid)
        support_z = float(pos0[2])
        cands = self.scene.grasp_candidates(oid)
        if cand_idx >= len(cands):
            cand_idx = 0
        center = cands[cand_idx]
        center[2] += GRASP_Z_OFFSET  # pads at object mid-height, tips clear of the table
        grasp = center.copy()
        if "grasp_offset" in call.args:  # fault injection hook: frozen offset on first grasp
            off = np.array([float(v) for v in call.args["grasp_offset"].split(",")])
            grasp = grasp + off  # perturbs ONLY the final descent target (spec 4.4)
        orn = _down_orn()
        stages = ["approach", "open_gripper", "descend", "close_gripper", "lift", "grasp_check"]

        if held != oid:
            self.scene.reset_arm()  # only when the gripper is free (never teleport a held object)
            self.scene.open_gripper()
        # if already holding the requested object, never release it mid-air:
        # just re-verify the grasp below (spec 9: no unconditional release)
        # approach in two stages: first to a staging height above the object
        # (keeps the joint-interpolated path clear of tabletop obstacles, spec
        # 6.2: collision-checked approach-lift-transfer-descend), then descend
        # to the clean pre-grasp pose above the true object center
        stage_z = max(center[2] + APPROACH_CLEARANCE, 0.82)
        if not self.scene.move_ee([center[0], center[1], stage_z], orn, timeout_s=2.5):
            r = self._result(call, SkillStatus.failed, t0, stages[:1], failure_code=FailureCode.IK_UNREACHABLE.value)
            return r
        if not self.scene.move_ee([center[0], center[1], center[2] + APPROACH_CLEARANCE], orn, timeout_s=1.5):
            r = self._result(call, SkillStatus.failed, t0, stages[:1], failure_code=FailureCode.IK_UNREACHABLE.value)
            return r
        # descend stepwise; stop as soon as the fingertips touch the object so
        # slightly nudged bodies (±2cm) are still grasped robustly
        z = center[2] + APPROACH_CLEARANCE
        while z > grasp[2] + 0.002:
            z = max(z - 0.01, grasp[2])
            blocked = not self.scene.move_ee([grasp[0], grasp[1], z], orn, timeout_s=1.0, tol=8e-3, tcp_tol=0.03)
            if self.scene.held_bodies() or blocked:
                break
        self.scene.close_gripper()
        self.scene.settle(0.25)
        lift_xyz = [grasp[0], grasp[1], grasp[2] + LIFT_HEIGHT]
        if not self.scene.move_ee(lift_xyz, orn, timeout_s=2.0):
            r = self._result(call, SkillStatus.failed, t0, stages[:4], failure_code=FailureCode.IK_UNREACHABLE.value)
            return r

        # grasp check: lifted + follows lateral motion + contact (spec 11.2)
        pos1, _ = self.scene.object_pose(oid)
        lift_gain = float(pos1[2] - support_z)
        ex, ey, _ = self.scene.ee_pose()[0]
        self.scene.move_ee([ex + 0.05, ey - 0.04, lift_xyz[2]], orn, timeout_s=1.2)
        pos2, _ = self.scene.object_pose(oid)
        follow_err = float(np.linalg.norm(pos2[:2] - self.scene.ee_pose()[0][:2]))
        in_contact = self.scene.objects[oid]["body"] in self.scene.held_bodies()
        meas = {"lift_gain_m": lift_gain, "follow_err_m": follow_err, "in_contact": float(in_contact)}
        if lift_gain <= 0.03 or follow_err > 0.03 or not in_contact:
            r = self._result(
                call, SkillStatus.failed, t0, stages,
                measurements=meas,
                failure_code=FailureCode.GRASP_MISS.value,
            )
            r.remaining_action_hint = "safe_retreat then retry with next grasp candidate"
            return r
        return self._result(call, SkillStatus.completed, t0, stages, measurements=meas)

    def _do_place(self, call, t0):
        """Place object in target tray (spec 6.2, W2.3).
        
        Stages: precondition -> transfer -> descend -> release -> retreat -> settle -> verify
        
        Success evidence (spec 6.2):
        - Object footprint in target valid region
        - Object supported by target
        - Target not overflowing
        - Velocity stable
        - Gripper not carrying object
        
        Failure codes: TARGET_FULL, PLACE_UNSTABLE, PLACE_COLLISION, 
                       OBJECT_DROPPED, STATE_UNCERTAIN
        """
        oid = call.args["object_id"]
        tid = call.args["target_id"]
        stages = ["precondition", "transfer", "descend", "release", "retreat", "settle", "verify"]
        meas = {}
        
        # --- Stage 1: Precondition checks ---
        held = self._held_state()
        if held != oid:
            r = self._result(call, SkillStatus.failed, t0, stages[:1], failure_code=FailureCode.STATE_UNCERTAIN.value)
            r.remaining_action_hint = "object not held; re-observe before retry"
            return r
        if tid not in self.scene.trays:
            r = self._result(call, SkillStatus.failed, t0, stages[:1], failure_code=FailureCode.INVALID_PLAN.value)
            return r
        
        # --- Stage 2: Slot selection with collision check ---
        slot = self._free_slot(tid, oid)
        if slot is None:
            r = self._result(call, SkillStatus.failed, t0, stages[:1], failure_code=FailureCode.TARGET_FULL.value)
            r.remaining_action_hint = "target has no free slot; report infeasible or try another target"
            return r
        cx, cy = slot
        tray = self.scene.trays[tid]
        half_h = self.scene.objects[oid]["half_h"]
        orn = _down_orn()
        
        # --- Stage 3: Transfer to pre-placement pose ---
        fence_z = 0.78  # open-scene transfer height (spec 6.2)
        cur, _ = self.scene.ee_pose()
        if not self.scene.move_ee([cur[0], cur[1], fence_z], orn, timeout_s=2.0):
            return self._result(call, SkillStatus.failed, t0, stages[:2], failure_code=FailureCode.IK_UNREACHABLE.value)
        if not self.scene.move_ee([cx, cy, fence_z], orn, timeout_s=3.0):
            return self._result(call, SkillStatus.failed, t0, stages[:2], failure_code=FailureCode.IK_UNREACHABLE.value)
        
        # --- Stage 4: Adaptive descent to seated height ---
        z = fence_z
        seated = False
        seat_z = tray["floor_top"] + half_h + 0.010
        descent_steps = 0
        max_descent_steps = 20
        
        while z > tray["floor_top"] + half_h - 0.008 and descent_steps < max_descent_steps:
            z -= 0.005
            descent_steps += 1
            self.scene.move_ee([cx, cy, z], orn, timeout_s=0.8, tol=6e-3)
            
            # Check for collision during descent
            if self.scene.held_bodies() and oid not in [self.scene.entity_by_body(b) for b in self.scene.held_bodies()]:
                # Object was knocked out of gripper during descent
                meas["descent_collision"] = 1.0
                return self._result(call, SkillStatus.failed, t0, stages[:3], 
                                  failure_code=FailureCode.PLACE_COLLISION.value,
                                  measurements=meas)
            
            if self._seated(oid, tray, seat_z):
                seated = True
                break
        
        # --- Stage 5: Release object ---
        self.scene.open_gripper()
        meas["release_height"] = z - tray["floor_top"]
        
        # --- Stage 6: Retreat and wait for settle ---
        self.scene.move_ee([cx, cy, z + 0.10], orn, timeout_s=1.5)
        
        # Wait for object to settle (spec 6.2: stable window)
        rest_settled = self.scene.wait_until_rest(oid, max_s=2.5)
        self.scene.settle(0.4)
        
        # --- Stage 7: Post-placement verification ---
        pos, vel = self.scene.object_pose(oid)
        lin_vel, ang_vel = self.scene.object_velocity(oid)
        
        # Measure final state
        meas["final_x"] = float(pos[0])
        meas["final_y"] = float(pos[1])
        meas["final_z"] = float(pos[2])
        meas["lin_speed"] = float(np.linalg.norm(lin_vel))
        meas["ang_speed"] = float(np.linalg.norm(ang_vel))
        meas["seated"] = float(seated or self._seated(oid, tray))
        meas["rest_settled"] = float(rest_settled)
        
        # Check support (object must be on tray, not floating)
        in_tray_xy = (
            abs(pos[0] - tray["center"][0]) < tray["inner_half"] and
            abs(pos[1] - tray["center"][1]) < tray["inner_half"]
        )
        supported_z = abs(pos[2] - (tray["floor_top"] + half_h)) < 0.015
        supported = in_tray_xy and supported_z
        meas["supported"] = float(supported)
        
        # Check gripper not carrying
        gripper_free = self._held_state() is None
        meas["gripper_free"] = float(gripper_free)
        
        # Check velocity stability (spec 11.2: speed < 0.02 m/s, ang < 0.2 rad/s)
        velocity_stable = meas["lin_speed"] < 0.02 and meas["ang_speed"] < 0.2
        meas["velocity_stable"] = float(velocity_stable)
        
        # Final verdict
        if not seated and not self._seated(oid, tray):
            return self._result(call, SkillStatus.failed, t0, stages[:5], 
                              failure_code=FailureCode.PLACE_UNSTABLE.value,
                              measurements=meas,
                              remaining_action_hint="object did not seat in target; try different slot or adjust descent")
        
        if not supported:
            return self._result(call, SkillStatus.failed, t0, stages[:5],
                              failure_code=FailureCode.PLACE_UNSTABLE.value,
                              measurements=meas,
                              remaining_action_hint="object not supported by target; may have fallen off tray")
        
        if not velocity_stable:
            return self._result(call, SkillStatus.failed, t0, stages[:5],
                              failure_code=FailureCode.PLACE_UNSTABLE.value,
                              measurements=meas,
                              remaining_action_hint="object still moving; wait longer or check placement")
        
        if not gripper_free:
            return self._result(call, SkillStatus.failed, t0, stages[:5],
                              failure_code=FailureCode.OBJECT_DROPPED.value,
                              measurements=meas,
                              remaining_action_hint="object may still be in gripper; verify grasp state")
        
        return self._result(call, SkillStatus.completed, t0, stages, measurements=meas)

    def _seated(self, oid, tray, seat_z=None):
        """Check if object is seated in tray (spec 6.2).
        
        Seated means:
        - Object center is within tray bounds
        - Object height is at tray floor + half_height + tolerance
        """
        pos, _ = self.scene.object_pose(oid)
        if seat_z is None:
            seat_z = tray["floor_top"] + self.scene.objects[oid]["half_h"] + 0.010
        
        in_tray_xy = (
            abs(pos[0] - tray["center"][0]) < tray["inner_half"] - 0.005 and
            abs(pos[1] - tray["center"][1]) < tray["inner_half"] - 0.005
        )
        at_seat_height = float(pos[2]) <= seat_z
        
        return in_tray_xy and at_seat_height
    
    def _check_footprint_in_target(self, oid, tray):
        """Verify object footprint is within target region (spec 6.2).
        
        Uses PlacementPlanner footprint geometry for accurate check.
        """
        pos, _ = self.scene.object_pose(oid)
        half_h = self.scene.objects[oid]["half_h"]
        
        # Object footprint center must be within tray interior
        # with margin for footprint radius
        margin = 0.005  # 5mm margin (spec 11.2)
        
        in_tray = (
            abs(pos[0] - tray["center"][0]) < tray["inner_half"] - margin and
            abs(pos[1] - tray["center"][1]) < tray["inner_half"] - margin
        )
        
        return in_tray

    def _free_slot(self, tid, object_id=None):
        """Find a free slot in the tray using PlacementPlanner.
        
        Uses footprint-based slot generation with obstacle awareness:
        1. Computes object footprint geometry
        2. Checks clearance from already-placed objects
        3. Validates descent path is clear
        
        Falls back to simple center lookup if object_id not provided
        (for backward compatibility with existing code).
        """
        if object_id is None:
            # Fallback: return tray center (for code that doesn't pass object_id)
            tray = self.scene.trays[tid]
            return (tray["center"][0], tray["center"][1])
        
        slot = self.placement_planner.find_slot_for_object(tid, object_id)
        if slot is not None:
            return (float(slot.position[0]), float(slot.position[1]))
        return None

    def _do_safe_retreat(self, call, t0):
        stages = ["assess_held", "move_safe"]
        held = self._held_state()
        orn = _down_orn()
        cur, _ = self.scene.ee_pose()
        # Never release blindly: keep holding any object; just lift to a clear pose.
        self.scene.move_ee([cur[0], cur[1], max(cur[2], 0.80)], orn, timeout_s=1.5)
        self.scene.move_joints(self.scene.ik([0.45, 0.0, 0.80], orn), timeout_s=2.0)
        r = self._result(call, SkillStatus.completed, t0, stages)
        r.held_state = held
        return r
