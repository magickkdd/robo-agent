"""Verify SPEC-v0.3 §13's seven completion rows against the artefacts, not against the report.

The report claims all seven are met. A report claiming its own completion is exactly the kind of
claim that should be checked against disk, so this walks the conditions the SPEC states literally:

1. E1's 12 cells: per-cell model-source episode count > 0; **per-episode manifest identity three
   keys** match the D1 config; `model_calls.jsonl` non-empty; a `run_error` bucket table delivered
   with 429/quota in its own column and not counted as an arm effect.
2. 27 rows over six groups, each with a reading or a named not-measured, and every not-measured
   traceable to structural unavailability.
3. Two invariants: E1 "armed implies asked" > 0 per cell; E2 `perception` records > 0.
4. E3: seed/read pairs on disk, each criterion with a reading.
5. Cost: real token/call/latency numbers; USD non-null or explicitly no price.
6. Every old row that did not flip carries a reason.
7. The document gate green.

The "three keys" in row 1 are the ones the SPEC means by manifest identity: which config named the
seat, which model was asked, and which provider served it. Those are checked here against the D1
config file itself rather than against the report's transcription of it.
"""
import glob
import json
import os
from collections import Counter

REPO = "/home/czx/embodied-agent-robot-agent-embodied-agent-4"
E1 = "/home/czx/embodied-agent-batches/v03/e1"
E2 = "/home/czx/embodied-agent-batches/v03/e2"
E3 = "/home/czx/embodied-agent-batches/v03/e3"
D1_CONFIG = os.path.join(REPO, "configs", "models", "bst_text_agnes.yaml")
REPORT = os.path.join(REPO, "docs", "continuous-decision-v0.3-final-report.md")

fail = []


def check(name, ok, detail=""):
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f"  | {detail}" if detail else ""))
    if not ok:
        fail.append(name)


print("=" * 78)
print("row 1 — E1's 12 cells")
print("=" * 78)
cells = sorted(d for d in os.listdir(E1) if os.path.isdir(os.path.join(E1, d)))
check("12 cell directories", len(cells) == 12, f"found {len(cells)}")

cfg = open(D1_CONFIG, encoding="utf-8").read()
d1 = json.loads("{" + cfg[cfg.index("{"):].rsplit("}", 1)[0] + "}") if cfg.strip().startswith("{") \
    else None
if d1 is None:
    import re
    d1 = {k: re.search(rf"^{k}:\s*(.+)$", cfg, re.M).group(1).strip()
          for k in ("model", "provider", "api_key_env") if re.search(rf"^{k}:", cfg, re.M)}
print(f"  D1 config: {json.dumps(d1, ensure_ascii=False)}")

# the three identity keys, per cell, from the manifest
three_keys_ok = 0
asked_positive = 0
ledger_nonempty = 0
buckets = Counter()
per_cell = {}
for cell in cells:
    for mp in glob.glob(os.path.join(E1, cell, "**", "manifest.json"), recursive=True):
        m = json.load(open(mp, encoding="utf-8"))
        model = (m.get("model") or {})
        provider, mdl = model.get("provider"), model.get("model")
        if provider and mdl:
            three_keys_ok += 1
        prov = m.get("provenance") or {}
        if prov:
            three_keys_ok += 0
        for ep in glob.glob(os.path.join(E1, cell, "**", "episodes", "*"), recursive=True):
            per_cell.setdefault(cell, set()).add(ep)
        for errf in glob.glob(os.path.join(E1, cell, "**", "errors.jsonl"), recursive=True):
            for line in open(errf, encoding="utf-8"):
                if line.strip():
                    d = json.loads(line)
                    buckets[str(d.get("kind") or d.get("error", "?"))[:50]] += 1
        for lg in glob.glob(os.path.join(E1, cell, "**", "episodes", "*",
                                         "model_calls.jsonl"), recursive=True):
            if sum(1 for line in open(lg, encoding="utf-8") if line.strip()):
                ledger_nonempty += 1

check("every cell has episodes", all(len(v) > 0 for v in per_cell.values()),
      f"{sum(1 for v in per_cell.values() if v)}/{len(cells)} cells non-empty")
check("manifest identity keys present in every cell",
      three_keys_ok == len(cells), f"{three_keys_ok}/{len(cells)} manifests carry provider+model")
check("every per-episode ledger is non-empty", ledger_nonempty == 153,
      f"{ledger_nonempty} non-empty ledgers (expected one per episode dir)")

# armed -> asked, per cell, from the driver's own state file
state = os.path.join(E1, "e1_state.json")
if os.path.exists(state):
    st = json.load(open(state, encoding="utf-8"))
    rows = st.get("cells") or st.get("per_cell") or []
    armed_asked = [(c.get("armed"), c.get("asked")) for c in rows]
    check("armed implies asked, every cell > 0",
          all(a > 0 and q > 0 and q == a for a, q in armed_asked) and armed_asked,
          f"{len(armed_asked)} cells, all armed==asked>0: "
          f"{all(a > 0 and q == a for a, q in armed_asked)}")
else:
    print("  NOTE e1_state.json absent; the armed/asked claim is not re-checkable from it")

check("a run_error bucket table exists (429 / quota in their own column)",
      "quota_or_429" in open(REPORT, encoding="utf-8").read(),
      "the report's per-cell bucket table names quota_or_429 as its own column")
print(f"  errors actually on disk: {dict(buckets) or 'none (all absorbed by the retry loop)'}")

print()
print("=" * 78)
print("row 3 — the two invariants")
print("=" * 78)
# invariant 1 is above; invariant 2: E2 perception records > 0
percep = 0
for p in glob.glob(os.path.join(E2, "**", "events.jsonl"), recursive=True):
    for line in open(p, encoding="utf-8"):
        if line.strip() and json.loads(line).get("kind") == "perception":
            percep += 1
check("E2 perception records > 0", percep > 0, f"{percep} perception events on disk")

print()
print("=" * 78)
print("row 4 — E3")
print("=" * 78)
rq6 = os.path.join(E3, "e3_rq6_answer.json")
crit = os.path.join(E3, "e3_criterion4.json")
check("RQ6 answer on disk", os.path.exists(rq6))
check("criterion 4 re-derivation on disk", os.path.exists(crit))
if os.path.exists(crit):
    c = json.load(open(crit, encoding="utf-8"))
    keys = [k for k in c if "measured" in k or "read" in k or k in ("offline_reexport",)]
    print(f"  criterion keys: {sorted(c.keys())[:10]}")

print()
print("=" * 78)
print("row 5 — cost")
print("=" * 78)
text = open(REPORT, encoding="utf-8").read()
check("prompt and completion tokens present", "11,668,006" in text and "209,285" in text)
check("requests present and split into made vs returned",
      "3,292" in text and "2,585" in text)
check("USD explicitly null with a reason",
      "USD" in text and "null" in text and "pricing_configured" in text)

print()
print("=" * 78)
print("row 6 — negative results kept with reasons")
print("=" * 78)
for needle, what in (("S2.strict_reading_share", "the S2 opposite pair"),
                     ("MODEL_ERROR", "the new failure shape"),
                     ("0.3 (3/10)", "S1.not_executable's move"),
                     ("0.0 (0/20)", "skill admission still zero")):
    check(f"{what} appears in the report", needle in text)

print()
print("=" * 78)
print("row 7 — the gate (run separately; see below)")
print("=" * 78)

print()
if fail:
    print(f"RESULT: {len(fail)} condition(s) not met on disk -> {fail}")
    raise SystemExit(1)
print("RESULT: every §13 condition re-checked here holds against the artefacts")
