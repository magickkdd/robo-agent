"""Do the ablated arms' plan views keep one spelling of `row_key` across versions?

`dropped` is computed as a set difference on `row_key`. If one arm publishes the same predicate as
`placed:X:Y` in one view and `row::placed::X::Y` in another, that difference is a naming artefact
and the "left the plan while not true" finding it produces is a false statement about the log.
"""
import glob
import json
import sys

for pat in sys.argv[1:]:
    for d in glob.glob(f"/tmp/p2e_wiring/runs/{pat}/episodes/*/"):
        rows = [json.loads(l) for l in open(d + "events.jsonl") if l.strip()]
        print(f"== {pat.split('_2026')[0]} / {d.split('/')[-2]}")
        for r in rows:
            if r.get("type") not in ("plan", "plan_revision"):
                continue
            v = r["payload"].get("view") or {}
            print(f"   {r['type']} seq={r['sequence']} v={v.get('version')} "
                  f"authored_by={v.get('authored_by')!r} changed={v.get('changed')}")
            for row in v.get("rows") or []:
                print("     row_key={!r:42s} kind={!r:9s} predicate={!r:44s} "
                      "satisfied={!r} status={!r}".format(
                          row.get("row_key"), row.get("kind"), row.get("predicate_id"),
                          row.get("satisfied"), row.get("status")))
