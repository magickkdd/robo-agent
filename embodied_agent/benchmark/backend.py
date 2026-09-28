"""The ALFWorld text backend, and the wall between it and the scorer (SPEC-BST 4, 8).

Two things here are the point of the module rather than incidental:

* the environment is built **without** `admissible_commands`, so the command list the
  engine keeps for its own parser is never in this process's reach. Phase 0 measured
  that the initial observation of such a construction is byte-identical to the
  official one (`benchmark/envcheck.py`), so nothing is lost from the agent's view
  except the answer sheet;
* `won` / `done` / reward land in `eval_view()`, which the decision loop reads only to
  stop and to score. It is never copied into an `Observation`, a `WorldState`, a
  `Feedback` or a `DecisionContext` (SPEC-BST 6: 官方 done 与模型 finish 分开记录).
"""
from __future__ import annotations

import os
import signal
import time
from typing import Any, Optional

import textworld

from .envcheck import _b, _unregister


class CommandTimeout(Exception):
    """An environment command did not return inside its own budget."""


class BackendError(Exception):
    """The environment itself failed. Not a model failure and not a task outcome."""


class AlfredTextBackend:
    """One game, one step at a time. `step` is the only thing that moves the world."""

    def __init__(self, gamefile: str, *, max_env_actions: int = 60,
                 command_timeout_s: float = 10.0, data_dir: Optional[str] = None):
        self.gamefile = gamefile
        self.max_env_actions = int(max_env_actions)
        self.command_timeout_s = float(command_timeout_s)
        self.data_dir = data_dir or os.path.expanduser("~/.cache/alfworld")
        self.env_id: Optional[str] = None
        self.env = None
        self.raw: str = ""
        self.env_steps = 0
        self.decision_steps = 0
        self.reset_seconds: Optional[float] = None
        self.last_step_seconds: Optional[float] = None
        self.error: Optional[dict[str, Any]] = None
        self.done = False
        self.won = False
        self.reward = 0
        self._started = False

    # ---------- lifecycle ----------
    def start(self) -> str:
        if self._started:
            raise BackendError("start() called twice on one backend")
        from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos
        request_infos = textworld.EnvInfos(won=True, extras=["gamefile"])
        t0 = time.time()
        try:
            self.env_id = textworld.gym.register_games(
                [self.gamefile], request_infos, batch_size=1, asynchronous=False,
                max_episode_steps=self.max_env_actions,
                wrappers=[AlfredDemangler(shuffle=False), AlfredInfos])
            self.env = textworld.gym.make(self.env_id)
            obs, infos = self.env.reset()
        except Exception as e:  # noqa: BLE001 - a registration failure is infrastructure
            self.error = {"phase": "reset", "type": type(e).__name__, "message": str(e)[:400]}
            raise BackendError(f"reset failed: {type(e).__name__}: {e}") from e
        self.reset_seconds = round(time.time() - t0, 3)
        self.raw = str(obs[0])
        self.won = bool(_b(infos, "won") or False)
        self._started = True
        return self.raw

    def step(self, command: str) -> str:
        """Exactly one environment action for one legal skill call (SPEC-BST 3.5)."""
        if not self._started:
            raise BackendError("step before reset")
        if self.done:
            raise BackendError(f"step after the environment terminated on {self.raw[-60:]!r}")
        if self.env_steps >= self.max_env_actions:
            raise BackendError(f"env action budget {self.max_env_actions} exhausted")
        t0 = time.time()
        installed = _alarm(self.command_timeout_s)
        try:
            obs, reward, done, infos = self.env.step([command])
        except CommandTimeout as e:
            self.error = {"phase": "step", "type": "CommandTimeout",
                          "message": f"{command!r} exceeded {self.command_timeout_s}s"}
            raise BackendError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            self.error = {"phase": "step", "type": type(e).__name__,
                          "message": f"{command!r}: {str(e)[:400]}"}
            raise BackendError(f"step failed: {type(e).__name__}: {e}") from e
        finally:
            _cancel_alarm(installed)
        self.env_steps += 1
        self.last_step_seconds = round(time.time() - t0, 4)
        self.raw = str(obs[0])
        # reward is a `step` return value, not an infos key; a batch of one returns
        # a one-element array
        self.reward = int(reward[0]) if hasattr(reward, "__len__") else int(reward or 0)
        self.done = bool(done[0]) if hasattr(done, "__len__") else bool(done)
        won = _b(infos, "won")
        if won is not None:
            self.won = bool(won)
        return self.raw

    def close(self) -> None:
        if self.env is not None:
            try:
                self.env.close()
            except Exception:  # noqa: BLE001 - closing is best-effort; nothing to score
                pass
            self.env = None
        if self.env_id:
            _unregister(self.env_id)
            self.env_id = None

    # ---------- the evaluation-only side ----------
    def eval_view(self) -> dict[str, Any]:
        """Termination and the official verdict. Read by the runner to stop and to
        score; it is not an observation channel and carries no command list."""
        return {"env_done": self.done, "official_won": self.won, "env_steps": self.env_steps,
                "max_env_actions": self.max_env_actions, "reward": self.reward,
                "error": dict(self.error) if self.error else None,
                "reset_seconds": self.reset_seconds,
                "last_step_seconds": self.last_step_seconds}


# ------------------------------------------------------------- command alarm --


def _alarm(seconds: float) -> bool:
    """SIGALRM around one command: the loop is single-threaded (SPEC-BST 5.5
    concurrency 1), so a per-process timer is enough to bound the engine."""
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        return False

    def _fire(signum, frame):  # noqa: ARG001
        raise CommandTimeout(f"environment command exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    return True


def _cancel_alarm(installed: bool) -> None:
    if installed:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
