"""P1-d visual-verification check: what layer 2 can certify from pixels, measured.

Zero model cost. `StubReader` + `PerceptionAssembler` + `perception/world_state.py` produce
a percept-derived `WorldState`; `perception/verify_percept.PerceptVerifier` answers the
placement postcondition from it; the *privileged* channel appears only in two roles — as
the answer key a verdict is graded against, and as the hand that seats a body when the
measurement needs a placement to have really happened. Every number below is read out of
the report the pixels produced.

Four questions, in the order the phase log answers them:

1. **Certified placements.** Seat each body in `tray_middle` and ask both channels the same
   postcondition, per view. The five outcome classes are not symmetric: `confirmed` and
   `honest_decline` are both safe, `contrary_false` and `dangerous_true` each wreck a task
   in the opposite direction. A per-view split is the point — P1-c measured that a view can
   differ from another by 71 mm, so "how much can this channel certify" is a question with a
   different answer per camera.
2. **The negative, sound?** On an untouched table every body rests on the table, so every
   tray postcondition is genuinely false. Does the sensor channel ever say `false` where the
   answer key says the body is inside? That is the one claim a `false` must never make.
3. **What is named as unmeasured.** The evidence keys each channel actually produced, per
   report, and how often each declined fact is named. This is the anti-circularity audit:
   the seating-height keys appear in exactly one column.
4. **The uncertain budget.** Run the seam (`outcome_status`) over every completed actuation
   these rows stand for, and count what the loop would be told. `uncertain` is a cost the
   agent pays in extra looks; the privileged channel's count is the baseline it must be
   compared with, and it is 0 by construction — which is why the number is reported per
   channel and never mixed.

Run:  PYTHONPATH=. python work/p1_verify_check.py [case_id ...]
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pybullet as p                                                  # noqa: E402

from embodied_agent.core.contracts import (                             # noqa: E402
    SkillResult,
    SkillStatus,
)
from embodied_agent.core.scene import PhysicsScene                        # noqa: E402
from embodied_agent.core.verify import (                                # noqa: E402
    RuntimeVerifier,
    build_world_state,
    state_inside,
)
from embodied_agent.evaluation.calibration import cell_catalog, task_context_of   # noqa: E402
from embodied_agent.evaluation.tasks import find_case                     # noqa: E402
from embodied_agent.perception.frames import camera_for, capture          # noqa: E402
from embodied_agent.perception.observe import (                           # noqa: E402
    PerceptionAssembler,
    StubReader,
    TaskContext,
)
from embodied_agent.perception.verify_percept import (                    # noqa: E402
    DECLINED_FACTS,
    PerceptVerifier,
)
from embodied_agent.perception.world_state import world_state_from_percept  # noqa: E402

OUT = "/tmp/v02_p1d_verify"
VIEWS = ("main", "front_high", "overhead")
TARGET = "tray_middle"
# the keys only the seating-height comparison produces. If any of them appears in a sensor
# report, the circular check came back — see the module docstring of verify_percept.py.
CIRCULAR_KEYS = ("height_err_m", "rest_z", "rest_z_expected", "rest_z_measured",
                 "rest_z_nominal")


def look(scene, catalog, reader, assembler, name: str, view: str, task):
    """A frame, a reading of it, and the percept it assembles into. Pixels only."""
    frame, _, _ = capture(scene, OUT, camera_for(view), name, sim_time=scene.sim_time)
    reading = reader.read(frame, catalog=catalog, task=task)
    return assembler.assemble(frame, reading)


def initial_poses(scene) -> dict:
    """Where every body started, so a later row can be measured on a clean table."""
    return {eid: scene.object_pose(eid) for eid in scene.objects}


def restore(scene, start: dict) -> None:
    for eid, (pos, orn) in start.items():
        p.resetBasePositionAndOrientation(scene.objects[eid]["body"], list(pos), list(orn),
                                         physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 0.4


def seat_at(scene, entity_id: str, target_id: str, dx: float, dy: float) -> str:
    """Put a body in a tray with the simulator's own hand; return its colour."""
    d = scene.objects[entity_id]
    tray = scene.trays[target_id]
    z = float(tray["floor_top"]) + float(d["geometry"].half_h_vertical)
    p.resetBasePositionAndOrientation(
        d["body"], [float(tray["center"][0]) + dx, float(tray["center"][1]) + dy, z],
        p.getQuaternionFromEuler([0, 0, 0.05], physicsClientId=scene.cid),
        physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 40 * 0.01
    return str(d["attributes"]["color"])


def upright_half_xy(scene, entity_id: str) -> tuple:
    geom = scene.objects[entity_id]["geometry"]
    return ((geom.half_extents.x, geom.half_extents.y) if geom.half_extents is not None
            else (geom.radius, geom.radius))


def wall_dx(scene, entity_id: str) -> float:
    """A centre offset that leaves the footprint crossing the inner wall by 4 mm.

    The declared placement margin is 5 mm and occupancy is tested with it *relaxed*, so
    this one physical configuration answers `occupies the tray` yes and `placed inside` no
    at the same time — which is exactly the pair the first draft of `verify_percept`
    confused, and the reason the occupancy row is not the bar."""
    return float(scene.trays[TARGET]["inner_half"]) - max(upright_half_xy(scene, entity_id)) + 0.004


def classify(percept_value: str, truth_value: str, seen: bool) -> str:
    """The five things a sensor verdict can be against the answer key."""
    if not seen:
        return "no_answer"
    if percept_value == "true" and truth_value == "true":
        return "confirmed"
    if percept_value == "true":
        return "dangerous_true"
    if percept_value == "false" and truth_value == "true":
        return "contrary_false"
    if percept_value == "false":
        return "correct_negative"
    return "honest_decline" if truth_value == "true" else "declined_negative"


def completed() -> SkillResult:
    """Layer 1 said the actuation finished; only layer 2's evidence varies here."""
    return SkillResult(call_id="probe", status=SkillStatus.completed, t_start=0.0, t_end=1.0,
                       stages_executed=["probe"])


def verify_view(scene, catalog, reader, assembler, task, view: str, colour: str,
                entity_id: str, index: int) -> dict:
    """Both channels' answer to one postcondition about one seated body.

    The answer key is the *same conjunction the sensor claims* — support names the target,
    and the footprint clears the wall by the declared margin — graded on the privileged
    position and the body's real orientation. Not the privileged verifier's full verdict,
    which also asks for `at_rest` and `not_held`: those are extra promises the privileged
    channel makes, and a body still settling would otherwise turn a correct sensor `true`
    into a recorded over-claim.
    """
    obs = look(scene, catalog, reader, assembler, f"v{index}_{view}", view, task)
    state = world_state_from_percept(obs, catalog, state_version=index + 1)
    eid_seen = f"seen:{colour}"
    ent = state.entity(eid_seen) if state.has_entity(eid_seen) else None
    verifier = PerceptVerifier(state, view=view,
                               alt_views=tuple(v for v in VIEWS if v != view))
    privileged = build_world_state(scene, 100 + index, f"obs_seat_{index}")
    pri_report = RuntimeVerifier(privileged).verify_placement(entity_id, TARGET)
    pri = privileged.entity(entity_id)
    region = privileged.target(TARGET)
    truth_inside = state_inside(pri, region, verifier.config.footprint_margin_m)[0]
    truth_support = TARGET in str(pri.supported_by).split("+")
    # asked whether or not the frame reported the body: an absent entity is exactly the
    # case where this channel's answer matters most
    report = verifier.verify_placement(eid_seen, TARGET)
    value = report.value.value
    keys = sorted(report.evidence)
    truth = "true" if (truth_inside and truth_support) else "false"
    return {
        "view": view,
        "seen": ent is not None,
        "truth_support": str(pri.supported_by),
        "truth_inside": truth_inside,
        "truth_placed": truth,
        "percept_verdict": value,
        "privileged_verdict": pri_report.value.value,
        "classification": classify(value, truth, ent is not None),
        "wall_margin_m": report.evidence.get("wall_margin_m"),
        "occupies_target": report.evidence.get("occupies_target"),
        "percept_xy_err_mm": [round(float(report.evidence[k]) * 1000, 1)
                              for k in ("xy_err_x", "xy_err_y") if k in report.evidence],
        "true_xy_err_mm": (None if ent is None or ent.pose is None else round(
            math.hypot(ent.pose.position.x - pri.pose.position.x,
                       ent.pose.position.y - pri.pose.position.y) * 1000, 1)),
        "percept_unmeasured": list(report.unmeasured),
        "percept_evidence_keys": keys,
        "privileged_evidence_keys": sorted(pri_report.evidence),
        "circular_keys_in_percept_report": [k for k in CIRCULAR_KEYS if k in keys],
        "would_resolve": verifier.would_resolve(next(
            (u for u in report.unmeasured if u not in DECLINED_FACTS), "not_in_this_frame")),
        "outcome_status": (verifier.outcome_status(completed(), [report]) or SkillStatus.completed).value,
        "privileged_outcome_status": (
            RuntimeVerifier(privileged).outcome_status(completed(), [pri_report])
            or SkillStatus.completed).value,
    }


def tally(rows, key) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        out[row[key]] = out.get(row[key], 0) + 1
    return dict(sorted(out.items()))


def summarise(seated) -> dict:
    """One seating geometry, per view: verdicts, what the loop would be told, and how
    close the thinnest `true` was to being wrong."""
    return {
        "per_view": {view: {
            "classifications": tally([s["views"][view] for s in seated], "classification"),
            "outcome_status": tally([s["views"][view] for s in seated], "outcome_status"),
            "unmeasured_named": {fact: sum(1 for s in seated
                                           if fact in s["views"][view]["percept_unmeasured"])
                                 for fact in DECLINED_FACTS},
            "circular_keys_found": sorted({k for s in seated
                                           for k in s["views"][view]["circular_keys_in_percept_report"]}),
            # a `true` won by a fraction of a millimetre is a different risk from one won by
            # 40 mm, and the pair of numbers has to be visible together
            "true_claims": [{"colour": s["colour"],
                             **{k: s["views"][view][k] for k in
                                ("wall_margin_m", "true_xy_err_mm", "occupies_target")}}
                            for s in seated if s["views"][view]["percept_verdict"] == "true"],
            "thinnest_true_margin_mm": _thinnest(seated, view),
            # the witness for the third refusal: a body the answer key says is *not* placed
            # while the occupancy row says it blocks the tray
            "occupies_but_not_placed": sum(1 for s in seated
                                           if s["views"][view]["occupies_target"] == 1.0
                                           and s["views"][view]["truth_placed"] == "false"),
            "xy_err_mm": sorted(s["views"][view]["true_xy_err_mm"] for s in seated
                                if s["views"][view]["true_xy_err_mm"] is not None),
        } for view in VIEWS},
        "unsafe": sum(1 for s in seated for v in s["views"].values()
                      if v["classification"] in ("dangerous_true", "contrary_false")),
        "uncertain_by_channel": {
            "percept": sum(1 for s in seated for v in s["views"].values()
                           if v["outcome_status"] == "uncertain"),
            "privileged": sum(1 for s in seated for v in s["views"].values()
                              if v["privileged_outcome_status"] == "uncertain")},
        "of_which": len(seated) * len(VIEWS),
    }


def _thinnest(seated, view):
    """Millimetres of wall margin on the view's tightest `true` claim, or None."""
    margins = [s["views"][view]["wall_margin_m"] for s in seated
               if s["views"][view]["percept_verdict"] == "true"]
    return None if not margins else round(min(margins) * 1000, 1)


def one_case(case_id: str) -> dict:
    case = find_case(case_id)
    scene = PhysicsScene(seed=case.seed, object_layout=case.objects)
    catalog = cell_catalog(scene.trays.values())
    assembler = PerceptionAssembler(catalog)
    reader, task = StubReader(), TaskContext(**task_context_of(case))
    ids = sorted(scene.objects)

    # ---- 2. the negative, on an untouched table -------------------------------------
    negatives = []
    for view in VIEWS:
        obs = look(scene, catalog, reader, assembler, f"fresh_{view}", view, task)
        state = world_state_from_percept(obs, catalog, state_version=0)
        verifier = PerceptVerifier(state, view=view,
                                   alt_views=tuple(v for v in VIEWS if v != view))
        privileged = build_world_state(scene, 1, "obs_fresh")
        region = privileged.target(TARGET)
        for eid in ids:
            colour = str(scene.objects[eid]["attributes"]["color"])
            seen = state.has_entity(f"seen:{colour}")
            report = verifier.verify_placement(f"seen:{colour}", TARGET)
            pri = privileged.entity(eid)
            truth = ("true" if (TARGET in str(pri.supported_by).split("+")
                                and state_inside(pri, region,
                                                 verifier.config.footprint_margin_m)[0])
                     else "false")
            negatives.append({
                "view": view, "colour": colour, "seen": seen,
                "percept_verdict": report.value.value, "truth_placed": truth,
                "truth_support": str(pri.supported_by),
                "wall_margin_m": report.evidence.get("wall_margin_m"),
                "classification": classify(report.value.value, truth, seen)})

    # ---- 1/3/4. the same postcondition after a real placement -------------------------
    # Three seating geometries, because they answer different questions. `alone` is one
    # body in an otherwise empty tray: the clean case a placement skill actually produces.
    # `crowded` packs every body into the same tray at P1-c's 50 mm one-sided grid, which
    # pushes the last one against the wall — the configuration that turns a position error
    # into a verdict, and the one P1-c's parity sweep measured row by row and never reached
    # as a whole. `wall` parks a single body with its footprint across the inner wall, so
    # the answer key says `not placed` while the *occupancy* row still says it blocks the
    # tray: the one case that separates those two predicates.
    start = initial_poses(scene)
    modes = {}
    for mode in ("alone", "crowded", "wall"):
        restore(scene, start)
        if mode == "crowded":
            for i, eid in enumerate(ids):
                seat_at(scene, eid, TARGET, -0.075 + 0.05 * i, 0.0)
        seated = []
        for i, eid in enumerate(ids):
            if mode == "crowded":
                colour = str(scene.objects[eid]["attributes"]["color"])
            else:
                restore(scene, start)
                colour = seat_at(scene, eid, TARGET,
                                 0.0 if mode == "alone" else wall_dx(scene, eid), 0.0)
            rest_xy, _orn = scene.object_pose(eid)
            seated.append({"entity_id": eid, "colour": colour, "index": i,
                           "rest_xy": [round(rest_xy[0], 4), round(rest_xy[1], 4)],
                           "views": {v["view"]: v for v in (
                               verify_view(scene, catalog, reader, assembler, task, view,
                                           colour, eid, i) for view in VIEWS)}})
        modes[mode] = {"seated_rows": seated, "summary": summarise(seated)}
    scene.close()

    return {
        "case_id": case_id, "seed": case.seed, "utterance": case.utterance,
        "bodies": len(ids),
        "seated_target": TARGET,
        "negative_sweep": {"classifications": tally(negatives, "classification"),
                           "per_view": {v: tally([r for r in negatives if r["view"] == v],
                                                 "classification") for v in VIEWS},
                           "unsafe": sum(1 for r in negatives if r["classification"]
                                         in ("dangerous_true", "contrary_false")),
                           "rows": negatives},
        "modes": modes,
    }


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    ids = [a for a in sys.argv[1:] if not a.startswith("--")] or ["dev_c5"]
    report = {"cases": [one_case(c) for c in ids]}
    with open(os.path.join(OUT, "verify_check.json"), "w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    for case in report["cases"]:
        print(f"== {case['case_id']} seed={case['seed']} bodies={case['bodies']} "
              f"target={case['seated_target']!r}  utterance={case['utterance']}")
        for mode, block in case["modes"].items():
            print(f"   seated {mode} (classification / what the loop would be told)")
            for view, rows in block["summary"]["per_view"].items():
                print(f"     {view:10s} {rows['classifications']} status={rows['outcome_status']}"
                      f" thinnest_true={rows['thinnest_true_margin_mm']}mm "
                      f"occupies_not_placed={rows['occupies_but_not_placed']} "
                      f"circular={rows['circular_keys_found']}")
                print(f"     {'':10s} xy_err_mm={rows['xy_err_mm']}")
                for claim in rows["true_claims"]:
                    print(f"        true: {claim['colour']:7s} margin={claim['wall_margin_m']}m "
                          f"xy_err={claim['true_xy_err_mm']}mm occupies={claim['occupies_target']}")
            print(f"     unsafe={block['summary']['unsafe']} "
                  f"uncertain={block['summary']['uncertain_by_channel']} of "
                  f"{block['summary']['of_which']} placement verifications")
        print(f"   untouched table (every tray postcondition is really false): "
              f"{case['negative_sweep']['classifications']}  unsafe="
              f"{case['negative_sweep']['unsafe']}")
        for view, rows in case["negative_sweep"]["per_view"].items():
            print(f"     {view:10s} {rows}")
    print(f"json -> {OUT}/verify_check.json")


if __name__ == "__main__":
    main()
