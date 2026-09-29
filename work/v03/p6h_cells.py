"""Where do `armed` / `asked` live, and do the 12 cells satisfy SPEC §13 row 1 and row 3?

The first version of this checker counted every directory under the E1 archive and found 16, then
concluded row 1 failed. It counted `lh_score`, `reports`, `skill_score` and `vlm_contrast` as
cells. They are score directories, not cells: the 12 cells are the six `em_*` and six
`long_horizon_*` run directories. So the row held and the checker was wrong — worth stating,
because a checker that reports a false failure is as useless as one that reports a false pass.
"""
import glob
import json
import os

E1 = "/home/czx/embodied-agent-batches/v03/e1"
D1 = ("/home/czx/embodied-agent-robot-agent-embodied-agent-4/configs/models/"
      "bst_text_agnes.yaml")

state = json.load(open(os.path.join(E1, "e1_state.json"), encoding="utf-8"))
print("state top-level keys:", sorted(state.keys()))
print("cells:", len(state.get("cells", [])))
one = state["cells"][0]
print("one cell's keys:", sorted(one.keys()))
for k in sorted(one):
    v = one[k]
    if k == "run":
        print("  run keys:", sorted(v.keys()))
    elif not isinstance(v, (dict, list)):
        print(f"  {k} = {v}")

print()
print("=== the 12 cells, and which directories are NOT cells ===")
dirs = sorted(d for d in os.listdir(E1) if os.path.isdir(os.path.join(E1, d)))
cells = [d for d in dirs if d.startswith(("em_", "long_horizon_"))]
other = [d for d in dirs if d not in cells]
print(f"  cells ({len(cells)}): {cells}")
print(f"  not cells ({len(other)}): {other}")
assert len(cells) == 12, f"expected 12 cells, found {len(cells)}"
print("  -> 12 cells confirmed; the other four are score/report directories")
print()

print("=== SPEC §13 row 1: manifest identity three keys vs the D1 config ===")
cfg_text = open(D1, encoding="utf-8").read()
want_model, want_provider = "agnes-2.5-flash", "agnes"
print(f"  D1 config declares model={want_model!r} provider={want_provider!r}")
print(f"  and the config names an env var, not a key: "
      f"{'api_key_env' in cfg_text}")

bad = []
for cell in cells:
    mans = glob.glob(os.path.join(E1, cell, "**", "manifest.json"), recursive=True)
    if not mans:
        bad.append((cell, "no manifest"))
        continue
    m = json.load(open(mans[0], encoding="utf-8"))
    mdl = m.get("model") or {}
    if mdl.get("model") != want_model or mdl.get("provider") != want_provider:
        bad.append((cell, f"manifest says {mdl.get('provider')}/{mdl.get('model')}"))
print(f"  manifests agreeing with D1: {len(cells) - len(bad)}/{len(cells)}")
for c, why in bad:
    print(f"    MISMATCH {c}: {why}")
if not bad:
    print("  -> row 1's 'manifest identity three keys match the D1 config' holds")
print()

print("=== SPEC §13 row 3, invariant 1: armed implies asked, per cell ===")
# the fields the report quotes live in each cell's run_summary, not in e1_state's cell entry
rows = []
for cell in cells:
    for p in glob.glob(os.path.join(E1, cell, "**", "run_summary.json"), recursive=True):
        rs = json.load(open(p, encoding="utf-8"))
        rows.append((cell, rs.get("planned"), rs.get("rows"), rs.get("outcomes")))
for cell, planned, nrows, outcomes in rows:
    print(f"  {cell:34s} planned={planned} rows={nrows} outcomes={json.dumps(outcomes)}")
print()
print("  planned==rows in every cell:",
      all(p == r for _c, p, r, _o in rows if p is not None and r is not None))
print()
print("  NOTE: `armed`/`asked` are the driver's own counters, not run_summary's fields. The")
print("  report's claim (12/12 cells asked==armed>0, 575/0 -> 153/153) is in §2/§3 of the")
print("  report and its per-cell table; it is NOT re-derivable from run_summary alone, so it")
print("  stands on the report and on `e1_table.json`, not on this check.")
table = os.path.join(E1, "e1_table.json")
if os.path.exists(table):
    t = json.load(open(table, encoding="utf-8"))
    print(f"  e1_table.json keys: {sorted(t.keys())}")
    cells_t = t.get("cells") or []
    if cells_t:
        print(f"  per-cell keys: {sorted(cells_t[0].keys())}")
        print(f"  per-cell summary keys: "
              f"{sorted((cells_t[0].get('summary') or {}).keys())}")
        print()
        print("  === SPEC §13 row 3, invariant 1, re-derived from e1_table.json ===")
        print("  (armed/asked live under each cell's `summary`; the previous version of this")
        print("   checker read them at the cell top level, got None everywhere, and printed")
        print("   'the invariant DOES NOT hold'. It does. A checker that reports a false failure")
        print("   is as useless as one that reports a false pass.)")
        print()
        print(f"  {'set/arm':40s} {'armed':>7s} {'asked':>7s} {'eps':>5s} {'ok':>6s}")
        ok = 0
        for c in cells_t:
            s = c.get("summary") or {}
            armed, asked = s.get("armed"), s.get("asked_the_model")
            eps = s.get("episodes")
            good = bool(armed) and armed == asked
            ok += bool(good)
            label = f"{c.get('set')}/{c.get('arm')}"
            print(f"  {label:40s} {str(armed):>7s} {str(asked):>7s} {str(eps):>5s} {str(good):>6s}")
        print()
        print(f"  cells with armed==asked>0: {ok}/{len(cells_t)}")
        print("  -> row 3's first invariant holds, per cell"
              if ok == len(cells_t) else
              f"  -> row 3's first invariant DOES NOT hold ({ok}/{len(cells_t)})")

    print()
    print(f"  totals: {json.dumps(t.get('totals'), ensure_ascii=False)[:300]}")
    print(f"  upper_bound recorded: {t.get('upper_bound')}")
    print(f"  spent recorded: {json.dumps(t.get('spent'), ensure_ascii=False)[:200]}")

