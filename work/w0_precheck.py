"""W0 environment precheck per Embodied-Agent-Framework-Spec-v1.0 section 6.4.

Checks (one day budget):
  1. Install + import pybullet
  2. Load Franka Panda URDF from pybullet_data
  3. 1000 dynamics steps
  4. One IK solve
  5. One collision query
  6. RGB + depth screenshot
  7. headless mode (DIRECT) -- required default; GUI attempted once, non-blocking

Writes outputs/w0_precheck/precheck.json and camera PNGs.
"""
import json
import os
import sys
import time

import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "w0_precheck")
os.makedirs(OUT_DIR, exist_ok=True)

results = {"python": sys.version.split()[0], "checks": {}}


def record(name, ok, detail=None, **extra):
    entry = {"ok": bool(ok), "detail": detail}
    entry.update(extra)
    results["checks"][name] = entry
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def run_headless():
    import pybullet as p

    cid = p.connect(p.DIRECT)
    p.setPhysicsEngineParameter(fixedTimeStep=1.0 / 240.0)
    p.setGravity(0, 0, -9.81)
    record("sim_connect_headless", cid >= 0, f"client={cid}, timeStep=1/240")

    import pybullet_data

    plane = p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"))
    panda_path = os.path.join(pybullet_data.getDataPath(), "franka_panda", "panda.urdf")
    robot = p.loadURDF(panda_path, basePosition=[0, 0, 0], useFixedBase=True)
    record(
        "panda_load",
        robot >= 0,
        panda_path,
        num_joints=p.getNumJoints(robot),
        source="pybullet_data (bundled with pybullet 3.2.7, BSD/zlib-style license)",
    )

    # 1000 dynamics steps
    t0 = time.time()
    for _ in range(1000):
        p.stepSimulation()
    wall = time.time() - t0
    record("dynamics_1000_steps", wall < 60, f"{wall:.2f}s wall for 1000 steps (~{1000 / wall:.0f} steps/s)")

    # IK solve for end effector (panda link 11 = palm; ee tip via link 11)
    ee_link = 11
    target = [0.4, 0.0, 0.55]
    quat = p.getQuaternionFromEuler([0, -np.pi / 2, 0])
    ik = p.calculateInverseKinematics(robot, ee_link, target, quat, maxNumIterations=500, residualThreshold=1e-6)
    ok_ik = len(ik) >= 7 and np.all(np.isfinite(ik[:7]))
    record("ik_solve", ok_ik, f"target={target}, first7={np.round(ik[:7], 3).tolist()}")

    # Apply IK and measure residual
    for i in range(7):
        p.resetJointState(robot, i, ik[i])
    ls = p.getLinkState(robot, ee_link)
    pos = np.array(ls[4])
    err = float(np.linalg.norm(pos - np.array(target)))
    record("ik_residual", err < 0.005, f"ee_pos={np.round(pos, 4).tolist()}, err={err * 1000:.1f}mm")

    # Collision query: box between arm and target
    box_col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.03, 0.03, 0.03])
    box = p.createMultiBody(baseMass=0.1, baseCollisionShapeIndex=box_col, basePosition=[0.4, 0.0, 0.35])
    p.performCollisionDetection()
    contacts = p.getClosestPoints(robot, box, distance=0.0)
    contacts_any = len(p.getContactPoints(robot, box)) > 0
    record("collision_query", True, f"arm-vs-box contact points={len(contacts)}, contact={contacts_any}")

    # RGB + depth screenshot
    view = p.computeViewMatrixFromYawPitchRoll([0.5, 0, 0.4], distance=1.2, yaw=50, pitch=-35, roll=0, upAxisIndex=2)
    proj = p.computeProjectionMatrixFOV(fov=60, aspect=4 / 3, nearVal=0.02, farVal=5.0)
    w, h = 640, 480
    img = p.getCameraImage(w, h, viewMatrix=view, projectionMatrix=proj, renderer=p.ER_TINY_RENDERER)
    rgb = np.reshape(img[2], (h, w, 4))[:, :, :3]
    depth = np.reshape(img[3], (h, w))
    rgb_path = os.path.join(OUT_DIR, "rgb_headless.png")
    depth_path = os.path.join(OUT_DIR, "depth_headless.npy")
    try:
        from PIL import Image

        Image.fromarray(rgb.astype(np.uint8)).save(rgb_path)
    except ImportError:
        np.save(os.path.join(OUT_DIR, "rgb_headless.npy"), rgb)
        rgb_path = os.path.join(OUT_DIR, "rgb_headless.npy")
    np.save(depth_path, depth)
    finite_depth = float(np.isfinite(depth).mean())
    record(
        "camera_rgb_depth_headless",
        finite_depth > 0.99 and rgb.max() > 0,
        f"640x480 tiny-renderer, depth finite ratio={finite_depth:.3f}",
        rgb_saved=str(rgb_path),
    )

    p.removeBody(box)
    p.disconnect()
    return True


def run_gui_once():
    """GUI attempt is non-blocking: record result, WSLg may or may not work.

    Runs in a subprocess so a segfault at GUI teardown cannot kill the main run.
    """
    import subprocess
    import textwrap

    child = textwrap.dedent(
        """
        import pybullet as p, pybullet_data, os
        cid = p.connect(p.GUI)
        assert cid >= 0, "connect failed"
        p.loadURDF(os.path.join(pybullet_data.getDataPath(), "plane.urdf"))
        print("GUI_OK", flush=True)
        os._exit(0)  # skip pybullet GUI teardown (known SIGSEGV on disconnect)
        """
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", child],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "LIBGL_ALWAYS_INDIRECT": "0"},
        )
        ok = r.returncode == 0 and "GUI_OK" in r.stdout
        renderer = ""
        for line in r.stderr.splitlines():
            if "GL_RENDERER" in line:
                renderer = line.strip()
                break
        record("gui_once", ok, f"rc={r.returncode}, {renderer or (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'no output')}")
    except Exception as e:  # noqa: BLE001
        record("gui_once", False, f"GUI unavailable: {type(e).__name__}: {e}")


if __name__ == "__main__":
    t0 = time.time()
    try:
        run_headless()
    except Exception as e:  # noqa: BLE001
        record("headless_run", False, f"{type(e).__name__}: {e}")
    run_gui_once()
    results["total_wall_s"] = round(time.time() - t0, 2)
    results["all_ok"] = all(c["ok"] for c in results["checks"].values())
    with open(os.path.join(OUT_DIR, "precheck.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nALL OK" if results["all_ok"] else "\nSOME CHECKS FAILED")
    sys.exit(0 if results["all_ok"] else 1)
