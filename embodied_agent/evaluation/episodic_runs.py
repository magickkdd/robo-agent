"""Drive the `em` pairs through the production batch entry, then read back what the memory did (§11, §13 P3).

Two halves, because the experiment is two things.

**Run.** `run_episodic_pairs` calls `run_group` — the same entry `cli.py run` uses, so nothing here is
a test-harness imitation of the batch path — once per (pair, arm, role). The one thing it adds is the
thing `run_group` cannot do for itself: **one store per pair**. A batch installs one store for every
case it runs, and episodes append to the file the next episode reads, so an `em` batch of all eight
cases would hand p2's reader p1's writer's residue as well, and §13's RQ3 would be answered about a
store nobody isolated. The pairing is therefore the unit of batching here: writer first, reader
second, into a file named for the pair. Both arms run the same two cases, so the control's store
holds the same number of rows and the difference between them is the §9 gate and nothing else.

**Measure.** `measure_episodic_pairs` reads the artifacts back and answers, per pair, the only
question the set was authored for: did the recall change what the reader did, and in the direction
the pair pre-registered? `first_pick` comes from the trajectory, not from a rationale;
`trajectory` is the ordered list of executed actions, which is what §12.3 says to grade;
`recalled`, `memory_order`, `declines` and `followed_rounds` come from the `memory_retrieval` records
and the `policy` block of the episode summary (the control policy's own per-round trace, filed by
`run.py`). Every field is compared against `episodic_tasks.EM_PAIRS[].expectation`, and the verdict
is recorded per field rather than as one boolean, because a pair that half-agrees is information and
a pair that reports "mismatch" is not.

Zero spend: `planner_kind="rule"`, `policy="memory"`, `perceive="privileged"`, one repeat.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Optional

from ..core.v02 import ablation as ablation_record
from .episodic_tasks import (EM_ARMS, EM_CONTROL_ARM, EM_FROZEN_PATH, EM_PAIRS,
                             EM_SET_NAME, EM_TREATMENT_ARM)
from .run import batch_spend, run_group

#: The order a pair's two episodes go into one store, and the only order it can go in. The reader's
#: claim to test is *one writer's* residue: run it first and every recall is empty, run it twice and
#: the row the guard weighed is one the pair never declared.
EM_ROLES = ("writer", "reader")


# --------------------------------------------------------------------- run ----
def run_episodic_pairs(out_root: str, *, pairs: Optional[list[dict]] = None,
                       arms: tuple[str, ...] = EM_ARMS, frames: bool = False,
                       planner_kind: str = "rule", model_config: Optional[str] = None,
                       stop_at_requests: Optional[int] = None) -> dict:
    """Every pair, every arm, writer-then-reader into one store per pair per arm.

    `frames=False` is the default and the honest setting for this channel: a privileged batch renders
    no camera, so writing frame PNGs would only cost disk.

    **The decision maker is a parameter, and the policy is derived from it rather than passed.**
    On the rule seat the reader is answered by `MemoryPolicy`, which *is* the decision maker: it
    reads only `ctx.model_payload()`. That is why `run_group` refuses `policy="memory"` together
    with a model planner (two decision makers, rows whose source nobody could name) — and why
    this function does not expose a way to ask for it. `policy` is computed here:

        rule seat     -> policy="memory"   (MemoryPolicy answers; zero spend)
        model seat    -> policy=None       (the model planner answers; the store still records)

    So the incoherent batch cannot be *expressed*, rather than being documented as unsupported.
    A `--planner` flag that also carried `--policy memory` would be one careless invocation away
    from the exact failure the refusal exists to prevent.

    A model seat keeps the experience store and the arm's ablation, which is the whole point:
    the pair protocol asks whether a reader *uses* what the writer left, and on a model seat the
    reader is the model. What it does not do is make the two arms differ only in memory — a model
    planner is stochastic, so treatment and control also differ by the draw. That is a real
    confound and `measure_episodic_pairs` says which seat produced a table rather than letting a
    rule-seat reading and a model-seat reading be compared as if they were the same experiment.

    `planner_kind="fixture"` is refused: it is a test double, and a `pairs_run.json` naming it
    would be indistinguishable in kind from one naming a real seat.

    **`stop_at_requests` is checked between pairs, and the check lives here rather than in a
    driver.** The bound a pre-registration declares is only real if the thing that spends enforces
    it, and a check bolted on outside would be reporting a bound it had no power to keep. It is
    checked at a pair boundary because a pair is the protocol's unit — a half-run pair is a writer
    with no reader, and the reader is the whole object of the measurement. On a halt the artifact
    records which pairs ran and names the bound, so a partial batch says so rather than looking
    like a small complete one.
    """
    from ..episodic.store import ExperienceStore

    if planner_kind == "fixture":
        raise ValueError(
            "--planner fixture is a test double; a pairs_run.json naming it could not be told "
            "apart from one naming a real seat. Use 'rule' or a model planner.")
    if planner_kind not in ("rule", "deepseek"):
        raise ValueError(f"--planner must be 'rule' or a model planner, got {planner_kind!r}")
    if planner_kind != "rule" and not model_config:
        raise ValueError(f"--planner {planner_kind} needs --model-config: without a named seat "
                         f"the artifact could not say who decided, which is the one thing this "
                         f"ledger exists to record")
    policy = "memory" if planner_kind == "rule" else None

    os.makedirs(out_root, exist_ok=True)
    manifest: list[dict] = []
    halted_at: Optional[str] = None
    spent = 0
    for pair in (pairs if pairs is not None else EM_PAIRS):
        if stop_at_requests is not None and spent >= stop_at_requests:
            halted_at = pair["pair_id"]
            break
        for arm in arms:
            arm_root = os.path.join(out_root, pair["pair_id"], arm)
            store_path = os.path.join(arm_root, "store.jsonl")
            os.makedirs(arm_root, exist_ok=True)
            row = {"pair_id": pair["pair_id"], "condition": pair["condition"], "arm": arm,
                   "store_path": store_path, "batches": {}}
            for role in EM_ROLES:
                case_id = pair["writer"] if role == "writer" else pair["reader"]
                # Reload from the file for every batch: the reader must query exactly what the
                # writer left, and an in-memory handle would also be a store the writer's failed
                # episodes could still be holding rows for.
                store = ExperienceStore.load(store_path)
                stats = run_group(EM_SET_NAME, modes=("B",), planner_kind=planner_kind,
                                  model_config=model_config, repeats=1,
                                  out_root=os.path.join(arm_root, role), case_ids=[case_id],
                                  frames=frames, perceive="privileged",
                                  ablation=ablation_record(arm), policy=policy,
                                  experience_store=store)
                row["batches"][role] = {"case_id": case_id, "root": stats["root"],
                                        "run_id": stats["run_id"], "rows": stats["rows"]}
            manifest.append(row)
        if stop_at_requests is not None:
            # The run root each batch *reported* is the authoritative path, not the one this
            # function passed in: `run_group` creates a timestamped subdirectory beneath it. The
            # first version of this check globbed the directory it had asked for, found an empty
            # tree, and reported `spent 0, completed within bound` — a bound check that measured
            # nothing and called it a pass, which is the most dangerous shape this project has
            # produced. A zero reading now counts as a failure of the check, not as good news.
            spent = sum(batch_spend(b["root"])["http_requests"]
                        for r in manifest for b in r["batches"].values())
            if spent <= 0:
                raise RuntimeError(
                    f"the stop rule read {spent} requests under {out_root} after "
                    f"{len(manifest)} pair-arms: the ledgers are not where it is looking, so a "
                    f"'within bound' verdict from this check would be meaningless. Refusing to "
                    f"continue rather than reporting a bound it did not measure.")
    artifact = {"kind": "episodic_pairs_run", "root": out_root, "set": EM_SET_NAME,
                "arms": list(arms), "policy": policy, "planner": planner_kind,
                "model_config": model_config, "perceive": "privileged", "repeats": 1,
                "pairs_ran": [r["pair_id"] for r in manifest],
                "pairs_declared": [p["pair_id"] for p in (pairs if pairs is not None else EM_PAIRS)],
                "stop_rule": {"bound_requests": stop_at_requests,
                              "spent_requests_when_stopped": spent if stop_at_requests is not None
                              else None,
                              "halted_before_pair": halted_at,
                              "note": "checked at a pair boundary; a pair is the protocol's unit, "
                                      "so a half-run pair would be a writer with no reader"},
                "batches": manifest}
    with open(os.path.join(out_root, "pairs_run.json"), "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    return artifact


# ---------------------------------------------------------------- measure ----
def _episode_dir(batch: dict) -> str:
    hits = sorted(glob.glob(os.path.join(batch["root"], "episodes", "*")))
    if len(hits) != 1:
        raise ValueError(f"{batch['case_id']}: expected one episode under {batch['root']}, "
                         f"found {len(hits)}")
    return hits[0]


#: Returned by `_role_state` when the root path does not exist at all. Deferring is the honest
#: answer: the metric cannot say anything about a path that was never created, and the reader
#: (`read_pair` → `_episode_dir`) already refuses with the path named. So production still fails
#: loudly; only a caller that has replaced the reader is trusted to supply the episode.
DEFER = "defer"


def _recorded_failure(root: str) -> Optional[dict]:
    """The error this role's own goal ledger recorded, if it recorded one."""
    path = os.path.join(root, "goal_resolutions", "goal_calls.jsonl")
    if not os.path.exists(path):
        return None
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("error"):
            return {"error": row.get("error"),
                    "http_requests_this_call": row.get("http_requests_this_call"),
                    "transport_attempts": row.get("transport_attempts")}
    return None


def _role_state(batch: Optional[dict]) -> tuple[str, Optional[dict]]:
    """Did this role run, fail demonstrably, or is its state unknown?

    Three answers, and the middle one is the point. A manifest row is created when the arm's loop
    is entered, so a row and a run are different claims; on the MEM-1 model-seat batch two control
    readers exhausted five attempts on HTTP 429 and wrote a run root with a goal ledger and no
    episode.

    * **ran** — an episode directory exists.
    * **failed** — the root exists, there is no episode, and the goal ledger names an error. The
      pair-arm is absent and the error travels with it.
    * **defer** — the root path does not exist, so the metric has nothing to reconcile against.

    And a fourth case that is *not* a role state but a refusal: the root exists, produced no
    episode, and recorded no reason. That is a corrupt ledger, not a failed run, and v0.2's
    contract is right that it must not be quietly averaged over the pairs that happened to
    survive — `check_unreconciled` raises on it. Collapsing that into "absent" is how corruption
    becomes a plausible-looking rate.
    """
    if not batch or not batch.get("root"):
        return DEFER, None
    root = batch["root"]
    if len(glob.glob(os.path.join(root, "episodes", "*"))) == 1:
        return "ran", None
    if not os.path.exists(root):
        return DEFER, None
    failure = _recorded_failure(root)
    if failure is not None:
        return "failed", failure
    return "ran", {"corrupt": True}


def check_unreconciled(root: str) -> None:
    """Refuse a ledger that names a run root which exists, produced no episode and said nothing.

    v0.2's contract: "`absent` is a claim about the ledger, so the ledger and the disk have to be
    reconciled by a refusal, not by a rate computed over the pairs that happened to survive." That
    is kept, and narrowed to the case it was written for — a *silent* missing episode. A role
    that recorded why it produced nothing is not a reconciliation failure, and the MEM-1 batch is
    the case in point.
    """
    with open(os.path.join(root, "pairs_run.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    for row in ledger.get("batches", []):
        for role in EM_ROLES:
            batch = row.get("batches", {}).get(role)
            state, detail = _role_state(batch)
            if detail and detail.get("corrupt"):
                raise ValueError(
                    f"{row.get('pair_id')}/{row.get('arm')}/{role}: the ledger names a run root "
                    f"that exists but holds no episode and no recorded error "
                    f"({batch.get('root')}). That is a corrupt ledger, not a failed run: a run "
                    f"that failed records why in its goal ledger. Refusing rather than reporting "
                    f"this pair as 'did not run', which would quietly shrink the denominator.")


def pair_arm_ran(row: dict) -> bool:
    """A pair-arm counts as run unless one of its roles *demonstrably* failed.

    `defer` counts as run: a root path that does not exist is not evidence of anything, and the
    reader is the thing that will say so — in production with the path named, in a test with a
    reader the caller supplied. Only a recorded failure removes a pair-arm from the denominator.
    """
    return all(_role_state(row.get("batches", {}).get(role))[0] != "failed"
               for role in EM_ROLES)


def why_pair_arm_missing(row: Optional[dict]) -> dict:
    """Name the roles that did not run, and the error each one recorded, if any."""
    out: dict = {"roles_missing": []}
    if not row:
        out["reason"] = "no manifest row for this pair-arm"
        return out
    for role in EM_ROLES:
        state, detail = _role_state(row.get("batches", {}).get(role))
        if state == "ran" and not (detail or {}).get("corrupt"):
            continue
        out["roles_missing"].append(role)
        if (detail or {}).get("corrupt"):
            out.setdefault("role_errors", {})[role] = "no error recorded in the goal ledger"
        else:
            out.setdefault("role_errors", {})[role] = detail or "no error recorded in the goal ledger"
    return out
    return hits[0]


def _read_episode(episode_dir: str) -> dict:
    with open(os.path.join(episode_dir, "episode_summary.json"), encoding="utf-8") as f:
        summary = json.load(f)
    events = [json.loads(line) for line in
              open(os.path.join(episode_dir, "events.jsonl"), encoding="utf-8") if line.strip()]
    return {"dir": episode_dir, "summary": summary, "events": events}


def _trajectory(events: list[dict]) -> list[str]:
    """The executed actions in order, as the episode filed them.

    From the `decision` records rather than from `skill_call`, because the object of measurement is
    the *choice*: a call the runtime rejected or never made is not part of what the agent decided to
    do, and §12.3's other half (what actually happened) is read from the score and the outcome.
    """
    out = []
    for event in events:
        if event["type"] != "decision":
            continue
        payload = event["payload"]
        ex = payload.get("execute") or {}
        if payload.get("action") != "execute":
            out.append(str(payload.get("action")))
            continue
        args = dict(ex.get("args") or {})
        out.append(">".join(filter(None, [str(ex.get("skill") or ""),
                                          str(args.get("object_id") or ""),
                                          str(args.get("target_id") or "")])))
    return out


def _first_pick(events: list[dict]) -> Optional[str]:
    for event in events:
        payload = event["payload"]
        if event["type"] == "decision" and payload.get("action") == "execute":
            object_id = str(((payload.get("execute") or {}).get("args") or {})
                            .get("object_id") or "")
            if object_id:
                return object_id
    return None


def _plan_first_object(events: list[dict]) -> Optional[str]:
    """The object the plan itself would put first: `ready[0]`, no memory in sight.

    `plan_view` publishes `ready` in `row_key` order, and `work/p3d_lever_scan.py` measured that the
    first published row is the row the control policy takes in 370 of 370 rounds. This reads that
    order off the episode's own `plan` record, so a pair whose expectation says "whatever the plan
    does" is still checked, against the plan this episode was actually given.
    """
    for event in events:
        if event["type"] != "plan":
            continue
        view = event["payload"].get("view") or {}
        entity_of = {str(row.get("subgoal_id")): str(row.get("predicate_id") or "")
                     for row in view.get("rows") or []}
        for subgoal in view.get("ready") or []:
            parts = entity_of.get(str(subgoal), "").split(":")
            if len(parts) == 3:
                return parts[1]
    return None


def _recall_census(events: list[dict]) -> dict:
    """What §5.4 put on the page, round by round, and what the guard said about it."""
    rounds = []
    for event in events:
        if event["type"] != "memory_retrieval":
            continue
        payload = event["payload"]
        rows = payload.get("retrieved") or []
        rounds.append({
            "round_index": payload.get("round_index"),
            "store_size": payload.get("store_size"),
            "rows": len(rows),
            "experience_ids": [str(r.get("experience_id")) for r in rows],
            "relevance": [r.get("relevance") for r in rows],
            # `match_terms` is carried because §11's `retrieval relevance` row is not answerable
            # without it: a row that matched on nothing but the batch's own set name was not chosen
            # for any reason a reader would call relevance, and the frozen set says so in
            # `relevance_within_a_batch`. `episodic_metrics.py` counts that; this file only records.
            "match_terms": [sorted(str(t) for t in (r.get("match_terms") or [])) for r in rows],
            "contradicted": [bool(r.get("contradicted_current_state")) for r in rows],
            "clash_counts": [len((r.get("provenance") or {}).get("contradictions") or [])
                             for r in rows]})
    return {"rounds": rounds,
            "rounds_with_a_row": sum(1 for r in rounds if r["rows"]),
            "rows_offered": sum(r["rows"] for r in rounds),
            "rows_refuted": sum(1 for r in rounds for flag in r["contradicted"] if flag),
            "first_round_rows": rounds[0]["rows"] if rounds else 0,
            "first_round_refuted": bool(rounds and rounds[0]["contradicted"]
                                        and rounds[0]["contradicted"][0])}


def read_pair(root: str, pair: dict, arm: str) -> dict:
    """One arm's two episodes of one pair, reduced to the fields the comparison uses."""
    with open(os.path.join(root, "pairs_run.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    row = next(r for r in ledger["batches"]
               if r["pair_id"] == pair["pair_id"] and r["arm"] == arm)
    out: dict[str, Any] = {"pair_id": pair["pair_id"], "condition": pair["condition"], "arm": arm}
    for role in EM_ROLES:
        episode = _read_episode(_episode_dir(row["batches"][role]))
        summary, events = episode["summary"], episode["events"]
        policy = summary.get("policy") or {}
        result, score = summary.get("result") or {}, summary.get("score") or {}
        orders = [entry.get("memory_order") or [] for entry in policy.get("trace") or []]
        out[role] = {
            "case_id": row["batches"][role]["case_id"],
            "episode_id": summary.get("episode_id"),
            "first_pick": _first_pick(events),
            "plan_first_object": _plan_first_object(events),
            "trajectory": _trajectory(events),
            "outcome": result.get("terminal_status"),
            "failure_type": result.get("failure_type"),
            "complete_success": score.get("complete_success"),
            "decision_rounds": result.get("decision_rounds"),
            "skill_calls": result.get("skill_calls"),
            "provider": policy.get("provider"),
            "recalled_ids": policy.get("recalled_ids") or [],
            "declines": policy.get("declines") or [],
            "followed_rounds": policy.get("followed_rounds") or [],
            "first_memory_order": next((o for o in orders if o), []),
            "trace": policy.get("trace") or [],
            "episodic": summary.get("episodic") or {},
            "recall": _recall_census(events)}
    return out


def _first_pick_after_guard(reader: dict) -> Optional[str]:
    """The first pick made at a round whose retrieval record carried a refuted row.

    p2's claim is about a *sequence* — the misleading memory moves the first hand because nothing has
    been measured yet, and the decline governs the next one — so "the first pick" is not one number.
    """
    refuted = {int(r["round_index"]) for r in reader["recall"]["rounds"] if any(r["contradicted"])}
    if not refuted:
        return reader["first_pick"]
    for entry in reader["trace"]:
        if int(entry.get("round_index") or 0) in refuted:
            object_id = str((entry.get("args") or {}).get("object_id") or "")
            if object_id:
                return object_id
    return None


def _refuted_rounds(reader: dict) -> set[int]:
    """Rounds whose retrieval record says the row on the page was refuted by this snapshot."""
    return {int(r["round_index"]) for r in reader["recall"]["rounds"]
            if r.get("round_index") is not None and any(r["contradicted"])}


def _followed_rounds(reader: dict) -> set[int]:
    return {int(e["round_index"]) for e in reader["trace"]
            if e.get("memory_followed") and e.get("round_index") is not None}


def _stale_governing_rounds(reader: dict) -> list[int]:
    """Rounds where the page was already refuted *and* still set the order.

    The intersection is the whole content of §11's `stale memory usage` row: a recall the agent was
    shown, the world had contradicted, and the policy ranked its next action by anyway.
    """
    return sorted(_followed_rounds(reader) & _refuted_rounds(reader))


def _flag_matches_the_ranking(reader: dict) -> bool:
    """Does `memory_followed` mean what `policy.py` computes it from?

    The flag is set when the row that won was one the page ranked, so recomputing it from the two
    recorded fields — the round's `args.object_id` and its `memory_order` — must agree everywhere.
    It is a consistency test on the instrument, not a claim about the world, and it is what the p4
    finding leaves behind in the artifact.
    """
    for entry in reader["trace"]:
        chosen = str((entry.get("args") or {}).get("object_id") or "")
        ranked = bool(chosen) and chosen in (entry.get("memory_order") or [])
        if bool(entry.get("memory_followed")) != ranked:
            return False
    return True


def _followed_with_the_control(reader: dict, control: dict) -> list[int]:
    """Rounds where the memory says it was followed and the no-memory arm chose the same object.

    Reported, never pre-registered: how much of `memory_followed` is coincidence is a property of
    the pair, and turning it into an expectation would be tuning a claim to a result.
    """
    same = {int(e["round_index"]): str((e.get("args") or {}).get("object_id") or "")
            for e in control["trace"] if e.get("round_index") is not None}
    return sorted(int(e["round_index"]) for e in reader["trace"]
                  if e.get("memory_followed") and e.get("round_index") is not None
                  and same.get(int(e["round_index"]))
                  == str((e.get("args") or {}).get("object_id") or ""))


def compare_pair(root: str, pair: dict) -> dict:
    """The pair's verdict: every pre-registered field, measured in both arms.

    Each check stays a {expected, measured} pair rather than folding into one boolean, because a pair
    that half-agrees is a finding and a pair that reports "mismatch" is not.
    """
    treatment = read_pair(root, pair, EM_TREATMENT_ARM)
    control = read_pair(root, pair, EM_CONTROL_ARM)
    expected, reader_t, reader_c = (pair["expectation"], treatment["reader"], control["reader"])
    control_pick = expected["control_first_pick"] or reader_c["plan_first_object"]
    checks = {
        "control_follows_the_plan_order": {
            "expected": True, "measured": reader_c["first_pick"] == reader_c["plan_first_object"]},
        "control_first_pick": {"expected": control_pick, "measured": reader_c["first_pick"]},
        "treatment_first_pick_round1": {
            "expected": expected["treatment_first_pick_round1"] or control_pick,
            "measured": reader_t["first_pick"]},
        "treatment_first_pick_after_guard": {
            "expected": expected["treatment_first_pick_after_guard"] or control_pick,
            "measured": _first_pick_after_guard(reader_t)},
        "declined_when_measured": {
            "expected": sorted(expected["declined_when_measured"]),
            "measured": sorted(reader_t["declines"])},
        "rows_per_round": {
            "expected": expected["rows_per_round"],
            "measured": max((r["rows"] for r in reader_t["recall"]["rounds"]), default=0)},
        "guard_blind_at_round1": {
            "expected": True, "measured": not reader_t["recall"]["first_round_refuted"]},
        "stale_memory_governs": {
            "expected": expected["stale_memory_governs"],
            "measured": bool(_stale_governing_rounds(reader_t))},
        "memory_followed_is_the_ranking": {
            "expected": True, "measured": _flag_matches_the_ranking(reader_t)},
        "trajectory_changes": {
            "expected": expected["trajectory_changes"],
            "measured": reader_t["trajectory"] != reader_c["trajectory"]},
        "both_arms_complete_the_reader": {
            "expected": True,
            "measured": bool(reader_t["complete_success"]) and bool(reader_c["complete_success"])},
        "store_held_only_the_writer": {
            "expected": 1, "measured": int(reader_t["episodic"].get("store_size_before") or 0)}}
    return {"pair_id": pair["pair_id"], "condition": pair["condition"],
            "expectation": expected, "checks": checks,
            "reported": {
                "refuted_rounds": sorted(_refuted_rounds(reader_t)),
                "followed_rounds": sorted(_followed_rounds(reader_t)),
                "stale_governing_rounds": _stale_governing_rounds(reader_t),
                "followed_while_control_chose_the_same": _followed_with_the_control(reader_t,
                                                                                    reader_c),
                "control_rounds": reader_c["decision_rounds"],
                "treatment_rounds": reader_t["decision_rounds"]},
            "ok": all(c["expected"] == c["measured"] for c in checks.values()),
            "treatment": treatment, "control": control}


def measure_episodic_pairs(root: str, *, pairs: Optional[list[dict]] = None) -> dict:
    """Both arms of every pair, the pre-registered fields, and where the two trajectories split.

    A root that ran three pairs measures three and says the fourth is absent. Silently dropping it
    would be the wrong failure: the artifact names what it did not run, so a table with a row missing
    is a table that admits the row is missing.

    The seat is carried through. A model seat and a rule seat are not two readings of one
    experiment: on the rule seat the reader is `MemoryPolicy` and the two arms differ *only* in
    memory, while on a model seat the reader is a stochastic model and the arms also differ by the
    draw. Pooling or comparing the two without naming the seat would attribute the difference to
    memory. So `planner` and `policy` are part of the result, not incidental metadata.
    """
    with open(os.path.join(root, "pairs_run.json"), encoding="utf-8") as f:
        ledger = json.load(f)
    # a corrupt ledger is a refusal, not a smaller table — see `check_unreconciled`
    check_unreconciled(root)
    rows_by_key = {(row["pair_id"], row["arm"]): row for row in ledger["batches"]}
    # A manifest row is not a run: a pair-arm whose role produced no episode is absent, and the
    # reason is named. Counting rows instead would measure a pair whose control reader never
    # started as if it had, and would raise on it rather than report it.
    ran = {k for k, row in rows_by_key.items() if pair_arm_ran(row)}
    wanted = pairs if pairs is not None else EM_PAIRS
    out = {"kind": "episodic_pairs_measured", "root": root, "set": EM_SET_NAME,
           "frozen_manifest": EM_FROZEN_PATH,
           "planner": ledger.get("planner"), "policy": ledger.get("policy"),
           "model_config": ledger.get("model_config"),
           "seat_note": ("rule seat: the reader is MemoryPolicy, so the two arms differ only in "
                         "memory" if ledger.get("planner") == "rule" else
                         "model seat: the reader is the model, so the two arms differ in memory "
                         "AND in the draw — a within-pair difference is not attributable to "
                         "memory alone"),
           "pairs": [], "absent": []}
    for pair in wanted:
        missing = [arm for arm in EM_ARMS if (pair["pair_id"], arm) not in ran]
        if missing:
            out["absent"].append({
                "pair_id": pair["pair_id"],
                "arms_missing": missing,
                "why": [why_pair_arm_missing(rows_by_key.get((pair["pair_id"], arm)))
                        for arm in missing],
            })
            continue
        verdict = compare_pair(root, pair)
        treated, control = verdict["treatment"]["reader"], verdict["control"]["reader"]
        out["pairs"].append({
            "pair_id": verdict["pair_id"], "condition": verdict["condition"],
            "ok": verdict["ok"], "checks": verdict["checks"],
            "treatment_trajectory": treated["trajectory"],
            "control_trajectory": control["trajectory"],
            "divergence_first_index": next(
                (i for i, (a, b) in enumerate(zip(treated["trajectory"], control["trajectory"]))
                 if a != b), None),
            "treatment_rounds": treated["decision_rounds"],
            "control_rounds": control["decision_rounds"],
            "treatment_recall": treated["recall"], "declines": treated["declines"],
            "reported": verdict["reported"]})
    with open(os.path.join(root, "pairs_measured.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    return out


def print_pairs(root: str) -> dict:
    measured = measure_episodic_pairs(root)
    head = (f"{'pair':7s} {'condition':28s} {'ok':>3s} {'ctrl':>10s} {'treat':>10s} "
            f"{'after gd':>9s} {'decl':>5s} {'rnd c/t':>8s} {'split':>6s}")
    print(head)
    print("-" * len(head))
    for pair in measured["pairs"]:
        c = pair["checks"]
        print(f"{pair['pair_id']:7s} {pair['condition']:28s} "
              f"{'yes' if pair['ok'] else 'NO':>3s} "
              f"{str(c['control_first_pick']['measured']):>10s} "
              f"{str(c['treatment_first_pick_round1']['measured']):>10s} "
              f"{str(c['treatment_first_pick_after_guard']['measured']):>9s} "
              f"{len(c['declined_when_measured']['measured']):>5d} "
              f"{str(pair['control_rounds']) + '/' + str(pair['treatment_rounds']):>8s} "
              f"{str(pair['divergence_first_index']):>6s}")
        r = pair["reported"]
        print(f"        refuted={r['refuted_rounds']} followed={r['followed_rounds']} "
              f"stale-and-governing={r['stale_governing_rounds']} "
              f"followed-where-control-agreed={r['followed_while_control_chose_the_same']}")
    for pair in measured["pairs"]:
        for name, check in sorted(pair["checks"].items()):
            if check["expected"] != check["measured"]:
                print(f"  MISMATCH {pair['pair_id']}.{name}: expected {check['expected']!r} "
                      f"measured {check['measured']!r}")
    for absent in measured["absent"]:
        print(f"  NOT RUN  {absent['pair_id']}: arms missing {absent['arms_missing']}")
    return measured


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if not argv or argv[0] not in ("--run", "--measure"):
        print(__doc__)
        raise SystemExit(1)
    target = argv[1]
    if argv[0] == "--run":
        info = run_episodic_pairs(target)
        print(json.dumps({k: info[k] for k in ("kind", "root", "arms", "set")},
                         ensure_ascii=False))
        print(f"batches: {len(info['batches'])}")
    print_pairs(target)