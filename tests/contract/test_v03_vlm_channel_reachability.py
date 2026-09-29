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

import json
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


def test_a_config_path_builds_a_planner_and_an_adapter_the_same_way():
    """The one reader of a model config, and it is read from two doors.

    `DeepSeekPlanner.from_env(path)` had its `@classmethod` decorator lost in an edit this
    round, which turned the path into `cls` and made every `--planner deepseek` batch die on
    its first episode with `TypeError: 'str' object is not callable` — and 1187 tests passed,
    because nothing in the suite called it with a path. So the door is opened here, by name,
    from a config on disk.

    Two doors and one reader is the point: `DeepSeekAdapter.from_config` exists precisely so
    a camera arm does not have to build a planner to get an adapter, and this asserts the
    planner door still opens after the split."""
    from embodied_agent.adapters.deepseek import DeepSeekAdapter, DeepSeekPlanner

    cfg = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "models",
                       "bst_text_agnes.yaml")
    assert os.path.exists(cfg), cfg

    planner = DeepSeekPlanner.from_env(cfg)
    assert isinstance(planner, DeepSeekPlanner)
    assert isinstance(planner.adapter, DeepSeekAdapter)
    assert planner.adapter.model == "agnes-2.5-flash"
    assert planner.goal_prompt == "s2-goal-v1"

    adapter = DeepSeekAdapter.from_config(cfg)
    assert adapter.model == planner.adapter.model
    assert adapter.base_url == planner.adapter.base_url
    assert adapter.max_tokens == planner.adapter.max_tokens
    assert adapter.sampling == planner.adapter.sampling, \
        "the camera seat and the decision seat must record the same sampling"


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


def test_offline_asks_the_batch_not_the_decision_seat():
    """A `rule` planner on a `vlm` channel spends, and the manifest used to say it did not.

    The Cost group's columns sit next to this flag, so a flag that under-reports spend is
    not a cosmetic field — it qualifies the reading it sits beside."""
    from embodied_agent.evaluation.run import _batch_is_offline

    assert _batch_is_offline("rule", "vlm") is False
    assert _batch_is_offline("deepseek", "vlm") is False
    assert _batch_is_offline("deepseek", "privileged") is False
    assert _batch_is_offline("rule", "privileged") is True
    assert _batch_is_offline("rule", "stub") is True
    assert _batch_is_offline("fixture", "privileged") is True


class _FakeVisionAdapter:
    """Answers like a provider, costs nothing, and reports a ledger the way the real
    adapter does — the row is the thing under test, so the answer can be a stub."""

    http_requests = prompt_tokens = completion_tokens = 0
    transport_retries = api_errors = format_repairs = 0
    model = provider = "fake"
    max_tokens = 1200
    sampling = {"model": "fake"}

    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def chat_vision_json(self, system, user, images, *, kind, prompt_version,
                         max_tokens=None, modality="rgb"):
        if self.fail:
            # the #155 shape: a billed generation that never became readable
            raise _BilledAndEmpty(f"provider sent a message with no `content` field "
                                  f"(reasoning 14501 chars, finish_reason=length, "
                                  f"completion_tokens=4096 of ceiling max_tokens=4096)",
                                  requests_made=1,
                                  meta={"provider": "fake", "requested_model": "fake",
                                        "finish_reason": "length", "latency_s": 12.5,
                                        "content_field_present": False,
                                        "reasoning_field_present": True,
                                        "reasoning_chars": 14501,
                                        "http_requests_this_call": 1,
                                        "prompt_tokens_this_call": 428,
                                        "completion_tokens_this_call": 4096,
                                        "transport_retries_this_call": 0,
                                        "api_errors_this_call": 0,
                                        "format_repairs_this_call": 0, "raw_chars": 0})
        return ({"objects": []},
                {"provider": "fake", "requested_model": "fake", "returned_model": "fake",
                 "finish_reason": "stop", "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                 "latency_s": 0.5, "raw_response": '{"objects": []}', "raw_chars": 14,
                 "content_field_present": True, "reasoning_field_present": False,
                 "reasoning_chars": 0, "http_requests_this_call": 1,
                 "prompt_tokens_this_call": 7, "completion_tokens_this_call": 3,
                 "transport_retries_this_call": 0, "api_errors_this_call": 0,
                 "format_repairs_this_call": 0, "kind": kind, "prompt_version": prompt_version})


class _BilledAndEmpty(Exception):
    def __init__(self, message, *, requests_made=0, meta=None):
        super().__init__(message)
        self.requests_made = requests_made
        self.meta = meta or {}


def _a_frame(tmp_path):
    import numpy as np
    from PIL import Image

    from embodied_agent.perception.camera import CameraSpec
    from embodied_agent.perception.frames import SensorFrame

    png = tmp_path / "f0.png"
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(png)
    return SensorFrame(frame_id="look_0001", camera_id="main",
                       camera=CameraSpec.model_validate({
                           "eye": {"x": 0.0, "y": 0.0, "z": 0.5},
                           "target": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "up": {"x": 0.0, "y": 0.0, "z": 1.0}}),
                       modality="rgb", image_ref=str(png),
                       image_sha256="0" * 64, image_bytes=png.stat().st_size)


def _a_catalog():
    """A catalog the validator accepts: one colour is the minimum, and the word table has to
    match it or `attributes_for_word` cannot resolve anything."""
    from embodied_agent.perception.catalog import PerceptionCatalog

    return PerceptionCatalog.model_validate({
        "colours": {"red": (1.0, 0.0, 0.0)},
        "colour_words": {"红色": "red", "red": "red"},
        "regions": [{"target_id": "tray_left", "label": "左盘", "center_xy": (0.0, 0.0),
                     "inner_half": 0.1, "floor_top_z": 0.6, "wall_top_z": 0.64}],
    })


def test_an_answered_look_files_one_cost_grade_row(tmp_path):
    """The happy path, so the wiring is not only ever seen failing."""
    from embodied_agent.perception.observe import VLMReader

    rows = []
    reader = VLMReader(_FakeVisionAdapter(), on_call=rows.append)
    reader.read(_a_frame(tmp_path), catalog=_a_catalog())

    assert len(rows) == 1
    row = rows[0]
    assert row["ok"] is True
    assert row["prompt_tokens_this_call"] == 7
    assert row["completion_tokens_this_call"] == 3
    assert row["content_field_present"] is True
    assert row["frame_ref"] == "look_0001"
    assert row["latency_s"] == 0.5


def test_a_dead_look_files_its_row_too(tmp_path):
    """The row that matters most. A look that returned nothing was still billed, and a Cost
    group that only counts answers understates the batch it is describing — which is how a
    run that spent 4096 tokens per look reads as spending none."""
    from embodied_agent.perception.observe import VLMReader

    rows = []
    reader = VLMReader(_FakeVisionAdapter(fail=True), on_call=rows.append)
    with pytest.raises(_BilledAndEmpty):
        reader.read(_a_frame(tmp_path), catalog=_a_catalog())

    assert len(rows) == 1
    row = rows[0]
    assert row["ok"] is False
    assert row["finish_reason"] == "length"
    assert row["content_field_present"] is False
    assert row["reasoning_chars"] == 14501
    assert row["completion_tokens_this_call"] == 4096
    assert "no `content` field" in row["error"]


def test_a_reader_with_no_callback_makes_no_row(tmp_path):
    """Kept as the *other* half of the contract: `on_call=None` is still legal and still
    silent, so the runner passing one is the thing that has to be asserted elsewhere."""
    from embodied_agent.perception.observe import VLMReader

    reader = VLMReader(_FakeVisionAdapter(), on_call=None)
    reading = reader.read(_a_frame(tmp_path), catalog=_a_catalog())
    assert reading is not None


def test_the_runner_hands_the_ledger_to_the_perceiver(tmp_path, monkeypatch):
    """`build_perceiver` is given a callback, and that callback writes the batch's file.

    Asserted at the seam the runner owns — the call into `build_perceiver` — because the
    callback is created inside `run_one_episode` and never appears in its own signature.
    What was silently absent before was the `on_call=` argument at that call, and `None` is
    a legal value there that files nothing."""
    from embodied_agent.evaluation import run as runmod
    from embodied_agent.perception import arm as armmod

    vision = _write(tmp_path, "sees.yaml", ["text", "vision"])
    captured = {}

    def _fake_build_perceiver(**kwargs):
        captured["on_call"] = kwargs.get("on_call")
        captured["perceive"] = kwargs.get("perceive")
        raise KeyboardInterrupt("stop before the reader is built")

    monkeypatch.setattr(armmod, "build_perceiver", _fake_build_perceiver)
    with pytest.raises(KeyboardInterrupt):
        runmod.run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                         out_root=str(tmp_path / "out"), perceive="vlm",
                         model_config=vision, frames=False,
                         case_ids=["lh_c1_shared_pair_restore"])

    assert captured["perceive"] == "vlm"
    assert callable(captured["on_call"]), \
        "the runner must hand the perceiver a ledger callback; `None` files nothing (#109)"

    # and the callback reaches the file the batch is billed from
    captured["on_call"]({"ok": True, "kind": "perception", "prompt_version": "s2-perceive-v1",
                         "provider": "fake", "prompt_tokens_this_call": 7,
                         "completion_tokens_this_call": 3, "latency_s": 0.5})
    ledgers = list((tmp_path / "out").glob("*/episodes/*/model_calls.jsonl"))
    assert ledgers, "the callback must write the batch's model_calls.jsonl"
    rows = [json.loads(line) for line in ledgers[0].read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert len(rows) == 1
    assert rows[0]["completion_tokens_this_call"] == 3
    assert rows[0]["case_id"] == "lh_c1_shared_pair_restore"
    assert rows[0]["kind"] == "perception"
    assert rows[0]["mode"] == "B"


def test_a_privileged_batch_hands_the_perceiver_nothing(tmp_path, monkeypatch):
    """The privileged channel never builds a perceiver, so it must not be paying for one —
    and its ledger stays the decision source's alone, which is the identity a later reader
    pools on."""
    from embodied_agent.acquisition.memory import SkillMemory
    from embodied_agent.episodic.store import ExperienceStore
    from embodied_agent.evaluation import run as runmod
    from embodied_agent.perception import arm as armmod

    def _boom(**kwargs):
        raise AssertionError("a privileged batch must not build a perceiver")

    monkeypatch.setattr(armmod, "build_perceiver", _boom)
    store = tmp_path / "store.jsonl"
    store.write_text("", encoding="utf-8")
    lib = tmp_path / "lib.jsonl"
    lib.write_text("", encoding="utf-8")
    out = tmp_path / "out"
    r = runmod.run_group("long_horizon", modes=("B",), planner_kind="rule", repeats=1,
                         out_root=str(out), perceive="privileged", ablation="full",
                         experience_store=ExperienceStore.load(str(store)),
                         skill_memory=SkillMemory.load(str(lib)), frames=False,
                         case_ids=["lh_c1_shared_pair_restore"])
    assert r["outcomes"].get("infrastructure_error", 0) == 0, r["outcomes"]


def test_the_ledger_row_carries_the_columns_a_cost_group_sums():
    """Which columns the Cost group will sum, named here so a rename cannot quietly remove
    the denominator."""
    from embodied_agent.perception.observe import READING_ROW_COLUMNS

    for k in ("ok", "prompt_tokens_this_call", "completion_tokens_this_call", "latency_s",
              "http_requests_this_call", "finish_reason", "raw_chars",
              "content_field_present", "reasoning_chars", "prompt_version", "frame_ref",
              "image_sha256"):
        assert k in READING_ROW_COLUMNS, k
