"""Which real episodes leave a memory whose *order* is not the plan's order? Zero spend.

`episodic/policy.py` can do exactly one thing to a trajectory: re-rank the ready rows by the order a
recall's steps acted on their objects. Everything else is `PlanPolicy`'s. So a memory can only show
up in a run when the remembered first-mention order differs from the order the plan would have used
— and `plan_view` publishes `ready` in `row_key` order, which for placement rows is the predicate
string `placed:obj_<color>_<i>:<region>`, i.e. alphabetical by entity id. The first connected probe
(`work/p3c_render_probe.py`) measured that on `dev_c1` the two orders are the same list, so the
memory was retrieved, shown, followed — and changed nothing. A task set built on that shape cannot
ask §13's RQ3 at all.

This sweeps every case this package already freezes (dev, formal, long-horizon), runs one zero-spend
privileged episode of each against its own empty store, and prints the two orders side by side. A
case is a usable *seed* for P3-d only if the row it writes has an order of its own.

Artifacts go to a temp directory; nothing here is read back into a prompt, a Decision or a metric.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embodied_agent.core.v02 import ablation                                  # noqa: E402
from embodied_agent.episodic.store import ExperienceStore                     # noqa: E402
from embodied_agent.evaluation.run import resolve_goal, run_one_episode       # noqa: E402
from embodied_agent.evaluation.tasks import build_set                         # noqa: E402

CASES = [c for name in ("dev", "formal") for c in build_set(name)]
CASES += build_set("long_horizon")

# The policy's own first-mention rule, applied to a stored row: which object did this episode act on
# first, second, ... Same shape `MemoryPolicy._remembered_order` reads out of the page.
def remembered(row):
    order = []
    for step in row.steps:
        if str(step.skill) not in ("pick", "place"):
            continue
        entity = str(dict(step.args or {}).get("object_id") or "")
        if entity and entity not in order:
            order.append(entity)
    return order


def plan_order(case):
    """What `row_key` sorting gives this case: `placed:<entity>:<target>` strings, so entity order."""
    return sorted({e.entity_id for e in case.eval_spec.assignments})


root = tempfile.mkdtemp(prefix="p3d_order_")
print(f"sweeping {len(CASES)} cases into {root}\n")
usable = []
for case in CASES:
    if case.expected != "success":
        print(f"{case.task_id:32s} skipped: expected={case.expected}")
        continue
    ep_root = os.path.join(root, case.task_id)
    os.makedirs(ep_root, exist_ok=True)
    store = ExperienceStore.load(os.path.join(ep_root, "store.jsonl"))
    resolution = resolve_goal(case, 0, None, ep_root)
    try:
        summary = run_one_episode(case, 0, "B", resolution, None, ep_root, case.subset,
                                  frames=False, perceive="privileged",
                                  ablation=ablation("full"), experience_store=store,
                                  policy="memory")
    except Exception as exc:  # noqa: BLE001 - a crash is a result of the sweep, not of the sweep's bug
        print(f"{case.task_id:32s} CRASH {type(exc).__name__}: {exc}")
        continue
    rows = store.all()
    status = (summary.get("result") or {}).get("terminal_status") or summary.get("outcome")
    if not rows:
        print(f"{case.task_id:32s} terminal={status} wrote no row")
        continue
    row = rows[0]
    got, want = remembered(row), plan_order(case)
    same = got == want
    if not same and status == "success":
        usable.append(case.task_id)
    print(f"{case.task_id:32s} terminal={status} rows={len(rows)} "
          f"steps={len(row.steps)} order_{'same' if same else 'DIFFERS'}")
    if not same:
        print(f"    remembered: {got}")
        print(f"    plan      : {want}")

print(f"\nusable seeds (successful, non-plan order): {usable}")
