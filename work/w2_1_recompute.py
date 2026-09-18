"""W2.1 task 3: independently recompute S1 metrics from raw episode summaries.

Deliberately does NOT reuse embodied_agent.evaluation.run summarize logic —
this is a cross-check of the reported numbers straight from summary.json files.

Read-only. Usage: python work/w2_1_recompute.py
"""
import glob
import json
import os
from collections import Counter, defaultdict

OFFICIAL = {
    "B1_clean_rule": "runs/s1_clean_rule_recoveryon_20260918_034252",
    "E1_clean_deepseek": "runs/s1_clean_deepseek_recoveryon_20260918_051112",
    "E0_clean_deepseek_norecov": "runs/s1_clean_deepseek_recoveryoff_20260918_043353",
    "B1_fault_rule": "runs/s1_fault_rule_recoveryon_20260918_052515",
    "B0_fault_rule_norecov": "runs/s1_fault_rule_recoveryoff_20260918_044811",
    "EF1_fault_deepseek": "runs/s1_fault_deepseek_recoveryon_20260918_053722",
    "EF0_fault_deepseek_norecov": "runs/s1_fault_deepseek_recoveryoff_20260918_045231",
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    ph = k / n
    denom = 1 + z * z / n
    center = (ph + z * z / (2 * n)) / denom
    half = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def load_episodes(run_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(run_dir, "*", "summary.json"))):
        s = json.load(open(f))
        s["_dir"] = os.path.basename(os.path.dirname(f))
        out.append(s)
    return out


def recompute(name, run_dir):
    eps = load_episodes(run_dir)
    n = len(eps)
    k = sum(1 for e in eps if e["independent_complete_success"])
    lo, hi = wilson(k, n)
    layers = defaultdict(lambda: [0, 0])
    fails = Counter()
    per_object = defaultdict(lambda: [0, 0])
    wall = []
    for e in eps:
        n_obj = e["objects_total"]
        layers[n_obj][0] += e["independent_complete_success"]
        layers[n_obj][1] += 1
        per_object[n_obj][0] += e["objects_completed"]
        per_object[n_obj][1] += n_obj
        wall.append(e["wall_time_s"])
        if not e["independent_complete_success"]:
            fails[e["failure_type"] or "VERIFY_FAILED"] += 1
    print(f"\n== {name} ({run_dir})")
    print(f"   episodes={n} 成功={k} 成功率={k / max(1, n):.3f} Wilson95=[{lo:.3f}, {hi:.3f}]")
    for n_obj in sorted(layers):
        kk, nn = layers[n_obj]
        print(f"   {n_obj} 物体: {kk}/{nn} = {kk / nn:.3f}")
    tot_c = sum(v[0] for v in per_object.values())
    tot_n = sum(v[1] for v in per_object.values())
    print(f"   每对象成功率: {tot_c}/{tot_n} = {tot_c / max(1, tot_n):.3f}")
    if fails:
        print(f"   失败分布: {dict(fails)}")
    print(f"   墙钟/集: 均值 {sum(wall) / len(wall):.1f}s, 最大 {max(wall):.1f}s")
    return eps, k, n


def fault_paired(dir_on, dir_off):
    eps_on = load_episodes(dir_on)
    eps_off = load_episodes(dir_off)
    # rule arms ran 3x per config; take per-config majority success
    def by_config(eps_list):
        # 每个 task_id 即一个冻结故障配置(固定 seed + 固定偏移);
        # runs>1 的重复执行按 task_id 聚合
        conf = defaultdict(list)
        for e in eps_list:
            conf[e["task_id"]].append(e["independent_complete_success"])
        return {c: (sum(v), len(v)) for c, v in conf.items()}

    def majority(t):
        k, n = t
        return k >= n / 2

    on_c, off_c = by_config(eps_on), by_config(eps_off)
    common = sorted(set(on_c) & set(off_c))
    wins = sum(1 for c in common if majority(on_c[c]) and not majority(off_c[c]))
    losses = sum(1 for c in common if not majority(on_c[c]) and majority(off_c[c]))
    ties = len(common) - wins - losses
    on_rate = sum(on_c[c][0] for c in common) / sum(on_c[c][1] for c in common)
    off_rate = sum(off_c[c][0] for c in common) / sum(off_c[c][1] for c in common)
    print(f"\n== fault 配对(rule arms, 30 配置 x 3 次确定性重复)")
    print(f"   配置数={len(common)} 恢复胜={wins} 恢复负={losses} 平={ties}")
    print(f"   恢复组 episode 成功率={sum(on_c[c][0] for c in common)}/{sum(on_c[c][1] for c in common)} = {on_rate:.3f}")
    print(f"   无恢复组   episode 成功率={sum(off_c[c][0] for c in common)}/{sum(off_c[c][1] for c in common)} = {off_rate:.3f}")


if __name__ == "__main__":
    results = {}
    for name, d in OFFICIAL.items():
        eps, k, n = recompute(name, d)
        results[name] = {"k": k, "n": n}
    fault_paired(OFFICIAL["B1_fault_rule"], OFFICIAL["B0_fault_rule_norecov"])
    # object-level success for E1 vs B1
    os.makedirs("outputs/s2_w2_1", exist_ok=True)
    with open("outputs/s2_w2_1/recomputed_metrics.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nsaved -> outputs/s2_w2_1/recomputed_metrics.json")
