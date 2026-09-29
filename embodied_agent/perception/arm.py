"""The sensing arm: one `Runtime` whose world is a camera (SPEC-v0.2 §5.1, §5.8, §9).

P1-a built the camera, P1-b read a frame into a `PerceptionObservation`, P1-c turned a
percept into a `WorldState` and P1-d answered postconditions from one. This module is where
those stop being a measurement rig and become **the loop's own observation channel**: one
subclass, ten overridden methods, and a name bridge — which is the whole of it, and
`test_the_arm_overrides_ten_seams_and_nothing_else` pins the set. Four of the ten move the
measurement, three move the accounting that a measurement makes necessary, two carry the
model's own request, and one (`_held_report`) reads the hand.

The measurement seams, and why each one has to move with the others:

* `_capture_world` — the snapshot comes from `capture → reader.read → assembler → tracker →
  ground_state`, so a version number, a camera id and a set of task-named entities all come
  out of one look.
* `_verifier` — returns `SensingVerifier` for the snapshot it was handed. This one and the
  one above are a **pair**: `Runtime._build_feedback` and `_finish_check` both call
  `self._verifier(world)`, and a sensor snapshot handed to the *privileged* verifier would
  be signed `Source.privileged` and trusted by the loop exactly as a measured fact (P1-d's
  `control` assertions pin that failure mode). Changing one seam without the other does not
  produce a weaker arm, it produces a lying one.
* `_state_diff` — refuses the comparison when the two snapshots came from different cameras.
  P1-c measured that one static table reads 71 mm apart across two views, so a cross-view
  diff would manufacture events out of a change of viewpoint; the row says so instead.
* `_begin_episode` — files which arm the episode is about to run in, before the records that
  claim governs.

The accounting is not optional either, because a look is a request: `_requests_used` puts
frames and decisions on one counter, `_preflight_trip` honours the ceiling that counter is
measured against (which the v0.1 loop only reports), and `_finalize` bills the terminal
measurement the finish protocol takes after the last decision round.

Two things the arm does **not** do, both of them §6.1/§3.4 rather than style:

* it never picks a viewpoint for the model. `observe` takes an optional `view` argument, an
  undeclared name is a structured rejection the model repairs, and the terminal look reuses
  the camera the loop last stood at — a default is configuration, a *choice of where to look
  next* is the policy being measured;
* it never relaxes a threshold to make an arm finish. `_finish_check` still requires
  `overall == true` (`runtime.py:709-710`), so on this arm a finish is reachable only by
  *looking from somewhere that can see the answer*, which is what an `uncertain` feedback
  and a `would_resolve: re_observe:<view>` refusal are for. The cost lands in decision rounds
  and HTTP requests, where the ledger can see it (§11).

`held_object` is the one field that does not come from the *reading*, and `grounding.py`
explains why it usually cannot: no camera on a table sees what the hand holds, `PlanValidator`
refuses every pick and place while the hold state is unknown, and the gripper reports it as
§5.8's first layer. It is labelled `held_source: actuator` in the snapshot rather than blended
into the visual attributes. `Perceiver.held_for_frame` is the seam for the case where the hand
is the instrument that cannot answer and the frame's *geometry* can; the default of that seam
is this cell's answer, which is that neither the frame nor anything behind it gets to say.

Three perception channels, two of them registered arms (§9's *w/o VLM (privileged or
limited semantic baseline)* is read as both):

| `--perceive` | reader | §9 arm | files `perception`? |
|---|---|---|---|
| `privileged` | none — the v0.1 snapshot builder reads simulator state | `wo_vlm` | no |
| `stub` | `StubReader` — colour segmentation on the same PNG | `wo_vlm`, limited semantic | no |
| `vlm` | `VLMReader` — one vision request per look | `full` | yes |

The stub files no `perception` record because that record type belongs to the `vlm` module in
`v02.EVENT_MODULE`, and `Ablation.violations()` exists precisely to catch an arm producing a
record for a module it switched off. Its cost evidence is therefore the `perception` events
of the `vlm` arm and nothing else — which is the point of measuring the two against each
other.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional, Sequence

from ..core.contracts import (
    Budgets,
    Decision,
    DecisionContext,
    FailureCode,
    PredicateReport,
    PredicateVerdict,
    SkillCall,
    TerminalStatus,
    VerifyConfig,
    WorldState,
)
from ..core.runtime import Runtime
from ..core.v02 import registry_sha256
from .frames import VIEWS, CameraSpec, camera_for, capture
from .grounding import (
    GroundingMap,
    arm_coherence,
    channel_is_declared,
    channel_readiness,
    declared_views,
    ground_state,
)
from .observe import (
    PerceptionAssembler,
    ReadingError,
    StubReader,
    TaskContext,
    VLMReader,
)
from .verify_percept import PerceptVerifier
from .world_state import PerceptWorldStateTracker


class PerceptionUnavailable(Exception):
    """A look that did not produce a reading.

    Carried rather than swallowed: the alternative is a snapshot with no entities in it, and
    "the model answered in a shape we could not parse" must never be reportable as "the
    table is empty". The runner records it as an unmeasurable episode."""

    def __init__(self, message: str, *, reasons: Sequence[str] = ()):
        super().__init__(message)
        self.reasons = list(reasons)


#: what the grasp question cannot answer from a frame, named apart from the placement list
#: because these resolve with a different *instrument*, not a different viewpoint.
GRASP_UNMEASURED = ("lift_gain_m", "hold_state")


class SensingVerifier(PerceptVerifier):
    """`PerceptVerifier` plus the one postcondition a camera cannot answer at all.

    `verify_grasp` is inherited from `RuntimeVerifier`, and on this channel it is worse than
    uninformative: `lift_gain_m` is `z_after - z_before`, and both z's are
    `measured support height + declared upright half-height` (P1-c). Lift a cube off the
    table and the pixels under it still say *table*, so the estimate puts it back at the
    height it started at, the gain comes out ~0, and the verdict is a confident `false` about
    a grasp the gripper is reporting. That is not a weak sensor, it is a fabricated
    measurement — so the comparison is declined here and the key is never computed, the way
    P1-d declined the seating-height check for exactly this reason
    (`test_the_declined_fact_is_never_even_computed` pins the placement half of the rule;
    `test_the_lift_comparison_is_never_even_computed` pins this one).

    What remains is the question as §5.8 means it: the gripper's own report, read through
    `ground_state`'s labelled `held_object`. An empty gripper is *positive* contrary evidence
    about a grasp and answers `false`; a gripper that cannot tell which of two bodies it
    closes on answers `unknown`, and no further look changes that."""

    def would_resolve(self, reason: str) -> list[str]:
        if reason in GRASP_UNMEASURED:
            return []
        return super().would_resolve(reason)

    def _report(self, pid: str, value: PredicateVerdict, why: str,
                evidence: dict[str, float]) -> PredicateReport:
        """Every answer this channel gives carries the same five evidence keys, `unknown`
        included. `_unknown`'s `{"measurable": 0.0}` would be true of the verdict and useless
        about it: a reviewer needs to see *which* channel failed to speak, and an `unknown`
        that looks like an empty report is indistinguishable from a bug in the report."""
        return PredicateReport(
            predicate_id=pid, description=why, value=value, evidence=evidence,
            evidence_refs=[self.world.observation_ref or ""],
            unmeasured=list(GRASP_UNMEASURED), source=self.source)

    def verify_grasp(self, eid: str, before: WorldState | None = None,
                     support_z_before: float | None = None) -> PredicateReport:
        """`grasp:<eid>` from the actuator's report, with no geometry claimed."""
        pid = f"grasp:{eid}"
        reported = self.world.held_object
        seen = self.world.has_entity(eid)
        evidence: dict[str, float] = {
            "gripper_named_the_entity": float(reported == eid),
            "gripper_reports_empty": float(reported is None),
            "gripper_report_is_ambiguous": float(reported == "unknown"),
            "reported_by_this_view": float(seen),
            "held_flag_is_true": float(self.world.entity(eid).held is True) if seen else 0.0,
        }
        if reported == eid:
            # The body may well be out of frame — in the gripper, above the table, in front
            # of the camera. That is why `reported_by_this_view` is published instead of the
            # absence being read as a contradiction: the answer came from the hand.
            return self._report(
                pid, PredicateVerdict.true,
                "the gripper reports holding this entity; the lift itself is not a quantity "
                "a frame of the table can measure", evidence)
        if reported is None:
            return self._report(pid, PredicateVerdict.false,
                                "the gripper reports holding nothing", evidence)
        if reported == "unknown":
            return self._report(pid, PredicateVerdict.unknown,
                                "the gripper cannot say which body it is closed on", evidence)
        if not seen:
            # the hand names a different body and this frame never saw `eid`: two channels
            # that both fail to place it, which is not the same as a measurement of it
            return self._report(pid, PredicateVerdict.unknown,
                                "held by nothing in this frame, and the gripper names "
                                f"another body ({reported})", evidence)
        return self._report(pid, PredicateVerdict.false,
                            f"the gripper reports holding {reported}, not {eid}", evidence)


class Perceiver:
    """One episode's camera: renders, reads, assembles, versions and grounds a look.

    Owns the two memories a single capture cannot have — the previous percept (which is what
    makes the `changes` group possible at all) and which camera each `observation_ref` came
    from (which is what makes a cross-view diff refusable)."""

    def __init__(self, scene, catalog, gmap: GroundingMap, *, out_dir: str,
                 reader=None, arm_channel: str = "stub", task: Optional[TaskContext] = None,
                 episode_id: str = "",
                 default_view: str = "main", views: Optional[Iterable[str]] = None,
                 keep_depth: bool = True, thresholds: Optional[dict] = None,
                 known_views: Optional[Iterable[str]] = None):
        self.scene = scene
        self.catalog = catalog
        self.gmap = gmap
        self.out_dir = out_dir
        self.task = task or TaskContext()
        self.episode_id = episode_id
        #: which §9 arm this camera carries. Deliberately *not* read off the reader:
        # `StubReader.channel` is `"sensor"`, which is the reading's vocabulary (where the
        # box coordinates came from), not the arm's (whether a vision model was consulted).
        # Conflating the two is how an arm ends up claiming a module it never ran.
        self._arm_channel = arm_channel
        self.known_views = tuple(known_views) if known_views is not None else tuple(VIEWS)
        self.views = declared_views(views, known=self.known_views)
        if default_view not in self.views:
            raise ValueError(f"default view {default_view!r} is not among this arm's cameras "
                             f"{list(self.views)}")
        self.default_view = default_view
        self.keep_depth = bool(keep_depth)
        self.reader = reader if reader is not None else StubReader()
        self.assembler = PerceptionAssembler(catalog,
                                             **({"known_views": self.known_views} |
                                                dict(thresholds or {})))
        self.tracker = PerceptWorldStateTracker(catalog, episode_id=episode_id or None)
        #: observation_ref -> camera_id, for every look this arm has taken
        self.view_of_ref: dict[str, str] = {}
        #: every percept this arm produced, oldest first (§11's cost and grounding metrics
        # are counted from these records, not re-derived)
        self.percepts: list = []
        os.makedirs(out_dir, exist_ok=True)

    # ---------- accounting ----------
    @property
    def channel(self) -> str:
        return self._arm_channel

    @property
    def http_requests(self) -> int:
        return sum(int(p.provenance.get("http_requests_this_call") or 0) for p in self.percepts)

    def usage(self) -> dict[str, int]:
        """Cumulative token spend on looks. Reported per request in each `perception` record
        and summed here; it deliberately does not enter `EpisodeResult.usage`, whose counters
        track the *decision* source's cumulative deltas and would be double-billed by a
        second stream on a shared adapter."""
        out: dict[str, int] = {}
        for p in self.percepts:
            for k, v in (p.tokens or {}).items():
                out[k] = out.get(k, 0) + int(v)
        return out

    def view_of(self, observation_ref: Optional[str]) -> str:
        return self.view_of_ref.get(str(observation_ref or ""), "")

    def camera_for_view(self, view: str) -> CameraSpec:
        """The optics behind one named view. A subclass on another benchmark answers this
        from its own model file; `frames.camera_for` is this cell's rig."""
        return camera_for(view)

    def capture_view(self, camera: CameraSpec, name: str):
        """Render and file one frame. Split out from `look` for the same reason as
        `camera_for_view`: a second benchmark's renderer is not pybullet's, and the
        sequence below — frame, reading, assembly, tracking, grounding — is the part that
        must stay identical across the two, because that sequence *is* the claim."""
        return capture(self.scene, self.out_dir, camera, name, sim_time=self.scene.sim_time,
                       keep_depth=self.keep_depth)

    # ---------- the grasp question, once the frame has been measured ----------
    def held_for_frame(self, held: Optional[str],
                       state: WorldState) -> tuple[Optional[str], str]:
        """The last word on what the hand holds, from the frame that was just read.

        The default is that the frame has no word: no camera on a table sees inside its own
        gripper, so `held` stands exactly as the actuator reported it (§5.8's first layer, and
        `grounding.py` for why the field cannot simply be left unknown). A subclass on another
        benchmark may settle the question from **measured geometry** — the position this
        channel's own instrument solved the body to — and returns which authority spoke, which
        is what the snapshot's sentence is stamped with. Two things it may not do: override a
        hand that did speak (the seam exists for `held == "unknown"`, and a frame
        contradicting an actuator report is a calibration failure rather than a licence to
        outvote it), or reach past the frame for a simulator handle. Both are pinned by
        `tests/contract/test_mujoco_channel.py`.
        """
        return held, "actuator"

    # ---------- the one entry point ----------
    def look(self, view: str, *, held: Optional[str] = "unknown") -> WorldState:
        """Take one measurement and return the snapshot the loop may act on."""
        if view not in self.views:
            raise ValueError(f"undeclared view {view!r}; this arm has {list(self.views)}")
        camera = self.camera_for_view(view)
        name = f"look_{len(self.percepts) + 1:04d}_{view}"
        frame, _, _ = self.capture_view(camera, name)
        try:
            reading = self.reader.read(frame, catalog=self.catalog, task=self.task)
        except ReadingError as e:
            raise PerceptionUnavailable(
                f"{self.channel} could not answer {view}: {e}",
                reasons=list(getattr(e, "reasons", []) or [])) from e
        percept = self.assembler.assemble(frame, reading, previous=self.tracker.percept,
                                          frame_index=len(self.percepts) + 1,
                                          episode_id=self.episode_id or None)
        state = self.tracker.observe(percept)
        # before grounding, because `held_object` is part of the snapshot the model reads and
        # an instrument that answered afterwards would have its own look called someone else's
        held, held_from = self.held_for_frame(held, state)
        grounded = ground_state(state, self.gmap, not_seen=self.tracker.unseen(view),
                                held=held, view=view, held_from=held_from)
        self.view_of_ref[str(state.observation_ref)] = view
        self.percepts.append(percept)
        return grounded


class PerceptRuntime(Runtime):
    """`Runtime` with the three perception seams moved to a camera.

    Everything else is inherited unchanged — the decision loop, the candidate offer, the
    validator, the ledger, the finish protocol, the evaluator boundary. What a reviewer
    should be able to check in one screen is that **no strategy lives here**."""

    def __init__(self, scene, executor, run_dir: str, budgets: Budgets, episode_id: str, *,
                 perceiver: Perceiver, gmap: GroundingMap, ablation=None,
                 config: VerifyConfig | None = None, environment=None, store=None):
        super().__init__(scene, executor, run_dir, budgets, episode_id, config=config,
                         environment=environment, store=store)
        self.perceiver = perceiver
        self.gmap = gmap
        self.ablation = ablation
        #: the camera the model asked for and the loop has not measured with yet
        self._requested_view: Optional[str] = None
        self._last_view = perceiver.default_view
        self._looks_unbilled = 0
        if ablation is not None:
            clash = arm_coherence(ablation, perceiver.channel)
            if clash:
                raise ValueError(f"arm {ablation.condition!r} cannot run on channel "
                                 f"{perceiver.channel!r}: " + "; ".join(clash))

    # ---------- arm identity ----------
    def _begin_episode(self) -> None:
        """Record which arm this episode ran in, before anything it governs is logged.

        The record carries the channel and the grounding table because both change what the
        same pictures mean: `stub` and `vlm` look at the same PNGs, and a run that cannot say
        which read them cannot report a VLM effect. `registry_sha256` makes the arm itself a
        registry-backed claim rather than a word in a note.

        The record's own fields are filled here, not passed as `log_record` keywords: the
        store expands the record into the payload, so a keyword that names an existing field
        is a duplicate argument, and a field this important must not be recorded as an
        afterthought beside it."""
        if self.ablation is not None:
            self.store.log_record(
                "ablation",
                self.ablation.model_copy(update={
                    "episode_id": self.episode_id,
                    "registry_sha256": self.ablation.registry_sha256 or registry_sha256(),
                    "notes": (f"perception channel {self.perceiver.channel}; "
                              f"views {list(self.perceiver.views)}; "
                              f"grounding map {self.gmap.sha256()[:12]}")}))

    # ---------- the three seams ----------
    def _capture_world(self, state_version: int, observation_ref: str) -> WorldState:
        view = self._requested_view or self._last_view
        self._requested_view = None
        self._last_view = view
        before = len(self.perceiver.percepts)
        state = self.perceiver.look(view, held=self._held_report())
        if len(self.perceiver.percepts) > before:
            percept = self.perceiver.percepts[-1]
            if int(state.state_version) != int(state_version):
                raise RuntimeError(
                    f"two ledgers: the tracker stamped v{state.state_version} for the "
                    f"snapshot the loop asked for as v{state_version}")
            if self.ablation is not None and self.ablation.enabled("vlm"):
                self.store.log_record("perception", percept,
                                      state_version=state_version, view=view)
            self._looks_unbilled += int(
                percept.provenance.get("http_requests_this_call") or 0)
        return state

    def _verifier(self, world: WorldState):
        view = self.perceiver.view_of(world.observation_ref) or self._last_view
        return SensingVerifier(world, self.config, view=view,
                               alt_views=[v for v in self.perceiver.views if v != view])

    def _state_diff(self, pre_world: WorldState, post_world: WorldState) -> dict:
        a = self.perceiver.view_of(pre_world.observation_ref)
        b = self.perceiver.view_of(post_world.observation_ref)
        if a and b and a != b:
            # P1-c's rule, applied to the feedback a decision is made from: one static table
            # reads tens of millimetres apart across two cameras, so nothing may be
            # concluded about motion from a change of viewpoint
            return {"moved": [], "state_changed": [], "position_unmeasured": [],
                    "held_before": str(pre_world.held_object),
                    "held_after": str(post_world.held_object),
                    "not_comparable": (f"{a} -> {b}: a difference between two cameras is not "
                                       f"a difference between two worlds")}
        return super()._state_diff(pre_world, post_world)

    # ---------- the model's own camera request ----------
    def _before_execute(self, call: SkillCall) -> None:
        if call.skill == "observe":
            view = str(call.args.get("view") or "")
            self._requested_view = view or None

    def _validate_execution(self, decision: Decision, ctx: DecisionContext,
                            world: WorldState) -> tuple[bool, list[str]]:
        ok, errs = super()._validate_execution(decision, ctx, world)
        args = dict(getattr(decision.execute, "args", {}) or {})
        view = str(args.get("view") or "")
        if view and view not in self.perceiver.views:
            ok = False
            errs = list(errs) + [
                f"view {view!r} is not a camera this arm has: {sorted(self.perceiver.views)}. "
                f"An `observe` has to name a viewpoint it can actually measure with"]
        return ok, errs

    # ---------- request accounting ----------
    def _requests_used(self, source, before) -> int:
        """The round's HTTP total, looks included: a vision request spends the same budget
        as a decision request, and an arm that billed only the decision would report a VLM
        episode as free (SPEC 6.2)."""
        total = int(Runtime._requests_used(source, before)) + self._looks_unbilled
        self._looks_unbilled = 0
        return total

    def _finalize(self, task, ledger, status, failure, note, mode):
        """The last look is inside the budget, not beside it.

        `_finish_check` measures the world once more when the loop stopped without asking to
        finish (budget exhausted, model error), and that look costs requests like any other.
        Flushing before it — the obvious reading of "charge the tail" — bills the episode for
        every look *except* the one the ledger is about to read the answer out of, so the
        reported total is one look short of the spent one. Taking the terminal measurement
        first is what makes the flush complete: the base class then finds `terminal_world`
        already set, spends no request of its own, and the number in the row is the number in
        the ledger."""
        if self.terminal_world is None and getattr(self, "goal", None) is not None:
            self._finish_check(self.goal)
        ledger.charge_requests(self._looks_unbilled)
        self._looks_unbilled = 0
        return super()._finalize(task, ledger, status, failure, note, mode)

    # ---------- the declared ceiling, honoured where it is spent ----------
    def _preflight_trip(self, ledger) -> tuple | None:
        """Stop at the HTTP ceiling this arm's own looks are spending.

        The base loop reports `http_requests_used` beside `max_http_requests` and never stops
        for the pair: on a rule policy the number is 0, across the 904 archived v0.1 episode
        rows the peak is 3 of a declared 32, and the text backend of the same loop family
        already enforces it at exactly this point (`runtime_alfred._preflight_trip`, SPEC-BST
        5.3). A camera changes the arithmetic rather than the contract — the measured figure
        here is 4.38 looks per decision round — so an arm that billed frames into the shared
        counter and then ignored it would spend three times the declared budget and still
        report `remaining 0`. §12.1's "same budgets across arms" is only a claim if the budget
        can end an episode on both sides of the comparison.

        Checked before the round, like every other budget in the loop (`runtime.py:333-344`),
        and never after a request that already went out."""
        if ledger.http_requests >= self.budgets.max_http_requests:
            return (TerminalStatus.failed, FailureCode.BUDGET_EXHAUSTED,
                    f"http request budget {self.budgets.max_http_requests} spent before the "
                    f"next round ({len(self.perceiver.percepts)} looks billed to "
                    f"{ledger.http_requests} requests)")
        return Runtime._preflight_trip(self, ledger)

    # ---------- the layer-1 channel ----------
    def _held_report(self) -> Optional[str]:
        """What the gripper says it holds, in the task's own naming.

        Read at the moment the snapshot is taken, not remembered from the last action: a
        `place` that ended in a timeout changes the hand without changing any plan step.
        `unknown` and `None` pass through untouched — the first is "two bodies, cannot tell",
        the second is "empty", and neither is a fact this arm may upgrade."""
        return self.executor.held_state()


def build_perceiver(*, case, scene, run_dir: str, episode_id: str, perceive: str,
                    adapter=None, out_dir: Optional[str] = None,
                    default_view: str = "main", views: Optional[Iterable[str]] = None,
                    task: Optional[TaskContext] = None, catalog=None,
                    gmap: Optional[GroundingMap] = None,
                    on_call=None) -> tuple["Perceiver", GroundingMap]:
    """Assemble one episode's camera, and hand back the grounding table it reads through.

    P5 joins the camera channel to the memory and acquisition arms, and those builders are given a
    *finished* perceiver rather than the parts: neither can reconstruct which body `obj_red_1` names
    without re-deciding the segmentation catalogue, and a second opinion about that is a second
    experiment. So the construction that used to sit inside `build_arm` lives here, `build_arm`
    calls it, and the two stacked arms call it too — one statement of how a look is wired.

    `gmap` is optional because it is derivable from the task declaration (`case.objects` is words
    the task chose, not a state query); `catalog` is not, and a caller that has a scene builds it
    with `evaluation/calibration.py:cell_catalog` over the scene's own tray inventory. That is not a
    style preference: §5.1's sensor channel may not ask the simulator where the receptacles are, so
    the package-boundary test forbids that read anywhere under `perception/`, and it has to sit on
    the caller's side of that line. `build_arm` has always been handed a catalogue; this function
    keeps the same rule and says so when it is not given one.

    `on_call` is the batch's request ledger. `VLMReader` has always built a row for every vision
    request it made — the row, not just the answer — and `observe.py:_file_row` returns at once when
    it is handed nothing, so a caller that left this at `None` had a camera arm whose spending was
    measurable in memory and absent from disk. That is v0.2 residual #109, and it is why a real
    `--perceive vlm` episode on the main cli billed a 4096-token generation, died on the zero-body
    refusal, and left no `model_calls.jsonl` row behind: the Cost group would have had a
    denominator of zero for a batch that spent. A `stub` channel ignores it (it makes no request).

    The map is *returned* because the caller has to give the same object to the runtime: two
    `GroundingMap.from_objects` calls would agree today and drift tomorrow, and the drift would show
    up as a percept grounding onto a body the plan cannot name.
    """
    reader: Any
    if perceive == "stub":
        reader = StubReader()
    elif perceive == "vlm":
        not_ready = channel_readiness("vlm", adapter)
        if not_ready:
            raise ValueError("; ".join(not_ready))
        reader = VLMReader(adapter, on_call=on_call)
    elif perceive == "privileged":
        raise ValueError("a privileged world has no camera to build: `perceive='privileged'` "
                         "means the loop reads simulator state, and a perceiver over the same "
                         "state would be a second, disagreeable account of it")
    elif not channel_is_declared(perceive):
        raise ValueError(f"unknown perception channel {perceive!r}; "
                         f"declared: privileged | stub | vlm")
    from ..evaluation.calibration import task_context_of

    gmap = gmap if gmap is not None else GroundingMap.from_objects(case.objects)
    if catalog is None:
        raise ValueError(
            "build_perceiver needs the caller's target catalogue: this package may not read the "
            "scene's receptacle inventory to make one (a sensor channel that asks the simulator "
            "where the trays are is not §5.1's channel). Build it with "
            "`evaluation/calibration.py:cell_catalog`, which is what `build_arm`'s callers already "
            "do")
    perceiver = Perceiver(scene, catalog, gmap,
                          out_dir=out_dir or os.path.join(run_dir, "perception"),
                          reader=reader, arm_channel=perceive,
                          task=task or TaskContext(**task_context_of(case)),
                          episode_id=episode_id, default_view=default_view, views=views)
    return perceiver, gmap


def arm_for_channel(perceive: str, ablation=None):
    """The arm a camera channel implies when nobody declared one, and only that.

    `stub` is §9's *limited semantic baseline*: a reading from colour segmentation that consults no
    vision model, so the module that owns the `perception` record is off and the row must be named
    `wo_vlm` — never `full`, which is the substitution `arm_coherence` exists to catch. `vlm` is the
    opposite: a model was consulted, so the default claim is `full`. A declared arm is passed
    through untouched; this function never overrides an operator.
    """
    from ..core.v02 import ablation as ablation_record

    if ablation is not None or perceive == "privileged":
        return ablation
    return ablation_record("wo_vlm" if perceive == "stub" else "full")


def build_arm(*, case, scene, executor, store, run_dir: str, episode_id: str, perceive: str,
              catalog, gmap: GroundingMap, budgets: Budgets, environment=None,
              ablation=None, adapter=None, out_dir: Optional[str] = None,
              default_view: str = "main", views: Optional[Iterable[str]] = None,
              task: Optional[TaskContext] = None, on_call=None) -> Runtime:
    """The one place an arm is assembled, so a runner cannot build a half-switched loop.

    `perceive='privileged'` returns the base `Runtime`: P1-e adds nothing to that path, and
    the v0.1 regression is exactly the claim that it stays untouched."""
    if perceive == "privileged":
        if ablation is not None and ablation.enabled("vlm"):
            raise ValueError("a privileged world is not the full system: channel 'privileged' "
                             "consults no vision model, so condition "
                             f"{ablation.condition!r} cannot be claimed for it")
        return Runtime(scene, executor, run_dir, budgets, episode_id, config=case.verify,
                       environment=environment, store=store)
    perceiver, gmap = build_perceiver(case=case, scene=scene, run_dir=run_dir,
                                      episode_id=episode_id, perceive=perceive, adapter=adapter,
                                      out_dir=out_dir, default_view=default_view, views=views,
                                      task=task, catalog=catalog, gmap=gmap, on_call=on_call)
    arm = arm_for_channel(perceive, ablation)
    rt = PerceptRuntime(scene, executor, run_dir, budgets, episode_id, perceiver=perceiver,
                        gmap=gmap, ablation=arm, config=case.verify, environment=environment,
                        store=store)
    return rt


__all__ = ["Perceiver", "PerceptRuntime", "PerceptionUnavailable", "SensingVerifier",
           "GRASP_UNMEASURED", "arm_for_channel", "build_arm", "build_perceiver"]
