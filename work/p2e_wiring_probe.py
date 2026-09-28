"""P2-e dev probe: does the production entry reach 9's planning arms at zero spend?

Read-only against the repository; every artifact it writes goes under /tmp. It runs one small
case through `evaluation/run.py:run_group` four times — once per arm in `PLANNING_ARMS`, once with
the v0.1 control and once with the payload-reading control — and prints what a reader needs to
trust the batch before any metric is computed from it:

* which runtime class actually ran, and whether an `ablation` record was filed at all (a batch
  whose rows carry no claim is a batch whose arm nobody declared);
* `http_requests` on every row, because the standing rule for P2 is that none of this spends
  anything;
* which payload sections reached the page, arm by arm — the difference this whole wiring exists to
  make measurable, since the v0.1 control never reads them;
* the manifest's own account of the batch (`perception.arm`, `decision_source.policy`), which is
  where a reader who did not type the command looks first.

Nothing here is a measurement for the report. It is the smoke check that decides what the contract
tests have to pin.
"""
import json
import os
import sys

from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.v02 import SCHEMA_VERSION, ablation
from embodied_agent.evaluation.run import run_group
from embodied_agent.planning.arm import PLANNING_ARMS, PlannedRuntime

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/p2e_wiring"
CASE = "lh_c1_shared_pair_restore"


def rows_of(root: str) -> list[dict]:
    out = []
    for episode in sorted(os.listdir(os.path.join(root, "episodes"))):
        with open(os.path.join(root, "episodes", episode, "episode_summary.json"),
                  encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def show(label: str, group: dict) -> None:
    root = group["root"]
    with open(os.path.join(root, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    for row in rows_of(root):
        ep_dir = row["artifacts"]["episode_dir"]
        events = EpisodeStore(ep_dir, row["episode_id"]).read_all(SCHEMA_VERSION)
        types = sorted({e["type"] for e in events})
        claims = [e["payload"] for e in events if e["type"] == "ablation"]
        print(json.dumps({
            "label": label, "run_id": os.path.basename(root),
            "manifest_arm": manifest["perception"]["arm"],
            "manifest_modules_off": manifest["perception"].get("modules_off"),
            "manifest_policy": manifest["decision_source"]["policy"],
            "row_arm": row["perception"]["arm"],
            "decision_source": row.get("decision_source"),
            "status": row["result"]["terminal_status"],
            "rounds": row["result"]["decision_rounds"],
            "http": row["result"]["http_requests"],
            "completed": f'{row["result"]["objects_completed"]}/'
                         f'{row["result"]["objects_total"]}',
            "ablation_records": [c.get("condition") for c in claims],
            "ablation_note": (claims[0].get("notes", "")[:90] if claims else None),
            "v02_types": [t for t in types if t not in
                          ("observation", "decision", "execution_feedback", "environment_event",
                           "episode_end", "budget_exhausted", "model_call", "terminal_snapshot")],
        }, ensure_ascii=False, default=str), flush=True)


print("is a PlannedRuntime a Runtime?", issubclass(PlannedRuntime, Runtime), flush=True)
for policy in (None, "payload"):
    for condition in PLANNING_ARMS:
        try:
            group = run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                              out_root=os.path.join(ROOT, "runs"), case_ids=[CASE], frames=False,
                              perceive="privileged", ablation=ablation(condition), policy=policy)
        except Exception as e:  # noqa: BLE001 - the probe reports, it does not fix
            print(json.dumps({"label": f"{condition}/{policy or 'rule'}",
                              "error": f"{type(e).__name__}: {e}"}), flush=True)
            continue
        show(f"{condition}/{policy or 'rule'}", group)

# and the refusal side: an arm this channel cannot mean, and a control this batch cannot carry
for kw, why in (({"ablation": ablation("wo_episodic_memory")}, "arm the channel cannot carry"),
                ({"policy": "payload", "modes": ("A", "B")}, "control outside mode B"),
                ({"policy": "nonsense"}, "undeclared control")):
    args = dict(modes=("B",), planner_kind="rule", out_root=os.path.join(ROOT, "refused"),
                case_ids=[CASE], frames=False, perceive="privileged")
    args.update(kw)
    try:
        run_group("long_horizon", **args)
        print(f"NOT REFUSED: {why} ({kw})", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"refused {why}: {type(e).__name__}: {str(e)[:150]}", flush=True)
