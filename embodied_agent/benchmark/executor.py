"""One skill call, one environment command, one measured reply (SPEC-BST 3.5, 6.1).

This is the layer the prohibitions are about, so what it does *not* do is the
content of the record:

* it never sends more than one command for one `SkillCall`, and never sends a
  navigation, opening or find-and-retry sequence on the model's behalf;
* it never repairs a reference: `native_actions.render` uses the names verbatim and
  refuses with reasons when a name is not an instance reference, and an id that the
  engine does not know is the engine's answer to read, not a thing to guess about;
* it never decides whether an action was a good idea. It reports what was issued,
  whether it left this process, how long the engine took and what came back;
* a backend fault is turned into a `SkillResult` rather than an exception, because
  an environment that broke must still produce an episode record with the real
  cost in it (SPEC-BST 6.3), and the loop's own `RuntimeError` guard for an action
  left open stays honest.

`TextScene` exists for the same reason: `Runtime` was written against a simulator
and asks it two questions per round — "is an action still open?" and "what is the
simulated clock?" — and a text world's answers are "no" and "there is none".
"""
from __future__ import annotations

import time

from ..core.contracts import SkillCall, SkillResult, SkillStatus
from .native_actions import render
from .parser import parse


class TextScene:
    """The two scene facts the frozen loop reads, with a text world's answers."""

    def __init__(self):
        self.action_in_progress = False
        # not 0.0-as-a-lie: a text environment has no simulated clock, and the
        # numbers that do exist (env actions, command seconds, wall clock) are
        # recorded beside it by the executor and the backend
        self.sim_time = 0.0
        self.objects: dict = {}
        self.trays: dict = {}

    def settle(self, seconds: float) -> None:  # pragma: no cover - never stepped
        return None

    def render(self, path: str) -> None:  # pragma: no cover - no frames to draw
        return None


class AlfredTextExecutor:
    """`SkillCall` -> `render` -> exactly one `env.step` -> `SkillResult`."""

    def __init__(self, backend, *, placement_planner=None):
        self.backend = backend
        # the desktop runtime reads this attribute; a text episode has no slot
        # geometry to resolve, so there is no planner to name
        self.placement_planner = placement_planner
        self.calls = 0
        self.command_seconds = 0.0

    def execute(self, call: SkillCall) -> SkillResult:
        t0 = time.time()
        command, reasons = render(call.skill, dict(call.args or {}))
        if command is None:
            # never reached from the runtime (the text validator refuses first);
            # kept as a real guard so no path can send a half-built command
            return SkillResult(
                call_id=call.call_id, status=SkillStatus.rejected,
                failure_code="ADAPTER_ERROR", t_start=t0, t_end=time.time(),
                held_state="unknown",
                notes=[f"skill {call.skill!r} does not map to one environment command: {r}"
                       for r in reasons])
        timeout_mismatch = (float(call.timeout_s) != float(self.backend.command_timeout_s))
        try:
            raw = self.backend.step(command)
        except Exception as e:  # noqa: BLE001 - an environment fault is an outcome
            self.calls += 1
            return SkillResult(
                call_id=call.call_id, status=SkillStatus.failed,
                failure_code="ENVIRONMENT_ERROR", t_start=t0, t_end=time.time(),
                sim_seconds_used=0.0, stages_executed=["command_issued"],
                held_state="unknown",
                measurements={"env_steps_total": float(self.backend.env_steps)},
                notes=[f"{type(e).__name__}: {e}",
                       "the command may or may not have reached the engine; it is never "
                       "replayed to find out (SPEC-BST 6.3)"])
        self.calls += 1
        seconds = round(time.time() - t0, 4)
        self.command_seconds = round(self.command_seconds + seconds, 4)
        p = parse(raw)
        held = "unknown"
        if p.held_said:
            held = p.held[0] if len(p.held) == 1 else (None if not p.held else "unknown")
        notes = [f"command: {command}", f"reply: {_one_line(raw)}"]
        if timeout_mismatch:
            notes.append("the backend's own command timeout differs from the per-skill "
                         "budget in this call")
        refused = any(f.kind == "no_effect" for f in p.facts)
        if refused:
            notes.append("the engine reported no effect")
        return SkillResult(
            call_id=call.call_id, status=SkillStatus.completed,
            t_start=t0, t_end=time.time(), sim_seconds_used=0.0,
            stages_executed=["command_issued", "reply_received"],
            held_state=held,
            measurements={"command_seconds": seconds,
                          "env_steps_total": float(self.backend.env_steps),
                          "reply_chars": float(len(raw))},
            notes=notes,
            candidate_resolution=None)


def _one_line(text: str, limit: int = 240) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "..."
