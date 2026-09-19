"""PyBullet physics backend (SPEC v0.1 section 8: keep the backend, fix the
defects that mask decision capability).

Changes that matter for the continuous-decision stage:

* Geometry truth. Every object registers a `GeometrySpec` with its *horizontal*
  half-extents and vertical half-height, so planners and verifiers stop using
  the vertical half-height as a footprint radius.
* Contact evidence. `support_evidence()` derives what an object rests on from
  measured contacts instead of assuming "table".
* No teleport during execution. `reset_arm()` only runs at scene construction or
  an explicit episode reset (`allow_teleport=True`); mid-skill joint resets were
  pushing tabletop objects around.
* Real time units. The old `settle(60)` inside `reset_arm` advanced 60 *seconds*
  of simulated time per grasp attempt.
* Bounded actions. `start_action(timeout_s)` gives every motion primitive a
  sim-time deadline so a skill cannot silently exceed the per-skill budget
  (SPEC 6.2: budgets cover internal waiting).
* Environment-side disturbance. `apply_environment_impulse` is the only way an
  external event enters the physics world, and it records its own evidence;
  skills and results are never edited after the fact (SPEC 8 fault_injection).
"""
from __future__ import annotations

import contextlib
import math
import os

import numpy as np
import pybullet as p
import pybullet_data

from .contracts import GeometrySpec, Vec3

SIM_DT = 1.0 / 240.0
EE_LINK = 11  # panda_grasptarget_hand
ARM_JOINTS = list(range(7))
FINGER_JOINTS = [9, 10]
MAX_FINGER_OPEN = 0.04
HOME_Q = [0.0, -0.5, 0.4, -2.1, 0.0, 1.4, 0.0]

JOINT_KP = 0.35
JOINT_MAX_FORCE = 240.0
FINGER_CLOSE_FORCE = 60.0
GRASP_FRICTION = 2.0
TABLE_TOP_Z = 0.62
TABLE_XY = (0.78, 0.0)
TABLE_HALF = (0.45, 0.5)

TRAY_INNER = 0.135
TRAY_WALL_H = 0.025
TRAY_WALL_T = 0.008

# --- motion criteria (one definition, shared by the mover and by claims) ---
MOVE_TOL_RAD = 8e-3          # joint-space convergence for a commanded solution
MOVE_TCP_TOL_M = 0.010       # TCP error a commanded waypoint must not exceed
# Measured on `dev` (work/p0_reach3.py): as the envelope tightens, the servo
# settles 0.2-2.4 mm *beyond* the residual its IK solution reports. A candidate
# slot is therefore only claimed reachable with this much headroom, so a claim
# cannot be contradicted by the very action that follows it.
SERVO_SETTLE_ALLOWANCE_M = 0.002
# A held body slips in the grip when the hand is commanded across a long distance
# in one joint-space jump: measured 21 mm off the palm axis for a 280 mm transfer,
# 4 mm when the same path is interpolated (work/p0_transfer.py). The slip is what
# puts a finger pad inside the object's silhouette, so the rising hand drags a
# cylinder out of the slot the decision named. Waypoint spacing is therefore
# declared experiment-environment calibration (SPEC 10.3), fixed before the
# experiment and identical for every mode.
TRANSFER_WAYPOINT_M = 0.040


class SimTimeout(Exception):
    """Raised inside a motion primitive when the action's sim-time budget is
    spent. Skills translate it into a `timeout` SkillResult."""


class PhysicsScene:
    def __init__(self, seed: int = 0, object_layout: list[dict] | None = None, gui: bool = False):
        self.sim_time = 0.0
        self.cid = p.connect(p.GUI if gui else p.DIRECT)
        assert self.cid >= 0
        p.resetSimulation(physicsClientId=self.cid)
        p.setGravity(0, 0, -9.81, physicsClientId=self.cid)
        p.setTimeStep(SIM_DT, physicsClientId=self.cid)
        p.setPhysicsEngineParameter(numSolverIterations=60, physicsClientId=self.cid)
        p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"), physicsClientId=self.cid)

        self.robot = p.loadURDF(
            os.path.join(pybullet_data.getDataPath(), "franka_panda", "panda.urdf"),
            basePosition=[0.05, 0.0, 0.30],
            useFixedBase=True,
            physicsClientId=self.cid,
        )
        ped = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.09, 0.09, 0.15], physicsClientId=self.cid)
        self.pedestal = p.createMultiBody(0, ped, basePosition=[0.05, 0.0, 0.15], physicsClientId=self.cid)
        for j in FINGER_JOINTS:
            p.changeDynamics(self.robot, j, lateralFriction=GRASP_FRICTION, physicsClientId=self.cid)

        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[*TABLE_HALF, TABLE_TOP_Z / 2],
                                     physicsClientId=self.cid)
        self.table = p.createMultiBody(0, col, basePosition=[*TABLE_XY, TABLE_TOP_Z / 2],
                                       physicsClientId=self.cid)
        p.changeDynamics(self.table, -1, lateralFriction=0.6, physicsClientId=self.cid)

        self.trays: dict[str, dict] = {}
        for name, cx, cy in [("tray_left", 0.66, -0.32), ("tray_middle", 0.66, 0.0), ("tray_right", 0.66, 0.32)]:
            self._make_tray(name, [cx, cy])

        self.objects: dict[str, dict] = {}
        layout = object_layout or [
            {"entity_id": "obj_cube", "shape": "box", "dims": [0.025, 0.025, 0.025],
             "xy": [0.45, -0.10], "color": [0.85, 0.1, 0.1, 1],
             "attributes": {"color": "red", "shape": "cube"}},
        ]
        rng = np.random.default_rng(seed)
        for d in layout:
            self._make_object(d, rng)

        self.sim_time = 0.0
        self._joint_lower = [p.getJointInfo(self.robot, j, physicsClientId=self.cid)[8] for j in ARM_JOINTS]
        self._joint_upper = [p.getJointInfo(self.robot, j, physicsClientId=self.cid)[9] for j in ARM_JOINTS]
        self._joint_ranges = [u - l for l, u in zip(self._joint_lower, self._joint_upper)]

        self._action_steps_left = 0
        self._action_seconds_spent = 0.0
        self._action_label = ""
        # controller calibration error, owned by the environment, invisible to
        # the agent: it perturbs the *actuated* descent target, not the request
        self.grasp_calibration_offset: list[float] | None = None
        self.grasp_calibration_event: str | None = None
        # same mechanism on the release side: the actuated transfer point and
        # release height are off, while the named candidate and every measured
        # value stay what they were (SPEC 10.2 "release obstruction")
        self.place_calibration_offset: list[float] | None = None
        self.place_calibration_event: str | None = None
        self._teleport_allowed = True
        self._closed = False
        self.reset_arm(quiet=True)

    # ---------- construction ----------
    def _make_tray(self, name, center_xy, inner=TRAY_INNER, wall_h=TRAY_WALL_H, wall_t=TRAY_WALL_T):
        cid_floor = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + wall_t, inner + wall_t, 0.004],
                                           physicsClientId=self.cid)
        floor = p.createMultiBody(0, cid_floor,
                                  basePosition=[center_xy[0], center_xy[1], TABLE_TOP_Z + 0.004],
                                  physicsClientId=self.cid)
        cid_wall_ns = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + 2 * wall_t, wall_t, wall_h / 2],
                                             physicsClientId=self.cid)
        cid_wall_ew = p.createCollisionShape(p.GEOM_BOX, halfExtents=[wall_t, inner, wall_h / 2],
                                             physicsClientId=self.cid)
        z = TABLE_TOP_Z + 0.008 + wall_h / 2
        walls = [
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] - inner - wall_t / 2, z],
                              physicsClientId=self.cid),
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] + inner + wall_t / 2, z],
                              physicsClientId=self.cid),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] - inner - wall_t / 2, center_xy[1], z],
                              physicsClientId=self.cid),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] + inner + wall_t / 2, center_xy[1], z],
                              physicsClientId=self.cid),
        ]
        self.trays[name] = {
            "target_id": name,
            "label": name,
            "center": [center_xy[0], center_xy[1]],
            # The free interior, not the nominal one. The walls are boxes centred
            # at +-inner-wall_t/2 with half-thickness wall_t, so their *inner*
            # faces sit at inner - wall_t/2 = 0.131 m, not at 0.135. Reporting the
            # nominal half made every boundary check 4 mm too generous, and a body
            # pinned against a wall then landed 1 mm from the validity limit:
            # "is this placement still inside?" was decided by the object's own
            # half-extent rather than by the wall (measured in work/p0_impulse.py).
            "inner_half": inner - wall_t / 2.0,
            "nominal_inner_half": inner,
            "floor_top": TABLE_TOP_Z + 0.008,
            "wall_top": TABLE_TOP_Z + 0.008 + wall_h,
            "bodies": [floor] + walls,
        }

    def _make_object(self, d, rng):
        shape = "cylinder" if d["shape"] == "cylinder" else "box"
        if shape == "box":
            half = [float(v) for v in d["dims"]]
            col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.cid)
            z = TABLE_TOP_Z + half[2]
            geom = GeometrySpec(shape="box", half_extents=Vec3(x=half[0], y=half[1], z=half[2]), mass_kg=0.05)
            mass = 0.05
        else:
            radius, half_h = float(d["dims"][0]), float(d["dims"][1])
            col = p.createCollisionShape(p.GEOM_CYLINDER, radius=radius, height=half_h * 2,
                                         physicsClientId=self.cid)
            z = TABLE_TOP_Z + half_h
            geom = GeometrySpec(shape="cylinder", radius=radius, half_h=half_h, mass_kg=0.04)
            mass = 0.04
        body = p.createMultiBody(
            baseMass=mass,
            baseCollisionShapeIndex=col,
            basePosition=[d["xy"][0] + rng.uniform(-0.01, 0.01), d["xy"][1] + rng.uniform(-0.01, 0.01), z],
            baseOrientation=p.getQuaternionFromEuler([0, 0, rng.uniform(-0.2, 0.2)], physicsClientId=self.cid),
            physicsClientId=self.cid,
        )
        if d.get("color"):
            p.changeVisualShape(body, -1, rgbaColor=d["color"], physicsClientId=self.cid)
        p.changeDynamics(body, -1, lateralFriction=0.5, spinningFriction=0.01, linearDamping=0.05,
                         physicsClientId=self.cid)
        self.objects[d["entity_id"]] = {
            "body": body,
            "dims": list(d["dims"]),
            "shape": shape,
            "half_h": geom.half_h_vertical,
            "geometry": geom,
            "attributes": d.get("attributes", {}),
            "rest_z_table": TABLE_TOP_Z + geom.half_h_vertical,
            "spawn_xy": [float(d["xy"][0]), float(d["xy"][1])],
        }

    # ---------- action budget ----------
    def start_action(self, timeout_s: float, label: str = ""):
        self._action_steps_left = max(1, int(timeout_s / SIM_DT))
        self._action_seconds_spent = 0.0
        self._action_label = label

    def end_action(self) -> float:
        """Close the action window. No further simulated time is granted, and the
        seconds the action really consumed (internal waiting included) are
        reported so the ledger can charge them (SPEC 6.2)."""
        spent = self._action_seconds_spent
        self._action_steps_left = 0
        self._action_seconds_spent = 0.0
        self._action_label = ""
        return round(spent, 3)

    @property
    def action_in_progress(self) -> bool:
        return self._action_steps_left > 0

    @contextlib.contextmanager
    def _sub_budget(self, seconds: float, label: str):
        """Spend at most `seconds` of the remaining action budget, then hand the
        rest back: an internal wait must not eat the next stage's allowance."""
        saved, saved_label = self._action_steps_left, self._action_label
        cap = max(1, int(seconds / SIM_DT))
        self._action_steps_left = min(saved, cap) if saved > 0 else cap
        granted = self._action_steps_left
        self._action_label = label
        try:
            yield
        finally:
            spent = granted - self._action_steps_left
            self._action_steps_left = max(0, saved - spent) if saved > 0 else saved
            self._action_label = saved_label

    def _tick(self, n: int = 1):
        if self._action_steps_left <= 0:
            raise SimTimeout(f"{self._action_label or 'action'} exceeded its sim-time budget")
        steps = min(n, self._action_steps_left)
        for _ in range(steps):
            p.stepSimulation(physicsClientId=self.cid)
            self.sim_time += SIM_DT
        self._action_steps_left -= steps
        self._action_seconds_spent += steps * SIM_DT
        if self._action_steps_left <= 0:
            raise SimTimeout(f"{self._action_label or 'action'} exceeded its sim-time budget")

    # ---------- low-level control (motion primitives; not exposed to the LLM) ----------
    def reset_arm(self, quiet: bool = False):
        """Teleport to home. Only legal at scene build or an explicit episode
        reset: an instantaneous joint change during execution drags objects
        through the solver (SPEC 12.1.5)."""
        if not self._teleport_allowed and not quiet:
            raise RuntimeError("reset_arm teleports joints; allowed only outside execution")
        for i, j in enumerate(ARM_JOINTS):
            p.resetJointState(self.robot, j, HOME_Q[i], physicsClientId=self.cid)
        for j in FINGER_JOINTS:
            p.resetJointState(self.robot, j, MAX_FINGER_OPEN, physicsClientId=self.cid)
        self._engage_home_motors()
        self.hold_position(0.5)

    def begin_execution(self):
        """Execution is underway: joint teleportation is no longer legal."""
        self._teleport_allowed = False

    def end_execution(self):
        self._teleport_allowed = True

    def _engage_home_motors(self):
        p.setJointMotorControlArray(
            self.robot, ARM_JOINTS, p.POSITION_CONTROL,
            targetPositions=list(HOME_Q), forces=[JOINT_MAX_FORCE] * 7, physicsClientId=self.cid,
        )

    def hold_position(self, seconds: float):
        """Hold the current commanded posture under active motors for `seconds`
        of simulated time (the arm must not free-fall while things settle)."""
        cur = [p.getJointState(self.robot, j, physicsClientId=self.cid)[0] for j in ARM_JOINTS]
        try:
            with self._sub_budget(seconds, "hold_position"):
                while self._action_steps_left > 0:
                    p.setJointMotorControlArray(self.robot, ARM_JOINTS, p.POSITION_CONTROL,
                                                targetPositions=cur, forces=[JOINT_MAX_FORCE] * 7,
                                                physicsClientId=self.cid)
                    self._tick()
        except SimTimeout:
            pass

    def open_gripper(self):
        self._fingers([MAX_FINGER_OPEN, MAX_FINGER_OPEN], 20.0)

    def close_gripper(self):
        self._fingers([0.0, 0.0], FINGER_CLOSE_FORCE)

    def _fingers(self, targets, force):
        p.setJointMotorControlArray(self.robot, FINGER_JOINTS, p.POSITION_CONTROL,
                                    targetPositions=targets, forces=[force, force],
                                    physicsClientId=self.cid)

    def ik(self, xyz, orn):
        """Shadow-state IK: solve from the HOME seed, then restore the real
        state. Seeding from an arbitrary current posture makes PyBullet's DLS
        converge to branches that violate joint4's [-pi, 0] limit."""
        saved = [p.getJointState(self.robot, j, physicsClientId=self.cid)[0] for j in ARM_JOINTS + FINGER_JOINTS]
        for i, j in enumerate(ARM_JOINTS):
            p.resetJointState(self.robot, j, HOME_Q[i], physicsClientId=self.cid)
        for j in FINGER_JOINTS:
            p.resetJointState(self.robot, j, MAX_FINGER_OPEN, physicsClientId=self.cid)
        sol = self._ik_raw(xyz, orn)
        if not self._within_limits(sol):
            for yaw in (0.5, -0.5, 1.0, -1.0, 1.6, -1.6, 2.4, -2.4):
                q = self._rotate_orn_about_z(np.array(orn, dtype=float), yaw)
                sol = self._ik_raw(xyz, q)
                if self._within_limits(sol):
                    break
        for j, v in zip(ARM_JOINTS + FINGER_JOINTS, saved):
            p.resetJointState(self.robot, j, v, physicsClientId=self.cid)
        return sol

    def _ik_raw(self, xyz, orn):
        return p.calculateInverseKinematics(
            self.robot, EE_LINK, xyz, orn,
            maxNumIterations=500, residualThreshold=1e-6, physicsClientId=self.cid,
        )[:7]

    def _within_limits(self, sol) -> bool:
        for v, lo, hi in zip(sol, self._joint_lower, self._joint_upper):
            if v < lo - 1e-3 or v > hi + 1e-3:
                return False
        return True

    @staticmethod
    def _rotate_orn_about_z(q, yaw):
        half = yaw / 2.0
        dq = np.array([0.0, 0.0, math.sin(half), math.cos(half)])
        q = np.array(q, dtype=float)
        x1, y1, z1, w1 = dq
        x2, y2, z2, w2 = q
        return (
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 + y1 * w2 + z1 * x2 - x1 * z2,
            w1 * z2 + z1 * w2 + x1 * y2 - y1 * x2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        )

    def reachable(self, xyz, orn, tcp_tol: float = MOVE_TCP_TOL_M - SERVO_SETTLE_ALLOWANCE_M) -> bool:
        """Kinematic reachability probe with the *execution* criterion.

        Solves in shadow state and measures the residual the IK reports, using the
        same tolerance `move_ee` applies less the measured settling allowance: a
        claim about a waypoint must not be looser than the action that follows it,
        or a candidate slot is offered and then contradicted mid-motion (SPEC 5.5).
        """
        sol = self.ik(xyz, orn)
        if not self._within_limits(sol):
            return False
        saved = [p.getJointState(self.robot, j, physicsClientId=self.cid)[0] for j in ARM_JOINTS + FINGER_JOINTS]
        for j, v in zip(ARM_JOINTS, sol):
            p.resetJointState(self.robot, j, float(v), physicsClientId=self.cid)
        pos, _ = self.ee_pose()
        for j, v in zip(ARM_JOINTS + FINGER_JOINTS, saved):
            p.resetJointState(self.robot, j, v, physicsClientId=self.cid)
        self._engage_current_motors()
        return float(np.linalg.norm(pos - np.asarray(xyz, dtype=float))) <= tcp_tol

    def _engage_current_motors(self):
        cur = [p.getJointState(self.robot, j, physicsClientId=self.cid)[0] for j in ARM_JOINTS]
        p.setJointMotorControlArray(self.robot, ARM_JOINTS, p.POSITION_CONTROL,
                                    targetPositions=cur, forces=[JOINT_MAX_FORCE] * 7,
                                    physicsClientId=self.cid)

    def move_joints(self, target, timeout_s=3.0, tol=MOVE_TOL_RAD):
        target = np.array(target[:7])
        with self._sub_budget(timeout_s, "move_joints"):
            while self._action_steps_left > 0:
                p.setJointMotorControlArray(self.robot, ARM_JOINTS, p.POSITION_CONTROL,
                                            targetPositions=target.tolist(), forces=[JOINT_MAX_FORCE] * 7,
                                            physicsClientId=self.cid)
                try:
                    self._tick()
                except SimTimeout:
                    return False
                cur = np.array([p.getJointState(self.robot, j, physicsClientId=self.cid)[0] for j in ARM_JOINTS])
                if np.max(np.abs(cur - target)) < tol:
                    return True
        return False

    def move_ee(self, xyz, orn, timeout_s=3.0, tol=MOVE_TOL_RAD, tcp_tol=MOVE_TCP_TOL_M):
        """Servo to the IK solution, then verify the TCP actually arrived:
        joint convergence alone hides unreachable targets."""
        sol = self.ik(xyz, orn)
        if not self.move_joints(sol, timeout_s=timeout_s, tol=tol):
            self._last_tcp_err = float("inf")
            return False
        pos, _ = self.ee_pose()
        err = float(np.linalg.norm(pos - np.asarray(xyz, dtype=float)))
        self._last_tcp_err = err
        return err <= tcp_tol

    def move_ee_straight(self, xyz, orn, waypoint_m: float = TRANSFER_WAYPOINT_M,
                         timeout_s: float = 1.2, tol=MOVE_TOL_RAD, tcp_tol=MOVE_TCP_TOL_M) -> bool:
        """Straight-line motion in `waypoint_m` steps.

        Used wherever the hand is carrying something: the postcondition a skill
        claims is about the *object*, so letting the grip slip on the way there
        would make the reported candidate position a claim about the palm
        (SPEC 5.5, 12.1.5)."""
        start, _ = self.ee_pose()
        delta = np.asarray(xyz, dtype=float) - np.asarray(start, dtype=float)
        n = max(1, int(math.ceil(float(np.linalg.norm(delta)) / max(1e-3, float(waypoint_m)))))
        for i in range(1, n + 1):
            waypoint = start + delta * (i / n)
            if not self.move_ee(waypoint.tolist(), orn, timeout_s=timeout_s, tol=tol, tcp_tol=tcp_tol):
                return False
        return True

    def fingers_clear_of(self, entity_id: str) -> bool:
        """True when no finger link is in contact with that body any more."""
        return self.objects[entity_id]["body"] not in self.held_bodies()

    def wait_until_rest(self, entity_id: str, max_s: float = 2.5,
                        speed_tol: float = 0.02, ang_tol: float = 0.2):
        """Sim-time bounded wait until the object stops moving/spinning."""
        stable_needed = int(0.25 / SIM_DT)
        stable = 0
        body = self.objects[entity_id]["body"]
        try:
            with self._sub_budget(max_s, "wait_until_rest"):
                while self._action_steps_left > 0:
                    self._tick()
                    lin, ang = p.getBaseVelocity(body, physicsClientId=self.cid)
                    if np.linalg.norm(lin) < speed_tol and np.linalg.norm(ang) < ang_tol:
                        stable += 1
                        if stable > stable_needed:
                            return True
                    else:
                        stable = 0
        except SimTimeout:
            return False
        return False

    def settle(self, seconds: float):
        """Advance simulated time. When an action budget is active this consumes
        it, so internal waiting is inside the per-skill timeout (SPEC 6.2)."""
        steps = max(1, int(seconds / SIM_DT))
        if self._action_steps_left > 0:
            try:
                self._tick(steps)
            except SimTimeout:
                pass
            return
        for _ in range(steps):
            p.stepSimulation(physicsClientId=self.cid)
            self.sim_time += SIM_DT

    # ---------- observations ----------
    def ee_pose(self):
        ls = p.getLinkState(self.robot, EE_LINK, physicsClientId=self.cid)
        return np.array(ls[4]), ls[5]

    def object_pose(self, entity_id):
        pos, orn = p.getBasePositionAndOrientation(self.objects[entity_id]["body"],
                                                   physicsClientId=self.cid)
        return np.array(pos), np.array(orn)  # xyzw

    def object_velocity(self, entity_id):
        lin, ang = p.getBaseVelocity(self.objects[entity_id]["body"], physicsClientId=self.cid)
        return np.array(lin), np.array(ang)

    def geometry(self, entity_id) -> GeometrySpec:
        return self.objects[entity_id]["geometry"]

    def footprint_half_xy(self, entity_id) -> tuple[float, float]:
        """Measured horizontal half-extents in the measured orientation."""
        geom = self.geometry(entity_id)
        _, orn = self.object_pose(entity_id)
        from .contracts import footprint_half_xy_from_quat

        return footprint_half_xy_from_quat(geom, tuple(orn))

    def contact_partners(self, entity_id: str) -> list[str]:
        """Logical ids of what this body measurably touches (excluding the
        robot). Returns [] for genuine free flight; callers must not confuse
        that with 'unknown'."""
        body = self.objects[entity_id]["body"]
        partners = set()
        for c in p.getContactPoints(bodyA=body, physicsClientId=self.cid):
            other = c[2]
            if other == -1 or other == self.robot or other == self.pedestal:
                continue
            if other == self.table:
                partners.add("table")
                continue
            eid = self.entity_by_body(other)
            if eid:
                partners.add(eid)
                continue
            tid = self.target_by_body(other)
            if tid:
                partners.add(tid)
        return sorted(partners)

    def touchdown_contact(self, entity_id: str, proximity_m: float = 0.003,
                          min_vertical_normal: float = 0.7) -> bool:
        """True when something within `proximity_m` supports this body from below:
        the descent has found the surface to release onto. A wall brush is sideways
        and does not count, or a slot next to a wall would be 'served' early with
        the object still in mid-air.

        `getContactPoints` in this pybullet build only reports resolved contacts, and
        the solver reports a touch at ~1e-5 m penetration, so proximity is queried
        per body pair instead of reading the contact cache.
        """
        body = self.objects[entity_id]["body"]
        for other in range(p.getNumBodies(physicsClientId=self.cid)):
            if other in (-1, body, self.robot, self.pedestal):
                continue
            for c in p.getClosestPoints(bodyA=body, bodyB=other, distance=proximity_m,
                                        physicsClientId=self.cid):
                if float(c[8]) > proximity_m:
                    continue
                normal = np.asarray(c[7], dtype=float)
                if abs(float(normal[2])) >= min_vertical_normal:
                    return True
        return False

    def target_by_body(self, body_id):
        for tid, t in self.trays.items():
            if body_id in t["bodies"]:
                return tid
        return None

    def held_bodies(self):
        held = set()
        for j in FINGER_JOINTS:
            for c in p.getContactPoints(self.robot, linkIndexA=j, physicsClientId=self.cid):
                if c[2] != -1 and c[2] != self.table:
                    held.add(c[2])
        return held

    def entity_by_body(self, body_id):
        for eid, d in self.objects.items():
            if d["body"] == body_id:
                return eid
        return None

    def grasp_candidates(self, entity_id, max_n: int = 3):
        """Deterministic candidate grasp targets around the observed top pose.
        Candidate 0 is the observed centre; the others are small lateral offsets
        the *model* may select (SPEC 5.5: candidate choice is a model decision)."""
        pos, orn = self.object_pose(entity_id)
        geom = self.geometry(entity_id)
        hx, hy = geom.footprint_half_xy_at_zero_yaw
        cands = [pos.copy()]
        step = min(0.008, max(hx, hy) * 0.4)
        for dx, dy in [(step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step)]:
            cands.append(pos + np.array([dx, dy, 0.0]))
        return cands[:max_n]

    # ---------- environment side (never reached from a SkillCall) ----------
    def apply_environment_impulse(self, entity_id: str, impulse_xy: list[float],
                                  duration_s: float = 0.05) -> dict:
        """A real, declared physical disturbance applied over simulated time.
        Returns the measured evidence of what it did; it never edits any skill
        result (SPEC 8 fault_injection, 10.3).

        The Python bindings expose no `applyExternalImpulse`, so the declared
        impulse is applied as what it *is*: a constant force of impulse/duration
        held for `duration_s` of simulated time and then released. The body is
        never moved or re-velocity-set, which would be the teleporting this
        protocol forbids (SPEC 12.1.5)."""
        body = self.objects[entity_id]["body"]
        before, _ = self.object_pose(entity_id)
        before = before.copy()
        steps = max(1, int(duration_s / SIM_DT))
        force = [float(v) / max(1e-6, duration_s) for v in impulse_xy] + [0.0]
        pos = list(before)
        try:
            for _ in range(steps):
                pos, _ = p.getBasePositionAndOrientation(body, physicsClientId=self.cid)
                p.applyExternalForce(body, -1, force, pos, p.WORLD_FRAME, physicsClientId=self.cid)
                self.settle(SIM_DT)
        finally:
            pos, _ = p.getBasePositionAndOrientation(body, physicsClientId=self.cid)
            p.applyExternalForce(body, -1, [0.0, 0.0, 0.0], pos, p.WORLD_FRAME,
                                 physicsClientId=self.cid)
        self.settle(0.6)
        after, orn = self.object_pose(entity_id)
        return {
            "entity_id": entity_id,
            "impulse_xy": [float(v) for v in impulse_xy],
            "duration_s": duration_s,
            "before_xy": [round(float(v), 4) for v in before[:2]],
            "after_xy": [round(float(v), 4) for v in after[:2]],
            "displacement_m": round(float(np.linalg.norm(after[:2] - before[:2])), 4),
            "sim_time": round(self.sim_time, 3),
        }

    def render(self, path, size=(640, 480)):
        view = p.computeViewMatrixFromYawPitchRoll([0.6, 0.0, 0.5], distance=1.5, yaw=55, pitch=-38, roll=0,
                                                   upAxisIndex=2, physicsClientId=self.cid)
        proj = p.computeProjectionMatrixFOV(fov=55, aspect=size[0] / size[1], nearVal=0.02, farVal=5.0,
                                            physicsClientId=self.cid)
        img = p.getCameraImage(size[0], size[1], viewMatrix=view, projectionMatrix=proj,
                               renderer=p.ER_TINY_RENDERER, physicsClientId=self.cid)
        if path:
            from PIL import Image

            Image.fromarray(np.reshape(img[2], (size[1], size[0], 4))[:, :, :3].astype(np.uint8)).save(path)
        return img

    def close(self):
        if not self._closed and p.isConnected(self.cid):
            p.disconnect(physicsClientId=self.cid)
        self._closed = True
