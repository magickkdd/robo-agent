import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/home/czx/embodied-agent-batches/v03/e5/p0_full"
print("files:", sorted(os.listdir(root)))
p = os.path.join(root, "progress.json")
if not os.path.exists(p):
    print("no progress.json -> the batch path was never taken; these came from --single runs")
    raise SystemExit(0)
prog = json.load(open(p, encoding="utf-8"))
print("segment:", prog.get("segment"), "| token_cap:", prog.get("token_cap"),
      "| tokens_used:", prog.get("tokens_used"))
print("n slots:", len(prog.get("slots") or []))
print("halted:", json.dumps(prog.get("halted"), ensure_ascii=False)[:300])
for r in prog.get("slots") or []:
    ep = r.get("episodic") or {}
    print(f"  {r.get('slot_index'):3d} {str(r.get('task_type')):30s} "
          f"{str(r.get('outcome')):24s} term={str(r.get('termination_reason')):20s} "
          f"arm={r.get('arm')} before={ep.get('store_size_before')} "
          f"retrieved={ep.get('rows_retrieved')} used={ep.get('rows_used')}")
