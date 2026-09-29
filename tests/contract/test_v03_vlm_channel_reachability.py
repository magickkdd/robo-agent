"""P0' contract tests for the `--perceive vlm` gate on the MAIN cli.

The finding these hold: `channel_readiness` was called from `cli.py` and from
`run_group` with one argument, so `adapter` was `None` on every main-CLI path and
`--perceive vlm` was refused *whatever* `--model-config` said — while the sentence
it returned told the operator to pass one. A refusal that names a knob which is
not connected sends the reader to the wrong place, so the gate is now asked the
question it was written about, and these tests say which answer each input gets.

Four things are asserted, and none of them spends a request:

* the gate opens for a config that declares `vision` and refuses one that does not,
  naming the config it actually read;
* the no-config refusal is unchanged, because a caller holding only an adapter
  (the camera builders) is the weaker check and must stay the weaker check;
* the main CLI's own argument resolution accepts the vision config and refuses the
  text-only one, before a run directory exists;
* `run_group` builds that adapter and hands the same object to the episode — the
  wiring, not the sentence, since a correct refusal over a disconnected wire is the
  defect itself.

The vision request itself is E2's business, measured on a real batch; these tests
stop at the point where money would start, which is also the only place they can
stop and still be a contract rather than a rate.
"""
from __future__ import annotations

import os

import pytest
import yaml

from embodied_agent.perception.grounding import channel_readiness

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "models")


@pytest.fixture(autouse=True)
def _a_key_the_adapter_can_be_built_from(monkeypatch):
    """The adapter refuses to construct without a key, which is a real guard and not what
    these tests are about: they stop at the gate and at the wire, before any request. The
    value is a placeholder — nothing here dials a number, and `example.invalid` is not a host."""
    monkeypatch.setenv("TEST_KEY", "placeholder-not-a-real-key")


def _write(tmp_path, name, caps):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({
        "schema_version": "1", "provider": "test", "api_key_env": "TEST_KEY",
        "base_url": "https://example.invalid/v1", "model": "test-model",
        "capabilities": caps, "max_tokens": 64,
    }, sort_keys=True), encoding="utf-8")
    return str(path)


def test_the_gate_reads_the_config_it_is_handed(tmp_path):
    vision = _write(tmp_path, "sees.yaml", ["text", "vision"])
    text_only = _write(tmp_path, "blind.yaml", ["text"])
    blind = _write(tmp_path, "silent.yaml", None)

    assert channel_readiness("vlm", config_path=vision) == []

    refused = channel_readiness("vlm", config_path=text_only)
    assert len(refused) == 1
    # the sentence must name the config that was read, or it is the defect again
    assert "blind.yaml" in refused[0]
    assert "capabilities" in refused[0]

    refused = channel_readiness("vlm", config_path=blind)
    assert len(refused) == 1
    assert "silent.yaml" in refused[0]

    refused = channel_readiness("vlm", config_path=str(tmp_path / "absent.yaml"))
    assert len(refused) == 1
    assert "absent.yaml" in refused[0]


def test_a_channel_that_asks_for_nothing_is_never_refused(tmp_path):
    for channel in ("privileged", "stub", "vlm"):
        if channel != "vlm":
            assert channel_readiness(channel, config_path=str(tmp_path / "absent.yaml")) == []


def test_the_no_config_refusal_is_unchanged():
    """The adapter-only caller keeps the weaker check, and the sentence keeps its wording."""
    assert channel_readiness("vlm")
    assert "no vision model is configured" in channel_readiness("vlm")[0]
    assert "budget stated" in channel_readiness("vlm")[0]
    assert channel_readiness("vlm", adapter=object()) == []


def test_the_main_cli_accepts_a_vision_config_and_refuses_a_text_only_one(tmp_path):
    """`_perception_kwargs` is the operator's first gate, so the refusal has to happen here.
    It gates on the config; the adapter is built further in, by the runner."""
    import argparse

    from embodied_agent.cli import _perception_kwargs

    vision = _write(tmp_path, "sees.yaml", ["text", "vision"])
    text_only = _write(tmp_path, "blind.yaml", ["text"])

    def _args(**kw):
        base = dict(perceive="vlm", ablation=None, views=None, view="main", policy=None,
                    experience_store=None, skill_memory=None, propose="rule",
                    validation_budget=1, model_config=None, planner="rule", modes="B")
        base.update(kw)
        return argparse.Namespace(**base)

    kw, refusals = _perception_kwargs(_args(model_config=vision))
    assert refusals == []
    assert kw["ablation"].condition == "full", \
        "a camera channel that consults a model is `full`, not the `wo_vlm` default"

    _, refusals = _perception_kwargs(_args(model_config=text_only))
    assert len(refusals) == 1 and "blind.yaml" in refusals[0]

    _, refusals = _perception_kwargs(_args(model_config=None))
    assert len(refusals) == 1 and "no vision model is configured" in refusals[0]


def test_run_group_hands_the_episode_the_adapter_it_built(tmp_path, monkeypatch):
    """The wire, not the sentence. A gate that opens over a disconnected adapter is the
    same defect wearing a passing test.

    `resolve_goal` is left alone: with `planner_kind="rule"` it is the offline interpreter,
    so the batch reaches the episode call with nothing dialled. The fake stands in for the
    episode itself and stops there."""
    from embodied_agent.evaluation import run as runmod

    vision = _write(tmp_path, "sees.yaml", ["text", "vision"])
    seen = {}

    def _fake(case, repeat, mode, resolution, planner, root, set_name, **kwargs):
        seen.update(kwargs)
        raise KeyboardInterrupt("stop before physics; this test asserts the wire only")

    monkeypatch.setattr(runmod, "run_one_episode", _fake)

    with pytest.raises(KeyboardInterrupt):
        runmod.run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                         out_root=str(tmp_path / "out"), perceive="vlm",
                         model_config=vision, frames=False)

    assert seen.get("adapter") is not None
    assert seen["adapter"].model == "test-model"
    assert seen["adapter"].api_key == "placeholder-not-a-real-key"


def test_a_vision_config_with_no_key_behind_it_is_refused_by_name(tmp_path, monkeypatch):
    """Two failures, two sentences. The gate reads the YAML; the key is a separate fact, and
    a caller who fixed neither must be able to tell which one is still wrong."""
    from embodied_agent.evaluation.run import InfraError, run_group

    monkeypatch.delenv("TEST_KEY", raising=False)
    vision = _write(tmp_path, "sees.yaml", ["text", "vision"])

    with pytest.raises(InfraError) as e:
        run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                  out_root=str(tmp_path / "out"), perceive="vlm",
                  model_config=vision, frames=False)
    assert "TEST_KEY" in str(e.value)
    assert "sees.yaml" in str(e.value)
    assert not os.path.exists(str(tmp_path / "out"))


def test_a_vision_arm_on_a_text_only_config_is_refused_by_the_runner_too(tmp_path):
    """`run_group` carries the second of the two gates, and it must carry the same answer."""
    from embodied_agent.evaluation.run import InfraError, run_group

    text_only = _write(tmp_path, "blind.yaml", ["text"])
    with pytest.raises(InfraError) as e:
        run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                  out_root=str(tmp_path / "out"), perceive="vlm",
                  model_config=text_only, frames=False)
    assert "blind.yaml" in str(e.value)
    assert not os.path.exists(str(tmp_path / "out")), \
        "a refusal must not leave a half-written batch behind"
