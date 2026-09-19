"""Frozen experiment task sets (SPEC 11.1, 10.1-10.3, 12.1).

Everything a run must not invent at run time lives here: the configurations,
seeds, layouts, instruction text, environment events, public budgets and the
independently authored hidden `EvalSpec`. `build_set(name)` is deterministic, and
`frozen_manifest()` is written to `configs/experiment/` and hash-checked by a
test, so "frozen" is an executable claim rather than a comment.

Sets
    smoke      4  wiring check only; never reported as a result
    dev        8  physics / prompt / budget calibration (SPEC 11.1: 8 configs)
    formal    24  clean 8 + state-change 8 + execution-deviation 8, x3 runs = 72 per mode
    protocol 11  boundary cases a scene can express, excluded from the success
                 denominator; the other 11 SPEC 11.1 bullets are PROTOCOL_HARNESS
                 cases owned by named tests, for 22 boundary cases in all
    clean / state_change / execution_deviation
               the three `formal` subsets, by name
    all        47  the four registered sets above, each case once

`SET_NAMES` is the list a caller may ask for, and `build_set` resolves exactly it;
a name accepted by `--set` that resolved to nothing would be a silent empty run.

Three claims stay separate in this module (SPEC 1.2, 12.1.1):
    * what the agent believed  -> built from the *utterance* via `interpret`
    * what the environment did -> `events`, visible to the runner, never to a
      `DecisionContext`
    * what actually happened   -> hidden `EvalSpec`, read only by the evaluator

A case may therefore be *expected* to fail: `goal_truth_mismatch` below is a
deliberate wrong-goal-vs-hidden-answer case whose correct outcome is an agent
that reports success and scores zero.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from ..core.contracts import Budgets, EvalSpec, VerifyConfig
from ..core.fault_injection import EnvironmentEvent, EventAction, EventTrigger

TRAYS = ["tray_left", "tray_middle", "tray_right"]
# One canonical frozen file: the writer, the `--frozen` gate and the test all
# default to this path, so "the manifest was checked" and "the manifest was
# written" cannot be claims about two different files (SPEC 7, 11.1).
FROZEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "configs", "experiment", "frozen_tasks_v1.json")
# See `_respect_region_capacity`: measured tray capacity, with slack.
MAX_OBJECTS_PER_REGION = 3
COLORS = ["red", "green", "blue", "yellow", "purple"]
COLOR_ZH = {"red": "红色", "green": "绿色", "blue": "蓝色", "yellow": "黄色", "purple": "紫色"}
SHAPES = ["cube", "cuboid", "cylinder"]
SHAPE_ZH = {"cube": "方块", "cuboid": "长方体", "cylinder": "圆柱"}
TARGET_ZH = {"tray_left": "左边托盘", "tray_middle": "中间托盘", "tray_right": "右边托盘"}
PALETTE = {
    "red": [0.85, 0.1, 0.1, 1], "green": [0.1, 0.7, 0.2, 1], "blue": [0.1, 0.2, 0.85, 1],
    "yellow": [0.9, 0.8, 0.1, 1], "purple": [0.5, 0.2, 0.7, 1], "grey": [0.45, 0.45, 0.45, 1],
}
# dims are HALF extents for a box (x, y, z) and (radius, half-height) for a cylinder
DIMS = {"cube": [0.021, 0.021, 0.021], "cuboid": [0.027, 0.019, 0.035], "cylinder": [0.023, 0.042]}

# Spawning slots: inside the verified reach envelope of the fixed base
# (base_z=0.30), pairwise non-overlapping, and clear of every tray footprint
# (trays span x >= 0.517 with |y - tray_y| <= 0.143).
SLOTS = [
    (0.38, -0.16), (0.38, 0.0), (0.38, 0.16),
    (0.445, -0.16), (0.445, 0.0), (0.445, 0.16),
]

# --- frozen disturbance magnitudes (calibrated on `dev` only, SPEC 10.3) ---
# Measured, not derived: work/p0_impulse.py, work/p0_impulse2.py and
# work/p0_impulse3.py place an object with the real skills, apply the declared
# impulse, and then run the *recovery* the agent will have to attempt (pick it, put
# it back), on every candidate slot the planner offers and on four seeds.
#
# Direction, not magnitude, is what makes this mechanism usable. A placement is
# valid while `|offset| + footprint <= inner_half - margin` (0.126 m) and the wall
# stops a sliding box at 0.131 m, so *any* in-plane shove inside a tray invalidates
# the relation by exactly `footprint_margin_m` = 5 mm and never further: the object
# is pinned against the wall. What varies is whether the pinned pose is still
# reachable. The old `+y` value carried a tray_right object to y=0.43, which is
# 0.75 m from the arm base and outside IK — the completed goal became false and
# stayed false forever (sc_c1 0/3, sc_c3 1/4, ending `REPEATED_INVALID`). `-x` is
# toward the robot: radial 0.50-0.62 m, and the recovery loop closed on 92/92
# trials at this magnitude (all three trays, all offered slots, 4 seeds).
#
# Magnitude then decides whether the wall is reached at all, and the answer is a
# plateau, not a threshold: for a cube, 0.05-0.09 N.s all pin it against the wall
# (0.04 leaves the far slots short by 14 mm; 0.10 makes it rebound back inside by
# 1-8 mm). 0.07 is the middle of that plateau.
IMPULSE_PIN_TO_WALL = [-0.07, 0.0]
# small enough to stay inside the region: a completed goal may still hold while
# the slots the next placement assumed are no longer free (measured 58 mm of
# travel, the footprint stays 45 mm clear of the validity limit, graspable)
IMPULSE_SHIFT_INSIDE = [-0.03, 0.0]
# a still-pending object is slid across the table: nothing has been satisfied
# yet, but the grasp and slot geometry the plan assumed is no longer the scene
# (measured 175 mm of travel, still on the table, at rest, supported by it). +y
# only, because -x on the table walks off its near edge at x=0.33.
IMPULSE_TABLE = [0.0, 0.055]
# diagonal so the fingers demonstrably close beside the object (0.071 m >
# object half-extent + maximum finger travel)
GRASP_OFFSET = [0.05, 0.05, 0.0]
# released 5.5 cm above the seated height: the hand-off happens, the outcome is
# not what the candidate promised
RELEASE_OFFSET_HIGH = [0.0, 0.0, 0.055]
# and 5 cm to the side of it, which is outside the region altogether
RELEASE_OFFSET_LATERAL = [-0.05, 0.0, 0.0]


@dataclass
class TaskCase:
    """One frozen episode definition.

    `objects` is the *scene* (including props that are not part of the task);
    `targets` are the entities the utterance is about. `budgets` and `verify` are
    public and reach the model; `eval_spec` and `events` do not.
    """

    task_id: str
    utterance: str
    seed: int
    subset: str  # dev | clean | state_change | execution_deviation | protocol | smoke
    objects: list[dict]
    targets: list[str]
    eval_spec: EvalSpec
    budgets: Budgets = field(default_factory=Budgets)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    events: list[EnvironmentEvent] = field(default_factory=list)
    expected: str = "success"  # success | needs_clarification | bounded_exit | goal_truth_mismatch
    probe: str = ""
    notes: str = ""

    @property
    def n_objects(self) -> int:
        return len(self.targets)

    def fresh_events(self) -> list[EnvironmentEvent]:
        """A per-episode copy of the declared events.

        `fired` / `matches_seen` are *episode* state, but they live on the frozen
        declaration, and one case drives many episodes (3 modes x 3 repeats).
        Handing out the shared objects would let the first episode consume the
        event and quietly spare every later one — the modes would not even face
        the same scenario (SPEC 10.3: same event policy for every mode)."""
        from dataclasses import replace

        return [replace(e, fired=False, matches_seen=0, fired_at={}) for e in self.events]

    def event_specs(self) -> list[dict]:
        return [
            {"event_id": e.event_id, "trigger": e.trigger.value, "action": e.action.value,
             "skill": e.skill, "when_entity_id": e.when_entity_id,
             "when_target_id": e.when_target_id, "at_match": e.at_match,
             "entity_id": e.entity_id, "target_id": e.target_id,
             "impulse_xy": list(e.impulse_xy), "offset": list(e.offset) if e.offset else None,
             "once": e.once}
            for e in self.events
        ]

    def public_payload(self) -> dict:
        """What the runner may hand to the agent chain: budgets and public
        verification parameters. Never `eval_spec`, never `events`."""
        return {"budgets": self.budgets.model_dump(mode="json"),
                "verify": self.verify.model_dump(mode="json")}


# ---------------------------------------------------------------- layout ------


def make_objects(n: int, seed: int, shapes: list[str] | None = None) -> list[dict]:
    """Deterministic, colour-unique objects in fixed slots.

    Attribute binding in `interpret` requires a colour/shape pair that matches
    exactly one entity, so colours never repeat within a case.
    """
    shift = seed % len(COLORS)
    cycle = COLORS[shift:] + COLORS[:shift]
    objs = []
    for i in range(n):
        shape = shapes[i] if shapes else SHAPES[(i + seed) % len(SHAPES)]
        color = cycle[i % len(COLORS)]
        objs.append(_object(f"obj_{color}_{i + 1}", shape, color, SLOTS[i % len(SLOTS)]))
    return objs


def _object(entity_id: str, shape: str, color: str, xy: tuple[float, float],
            dims: list[float] | None = None) -> dict:
    return {
        "entity_id": entity_id,
        "shape": shape,
        "dims": list(dims or DIMS[shape]),
        "xy": [float(xy[0]), float(xy[1])],
        "color": PALETTE[color],
        "attributes": {"color": color, "shape": shape},
    }


def assign_targets(objs: list[dict], seed: int, shared: bool = False) -> dict[str, str]:
    """Ground-truth binding, authored without any model in the loop.

    `shared=True` forces two objects into one region, which is what makes both a
    legal-ordering question (SPEC 11.1 clean) and the state-change demo
    possible (SPEC 10.1).
    """
    import numpy as np

    rng = np.random.default_rng(seed + 1000)
    assignment = {o["entity_id"]: TRAYS[int(rng.integers(0, len(TRAYS)))] for o in objs}
    if shared and len(objs) >= 2:
        tray = TRAYS[(seed + 1) % len(TRAYS)]
        assignment[objs[0]["entity_id"]] = tray
        assignment[objs[1]["entity_id"]] = tray
    _respect_region_capacity(objs, assignment)
    return assignment


def _respect_region_capacity(objs: list[dict], assignment: dict[str, str]) -> None:
    """Never author a task the hardware cannot complete.

    Measured floor (work/p0_capacity.py): with the hand's 50 mm descent envelope
    and the reachable lattice, a *side* tray has 4 mutually clear slots at the
    largest object size and the middle tray 6 — and even that assumes objects
    stop exactly on a lattice point, which they do not. Asking for more than
    MAX_OBJECTS_PER_REGION turns the episode into a physical impossibility that
    measures nothing (SPEC 11.1: the task list must be valid before it is frozen).
    Overflow moves to the emptiest region, in object order, so the forced shared
    pair is never the part that moves.
    """
    counts: dict[str, int] = {}
    for o in objs:
        eid = o["entity_id"]
        taken = assignment[eid]
        counts[taken] = counts.get(taken, 0) + 1
        if counts[taken] <= MAX_OBJECTS_PER_REGION:
            continue
        spare = min((r for r in TRAYS if counts.get(r, 0) < MAX_OBJECTS_PER_REGION),
                    key=lambda r: (counts.get(r, 0), TRAYS.index(r)))
        counts[taken] -= 1
        counts[spare] = counts.get(spare, 0) + 1
        assignment[eid] = spare


def explicit_utterance(objs: list[dict], assignment: dict[str, str]) -> str:
    return "，".join(
        f"把{COLOR_ZH[o['attributes']['color']]}的{SHAPE_ZH[o['attributes']['shape']]}"
        f"放到{TARGET_ZH[assignment[o['entity_id']]]}" for o in objs) + "。"


def class_utterance(objs: list[dict], assignment: dict[str, str]) -> str:
    """Attribute-class phrasing with a trailing 'the rest' clause."""
    parts = [f"把{COLOR_ZH[o['attributes']['color']]}物体放入{TARGET_ZH[assignment[o['entity_id']]]}"
             for o in objs[:-1]]
    parts.append(f"其余放到{TARGET_ZH[assignment[objs[-1]['entity_id']]]}")
    return "，".join(parts) + "。"


def eval_spec_for(objs: list[dict], assignment: dict[str, str], eval_id: str,
                  override: dict[str, str] | None = None) -> EvalSpec:
    """Hidden truth from the authored assignment, optionally re-pointed.

    A `goal_truth_mismatch` case passes `override` to state a *different* truth
    from the one its own utterance implies. Nothing downstream reconciles them:
    the agent may only read the utterance (SPEC 12.1.1).
    """
    truth = dict(assignment)
    truth.update(override or {})
    return EvalSpec(
        eval_id=eval_id,
        assignments=[{"entity_id": o["entity_id"], "target_id": truth[o["entity_id"]]}
                     for o in objs if o["entity_id"] in truth],
    )


# ---------------------------------------------------------------- events ------


def _displace_region_event(case_id: str, tray: str, victim: str, at_match: int = 1,
                           impulse: list[float] | None = None,
                           on_own_placement: bool = False) -> EnvironmentEvent:
    """SPEC 10.1: when an object is handed off into `tray`, the environment applies
    a frozen short impulse to `victim`, provided it is measurably resting there
    already. The trigger is a scene condition, not a step index, so the policy is
    identical for every mode whatever order they act in; the victim is named by the
    case rather than resolved from whatever happens to be in the tray, because only
    one body shape makes the declared effect reproducible (`_displaceable_in`).

    `on_own_placement` is what makes `displace_placed` mean what its name says: the
    trigger names the victim as well as the region, so the impulse is applied to the
    handoff that has just completed. Without it the first handoff into the tray
    fires the event even when that was a *different* body, and the named victim is
    still on the table — an episode that silently measures nothing instead of a
    completed relation invalidated after the fact.

    What the four `displace_placed` cases were measured to do (`sc_c1/sc_c2/sc_c5/
    sc_c8`, mode A and mode B, and the same shape in the smoke case): the event
    fires once per episode, slides the named cube to the wall (`after_xy` x within
    0.4 mm of 0.550 m in all four, however different the shove was — 0.150 m at
    sc_c1, 0.030 m at sc_c8) and leaves `placed` false in the verification of the
    post-event snapshot (xy_err 0.108-0.110 against 0.126 minus a 0.023 footprint).
    The skill's own reading still says the release landed inside, because it did:
    the two records describe two snapshots and SPEC 10.3 forbids blending them.
    The other handoff into the same region keeps reading true, and the pinned cube
    stays graspable where it ended up, so `expected: success` asks only for a policy
    that re-picks it — mode A's one-shot plan fails these cases 2/3 with three false
    finishes, mode B's continuous loop wins them 3/3.

    The one thing these cases do *not* measure, stated because the subset header
    might otherwise be read as claiming it: the impulse lands in the same round as
    the release, so the victim's relation is never *observed* true and there is no
    true->false transition in the transcript. "A completed relation was later
    undone" is realized by `displace_second` (at_match=2, no `when_entity_id`), where
    the first object is seen satisfied and the second handoff moves it — which is
    why pairs `sp03`/`sp04` take `sc_c3`/`dev_c2` as their base case, and why
    `_pair_satisfied_vs_displaced` would report these four as unreachable.
    `test_the_declared_impulse_undoes_the_placement_it_names_and_no_other` holds all
    of the above against a live episode."""
    return EnvironmentEvent(
        event_id=f"{case_id}-displace-{tray}-{victim}-m{at_match}",
        trigger=EventTrigger.AFTER_SKILL,
        action=EventAction.IMPULSE, skill="place", when_target_id=tray, at_match=at_match,
        when_entity_id=victim if on_own_placement else None,
        entity_id=victim, target_id=tray, impulse_xy=list(impulse or IMPULSE_PIN_TO_WALL))


def _displaceable_in(tray: str, objs: list[dict], assignment: dict[str, str]) -> str:
    """The object the disturbance is named on: the *cube* assigned to that tray.

    The mechanism only measures a decision if its declared effect happens every
    time, and that is a property of the body being shoved
    (work/p0_impulse3.py): an upright cylinder topples and rolls ~10 cm, ending on
    its side where the upright grasp geometry the skills offer cannot reach it; a
    cuboid is tall enough to rock on the 25 mm wall top and bounce back inside the
    valid region, non-monotonically in the impulse (102 mm of travel at 0.06 N.s,
    91 mm at 0.11, 62 mm at 0.18); a cube — centre of mass below the wall top —
    slides to the wall and stays there for every magnitude from 0.05 to 0.09 N.s
    and every slot the planner offers. So "a completed relation became false
    again" is realized on the cube, and a case that asks for this mechanism without
    one is not a valid task before it is frozen (SPEC 11.1) rather than an episode
    that silently measures nothing."""
    cubes = [o["entity_id"] for o in sorted(objs, key=lambda o: o["entity_id"])
             if assignment.get(o["entity_id"]) == tray and o["attributes"]["shape"] == "cube"]
    if not cubes:
        raise ValueError(f"{tray} has no cube among {sorted(assignment)}: the impulse "
                         "mechanism is only realizable on the shape whose slide against "
                         "the wall is deterministic (see _displaceable_in)")
    return cubes[0]


def _impulse_named_event(case_id: str, entity: str, impulse: list[float], *,
                         skill: str, at_match: int = 1) -> EnvironmentEvent:
    """A disturbance naming its victim: the same policy for every mode, applied
    when the declared scene condition occurs whatever order got there."""
    return EnvironmentEvent(
        event_id=f"{case_id}-impulse-{entity}", trigger=EventTrigger.AFTER_SKILL,
        action=EventAction.IMPULSE, entity_id=entity, skill=skill, at_match=at_match,
        impulse_xy=list(impulse))


def _calibration_event(case_id: str, action: EventAction, skill: str, offset: list[float],
                       entity: str | None = None, at_match: int = 1,
                       tray: str | None = None) -> EnvironmentEvent:
    return EnvironmentEvent(
        event_id=f"{case_id}-{action.value}-{entity or tray or skill}-m{at_match}",
        trigger=EventTrigger.BEFORE_SKILL, action=action, skill=skill,
        when_entity_id=entity, when_target_id=tray, at_match=at_match,
        entity_id=entity, target_id=tray, offset=list(offset))


# --------------------------------------------------------------- configs ------
# Each config is one frozen scene. `runs` (x3) is applied by the runner, not by
# duplicating configs: a repeat is not an independent task (SPEC 11.1, 11.4).


def _cfg(name, subset, n, seed, *, shared=False, style="explicit", mechanism=None,
         budget=None, verify=None, expected="success", probe="", notes="", props=None,
         shapes=None):
    return {
        "name": name, "subset": subset, "n": n, "seed": seed, "shared": shared,
        "style": style, "mechanism": mechanism, "budget": budget or {},
        "verify": verify or {}, "expected": expected, "probe": probe, "notes": notes,
        "props": props or [], "shapes": shapes,
    }


# --- dev: 8 configs, deliberately covering every mechanism ------------------
DEV_CONFIGS = [
    _cfg("dev_c1", "dev", 3, 101),
    _cfg("dev_c2", "dev", 3, 102, shared=True, mechanism="displace_second",
         notes="the SPEC 10.1 demo: the shared pair's first completion is undone "
               "when the second one is handed off into the same tray"),
    _cfg("dev_c3", "dev", 4, 103, mechanism="grasp_offset"),
    _cfg("dev_c4", "dev", 4, 104, shared=True, mechanism="release_high"),
    _cfg("dev_c5", "dev", 5, 105, style="class"),
    _cfg("dev_c6", "dev", 5, 106, shared=True, mechanism="release_lateral"),
    _cfg("dev_c7", "dev", 3, 107, mechanism="occupancy_shift"),
    _cfg("dev_c8", "dev", 4, 108, mechanism="impulse_unplaced"),
]

# --- formal: 24 configs, none used for tuning (SPEC 11.1) -------------------
FORMAL_CONFIGS = [
    # clean: 3-5 objects, shared regions, several legal orders, no events
    _cfg("clean_c1", "clean", 3, 201),
    _cfg("clean_c2", "clean", 3, 202, shared=True),
    _cfg("clean_c3", "clean", 4, 203, shared=True),
    _cfg("clean_c4", "clean", 4, 204),
    _cfg("clean_c5", "clean", 5, 205),
    _cfg("clean_c6", "clean", 5, 206, shared=True),
    _cfg("clean_c7", "clean", 3, 207, shapes=["cylinder", "cylinder", "cube"]),
    _cfg("clean_c8", "clean", 4, 208, shapes=["cuboid", "cuboid", "cylinder", "cube"]),
    # state_change: the environment invalidates a placement (measured: after the
    # release, before that object's relation is ever observed true — see
    # `_displace_region_event`), or occupancy makes the original plan unavailable
    _cfg("sc_c1", "state_change", 3, 211, shared=True, mechanism="displace_placed",
         shapes=["cylinder", "cube", "cuboid"],
         notes="cube authored into the shared pair: the wall-pin mechanism is only "
               "reproducible on that shape (see _displaceable_in). Measured: fires, "
               "verified false next round, the other completion survives, the pinned "
               "cube stays graspable — mode A fails 2/3, mode B wins (see "
               "_displace_region_event)"),
    _cfg("sc_c2", "state_change", 3, 212, shared=True, mechanism="displace_placed",
         notes="shared region measured on tray_left; same mechanism as sc_c1"),
    _cfg("sc_c3", "state_change", 4, 213, shared=True, mechanism="displace_second",
         notes="the second handoff moves the *first* object: the only form of this "
               "mechanism with an observed true->false flip, hence pair sp03"),
    _cfg("sc_c4", "state_change", 4, 214, shared=True, mechanism="occupancy_shift"),
    _cfg("sc_c5", "state_change", 5, 215, shared=True, mechanism="displace_placed",
         notes="five objects, three sharing one region: the invalidation of one "
               "placement must not disturb the other two completions"),
    _cfg("sc_c6", "state_change", 5, 216, shared=True, mechanism="impulse_unplaced"),
    _cfg("sc_c7", "state_change", 4, 217, mechanism="impulse_unplaced",
         notes="disturbs a still-pending object before it is ever picked"),
    _cfg("sc_c8", "state_change", 3, 218, shared=True, mechanism="displace_placed",
         shapes=["cuboid", "cube", "cylinder"],
         notes="the smallest measured shove of the four (0.030 m) is still decisive, "
               "because the margin is 0.126 m minus the footprint"),
    # execution-deviation: the action ends somewhere other than what was asked
    _cfg("ed_c1", "execution_deviation", 3, 221, mechanism="grasp_offset"),
    _cfg("ed_c2", "execution_deviation", 3, 222, shared=True, mechanism="release_high"),
    _cfg("ed_c3", "execution_deviation", 4, 223, mechanism="release_lateral"),
    _cfg("ed_c4", "execution_deviation", 4, 224, mechanism="grasp_offset",
         notes="offset armed on the second pick"),
    _cfg("ed_c5", "execution_deviation", 4, 225, shared=True, mechanism="release_lateral"),
    _cfg("ed_c6", "execution_deviation", 5, 226, shared=True, mechanism="release_high"),
    _cfg("ed_c7", "execution_deviation", 5, 227, mechanism="grasp_offset"),
    _cfg("ed_c8", "execution_deviation", 3, 228, mechanism="release_high"),
]

# --- protocol / boundary: >= 12, kept out of the success denominator ---------
# One case per SPEC 11.1 bullet that a scene can express (11 here); the bullets
# that need a specific *model payload* (a bad or absent response, a held-state
# conflict, a stale candidate) are listed in PROTOCOL_HARNESS and owned by named
# tests over the real Runtime, so the boundary total is 11 + 11 = 22 >= 12.
PROTOCOL_CONFIGS = [
    _cfg("pr_unknown_entity_id", "protocol", 3, 231, expected="needs_clarification",
         probe="clarifies", notes="explicit entity id that is not in the snapshot"),
    _cfg("pr_unknown_target", "protocol", 3, 232, expected="needs_clarification",
         probe="clarifies", notes="target region does not exist in this world"),
    _cfg("pr_no_assignment", "protocol", 3, 233, expected="needs_clarification",
         probe="clarifies", notes="utterance carries no binding at all"),
    _cfg("pr_partial_binding", "protocol", 3, 234, expected="needs_clarification",
         probe="clarifies", notes="two valid bindings plus one unresolvable "
                                  "reference: never guess the third"),
    _cfg("pr_stacking_out_of_scope", "protocol", 3, 243, expected="needs_clarification",
         probe="clarifies", notes="rest an object on another object: not an S1 target"),
    _cfg("pr_target_full", "protocol", 1, 235, expected="bounded_exit",
         probe="bounded_no_blind_place", shapes=["cuboid"],
         notes="no candidate clears the occupant: exit, never blind-place. The "
               "pre-check is conservative, so this shows boundedness, not that "
               "the tray was physically full",
         props=[{"entity_id": "obj_blocker_1", "shape": "cuboid", "dims": [0.06, 0.06, 0.02],
                 "xy": [0.66, 0.0], "attributes": {"color": "grey", "shape": "cuboid"}}]),
    _cfg("pr_skill_budget", "protocol", 3, 236, expected="bounded_exit",
         probe="bounded_budget_exhausted", budget={"max_skill_calls": 4},
         notes="3 objects need 6 skill calls; max_skill_calls=4"),
    _cfg("pr_round_budget", "protocol", 3, 237, expected="bounded_exit",
         probe="bounded_budget_exhausted", budget={"max_decision_rounds": 2},
         notes="decision rounds run out before the work does"),
    _cfg("pr_wall_budget", "protocol", 3, 238, expected="bounded_exit",
         probe="bounded_wall_clock", budget={"wall_clock_s": 0.2},
         notes="wall clock is its own budget, separate from rounds. 0.2 s is below "
               "the cheapest possible completion of this case in any mode (a "
               "3-object rule-policy episode measures 1.1-1.8 s of wall time), so "
               "the budget binds on the host it is run on rather than on how fast "
               "that host happens to be"),
    _cfg("pr_zero_skill_budget", "protocol", 3, 244, expected="bounded_exit",
         probe="bounded_before_action", budget={"max_skill_calls": 0},
         notes="nothing may be executed at all: no skill_call event is legal"),
    _cfg("pr_goal_truth_mismatch", "protocol", 1, 239, expected="goal_truth_mismatch",
         probe="independent_score_zero",
         notes="utterance and hidden truth deliberately disagree: agent success "
               "must not be able to buy score"),
]

PROTOCOL_HARNESS = [
    {"name": "h_hold_state_conflict", "asserts": "place while the gripper holds another entity is rejected before physics"},
    {"name": "h_missing_argument", "asserts": "a decision missing a required argument is rejected as INVALID_DECISION"},
    {"name": "h_stale_candidate", "asserts": "a candidate id never offered in this episode returns CANDIDATE_STALE"},
    {"name": "h_infeasible_candidate", "asserts": "a named candidate failing re-check is rejected while the object stays held"},
    {"name": "h_unknown_predicate", "asserts": "held/support/at_rest unknown propagate as unknown, not as satisfied"},
    {"name": "h_false_finish", "asserts": "finish with unsatisfied goals is refused and bounded"},
    {"name": "h_repeated_invalid", "asserts": "the identical invalid action under an unchanged world terminates the episode"},
    {"name": "h_semantic_repair_limit", "asserts": "rejections beyond max_semantic_repairs terminate instead of looping"},
    {"name": "h_empty_model_response", "asserts": "an empty model payload is a MODEL_ERROR outcome, not a crash"},
    {"name": "h_schema_error", "asserts": "a schema-invalid Decision is rejected with structured reasons"},
    {"name": "h_api_timeout", "asserts": "a transport timeout charges the request ledger and stays bounded"},
]


# ------------------------------------------------------------- case builder --


def build_case(cfg: dict) -> TaskCase:
    """Turn one frozen config into a TaskCase.

    Object identities, the utterance and the hidden truth all come from the same
    seed, and none of them comes from a model.
    """
    name, subset, seed = cfg["name"], cfg["subset"], cfg["seed"]
    objs = make_objects(cfg["n"], seed, cfg.get("shapes"))
    assignment = assign_targets(objs, seed, shared=cfg["shared"])
    props = [dict(p, attributes=p.get("attributes", {"color": "grey", "shape": p["shape"]}))
             for p in cfg.get("props", [])]
    for p in props:
        p.setdefault("color", PALETTE[p["attributes"]["color"]])
        p.setdefault("dims", DIMS[p["shape"]])

    override = None
    if name == "pr_goal_truth_mismatch":
        # the hidden answer is re-pointed one region clockwise from the one the
        # utterance itself implies; the agent has no way to see this
        first = objs[0]["entity_id"]
        override = {first: TRAYS[(TRAYS.index(assignment[first]) + 1) % len(TRAYS)]}

    utterance = _utterance_for(cfg, objs, assignment)
    spec = eval_spec_for(objs, assignment, f"eval-{name}", override=override)
    if name == "pr_target_full":
        # the only legal region is already occupied by the prop
        spec = eval_spec_for(objs, {objs[0]["entity_id"]: "tray_middle"}, f"eval-{name}")

    case = TaskCase(
        task_id=name, utterance=utterance, seed=seed, subset=subset,
        objects=objs + props, targets=[o["entity_id"] for o in objs],
        eval_spec=spec,
        budgets=Budgets(**cfg["budget"]) if cfg["budget"] else Budgets(),
        verify=VerifyConfig(**cfg["verify"]) if cfg["verify"] else VerifyConfig(),
        events=_events_for(name, cfg, objs, assignment),
        expected=cfg["expected"], probe=cfg["probe"], notes=cfg["notes"])
    return case


# Utterances a scene cannot express by itself. Each one is still a frozen string,
# authored without a model in the loop, and each is paired with the hidden truth
# it is meant to exercise.
FIXED_UTTERANCES = {
    "pr_unknown_entity_id": "把 obj_ghost_1 放到左边托盘。",
    "pr_unknown_target": "把红色方块放到抽屉里。",
    "pr_no_assignment": "开始整理吧。",
    "smoke_clarify": "把那个东西收好。",
}


def _utterance_for(cfg: dict, objs: list[dict], assignment: dict[str, str]) -> str:
    name = cfg["name"]
    if name in FIXED_UTTERANCES:
        return FIXED_UTTERANCES[name]
    if name == "pr_stacking_out_of_scope":
        return (f"把{COLOR_ZH[objs[0]['attributes']['color']]}方块放到"
                f"{COLOR_ZH[objs[1]['attributes']['color']]}长方体上面。")
    if name == "pr_partial_binding":
        # two bindings the scene can resolve, one that matches nothing: a partial
        # goal must clarify rather than quietly drop the third object (SPEC 4.2)
        victim = objs[-1]
        wrong_shape = next(s for s in SHAPES if s != victim["attributes"]["shape"])
        third = (f"把{COLOR_ZH[victim['attributes']['color']]}的{SHAPE_ZH[wrong_shape]}"
                 f"放到{TARGET_ZH[assignment[victim['entity_id']]]}")
        return explicit_utterance(objs[:-1], assignment).rstrip("。") + f"，{third}。"
    if cfg["style"] == "class":
        return class_utterance(objs, assignment)
    return explicit_utterance(objs, assignment)


def _events_for(name: str, cfg: dict, objs: list[dict], assignment: dict[str, str]) -> list[EnvironmentEvent]:
    mech = cfg["mechanism"]
    if mech is None:
        return []
    if mech in ("displace_placed", "displace_second"):
        # a satisfied relation is invalidated after the fact (SPEC 10.1). The
        # placed one is disturbed by its own handoff; `displace_second` is the
        # variant where a *later* handoff into the same region disturbs the first.
        tray = _shared_tray(objs, assignment)
        return [_displace_region_event(name, tray, at_match=1 if mech == "displace_placed" else 2,
                                       on_own_placement=mech == "displace_placed",
                                       victim=_displaceable_in(tray, objs, assignment))]
    if mech == "occupancy_shift":
        # the region changes shape around the plan: the victim stays where it
        # was handed off, but the slots the next placement assumed are no longer
        # the same geometry
        tray = _shared_tray(objs, assignment)
        return [_displace_region_event(name, tray, impulse=IMPULSE_SHIFT_INSIDE,
                                       victim=_displaceable_in(tray, objs, assignment))]
    if mech == "impulse_unplaced":
        return [_impulse_named_event(name, objs[-1]["entity_id"], IMPULSE_TABLE,
                                     skill="pick", at_match=1)]
    if mech == "grasp_offset":
        target = objs[1 if name == "ed_c4" else 0]["entity_id"]
        return [_calibration_event(name, EventAction.GRASP_CALIBRATION, "pick", GRASP_OFFSET,
                                   entity=target)]
    if mech == "release_high":
        return [_calibration_event(name, EventAction.PLACE_CALIBRATION, "place",
                                   RELEASE_OFFSET_HIGH, tray=_shared_tray(objs, assignment))]
    if mech == "release_lateral":
        return [_calibration_event(name, EventAction.PLACE_CALIBRATION, "place",
                                   RELEASE_OFFSET_LATERAL, tray=_shared_tray(objs, assignment))]
    raise ValueError(f"unknown mechanism {mech}")


def _shared_tray(objs: list[dict], assignment: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for eid in [o["entity_id"] for o in objs]:
        counts[assignment[eid]] = counts.get(assignment[eid], 0) + 1
    shared = [t for t, c in counts.items() if c > 1]
    return shared[0] if shared else assignment[objs[0]["entity_id"]]


# --------------------------------------------------- state-utilization pairs --
# SPEC 11.3: frozen pairs of valid DecisionContexts that differ in exactly one
# task-relevant respect, each with its acceptable action set defined from the
# public preconditions *before* any model sees it. The acceptable set is a
# constraint, never a single answer string. `state_utilization.py` reaches these
# states through real skills and asks for one decision per context (12 pairs x 1
# request = 24), including a candidate-order permutation control.
STATE_PAIR_SPECS = [
    {"pair_id": "sp01", "base_case": "clean_c2", "difference": "held_vs_released",
     "note": "same utterance; in one context the object is measurably in the gripper",
     "accept": {"held": ["place", "safe_retreat"], "released": ["pick"]},
     "reject": {"held": ["pick_other"], "released": ["place"]}},
    {"pair_id": "sp02", "base_case": "clean_c3", "difference": "held_vs_released",
     "note": "second object of a shared region, so the same choice recurs later",
     "accept": {"held": ["place", "safe_retreat"], "released": ["pick"]},
     "reject": {"held": ["pick_other"], "released": ["place"]}},
    {"pair_id": "sp03", "base_case": "sc_c3", "difference": "satisfied_vs_displaced",
     "note": "a completed goal measured inside the region, and the same object "
             "shoved out of it by the declared environment event (the relation is "
             "re-measured false in the following round of the same episode)",
     "accept": {"satisfied": ["advance_to_next_pending"], "displaced": ["pick"]},
     "reject": {"satisfied": ["re_pick_satisfied"], "displaced": ["finish"]}},
    {"pair_id": "sp04", "base_case": "dev_c2", "difference": "satisfied_vs_displaced",
     "note": "same difference, second object of a shared region, so the same choice "
             "recurs later in a longer episode",
     "accept": {"satisfied": ["advance_to_next_pending"], "displaced": ["pick"]},
     "reject": {"satisfied": ["re_pick_satisfied"], "displaced": ["finish"]}},
    {"pair_id": "sp05", "base_case": "sc_c4", "difference": "candidate_free_vs_occupied",
     "note": "one named candidate passes re-check; the same id is offered after "
             "the occupant moved onto it",
     "accept": {"free": ["place_with_named_candidate", "place"],
                "occupied": ["place", "place_with_other_candidate", "safe_retreat"]},
     "reject": {"occupied": ["place_with_occupied_candidate"]}},
    {"pair_id": "sp06", "base_case": "sc_c4", "difference": "candidate_order_permuted",
     "note": "identical state, candidate list presented in the opposite order: a "
             "model that only picks the first entry is detected here",
     "accept": {"any": ["place_with_any_feasible_candidate", "place"]},
     "reject": {"any": ["place_with_infeasible_candidate"]}},
    {"pair_id": "sp07", "base_case": "sc_c7", "difference": "grasp_point_moved",
     "note": "the pending object was displaced across the table; the same pick is "
             "still legal but its geometry is not what the plan assumed",
     "accept": {"any": ["pick"]},
     "reject": {"moved": ["place"]}},
    {"pair_id": "sp08", "base_case": "pr_target_full", "difference": "region_free_vs_full",
     "note": "the same place with and without the occupant: no candidate clears "
             "the pre-check in the second context",
     "accept": {"full": ["clarify", "blocked", "advance_to_next_pending"],
                "free": ["place", "place_with_named_candidate"]},
     "reject": {"full": ["place_with_named_candidate_rejected_last_round"]}},
    {"pair_id": "sp09", "base_case": "ed_c1", "difference": "unknown_vs_known_hold",
     "note": "held state unresolvable in one context (contact measured on two "
             "bodies): the honest move is to take a fresh measurement",
     "accept": {"unknown": ["observe", "safe_retreat"], "known": ["pick", "place"]},
     "reject": {"unknown": ["place"]}},
    {"pair_id": "sp10", "base_case": "ed_c1", "difference": "failed_vs_completed_feedback",
     "note": "the same descent measured once as a grasp miss and once as a "
             "completed pick, on the same object",
     "accept": {"failed": ["retry_same", "pick_other", "clarify", "blocked"],
                "completed": ["advance_to_next_pending"]},
     "reject": {"completed": ["repeat_just_completed_pick"]}},
    {"pair_id": "sp11", "base_case": "clean_c5", "difference": "pending_order",
     "note": "two pending assignments swapped in the progress list; order must "
             "not be read as priority (SPEC 11.1: multiple legal orders)",
     "accept": {"any": ["pick_any_pending"]},
     "reject": {"any": ["pick_already_satisfied"]}},
    {"pair_id": "sp12", "base_case": "dev_c2", "difference": "all_satisfied_vs_one_pending",
     "note": "everything done, versus one region member left outside",
     "accept": {"all_satisfied": ["finish"], "one_pending": ["pick", "place"]},
     "reject": {"all_satisfied": ["pick"], "one_pending": ["finish"]}},
]


# ------------------------------------------------------------------ sets ------


def _build(configs: list[dict]) -> list[TaskCase]:
    return [build_case(c) for c in configs]


def smoke_set() -> list[TaskCase]:
    """Four readable cases; wiring check only, never a reported result."""
    return [
        build_case(_cfg("smoke_clean", "smoke", 3, 51)),
        build_case(_cfg("smoke_shared", "smoke", 3, 52, shared=True)),
        build_case(_cfg("smoke_state", "smoke", 3, 53, shared=True, mechanism="displace_placed")),
        build_case(_cfg("smoke_clarify", "smoke", 1, 54, expected="needs_clarification",
                        probe="clarifies")),
    ]


# The sets whose cases are pre-registered (SPEC 11.1): `frozen_manifest` hashes
# exactly these, and `all` is their union, so no case can be reachable by one and
# not the other.
_REGISTERED_SETS = ("smoke", "dev", "formal", "protocol")


def _all_cases() -> list[TaskCase]:
    """Every case the task list declares, once each.

    Deduplicated by id because a name that spans the registered sets is asking for
    one sample of each case, not for a case that happens to appear twice."""
    seen: set[str] = set()
    out: list[TaskCase] = []
    for name in _REGISTERED_SETS:
        for case in build_set(name):
            if case.task_id not in seen:
                seen.add(case.task_id)
                out.append(case)
    return out


# One dict, so the names a caller may ask for and the names that resolve are the
# same fact. `clean`/`state_change`/`execution_deviation` are the SPEC 11.1 formal
# subsets, and `all` is every registered set at once.
_SET_BUILDERS = {
    "smoke": smoke_set,
    "dev": lambda: _build(DEV_CONFIGS),
    "clean": lambda: _build([c for c in FORMAL_CONFIGS if c["subset"] == "clean"]),
    "state_change": lambda: _build([c for c in FORMAL_CONFIGS if c["subset"] == "state_change"]),
    "execution_deviation": lambda: _build([c for c in FORMAL_CONFIGS if c["subset"] == "execution_deviation"]),
    "formal": lambda: _build(FORMAL_CONFIGS),
    "protocol": lambda: _build(PROTOCOL_CONFIGS),
    "all": _all_cases,
}

SET_NAMES = tuple(_SET_BUILDERS)


def build_set(name: str) -> list[TaskCase]:
    return _SET_BUILDERS[name]()


ALL_SETS = _REGISTERED_SETS


def find_case(task_id: str, sets: tuple[str, ...] = ALL_SETS) -> TaskCase:
    """One frozen case by id, from whichever set declares it.

    A single definition, so a diagnostic script or a test asks the task list what
    the episode ran instead of restating a scenario that could drift from it."""
    for name in sets:
        for case in build_set(name):
            if case.task_id == task_id:
                return case
    raise KeyError(f"no frozen case {task_id!r} in {list(sets)}")


# ------------------------------------------------------------- freeze check ---


def frozen_manifest() -> dict:
    """The whole task list in one JSON-able structure, hash-addressed.

    The runner writes this into every run directory and `--check` compares it
    against the recorded sha256, so a task edited after a result was produced is
    visible instead of silent (SPEC 7, 11.1)."""
    sets = {k: build_set(k) for k in _REGISTERED_SETS}
    payload = {
        "schema_version": "1",
        "sets": {
            k: [{"task_id": c.task_id, "subset": c.subset, "seed": c.seed,
                 "n_objects": c.n_objects, "utterance": c.utterance,
                 "objects": c.objects, "targets": c.targets,
                 "budgets": c.budgets.model_dump(mode="json"),
                 "verify": c.verify.model_dump(mode="json"),
                 "events": c.event_specs(), "expected": c.expected, "probe": c.probe,
                 "hidden_eval_spec": c.eval_spec.model_dump(mode="json")}
                for c in v]
            for k, v in sets.items()
        },
        "protocol_harness": PROTOCOL_HARNESS,
        "state_pair_specs": STATE_PAIR_SPECS,
        "constants": {"max_objects_per_region": MAX_OBJECTS_PER_REGION,
                      "impulse_pin_to_wall": IMPULSE_PIN_TO_WALL,
                      "impulse_shift_inside": IMPULSE_SHIFT_INSIDE,
                      "impulse_table": IMPULSE_TABLE,
                      "grasp_offset": GRASP_OFFSET,
                      "release_offset_high": RELEASE_OFFSET_HIGH,
                      "release_offset_lateral": RELEASE_OFFSET_LATERAL},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    payload["sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return payload


def dump_frozen(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(frozen_manifest(), f, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def check_frozen(path: str) -> tuple[bool, str]:
    """Compare the live task list against a frozen file; (ok, message).

    Two separate claims, checked separately: the file must agree with *its own*
    recorded hash (a hand-edited scenario would otherwise be believed, because the
    hash is a field of the file it describes), and that hash must agree with the
    task list the code now builds."""
    if not os.path.exists(path):
        return False, f"no frozen manifest at {path}"
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f)
    embedded = str(recorded.pop("sha256", ""))
    blob = json.dumps(recorded, ensure_ascii=False, sort_keys=True, indent=1)
    if hashlib.sha256(blob.encode("utf-8")).hexdigest() != embedded:
        return False, (f"frozen file is self-inconsistent: its contents hash to "
                       f"{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}, not "
                       f"the recorded {embedded[:12]}")
    live = frozen_manifest()
    if embedded == live["sha256"]:
        return True, live["sha256"][:12]
    return False, (f"task list drifted: frozen {embedded[:12]} != live {live['sha256'][:12]}")
