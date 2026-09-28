"""One skill call -> one motion -> one measured result (SPEC-v0.2 §10, SPEC-BST 3.5).

The prohibitions are what shaped this file, so what it does not do is the record:

* it never chains two verbs. `pick` does not then `place`; `place` does not first
  `pick`. Each call performs the motion of exactly one verb and returns;
* it never retries. A servo that timed out, or a grasp the next snapshot says failed,
  is reported once and the decision layer decides what the next round is;
* it never repairs a reference. An entity name that is not in the world it was handed
  is a refusal with a reason, not a nearest-neighbour lookup — choosing which peg the
  model meant would be the adapter acting for it;
* it never reads the score. `eval_view()` is not called anywhere below; the only
  numbers this module returns are the ones `measured()` produces from poses, plus how
  many environment steps and seconds the motion cost.

The motion recipes are the *meaning of the verb* in this world, in the same sense the
desktop `pick` owns an IK approach: `pick` = get above the object, descend, close,
lift to carry height. The constants are kinematic properties of the Sawyer bench
(gripper length, carry height, push overshoot), not knowledge about which task is
being run — no constant below encodes which object belongs where, and the two measured
corrections this file went through (`GRASP_DESCEND_STEPS`, `_do_place`'s use of the
object's own end) were both read off the physics, not off the score.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ..core.contracts import SkillCall, SkillResult, SkillStatus
from .env import GRASP_EFFORT, OPEN_EFFORT, SETTLE_STEPS, SQUEEZE_STEPS
from .skills import render

#: kinematic constants of this bench, in metres
APPROACH_ABOVE_M = 0.20     #: get the hand here before descending
CARRY_Z_M = 0.24            #: a height clear of the box and the wall
RETREAT_Z_M = 0.35
SOCKET_STANDOFF_M = 0.35    #: line up this far behind the target, along the object's own axis

#: step counts, not distances: what the fixed-budget phases of a motion cost
GRASP_DESCEND_STEPS = 40    #: measured: the hand reaches its floor (46 mm) within ~30
GRASP_LIFT_STEPS = 60
#: the insert phase is a fixed budget rather than a convergence test: the wall is in the
#: way, so the commanded point is reached only by pressing against it (`servo(press=True)`)
SOCKET_PUSH_STEPS = 140
SOCKET_ALIGN_STEPS = 80     #: the travel to the stand-off point is unconstrained space,
#: so it converges; the budget is there for the case where it does not
RELEASE_STEPS = 20          #: open the fingers and let go, the last half of `place`. Measured
#: as the aperture's own reopening time, not as a share of the motion: holding `OPEN_EFFORT`
#: (-1.0) after a grasp-and-lift, the pad gap crosses 0.0903 m at step 11-13 of all 35 layouts,
#: reads 0.0945-0.0947 m at step 20 (a fresh episode sits at 0.0943 m), and the peg is measurably
#: falling by step 18-19 (`/tmp/mw_release_probe.py`). Twenty steps is "open, and then some",
#: which is the side of the boundary this verb has to be on: the release is a fixed step count,
#: so a budget that stops short of step 13 returns a `place` whose object never left the hand.
#:
#: the largest hand-to-object offset a carried object can have on this arm. The same
#: fact `state.py:carried` measures (0.12 m), stated per axis so a `place` issued when
#: nothing is held refuses instead of sweeping an empty gripper through the scene.
MAX_CARRY_OFFSET_M = 0.12
#: The object's own site is below the lowest pose the hand frame can be commanded to
#: (`mocap_low[z] = 0.05` while the peg site sits at 0.025), so "aim slightly above the
#: centre" was measured to be wrong: a descend that stops 20 mm high closes on air and
#: the peg stays at 0.0249 m. The command is the object's own position, pressed for a
#: fixed budget, and the fingers then squeeze in place (`env.py:squeeze`).

# ------------------------------------------------------- the gripper's own report --
#: The two ends of the gripper's travel, in metres, that would turn `held_state()` from a refusal
#: into a reading. They are still `None`, and this is the measurement that says they cannot be
#: declared on this arm at all — which is a different finding from "nobody has measured it yet".
#:
#: `/tmp/mw_aperture_probe.py` runs `_do_pick`'s exact motion on all 35 layouts twice: once aimed
#: at the peg, once aimed 60 mm off it *across the rod's own long axis* (the first version offset
#: along x, which on this bench is along the rod, so it measured a successful grasp twice and
#: called the second copy an empty hand). The two classes then separate cleanly in the world:
#: 35/35 grasps lifted the peg — its site travelled 184.0-186.1 mm — and 35/35 empty squeezes left
#: it down, moving it 10.5-21.7 mm of jiggle and 5.1 mm further down onto the table.
#:
#: Both readings at the three instants after the fingers are commanded shut (metres):
#:
#:   stage        holding a peg      closed on air     separation
#:   squeeze end  gap 0.0267-0.0275  gap 0.0253-0.0264  0.3 mm
#:   lift         gap 0.0236-0.0243  gap 0.0214-0.0223  1.3 mm
#:   settle       gap 0.0278-0.0329  gap 0.0243         3.5 mm
#:   squeeze end  jnt 0.0353-0.0361  jnt 0.0364-0.0372  0.3 mm
#:   lift         jnt 0.0403-0.0406  jnt 0.0413-0.0416  0.7 mm
#:   settle       jnt 0.0353-0.0381  jnt 0.0398         1.7 mm
#:   fresh        gap 0.0943, jnt -0.0002 — the same number in all 35 layouts of both arms
#:
#: Three facts in that table make it a refusal rather than a threshold waiting to be typed.
#:
#: 1. The separation is smaller than the spread within one class. Holding, the pad gap at the
#:    settle instant ranges 5.1 mm (0.0278-0.0329) around a 3.5 mm distance from the single
#:    closed-on-air value (0.0243). Any margin that still calls 0.0243 closed has to be <= 1.75 mm,
#:    which is inside the noise of the class it exists to exclude — and at the squeeze instant both
#:    readings separate by 0.3 mm, which separates nothing.
#: 2. What does separate is a size, not a state. A gap reading is the distance between two pads;
#:    "something is between them" is a claim about the diameter of that something, which this
#:    channel never learns. A 30 mm rod holds the pads 3.5 mm wider than air at the settle instant
#:    and 1.3 mm wider mid-lift; a hypothetical 24 mm object on this bench with this squeeze would
#:    be reported as a closed hand. Encoding the peg's radius as a property of the gripper is the
#:    privileged-knowledge move this whole file exists to avoid, laundered through a calibration.
#: 3. The commanded joint is neither a force sensor nor limited where its range says. Under
#:    `GRASP_EFFORT` it travels to 0.0416 m against a declared range of [0, 0.04]
#:    (`env.py:finger_qpos_m`), and it reaches that on air as readily as it reaches 0.0406 pressed
#:    against a rod, because a 20-step squeeze followed by a 60-step lift saturates whichever way
#:    it can. The stall that would say "stopped by an object" is gone by the time anyone reads it.
#:
#: So the percept arm's `held_state` answers `"unknown"` for every skill result, and the verdict on
#: a pick is carried by §5.8's second layer — the next frame's measured change in where the object
#: is — which is the layer that can actually see the difference the aperture cannot.
APERTURE_OPEN_M: Optional[float] = None
APERTURE_CLOSED_M: Optional[float] = None
#: how far inside one end of the travel a reading must be to be *called* that end. It is inert
#: while the two ends are None — `held_state()` returns at its first guard — and it is left at
#: 0.004 rather than tuned down into the table above, because a margin smaller than the hold/air
#: separation would silently decide a grasp on 0.3 mm of aperture the day someone fills the ends
#: in, which is the failure this comment exists to prevent.
APERTURE_MARGIN_M = 0.004

#: The stages a motion can report and still have moved nothing: each is a refusal raised *inside*
#: a motion, before any actuator is asked — `_aim` returned None (twice), `_leading_end` returned
#: None, and the carry check refused to drag a rod across the table. A motion whose whole stage
#: list is one of these consumed no environment step, and this file must not file that as a skill
#: that ran — see `execute`. All three are named here because the archived camera channel contains
#: only the first: 4 `pick` rows of the 97 `execution_feedback` motions, all `target_lookup`, and 2
#: of the 30 rows of this file's own log (`/tmp/mw102/reconcile107.py` reads both). A refusal left
#: out of this set would keep being filed as a success; the set is not sized to the batch.
DECLINATION_STAGES = frozenset({"target_lookup", "extent_measurement", "carry_check"})


def _dist(a, b) -> float:
    return float(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)) ** 0.5)


class MujocoScene:
    """The two scene facts the frozen loop reads, answered for a MuJoCo world."""

    def __init__(self, backend: Any):
        self.backend = backend
        self.action_in_progress = False
        self.objects: dict = {}
        self.trays: dict = {}
        # `perception/frames.py` passes this to pybullet; this channel renders through
        # `backend.capture`, and the field exists so nothing downstream sees a None
        self.cid = None

    @property
    def sim_time(self) -> float:
        return float(self.backend.sim_time) if self.backend.env is not None else 0.0

    def settle(self, seconds: float) -> None:
        if self.backend.env is not None and not self.backend.done:
            self.backend.hold(seconds)

    def render(self, path: str) -> None:
        return None


class MujocoSkillExecutor:
    """`SkillCall` -> `render` -> one motion -> `SkillResult`."""

    def __init__(self, backend: Any, *, placement_planner: Any = None):
        self.backend = backend
        # the desktop runtime reads this attribute; this channel resolves targets from
        # measured poses, so there is no slot planner to name
        self.placement_planner = placement_planner
        self.calls = 0
        self.env_steps_total = 0
        #: one entry per motion this file was asked to run, oldest first, carrying the notes the
        # primitive wrote. See `_file` for why the log has to exist at this level.
        self.motions: list[dict[str, Any]] = []
        #: where a motion should be aimed, when that is somebody else's measurement.
        # `None` is this file's own answer — `backend.measured()["sites"]`, real metres off
        # the simulator — and it is what the privileged channel has always used. The
        # percept channel installs a `SnapshotGoals` here, and every point below that is
        # *aimed at* then comes out of the agent's own snapshot instead. Nothing else
        # moves: the hand's position, the aperture and the step counts stay with the
        # actuators, because a camera has no business deciding where the arm is.
        self.goal_source: Any = None
        #: the entity this hand last closed on, in the task's own naming. The gripper's
        # memory, not the world's: it is what makes an aperture reading name a body.
        self._grasped: Optional[str] = None

    # ------------------------------------------------------------------ the seams
    def _aim(self, name: str) -> Optional[tuple[float, float, float]]:
        """The point named by `name` that a motion is asked to reach.

        Read through `goal_source` when one is installed, so the answer is a perception;
        read from the simulator otherwise, which is this channel's control arm. A `None`
        here is a *refusal to move*, not a zero: the primitive then reports
        `target_lookup` and says which source had nothing to say."""
        if self.goal_source is not None:
            return self.goal_source.position(name)
        return self.backend.measured()["sites"].get(name)

    def _leading_end(self, eid: str, target: tuple[float, float, float],
                     away_from: tuple[float, float, float]) -> Optional[dict[str, Any]]:
        """The end of this object that has to arrive, and the axis it arrives along.

        Both arms answer with the *same* rule — of the object's two measured ends, the one
        **nearest where it is going** — because this is the seam at which they are meant to differ
        only in where the number comes from. A different choice of end between them would hide a
        policy difference inside a motor primitive. The two sources differ: the percept arm is
        asked for the ends of its own fitted axis, and the control arm takes the ends the
        simulator's caliper reports.

        The caliper's *own* sign convention is deliberately not reused here even though this branch
        reads it. `env.leading_tip` keys its tip on `away_from` — "the choice is a convention, not
        knowledge" — and `state.py:122` reads that convention for the geometry it publishes, where
        no target is in scope. Answering a target's question with a holder's convention is what this
        file got wrong twice:

        * `-_dist(p, target)`, "farthest from where it is going", was the rule until batch 4. It
          asks the hand to travel a full rod length farther, and was measured doing it: on both
          armed camera episodes the axis itself was good — 0.2405 m and 0.2343 m against the
          declared 0.240 m, 397 and 383 pixels, the near end within 5 mm of where the simulator put
          it — and only the *choice* was wrong, 0.5293 m of commanded travel where the privileged
          arm asked 0.2674 m. The arm delivered 0.279 m of that and released the rod 0.161 m from
          the hole, so what read as "place falls short of the target" was never a travel limit
          (`/tmp/mw_place_travel.py`: pick+place on the privileged arm reaches its commanded point
          to 0.1 mm, scores, and costs 297 of the 500 steps).
        * `-_dist(p, away_from)`, "farthest from the carrying hand", was the fix for that (Fix A)
          and replaced the target's answer with the hand's. Batch 5b measured what a grasp at the
          middle of a 0.24 m rod leaves for it to decide on: the two ends were 0.4-4.2 mm apart in
          hand distance against 112.7-227.4 mm apart in target distance, and all five live
          `place`s led with the end away from the hole (`/tmp/mw_end_rule5b.py`). The same archived
          axis ends under the same rule chose the other end in replay, because the replayed `pick`
          left the hand a few millimetres away (`/tmp/mw_fixb_replay.py`, phase-log H-11): a
          sub-5 mm margin is not a rule, it is a coin whose face is decided by pose noise. The two
          layouts whose hole *was* measured well (0.0436 m and 0.1106 m off) then stopped 0.2312 m
          and 0.2522 m short, where leading with the near end puts both in the socket at 186 and
          187 steps.
        """
        if self.goal_source is not None:
            ends = self.goal_source.extent(eid)
            if not ends:
                return None
            centre = self.goal_source.position(eid) or tuple(target)
            half = _dist(ends[0], ends[1]) / 2.0
            source = f"perceived axis of {self.goal_source.source_name()}"
        else:
            caliper = self.backend.leading_tip(eid, away_from)
            if caliper is None:
                return None
            ends, centre = list(caliper["ends"]), caliper["center"]
            half = float(caliper["half_length_m"])
            source = "simulator caliper of the carried body"
        tip = min(ends, key=lambda p: _dist(p, target))
        # centre -> tip, the same direction `leading_tip` publishes its `axis` in, so that
        # the one place `_do_place` reads this field cannot mean the opposite way on the
        # two arms depending on which installed a goal source.
        axis = [float(tip[i]) - float(centre[i]) for i in range(3)]
        n = (sum(v * v for v in axis)) ** 0.5 or 1.0
        return {"tip": tuple(round(float(v), 4) for v in tip),
                "center": tuple(round(float(v), 4) for v in centre),
                "axis": tuple(round(v / n, 4) for v in axis),
                "half_length_m": round(half, 4),
                "source": source}

    def held_state(self) -> Optional[str]:
        """What the gripper says it holds, in the contract's three-way convention.

        `core/skills.py:_held_state`'s vocabulary, answered by the aperture rather than by
        the poses: `None` when the fingers are at their calibrated open width (holding
        nothing), the entity id it last closed on when something is between the pads, and
        `"unknown"` when the aperture is unreadable, uncalibrated, or holding a body this
        hand has no memory of naming.

        This is the one fact about the world's *objects* that a camera cannot supply — no
        view of a table sees inside the gripper — and §5.8's first layer is exactly where
        the contract puts it. `_held_now` below answers the same question from simulator
        poses and stays the privileged channel's source; the percept arm never calls it.

        On this bench it answers `"unknown"`, at its first guard, every time: the two calibration
        ends are `None`, and the measurement in their comment says why they stay that way — this
        hand's aperture does not distinguish "closed" from "holding a rod", so the pick verdict is
        carried by the next frame instead. The branches below are the contract those numbers would
        follow on a gripper that can report its own stall.
        """
        q = self.backend.finger_qpos_m()
        if q is None or APERTURE_OPEN_M is None or APERTURE_CLOSED_M is None:
            return "unknown"
        if q >= APERTURE_OPEN_M - APERTURE_MARGIN_M:
            return None
        if q <= APERTURE_CLOSED_M + APERTURE_MARGIN_M:
            return None if self._grasped is None else "unknown"
        return self._grasped or "unknown"

    # ------------------------------------------------------------------- dispatch
    def _file(self, call: SkillCall, result: SkillResult) -> SkillResult:
        """Keep the motion's own account of itself, and hand the result straight through.

        `ExecutionFeedback` has no notes field: `core/runtime.py:691` routes a *rejected* call's
        notes into `rejection_reasons` and drops an executed one's, so the one number a reviewer
        needs in order to read a miss — which end the seam chose and what translation that asked
        of the hand — reaches the artifact only when the motion refused to run. Batch 4's layout 0
        is the case this costs: its live `place` released the rod 0.161 m from a hole its own
        archived frame measures 0.0393 m from, and the difference between "the aim was wrong" and
        "the perception was wrong" is written in notes nobody kept (phase-log H-6, H-9 item 5).

        This list is the channel's own answer, filed by `runner.py` into `episode_summary.json` —
        the file that already carries the survey evidence — so nothing on the model-facing side
        of the boundary changes.
        """
        self.motions.append({"skill": call.skill, "status": result.status.value,
                            "failure_code": result.failure_code,
                            "stages": list(result.stages_executed),
                            "held_state": result.held_state,
                            "env_steps_used": (result.measurements or {}).get("env_steps_used"),
                            "notes": list(result.notes)})
        return result

    def execute(self, call: SkillCall) -> SkillResult:
        t0 = time.time()
        args, reasons = render(call.skill, dict(call.args or {}))
        if args is None:
            return self._file(call, SkillResult(
                call_id=call.call_id, status=SkillStatus.rejected,
                failure_code="ADAPTER_ERROR", t_start=t0, t_end=time.time(),
                held_state="unknown",
                notes=[f"skill {call.skill!r} is not one motion of this world: {r}"
                       for r in reasons]))
        if self.backend.env is None:
            return self._file(call, SkillResult(
                call_id=call.call_id, status=SkillStatus.rejected,
                failure_code="ADAPTER_ERROR", t_start=t0, t_end=time.time(),
                held_state="unknown", notes=["the backend was never started"]))
        steps_before = int(self.backend.steps)
        try:
            stages, notes, held = getattr(self, f"_do_{call.skill}")(args)
        except Exception as e:  # noqa: BLE001 - an environment fault is an outcome
            self.calls += 1
            return self._file(call, SkillResult(
                call_id=call.call_id, status=SkillStatus.failed,
                failure_code="ENVIRONMENT_ERROR", t_start=t0, t_end=time.time(),
                stages_executed=["motion_started"], held_state="unknown",
                measurements={"env_steps_used": float(self.backend.steps - steps_before)},
                notes=[f"{type(e).__name__}: {e}",
                       "the motion may have partly reached the simulator; it is never replayed "
                       "to find out (SPEC-BST 6.3)"]))
        used = int(self.backend.steps) - steps_before
        # A motion that never got past asking where to go refused; it is not one that ran. Filed
        # `completed` it was counted in `skill_calls_executed` *and* its reason was thrown away --
        # `core/runtime.py:703` forwards a skill's notes as rejection reasons only for a call this
        # status says was refused -- so the model was told the pick succeeded. On the sensor arm
        # layer 2 then narrowed the *published* status to `uncertain`/`STATE_UNCERTAIN`
        # (`perceive.py:2163`), which is what the event log shows; the billing and the dropped
        # reason happened one layer before it. Measured by `/tmp/mw102/reconcile107.py`, which
        # reads both artifacts and says why they differ: 4 of the 97 `execution_feedback` motions
        # in the archived camera channel are this (`sum(skill_calls_executed)` 98 -> 94), and 2 of
        # the 30 rows of this file's own log are (`perception.motions`, kept only from batch 5b on,
        # so it cannot see the other two). `_aim`'s docstring calls a None "a refusal to move, not
        # a zero"; this is where that sentence becomes the record.
        declined = bool(stages) and used == 0 and set(stages) <= DECLINATION_STAGES
        if not declined:
            self.calls += 1
        self.env_steps_total += used
        truncated = bool(self.backend.done) and "horizon" in " ".join(notes).lower()
        status = (SkillStatus.timeout if truncated else
                  SkillStatus.rejected if declined else SkillStatus.completed)
        return self._file(call, SkillResult(
            call_id=call.call_id, status=status,
            failure_code="AIM_UNMEASURED" if declined else None,
            t_start=t0, t_end=time.time(), sim_seconds_used=self.backend.sim_time,
            stages_executed=stages, held_state=held,
            measurements={"env_steps_used": float(used),
                          "env_steps_total": float(self.backend.steps),
                          "motion_seconds": round(time.time() - t0, 4)},
            notes=notes, candidate_resolution=None))

    # -------------------------------------------------------------------- motions
    def _do_observe(self, args: dict[str, str]):
        """A measurement costs no environment step; the frames are written by the
        runtime's perception arm, and the poses are re-read on the next snapshot."""
        self.backend.capture_all()
        return (["measurement_taken"], ["no environment step was consumed by a look"], "unknown")

    def _do_pick(self, args: dict[str, str]):
        eid = args["object_id"]
        aim = self._aim(eid)
        if aim is None:
            return (["target_lookup"],
                    [f"{eid} has no position in {self._aim_source()}: this motion will not "
                     f"move toward a point nobody measured"], "unknown")
        x, y, z = aim
        b = self.backend
        a1 = b.servo((x, y, z + APPROACH_ABOVE_M), OPEN_EFFORT)
        a2 = b.servo((x, y, z), OPEN_EFFORT, budget=GRASP_DESCEND_STEPS, press=True)
        a3 = b.squeeze(GRASP_EFFORT, steps=SQUEEZE_STEPS)
        self._grasped = eid
        a4 = b.servo((x, y, max(z + APPROACH_ABOVE_M * 0.9, CARRY_Z_M)), GRASP_EFFORT,
                     budget=GRASP_LIFT_STEPS)
        b.hold(SETTLE_STEPS * 0.01)
        held = self.held_state() if self.goal_source is not None else self._held_now(eid)
        m = b.measured()
        where_line = (f"hand at {m['hand']}, {eid} at {m['sites'].get(eid)} after the lift"
                      if self.goal_source is None else
                      f"hand at {m['hand']}; where {eid} got to is the next snapshot's "
                      f"measurement of it, and the fingers report {held}")
        notes = [f"aimed at {eid} = ({x}, {y}, {z}) from {self._aim_source()}",
                 f"servo errors (m): above={a1['final_error_m']}, down={a2['final_error_m']}, "
                 f"lift={a4['final_error_m']}; squeeze held {a3['steps']} steps",
                 where_line,
                 ("the grasp is reported by the next snapshot's measured poses, not by this "
                  "call" if self.goal_source is None else
                  "the grasp is reported by the next frame and by the fingers, not by this "
                  "call")]
        if not a4["converged"]:
            notes.append("the lift did not reach its commanded height before its step "
                         "budget ran out (horizon)")
        return (["approach", "descend", "close", "lift"], notes, held)

    def _do_place(self, args: dict[str, str]):
        """Bring the held object so that **its own leading end** arrives at the target.

        Two measured facts shape this, and neither is knowledge about which task is
        being run:

        * the part that has to arrive is the end of the object, not the point the text
          names it by — `env.py:leading_tip` explains why, with the numbers this recipe
          got wrong before the tip was measured, and `_leading_end` says which of the two
          ends that is and what the two wrong answers there cost;
        * the motion is then one rigid translation, `hand + (target - tip)`, so nothing
          in this file pretends to know how far a peg hangs below the gripper or how deep
          a socket is. The stand-off is that same translation pulled back along the
          object's own long axis, which is also the only axis the object can enter along.

        It stops *at* the target rather than driving past it: the first version of this
        motion pushed 0.45 m beyond the named point and was measured to shove the tip
        past the place the task asked for.

        Both points it aims at — the target and the object's own leading end — are asked of
        `_aim`/`_leading_end`, which is where a percept channel's snapshot replaces this
        file's simulator read. The hand's position is not: that is an encoder.
        """
        eid, tid = args["object_id"], args["target_id"]
        m = self.backend.measured()
        hand = m["hand"]
        target = self._aim(tid)
        where = self._aim(eid)
        if target is None or where is None:
            missing = [n for n, v in ((tid, target), (eid, where)) if v is None]
            return (["target_lookup"],
                    [f"{', '.join(missing)} has no position in {self._aim_source()}"],
                    "unknown")
        tip = self._leading_end(eid, target, hand)
        if tip is None:
            return (["extent_measurement"],
                    [f"{eid} has no measured extent in {self._aim_source()}, so no end of it "
                     f"can be aimed at {tid}"], "unknown")
        tx, ty, tz = target
        px, py, pz = tip["tip"]
        cx, cy, cz = tip["center"]
        # The approach direction is from the leading end back into the body of the object:
        # the stand-off has to be on the side the object comes *from*, or the motion spends
        # its budget pressed against the far side of the wall (measured: the first version
        # of this recipe used the insertion axis itself, took 384 steps and never let the
        # loop ask a third question).
        bx, by, bz = cx - px, cy - py, cz - pz
        n = (bx * bx + by * by + bz * bz) ** 0.5
        if n == 0.0:
            bx, by, bz, n = -tip["axis"][0], -tip["axis"][1], -tip["axis"][2], 1.0
        bx, by, bz = bx / n, by / n, bz / n
        dx, dy, dz = tx - px, ty - py, tz - pz          # the one translation being asked for
        # the carry check is against the point the entity is *named* by — the grasp site,
        # which is the part of it the hand is next to. Measured against the tip it would
        # always fail on an object this long.
        off = (hand[0] - where[0], hand[1] - where[1], hand[2] - where[2])
        if any(abs(v) > MAX_CARRY_OFFSET_M for v in off):
            return (["carry_check"], [f"{eid} is not measured close enough to the hand for a "
                                      f"place to move it to {tid}: offset {tuple(round(v, 4) for v in off)} "
                                      f"exceeds {MAX_CARRY_OFFSET_M} m"], "unknown")
        b = self.backend
        g1 = (hand[0] + dx + SOCKET_STANDOFF_M * bx,
              hand[1] + dy + SOCKET_STANDOFF_M * by,
              hand[2] + dz + SOCKET_STANDOFF_M * bz)
        g2 = (hand[0] + dx, hand[1] + dy, hand[2] + dz)
        a1 = b.servo(g1, GRASP_EFFORT, budget=SOCKET_ALIGN_STEPS)
        a2 = b.servo(g2, GRASP_EFFORT, budget=SOCKET_PUSH_STEPS, press=True)
        a3 = b.squeeze(OPEN_EFFORT, steps=RELEASE_STEPS)
        b.hold(SETTLE_STEPS * 0.01)
        self._grasped = None
        held = self.held_state() if self.goal_source is not None else self._held_now(eid)
        m2 = b.measured()
        if self.goal_source is None:
            tip2 = self.backend.leading_tip(eid, m2["hand"]) or {}
            after = (f"{eid} tip at {tip2.get('tip')}, {eid} at {m2['sites'].get(eid)}, "
                     f"{tid} at {m2['sites'].get(tid)} after the release")
        else:
            after = (f"the hand is at {m2['hand']} and the fingers report {held}; where "
                     f"{eid} got to is this snapshot's next measurement, not a number this "
                     f"call owns")
        notes = [f"tip of {eid} measured at {tip['tip']} (half-length {tip['half_length_m']} m, "
                 f"approach from ({bx:+.3f}, {by:+.3f}, {bz:+.3f})), translation asked of the "
                 f"hand ({dx:+.4f}, {dy:+.4f}, {dz:+.4f}), both from {self._aim_source()}",
                 f"standoff error (m)={a1['final_error_m']}, insert error (m)={a2['final_error_m']}",
                 f"insert ran {a2['steps']} steps and {'reached' if a2['converged'] else 'stopped at'} "
                 f"its commanded point; fingers held open for {a3['steps']} steps",
                 after,
                 "whether the object stayed at the target is the next snapshot's answer, and "
                 "this call never read the benchmark's score to fill it in"]
        return (["align", "insert", "release", "settle"], notes, held)

    def _do_safe_retreat(self, args: dict[str, str]):
        b = self.backend
        low, high = b.measured()["workspace"]
        home = (round((low[0] + high[0]) / 2.0, 3), round((low[1] + high[1]) / 2.0, 3), RETREAT_Z_M)
        a1 = b.servo(home, OPEN_EFFORT, budget=80)
        b.hold(SETTLE_STEPS * 0.01)
        return (["open", "raise"], [f"retreat target {home}, final error (m)={a1['final_error_m']}"],
                "unknown")

    # --------------------------------------------------------------------- helpers
    def _aim_source(self) -> str:
        """Which measurement a motion was aimed by, in the notes and nowhere else.

        Named because the same three numbers mean two different experiments: a point from
        `measured()` is the simulator's, and a point from the snapshot is the agent's own
        seeing. A note that says only "measured" cannot be audited for that difference."""
        return (self.goal_source.source_name() if self.goal_source is not None
                else "the simulator's own site poses (privileged)")

    def _held_now(self, eid: str) -> Optional[str]:
        """The held report, in the contract's own three-way convention
        (`core/skills.py:_held_state`): the entity id when this snapshot's poses say it
        is carried, None when they say it is not, `"unknown"` when they say neither.

        A bool is not one of those three answers, which is why this converts rather than
        returning what `carried` measured."""
        from .state import carried
        m = self.backend.measured()
        if eid not in m["sites"]:
            return "unknown"
        init = getattr(self.backend, "init_z", {})
        held = carried(m["sites"][eid], m["hand"], init.get(eid, m["sites"][eid][2]))
        if held == "unknown":
            return "unknown"
        return eid if held else None


__all__ = ["MujocoScene", "MujocoSkillExecutor", "APPROACH_ABOVE_M", "CARRY_Z_M",
           "SOCKET_STANDOFF_M", "MAX_CARRY_OFFSET_M"]
