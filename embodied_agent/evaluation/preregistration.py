"""P3 freeze: one pre-registration the runner, the report and a test can all check.

SPEC 9 P3 asks to freeze 代码、任务、seed、事件、prompt、预算、模型配置和统计口径 *before*
running the pre-registered batch. A freeze is only a claim about a file until
something refuses to proceed when the file stops matching reality, so this module
has three jobs:

* **derive** the frozen values from the code that will act on them. Nothing here is
  retyped from a config file: budgets are `Budgets`' field defaults, sampling is
  `DeepSeekAdapter.__init__`'s defaults, prompt identity is the sha256 of the
  strings actually sent, the run matrix is `build_set("formal")`, the bootstrap
  numbers are `report.py`'s constants. A frozen number that was copied by hand
  could only ever disagree with the code at run time.
* **hash the rules and nothing else.** `rules_sha256` covers every block that
  changes what gets measured or how it is read; `provenance` (commit, dirty-diff
  hash, dependency versions, timestamp) is recorded beside it but outside it. A
  docs-only commit therefore cannot invalidate the rules, while every run still
  pairs a rules hash with the code identity that executed it — which is the only
  honest direction of that dependency.
* **make drift loud.** `check_prereg` re-derives and names the fields that moved
  instead of printing two hashes; `evaluation.run` refuses a batch whose matrix or
  sampling contradicts the file it was told to honour; and the report refuses to
  read gates out of a file whose rules hash differs from the one the run recorded,
  so lowering a threshold after seeing the numbers produces an error, not a
  smaller bar (SPEC 12.2: 正式结果出现后不能调低门槛再宣称原验收通过).

Credentials are not part of a pre-registration: the model config file is hashed by
its bytes and the key is named by its environment variable (`configs/models/
deepseek.yaml: api_key_env`), never by value (SPEC 7).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re

from ..adapters.deepseek import (DECISION_SYSTEM, DECISION_USER, GOAL_SYSTEM, GOAL_USER,
                                 PLAN_SYSTEM, PLAN_USER, DeepSeekAdapter, DeepSeekPlanner)
from ..core.contracts import Budgets
from ..core.events import _dependency_versions, git_state
from ..core.runtime import HISTORY_CAP
from . import report as report_mod
from .blind_review import RUBRIC_PATH, load_rubric, rubric_sha256
from .tasks import (FROZEN_PATH, PROTOCOL_HARNESS, STATE_PAIR_SPECS, build_set,
                    frozen_manifest)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PREREG_PATH = os.path.join(REPO_ROOT, "configs", "experiment", "p3_preregistration_v1.json")
MODEL_CONFIG_PATH = os.path.join(REPO_ROOT, "configs", "models", "deepseek.yaml")
SPEC_PATH = os.path.join(REPO_ROOT, "Continuous-Decision-Embodied-Agent-SPEC-v0.1.md")
PREREG_ID = "p3-formal-v1"
FORMAL_SET = "formal"
ARMS = ("A", "B", "C")
REPEATS = 3


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(path: str) -> str:
    """The hash of the bytes on disk, not of a re-serialisation: the thing being
    bound is the text a reader (or a model) would actually see."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _defaults_of(func, names: tuple[str, ...]) -> dict:
    params = inspect.signature(func).parameters
    return {n: params[n].default for n in names}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)


# ------------------------------------------------------------------ rules -----


def _spec_block() -> dict:
    return {
        "path": os.path.relpath(SPEC_PATH, REPO_ROOT),
        "sha256": _sha_file(SPEC_PATH),
        "sections_frozen_by_this_file": ["9 P3", "10.1", "10.2", "10.3", "11.1", "11.2",
                                         "11.3", "11.4", "12.1", "12.2"],
        "note": "the SPEC is the only source of terms; this file records the numbers it "
                "left to be frozen, and where a value is the code's rather than the "
                "document's, the code is named as the source",
    }


def _run_matrix() -> dict:
    formal = build_set(FORMAL_SET)
    return {
        "set": FORMAL_SET,
        "cases": len(formal),
        "case_ids": [c.task_id for c in formal],
        "seeds": {c.task_id: c.seed for c in formal},
        "subset_counts": {s: sum(1 for c in formal if c.subset == s)
                          for s in sorted({c.subset for c in formal})},
        "modes": list(ARMS),
        "repeats": REPEATS,
        "episodes": len(formal) * len(ARMS) * REPEATS,
        "shared_goal_parses": len(formal) * REPEATS,
        "planner": "deepseek",
        "execution_order": "modes interleaved inside each (case, repeat): a provider "
                           "that changes behaviour mid-batch perturbs all three arms of "
                           "a pair equally (SPEC 11.2)",
        "seed_policy": "repeat n reuses the case seed, so the scene is fixed; the model "
                       "run is still an independent sample and is recorded by its own "
                       "episode id and completion id (SPEC 11.1)",
        "per_episode_http_cap": max(c.budgets.max_http_requests for c in formal),
        "per_episode_budgets": "the per-case `Budgets` in the frozen task list, not a "
                               "number restated here; the http cap is the widest of them "
                               "and is the only one quoted as an upper bound",
        "batch_http_upper_bound": (len(formal) * len(ARMS) * REPEATS
                                   * max(c.budgets.max_http_requests for c in formal)),
        "budget_estimate": {
            "requests": "1400-1600", "prompt_tokens_m": "6-7", "wall_clock_h": "1.5-2.5",
            "status": "an estimate for planning only; the limit that binds is "
                      "per_episode_http_cap, and no estimate enters a denominator",
        },
        "pilot": {
            "set": "smoke", "modes": list(ARMS), "repeats": 1,
            "purpose": "measures the cost of one batch and the request count per "
                       "episode before the formal run; its episodes are never folded "
                       "into a formal rate",
            "enforced_by_prereg": False,
            "why": "`--prereg` refuses any matrix but the formal one, so a pilot cannot "
                   "run under the pre-registration and be mistaken for part of it; the "
                   "shape is recorded here so the pilot that was run is checkable",
        },
    }


def _diagnostics() -> dict:
    protocol = build_set("protocol")
    return {
        "state_utilization": {
            "pairs": len(STATE_PAIR_SPECS),
            "pair_ids": [p["pair_id"] for p in STATE_PAIR_SPECS],
            "differences": sorted({p["difference"] for p in STATE_PAIR_SPECS}),
            "requests_per_arm": len(STATE_PAIR_SPECS) * 2,
            "acceptable_sets_authored_from": "public preconditions and task constraints, "
                                             "written before any model answered "
                                             "(SPEC 11.3: 不以单一动作字符串作答案)",
            "excluded_from_success_denominator": True,
        },
        "protocol_cases_in_the_scene": {
            "set": "protocol", "cases": len(protocol),
            "case_ids": [c.task_id for c in protocol],
        },
        "protocol_cases_owned_by_tests": {
            "harness_entries": len(PROTOCOL_HARNESS), "names": [h["name"] for h in PROTOCOL_HARNESS]},
        "boundary_cases_total": len(protocol) + len(PROTOCOL_HARNESS),
        "note": "SPEC 11.1 asks for at least 12 boundary cases that do not enter the "
                "success denominator; both halves are counted, and neither is reported "
                "as a task success rate",
    }


def _task_list() -> dict:
    live = frozen_manifest()
    return {
        "path": os.path.relpath(FROZEN_PATH, REPO_ROOT),
        "sha256": live["sha256"],
        "sets": {k: len(v) for k, v in live["sets"].items()},
        "what_is_hashed": "task_id, subset, seed, n_objects, utterance, objects, targets, "
                          "budgets, verify, events, expected, probe, hidden EvalSpec, the "
                          "protocol harness, the state-pair specs and the physics constants",
        "constants": live["constants"],
        "events_note": "environment events are inside that hash: a perturbation changed "
                       "after a result was produced is drift, not tuning (SPEC 10.3)",
    }


def _prompts() -> dict:
    versions = _defaults_of(DeepSeekPlanner.__init__,
                            ("goal_prompt", "decision_prompt", "plan_prompt"))
    texts = {"goal": (GOAL_SYSTEM, GOAL_USER), "decision": (DECISION_SYSTEM, DECISION_USER),
             "plan": (PLAN_SYSTEM, PLAN_USER)}
    return {
        "versions": versions,
        "sha256_by_kind": {k: _sha_text(s + u) for k, (s, u) in texts.items()},
        "history_cap": HISTORY_CAP,
        "feedback_depth": _defaults_of(DeepSeekPlanner.__init__, ("feedback_depth",))["feedback_depth"],
        "source_of_the_hashed_text": "embodied_agent/adapters/deepseek.py",
        "note": "the version string is what a manifest labels a prompt with; the hash is "
                "what proves the labelled text is still the text that gets sent",
    }


def _sampling() -> dict:
    defaults = _defaults_of(DeepSeekAdapter.__init__,
                            ("model", "base_url", "temperature", "max_tokens", "timeout_s",
                             "max_retries"))
    cfg_path = os.path.relpath(MODEL_CONFIG_PATH, REPO_ROOT)
    return {
        "provider": "deepseek",
        "declared_in": cfg_path,
        "declared_sha256": _sha_file(MODEL_CONFIG_PATH),
        "defaults_in_code": defaults,
        "effective_source": "DeepSeekAdapter.sampling, recorded per run as model.sampling; "
                            "the runner compares the live object against this block "
                            "(SPEC 11.2: 固定 provider、实际模型标识、采样参数)",
        "credential_policy": "api_key_env names the variable; the value is in the "
                             "environment or the gitignored .env and appears in no "
                             "config, snapshot, prompt or log (SPEC 7)",
        "env_overridable": ["DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"],
        "pricing_configured": _pricing_configured(),
        "api_cost_estimate": None,
        "pricing_note": "no rate is asserted for a provider whose price was not read from "
                        "a source in this repository; requests and tokens are reported "
                        "instead of an invented cost, and `api_cost_estimate` stays null "
                        "until a price is configured (SPEC 11.4 成本指标)",
    }


def _pricing_configured() -> bool:
    """Whether the model config actually carries a rate, read from its text so a
    commented-out example cannot be mistaken for a configured price."""
    with open(MODEL_CONFIG_PATH, encoding="utf-8") as f:
        return any(ln.strip().startswith("pricing_usd_per_mtok:")
                   for ln in f if not ln.lstrip().startswith("#"))


def _budgets() -> dict:
    """`Budgets`' own defaults, with the two numbers that are protection rather than
    budget named separately so a reader can tell them apart."""
    fields = {name: f.default for name, f in Budgets.model_fields.items()}
    return {
        "declared_in": "embodied_agent/core/contracts.py::Budgets",
        "per_case_overrides": "the frozen task list's budget_profile, hashed with the rest",
        "defaults": fields,
        "shared_across_arms": True,
        "protections": {
            "max_identical_invalid_attempts": fields["max_identical_invalid_attempts"],
            "max_semantic_repairs": fields["max_semantic_repairs"],
            "per_action_extra_repeats_mode_A": fields["per_action_extra_repeats"],
            "note": "mode A's bounded repeat of the identical skill/object/candidate is "
                    "the pre-registered baseline rule (SPEC 11.2), not a strategy: B uses "
                    "the same protection and the same attempt ceiling, and only differs "
                    "in who decides to retry",
        },
    }


def _arms() -> dict:
    return {
        "A": {"name": "one-shot plan", "decider": "one plan request, then execution only",
              "rule": "on verification failure, while the precondition still holds, repeat "
                      "the identical skill/object/candidate at most "
                      "per_action_extra_repeats times; terminate when a new action would "
                      "be needed, the budget is spent, or the repeats fail",
              "no_replanning": True},
        "B": {"name": "continuous decision", "decider": "the same model, one decision per "
                                                        "skill boundary, on the freshest context"},
        "C": {"name": "ablated feedback", "decider": "as B, on a context with the detailed "
                                                     "execution diagnostics, attempt history "
                                                     "and verification summary removed",
              "still_keeps": "execution verification, safety protections and a WorldState "
                             "that can still reveal effects; it is not a no-feedback arm"},
        "identical_across_arms": "goal source and its shared parse, skill catalogue, "
                                 "executor, verifier, budgets, candidate registry, scene, "
                                 "evaluator and scoring",
        "not_an_arm": "the legacy full-plan+replan reference path stays available in code "
                      "and may not be labelled one-shot planning (SPEC 11.2)",
    }


def _statistics() -> dict:
    return {
        "primary_metric": "independent complete-task success rate judged by "
                          "IndependentEvaluator against the hidden EvalSpec",
        "aggregation": "per case over its repeats first, then paired differences per case, "
                       "bootstrapped over cases; three rollouts of one configuration are "
                       "not three tasks (SPEC 11.1, 11.4)",
        "bootstrap": {"replicates": report_mod.BOOTSTRAP_REPLICATES,
                      "seed": report_mod.BOOTSTRAP_SEED,
                      "cluster": "case", "interval": "percentile 2.5/97.5",
                      "source_of_truth": "embodied_agent/evaluation/report.py",
                      "note": "the seed is fixed so the interval of a frozen report is "
                              "reproducible rather than re-drawn on every render"},
        "wilson": {"z": inspect.signature(report_mod.wilson_interval).parameters["z"].default,
                   "role": "descriptive only; it never carries the claim"},
        "claim_rule": "only a paired difference whose 95% lower bound is above zero is "
                      "worded as a supported gain; a point estimate that clears the bar "
                      "with an interval crossing zero is worded 初步收益，证据不足",
        "denominator": "every planned run: report.planned_runs",
        "infra_outcomes_stay_in_the_denominator": list(report_mod.INFRA_OUTCOMES),
        "exclusions_permitted": "none after the fact; an API error, a simulation crash, an "
                                "unfired declared event and a wrong goal parse are "
                                "reported as themselves (SPEC 11.4: 合法性排除规则必须事先"
                                "冻结；不因任务难或事件未触发而事后删样本)",
        "layering": "clean / state_change / execution_deviation, and by object count, each "
                    "with its own numerator and denominator",
        "perturbed_subset_for_gate_G3_G4": ["state_change", "execution_deviation"],
        "cost_metrics": ["skill_calls", "decision_rounds", "http_requests", "prompt_tokens",
                         "completion_tokens", "wall_clock_s", "sim_time_s",
                         "api_errors", "infrastructure_errors", "provider_latency"],
        "behaviour_metrics": ["state_fork_action_fit", "repeated_failure_without_new_evidence",
                              "redundant_rework_of_satisfied_goals",
                              "disturbed_targets_reincorporated", "illegal_decisions",
                              "rejections", "false_finishes"],
        "needs_human_reading": ["place_of_already_satisfied",
                               "fork_answer_outside_both_label_sets"],
    }


def _gates() -> dict:
    """SPEC 12.2, as data the report reads instead of numbers it can invent."""
    return {
        "declared_in": "SPEC 12.2 (本初稿建议值，正式测试前冻结)",
        "status": "阶段目标，不是已达到的结果，也不是总体能力保证",
        "items": [
            {"id": "G1", "metric": "b_clean_success_point_estimate", "op": ">=",
             "threshold": 0.80, "unit": "rate"},
            {"id": "G2", "metric": "a_minus_b_clean_success", "op": "<=", "threshold": 0.05,
             "unit": "rate difference"},
            {"id": "G3", "metric": "b_perturbed_success_point_estimate", "op": ">=",
             "threshold": 0.70, "unit": "rate"},
            {"id": "G4", "metric": "b_minus_a_perturbed_paired_mean", "op": ">=",
             "threshold": 0.15, "unit": "paired mean difference of per-case rates"},
            {"id": "G5", "metric": "state_pairs_with_both_arms_fitting", "op": ">=",
             "threshold": 10, "unit": f"of {len(STATE_PAIR_SPECS)} pairs"},
        ],
        "also_required_to_claim": {
            "G4_interval_lower_bound": "> 0 (paired, configuration-clustered bootstrap)",
            "cost_reported": "an improvement in success bought with a large cost increase "
                             "is reported as such, not as a capability gain (SPEC 12.2)",
        },
        "if_engineering_passes_and_gates_do_not": "the conclusion is 持续决策闭环已实现，"
                                                 "预期能力增益未证实, decided by the error "
                                                 "distribution — not by widening the module "
                                                 "count until the number moves",
        "may_not_be_relaxed_after_results": True,
    }


def _blind_review_block() -> dict:
    rubric = load_rubric()
    return {
        "path": os.path.relpath(RUBRIC_PATH, REPO_ROOT),
        "rubric_id": rubric["rubric_id"],
        "sha256": rubric_sha256(),
        "registered_before_any_p3_run": rubric.get("registered_before_any_p3_run"),
        "reviewers": rubric["reviewers"]["count"],
        "sampling": rubric["sampling"],
        "kinds": [i["kind"] for i in rubric["items"]],
        "adverse_values": {i["kind"]: i["adverse_value"] for i in rubric["items"]},
        "prohibitions": list(rubric["prohibitions"]),
        "published_rates": ["adverse_rate_strict", "adverse_rate_counting_divided_as_adverse"],
        "empty_is_a_result": "a batch with no contested items reports an empty blind review; "
                             "predicates are not loosened to manufacture items",
    }


def _invariants() -> list[dict]:
    """Claims about the code that a freeze has to keep being true.

    Each is a substring of a named source file's *current* text, so `check_prereg`
    fails when one stops holding — which is the difference between freezing a
    number and freezing the behaviour that produced it. Needle and file are stated
    rather than implied so the reader can check the claim without reading Python.
    """
    return [
        {"id": "I1", "claim": "every request asks for a JSON object and never streams",
         "file": "embodied_agent/adapters/deepseek.py",
         "check": {'function': 'DeepSeekAdapter.chat', 'needle': '"response_format": {"type": "json_object"}'}},
        {"id": "I2", "claim": "a provider error body is never read into a message, so it "
                              "cannot carry the key into a log",
         "file": "embodied_agent/adapters/deepseek.py",
         "check": {'function': 'DeepSeekAdapter._post', 'needle': "(body not read)"}},
        {"id": "I3", "claim": "run manifests record no credentials and no environment",
         "file": "embodied_agent/core/events.py",
         "check": {'function': 'write_manifest',
                   'needle': "not recorded (SPEC 7: no credentials or full env)"}},
        {"id": "I4", "claim": "the hidden EvalSpec is read only by the evaluator, never by "
                              "a runtime that builds a decision context",
         "file": "embodied_agent/core/runtime.py",
         "check": {'needle': "eval_spec", 'absent': True}},
        {"id": "I5", "claim": "a verdict row is accepted only from a human reviewer, so a "
                              "model cannot grade the policy it is part of (SPEC 11.4)",
         "file": "embodied_agent/evaluation/blind_review.py",
         "check": {'function': 'validate_verdicts',
                   'needle': 'row.get("reviewer_kind") != "human"'}},
        {"id": "I6", "claim": "the mode-A baseline's only permitted reaction is the frozen "
                              "bounded repeat of the identical action; it never adds or "
                              "reorders a step (SPEC 11.2)",
         "file": "embodied_agent/core/planner.py",
         "check": {'function': 'OneShotPlanSource._settle_pending',
                   'needle': "self.per_action_extra_repeats"}},
        {"id": "I7", "claim": "the shared goal parse is billed into every arm of a pair, so "
                              "no arm gets a cheaper budget than the run that paid for it",
         "file": "embodied_agent/core/runtime.py",
         "check": {'function': 'Runtime.run_episode',
                   'needle': 'prologue.get("http_requests")'}},
    ]


def _change_policy() -> dict:
    return {
        "hashed": "every block of this file except `provenance`",
        "recorded_but_not_hashed": ["code identity (commit, dirty, dirty_diff_sha256)",
                                    "dependency versions", "generated_at"],
        "why": "a commit that only touches docs must not invalidate the rules, and a rules "
               "change must be visible rather than silent; each run pairs the rules hash it "
               "was started under with its own code identity",
        "to_amend": "`cli prereg --out <new path>` then `--amend` prints the hash it "
                    "replaced; the old file stays readable, and a run recorded under the "
                    "old hash stops being gradeable against the new one",
        "never": ["lower a gate after results exist", "drop a sample from a denominator",
                  "change a prompt, budget, event or seed mid-batch",
                  "let a model grade the policy it is part of",
                  "swap the provider or model inside a batch without re-freezing"],
        "self_certification_limit": "this file cannot prove it was written before the "
                                    "results; the rules hash inside every run manifest, and "
                                    "the git history of this path, are that evidence",
    }


def _rules() -> dict:
    return {
        "prereg_id": PREREG_ID,
        "spec": _spec_block(),
        "run_matrix": _run_matrix(),
        "diagnostics": _diagnostics(),
        "task_list": _task_list(),
        "prompts": _prompts(),
        "sampling": _sampling(),
        "budgets": _budgets(),
        "arms": _arms(),
        "statistics": _statistics(),
        "gates": _gates(),
        "blind_review": _blind_review_block(),
        "code_invariants": _invariants(),
        "change_policy": _change_policy(),
    }


def _provenance() -> dict:
    import platform
    import sys

    return {
        "generated_at": None,  # filled by dump_prereg: a timestamp is provenance, not a rule
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": _dependency_versions(),
        "code": git_state(),
        "environment_variables": "not recorded (SPEC 7: no credentials or full env)",
        "dirty_note": "an uncommitted tree is recorded, not hidden: what is frozen is a "
                      "commit identity plus a diff hash, not a claim of a clean checkout",
    }


def rules_sha256(rules: dict) -> str:
    return _sha_text(_json(rules))


def preregistration() -> dict:
    """The live pre-registration, derived from the code as it stands now."""
    rules = _rules()
    prov = _provenance()
    return {"schema_version": "1", "rules": rules, "provenance": prov,
            "rules_sha256": rules_sha256(rules)}


def dump_prereg(path: str = PREREG_PATH, previous_rules_sha256: str | None = None) -> str:
    """Write the freeze. `previous_rules_sha256` is only ever passed by an explicit
    amendment, and it lands in `provenance` — outside the hash — so the file itself
    carries the lineage of which rules it replaced."""
    from datetime import datetime, timezone

    payload = preregistration()
    payload["provenance"]["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if previous_rules_sha256:
        payload["provenance"]["amended_from"] = previous_rules_sha256
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    return path


# ------------------------------------------------------------------ checks ----


def load_prereg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _flat(obj, prefix: str = "") -> dict[str, str]:
    """Leaf paths of a payload, so a refusal can name the field that moved."""
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flat(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = str(obj)
    return out


def _drift(frozen: dict, live: dict, limit: int = 6) -> list[str]:
    a, b = _flat(frozen), _flat(live)
    moved = []
    for key in sorted(set(a) | set(b)):
        if a.get(key, "<absent>") != b.get(key, "<absent>"):
            moved.append(f"{key}: frozen {a.get(key, '<absent>')} != live "
                         f"{b.get(key, '<absent>')}")
    return moved[:limit] + ([f"(+{len(moved) - limit} more)"] if len(moved) > limit else [])


def check_invariants(rules: dict | None = None) -> list[str]:
    """Check each frozen claim against the source text it names; the problems back."""
    problems = []
    for item in (rules or _rules())["code_invariants"]:
        path = os.path.join(REPO_ROOT, item["file"])
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            problems.append(f"{item['id']}: cannot read {item['file']}: {e}")
            continue
        needle = item["check"]["needle"]
        where = item["check"].get("function")
        if where:
            body = _function_source(source, where)
            if body is None:
                problems.append(f"{item['id']}: {where} not found in {item['file']}")
                continue
        else:
            body = source
        present = needle in body
        if present == bool(item["check"].get("absent", False)):
            problems.append(f"{item['id']} no longer holds: {item['claim']} "
                            f"({'needle present' if present else 'needle absent'} in "
                            f"{item['file']})")
    return problems


def _function_source(source: str, dotted: str) -> str | None:
    """The text of a function or method, by indentation — deliberately without an
    AST parse, so the check reads the same bytes a reviewer would open in an editor."""
    name = dotted.split(".")[-1]
    match = re.search(rf"^ *def {re.escape(name)}\(", source, re.M)
    if not match:
        return None
    rest = source[match.start():]
    lines = rest.splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    out = [lines[0]]
    for line in lines[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def check_prereg(path: str) -> tuple[bool, str]:
    """(ok, message) for a frozen pre-registration against the code now.

    Two claims, checked in this order: the file must agree with its own rules hash
    (the hash is a field of the file it describes, so a hand-edit that leaves it
    self-inconsistent is caught first), and that hash must agree with what the code
    would freeze today. Every invariant is checked too, because a rule about the
    code can break without any frozen value moving."""
    if not os.path.exists(path):
        return False, f"no pre-registration file at {path}"
    recorded = load_prereg(path)
    embedded = str(recorded.get("rules_sha256", ""))
    if rules_sha256(recorded.get("rules") or {}) != embedded:
        return False, (f"{path} is self-inconsistent: its rules hash to "
                       f"{rules_sha256(recorded.get('rules') or {})[:12]}, not the recorded "
                       f"{embedded[:12]}")
    live = preregistration()
    broken = check_invariants(recorded.get("rules") or {})
    if broken:
        return False, ("a frozen claim about the code no longer holds: " + " | ".join(broken))
    if embedded == live["rules_sha256"]:
        return True, embedded
    return False, ("the code has drifted from the pre-registration "
                   f"({embedded[:12]} != live {live['rules_sha256'][:12]}): "
                   + " | ".join(_drift(recorded.get("rules") or {}, live["rules"])))


# --------------------------------------------------------------- enforcement --


def matrix_mismatch(rules: dict, *, set_name: str, modes, repeats: int, planner_kind: str,
                    case_ids: list[str] | None, limit: int | None,
                    sampling: dict | None) -> list[str]:
    """What a runner can contradict about a pre-registration, in one call.

    Each reason is a refusal, so `--prereg` cannot quietly become a flag that only
    records: a subset of the frozen cases, a fourth repeat, a different arm set, a
    rule planner in place of the model, or a temperature edited in the yaml all
    stop the batch before the first request.
    """
    m = rules["run_matrix"]
    reasons = []
    if set_name != m["set"]:
        reasons.append(f"the pre-registered set is {m['set']!r}, not {set_name!r}")
    if list(modes) != list(m["modes"]):
        reasons.append(f"the pre-registered arms are {m['modes']}, got {list(modes)}")
    if repeats != m["repeats"]:
        reasons.append(f"the pre-registered repeats are {m['repeats']}, got {repeats}")
    if planner_kind != m["planner"]:
        reasons.append(f"the pre-registered planner is {m['planner']!r}, got {planner_kind!r}")
    if limit is not None or case_ids:
        reasons.append("a pre-registered batch runs all of its cases: --cases/--limit would "
                       "change the denominator after the fact")
    if sampling is not None:
        frozen_sampling = {k: v for k, v in rules["sampling"]["defaults_in_code"].items()}
        live = {k: sampling[k] for k in frozen_sampling}
        if live != frozen_sampling:
            reasons.append("the model the adapter will send differs from the frozen one: "
                           + " | ".join(_drift(frozen_sampling, live)))
    return reasons
