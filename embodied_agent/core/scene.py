"""PyBullet physics backend for S1 (promoted from work/w1_baseline.py).

The simulator owns physics stepping, sensors and the robot action interface
(spec section 7). Skill executors and the runtime talk to this class only;
nothing above it imports pybullet directly.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pybullet as p
import pybullet_data

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


class PhysicsScene:
    def __init__(self, seed: int = 0, object_layout: list[dict] | None = None, gui: bool = False):
        self.sim_time = 0.0
        self.cid = p.connect(p.GUI if gui else p.DIRECT)
        assert self.cid >= 0
        p.resetSimulation()
        p.setGravity(0, 0, -9.81)
        p.setTimeStep(SIM_DT)
        p.setPhysicsEngineParameter(numSolverIterations=150)
        p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"))

        self.robot = p.loadURDF(
            os.path.join(pybullet_data.getDataPath(), "franka_panda", "panda.urdf"),
            basePosition=[0.05, 0.0, 0.30],
            useFixedBase=True,
        )
        ped = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.09, 0.09, 0.15])
        p.createMultiBody(0, ped, basePosition=[0.05, 0.0, 0.15])
        for j in FINGER_JOINTS:
            p.changeDynamics(self.robot, j, lateralFriction=GRASP_FRICTION)
        self.reset_arm()

        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.45, 0.5, TABLE_TOP_Z / 2])
        self.table = p.createMultiBody(0, col, basePosition=[0.78, 0.0, TABLE_TOP_Z / 2])
        p.changeDynamics(self.table, -1, lateralFriction=0.6)

        self.trays = {}
        for name, cx, cy in [("tray_left", 0.66, -0.32), ("tray_middle", 0.66, 0.0), ("tray_right", 0.66, 0.32)]:
            self._make_tray(name, [cx, cy])

        self.objects: dict[str, dict] = {}
        layout = object_layout or [
            {"entity_id": "obj_cube", "shape": "box", "dims": [0.025, 0.025, 0.025], "xy": [0.45, -0.10], "color": [0.85, 0.1, 0.1, 1], "attributes": {"color": "red", "shape": "cube"}},
            {"entity_id": "obj_cuboid", "shape": "box", "dims": [0.032, 0.022, 0.040], "xy": [0.45, 0.12], "color": [0.1, 0.7, 0.2, 1], "attributes": {"color": "green", "shape": "cuboid"}},
            {"entity_id": "obj_cylinder", "shape": "cylinder", "dims": [0.026, 0.045], "xy": [0.75, -0.05], "color": [0.1, 0.2, 0.85, 1], "attributes": {"color": "blue", "shape": "cylinder"}},
        ]
        rng = np.random.default_rng(seed)
        for d in layout:
            self._make_object(d, rng)

        self.sim_time = 0.0

        self._joint_lower = [p.getJointInfo(self.robot, j)[8] for j in ARM_JOINTS]
        self._joint_upper = [p.getJointInfo(self.robot, j)[9] for j in ARM_JOINTS]
        self._joint_ranges = [u - l for l, u in zip(self._joint_lower, self._joint_upper)]

    # ---------- construction ----------
    def _make_tray(self, name, center_xy, inner=0.135, wall_h=0.025, wall_t=0.008):
        cid_floor = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + wall_t, inner + wall_t, 0.004])
        floor = p.createMultiBody(0, cid_floor, basePosition=[center_xy[0], center_xy[1], TABLE_TOP_Z + 0.004])
        # walls form a border ring: N/S walls long in x, E/W walls long in y
        cid_wall_ns = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + 2 * wall_t, wall_t, wall_h / 2])
        cid_wall_ew = p.createCollisionShape(p.GEOM_BOX, halfExtents=[wall_t, inner, wall_h / 2])
        z = TABLE_TOP_Z + 0.008 + wall_h / 2
        walls = [
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] - inner - wall_t / 2, z]),
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] + inner + wall_t / 2, z]),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] - inner - wall_t / 2, center_xy[1], z]),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] + inner + wall_t / 2, center_xy[1], z]),
        ]
        self.trays[name] = {
            "target_id": name,
            "label": name,
            "center": [center_xy[0], center_xy[1]],
            "inner_half": inner,
            "floor_top": TABLE_TOP_Z + 0.008,
            "wall_top": TABLE_TOP_Z + 0.008 + wall_h,
            "bodies": [floor] + walls,
        }

    def _make_object(self, d, rng):
        if d["shape"] in ("box", "cuboid"):
            col = p.createCollisionShape(p.GEOM_BOX, halfExtents=d["dims"])
            z = TABLE_TOP_Z + d["dims"][2]
            half_h = d["dims"][2]
            mass = 0.05
        else:
            col = p.createCollisionShape(p.GEOM_CYLINDER, radius=d["dims"][0], height=d["dims"][1] * 2)
            z = TABLE_TOP_Z + d["dims"][1]
            half_h = d["dims"][1]
            mass = 0.04
        body = p.createMultiBody(
            baseMass=mass,
            baseCollisionShapeIndex=col,
            basePosition=[d["xy"][0] + rng.uniform(-0.01, 0.01), d["xy"][1] + rng.uniform(-0.01, 0.01), z],
            baseOrientation=p.getQuaternionFromEuler([0, 0, rng.uniform(-0.2, 0.2)]),
        )
        p.changeVisualShape(body, -1, rgbaColor=d.get("color"))
        p.changeDynamics(body, -1, lateralFriction=0.5, spinningFriction=0.01, linearDamping=0.05)
        self.objects[d["entity_id"]] = {
                "body": body,
                "dims": d["dims"],
                "shape": d["shape"],
                "half_h": half_h,
                "attributes": d.get("attributes", {}),
                "support": TABLE_TOP_Z,
            }

    # ---------- low-level control (motion primitives; not exposed to the LLM) ----------
    def reset_arm(self):
        """Return the arm to the home pose. Teleport + immediately engage
        position control: without active motors the arm free-falls under
        gravity during settling and sweeps through the workspace."""
        for i, j in enumerate(ARM_JOINTS):
            p.resetJointState(self.robot, j, HOME_Q[i])
        for j in FINGER_JOINTS:
            p.resetJointState(self.robot, j, MAX_FINGER_OPEN)
        p.setJointMotorControlArray(
            self.robot, ARM_JOINTS, p.POSITION_CONTROL,
            targetPositions=list(HOME_Q), forces=[JOINT_MAX_FORCE] * 7,
        )
        self.settle(60)

    def open_gripper(self):
        self._fingers([MAX_FINGER_OPEN, MAX_FINGER_OPEN], 20.0)

    def close_gripper(self):
        self._fingers([0.0, 0.0], FINGER_CLOSE_FORCE)

    def _fingers(self, targets, force):
        p.setJointMotorControlArray(self.robot, FINGER_JOINTS, p.POSITION_CONTROL, targetPositions=targets, forces=[force, force])

    def ik(self, xyz, orn):
        """Shadow-state IK: solve from the HOME seed, then restore the real
        state. Seeding from an arbitrary current posture makes PyBullet's DLS
        converge to branches that violate joint4's [-pi, 0] limit, which the
        servos can never follow (spec 6.3: planning may use shadow state as
        long as the execution world is not polluted)."""
        saved = [p.getJointState(self.robot, j)[0] for j in ARM_JOINTS + FINGER_JOINTS]
        for i, j in enumerate(ARM_JOINTS):
            p.resetJointState(self.robot, j, HOME_Q[i])
        for j in FINGER_JOINTS:
            p.resetJointState(self.robot, j, MAX_FINGER_OPEN)
        sol = self._ik_raw(xyz, orn)
        if not self._within_limits(sol):
            # alternate tool yaws (parallel gripper is symmetric about tool axis)
            for yaw in (0.5, -0.5, 1.0, -1.0, 1.6, -1.6, 2.4, -2.4):
                q = self._rotate_orn_about_z(np.array(orn, dtype=float), yaw)
                sol = self._ik_raw(xyz, q)
                if self._within_limits(sol):
                    break
        for j, v in zip(ARM_JOINTS + FINGER_JOINTS, saved):
            p.resetJointState(self.robot, j, v)
        return sol

    def _ik_raw(self, xyz, orn):
        return p.calculateInverseKinematics(
            self.robot, EE_LINK, xyz, orn,
            maxNumIterations=500, residualThreshold=1e-6,
        )[:7]

    def _within_limits(self, sol) -> bool:
        for v, lo, hi in zip(sol, self._joint_lower, self._joint_upper):
            if v < lo - 1e-3 or v > hi + 1e-3:
                return False
        return True

    @staticmethod
    def _rotate_orn_about_z(q, yaw):
        # xyzw quaternion pre-multiplied by a world-z rotation of `yaw`
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

    def move_joints(self, target, timeout_s=3.0, tol=8e-3):
        steps = int(timeout_s / SIM_DT)
        target = np.array(target[:7])
        for _ in range(steps):
            p.setJointMotorControlArray(self.robot, ARM_JOINTS, p.POSITION_CONTROL, targetPositions=target.tolist(), forces=[JOINT_MAX_FORCE] * 7)
            p.stepSimulation()
            self.sim_time += SIM_DT
            cur = np.array([p.getJointState(self.robot, j)[0] for j in ARM_JOINTS])
            if np.max(np.abs(cur - target)) < tol:
                return True
        return False

    def move_ee(self, xyz, orn, timeout_s=3.0, tol=8e-3, tcp_tol=0.010):
        """Servo to the IK solution, then verify the TCP actually reached the
        target: joint convergence alone hides unreachable targets (the IK may
        return a best-effort pose tens of mm away)."""
        sol = self.ik(xyz, orn)
        if not self.move_joints(sol, timeout_s=timeout_s, tol=tol):
            return False
        pos, _ = self.ee_pose()
        import numpy as _np

        err = float(_np.linalg.norm(pos - _np.asarray(xyz, dtype=float)))
        if err > tcp_tol:
            self._last_tcp_err = err
            return False
        self._last_tcp_err = err
        return True

    def wait_until_rest(self, entity_id: str, max_s: float = 2.5,
                        speed_tol: float = 0.02, ang_tol: float = 0.2):
        """Sim-time bounded wait until the object stops moving/spinning."""
        n = int(max_s / SIM_DT)
        stable = 0
        for _ in range(n):
            p.stepSimulation()
            self.sim_time += SIM_DT
            lin, ang = p.getBaseVelocity(self.objects[entity_id]["body"])
            if np.linalg.norm(lin) < speed_tol and np.linalg.norm(ang) < ang_tol:
                stable += 1
                if stable > int(0.25 / SIM_DT):
                    return True
            else:
                stable = 0
        return False

    def settle(self, seconds: float):
        n = max(1, int(seconds / SIM_DT))
        for _ in range(n):
            p.stepSimulation()
            self.sim_time += SIM_DT

    # ---------- observations ----------
    def ee_pose(self):
        ls = p.getLinkState(self.robot, EE_LINK)
        return np.array(ls[4]), ls[5]

    def object_pose(self, entity_id):
        pos, orn = p.getBasePositionAndOrientation(self.objects[entity_id]["body"])
        return np.array(pos), np.array(orn)  # xyzw

    def object_velocity(self, entity_id):
        lin, ang = p.getBaseVelocity(self.objects[entity_id]["body"])
        return np.array(lin), np.array(ang)

    def held_bodies(self):
        held = set()
        for j in FINGER_JOINTS:
            for c in p.getContactPoints(self.robot, linkIndexA=j):
                if c[2] != -1:
                    held.add(c[2])
        return held

    def entity_by_body(self, body_id):
        for eid, d in self.objects.items():
            if d["body"] == body_id:
                return eid
        return None

    def grasp_candidates(self, entity_id, max_n: int = 3):
        """Deterministic candidate grasp targets around the observed top pose.
        Candidate 0 is the observed center; others are small lateral offsets
        used by GRASP_MISS recovery (spec 11.3)."""
        pos, orn = self.object_pose(entity_id)
        yaw = 2 * math.atan2(orn[3], orn[2]) if len(orn) == 4 else 0.0  # not used for planning precision
        cands = [pos.copy()]
        for dx, dy in [(0.008, 0.0), (-0.008, 0.0), (0.0, 0.008)]:
            cands.append(pos + np.array([dx, dy, 0.0]))
        return cands[:max_n]

    def render(self, path, size=(640, 480)):
        view = p.computeViewMatrixFromYawPitchRoll([0.6, 0.0, 0.5], distance=1.5, yaw=55, pitch=-38, roll=0, upAxisIndex=2)
        proj = p.computeProjectionMatrixFOV(fov=55, aspect=size[0] / size[1], nearVal=0.02, farVal=5.0)
        img = p.getCameraImage(size[0], size[1], viewMatrix=view, projectionMatrix=proj, renderer=p.ER_TINY_RENDERER)
        if path:
            from PIL import Image

            Image.fromarray(np.reshape(img[2], (size[1], size[0], 4))[:, :, :3].astype(np.uint8)).save(path)
        return img

    def close(self):
        if p.isConnected(self.cid):
            p.disconnect()
