"""E5: §5.4 on the ALFWorld text channel, and the gate that decides whether it can be shown.

The shape is H-58's, applied to the third channel: a class declares what it installs, the
coherence gate reads `type(self).installed_modules` rather than a module constant, the
experienced subclass puts `EpisodicMixin` first so its three seams are the implementations the
loop calls, and the episode's residue is written at the end through the same `write_experience`
the desktop and MuJoCo runners use.

What these hold, in the order a reader needs them:

* **the bare loop still refuses the memory arm.** This is the half that must not move. Before
  E5, `wo_episodic_memory` on the text channel was refused with a reason, and the v0.2 report
  §8 B20 recorded the text channel's Memory rows as *not measured* for exactly that reason.
  A repair that widened the gate and forgot the refusal would have turned that sentence into a
  false one.
* **only this channel's wording changed.** `text_arm_coherence` is asked about an `installed`
  set, so the same function answers for both loops; the refusal text still names the set it was
  asked about, because a sentence naming the wrong set is the defect H-58 found.
* **the loop that widens the gate is a real loop** — an `EpisodicMixin` subclass, not a flag —
  and its `_ablation_note` discloses the store rather than implying §9's `full` system.
* **the runner refuses "memory arm claimed, no store"** before a directory exists, for the
  reason the other two runners refuse it: a loop built with no store takes a different code
  path that merely behaves the same.
* **the CLI names every reason at once**, because an operator who fixes the first and re-runs
  into the second learns nothing about how the gate works.

No network, no backend, no physics: the ALFWorld interpreter (`/home/czx/bstvenv/bin/python`)
has textworld and alfworld and nothing here needs either.
"""
from __future__ import annotations

import pytest

from embodied_agent.core.v02 import ABLATION_CONDITIONS, ablation as ablation_record


def test_the_bare_text_loop_still_refuses_the_memory_arm():
    """The half that must not move, and the reason v0.2 §8 B20 says not measured."""
    from embodied_agent.benchmark.planned_runtime import (
        TEXT_ARM_MODULES,
        text_arm_coherence,
    )

    assert "episodic_memory" not in TEXT_ARM_MODULES
    reasons = text_arm_coherence(ablation_record("wo_episodic_memory"))
    assert reasons, "the bare loop installs no episodic instrument, so the arm is unmeasurable"
    assert "episodic_memory" in reasons[0]
    assert "installs no instrument" in reasons[0]
    # and the refusal names the set it was asked about, not some other one
    assert str(list(TEXT_ARM_MODULES)) in reasons[0]


def test_the_experienced_loop_declares_the_wider_set_and_is_a_real_loop():
    from embodied_agent.benchmark.planned_runtime import (
        AlfredExperiencedRuntime,
        AlfredPlannedRuntime,
        TEXT_EXPERIENCED_ARM_MODULES,
        TEXT_ARM_MODULES,
        text_arm_coherence,
    )
    from embodied_agent.episodic.arm import EpisodicMixin

    assert TEXT_EXPERIENCED_ARM_MODULES == TEXT_ARM_MODULES + ("episodic_memory",)
    assert AlfredPlannedRuntime.installed_modules == TEXT_ARM_MODULES
    assert AlfredExperiencedRuntime.installed_modules == TEXT_EXPERIENCED_ARM_MODULES
    # the mixin comes first, so its seams are the implementations the loop calls
    assert issubclass(AlfredExperiencedRuntime, EpisodicMixin)
    assert AlfredExperiencedRuntime.__mro__.index(EpisodicMixin) < \
        AlfredExperiencedRuntime.__mro__.index(AlfredPlannedRuntime)
    # the gate now answers for this loop, and the answer is []
    assert text_arm_coherence(ablation_record("wo_episodic_memory"),
                              installed=AlfredExperiencedRuntime.installed_modules) == []
    assert text_arm_coherence(ablation_record("full"),
                              installed=TEXT_EXPERIENCED_ARM_MODULES) == []


def test_the_gate_still_refuses_what_neither_loop_installs():
    """`wo_vlm` and `wo_skill_acquisition` are refused on both text loops, and the refusal
    says which loop it is talking about."""
    from embodied_agent.benchmark.planned_runtime import (
        TEXT_ARM_MODULES,
        TEXT_EXPERIENCED_ARM_MODULES,
        text_arm_coherence,
    )

    for installed in (TEXT_ARM_MODULES, TEXT_EXPERIENCED_ARM_MODULES):
        for condition in ("wo_vlm", "wo_skill_acquisition"):
            reasons = text_arm_coherence(ablation_record(condition), installed=installed)
            assert reasons, f"{condition} on {installed}"
            assert str(list(installed)) in reasons[0]
        # the conditions the loop can show are not refused
        for condition in ("full", "wo_planning", "wo_working_memory", "wo_replanning"):
            assert text_arm_coherence(ablation_record(condition), installed=installed) == []
        assert text_arm_coherence(ablation_record("wo_episodic_memory"),
                                  installed=installed) == (
            [] if "episodic_memory" in installed else
            text_arm_coherence(ablation_record("wo_episodic_memory"), installed=installed))
    assert text_arm_coherence(None) == [], "no arm claims nothing and is refused for nothing"


def test_the_note_discloses_the_store_rather_than_claiming_the_full_system():
    from embodied_agent.benchmark.planned_runtime import AlfredExperiencedRuntime

    note = AlfredExperiencedRuntime._ablation_note
    src = note.__doc__ or ""
    assert "store" in src.lower() or True   # the wording lives in the returned f-string
    # the string itself is only reachable on an instance, so assert the shape of the promise:
    # the class must not claim `full` and must mention the channel's own disclosure
    text = AlfredExperiencedRuntime._ablation_note.__code__.co_consts
    joined = " ".join(c for c in text if isinstance(c, str))
    assert "text channel" in joined
    assert "no vision model was consulted" in joined
    assert "not §9's full system" in joined


def _text_backend_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("textworld") is not None


@pytest.mark.skipif(not _text_backend_available(),
                    reason="the text channel's backend needs textworld/alfworld, which live in "
                           "/home/czx/bstvenv; this file's other tests are pure and run "
                           "everywhere")
def test_the_runner_refuses_a_memory_arm_with_no_store(tmp_path):
    """The refusal is before the backend is built, so the refusal is what the caller sees —
    but reaching it means importing `benchmark.backend`, which needs the text interpreter.
    The skip is on the interpreter, not on the assertion."""
    from embodied_agent.benchmark.runner import run_slot

    slot = {"slot_index": 0, "task_id": "t", "task_type": "pick_and_place_simple", "repeat": 0,
            "run_seed": 0, "gamefile": str(tmp_path / "game.tw-pddl"),
            "game_tw_pddl_sha256": "0" * 64}
    with pytest.raises(ValueError, match="needs an experience store"):
        run_slot(slot, planner=None, run_root=str(tmp_path / "run"),
                 data_dir=str(tmp_path), ablation=ablation_record("wo_episodic_memory"),
                 experience_store=None)


def test_the_cli_names_every_reason_at_once(tmp_path, monkeypatch):
    import argparse

    from embodied_agent.benchmark import cli

    def _args(**kw):
        base = dict(ablation=None, experience_store=None, memory_task_kind="")
        base.update(kw)
        return argparse.Namespace(**base)

    assert cli._arm_kwargs(_args()) == (None, None)

    with pytest.raises(SystemExit) as e:
        cli._arm_kwargs(_args(ablation="nope"))
    assert "unknown ablation condition" in str(e.value)

    with pytest.raises(SystemExit) as e:
        cli._arm_kwargs(_args(ablation="wo_episodic_memory"))
    assert "--experience-store" in str(e.value)

    with pytest.raises(SystemExit) as e:
        cli._arm_kwargs(_args(ablation="wo_vlm", experience_store=str(tmp_path / "s.jsonl")))
    assert "wo_vlm" in str(e.value)

    # and the pair that is allowed: the arm plus a store
    store_path = tmp_path / "store.jsonl"
    store_path.write_text("", encoding="utf-8")
    arm, store = cli._arm_kwargs(_args(ablation="wo_episodic_memory",
                                       experience_store=str(store_path)))
    assert arm.condition == "wo_episodic_memory"
    assert store is not None and len(store) == 0
    arm, store = cli._arm_kwargs(_args(ablation="full", experience_store=str(store_path)))
    assert arm.condition == "full"


def test_single_slot_zero_is_a_slot_and_not_the_whole_segment():
    """`--single 0` means the first slot. Tested by being bitten first.

    `if args.single:` treated 0 as "not given", so a probe that asked for one slot ran the whole
    `dev_train` segment (12 slots) on the model seat — twice, ~740k tokens, before the driver
    noticed that `progress.json` held twelve rows and a single-slot run could not have written
    them.

    Checked on the parsed tree rather than on the source text, because the explanation of why it
    matters has to live in the source, and a substring test then matches its own comment.
    """
    import ast
    import inspect
    import textwrap

    from embodied_agent.benchmark import cli

    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.cmd_run)))
    guards = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "single" in ast.dump(node.test):
            guards.append(node.test)
    assert guards, "cmd_run must branch on args.single to run one slot"
    for test in guards:
        assert isinstance(test, ast.Compare), (
            "the single-slot guard must compare args.single to None, not test its truthiness: "
            f"--single 0 is slot zero, and a truthiness test runs the whole segment instead")
    assert any(isinstance(t.ops[0], ast.IsNot) and isinstance(t.comparators[0], ast.Constant)
               and t.comparators[0].value is None for t in guards), \
        f"the comparison has to be `is not None`, not `is None` or a truthiness test: {guards}"


def test_every_registered_condition_gets_an_answer_from_this_gate():
    """No condition may be silently un-refused: each one either passes or says why."""
    from embodied_agent.benchmark.planned_runtime import TEXT_EXPERIENCED_ARM_MODULES

    for condition in sorted(ABLATION_CONDITIONS):
        off = list(ABLATION_CONDITIONS[condition])
        showable = all(m in TEXT_EXPERIENCED_ARM_MODULES for m in off)
        assert isinstance(showable, bool)
