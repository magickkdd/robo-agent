"""W1 physical manipulation baseline.

Scene: fixed Franka Panda (pybullet_data URDF), custom tabletop with three shallow
trays (left/middle/right), three rigid bodies (cube, cuboid, upright cylinder).

Scripted sequence (hand-written goal order, no planner yet):
    pick cube      -> place tray_left
    pick cylinder  -> place tray_right
    pick cuboid    -> place tray_middle

All grasps use physics contact + gripper constraint force. No teleporting objects,
no fixed-constraint snapping (spec 6.3). Grasp verification = lifted height + follows
end-effector while translating (spec 11.2).

Outputs: outputs/w1_baseline/run_report.json + key frames PNG.
"""
import json
import math
import os
import sys

import numpy as np
import pybullet as p
import pybullet_data

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "w1_baseline")
os.makedirs(OUT_DIR, exist_ok=True)

TABLE_TOP_Z = 0.62
SIM_DT = 1.0 / 240.0
EE_LINK = 11  # panda_grasptarget_hand

ARM_JOINTS = list(range(7))
FINGER_JOINTS = [9, 10]
MAX_FINGER_OPEN = 0.04  # per finger; 0.08 m total opening

JOINT_KP = 0.35
JOINT_MAX_FORCE = 240.0
FINGER_CLOSE_FORCE = 60.0
GRASP_FRICTION = 2.0

LIFT_HEIGHT = 0.09  # grasp-verification lift above support
APPROACH_CLEARANCE = 0.14


class Scene:
    def __init__(self, seed=0, gui=False):
        self.seed = seed
        if gui:
            self.cid = p.connect(p.GUI)
        else:
            self.cid = p.connect(p.DIRECT)
        assert self.cid >= 0
        p.resetSimulation()
        p.setGravity(0, 0, -9.81)
        p.setTimeStep(SIM_DT)
        p.setPhysicsEngineParameter(numSolverIterations=150)
        p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"))

        # Robot on a fixed pedestal; base_z=0.30 keeps all task points inside the
        # Panda workspace (empirically verified, see work/w1_ik_grid results).
        self.robot = p.loadURDF(
            os.path.join(pybullet_data.getDataPath(), "franka_panda", "panda.urdf"),
            basePosition=[0.05, 0.0, 0.30],
            useFixedBase=True,
        )
        ped_col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.09, 0.09, 0.15])
        p.createMultiBody(0, ped_col, basePosition=[0.05, 0.0, 0.15])
        for j in FINGER_JOINTS:
            p.changeDynamics(self.robot, j, lateralFriction=GRASP_FRICTION)
        for j in ARM_JOINTS:
            p.changeDynamics(self.robot, j, linearDamping=0.0, angularDamping=0.0)
        self.reset_arm()

        # Table: solid static box, top surface at TABLE_TOP_Z
        half = [0.45, 0.5, TABLE_TOP_Z / 2]
        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
        self.table = p.createMultiBody(
            baseMass=0.0, baseCollisionShapeIndex=col, basePosition=[0.78, 0.0, TABLE_TOP_Z / 2]
        )
        p.changeDynamics(self.table, -1, lateralFriction=0.6)

        self.trays = {}
        self._make_tray("tray_left", [0.62, -0.30])
        self._make_tray("tray_middle", [0.62, 0.0])
        self._make_tray("tray_right", [0.62, 0.30])

        self.objects = {}
        self._make_objects()

    # ---------- scene construction ----------
    def _make_tray(self, name, center_xy, inner=0.075, wall_h=0.025, wall_t=0.008):
        """Shallow open tray built from 5 static boxes; floor top at TABLE_TOP_Z."""
        cid_floor = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + wall_t, inner + wall_t, 0.004])
        floor = p.createMultiBody(0, cid_floor, basePosition=[center_xy[0], center_xy[1], TABLE_TOP_Z + 0.004])
        cid_wall_ns = p.createCollisionShape(p.GEOM_BOX, halfExtents=[inner + wall_t, wall_t, wall_h / 2])
        cid_wall_ew = p.createCollisionShape(p.GEOM_BOX, halfExtents=[wall_t, inner, wall_h / 2])
        z = TABLE_TOP_Z + 0.008 + wall_h / 2
        walls = [
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] - inner - wall_t / 2, z]),
            p.createMultiBody(0, cid_wall_ns, basePosition=[center_xy[0], center_xy[1] + inner + wall_t / 2, z]),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] - inner - wall_t / 2, center_xy[1], z]),
            p.createMultiBody(0, cid_wall_ew, basePosition=[center_xy[0] + inner + wall_t / 2, center_xy[1], z]),
        ]
        self.trays[name] = {
            "center": [center_xy[0], center_xy[1]],
            "inner_half": inner,  # usable interior half-extent (x and y)
            "floor_top": TABLE_TOP_Z + 0.008,
            "wall_top": TABLE_TOP_Z + 0.008 + wall_h,
            "bodies": [floor] + walls,
        }

    def _make_objects(self):
        defs = [
            # (logical_id, shape, half-extents/radius+height, initial xy)
            ("obj_cube", "box", [0.025, 0.025, 0.025], [0.45, -0.10]),
            ("obj_cuboid", "box", [0.032, 0.022, 0.040], [0.45, 0.12]),
            ("obj_cylinder", "cylinder", [0.026, 0.045], [0.75, -0.05]),
        ]
        rng = np.random.default_rng(self.seed)
        for oid, shape, dims, xy in defs:
            if shape == "box":
                col = p.createCollisionShape(p.GEOM_BOX, halfExtents=dims)
                mass = 0.05
            else:
                col = p.createCollisionShape(p.GEOM_CYLINDER, radius=dims[0], height=dims[1] * 2)
                mass = 0.04
            z = TABLE_TOP_Z + dims[2] if shape == "box" else TABLE_TOP_Z + dims[1]
            body = p.createMultiBody(
                baseMass=mass,
                baseCollisionShapeIndex=col,
                basePosition=[xy[0] + rng.uniform(-0.01, 0.01), xy[1] + rng.uniform(-0.01, 0.01), z],
                baseOrientation=p.getQuaternionFromEuler([0, 0, rng.uniform(-0.2, 0.2)]),
            )
            p.changeDynamics(body, -1, lateralFriction=0.5, spinningFriction=0.01, linearDamping=0.05)
            self.objects[oid] = {"body": body, "dims": dims, "shape": shape, "initial_support": TABLE_TOP_Z}

    # ---------- low-level control ----------
    def reset_arm(self):
        home = [0.0, -0.5, 0.4, -2.1, 0.0, 1.4, 0.0]
        for i, j in enumerate(ARM_JOINTS):
            p.resetJointState(self.robot, j, home[i])
        for j in FINGER_JOINTS:
            p.resetJointState(self.robot, j, MAX_FINGER_OPEN)
        self.settle(60)

    def open_gripper(self):
        self._control_fingers([MAX_FINGER_OPEN, MAX_FINGER_OPEN], force=20)

    def close_gripper(self, width_per_finger=0.0):
        """Close with force limit; contact stalls the position-controlled fingers."""
        self._control_fingers([width_per_finger, width_per_finger], force=FINGER_CLOSE_FORCE)

    def _control_fingers(self, targets, force):
        p.setJointMotorControlArray(
            self.robot,
            FINGER_JOINTS,
            p.POSITION_CONTROL,
            targetPositions=targets,
            forces=[force, force],
        )

    def ik(self, xyz, orn):
        return p.calculateInverseKinematics(
            self.robot, EE_LINK, xyz, orn,
            maxNumIterations=500, residualThreshold=1e-6,
        )[:7]

    def ee_pose(self):
        ls = p.getLinkState(self.robot, EE_LINK)
        return np.array(ls[4]), ls[5]

    def move_joints(self, target, timeout_s=3.0, tol=8e-3):
        """Position-control servos toward joint target; returns True when converged."""
        steps = int(timeout_s / SIM_DT)
        target = np.array(target[:7])
        for _ in range(steps):
            p.setJointMotorControlArray(
                self.robot, ARM_JOINTS, p.POSITION_CONTROL,
                targetPositions=target.tolist(),
                forces=[JOINT_MAX_FORCE] * 7,
            )
            p.stepSimulation()
            cur = np.array([p.getJointState(self.robot, j)[0] for j in ARM_JOINTS])
            if np.max(np.abs(cur - target)) < tol:
                return True
        return False

    def move_ee(self, xyz, orn, **kw):
        sol = self.ik(xyz, orn)
        return self.move_joints(sol, **kw)

    def settle(self, n):
        for _ in range(n):
            p.stepSimulation()

    # ---------- observations (privileged during W1) ----------
    def object_pose(self, oid):
        pos, orn = p.getBasePositionAndOrientation(self.objects[oid]["body"])
        return np.array(pos), orn

    def object_velocity(self, oid):
        return np.array(p.getBaseVelocity(self.objects[oid]["body"])[0])

    def held_bodies(self):
        """Bodies in contact with either finger link."""
        held = set()
        for j in FINGER_JOINTS:
            for c in p.getContactPoints(self.robot, linkIndexA=j):
                if c[2] != -1:
                    held.add(c[2])
        return held

    def render(self, path):
        view = p.computeViewMatrixFromYawPitchRoll([0.6, 0.0, 0.5], distance=1.5, yaw=55, pitch=-38, roll=0, upAxisIndex=2)
        proj = p.computeProjectionMatrixFOV(fov=55, aspect=4 / 3, nearVal=0.02, farVal=5.0)
        img = p.getCameraImage(640, 480, viewMatrix=view, projectionMatrix=proj, renderer=p.ER_TINY_RENDERER)
        from PIL import Image

        Image.fromarray(np.reshape(img[2], (480, 640, 4))[:, :, :3].astype(np.uint8)).save(path)


# ---------- skills (primitive chains for W1) ----------
def grasp_pose_for(scene, oid, grasp_offset=np.zeros(3)):
    """Pre-grasp TCP pose: directly above object center, tool pointing down."""
    pos, _ = scene.object_pose(oid)
    target = np.array([pos[0], pos[1], pos[2]]) + grasp_offset
    pre = target + np.array([0, 0, APPROACH_CLEARANCE])
    orn = p.getQuaternionFromEuler([math.pi, 0, 0])  # fingers point down
    return pre, target, orn


def pick(scene, oid, injected_offset=None):
    """pick primitive chain. Returns (ok, info). injected_offset perturbs the
    descent target for fault-injection experiments."""
    pos0, _ = scene.object_pose(oid)
    support_z = pos0[2]
    pre, grasp, orn = grasp_pose_for(scene, oid)
    if injected_offset is not None:
        grasp = grasp + np.asarray(injected_offset)

    scene.reset_arm()  # normalize IK seed before every pick
    scene.open_gripper()
    if not scene.move_ee(pre.tolist(), orn, timeout_s=2.5):
        return False, {"stage": "pregrasp", "reason": "ik/convergence"}
    if not scene.move_ee(grasp.tolist(), orn, timeout_s=2.0):
        return False, {"stage": "grasp_reach", "reason": "ik/convergence"}
    scene.close_gripper()
    scene.settle(int(0.25 / SIM_DT))

    # Lift
    lift_xyz = [grasp[0], grasp[1], grasp[2] + LIFT_HEIGHT]
    if not scene.move_ee(lift_xyz, orn, timeout_s=2.0):
        return False, {"stage": "lift", "reason": "ik/convergence"}

    # Verify: lifted above original support and follows ee laterally
    pos1, _ = scene.object_pose(oid)
    lift_gain = pos1[2] - support_z
    x0, y0 = scene.ee_pose()[0][:2]
    scene.move_ee([x0 + 0.06, y0 - 0.04, lift_xyz[2]], orn, timeout_s=1.2)
    pos2, _ = scene.object_pose(oid)
    follow_err = float(np.linalg.norm(pos2[:2] - scene.ee_pose()[0][:2]))
    ok = lift_gain > 0.03 and follow_err < 0.03 and (scene.objects[oid]["body"] in scene.held_bodies())
    info = {
        "stage": "grasp_verify",
        "lift_gain_m": round(float(lift_gain), 4),
        "follow_err_m": round(follow_err, 4),
        "in_contact": scene.objects[oid]["body"] in scene.held_bodies(),
    }
    return ok, info


def place(scene, oid, tray_id, slot_xy=None):
    """place primitive chain: transfer above tray, descend, release, retreat."""
    ok_held = scene.objects[oid]["body"] in scene.held_bodies()
    if not ok_held:
        return False, {"stage": "place_prereq", "reason": "object not held"}
    tray = scene.trays[tray_id]
    cx, cy = slot_xy if slot_xy else tray["center"]
    drop_z = tray["floor_top"] + 0.05
    orn = p.getQuaternionFromEuler([math.pi, 0, 0])
    above = [cx, cy, drop_z + 0.08]
    if not scene.move_ee(above, orn, timeout_s=3.0):
        return False, {"stage": "transfer", "reason": "ik/convergence"}
    if not scene.move_ee([cx, cy, drop_z], orn, timeout_s=2.0):
        return False, {"stage": "descend", "reason": "ik/convergence"}
    scene.open_gripper()
    scene.settle(int(0.4 / SIM_DT))
    scene.move_ee([cx, cy, drop_z + 0.10], orn, timeout_s=1.5)  # retreat
    scene.settle(int(0.6 / SIM_DT))

    pos, _ = scene.object_pose(oid)
    vel = np.linalg.norm(scene.object_velocity(oid))
    margin = 0.005  # spec 11.2: footprint inside target polygon minus 5mm edge margin
    half_h = scene.objects[oid]["dims"][2] if scene.objects[oid]["shape"] == "box" else scene.objects[oid]["dims"][1]
    rest_z = tray["floor_top"] + half_h  # expected center height when supported by tray floor
    supported = abs(pos[2] - rest_z) < 0.01  # spec 11.2: support height error <= 1cm
    in_tray = (
        abs(pos[0] - cx) < tray["inner_half"] - margin
        and abs(pos[1] - cy) < tray["inner_half"] - margin
        and supported
        and scene.objects[oid]["body"] not in scene.held_bodies()
    )
    return in_tray and vel < 0.02, {
        "stage": "place_verify",
        "pos": [round(float(v), 3) for v in pos],
        "speed": round(float(vel), 4),
    }


def jsonable(o):
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)


def main():
    scene = Scene(seed=7)
    report = {"episode": "w1_baseline", "seed": 7, "steps": []}

    def snap(name):
        scene.render(os.path.join(OUT_DIR, f"{name}.png"))

    snap("00_initial")

    seq = [
        ("obj_cube", "tray_left"),
        ("obj_cylinder", "tray_right"),
        ("obj_cuboid", "tray_middle"),
    ]
    all_ok = True
    for i, (oid, tray) in enumerate(seq, 1):
        ok_p, info_p = pick(scene, oid)
        report["steps"].append({"action": "pick", "object": oid, "ok": ok_p, **info_p})
        print(f"pick {oid}: ok={ok_p} {info_p}")
        all_ok &= ok_p
        if not ok_p:
            snap(f"fail_pick_{i}")
            break
        ok_d, info_d = place(scene, oid, tray)
        report["steps"].append({"action": "place", "object": oid, "target": tray, "ok": ok_d, **info_d})
        print(f"place {oid} -> {tray}: ok={ok_d} {info_d}")
        all_ok &= ok_d
        snap(f"{i:02d}_after_{oid}")

    snap("99_final")
    # Final physical check: every object inside its assigned tray, at rest, not held
    final = {}
    for oid, tray in seq:
        pos, _ = scene.object_pose(oid)
        t = scene.trays[tray]
        half_h = scene.objects[oid]["dims"][2] if scene.objects[oid]["shape"] == "box" else scene.objects[oid]["dims"][1]
        rest_z = t["floor_top"] + half_h
        final[oid] = bool(
            abs(pos[0] - t["center"][0]) < t["inner_half"]
            and abs(pos[1] - t["center"][1]) < t["inner_half"]
            and abs(pos[2] - rest_z) < 0.01
            and scene.objects[oid]["body"] not in scene.held_bodies()
        )
    report["final_objects_in_tray"] = final
    report["success"] = bool(all_ok and all(final.values()))
    with open(os.path.join(OUT_DIR, "run_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=jsonable)
    print("SUCCESS" if report["success"] else "FAILED", final)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
