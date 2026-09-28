"""BST-1.0 Phase 0: ALFWorld text-backend environment check.

Answers three questions and writes them to JSON: is the pinned text stack
installable and importable on CPU, does the official game collection reproduce
the published splits, and can a game be reset/stepped/closed without an
infrastructure error.

Run:
  /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.envcheck \
      --repo /home/czx/embodied-agent-robot-agent-embodied-agent-4 \
      --out /tmp/bst_v01/environment_check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# The import name is not always the distribution name. Recording only one of them
# makes a healthy install look broken: `pip show fast_downward_textworld` reports a
# version, while `import fast_downward_textworld` raises ModuleNotFoundError because
# the package installs a module called `fast_downward`.
IMPORT_ALIAS = {"fast_downward_textworld": "fast_downward"}


def pkg_version(name: str) -> dict:
    out = {"version": None, "path": None, "distribution": name,
           "import_name": IMPORT_ALIAS.get(name, name)}
    try:
        mod = __import__(out["import_name"])
        out["version"] = getattr(mod, "__version__", None) or getattr(mod, "version", None)
        out["path"] = getattr(mod, "__file__", None)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    if out["version"] is None:
        # a module that imports without exposing a version attribute (or one whose
        # distribution is named differently) is still pinned by its metadata
        try:
            from importlib.metadata import version

            out["version"] = version(name)
            out["version_source"] = "importlib.metadata"
        except Exception:  # noqa: BLE001
            pass
    return out


def license_records() -> dict:
    """What each third-party piece of this stack says about its own terms.

    Phase 0 has to answer "may this be redistributed with the run products" before
    an experiment generates any, and the answer differs per piece: the two code
    distributions, the game data downloaded from GitHub releases, and the solver
    binary are four separate grants. Only what each distribution's own metadata
    claims — version, licence classifier, home page — is recorded, plus the archive
    digests in `data.archive_manifest`. The licence texts themselves stay upstream;
    copying them into a run product would make this repository look like the thing
    being redistributed.
    """
    from importlib.metadata import distribution

    out = {}
    for dist in ("alfworld", "textworld", "fast_downward_textworld", "tatsu", "jericho",
                 "spacy", "numpy", "pytest", "pydantic", "pybullet", "pyyaml"):
        rec = {"found": False}
        try:
            d = distribution(dist)
            meta = d.metadata
            rec.update(found=True, version=d.version,
                       license=(meta.get("License") or "")[:200] or None,
                       classifiers=[c for c in meta.get_all("Classifier") or []
                                    if c.startswith("License")],
                       home_page=meta.get("Home-page"))
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
        out[dist] = rec
    out["_data"] = {
        "note": "json_2.1.1 / json_2.1.2_tw-pddl archives fetched from the ALFWorld GitHub "
                "releases listed in data.archive_manifest; their terms are the ones upstream "
                "states for that release, and the digests below pin exactly which bytes were used",
        "sha256s": "recorded per archive in data.archive_manifest",
    }
    return out


def textworld_base_config(data_dir: str, max_steps: int) -> dict:
    """The keys AlfredTWEnv actually reads, with the values from the official
    configs/base_config.yaml at commit-pinned 0.4.2."""
    p = lambda sub: os.path.join(data_dir, "json_2.1.1", sub)  # noqa: E731
    return {
        "dataset": {
            "data_path": p("train"),
            "eval_id_data_path": p("valid_seen"),
            "eval_ood_data_path": p("valid_unseen"),
            "num_train_games": -1,
            "num_eval_games": -1,
        },
        "logic": {
            "domain": os.environ.get("ALFRED_PDDL_PATH", ""),
            "grammar": os.environ.get("ALFRED_TWL2_PATH", ""),
        },
        "env": {
            "type": "AlfredTWEnv",
            "domain_randomization": False,
            "task_types": [1, 2, 3, 4, 5, 6],
            "expert_timeout_steps": 150,
            "expert_type": "handcoded",
            "goal_desc_human_anns_prob": 0.0,
        },
        "general": {"random_seed": 42, "use_cuda": False, "task": "alfred",
                    "training_method": "dqn", "observation_pool_capacity": 3,
                    "hide_init_receptacles": False},
        "rl": {"training": {"max_nb_steps_per_episode": max_steps}},
        "dagger": {"training": {"max_nb_steps_per_episode": max_steps}},
    }


def filesystem_enumeration(data_dir: str, splits: list[str]) -> dict:
    """Count game dirs that carry both traj_data.json and game.tw-pddl, by type,
    straight off disk — the cross-check for the official collector."""
    res = {}
    for split in splits:
        root = Path(data_dir) / "json_2.1.1" / split
        per_type: Counter = Counter()
        games = {}
        if not root.is_dir():
            res[split] = {"error": f"missing {root}"}
            continue
        for traj in root.rglob("traj_data.json"):
            gdir = traj.parent
            twp = gdir / "game.tw-pddl"
            if not twp.is_file():
                continue
            rel = str(gdir.relative_to(root))
            if "movable" in rel or "Sliced" in rel:
                continue
            try:
                task_type = json.loads(traj.read_text())["task_type"]
                gamedata = json.loads(twp.read_text())
            except Exception as e:  # noqa: BLE001
                per_type[f"unreadable({type(e).__name__})"] += 1
                continue
            solvable = gamedata.get("solvable", None)
            per_type[f"{task_type}|solvable={solvable}"] += 1
            if solvable is True:
                games[rel] = {"task_type": task_type,
                              "game_tw_pddl_sha256": sha256_file(str(twp)),
                              "traj_data_sha256": sha256_file(str(traj))}
        res[split] = {"counts_by_type_and_solvability": dict(per_type),
                      "n_solvable_games": len(games),
                      "games": games}
    return res


def official_collection(config: dict) -> dict:
    from alfworld.agents.environment.alfred_tw_env import TASK_TYPES, AlfredTWEnv

    out = {}
    for train_eval, key in (("train", "data_path"),
                            ("eval_in_distribution", "eval_id_data_path"),
                            ("eval_out_of_distribution", "eval_ood_data_path")):
        t0 = time.time()
        env = AlfredTWEnv(config, train_eval=train_eval)
        files = list(env.game_files)
        types = Counter()
        for f in files:
            td = Path(f).parent / "traj_data.json"
            types[json.loads(td.read_text())["task_type"]] += 1
        out[train_eval] = {"config_key": key, "num_games": env.num_games,
                           "n_game_files": len(files),
                           "by_task_type": {TASK_TYPES[i]: types[TASK_TYPES[i]] for i in range(1, 7)},
                           "game_files": [str(f) for f in files],
                           "collect_seconds": round(time.time() - t0, 2)}
    return out


def _unregister(env_id):
    import textworld.gym as g

    fn = getattr(g, "unregister", None)
    if fn is not None:
        try:
            fn(env_id)
        except Exception:  # noqa: BLE001
            pass


def _b(infos, key, i=0):
    """gym batch infos is {key: sequence-over-batch}; a plain textworld env hands
    back a flat dict. Read either without guessing."""
    v = infos.get(key) if hasattr(infos, "get") else None
    if isinstance(v, dict):
        return v.get(key)
    try:
        return v[i]
    except (TypeError, IndexError, KeyError):
        return v


def play_one_game(gamefile: str, max_steps: int) -> dict:
    """Official construction (init_env) then a no-admissible_commands
    construction, on the same game, so the two can be compared."""
    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    report: dict = {"gamefile": gamefile}
    request_infos = textworld.EnvInfos(won=True, admissible_commands=True, extras=["gamefile"])
    wrappers = [AlfredDemangler(shuffle=False), AlfredInfos]
    t0 = time.time()
    env_id = textworld.gym.register_games([gamefile], request_infos, batch_size=1,
                                          asynchronous=False, max_episode_steps=max_steps,
                                          wrappers=wrappers)
    env = textworld.gym.make(env_id)
    obs, infos = env.reset()
    report["reset_seconds"] = round(time.time() - t0, 2)
    report["infos_type"] = type(infos).__name__
    report["infos_keys"] = sorted(list(infos.keys()))
    report["initial_observation"] = obs[0]
    report["initial_observation_sha256"] = sha256_text(obs[0])
    report["won_after_reset"] = bool(_b(infos, "won"))
    report["admissible_commands_requested"] = "admissible_commands" in infos
    adm = _b(infos, "admissible_commands")
    report["n_admissible_at_reset"] = len(adm or [])
    report["admissible_sample_at_reset"] = list(adm or [])[:40]

    cmds = ["look", "inventory", "go to cabinet"]
    steps = []
    for c in cmds:
        t1 = time.time()
        obs, reward, done, infos = env.step([c])
        steps.append({"command": c, "reward": float(_b(reward if hasattr(reward, "__len__")
                                                       else [reward], "x", 0)
                                                    if isinstance(reward, dict) else reward[0]),
                      "done": bool(done[0]), "obs_sha256": sha256_text(obs[0]), "obs": obs[0],
                      "won": bool(_b(infos, "won")),
                      "step_seconds": round(time.time() - t1, 3)})
    report["steps"] = steps
    env.close()
    report["closed"] = True
    _unregister(env_id)

    t2 = time.time()
    env_id2 = textworld.gym.register_games(
        [gamefile], textworld.EnvInfos(won=True, extras=["gamefile"]), batch_size=1,
        asynchronous=False, max_episode_steps=max_steps, wrappers=wrappers)
    env2 = textworld.gym.make(env_id2)
    obs2, infos2 = env2.reset()
    report["no_admissible_construction"] = {
        "obs_identical_to_official": obs2[0] == report["initial_observation"],
        "keys": sorted(list(infos2.keys())),
        "has_admissible_commands": "admissible_commands" in infos2,
        "won": bool(_b(infos2, "won")),
        "seconds": round(time.time() - t2, 2)}
    obs2, r2, d2, infos2b = env2.step(["look"])
    report["no_admissible_construction"]["step_keys"] = sorted(list(infos2b.keys()))
    report["no_admissible_construction"]["step_won"] = bool(_b(infos2b, "won"))
    env2.close()
    _unregister(env_id2)
    return report


def repeat_stability(gamefile: str, n: int = 3) -> dict:
    """reset/step/close x n: the Phase 0 exit criterion is no infrastructure error."""
    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    hashes, errors = [], []
    for i in range(n):
        try:
            eid = textworld.gym.register_games(
                [gamefile], textworld.EnvInfos(won=True, extras=["gamefile"]),
                batch_size=1, asynchronous=False, max_episode_steps=60,
                wrappers=[AlfredDemangler(shuffle=False), AlfredInfos])
            e = textworld.gym.make(eid)
            obs, infos = e.reset()
            o, _, d, inf = e.step(["look"])
            e.close()
            _unregister(eid)
            hashes.append({"iter": i, "reset_sha256": sha256_text(obs[0]),
                           "step_sha256": sha256_text(o[0]), "done": bool(d[0]),
                           "won": bool(_b(inf, "won")),
                           "reward_was_none": _b(inf, "reward") is None})
        except Exception:  # noqa: BLE001
            errors.append({"iter": i, "traceback": traceback.format_exc(limit=6)})
    same = len({h["reset_sha256"] for h in hashes}) == 1
    return {"n": n, "runs": hashes, "errors": errors,
            "deterministic_reset_text": same, "hard_errors": len(errors)}


def main(argv=None) -> int:
    """`argv` is a parameter rather than an implicit `sys.argv` so the benchmark CLI
    can invoke the same check with its own flags; `-m embodied_agent.benchmark.envcheck`
    still works unchanged."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("ALFWORLD_DATA")
                    or os.path.expanduser("~/.cache/alfworld"))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="/tmp/bst_v01/environment_check.json")
    ap.add_argument("--max-steps", type=int, default=60)
    args = ap.parse_args(argv)

    os.environ.setdefault("ALFWORLD_DATA", args.data_dir)
    check: dict = {
        "bst_spec": "BST-1.0",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 "python": sys.version.split()[0], "python_executable": sys.executable,
                 "cpu_count": os.cpu_count(), "java": None, "gpu_used": False,
                 "note": "text backend only; no ai2thor, no torch, no CUDA"},
        "packages": {},
        "repo": {},
        "data": {},
        "errors": [],
    }
    try:
        java = subprocess.run(["java", "-version"], capture_output=True, text=True)
        check["host"]["java"] = (java.stderr or java.stdout).splitlines()[:1]
    except Exception:  # noqa: BLE001
        check["host"]["java"] = "not installed (not required by the text backend)"

    # `yaml` is in this list because a benchmark run cannot read its own subject
    # config without it: `cli.load_model_config` imports it, so a version that is not
    # recorded here is a version that ran the experiment anyway.
    for p in ("alfworld", "textworld", "numpy", "tatsu", "jericho",
              "fast_downward_textworld", "spacy", "yaml"):
        check["packages"][p] = pkg_version(p)
    import alfworld

    site = str(Path(alfworld.__file__).parent)
    check["packages"]["_site_packages_path"] = site
    check["packages"]["pip_freeze_sha256"] = None
    try:
        fr = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True,
                            text=True).stdout
        check["packages"]["pip_freeze_sha256"] = sha256_text(fr)
        check["packages"]["pip_freeze_lines"] = len([l for l in fr.splitlines() if l])
    except Exception:  # noqa: BLE001
        pass

    repo = Path(args.repo)
    check["repo"]["path"] = str(repo)
    check["repo"]["git_commit"] = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    # SPEC-BST 5.5 pins more than a commit: the tree these numbers came from was
    # dirty, so "which commit" alone cannot reproduce it. The diff of the tracked
    # files and the digest of every untracked file that takes part in a run are
    # both recorded, and neither record contains file contents.
    tracked_diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-color", "HEAD"], capture_output=True,
        text=True).stdout
    check["repo"]["tracked_diff_sha256"] = sha256_text(tracked_diff)
    check["repo"]["tracked_diff_changed_files"] = [
        l.split(" b/")[-1] for l in tracked_diff.splitlines()
        if l.startswith("diff --git ")]
    check["repo"]["untracked"] = {}
    for rel in subprocess.run(["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"],
                              capture_output=True, text=True).stdout.splitlines():
        p = repo / rel
        if p.is_file():
            check["repo"]["untracked"][rel] = {
                "sha256": sha256_bytes(p.read_bytes()), "bytes": p.stat().st_size}

    check["licenses"] = license_records()
    check["packages"]["pytest"] = pkg_version("pytest")

    data_manifest = os.path.join(args.data_dir, "bst_download_manifest.json")
    check["data"]["dir"] = args.data_dir
    check["data"]["archive_manifest"] = (json.loads(Path(data_manifest).read_text())
                                         if os.path.isfile(data_manifest) else "missing")
    tw_pddl_dir = os.path.join(args.data_dir, "logic")
    check["data"]["logic_dir"] = tw_pddl_dir

    config = textworld_base_config(args.data_dir, args.max_steps)
    check["data"]["config_used"] = config

    try:
        check["filesystem"] = filesystem_enumeration(
            args.data_dir, ["train", "valid_seen", "valid_train", "valid_unseen"])
    except Exception:  # noqa: BLE001
        check["errors"].append({"stage": "filesystem", "traceback": traceback.format_exc()})

    try:
        coll = official_collection(config)
        check["official_collection"] = {k: {kk: vv for kk, vv in v.items() if kk != "game_files"}
                                        for k, v in coll.items()}
        check["official_collection"]["_game_files"] = {
            k: v["game_files"] for k, v in coll.items()}
    except Exception:  # noqa: BLE001
        check["errors"].append({"stage": "official_collection",
                                "traceback": traceback.format_exc()})
        coll = {}

    sample = None
    if coll.get("eval_out_of_distribution"):
        sample = coll["eval_out_of_distribution"]["game_files"][0]
    if sample:
        try:
            check["playthrough"] = play_one_game(sample, args.max_steps)
        except Exception:  # noqa: BLE001
            check["errors"].append({"stage": "playthrough", "traceback": traceback.format_exc()})
        try:
            check["repeat_stability"] = repeat_stability(sample, 3)
        except Exception:  # noqa: BLE001
            check["errors"].append({"stage": "repeat_stability",
                                    "traceback": traceback.format_exc()})

    check["exit_criteria"] = {
        "text_only_stack_installed": all(
            check["packages"].get(p, {}).get("version") for p in ("alfworld", "textworld")),
        "six_types_enumerable": bool(coll.get("eval_out_of_distribution", {})
                                     .get("by_task_type")),
        "no_hard_errors_during_play": (check.get("repeat_stability", {})
                                       .get("hard_errors", 1) == 0),
        "errors_recorded": len(check["errors"]),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(check, indent=1))
    slim = {k: v for k, v in check.items() if k not in ("filesystem", "official_collection")}
    print(json.dumps(slim, indent=1)[:3000])
    fs = check.get("filesystem", {})
    for split, v in fs.items():
        print(f"[fs] {split}: solvable={v.get('n_solvable_games')} "
              f"{v.get('counts_by_type_and_solvability')}")
    for k, v in check.get("official_collection", {}).items():
        if isinstance(v, dict):
            print(f"[official] {k}: num_games={v.get('num_games')} {v.get('by_task_type')}")
    print("wrote", args.out)
    return 0 if not check["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
