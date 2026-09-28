"""The MetaWorld (MuJoCo) backend: one benchmark env, one official evaluator, no agent.

Why this file is small on purpose: every decision that belongs to the agent lives in
the loop that imports it (`runtime.py`, `policy.py`), and the two things this module
is allowed to know are (a) how to take one environment step and (b) how the benchmark
scores the result. SPEC-v0.2 §10 forbids an adapter from planning, retrying,
recovering, composing two verbs into one call, or handing the agent a hidden state or
an expert answer, so:

* the only controller here is a proportional servo on the gripper's Cartesian
  position plus a constant gripper effort. It has no goal, no task name, no waypoint
  list and no idea what a "peg" is for; `metaworld.policies.*` — the shipped expert
  action generators — are never imported by this package, and the fact that they
  exist is recorded in `docs/` instead of being used here;
* the official `success` / `reward` / `obj_to_target` fields returned by `env.step`
  go into `eval_view()` and nowhere else. They never enter a `WorldState`, a
  `Feedback` or a `DecisionContext` (`state.py` takes its numbers from `measured()`,
  which reads body/site poses only), so scoring cannot become a policy oracle;
* `env.observation_space` is a 39-vector that *contains the object position and the
  target position*. It is therefore never handed to anything: `measured()` re-reads
  the same numbers out of `data` under names that say which channel produced them,
  and the perception channel label decides whether the agent is allowed to see them.

Install: `/home/czx/mwvenv` (metaworld 3.0.0, mujoco 3.13.0, gymnasium 1.3.0), created
by `python -m venv` from the `embodied` conda interpreter. Run this package with that
interpreter; the desktop interpreter has no mujoco.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Optional

#: MuJoCo chooses its OpenGL platform when the module is first imported, so this has to
#: run before anything pulls `mujoco` in — including a caller that imports `metaworld`
#: first (measured: `pytest.importorskip("metaworld")` alone was enough to leave the
#: variable unset and the offscreen renderer dead). A test module or a script that
#: imports either package must set `MUJOCO_GL` at its own top, before those imports.
os.environ.setdefault("MUJOCO_GL", "osmesa")

#: The one task this adapter can name the world of, and its official sentence.
#
# Two neighbours are deliberately absent, and the reason is a measurement rather than a
# choice to skip work: this channel's vocabulary is a table of site names, and the other
# two socket-shaped tasks do not have the sites this one reads. Starting them with this
# table fails on the first observation —
#   peg-unplug-side-v3 -> KeyError "Invalid name 'pegGrasp'. Valid names: [..., 'hole',
#       'pegEnd', 'pegHead', ...]"   (no grasp site, and its goal is to get a peg *out*,
#                                    which the `entity at target` assignment cannot state)
#   assembly-v3        -> KeyError "Invalid name 'pegGrasp'. Valid names: ['RoundNut',
#       'RoundNut-8', 'pegTop', ...]" (a different object, a different site set)
# Offering them as `--task` choices would therefore advertise a run that cannot start, so
# they are recorded here as the next work package: a second vocabulary table, and for the
# unplug task an "out of" predicate the frozen `GoalAssignment` shape does not have.
TASKS: dict[str, str] = {
    "peg-insert-side-v3": "grasp the peg and push it into the hole in the side of the box",
}

#: the workspace the mocap target is clipped to, read out of the env at start-up and
#: recorded here only as a default (`mocap_low/high` are measured, not assumed)
DEFAULT_WORKSPACE = ((-0.5, 0.4, 0.05), (0.5, 1.0, 0.5))

GRASP_EFFORT = 0.6      #: positive closes the Sawyer fingers (`action[-1]`)
OPEN_EFFORT = -1.0
SERVO_GAIN = 25.0       #: the gain the benchmark's own action scale was tuned for
SERVO_TOL_M = 0.01      #: stop servoing within this Cartesian error
SETTLE_STEPS = 8        #: hold the pose so the object's velocity decays
#: The fingers are a measured distance away from the pose `servo` can reach: the hand
#: frame lands ~46 mm above the floor while its goal sits at the object's own site, so
#: a grasp needs the closing effort held *in place* for a fixed number of steps.
#: `servo` cannot supply those steps — it returns after its first iteration once it is
#: inside `tol`, and 12 steps of squeeze were measured to be the point where the peg
#: starts coming along (8 leaves it on the table).
SQUEEZE_STEPS = 20      #: a margin over the measured 12, not a tuned value


class BackendError(RuntimeError):
    """The environment itself faulted. An outcome, never a stack trace to the agent."""


class MuJoCoBackend:
    """One MetaWorld task instance. `start()` once per episode; `servo()` per skill."""

    def __init__(self, env_name: str = "peg-insert-side-v3", *, task_index: int = 0,
                 seed: int = 0, max_env_actions: int = 500, views: tuple[str, ...] = ("corner2",),
                 width: int = 480, height: int = 480, gui_sink: Any = None):
        if env_name not in TASKS:
            raise BackendError(f"unknown benchmark task {env_name!r}; this adapter offers "
                               f"{', '.join(sorted(TASKS))}")
        self.env_name = env_name
        self.task_index = int(task_index)
        self.seed = int(seed)
        self.max_env_actions = int(max_env_actions)
        self.views = tuple(views)
        self.width, self.height = int(width), int(height)
        self.gui_sink = gui_sink
        self.env = None
        self.steps = 0
        self.reward_total = 0.0
        self.last_info: dict[str, Any] = {}
        self.error: Optional[dict[str, Any]] = None
        #: why the last frame could not be rendered, if one could not. A viewer is not
        #: a source of truth, so this is recorded rather than raised.
        self.capture_error: Optional[str] = None
        self.reset_seconds = 0.0
        self.primitive_seconds = 0.0
        self._frames: dict[str, Any] = {}
        self._renderer = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> str:
        t0 = time.time()
        try:
            import metaworld
        except Exception as e:  # noqa: BLE001 - a missing optional backend is a fact
            raise BackendError(f"metaworld is not importable with this interpreter "
                               f"({type(e).__name__}: {e}). Use /home/czx/mwvenv/bin/python") from e
        ml = metaworld.MT1(self.env_name, seed=self.seed)
        cls = ml.train_classes[self.env_name]
        self.env = cls(render_mode="rgb_array", width=self.width, height=self.height)
        if self.task_index >= len(ml.train_tasks):
            raise BackendError(f"task_index {self.task_index} out of range "
                               f"({len(ml.train_tasks)} layouts in this benchmark split)")
        self.env.set_task(ml.train_tasks[self.task_index])
        self.env.reset(seed=self.seed)
        self._ml = ml
        self.steps = 0
        self.reward_total = 0.0
        self.last_info = {}
        self.reset_seconds = round(time.time() - t0, 4)
        self._frames = {}
        self.capture_all()
        return self.public_text()

    def close(self) -> None:
        self.env = None

    # ------------------------------------------------------------------- geometry
    @property
    def data(self):
        return self.env.data

    @property
    def done(self) -> bool:
        return self.env is None or self.steps >= min(self.max_env_actions,
                                                     int(getattr(self.env, "max_path_length", 500)))

    def site(self, name: str) -> tuple[float, float, float]:
        p = self.data.site(name).xpos
        return (round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4))

    def hand(self) -> tuple[float, float, float]:
        p = self.data.body("hand").xpos
        return (round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4))

    def gripper_open_m(self) -> Optional[float]:
        """Fingertip separation, the one proprioceptive fact about the hand itself.

        Read from the two pad *bodies* — this env has no fingertip sites (`leftAnchor`
        / `rightAnchor` do not exist here; measured by listing `model.nsite`), and an
        unmeasurable aperture is reported as None rather than as a number or a zero.
        """
        import numpy as np
        d = self.data
        try:
            l = d.body("leftpad").xpos
            r = d.body("rightpad").xpos
        except Exception:  # noqa: BLE001 - a different arm may not name its pads
            return None
        return round(float(np.linalg.norm(np.asarray(l) - np.asarray(r))), 4)

    def _has_site(self, name: str) -> bool:
        import mujoco
        return mujoco.mj_name2id(self.env.model, mujoco.mjtObj.mjOBJ_SITE.value, name) >= 0

    def finger_qpos_m(self) -> Optional[float]:
        """The commanded aperture joint, if this arm has one: [0, 0.04] m on `r_close`.

        `data.qpos[model.jnt_qposadr[jid]]`, not a `jntpos` field: MuJoCo has never had
        `MjData.jntpos` (measured on 3.13.0 — `hasattr(data, "jntpos")` is `False`, and the only
        qpos-bearing attribute the struct exposes is `qpos`), so the version of this line that
        read it raised `AttributeError` on every call. That was not visible in the privileged
        batches, which answer `held_state` from body poses (`_held_now`) and never reach here; it
        is fatal to the percept arm, whose `executor.held_state` is the contract's §5.8 layer-1
        reading of the gripper and calls this on every skill result.

        The joint is `r_close`, a slide (`mjJNT_SLIDE`) at `qposadr` 7 with range `[0, 0.04]`,
        measured on layout 0's model, where a fresh episode sits at `-0.0002` with the pads
        0.0943 m apart. `l_close` is its mirror at `[-0.03, 0]`.

        That declared range is not where the reading stops. Pressed by `GRASP_EFFORT` through a
        whole pick it reaches 0.0353-0.0406 with the peg between the pads and 0.0364-0.0416 with
        nothing there (`/tmp/mw_aperture_probe.py`, all 35 layouts): the constraint is soft, the
        joint travels past its own limit under effort, and so the end of the range is not a stop a
        reading can be compared against. `executor.APERTURE_*` carries the consequence.
        """
        import mujoco
        if self.env is None:
            return None
        m = self.env.model
        jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT.value, "r_close")
        if jid < 0:
            return None
        return round(float(self.data.qpos[int(m.jnt_qposadr[jid])]), 4)

    @property
    def sim_time(self) -> float:
        return round(self.steps * int(getattr(self.env, "frame_skip", 5)) * 0.002, 4)

    def object_names(self) -> dict[str, str]:
        """Which benchmark site backs which entity id this channel may name. A table
        of *vocabulary*, not of solutions: it says where the peg is measured to be,
        which is what any camera could see, and never says what to do about it."""
        return {"peg 1": "pegGrasp", "socket 1": "goal"}

    #: entity id -> the body whose geoms give that entity its extent. The other half of
    # the same vocabulary table: a site says where a thing is, a body says how long it is.
    OBJECT_BODIES = {"peg 1": "peg", "socket 1": "box"}

    def body(self, name: str) -> Optional[tuple[float, float, float]]:
        import mujoco
        if self.env is None:
            return None
        bid = mujoco.mj_name2id(self.env.model, mujoco.mjtObj.mjOBJ_BODY.value, name)
        if bid < 0:
            return None
        p = self.data.body(bid).xpos
        return (round(float(p[0]), 4), round(float(p[1]), 4), round(float(p[2]), 4))

    def leading_tip(self, eid: str,
                    away_from: tuple[float, float, float]) -> Optional[dict[str, Any]]:
        """The far end of this entity along its own longest axis, measured from the model.

        `place` needs this because the end of an object is the part that has to arrive
        somewhere. The peg on this bench is 0.24 m long along x and is grasped 0.13 m
        from the end that goes in, so a motion that aims the *named* position at a point
        drives the tip 0.13 m past it — measured, and the reason an earlier recipe of
        this file over-shot the socket on its first run.

        The numbers come from `model.geom_size` and `data.geom(g).xmat`: the object's own
        extent in world frame, which is what a caliper, a depth camera or a player
        standing at the bench would report. Nothing here consults how the benchmark
        scores the result.
        """
        import mujoco
        import numpy as np
        if self.env is None or eid not in self.OBJECT_BODIES:
            return None
        m, d = self.env.model, self.data
        bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY.value, self.OBJECT_BODIES[eid])
        if bid < 0:
            return None
        best: Optional[dict[str, Any]] = None
        for g in range(m.ngeom):
            if int(m.geom_bodyid[g]) != bid:
                continue
            size = np.asarray(m.geom_size[g], dtype=np.float64)
            if not size.size:
                continue
            ax = int(np.argmax(size))
            half = float(size[ax])
            if best is not None and half <= float(best["half_length_m"]):
                continue
            xmat = np.asarray(d.geom(g).xmat, dtype=np.float64).reshape(3, 3)
            u = xmat[:, ax].copy()
            n = float(np.linalg.norm(u))
            if n == 0.0:
                continue
            center = np.asarray(d.body(bid).xpos, dtype=np.float64)
            # the end farther from the thing that holds it (or, with nothing holding it,
            # simply the +axis end: the choice is a convention, not knowledge)
            sign = 1.0 if float(np.dot(center - np.asarray(away_from, dtype=np.float64), u)) >= 0 \
                else -1.0
            tip = center + sign * u / n * half
            other = center - sign * u / n * half
            best = {"tip": tuple(round(float(v), 4) for v in tip),
                    "other_end": tuple(round(float(v), 4) for v in other),
                    "ends": (tuple(round(float(v), 4) for v in tip),
                             tuple(round(float(v), 4) for v in other)),
                    "center": tuple(round(float(v), 4) for v in center),
                    "axis": tuple(round(float(v), 4) for v in (sign * u / n)),
                    "half_length_m": round(half, 4),
                    "radius_m": round(float(np.max(np.delete(size, ax))), 4),
                    "geom": mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM.value, g) or "-"}
        return best

    # ------------------------------------------------------------------ primitives
    def servo(self, goal_xyz: tuple[float, float, float], effort: float, *,
              tol: float = SERVO_TOL_M, budget: int = 220, press: bool = False) -> dict[str, Any]:
        """Drive the hand toward one Cartesian point while holding one gripper effort.

        This is the whole actuator API of this adapter. It is closed-loop only in the
        sense a PID is: it stops on arrival or on budget, it never re-aims at a
        different point, and it holds no memory of a previous call.

        `press=True` is the same controller run for the whole budget instead of stopping
        on arrival: the point may be one the actuators cannot reach (the peg's own site
        is below the height the hand frame can be commanded to), and continuing to
        command toward it is what keeps the fingers around the object while they close.
        """
        import numpy as np
        if self.env is None:
            raise BackendError("servo before start()")
        t0 = time.time()
        used, converged, err = 0, False, float("nan")
        for _ in range(budget):
            if self.done:
                break
            err = float(np.linalg.norm(np.asarray(goal_xyz, dtype=np.float64) - np.asarray(self.hand())))
            if err <= tol and not press:
                converged = True
                break
            a = np.zeros(4)
            a[:3] = np.clip(SERVO_GAIN * (np.asarray(goal_xyz) - np.asarray(self.hand())), -1.0, 1.0)
            a[3] = effort
            self._one_step(a)
            used += 1
        err = float(np.linalg.norm(np.asarray(goal_xyz, dtype=np.float64) - np.asarray(self.hand())))
        if err <= tol:
            converged = True
        self.primitive_seconds = round(self.primitive_seconds + (time.time() - t0), 4)
        return {"steps": used, "converged": converged, "final_error_m": round(err, 4)}

    def squeeze(self, effort: float, *, steps: int = SQUEEZE_STEPS) -> dict[str, Any]:
        """Hold the commanded Cartesian position and apply one gripper effort for a
        fixed number of steps.

        Not a convenience: `servo` stops the moment it arrives, so a verb whose effect
        takes time — fingers closing around an object — would otherwise cost zero
        steps. The motion is the same one `hold()` performs with zero effort, so nothing
        here aims at anything new."""
        import numpy as np
        if self.env is None:
            raise BackendError("squeeze before start()")
        used = 0
        for _ in range(max(0, int(steps))):
            if self.done:
                break
            a = np.zeros(4)
            a[3] = effort
            self._one_step(a)
            used += 1
        return {"steps": used, "aperture_m_after": self.gripper_open_m()}

    def hold(self, seconds: float) -> int:
        """Step the world with zero commanded motion so velocities decay. A measurement
        taken while the object is still swinging is not a measurement."""
        import numpy as np
        n = max(1, int(round(seconds / (0.002 * max(1, int(getattr(self.env, "frame_skip", 5)))))))
        used = 0
        for _ in range(min(n, 40)):
            if self.done:
                break
            self._one_step(np.zeros(4))
            used += 1
        return used

    def _one_step(self, action) -> None:
        import numpy as np
        try:
            _, r, term, trunc, info = self.env.step(np.asarray(action, dtype=np.float64))
        except ValueError as e:  # the horizon guard inside step(): a lifecycle fact
            self.error = {"phase": "step", "type": "horizon", "message": str(e)[:200]}
            raise BackendError(str(e)) from e
        except Exception as e:  # noqa: BLE001 - an environment fault is an outcome
            self.error = {"phase": "step", "type": type(e).__name__, "message": str(e)[:200]}
            raise BackendError(str(e)) from e
        self.steps += 1
        self.reward_total += float(r)
        self.last_info = dict(info or {})
        if trunc or term:
            self.steps = min(self.steps, int(getattr(self.env, "max_path_length", self.steps)))
        if self.gui_sink is not None:
            try:
                self.gui_sink.on_step(self)
            except Exception:  # noqa: BLE001 - a viewer must never break a run
                pass

    # ------------------------------------------------------------------ evaluation
    def eval_view(self) -> dict[str, Any]:
        """The official scoreboard, read only from here. Nothing in this dict may enter
        an observation (SPEC-v0.2 §10: 不注入 hidden state / expert answer)."""
        info = self.last_info or {}
        return {
            "env_steps": int(self.steps),
            "max_env_actions": int(self.max_env_actions),
            "env_done": bool(self.done or (info.get("success") == 1.0)),
            "official_success": float(info.get("success", 0.0) or 0.0) == 1.0,
            "reward_total": round(self.reward_total, 6),
            "obj_to_target_m": (round(float(info["obj_to_target"]), 4)
                                if info.get("obj_to_target") is not None else None),
            "grasp_success_official": float(info.get("grasp_success", 0.0) or 0.0),
            "error": dict(self.error) if self.error else None,
            "reset_seconds": self.reset_seconds,
            "primitive_seconds": self.primitive_seconds,
            "evaluator": "metaworld env.step info['success'] (unmodified)",
        }

    # ------------------------------------------------------------------- perception
    def measured(self) -> dict[str, Any]:
        """Simulator-measured geometry for the privileged channel. Deliberately a
        different function from `eval_view`: poses are observations, `success` is not."""
        names = self.object_names()
        return {
            "hand": self.hand(),
            "gripper_open_m": self.gripper_open_m(),
            "sites": {eid: self.site(s) for eid, s in names.items()},
            "workspace": (tuple(round(float(v), 3) for v in self.env.mocap_low),
                          tuple(round(float(v), 3) for v in self.env.mocap_high)),
            "sim_time": self.sim_time,
        }

    def capture(self, view: str) -> Optional[Any]:
        """One RGB frame from one named benchmark camera. Depth is not produced by
        this channel; `frames.py` records that as `depth=None`, not as zeros.

        There is deliberately no fallback to `env.render()`. That call reaches gymnasium's
        windowed viewer, and on a host with no display it does not raise — it aborts the
        process (`libc++abi: __cxa_guard_acquire detected recursive initialization`,
        measured in this session), which would take the whole batch down with it. A frame
        this channel cannot render is a recorded fact instead: the episode goes on without
        the thumbnail, and `capture_error` says why.
        """
        import numpy as np
        if self.env is None:
            return None
        try:
            import mujoco
            if self._renderer is None:
                self._renderer = mujoco.Renderer(self.env.model, self.height, self.width)
            self._renderer.update_scene(self.env.data, camera=view)
            return np.asarray(self._renderer.render(), dtype=np.uint8)
        except Exception as e:  # noqa: BLE001 - a missing thumbnail is a fact, not an outcome
            self.capture_error = f"{type(e).__name__}: {str(e)[:200]}"
            return None

    def capture_all(self) -> dict[str, Any]:
        self._frames = {v: self.capture(v) for v in self.views}
        return self._frames

    def public_text(self) -> str:
        """What this channel can say in words. The privileged channel states measured
        poses; the official score is absent from this string by construction."""
        m = self.measured()
        lines = [f"[{self.env_name}] layout {self.task_index} step {self.steps}"]
        for eid, xyz in sorted(m["sites"].items()):
            lines.append(f"  {eid} measured at ({xyz[0]:.3f}, {xyz[1]:.3f}, {xyz[2]:.3f})")
        hx, hy, hz = m["hand"]
        aperture = m["gripper_open_m"]
        lines.append(f"  hand at ({hx:.3f}, {hy:.3f}, {hz:.3f}), "
                     f"gripper aperture "
                     + (f"{aperture} m" if aperture is not None else "not measured"))
        return "\n".join(lines)


__all__ = ["MuJoCoBackend", "BackendError", "TASKS", "GRASP_EFFORT", "OPEN_EFFORT",
           "SERVO_GAIN", "SERVO_TOL_M", "SETTLE_STEPS", "SQUEEZE_STEPS"]
