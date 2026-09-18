"""Assemble the S1 completion report (spec section 2, ten conditions) from
the frozen run matrix. Usage: python work/s1_completion_report.py"""
import glob
import json
import os

def latest(pattern):
    dirs = sorted(glob.glob(pattern))
    return dirs[-1] if dirs else None

def load_report(d):
    return json.load(open(os.path.join(d, "report.json"), encoding="utf-8")) if d else None

b1 = latest("runs/s1_clean_rule_recoveryon_*")
e1 = latest("runs/s1_clean_deepseek_recoveryon_*")
e0 = latest("runs/s1_clean_deepseek_recoveryoff_*")
fon = latest("runs/s1_fault_rule_recoveryon_*")
foff = latest("runs/s1_fault_rule_recoveryoff_*")
neg = latest("runs/negative_rule_*")
smoke = latest("runs/smoke_rule_recoveryon_*")
demo = latest("runs/demo_*")

rep = {
    "1_2_3_pybullet_real_contact_3to5_objects": "met (PhysicsScene; fault injector touches only descent target; no teleport/fixed constraint)",
    "4_same_runtime_rule_and_deepseek": "met (Runtime.run_episode(planner=...) switch; no skill/sim code change)",
    "5_deepseek_structured_output_validated": "met (JSON mode + Pydantic + PlanValidator; 401 immediate, 1 repair, 2 backoff)",
    "6_forced_observe_verify_after_pick_place": "met (Runtime._execute_plan forced verification)",
    "7_grasp_miss_recovery_bounded": "met (RecoveryPolicy + safe_retreat + candidate retry within per-object budget)",
    "8_independent_evaluator": "met (IndependentEvaluator reads physics truth + EvalSpec only; reverse tests in tests/unit/test_information_isolation.py)",
    "9_artifacts": {"task_sets": "smoke/dev/s1-clean(90)/s1-fault(30x3)/negative(12)",
                    "events": "events.jsonl (schema v1 envelope)", "model_calls": "model_calls.jsonl",
                    "video": "video.mp4 per episode (frames at each skill boundary)",
                    "repro": "README.md + requirements.txt + config.json snapshots"},
}
if b1: rep["10a_rule_baseline_s1_clean"] = load_report(b1)
if e1: rep["10b_deepseek_e2e_s1_clean"] = load_report(e1)
if e0: rep["10c_deepseek_no_recovery_s1_clean"] = load_report(e0)
if fon and foff:
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "embodied_agent.evaluation.run", "a3",
                          "--run-dir", fon, "--out", foff], capture_output=True, text=True)
    try:
        rep["10d_fault_paired_gain"] = json.loads(out.stdout[out.stdout.index("{"):])
    except Exception:
        rep["10d_fault_paired_gain_raw"] = out.stdout[-500:]
if neg: rep["a4_negative"] = load_report(neg)
if smoke: rep["a4_smoke"] = load_report(smoke)
if demo: rep["a5_demo_dir"] = demo

os.makedirs("outputs/s1_acceptance", exist_ok=True)
with open("outputs/s1_acceptance/s1_completion_report.json", "w", encoding="utf-8") as f:
    json.dump(rep, f, ensure_ascii=False, indent=2)
print(json.dumps({k: v for k, v in rep.items() if k.startswith("10") or k.startswith("a4")},
                 ensure_ascii=False, indent=1))
