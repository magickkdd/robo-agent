"""Reverse test (spec 6.5): agent-side components must NOT accept a truth
provider or EvalSpec — only the IndependentEvaluator may read scoring truth."""
import inspect

from embodied_agent.core.runtime import Runtime
from embodied_agent.core.skills import SkillExecutor, SkillRegistry
from embodied_agent.core.runtime import RecoveryPolicy  # noqa: F401


def test_agent_components_have_no_truth_provider_parameter():
    for cls in (SkillExecutor,):
        params = inspect.signature(cls.__init__).parameters
        assert not any("truth" in p.lower() or "eval_spec" in p.lower() for p in params), params


def test_evaluator_is_the_only_eval_spec_consumer():
    import embodied_agent.core.skills as sk
    import embodied_agent.core.planner as pl
    import embodied_agent.core.interpreter as interp
    for mod in (sk, pl, interp):
        src = inspect.getsource(mod)
        assert "EvalSpec" not in src, f"{mod.__name__} must not consume EvalSpec"
