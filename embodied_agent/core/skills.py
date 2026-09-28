"""Skill layer (SPEC v0.1 sections 5.5 and 8).

Exposed skills: observe / pick / place / safe_retreat. Motion primitives (reach,
open, descend, close, lift, transfer) stay inside the executors and are not part
of the model-facing catalogue.

Rules this module enforces:

* A skill never chooses the model's strategy. `place` executes the candidate the
  decision named; if none was named the runtime resolves one with the documented
  fallback rule and records it. An invalid named candidate is rejected, never
  silently swapped (SPEC 5.5).
* Rejection is not physics. Missing arguments, unknown entities, a busy gripper
  or a stale candidate return `status=rejected` with structured reasons; they
  must not be recorded as a collision (SPEC 5.4, 6.1).
* Every result carries the observation refs it was based on, the stages actually
  executed and the simulated seconds consumed, including internal waiting
  (SPEC 5.4, 6.2).
* No instantaneous joint reset during execution and no object teleporting
  (SPEC 12.1.5).
"""
from __future__ import annotations

import math

import numpy as np

from .contracts import (
    FailureCode,
    PlacementCandidate,
    SkillCall,
    SkillResult,
    SkillStatus,
    VerifyConfig,
    WorldState,
)
from .placement_planner import PlacementPlanner
from .scene import PhysicsScene, SimTimeout

APPROACH_CLEARANCE = 0.14
LIFT_HEIGHT = 0.09
GRASP_Z_OFFSET = 0.0
TRANSFER_Z = 0.80
PRE_GRASP_Z = 0.78


def _down_orn():
    import pybullet as pbl

    return pbl.getQuaternionFromEuler([math.pi, 0, 0])


class SkillRegistry:
    """Model-facing catalogue with public pre/postconditions (SPEC 5.2)."""

    CATALOGUE = {
        "observe": {
            "description": "Take a fresh measurement of the scene.",
            "args": {"view": "str (optional: which declared camera to measure with)"},
            "pre": ["none"],
            "post": ["a new WorldState snapshot with a fresh observation_ref"],
        },
        "pick": {
            "description": "Grasp one named object and hold it.",
            "args": {"object_id": "str (required)", "grasp_candidate_index": "int (optional, 0..2)"},
            "pre": ["object_id exists in the current observation",
                    "gripper is known to be free, or already holds object_id"],
            "post": ["object lifted above its support and confirmed held"],
        },
        "place": {
            "description": "Release the held object at a named placement candidate.",
            "args": {"object_id": "str (required)", "target_id": "str (required)",
                     "candidate_id": "str (optional: id from context candidates)"},
            "pre": ["held_object == object_id", "target exists",
                    "the named candidate passes re-check against the latest state"],
            "post": ["object rests inside the target region, supported, not held"],
        },
        "safe_retreat": {
            "description": "Lift the end effector to a clear posture without releasing anything.",
            "args": {},
            "pre": ["current pose and held state known, or conservatively treatable"],
            "post": ["no new contact, recoverable posture, still holding whatever was held"],
        },
    }


class SkillExecutor:
    def __init__(self, scene: PhysicsScene, world_provider, planner: PlacementPlanner | None = None,
                 config: VerifyConfig | None = None):
        self.scene = scene
        self.world_provider = world_provider  # callable -> current WorldState
        self.config = config or VerifyConfig()
        self.placement_planner = planner or PlacementPlanner(scene, self.config)
        self.last_candidate: PlacementCandidate | None = None

    # ---------- helpers ----------
    def _snapshot(self) -> WorldState:
        w = self.world_provider() if callable(self.world_provider) else self.world_provider
        if w is None:
            raise RuntimeError("SkillExecutor has no working world_provider; skills must not act blind")
        return w

    def _result(self, call, status, t0, stages, pre_obs, **kw) -> SkillResult:
        return SkillResult(
            call_id=call.call_id, status=status, t_start=t0, t_end=self.scene.sim_time,
            sim_seconds_used=round(self.scene.sim_time - t0, 3), stages_executed=list(stages),
            held_state=self._held_state(), pre_observation_ref=pre_obs, **kw,
        )

    def _rejected(self, call, t0, pre_obs, code: FailureCode, reasons: list[str],
                  stages=("rejected:validation",)) -> SkillResult:
        r = self._result(call, SkillStatus.rejected, t0, list(stages), pre_obs)
        r.failure_code = code.value
        r.notes = reasons
        return r

    def held_state(self):
        """The gripper's own report of what is in it, asked now rather than remembered.

        This is §5.8's first layer: what the actuators did, from the same call
        `_do_pick` and `_do_place` consult before they move. A perception-backed Runtime
        reads it here instead of a snapshot's `held_object`, because no camera pointed at
        a table answers what the hand holds — and a name it does answer with is the
        entity's, which is why the value needs grounding before a percept snapshot can
        use it."""
        return self._held_state()

    def _held_state(self):
        held_bodies = self.scene.held_bodies()
        if not held_bodies:
            return None
        eids = sorted({self.scene.entity_by_body(b) for b in held_bodies} - {None})
        if len(eids) == 1:
            return eids[0]
        return "unknown"

    # ---------- dispatch ----------
    def execute(self, call: SkillCall) -> SkillResult:
        t0 = self.scene.sim_time
        try:
            pre_obs = self._snapshot().observation_ref
        except Exception:  # noqa: BLE001 - an unavailable observation is reported, not hidden
            pre_obs = None
        self.scene.start_action(call.timeout_s, f"skill:{call.skill}")
        self.scene.begin_execution()
        self.last_candidate = None
        try:
            fn = getattr(self, f"_do_{call.skill}", None)
            if fn is None:
                return self._rejected(call, t0, pre_obs, FailureCode.INVALID_DECISION,
                                      [f"unknown skill {call.skill}"])
            return fn(call, t0, pre_obs)
        except SimTimeout as e:
            r = self._result(call, SkillStatus.timeout, t0, ["timeout"], pre_obs)
            r.failure_code = FailureCode.TIMEOUT.value
            r.notes = [f"sim-time budget spent: {e}"]
            return r
        except KeyError as e:
            return self._rejected(call, t0, pre_obs, FailureCode.INVALID_DECISION,
                                  [f"missing required argument {e}"])
        except Exception as e:  # noqa: BLE001 - report the anomaly, never fake a collision
            r = self._result(call, SkillStatus.failed, t0, [f"exception:{type(e).__name__}"], pre_obs)
            r.failure_code = FailureCode.ANOMALOUS_CONTACT.value
            r.notes = [f"unclassified skill exception: {type(e).__name__}: {e}",
                       "root cause not confirmed by measurement"]
            return r
        finally:
            self.scene.end_action()

    # ---------- observe ----------
    def _do_observe(self, call, t0, pre_obs):
        w = self._snapshot()
        r = self._result(call, SkillStatus.completed, t0, ["read_channels"], pre_obs,
                         post_observation_ref=w.observation_ref)
        r.measurements = {
            "held": 0.0 if w.held_object is None else (1.0 if w.held_object == "unknown" else 2.0),
            "n_entities": float(len(w.entities)),
            "sim_time": w.sim_time,
        }
        return r

    # ---------- pick ----------
    def _do_pick(self, call, t0, pre_obs):
        oid = call.args["object_id"]  # KeyError is mapped to a structured rejection above
        stages = ["precondition"]
        w = self._snapshot()
        if not w.has_entity(oid):
            return self._rejected(call, t0, pre_obs, FailureCode.UNKNOWN_ENTITY,
                                  [f"object {oid} is not in observation {pre_obs}"])
        held = self._held_state()
        if held == "unknown":
            self.scene.settle(0.3)
            held = self._held_state()
            if held == "unknown":
                return self._rejected(call, t0, pre_obs, FailureCode.STATE_UNCERTAIN,
                                      ["held object could not be identified after re-measurement; "
                                       "pick/place would be blind"])
        if held is not None and held != oid:
            return self._rejected(call, t0, pre_obs, FailureCode.HELD_STATE_CONFLICT,
                                  [f"gripper holds {held}; picking {oid} is not legal right now"])

        pos0, _ = self.scene.object_pose(oid)
        support_z = float(pos0[2])
        cands = self.scene.grasp_candidates(oid)
        try:
            idx = int(call.args.get("grasp_candidate_index", 0))
        except ValueError:
            return self._rejected(call, t0, pre_obs, FailureCode.INVALID_DECISION,
                                  ["grasp_candidate_index must be an integer"])
        if not 0 <= idx < len(cands):
            return self._rejected(call, t0, pre_obs, FailureCode.INVALID_DECISION,
                                  [f"grasp_candidate_index {idx} outside 0..{len(cands) - 1}"])
        center = np.array(cands[idx], dtype=float)
        grasp = center + GRASP_Z_OFFSET * np.array([0.0, 0.0, 1.0])
        # actuator calibration error belongs to the environment, not the request
        if self.scene.grasp_calibration_offset is not None:
            grasp = grasp + np.array(self.scene.grasp_calibration_offset, dtype=float)
        orn = _down_orn()

        if held == oid:
            # already grasping this object: only re-verify, never release mid-air
            stages = ["already_held", "grasp_check"]
        else:
            stages += ["approach", "open_gripper", "descend", "close_gripper", "lift", "grasp_check"]
            self.scene.open_gripper()
            stage_z = max(center[2] + APPROACH_CLEARANCE, PRE_GRASP_Z + 0.04)
            if not self.scene.move_ee([center[0], center[1], stage_z], orn, timeout_s=2.5):
                return self._motion_failure(call, t0, pre_obs, stages, "staging approach")
            if not self.scene.move_ee([center[0], center[1], grasp[2] + APPROACH_CLEARANCE], orn,
                                       timeout_s=1.5):
                return self._motion_failure(call, t0, pre_obs, stages, "pre-grasp hover")
            z = grasp[2] + APPROACH_CLEARANCE
            floor_z = grasp[2] + 0.002
            while z > floor_z:
                z = max(z - 0.01, floor_z)
                blocked = not self.scene.move_ee([grasp[0], grasp[1], z], orn, timeout_s=1.0,
                                                 tol=8e-3, tcp_tol=0.03)
                if self.scene.held_bodies() or blocked:
                    break
            self.scene.close_gripper()
            self.scene.settle(0.25)
            if not self.scene.move_ee([grasp[0], grasp[1], grasp[2] + LIFT_HEIGHT], orn, timeout_s=2.0):
                return self._motion_failure(call, t0, pre_obs, stages, "lift")

        pos1, _ = self.scene.object_pose(oid)
        lift_gain = float(pos1[2] - support_z)
        ex, ey, ez = self.scene.ee_pose()[0]
        self.scene.move_ee([ex + 0.05, ey - 0.04, ez], orn, timeout_s=1.2)
        pos2, _ = self.scene.object_pose(oid)
        follow_err = float(np.linalg.norm(pos2[:2] - self.scene.ee_pose()[0][:2]))
        in_contact = self.scene.objects[oid]["body"] in self.scene.held_bodies()
        held_now = self._held_state()
        meas = {"lift_gain_m": lift_gain, "follow_err_m": follow_err,
                "in_contact": float(in_contact), "support_z_m": support_z}
        ok = (lift_gain > self.config.lift_gain_min_m and follow_err <= self.config.grasp_follow_tol_m
              and in_contact and held_now == oid)
        if not ok:
            r = self._result(call, SkillStatus.failed, t0, stages, pre_obs, measurements=meas)
            r.failure_code = FailureCode.GRASP_MISS.value
            r.notes = ["grasp check failed on measured evidence"]
            return r
        return self._result(call, SkillStatus.completed, t0, stages, pre_obs, measurements=meas)

    def _motion_failure(self, call, t0, pre_obs, stages, where):
        r = self._result(call, SkillStatus.failed, t0, stages, pre_obs)
        r.failure_code = FailureCode.IK_UNREACHABLE.value
        r.notes = [f"end effector did not reach its target during {where} "
                   f"(tcp_err_m={getattr(self.scene, '_last_tcp_err', float('nan')):.3f})",
                   "root cause not confirmed: may be kinematic limit or contact"]
        return r

    # ---------- place ----------
    def _do_place(self, call, t0, pre_obs):
        oid = call.args["object_id"]
        tid = call.args["target_id"]
        w = self._snapshot()
        stages = ["precondition"]
        if not w.has_entity(oid):
            return self._rejected(call, t0, pre_obs, FailureCode.UNKNOWN_ENTITY,
                                  [f"object {oid} is not in observation {pre_obs}"])
        if w.target(tid) is None:
            return self._rejected(call, t0, pre_obs, FailureCode.UNKNOWN_TARGET,
                                  [f"target {tid} is not in observation {pre_obs}"])
        held = self._held_state()
        if held == "unknown":
            self.scene.settle(0.3)
            held = self._held_state()
            if held == "unknown":
                return self._rejected(call, t0, pre_obs, FailureCode.STATE_UNCERTAIN,
                                      ["held object unidentified; releasing would be blind"])
        if held != oid:
            return self._rejected(call, t0, pre_obs, FailureCode.HELD_STATE_CONFLICT,
                                  [f"cannot place {oid}: gripper holds {held!r}"])

        # --- candidate resolution: named by the decision, or the documented fallback ---
        cid = call.args.get("candidate_id") or call.candidate_id
        resolution = "model_named_candidate" if cid else "runtime_fallback_rule"
        if cid:
            named = self.placement_planner.resolve(cid)
            if named is None:
                return self._rejected(call, t0, pre_obs, FailureCode.CANDIDATE_STALE,
                                      [f"candidate id {cid} was never offered in this episode"])
            if named.entity_id != oid or named.target_id != tid:
                return self._rejected(call, t0, pre_obs, FailureCode.INVALID_DECISION,
                                      [f"candidate {cid} belongs to {named.entity_id}->{named.target_id}, "
                                       f"not {oid}->{tid}"])
            chk = self.placement_planner.recheck(named, w)
            if not chk.ok:
                return self._rejected(call, t0, pre_obs, FailureCode.CANDIDATE_INFEASIBLE,
                                      [f"candidate {cid} re-check failed at state "
                                       f"{w.state_version}: " + "; ".join(chk.reasons)],
                                      stages=["rejected:candidate_recheck"])
            cand = chk.candidate
        else:
            cand, notes = self.placement_planner.best_feasible(tid, oid, w)
            if cand is None:
                code = FailureCode.TARGET_FULL if notes else FailureCode.NO_FEASIBLE_CANDIDATE
                return self._rejected(call, t0, pre_obs, code,
                                      [f"no feasible candidate for {oid} in {tid} at state "
                                       f"{w.state_version}"] + (notes[:4] if notes else []))
        self.last_candidate = cand
        cx, cy = cand.position_xy
        tray = self.scene.trays[tid]
        orn = _down_orn()
        # An actuator calibration error moves the *actuated* transfer point and
        # release height only. The named candidate, the request and every value
        # reported below stay what the decision asked for, so the evidence the
        # model sees is a measured consequence, not a leaked label (SPEC 10.2).
        ax, ay = cx, cy
        release_lift = 0.0
        if self.scene.place_calibration_offset is not None:
            dx, dy, dz = self.scene.place_calibration_offset
            ax, ay = cx + dx, cy + dy
            release_lift = dz
        stages += ["transfer", "descend", "release", "retreat", "settle"]

        # Everything below the transfer is motion *with the object in the hand*, so
        # the path is interpolated: one long joint command swings the arm at motor
        # speed, the body slips ~20 mm in the grip, and the release then happens
        # somewhere the decision never named (work/p0_transfer.py).
        cur, _ = self.scene.ee_pose()
        if not self.scene.move_ee_straight([cur[0], cur[1], TRANSFER_Z], orn):
            return self._motion_failure(call, t0, pre_obs, stages, "lift to transfer height")
        if not self.scene.move_ee_straight([ax, ay, TRANSFER_Z], orn):
            return self._motion_failure(call, t0, pre_obs, stages, "transfer over target")

        hand_off_z = self.placement_planner.hand_off_z(oid, tid) + release_lift
        pos_held, _ = self.scene.object_pose(oid)
        meas: dict[str, float] = {
            "candidate_x": cx, "candidate_y": cy, "candidate_rest_z": cand.rest_z,
            # where the object hangs relative to the palm on arrival: the evidence
            # that the named candidate is where the *object* is let go
            "carry_offset_x_m": float(pos_held[0] - ax),
            "carry_offset_y_m": float(pos_held[1] - ay),
        }
        z = TRANSFER_Z
        touchdown = False
        while True:
            z = max(z - 0.005, hand_off_z)
            if not self.scene.move_ee([ax, ay, z], orn, timeout_s=0.8, tol=6e-3, tcp_tol=0.02):
                break
            if self.scene.touchdown_contact(oid):
                touchdown = True
                break
            if z <= hand_off_z:
                break
        released, _ = self.scene.object_pose(oid)
        meas["descent_touchdown"] = float(touchdown)
        meas["release_height_m"] = float(released[2] - tray["floor_top"])
        meas["release_gap_above_seat_m"] = float(released[2] - cand.rest_z)

        self.scene.open_gripper()
        # Do not back the hand out through the object: a rising finger pad that is
        # still beside it spins a low-inertia cylinder to several rad/s
        # (work/p0_handoff.py), so wait for the contact to measurably clear.
        clear = False
        for _ in range(10):
            self.scene.settle(0.05)
            if self.scene.fingers_clear_of(oid):
                clear = True
                break
        meas["fingers_clear_after_open"] = float(clear)
        if not self.scene.move_ee_straight([ax, ay, z + 0.10], orn):
            # the object may already be released; the evidence below decides
            pass
        rest = self.scene.wait_until_rest(oid, max_s=2.5, speed_tol=self.config.speed_tol_mps,
                                          ang_tol=self.config.ang_speed_tol_rps)
        self.scene.settle(0.6)

        pos, _ = self.scene.object_pose(oid)
        lin, ang = self.scene.object_velocity(oid)
        held_after = self._held_state()
        half_xy = self.scene.footprint_half_xy(oid)
        inside, ev = footprint_evidence(self.scene, oid, tid, self.config.footprint_margin_m)
        meas.update({
            "final_x": float(pos[0]), "final_y": float(pos[1]), "final_z": float(pos[2]),
            "lin_speed": float(np.linalg.norm(lin)), "ang_speed": float(np.linalg.norm(ang)),
            "inside_target": float(inside), "gripper_free": float(held_after is None),
            "at_rest": float(rest),
            "footprint_half_x": float(half_xy[0]), "footprint_half_y": float(half_xy[1]),
            "displacement_from_candidate_m": float(math.hypot(pos[0] - cx, pos[1] - cy)),
        })
        post = self._snapshot()
        r = self._result(call, SkillStatus.completed, t0, stages, pre_obs, measurements=meas)
        r.post_observation_ref = post.observation_ref
        r.held_state = held_after
        # recorded before the outcome branches: who chose the slot is true even when
        # the placement then failed, and the prose note below does not survive that
        r.candidate_resolution = resolution
        if held_after == oid:
            r.status = SkillStatus.failed
            r.failure_code = FailureCode.DROP_UNCONFIRMED.value
            r.notes = ["fingers still in contact with the object after release actuation; "
                       "the object was not handed off"]
            return r
        if not inside:
            r.status = SkillStatus.failed
            r.failure_code = FailureCode.PLACE_UNSTABLE.value
            r.notes = [f"footprint outside {tid}: err_x={ev['xy_err_x']:.3f} err_y={ev['xy_err_y']:.3f} "
                       f"allow_x={ev['allow_x']:.3f} allow_y={ev['allow_y']:.3f}"]
            return r
        r.notes = [f"candidate_resolution={resolution}", f"candidate={cand.candidate_id}"]
        return r

    # ---------- safe retreat ----------
    def _do_safe_retreat(self, call, t0, pre_obs):
        stages = ["assess_held", "move_safe"]
        held = self._held_state()
        orn = _down_orn()
        cur, _ = self.scene.ee_pose()
        # Never release blindly: keep whatever is held and climb to a clear pose.
        if not self.scene.move_ee([cur[0], cur[1], max(cur[2], 0.82)], orn, timeout_s=1.5):
            return self._motion_failure(call, t0, pre_obs, stages, "vertical clear")
        ok = self.scene.move_joints(self.scene.ik([0.45, 0.0, 0.82], orn), timeout_s=2.0)
        post = self._snapshot()
        r = self._result(call, SkillStatus.completed if ok else SkillStatus.timeout, t0, stages,
                         pre_obs, post_observation_ref=post.observation_ref)
        r.held_state = self._held_state()
        if not ok:
            r.failure_code = FailureCode.TIMEOUT.value
        if r.held_state != held:
            r.status = SkillStatus.failed
            r.failure_code = FailureCode.OBJECT_DROPPED.value
            r.notes = [f"held entity changed during retreat: {held} -> {r.held_state}"]
        return r


def footprint_evidence(scene: PhysicsScene, eid: str, target_id: str, margin: float) -> tuple[bool, dict]:
    """Local helper so the skill's own report and the verifier use one geometry."""
    region = scene.trays[target_id]
    from .contracts import TargetRegion as TR, Vec3 as V

    tr = TR(target_id=target_id, label=target_id, center=V(x=region["center"][0], y=region["center"][1],
                                                           z=region["floor_top"]),
            inner_half=region["inner_half"], floor_top_z=region["floor_top"])
    from .verify import object_in_region

    return object_in_region(scene, eid, tr, margin)
