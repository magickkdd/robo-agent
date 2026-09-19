"""Fixtures that drive the *production* path.

SPEC P0's exit condition is "隔离、预算、状态分叉测试通过 on 生产代码", and SPEC P1
asks that fixture responses run through the real Runtime. Both mean the same
thing here: an episode is assembled from the same pieces `evaluation.run` uses —
`build_scene`, `EnvironmentController`, `SkillExecutor`, `Runtime`,
`IndependentEvaluator`, `run_probe` — and the only thing a test substitutes is
the **decision source**. A script of `Decision`s standing in for a model measures
the harness, never a policy, so `Episode.source_kind` records which of the two
produced it and the tests that care assert on it.
"""
from __future__ import annotations

import dataclasses
import json
import os

import pytest

from embodied_agent.core.contracts import TaskInput
from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.runtime import Runtime
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation.evaluator import IndependentEvaluator
from embodied_agent.evaluation.protocol import run_probe
from embodied_agent.evaluation.run import build_scene, make_source, task_input
from embodied_agent.evaluation.sources import ScriptedSource
from embodied_agent.evaluation.tasks import TaskCase, find_case

__all__ = ["TaskCase", "find_case"]


@dataclasses.dataclass
class Episode:
    case: TaskCase
    mode: str
    source_kind: str
    result: object
    runtime: Runtime
    scene: object
    controller: EnvironmentController
    events: list[dict]
    score: dict
    probe: dict
    source: object = None
    contexts: list = dataclasses.field(default_factory=list)

    # ---------- evidence access ----------
    def payloads(self, event_type: str) -> list[dict]:
        return [e["payload"] for e in self.events if e.get("type") == event_type]

    def feedbacks(self) -> list[dict]:
        return [p["feedback"] for p in self.payloads("execution_feedback")]

    def last_feedback(self) -> dict:
        return self.feedbacks()[-1]

    @property
    def terminal_status(self) -> str:
        return self.result.terminal_status.value

    @property
    def failure_type(self) -> str | None:
        return None if self.result.failure_type is None else self.result.failure_type.value

    def agent_visible(self) -> str:
        """Everything that reached the decision chain, as one blob of text.

        Only these event types are what a policy is shown, so scanning them is
        the isolation test; the environment's own `environment_event` records and
        the hidden `eval_spec` are excluded by construction and the test that
        says so checks this list is non-empty first. When a run recorded the
        real `DecisionContext` objects (`recorder` below), their
        `model_payload()` — the exact prompt surface — is scanned too, because
        the log record is the runtime's *summary* of a context, not the context."""
        keep = ("decision_context", "decision", "skill_call", "observation",
                "episode_start", "execution_feedback", "finish_check", "state_version_stale")
        # the payload only: the log envelope's own `event_id`/`sequence` are
        # bookkeeping the store adds and never part of what a policy is shown,
        # so scanning them would let a real leak hide behind a false positive
        blob = json.dumps([{"type": e["type"], **e["payload"]}
                           for e in self.events if e.get("type") in keep],
                          ensure_ascii=False, default=str)
        if self.contexts:
            blob += json.dumps([c.model_payload() for c in self.contexts],
                               ensure_ascii=False, default=str)
        return blob


class _Recorder:
    """Transparent proxy that keeps the contexts a source was actually handed.

    It forwards everything, so the episode is driven by the same policy the
    runner would use; only the objects that crossed the boundary are retained."""

    def __init__(self):
        self.contexts: list = []

    def wrap(self, source):
        outer = self

        class _Proxy:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def decide(self, ctx):
                outer.contexts.append(ctx)
                return self._inner.decide(ctx)

        proxy = _Proxy(source)
        # a real instance attribute, so `__getattr__` cannot forward `contexts`
        # to the wrapped source and report it missing
        proxy.contexts = outer.contexts
        return proxy


@pytest.fixture
def recorder():
    """A factory for context recorders, one per episode.

    Usage: `ep = run_case(cid, source_factory=recorder().wrap)`, then read
    `ep.contexts`. A new recorder per run matters: a shared one would carry the
    previous episode's contexts into the next episode's scan."""
    return _Recorder


@pytest.fixture
def run_case(tmp_path):
    """Build and run one episode; closes every scene it opened, whatever failed."""
    opened: list = []

    def _run(case_id: str, *, mode: str = "B", steps: list | None = None,
             with_environment: bool = True, goal=None, source_factory=None,
             source=None, source_kind: str | None = None,
             **runtime_kw) -> Episode:
        case = find_case(case_id)
        # one directory per call: a second episode of the same case would else
        # append to the same events.jsonl, and its reader would see both
        n = len(opened)
        episode_id = f"{case_id}.{mode}.{n}"
        ep_dir = str(tmp_path / episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        scene = build_scene(case)
        opened.append(scene)
        controller = EnvironmentController(scene, case.fresh_events() if with_environment else [])
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        store = EpisodeStore(ep_dir, episode_id)
        runtime = Runtime(scene, executor, ep_dir, case.budgets, episode_id,
                          config=case.verify, environment=controller, store=store, **runtime_kw)
        executor.world_provider = runtime.observe

        task = task_input(case)
        if goal is None:
            goal = interpret(task, runtime.observe())
        if source is not None:
            # a source the test assembled itself — the SPEC 11.1 model-payload
            # boundaries need a real `LLMDecisionSource` over a fake transport,
            # which no `steps` list can express. `source_kind` says what it
            # stands in for, because a script measures the harness and never a
            # policy, and a reader of the episode must be able to tell them apart
            kind = source_kind or "injected"
        elif steps is not None:
            # a list of scripted decisions, or a ready-made `ScriptedSource` when
            # the test needs its own settings (`on_exhausted="raise", ...`)
            source = steps if isinstance(steps, ScriptedSource) else ScriptedSource(steps)
            kind = "script"
        else:
            source = make_source(mode, case, None, runtime, goal)
            kind = "rule"
        contexts: list = []
        if source_factory is not None:
            source = source_factory(source)
        # `is None`, not falsiness: the recorder's list is empty at this
        # moment and `or` would replace the very list the episode fills
        contexts = getattr(source, "contexts", None)
        if contexts is None:
            contexts = getattr(source, "seen", [])
        result = runtime.run_episode(task, goal, source, mode=mode)
        events = store.read_all()
        score = IndependentEvaluator(runtime.terminal_snapshot, case.eval_spec).score(result)
        probe = run_probe(case, result, score, events,
                          {"needs_clarification": str(result.failure_type or "")})
        return Episode(case=case, mode=mode, source_kind=kind, result=result, runtime=runtime,
                       scene=scene, controller=controller, events=events, score=score,
                       probe=probe, source=source, contexts=contexts)

    yield _run
    for scene in opened:
        scene.close()


@pytest.fixture
def scene_for():
    """A live scene for one frozen case, closed with the test."""
    opened: list = []

    def _open(case_id: str):
        scene = build_scene(find_case(case_id))
        opened.append(scene)
        return scene

    yield _open
    for scene in opened:
        scene.close()


@pytest.fixture
def world_for(scene_for):
    """The initial WorldState of a case, measured — the same snapshot a skill's
    precondition check reads, not a hand-written stand-in."""

    def _world(case_id: str):
        scene = scene_for(case_id)
        return scene, build_world_state(scene, 1, "obs_0001", find_case(case_id).verify)

    return _world
