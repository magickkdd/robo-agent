"""The frozen task list and the gate around it (SPEC 7, 10.3, 11.1).

These are the checks that make a reported number mean the same thing twice. None
of them needs physics, so they belong in a unit file: a scenario that drifted, a
case whose declared mechanism is not realizable, or an edited manifest must be
caught before an episode is ever run.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from embodied_agent.evaluation import tasks as T
from embodied_agent.evaluation.protocol import PROBES
from embodied_agent.evaluation.tasks import FROZEN_PATH, build_set, frozen_manifest

SETS = ("smoke", "dev", "formal", "protocol")
SIZES = {"smoke": 4, "dev": 8, "formal": 24, "protocol": 11}


def _all_cases() -> list:
    return [c for name in SETS for c in build_set(name)]


# ------------------------------------------------------------- the freeze ------


def test_the_manifest_is_a_function_of_the_task_list_only():
    assert frozen_manifest()["sha256"] == frozen_manifest()["sha256"]
    live = frozen_manifest()
    assert {k: len(v) for k, v in live["sets"].items()} == SIZES
    assert live["constants"]["max_objects_per_region"] == T.MAX_OBJECTS_PER_REGION


def test_the_repo_frozen_file_still_matches_the_code():
    ok, msg = T.check_frozen(FROZEN_PATH)
    assert ok, msg


def test_a_hand_edited_manifest_is_refused_even_though_it_carries_a_hash(tmp_path):
    """The hash is a field *inside* the file it describes, so 'the file says it is
    intact' is not evidence of anything unless the file is re-hashed independently."""
    payload = copy.deepcopy(frozen_manifest())
    path = str(tmp_path / "frozen.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    assert T.check_frozen(path)[0] is True

    with open(path, encoding="utf-8") as f:
        edited = json.load(f)
    victim = next((c for c in edited["sets"]["formal"] if c["events"]), None)
    assert victim is not None, "no formal case declares a disturbance to delete"
    victim["events"] = []                          # delete a declared disturbance
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edited, f, ensure_ascii=False, indent=1, sort_keys=True)
    ok, msg = T.check_frozen(path)
    assert ok is False and "self-inconsistent" in msg, msg


def test_a_task_edit_after_a_result_is_visible_as_drift(tmp_path, monkeypatch):
    """The gate must also catch the honest mistake: the file is internally
    consistent, the *code* changed underneath it."""
    path = str(tmp_path / "frozen.json")
    T.dump_frozen(path)
    assert T.check_frozen(path)[0] is True
    monkeypatch.setattr(T, "IMPULSE_PIN_TO_WALL", [-0.09, 0.0])
    ok, msg = T.check_frozen(path)
    assert ok is False and "drift" in msg, msg


def test_a_missing_file_is_reported_as_missing():
    ok, msg = T.check_frozen("/nonexistent/frozen_tasks.json")
    assert ok is False and "no frozen manifest" in msg


def test_refreezing_a_list_the_code_has_drifted_from_is_an_explicit_act(tmp_path, monkeypatch,
                                                                        capsys):
    """`freeze` is the only writer of the pre-registration, so it must not also be a
    way to lose it.

    A result has to stay traceable to the task list it was produced on, and a
    command that silently re-hashes a drifted file makes an old run's manifest point
    at tasks that were never run. The refusal is the difference between "the list was
    amended" and "the list changed, somewhere".
    """
    from embodied_agent import cli

    path = str(tmp_path / "frozen.json")
    assert cli.main(["freeze", "--out", path]) == cli.EXIT_OK
    assert T.check_frozen(path)[0] is True
    with open(path, encoding="utf-8") as f:
        before = json.load(f)["sha256"]

    monkeypatch.setattr(T, "IMPULSE_PIN_TO_WALL", [-0.09, 0.0])      # the code drifts
    assert cli.main(["freeze", "--out", path]) == cli.EXIT_CONFIG_ERROR
    assert T.check_frozen(path)[0] is False, "the refused write changed the file anyway"
    assert "refusing to overwrite" in capsys.readouterr().out

    assert cli.main(["freeze", "--out", path, "--amend"]) == cli.EXIT_OK
    with open(path, encoding="utf-8") as f:
        after = json.load(f)
    assert T.check_frozen(path)[0] is True
    assert '"amended_from"' in capsys.readouterr().out, "the amendment printed no origin"
    assert after["sha256"] != before


# ------------------------------------------------------- what may be authored --


def test_every_case_is_identifiable_and_declares_a_known_outcome():
    ids = [c.task_id for c in _all_cases()]
    assert len(ids) == len(set(ids)), "two cases share an id: results cannot be attributed"
    for c in _all_cases():
        assert c.expected in {"success", "needs_clarification", "bounded_exit",
                             "goal_truth_mismatch"}, c.task_id
        assert c.probe == "" or c.probe in PROBES, (c.task_id, c.probe)
        assert c.utterance.strip(), c.task_id
        assert c.targets and set(c.targets) <= {o["entity_id"] for o in c.objects}
        assert {a.entity_id for a in c.eval_spec.assignments} == set(c.targets)
        for t in {a.target_id for a in c.eval_spec.assignments}:
            assert t in T.TRAYS, (c.task_id, t)


def test_no_case_asks_for_more_than_a_region_can_hold():
    """SPEC 11.1: the list must be valid before it is frozen. A region asked to
    hold more objects than its descent envelope allows measures nothing."""
    for c in _all_cases():
        counts: dict[str, int] = {}
        for a in c.eval_spec.assignments:
            counts[a.target_id] = counts.get(a.target_id, 0) + 1
        assert max(counts.values()) <= T.MAX_OBJECTS_PER_REGION, (c.task_id, counts)


def test_a_disturbance_that_needs_a_cube_says_so_rather_than_becoming_a_no_op():
    """`_displaceable_in` is realizable on one body shape; a case that declares the
    mechanism without that shape is an invalid task, not an episode that quietly
    fires nothing."""
    with pytest.raises(ValueError):
        T._displaceable_in("tray_left", [{"entity_id": "o", "attributes": {"shape": "cylinder"}}],
                           {"o": "tray_left"})
    for c in _all_cases():
        for e in c.events:
            if e.action is T.EventAction.IMPULSE and e.entity_id and e.target_id:
                assert c.objects and e.entity_id in {o["entity_id"] for o in c.objects}
                shape = next(o["attributes"]["shape"] for o in c.objects
                             if o["entity_id"] == e.entity_id)
                assert shape == "cube", (c.task_id, e.entity_id, shape)


def test_fresh_events_carry_no_history_of_the_previous_episode():
    """`fired` is episode state living on a frozen declaration that drives many
    episodes; sharing it would let the first episode consume the event and spare
    every later one."""
    case = T.find_case("dev_c2")
    assert case.events, "dev_c2 no longer declares a disturbance to test with"
    first, second = case.fresh_events(), case.fresh_events()
    assert first[0] is not second[0] and first[0] is not case.events[0]
    first[0].fired = True
    first[0].matches_seen = 7
    assert second[0].fired is False and second[0].matches_seen == 0
    assert case.events[0].fired is False, "the frozen declaration absorbed episode state"


def test_the_state_utilization_pairs_are_frozen_before_any_model_sees_them():
    """SPEC 11.3: 12 pairs, each with an acceptable-action *set* defined from the
    public preconditions, never a single answer string.

    What is checked here is the shape of the specification, not its content: a pair
    whose labels do not name states of its own declared axis, or whose accept set
    overlaps its reject set for one state, cannot score anything a reader can trust."""
    pairs = T.STATE_PAIR_SPECS
    assert len(pairs) == 12
    assert len({p["pair_id"] for p in pairs}) == len(pairs)
    known = {c.task_id for name in SETS for c in build_set(name)}
    differences = set()
    for p in pairs:
        assert p["base_case"] in known, p
        assert p["difference"] and p["note"].strip(), p
        differences.add(p["difference"])
        for kind in ("accept", "reject"):
            assert isinstance(p[kind], dict) and p[kind], (p["pair_id"], kind)
            for state, actions in p[kind].items():
                assert state == "any" or state in p["difference"], \
                    f"{p['pair_id']}: state label {state!r} is not a state of " \
                    f"{p['difference']!r}"
                assert actions and all(isinstance(a, str) and a for a in actions), p
                assert len(actions) == len(set(actions)), p
        for state in set(p["accept"]) & set(p["reject"]):
            clash = set(p["accept"][state]) & set(p["reject"][state])
            assert not clash, f"{p['pair_id']}/{state}: {clash} is both required and forbidden"
    assert len(differences) >= 8, "the pairs are not sampling distinct state axes"
    # a model that always takes the first candidate must be caught by a pair that
    # changes nothing but presentation order (SPEC 11.1: several legal orders)
    order = [p for p in pairs if p["difference"] == "candidate_order_permuted"]
    assert order, "no candidate-order control"
    assert any("any" in p["accept"] for p in order), \
        "the order control must accept either feasible candidate"


def test_the_legacy_yaml_suites_are_kept_but_no_longer_read():
    """SPEC 2.1: the working tree is preserved, the experiment is not run from it.
    The files stay on disk; nothing in the product opens them."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(T.__file__)))   # embodied_agent/
    cfg = os.path.join(root, os.pardir, "configs", "tasks")
    assert os.path.isdir(cfg), cfg
    present = set(os.listdir(cfg))
    for name in ("v2_clean.yaml", "v2_suite_config.yaml"):
        assert name in present, f"the pre-P0 suite {name} was deleted rather than parked"

    hits = []
    for dirpath, _dirs, files in os.walk(os.path.join(root, "embodied_agent")):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                text = f.read()
            if "v2_clean" in text or "v2_suite_config" in text or "tasks/v2" in text \
                    or "configs/tasks" in text:
                hits.append(fn)
    assert not hits, f"product code still reads a legacy task file: {hits}"


# ---------------------------------------------------------- the set names ------


def test_every_set_name_resolves_to_cases_and_the_union_counts_each_once():
    """`--set` used to offer `all`, which the task list had no builder for: a name
    a caller can type and the code cannot resolve is a run that measures nothing."""
    assert set(T.SET_NAMES) == set(T._SET_BUILDERS)
    for name in T.SET_NAMES:
        cases = build_set(name)
        assert cases, f"{name} resolves to no cases at all"
        assert len({c.task_id for c in cases}) == len(cases), f"{name} repeats a case id"

    everything = build_set("all")
    assert len(everything) == len({c.task_id for n in T._REGISTERED_SETS
                                   for c in build_set(n)})
    assert [c.task_id for c in everything[:len(build_set('smoke'))]] == \
        [c.task_id for c in build_set("smoke")], "`all` is built from the registered sets in order"
    # the subsets are slices of `formal`, not separate declarations that may drift
    formal = {c.task_id: c for c in build_set("formal")}
    for name in ("clean", "state_change", "execution_deviation"):
        subset = [c for c in formal.values() if c.subset == name]
        assert [c.task_id for c in build_set(name)] == [c.task_id for c in subset], name


def test_a_name_the_task_list_cannot_resolve_is_a_refusal_not_a_crash(capsys):
    from embodied_agent import cli

    assert cli.main(["run", "--task", "nonsense:clean_c1"]) == cli.EXIT_CONFIG_ERROR
    assert "choose from" in capsys.readouterr().err
    # the parser rejects an unknown arm, and the factory behind it does too: a kind
    # that fell through would reach the online planner and spend requests nobody
    # asked for
    with pytest.raises(SystemExit):
        cli.main(["run", "--task", "smoke:smoke_clean", "--planner", "gpt"])
    with pytest.raises(ValueError, match="unknown planner kind"):
        cli._planner_for("gpt")
