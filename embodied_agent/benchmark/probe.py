"""BST-1.0 dev-only ALFWorld text probe: native grammar + feedback vocabulary.

Why this exists: SPEC 3.6 requires the action catalogue to be built from the
pinned version's *actual* grammar, and SPEC 4.4 requires a deterministic parser
for the public observation text. Both need the environment's own output
vocabulary, which is only knowable by asking the environment.

This is an environment check, not a policy. Its output is a dev-only artifact:
nothing it produces is ever put into a prompt, a Feedback record or a
DecisionContext (SPEC 4.5). It drives the games with commands sampled from the
engine's admissible list purely to reach every message template; the admissible
list itself never leaves this file.

Run:
  /home/czx/bstvenv/bin/python -m embodied_agent.benchmark.probe \
      --games 60 --steps 220 --out /tmp/bst_v01/env_probe.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
from collections import Counter
from pathlib import Path

from embodied_agent.benchmark.envcheck import _b, filesystem_enumeration

VERBS = ("go to", "take", "put", "open", "close", "use", "clean", "heat", "cool",
         "look", "inventory", "help", "examine")
# Object/receptacle references in ALFWorld text are "<name> <n>" (e.g. "cabinet 1",
# "potato 2"). Mask them so messages collapse to templates.
REF_RE = re.compile(r"\b([a-z][a-z\-]*(?: [a-z\-]+)*?) ((?:\d+))\b")


def mask(text: str) -> str:
    """Collapse instance numbers so messages collapse to templates. ALFWorld names
    one token per referent ("cabinet 1", "an apple 2"), so a single-word capture is
    enough and keeps the surrounding English readable."""
    out = re.sub(r"\b([a-z][a-z\-]+) (\d+)\b", r"<\1>", text)
    return re.sub(r"\s+", " ", out).strip()


def cmd_pattern(cmd: str) -> str:
    """`take apple 1 from cabinet 3` -> `take <X> from <X>`: the grammar shape, with
    the instance number dropped but the preposition and the noun kept."""
    return re.sub(r"\b([a-z0-9\-]+) (\d+)\b", r"<\1>", cmd)


def noun_after_number(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"\b([a-z][a-z\-]+) \d+\b", text)]


def verb_of(cmd: str) -> str:
    for v in VERBS:
        if cmd == v or cmd.startswith(v + " "):
            return v
    return "OTHER:" + cmd.split(" ")[0]


def load_split_games(data_dir: str, split: str, n: int, seed: str) -> list[str]:
    fs = filesystem_enumeration(data_dir, [split])[split]
    ids = sorted(fs["games"], key=lambda g: hashlib.sha256(f"{seed}|{g}".encode()).hexdigest())
    return [os.path.join(data_dir, "json_2.1.1", split, g, "game.tw-pddl") for g in ids[:n]]


def probe(games: list[str], steps: int, seed: int) -> dict:
    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    templates: Counter = Counter()
    first_seen: dict[str, dict] = {}
    cmd_templates: Counter = Counter()
    per_game = []
    nouns: Counter = Counter()
    t_all = time.time()
    for gi, gf in enumerate(games):
        rng = random.Random(f"{seed}|{gi}")
        eid = textworld.gym.register_games(
            [gf], textworld.EnvInfos(won=True, admissible_commands=True, extras=["gamefile"]),
            batch_size=1, asynchronous=False, max_episode_steps=steps + 5,
            wrappers=[AlfredDemangler(shuffle=False), AlfredInfos])
        env = textworld.gym.make(eid)
        obs, infos = env.reset()
        text = obs[0]
        templates[mask(text)] += 1
        first_seen.setdefault(mask(text), {"game": os.path.basename(os.path.dirname(gf)),
                                           "raw": text[:400]})
        won, done, nsteps, errors = False, False, 0, 0
        for _ in range(steps):
            if done:
                break
            adm = list(_b(infos, "admissible_commands") or [])
            if not adm:
                break
            # Bias towards state-changing verbs so that every template family is
            # reached; `go to`/`look` alone would never produce open/use messages.
            interesting = [c for c in adm if verb_of(c) in
                           ("open", "take", "put", "use", "clean", "heat", "cool", "close")]
            pool = interesting if (interesting and rng.random() < 0.75) else adm
            cmd = rng.choice(pool)
            cmd_templates[cmd_pattern(cmd)] += 1
            nouns.update(noun_after_number(cmd))
            try:
                obs, reward, done, infos = env.step([cmd])
                done = bool(done[0]) if hasattr(done, "__len__") else bool(done)
            except Exception as e:  # noqa: BLE001
                errors += 1
                templates[f"EXCEPTION {type(e).__name__}"] += 1
                break
            nsteps += 1
            text = obs[0]
            mt = mask(text)
            templates[mt] += 1
            if mt not in first_seen:
                first_seen[mt] = {"game": os.path.basename(os.path.dirname(gf)),
                                  "after": cmd, "raw": text[:500]}
            won = bool(_b(infos, "won"))
        try:
            env.close()
        except Exception:  # noqa: BLE001
            pass
        if hasattr(textworld.gym, "unregister"):
            try:
                textworld.gym.unregister(eid)
            except Exception:  # noqa: BLE001
                pass
        per_game.append({"gamefile": gf, "steps": nsteps, "won": won, "errors": errors})
        if (gi + 1) % 10 == 0:
            print(f"probed {gi+1}/{len(games)} templates={len(templates)} "
                  f"cmd_forms={len(cmd_templates)} t={time.time()-t_all:.0f}s", flush=True)
    return {"n_games": len(games), "n_steps": sum(p["steps"] for p in per_game),
            "nouns_seen_in_commands": dict(nouns.most_common()),
            "won_in_probe": sum(1 for p in per_game if p["won"]),
            "engine_errors": sum(p["errors"] for p in per_game),
            "probe_seconds": round(time.time() - t_all, 1),
            "command_templates": dict(cmd_templates.most_common()),
            "message_templates": [
                {"template": t, "count": c, "example_raw": first_seen.get(t, {}).get("raw"),
                 "example_after": first_seen.get(t, {}).get("after"),
                 "example_game": first_seen.get(t, {}).get("game")}
                for t, c in templates.most_common()],
            "per_game": per_game}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("ALFWORLD_DATA")
                    or os.path.expanduser("~/.cache/alfworld"))
    ap.add_argument("--split", default="train")
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--steps", type=int, default=220)
    ap.add_argument("--seed", default="20260919")
    ap.add_argument("--out", default="/tmp/bst_v01/env_probe.json")
    a = ap.parse_args()
    os.environ.setdefault("ALFWORLD_DATA", a.data_dir)
    games = load_split_games(a.data_dir, a.split, a.games, a.seed)
    res = probe(games, a.steps, int(hashlib.sha256(a.seed.encode()).hexdigest()[:8], 16))
    res["data_dir"] = a.data_dir
    res["split"] = a.split
    res["n_games_requested"] = a.games
    res["note"] = ("dev-only environment characterisation; never enters a prompt, "
                   "Feedback or DecisionContext (SPEC 4.5)")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in
                      ("n_games", "n_steps", "won_in_probe", "engine_errors", "probe_seconds")}))
    print("distinct message templates:", len(res["message_templates"]))
    for t in res["message_templates"][:40]:
        print(f"  {t['count']:6d}  {t['template'][:120]}")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
