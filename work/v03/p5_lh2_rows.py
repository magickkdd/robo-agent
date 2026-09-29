"""§11's Long-horizon group, six rows, and the arm contrast, printed the way the report needs.

Two things are being read here and they are different questions:

  * the **rows** — each metric's own value per arm, with its numerator and denominator, which is
    what SPEC §11 asks for;
  * the **contrast** — which metric's value actually *changed* between an arm and the `full`
    reference, which is what §9's ablation is for.

v0.2's product is the same shape, so the side-by-side SPEC §7.3 demands is a join on the metric
id rather than a re-derivation: same instruments, same frozen config, two decision sources. The
rule rows and the model rows are printed **beside** each other and never merged — §7.3 forbids
the average, and the whole point of E1 is that they differ.
"""
import json

V03 = "/home/czx/embodied-agent-batches/v03/e1/lh_score/long_horizon_v0.json"
V02 = "/tmp/p5c/lh_score/long_horizon_v0.json"

new = json.load(open(V03, encoding="utf-8"))
old = json.load(open(V02, encoding="utf-8")) if __import__("os").path.exists(V02) else None

rows = new["spec_rows"]
cells = new["contrast"]["cells"]
diffs = new["contrast"]["differences"]

print(f"runs={new['n_runs']} episodes={new['n_episodes']} metric_version={new['metric_version']}")
print(f"metric definitions: {len(new['definitions'])}   asymmetric_fields: {new['asymmetric_fields']}")
print()
print("=== §11 Long-horizon rows, per arm (value n/d) ===")
for spec_row, metric_ids in rows.items():
    print(f"\n-- {spec_row}")
    for mid in metric_ids:
        cells_with = {k.split("::")[-1]: v["metrics"].get(mid, {})
                      for k, v in cells.items()}
        bits = []
        for arm, m in cells_with.items():
            if m.get("not_measured_reason"):
                bits.append(f"{arm}=n/m({m['not_measured_reason'][:40]})")
            elif "value" in m:
                bits.append(f"{arm}={m['value']} ({m.get('numerator')}/{m.get('denominator')})")
            else:
                bits.append(f"{arm}=absent")
        print(f"   {mid:38s} " + " | ".join(bits))

print()
n_metric_ids = len({mid for v in cells.values() for mid in v["metrics"]})
n_arms = len(cells) - 1
print(f"=== arm differences vs `full`: {len(diffs)} rows over {n_arms} arms "
      f"x {n_metric_ids} metrics = {n_arms * n_metric_ids} possible ===")


def _fmt(v, w=9):
    return f"{'n/m' if v is None else v}".rjust(w)


changed = []
for d in diffs:
    delta = d.get("delta")
    is_change = (d.get("reference") != d.get("value")) and d.get("comparable")
    if is_change:
        changed.append(d)
    print(f"  {str(d['arm']):22s} {str(d['metric']):34s} ref={_fmt(d.get('reference'), 7)} -> "
          f"{_fmt(d.get('value'), 7)} delta={_fmt(delta, 8)} comparable={d.get('comparable')} "
          f"n/m_on={d.get('not_measured_on')}")
print()
print(f"--- rows whose value actually moved, comparable on both sides: {len(changed)} ---")
for d in changed:
    print(f"  {str(d['arm']):22s} {str(d['metric']):34s} {d.get('reference')} -> "
          f"{d.get('value')} (delta {d.get('delta'):+})")

if old:
    print()
    print("=== v0.2 (rule decision source), same instruments, for the side-by-side ===")
    ocells = old["contrast"]["cells"]
    odiffs = old["contrast"].get("differences") or []
    print(f"v0.2 runs={old['n_runs']} episodes={old['n_episodes']}")
    print(f"v0.2 arm differences: {len(odiffs)}")
    for d in odiffs:
        print(f"  {d['arm']:22s} {d['metric']:34s} ref={d['reference']} -> {d['value']} "
              f"(delta {d['delta']:+})")
    print()
    print("=== the same metric under the two decision sources, `full` reference ===")
    orows = {}
    for k, v in ocells.items():
        arm = k.split("::")[-1]
        for mid in ("L1.assignment_completion", "L5.repeated_failure_share",
                    "L6.recovery_effectiveness", "L6.replanning_effectiveness",
                    "L6.recorded_vs_inferred", "L3.regression_rate"):
            m = v["metrics"].get(mid, {})
            orows.setdefault(arm, {})[mid] = (m.get("value"), m.get("numerator"),
                                              m.get("denominator"), m.get("not_measured_reason"))
    nrows = {}
    for k, v in cells.items():
        arm = k.split("::")[-1]
        for mid in ("L1.assignment_completion", "L5.repeated_failure_share",
                    "L6.recovery_effectiveness", "L6.replanning_effectiveness",
                    "L6.recorded_vs_inferred", "L3.regression_rate"):
            m = v["metrics"].get(mid, {})
            nrows.setdefault(arm, {})[mid] = (m.get("value"), m.get("numerator"),
                                              m.get("denominator"), m.get("not_measured_reason"))
    for arm in sorted(set(orows) | set(nrows)):
        print(f"\n  {arm}")
        for mid in ("L1.assignment_completion", "L3.regression_rate", "L5.repeated_failure_share",
                    "L6.recovery_effectiveness", "L6.replanning_effectiveness",
                    "L6.recorded_vs_inferred"):
            o = orows.get(arm, {}).get(mid)
            n = nrows.get(arm, {}).get(mid)
            fmt = lambda t: ("n/m" if t is None else
                             (f"{t[3][:36]}" if t[3] else f"{t[0]} ({t[1]}/{t[2]})"))
            print(f"    {mid:32s} rule={fmt(o):28s} model={fmt(n)}")
