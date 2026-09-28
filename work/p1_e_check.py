"""P1-e dev probe: the sensing arm through the production entry point, and what it costs.

Read-only against the repository; writes only under /tmp. Nothing here feeds a prompt, a
Feedback or a DecisionContext.

The contract tests pin what the arm *may* claim. This measures what it *does*, on the path a
reported number will come from: `resolve_goal` (one shared parse) then `run_one_episode`, for
the privileged control and the limited-semantic stub arm on the same case, the same seed and
the same rule policy. Three numbers matter here and none of them is a success rate:

* **looks per round** — the arm's camera is asked once per decision *and* once per internal
  snapshot the executor takes inside a skill, so a round costs several frames. With a stub
  read that is free; with a vision model it is the §11 cost line, and it is the residual P1-f
  has to report rather than optimize away here;
* **the HTTP ledger with a costed look** — the same episode with a reader that says what each
  request cost, to see how far one round can run past `max_http_requests` when the ceiling is
  checked between rounds;
* **the identical-attempt guard** — `identical_repeats_total` on a channel whose snapshots now
  come from a camera. Measured, not assumed: `_fingerprint` ignores `observation_ref` and
  `state_version`, so a second look at an untouched table is *not* new evidence and the guard
  still fires — but a second *camera* is, because the same body reads millimetres apart across
  views. That asymmetry is what a viewpoint change can buy the model, and what P1-f has to
  report alongside the per-view cost.
"""
import json
import os
import sys
import tempfile
import time

from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.v02 import ablation as ablation_record
from embodied_agent.evaluation.calibration import cell_catalog
from embodied_agent.evaluation.run import build_scene, resolve_goal, run_one_episode
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.perception.arm import PerceptionUnavailable, build_arm
from embodied_agent.perception.grounding import PERCEIVE_CHANNELS, GroundingMap
from embodied_agent.perception.observe import StubReader
from embodied_agent.perception.frames import VIEWS

ROOT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="p1_e_check_")
CASE = "dev_c5"


def one(label, case, repeat, mode, perceive, *, ablation=None, views=None):
    """One episode via `run_one_episode`, in its own root.

    `run_one_episode` names its directory by (case, mode, repeat) — one logical execution, one
    artifact tree — so two rows of this probe over the same case would otherwise append to the
    same `events.jsonl` and count each other's records. A real batch runs each row once.

    The costed variant is produced by replacing `arm.StubReader` for the duration of the call
    rather than passing the arm a reader, because the only way to bill a look in this checkout
    is to *be* the thing that reads the frame — the vlm channel refuses to run at all until a
    vision model exists, which is the point of `channel_readiness`."""
    root = os.path.join(ROOT, label)
    os.makedirs(root, exist_ok=True)
    resolution = resolve_goal(case, repeat, None, root)
    started = time.time()
    summary = run_one_episode(case, repeat, mode, resolution, None, root, "dev",
                              frames=False, perceive=perceive, ablation=ablation,
                              views=views)
    row = json.loads(json.dumps(summary, default=str))
    result = row.get("result") or {}
    perception = row.get("perception") or {}
    return {
        "label": label,
        "perceive": perceive, "arm": perception.get("arm"),
        "status": result.get("terminal_status"), "failure": result.get("failure_type"),
        "note": (result.get("artifacts") or {}).get("note"),
        "rounds": result.get("decision_rounds"),
        "skill_calls": result.get("skill_calls"),
        "looks": perception.get("looks"),
        "looks_per_round": (round(perception.get("looks", 0)
                                  / max(1, result.get("decision_rounds") or 1), 2)),
        "http": result.get("http_requests"),
        "observations": (result.get("artifacts") or {}).get("observations"),
        "completed": f"{result.get('objects_completed')}/{result.get('objects_total')}",
        "unknown_goals": (result.get("artifacts") or {}).get("unknown_goals"),
        "false_finish_attempts": result.get("false_finish_attempts"),
        "identical_invalid": result.get("identical_invalid_attempts"),
        "identical_repeats": result.get("identical_repeats_total"),
        "perception_events": perception.get("perception_events"),
        "ablation_events": perception.get("ablation_events"),
        "grounding_map_sha256": (perception.get("grounding_map_sha256") or "")[:12],
        "wall_s": round(time.time() - started, 2),
        "episode_dir": row.get("episode_dir") or row.get("artifacts", {}).get("episode_dir"),
    }


def fingerprints(case, scene, gmap, root):
    """What §6.2's identical-attempt guard actually sees on this channel.

    `_fingerprint` is the loop's whole notion of "relevant new evidence". Measured here: two
    looks from one camera at an untouched table, and the same table from two cameras — the
    number that decides whether a change of viewpoint can buy a repeated failing action."""
    ex = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
    arm = build_arm(case=case, scene=scene, executor=ex, store=EpisodeStore(root, "fp"),
                    run_dir=root, episode_id="fp", perceive="stub",
                    catalog=cell_catalog(scene.trays.values()), gmap=gmap,
                    budgets=case.budgets)
    ex.world_provider = arm.observe
    shots = {v: arm.perceiver.look(v, held=None) for v in ("main", "overhead", "front_high")}
    fps = {v: arm._fingerprint(s) for v, s in shots.items()}
    out = {"same_view_twice_equal": None, "cross_view_equal": {}, "max_dxy_mm": {}}
    again = arm.perceiver.look("main", held=None)
    out["same_view_twice_equal"] = fps["main"] == arm._fingerprint(again)
    out["observation_ref_changed"] = str(shots["main"].observation_ref) != str(
        again.observation_ref)
    for a in ("main", "overhead", "front_high"):
        for b in ("main", "overhead", "front_high"):
            if a >= b:
                continue
            da = max(1000.0 * max(abs(p[1] - q[1]), abs(p[2] - q[2]))
                     for p, q in zip(fps[a][1], fps[b][1]))
            out["cross_view_equal"][f"{a}=={b}"] = fps[a] == fps[b]
            out["max_dxy_mm"][f"{a}_vs_{b}"] = round(da, 1)
    return out


if __name__ == "__main__":
    print("root:", ROOT, "case:", CASE)
    case = find_case(CASE)
    rows = []
    for perceive in ("privileged", "stub"):
        try:
            rows.append(one(perceive, case, 1, "B", perceive))
        except PerceptionUnavailable as e:
            rows.append({"perceive": perceive, "unavailable": str(e), "reasons": e.reasons})
    # the same arm, with a reader that bills every look, to see the ceiling do its work
    import embodied_agent.perception.arm as arm_mod  # noqa: E402

    class _Costed(StubReader):
        """Stub geometry, VLM cost: `meta["http_requests_this_call"]` is what the assembler
        publishes and what `_requests_used` bills, so this is the real billing path."""

        def read(self, frame, **kw):
            reading = super().read(frame, **kw)
            return reading.model_copy(update={
                "meta": {**(reading.meta or {}), "http_requests_this_call": 1},
                "tokens": {"prompt_tokens": 1200, "completion_tokens": 90}})

    original = arm_mod.StubReader
    arm_mod.StubReader = _Costed
    try:
        rows.append(one("costed_stub", case, 1, "B", "stub"))
    finally:
        arm_mod.StubReader = original
    # the same arm with two of the three declared cameras, unpriced: what a viewpoint
    # restriction costs in rounds, with no money in the looks at all
    rows.append(one("two_views", case, 1, "B", "stub", ablation=ablation_record("wo_vlm"),
                    views=("main", "overhead")))
    print(json.dumps({"declared_views": sorted(VIEWS), "channels": list(PERCEIVE_CHANNELS),
                      "budgets": case.budgets.model_dump(mode="json")}, indent=2))
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, default=str))
    # what the identical-attempt guard sees, measured on a table nothing has moved: the
    # citation in `test_the_fingerprint_ignores_a_second_look_and_notices_a_second_camera`
    scene = build_scene(case)
    try:
        print(json.dumps({"fingerprint": fingerprints(
            case, scene, GroundingMap.from_objects(case.objects),
            os.path.join(ROOT, "fingerprint"))}, indent=2, default=str))
    finally:
        scene.close()
