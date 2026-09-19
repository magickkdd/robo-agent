"""SPEC 11.4 blind review: the pre-registered criteria, applied mechanically.

The report deliberately does *not* score the two behaviour readings an automatic
predicate cannot settle — it only names them in `needs_blind_review`. This module
turns those pointers into a sheet a human can judge, and turns the human verdicts
back into a number with its denominator named. Three properties are the whole
point of the file:

* **blind.** An item is identified by a hash of `(rubric_id, kind, source, locator)`
  and every identity field — `episode_id`, `case_id`, `mode`, `planner`, `model`,
  `provider` — is stripped out of the evidence, including from inside the payload
  dicts. Which system acted is not one of the facts a reviewer is allowed to weigh,
  and the mode letter is encoded in the episode id, so the id cannot appear.
* **the criteria are frozen before the run.** `configs/experiment/` holds the
  rubric; its bytes are hashed, and a verdict row that does not carry that hash is
  refused. Editing a question after the verdicts arrive is therefore visible rather
  than silent (SPEC 7, 11.4: 预先制定准则).
* **disagreement survives.** Two reviewers must answer an item for it to count, and
  a split is reported as `divided` and listed, never averaged and never adjudicated
  by a third human. Two rates are published — unanimous-adverse over the items both
  reviewers settled, and the same numerator with the splits counted as adverse —
  because the gap between them *is* the sensitivity of the reading.

Nothing here lets a model grade a policy: `reviewer_kind` must be `human`
(SPEC 11.4: 不能靠模型自评分), and a verdict file is refused row by row with the
reason, so a malformed sheet loses its rows to the open rather than to the bin.
"""
from __future__ import annotations

import hashlib
import json
import os

from ..core.events import EpisodeStore

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUBRIC_PATH = os.path.join(REPO_ROOT, "configs", "experiment", "blind_review_rubric_v1.json")
SHEET_NAME = os.path.join("blind_review", "sheet.json")
RECORDS_NAME = os.path.join("blind_review", "records.jsonl")

# fields that identify *who* acted rather than *what* the state was, and the
# automatic verdict itself: none of them may reach the reviewer
LEAKY = ("episode_id", "case_id", "mode", "planner", "model", "provider", "policy_label",
         "subset", "expected", "repeat")
# the label sets and the automatic reading are the answer to the question asked
LEAKY_FOR_FORK = LEAKY + ("labels", "acceptable_labels", "rejected_labels", "accepted_because",
                          "rejected_because", "fit", "miss_kind", "error", "provider_cost")


def load_rubric(path: str = RUBRIC_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rubric_sha256(path: str = RUBRIC_PATH) -> str:
    """The hash of the exact bytes on disk, not of a re-serialisation: a key
    reorder or a whitespace edit must count as a changed criterion, because the
    thing being bound is the text a reviewer read."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _display_path(path: str) -> str:
    rel = os.path.relpath(path, REPO_ROOT)
    return path if rel.startswith("..") else rel


def item_key(rubric_id: str, kind: str, source_id: str, locator: str) -> str:
    blob = "|".join([rubric_id, kind, str(source_id), str(locator)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _scrub(obj, drop: tuple[str, ...]):
    """Drop identity keys at every depth — a payload carries `episode_id` itself."""
    if isinstance(obj, dict):
        return {k: _scrub(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [_scrub(v, drop) for v in obj]
    return obj


def _by_type(events: list[dict], event_type: str) -> dict[int, dict]:
    return {e["sequence"]: e["payload"] for e in events if e.get("type") == event_type}


def behaviour_items(run_dir: str, pointers: list[dict], rubric: dict) -> tuple[list, list]:
    """Kind 1: a place whose object->target already read true. The evidence is the
    three log records around that decision, so the reviewer sees the same round the
    policy saw rather than a summary of it."""
    questions = {it["kind"]: it["question"] for it in rubric["items"]}
    scales = {it["kind"]: it["scale"] for it in rubric["items"]}
    kind = "place_of_already_satisfied"
    cached: dict[str, list[dict]] = {}
    items, unreviewable = [], []
    for p in pointers:
        episode, sequence = str(p.get("episode_id", "")), p.get("sequence")
        key = item_key(rubric["rubric_id"], kind, episode, sequence)
        if episode not in cached:
            # one directory per episode, the way `evaluation.run` lays a run out
            cached[episode] = EpisodeStore(os.path.join(run_dir, "episodes", episode),
                                           episode).read_all()
        events = cached[episode]
        # the *nearest preceding* context is the one that was answered; taking the
        # first would hand the reviewer round 1's view of the world for a decision
        # taken at round 7
        before = [e for e in events if e["type"] == "decision_context"
                  and e["sequence"] < sequence]
        ctx = before[-1] if before else None
        dec = _by_type(events, "decision").get(sequence)
        if not events or ctx is None or dec is None:
            unreviewable.append({"item_key": key, "kind": kind,
                                 "reason": f"no decision_context/decision pair around "
                                           f"sequence {sequence} of an episode that "
                                           f"{'has' if events else 'has no'} log records"})
            continue
        nxt = next((e for e in events if e["sequence"] > sequence and e["type"] ==
                    "execution_feedback"), None)
        items.append({
            "item_key": key, "kind": kind, "question": questions[kind], "scale": scales[kind],
            "evidence": {"assignment": p.get("assignment"),
                         "context_at_the_decision": _scrub(ctx["payload"], LEAKY),
                         "decision": _scrub(dec, LEAKY),
                         "feedback_that_followed": _scrub(nxt["payload"], LEAKY) if nxt else None},
        })
    return items, unreviewable


def fork_items(rows: list[dict], rubric: dict) -> list[dict]:
    """Kind 2: a fork answer that carried none of the frozen labels, so `fit` was
    false by absence rather than by a positive rejection."""
    questions = {it["kind"]: it["question"] for it in rubric["items"]}
    scales = {it["kind"]: it["scale"] for it in rubric["items"]}
    kind = "fork_answer_outside_both_label_sets"
    rid = rubric["rubric_id"]
    out = []
    for row in rows:
        if row.get("error") is not None or row.get("labels") or row.get("fit"):
            continue
        src = f'{row.get("pair_id", "")}:{row.get("arm", "")}'
        out.append({
            "item_key": item_key(rid, kind, src, row.get("context_id", "")),
            "kind": kind, "question": questions[kind], "scale": scales[kind],
            "evidence": _scrub(row, LEAKY_FOR_FORK),
        })
    return out


def _read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("contexts", [])


def build_sheet(rubric: dict, run_dir: str, behaviour_pointers: list[dict],
                state_util_paths: list[str], rubric_path: str = RUBRIC_PATH) -> dict:
    """Every pointer, none sampled away (the rubric's own `sampling` is 100%)."""
    items, unreviewable = behaviour_items(run_dir, behaviour_pointers, rubric)
    for path in state_util_paths:
        items += fork_items(_read_rows(path), rubric)
    keys = [it["item_key"] for it in items]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"{len(dupes)} contested items collapsed onto one blind key "
                         f"({dupes[:3]}...): one reviewer verdict would then stand in for "
                         "several actions, which is not a smaller sample but a fake one")
    for it in items:
        blob = json.dumps(it["evidence"], ensure_ascii=False, sort_keys=True)
        for leak in ("episode_id", '"mode"', "policy_label"):
            if leak in blob:
                raise ValueError(f"item {it['item_key']} still carries {leak} in its evidence")
    return {"rubric_id": rubric["rubric_id"], "rubric_path": _display_path(rubric_path),
            "rubric_sha256": rubric_sha256(rubric_path),
            "sampling": rubric["sampling"], "unit": rubric["unit"],
            "reviewers": rubric["reviewers"], "record_schema": rubric["record_schema"],
            "items": sorted(items, key=lambda i: i["item_key"]),
            "not_reviewable": unreviewable}


def write_template(sheet: dict, path: str) -> int:
    """A file a human can fill in, with the verdict left empty.

    Blank rows are the point: the sheet's own hash is already in every line, and an
    unfilled template still refuses to aggregate, so no one can hand in the form as
    a result. `reviewer_id` is a slot to overwrite, not an identity to keep."""
    rows = [{"item_key": it["item_key"], "reviewer_id": f"reviewer-{n}",
             "reviewer_kind": "human", "rubric_sha256": sheet["rubric_sha256"],
             "verdict": None, "note": ""}
            for it in sheet["items"] for n in range(1, int(sheet["reviewers"]["count"]) + 1)]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def read_verdicts(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def validate_verdicts(rubric: dict, sheet: dict, verdicts: list[dict]) -> dict:
    """Row-by-row acceptance, each refusal with its reason."""
    sha = sheet["rubric_sha256"]
    kinds = {it["item_key"]: it["kind"] for it in sheet["items"]}
    scales = {it["kind"]: it["scale"] for it in rubric["items"]}
    accepted, refused, seen = [], [], set()
    for row in verdicts:
        key, reviewer = str(row.get("item_key", "")), str(row.get("reviewer_id", ""))
        why = None
        if row.get("reviewer_kind") != "human":
            why = ("reviewer_kind must be 'human': the policy that acted may not grade "
                   "itself, and neither may a model on its behalf (SPEC 11.4)")
        elif row.get("rubric_sha256") != sha:
            why = "rubric_sha256 does not match the criteria this sheet was built from"
        elif key not in kinds:
            why = "item_key is not on the sheet"
        elif row.get("verdict") not in scales[kinds[key]]:
            why = f"verdict is not in the {kinds[key]} scale {scales[kinds[key]]}"
        elif (key, reviewer) in seen:
            why = "duplicate (item_key, reviewer_id)"
        if why:
            refused.append({"row": row, "reason": why})
            continue
        seen.add((key, reviewer))
        accepted.append({"item_key": key, "reviewer_id": reviewer, "kind": kinds[key],
                         "verdict": row["verdict"]})
    return {"accepted": accepted, "refused": refused}


def _items_by_kind(sheet: dict, rubric: dict) -> dict:
    """Every registered kind, including the ones this run produced none of: a kind
    that came out empty is a fact about the sample, not a row to be averaged away."""
    return {it["kind"]: sum(1 for x in sheet["items"] if x["kind"] == it["kind"])
            for it in rubric["items"]}


def aggregate(rubric: dict, sheet: dict, accepted: list[dict]) -> dict:
    """The rubric's `rate_definition`, with every denominator named."""
    required = int(rubric["reviewers"]["count"])
    adverse_of = {it["kind"]: it["adverse_value"] for it in rubric["items"]}
    kinds = {it["item_key"]: it["kind"] for it in sheet["items"]}
    by_item: dict[str, list[str]] = {}
    for a in accepted:
        by_item.setdefault(a["item_key"], []).append(a["verdict"])
    rows = []
    for key, verdicts in sorted(by_item.items()):
        adverse = adverse_of[kinds[key]]
        if len(verdicts) < required:
            status = "partial"
        elif "cannot_tell" in verdicts:
            status = "cannot_tell"
        elif all(v == adverse for v in verdicts):
            status = "adverse"
        elif all(v != adverse for v in verdicts):
            status = "reasonable"
        else:
            status = "divided"
        rows.append({"item_key": key, "kind": kinds[key], "verdicts": sorted(verdicts),
                     "status": status})
    settled = [r for r in rows if r["status"] in ("adverse", "reasonable", "divided")]
    adverse_n = sum(1 for r in settled if r["status"] == "adverse")
    divided_n = sum(1 for r in settled if r["status"] == "divided")
    all_keys = {it["item_key"] for it in sheet["items"]}
    return {
        "reviewers_required": required,
        "items_settled": len(settled),
        "adverse_rate_strict": round(adverse_n / len(settled), 4) if settled else None,
        "adverse_rate_counting_divided_as_adverse":
            round((adverse_n + divided_n) / len(settled), 4) if settled else None,
        "by_status": {s: sum(1 for r in rows if r["status"] == s)
                      for s in ("adverse", "reasonable", "divided", "cannot_tell", "partial")},
        "unreviewed": sorted(all_keys - set(by_item)),
        "items_by_kind": _items_by_kind(sheet, rubric),
        "rows": rows,
        "rate_definition": rubric["rate_definition"],
    }


def review_state(run_dir: str, behaviour_pointers: list[dict],
                 state_util_paths: list[str] | None = None, rubric: dict | None = None,
                 records_path: str | None = None,
                 rubric_path: str = RUBRIC_PATH) -> tuple[dict, dict]:
    """The sheet a human judges, and what its verdicts currently say.

    One derivation, two callers: `report` reads only the state, `cli blind-review`
    also writes the sheet, so the numbers in the report and the rows on the printed
    sheet cannot come from different readings of the logs."""
    rubric = rubric or load_rubric(rubric_path)
    paths = list(state_util_paths or [])
    default = os.path.join(run_dir, "state_utilization.json")
    if os.path.exists(default):
        paths.append(default)
    sheet = build_sheet(rubric, run_dir, behaviour_pointers, paths, rubric_path=rubric_path)
    verdict_path = records_path or os.path.join(run_dir, RECORDS_NAME)
    out = {"rubric": {"path": _display_path(rubric_path), "rubric_id": rubric["rubric_id"],
                      "sha256": sheet["rubric_sha256"]},
           "items": len(sheet["items"]),
           "items_by_kind": _items_by_kind(sheet, rubric),
           "not_reviewable": sheet["not_reviewable"],
           "prohibitions": rubric["prohibitions"]}
    if not os.path.exists(verdict_path):
        # `ran: false` is a state of the experiment, not a score of zero (SPEC 11.4)
        return sheet, {"ran": False, "verdict_file": verdict_path,
                       "note": ("no human verdict has been recorded for these items: they are "
                                "unreviewed, which is not the same as reviewed-and-clear "
                                "(SPEC 11.4)"),
                       **out}
    checked = validate_verdicts(rubric, sheet, read_verdicts(verdict_path))
    return sheet, {"ran": True, "verdict_file": verdict_path,
                   "accepted": len(checked["accepted"]), "refused": checked["refused"],
                   **aggregate(rubric, sheet, checked["accepted"]), **out}


def scan(run_dir: str, behaviour_pointers: list[dict], state_util_paths: list[str] | None = None,
         rubric: dict | None = None, records_path: str | None = None,
         rubric_path: str = RUBRIC_PATH) -> dict:
    """The blind-review state of one finished run, read off its artifacts."""
    return review_state(run_dir, behaviour_pointers, state_util_paths, rubric, records_path,
                        rubric_path)[1]
