"""Reproducible entry points (SPEC 8 file map: cli.py 暴露规划模式与实验入口).

The commands are the ones the experiment is actually run with, and each one is a
thin shell over a module that could be called from a test:

  doctor       environment precheck (PyBullet backend)
  sim-smoke    scene load, 1000 stable steps, one frame
  run          one episode, one arm: --mode A|B|C
  evaluate     a frozen set x arms x repeats through the shared base
  report       statistics and attribution for a finished run directory
  replay       the two *different* things replay can mean (SPEC 7 naming):
                 --recorded  read back the logged trajectory, no physics
                 --plan      re-execute the episode's last plan in a fresh scene
  freeze       write or check the task-list hash a run must match
  prereg       write or check the pre-registration (matrix, prompts, budgets,
               sampling, statistics, gates) a batch must match
  blind-review the SPEC 11.4 sheet of contested items, and the human verdicts on it
  llm          the model exchanges recorded for one episode

There is deliberately no `--recovery` switch. Whether to retry, re-plan or stop
is the model's decision; a CLI flag that made that choice for it would be the
runtime taking over the thing under measurement (SPEC 2, 11.2).

Exit codes (implementation contract):
  0 success | 1 task failure | 2 needs clarification | 3 config error | 4 infrastructure error
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EXIT_OK = 0
EXIT_TASK_FAILED = 1
EXIT_NEEDS_CLARIFICATION = 2
EXIT_CONFIG_ERROR = 3
EXIT_INFRA_ERROR = 4

# What `--planner` accepts, in one place, and checked by `_planner_for`: the three
# arms differ only in who decides, so an unrecognized kind must be refused rather
# than fall through to the online planner and spend requests nobody asked for.
PLANNER_KINDS = ("rule", "fixture", "deepseek")


def _load_dotenv():
    from .adapters.deepseek import strip_env_quotes  # same rule as the adapter's own reader

    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), strip_env_quotes(v.strip()))


def cmd_doctor(args) -> int:
    """Environment report; reuses the W0 precheck rather than restating it."""
    script = os.path.join(os.path.dirname(__file__), "..", "work", "w0_precheck.py")
    if args.backend != "pybullet":
        print(f"unknown backend {args.backend}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    rc = os.system(f"{sys.executable} {script} > /dev/null 2>&1")
    report = os.path.join(os.path.dirname(__file__), "..", "outputs", "w0_precheck", "precheck.json")
    if not os.path.exists(report):
        print("infrastructure error: precheck produced no report", file=sys.stderr)
        return EXIT_INFRA_ERROR
    with open(report, encoding="utf-8") as f:
        data = json.load(f)
    for name, check in data["checks"].items():
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {name}: {check['detail']}")
    print("doctor:", "OK" if data["all_ok"] else "FAILED")
    return EXIT_OK if data["all_ok"] and rc == 0 else EXIT_INFRA_ERROR


def cmd_sim_smoke(args) -> int:
    try:
        from .core.scene import PhysicsScene

        scene = PhysicsScene(seed=0, gui=args.gui)
        scene.settle(1.0)
        out = os.path.join("outputs", "sim_smoke.png")
        scene.render(out)
        scene.close()
        print(f"sim-smoke OK, frame -> {out}")
        if args.gui:
            # pybullet's GUI example browser segfaults during interpreter
            # teardown, after all work is done
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(EXIT_OK)
        return EXIT_OK
    except Exception as e:  # noqa: BLE001
        print(f"sim-smoke infrastructure error: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR


def _planner_for(kind: str, model_config: str | None = None):
    """None is the deterministic rule interpreter, and is labelled as such —
    it is never reported as a model result (SPEC 11.3)."""
    if kind not in PLANNER_KINDS:
        raise ValueError(f"unknown planner kind {kind!r}; choose from {PLANNER_KINDS}")
    if kind == "rule":
        return None
    _load_dotenv()
    if kind == "fixture":
        from .adapters.deepseek import FixturePlanner

        return FixturePlanner()
    from .adapters.deepseek import DeepSeekPlanner

    return DeepSeekPlanner.from_env(model_config)


def _add_perception_flags(parser) -> None:
    """`--perceive` is the arm switch (SPEC-v0.2 §9, §13 P1-e), on both run entry points.

    It is a flag on *both* `run` and `evaluate` and defaults to `privileged` on both, so the
    sealed v0.1 invocation is unchanged and the new arm has to be asked for by name. The
    coherence rule — a `wo_vlm` episode may file no `perception` record, a `full` episode must
    consult a model — is enforced in `perception/grounding.arm_coherence` and re-checked by
    `build_arm`, not here; this function only refuses an unknown name early enough to print it
    as a config error rather than as a traceback."""
    from .perception.grounding import PERCEIVE_CHANNELS

    parser.add_argument("--perceive", default="privileged", metavar="CHANNEL",
                        choices=list(PERCEIVE_CHANNELS),
                        help="which observation channel the loop runs on: privileged is the "
                             "v0.1 path (the loop reads the simulator's own inventory, no "
                             "camera is rendered); stub reads the same PNG by deterministic "
                             "colour segmentation and consults no vision model, which is "
                             "9's 'limited semantic baseline'; vlm asks a vision model one "
                             "question per look and spends real money doing it")
    parser.add_argument("--ablation", default=None, metavar="CONDITION",
                        help="which 9 arm this batch claims. On `privileged` that means the "
                             "planning arms (`full`, `wo_planning`, `wo_working_memory`, "
                             "`wo_replanning`), the memory arm (`wo_episodic_memory`, which needs "
                             "`--experience-store`) and the acquisition arm "
                             "(`wo_skill_acquisition`, which needs `--skill-memory`): in both cases "
                             "the contrast is a module switched off on a loop that has one, not a "
                             "loop built without one; `wo_vlm` there is the baseline. Defaults to "
                             "the one the chosen channel can carry. Refused if the pair is "
                             "incoherent, because an arm whose records contradict its own switch "
                             "is not a measurement")
    parser.add_argument("--policy", default=None, metavar="POLICY",
                        choices=["rule", "payload", "memory"],
                        help="which deterministic control answers mode B's rounds. `rule` is the "
                             "v0.1 policy and the default: it reads pending goals and the "
                             "measured hold state, never a plan, so switching the plan off "
                             "changes nothing it can see. `payload` is planning.policy.PlanPolicy, "
                             "which reads only ctx.model_payload() — the page a model would read — "
                             "so the arms differ by what is offered and not by who is reading it. "
                             "`memory` is episodic.policy.MemoryPolicy: the same page, and it may "
                             "reorder the ready rows by the object sequence an unrefuted recalled "
                             "experience recorded, which is the smallest thing that can show §5.4 "
                             "having an effect. Needs --modes B and --planner rule")
    parser.add_argument("--experience-store", default=None, metavar="PATH",
                        help="the JSONL file §5.4's episodic memory reads and appends to. A "
                             "missing file is a cold start, which is a measured condition; an "
                             "absent flag on a memory arm is not, and is refused. Give the seed "
                             "store its batch wrote, or a copy of one, so the run's store state "
                             "is nameable by fingerprint in the manifest")
    parser.add_argument("--skill-memory", default=None, metavar="PATH",
                        help="the JSONL file §5.6's skill library admits into and offers from. "
                             "Installing one is what makes `--ablation wo_skill_acquisition` a gate "
                             "rather than an absence. A missing file is a cold start, which is a "
                             "measured condition and is reported as one: every offer in the batch "
                             "is then empty and the reuse rows have no program to match. A file "
                             "that does not parse, or holding a line that did not pass the "
                             "admission gate, is refused here rather than half-way through a "
                             "batch, because a hand-written library line is the exact thing the "
                             "gate exists to make impossible")
    parser.add_argument("--propose", default="rule", metavar="PROPOSER",
                        choices=["rule", "model"],
                        help="who writes the parameterized program in §5.6's third box. `rule` is "
                             "the zero-spend control — acquisition.propose.rule_candidate builds a "
                             "template from the gap's own effect and the primitives' argument "
                             "tables — and is the default. `model` asks a model for the program "
                             "instead, which bills, needs a caller-supplied asker the CLI does not "
                             "wire, and stops the run being the control the rest of v0.2 measures")
    parser.add_argument("--validation-budget", type=int, default=1, metavar="N",
                        help="how many of an episode's candidates may be validated (§5.6's fifth "
                             "box) before the budget is spent: each validation is four sandbox runs "
                             "on a fresh physics scene, so this is the batch's simulator-time "
                             "dial. Candidates past the limit are recorded as `not_validated` "
                             "rather than dropped, and a skill is never admitted on the strength of "
                             "a run it did not have")
    parser.add_argument("--view", default="main", metavar="VIEW",
                        help="the camera the loop starts at. A default, not a policy: which "
                             "view to look with *next* is the model's `observe` argument, "
                             "because choosing it is the capability under test (3.4)")
    parser.add_argument("--views", default=None, metavar="V1,V2",
                        help="restrict this arm to a subset of the declared cameras. An "
                             "`observe` naming a view outside it is a structured rejection")


def _perception_kwargs(args) -> tuple[dict, list[str]]:
    """(the kwargs for the runner, the reasons this request cannot run).

    The refusal is a list rather than an exception because a CLI answer has to name every
    reason at once: an operator who fixes the first and re-runs into the second learns nothing
    about how the gate works."""
    from .core.v02 import ABLATION_CONDITIONS, ablation as ablation_record
    from .perception.grounding import arm_coherence, channel_readiness, declared_views

    views = None
    if getattr(args, "views", None):
        try:
            views = declared_views(tuple(v for v in args.views.split(",") if v.strip()))
        except ValueError as e:
            return {}, [str(e)]
    if args.view not in (views or declared_views()):
        return {}, [f"--view {args.view!r} is not a camera this arm has: "
                    f"{sorted(views or declared_views())}"]
    kw = {"perceive": args.perceive, "views": views, "default_view": args.view}
    policy = getattr(args, "policy", None)
    store_path = getattr(args, "experience_store", None) or None
    if store_path:
        from .episodic.store import ExperienceStore

        try:
            # `load` on a missing file is an empty store with this path attached, which is §5.4's
            # cold start; a file that exists and does not parse, or that carries another schema
            # generation, raises here rather than half-way through a batch.
            kw["experience_store"] = ExperienceStore.load(store_path)
        except ValueError as e:
            return {}, [f"--experience-store {store_path!r} cannot be read: {e}"]
    library_path = getattr(args, "skill_memory", None) or None
    proposer = getattr(args, "propose", None) or "rule"
    budget = getattr(args, "validation_budget", 1)
    if proposer == "model":
        # Refused at the entry, before the library is even opened, because this flag is the one that
        # turns a zero-spend arm into a billed one: `acquisition.propose.model_candidate` needs an
        # `ask` callable, the CLI wires none, and a proposal drawn from a model is also the reason
        # §9's `w/o Skill Acquisition` contrast stops being a comparison of modules. Library callers
        # can still pass `propose_ask=` to `run_group`, which is where a spend becomes someone's
        # deliberate act with a provider named in the manifest.
        return {}, ["--propose model is not wired to the CLI: the asker is a caller-supplied "
                    "callable (evaluation.run.run_group(propose_ask=...)), and this entry point "
                    "spends nothing. A model-drawn proposal is also a decision, so the run would "
                    "no longer measure the module and only the module"]
    if budget is not None and int(budget) < 0:
        return {}, [f"--validation-budget {int(budget)} is not a budget: use 0 to propose and never "
                    f"validate (every candidate then survives as `not_validated`), or N to spend "
                    f"four sandbox runs on each of the first N"]
    if library_path:
        from .acquisition.memory import SkillMemory

        try:
            # `load` on a missing file is an empty library with this path attached — §5.6's cold
            # start, which the manifest reports as one. A file that exists and does not parse, or
            # that holds a line whose own admission says the gate did not pass, raises here: the
            # alternative is a batch that admits programs beside the gate and calls the store a
            # measurement.
            kw["skill_memory"] = SkillMemory.load(library_path)
        except ValueError as e:
            return {}, [f"--skill-memory {library_path!r} cannot be read: {e}"]
        kw["propose"] = proposer
        kw["validation_budget"] = int(budget)
    if policy in ("payload", "memory"):
        # Refused at the entry, in the same list as the arm refusals, because `--policy` replaces
        # mode B's decision maker: asked for alongside mode A's one-shot plan, mode C's ablated
        # feedback or a model planner, it would silently mean something else instead.
        requested = (getattr(args, "modes", None) or getattr(args, "mode", None) or "")
        modes = [m.strip().upper() for m in str(requested).split(",") if m.strip()]
        planner_kind = getattr(args, "planner", None)
        reasons = ([] if not modes or set(modes) == {"B"} else
                   [f"--policy {policy} replaces mode B's control policy; the batch asked for "
                    f"mode(s) {modes}"]) + \
                  ([] if planner_kind in (None, "rule") else
                   [f"--policy {policy} is the zero-spend control; --planner {planner_kind} "
                    f"brings its own decision maker"])
        if policy == "memory" and not store_path:
            reasons.append("--policy memory needs --experience-store: with no store the runtime "
                           "has no `recalled` section on the page, so the run would be "
                           "planning.policy.PlanPolicy answering under a second name")
        if reasons:
            return {}, reasons
    if policy:
        kw["policy"] = policy
    if args.perceive == "privileged":
        if args.ablation in (None, "wo_vlm"):
            # `wo_vlm` is accepted and then *dropped*. A privileged world consulted no vision
            # model to begin with, so the switch has nothing to turn off: the run is the sealed
            # v0.1 baseline. Passing the record through would have moved the episode into a
            # planning arm and renamed the baseline, which is a meaning change nobody asked for.
            return kw, []
        from .acquisition.arm import SKILL_ARMS
        from .episodic.arm import EPISODIC_ARMS
        from .planning.arm import PLANNING_ARMS

        if args.ablation not in ABLATION_CONDITIONS:
            return {}, [f"unknown ablation condition {args.ablation!r}; registered: "
                        f"{sorted(ABLATION_CONDITIONS)}"]
        if args.ablation not in SKILL_ARMS:
            return {}, [f"--perceive privileged cannot carry --ablation {args.ablation}: the "
                        f"arms this channel can mean are the planning, memory and acquisition "
                        f"ones {list(SKILL_ARMS)} (9's wo_vlm belongs to a camera channel, where "
                        f"the vision model is the thing being switched off)"]
        if args.ablation in EPISODIC_ARMS and args.ablation not in PLANNING_ARMS and not store_path:
            return {}, [f"--ablation {args.ablation} needs --experience-store: the arm switches "
                        f"§5.4's module off *an installed arm*, and a loop built with no store at "
                        f"all takes a different code path that merely happens to behave the same"]
        if args.ablation == "wo_skill_acquisition" and not library_path:
            # `not in PLANNING_ARMS` above is the memory arm's rule and deliberately not this one:
            # §5.6's module has nothing to do with §5.4's, and the installed thing being switched
            # off here is the library.
            return {}, [f"--ablation {args.ablation} needs --skill-memory: the arm switches §5.6's "
                        f"pipeline off *an installed library*, and a loop built with no library at "
                        f"all takes a different code path that merely happens to offer nothing"]
        arm = ablation_record(args.ablation)
        # `arm_coherence` is not consulted on this branch, and that is not an oversight: both of
        # its rules are about the `perception` record and the vision model, and no planning arm
        # turns the model off, so applying them here would refuse every arm this flag exists to
        # request. The asymmetry the check would have reported is instead said out loud — by the
        # arm in each episode's `ablation` record, and by the run manifest's `note`.
        kw["ablation"] = arm
        return kw, []
    condition = args.ablation or ("full" if args.perceive == "vlm" else "wo_vlm")
    if condition not in ABLATION_CONDITIONS:
        return {}, [f"unknown ablation condition {condition!r}; registered: "
                    f"{sorted(ABLATION_CONDITIONS)}"]
    arm = ablation_record(condition)
    model_config = getattr(args, "model_config", None)
    refusals = [f"--perceive {args.perceive} cannot carry --ablation {condition}: " + r
                for r in arm_coherence(arm, args.perceive)]
    # `config_path=model_config` is the fix for a gate that was asked a question it could not
    # answer: called with one argument this call had `adapter=None` for every run, so
    # `--perceive vlm` was refused whatever `--model-config` said, while the sentence it
    # returned told the operator to pass one. The config is consulted now, and the sentence
    # names the config that was actually read.
    refusals += channel_readiness(args.perceive, config_path=model_config)
    # §13 P5: the store and the library are now runnable on a camera channel — the perceiver
    # `build_arm` used to build privately is available as `build_perceiver`, and both stacked
    # builders take one. What the entry still refuses is the two ways a joined batch could report
    # one thing under two names: a gate on a module nobody installed (the runner's own rule, shared
    # here so an operator learns it before 40 frames are rendered), and a channel that cannot carry
    # the arm's claim (`arm_coherence` above).
    from .evaluation.run import installed_module_refusals

    refusals += installed_module_refusals(condition, experience_store=store_path,
                                  skill_memory=library_path)
    if refusals:
        return {}, refusals
    kw["ablation"] = arm
    return kw, []

def cmd_run(args) -> int:
    from .evaluation.run import goal_logger, resolve_goal, run_one_episode
    from .evaluation.tasks import SET_NAMES, build_set

    suite, _, case_id = args.task.partition(":")
    if suite not in SET_NAMES:
        print(f"config error: unknown set {suite!r} in --task {args.task!r}; "
              f"choose from {SET_NAMES}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    cases = [c for c in build_set(suite) if not case_id or c.task_id == case_id]
    if len(cases) != 1:
        print(f"config error: {args.task!r} selects {len(cases)} cases; run takes exactly one",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        planner = _planner_for(args.planner, args.model_config)
    except Exception as e:  # noqa: BLE001 - a missing key is a config problem
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    case = cases[0]
    arm_kwargs, refusals = _perception_kwargs(args)
    if refusals:
        print("config error:\n  " + "\n  ".join(refusals), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        resolution = resolve_goal(case, args.repeat, planner, args.out,
                                  log_call=goal_logger(args.out))
        if resolution.error is not None or resolution.goal is None:
            print(f"goal resolution failed: {resolution.error}", file=sys.stderr)
            return EXIT_INFRA_ERROR
        summary = run_one_episode(case, args.repeat, args.mode, resolution, planner, args.out,
                                  suite, frames=not args.no_frames, **arm_kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    r = summary["result"]
    print(json.dumps({"episode_id": summary["episode_id"],
                      "dir": os.path.join(args.out, "episodes", summary["episode_id"]),
                      "perception": summary.get("perception"),
                      "terminal_status": r["terminal_status"],
                      "failure_type": r["failure_type"],
                      "independent_complete_success": summary["score"]["complete_success"],
                      "objects_completed": r["objects_completed"],
                      "objects_total": r["objects_total"],
                      "decision_rounds": r["decision_rounds"],
                      "skill_calls": r["skill_calls"],
                      "http_requests": r["http_requests"]}, ensure_ascii=False, indent=1))
    if r["terminal_status"] == "needs_clarification":
        return EXIT_NEEDS_CLARIFICATION
    return EXIT_OK if r["terminal_status"] == "success" else EXIT_TASK_FAILED


def cmd_evaluate(args) -> int:
    from .evaluation.run import InfraError, run_group
    from .evaluation.tasks import SET_NAMES

    if args.set not in SET_NAMES:
        print(f"config error: unknown set {args.set!r}; choose from {SET_NAMES}",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    modes = tuple(m.strip().upper() for m in args.modes.split(",") if m.strip())
    arm_kwargs, refusals = _perception_kwargs(args)
    if refusals:
        print("config error:\n  " + "\n  ".join(refusals), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        out = run_group(args.set, modes=modes, planner_kind=args.planner, repeats=args.repeats,
                        out_root=args.out_root,
                        case_ids=[c for c in args.cases.split(",") if c.strip()] or None,
                        limit=args.limit, frames=not args.no_frames,
                        model_config=args.model_config, frozen_path=args.frozen,
                        prereg_path=args.prereg, **arm_kwargs)
    except InfraError as e:
        # a pre-flight refusal: nothing was measured, and the reason is stated
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    # `run_group` prints the run summary itself; the exit code only says whether
    # the measurement happened, not what it showed (SPEC 11.5).
    infra = out["outcomes"].get("infrastructure_error", 0) + \
        out["outcomes"].get("goal_resolution_error", 0)
    if infra:
        print(f"{infra} runs could not be measured at all (see errors.jsonl); "
              f"they stay in the denominator (SPEC 11.5)", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def cmd_report(args) -> int:
    from .evaluation.report import render_markdown, write_report

    if not os.path.isdir(args.run_dir):
        print(f"config error: no run directory at {args.run_dir}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        report = write_report(args.run_dir)
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json_only
                  else render_markdown(report))
    return EXIT_OK


def cmd_replay(args) -> int:
    from .evaluation.run import InfraError, plan_rerun, recorded_replay

    try:
        if args.recorded:
            recorded_replay(args.run_dir)
        else:
            plan_rerun(args.run_dir, args.set)
    except (InfraError, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except Exception as e:  # noqa: BLE001
        print(f"infrastructure error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def cmd_freeze(args) -> int:
    from .evaluation.tasks import check_frozen, dump_frozen, frozen_manifest

    if args.check:
        ok, msg = check_frozen(args.check)
        print(("frozen list matches: " if ok else "MISMATCH: ") + msg)
        return EXIT_OK if ok else EXIT_CONFIG_ERROR
    live = frozen_manifest()
    previous = None
    if os.path.exists(args.out):
        ok, msg = check_frozen(args.out)
        if not ok:
            # This file is the pre-registration: writing over a list that no longer
            # matches the code is how a result stops being traceable to the tasks it
            # was produced on. Say what changed and require the amendment by name.
            with open(args.out, encoding="utf-8") as f:
                previous = str(json.load(f).get("sha256", ""))
            if not args.amend:
                print(f"refusing to overwrite {args.out}: {msg}\n"
                      f"live list hashes to {live['sha256']}. Re-freeze with --amend "
                      f"only as a recorded change to the pre-registered task set.")
                return EXIT_CONFIG_ERROR
    path = dump_frozen(args.out)
    out = {"written": path, "sha256": live["sha256"],
           "cases": {k: len(v) for k, v in live["sets"].items()}}
    if previous:
        out["amended_from"] = previous
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_prereg(args) -> int:
    """SPEC 9 P3: write the pre-registration, or check the code against it.

    Nothing here selects a gate, a rubric, an arm set or a budget: every value in
    the file is read out of the code that will act on it, so this command can only
    record what the code currently says, or name the fields where it stopped saying
    it (SPEC 12.2: 门槛正式前冻结, 事后不调低).

    Exit codes: 0 = the file and the code agree, 3 = they do not, or the file is not
    a usable pre-registration."""
    from .evaluation.preregistration import (PREREG_PATH, check_invariants, check_prereg,
                                             dump_prereg, load_prereg, preregistration)

    if args.check:
        ok, msg = check_prereg(args.check)
        print(("pre-registration matches the code: " if ok else "MISMATCH: ") + msg)
        return EXIT_OK if ok else EXIT_CONFIG_ERROR

    live = preregistration()
    previous = detail = None
    if os.path.exists(args.out):
        ok, msg = check_prereg(args.out)
        if not ok:
            # Re-freezing over a file the code has drifted from is the one way this
            # experiment could silently change its own rules, so it takes an explicit
            # amendment and prints the hash it replaces.
            previous = str(load_prereg(args.out).get("rules_sha256", ""))
            detail = msg
            if not args.amend:
                print(f"refusing to overwrite {args.out}: {msg}\n"
                      f"the code now freezes to {live['rules_sha256']}. Re-freeze with "
                      f"--amend only as a recorded change, and name what moved in the "
                      f"run's own notes.")
                return EXIT_CONFIG_ERROR
    path = dump_prereg(args.out, previous_rules_sha256=previous)
    m = live["rules"]["run_matrix"]
    out = {"written": path, "prereg_id": live["rules"]["prereg_id"],
           "rules_sha256": live["rules_sha256"],
           "matrix": {"set": m["set"], "cases": m["cases"], "modes": m["modes"],
                      "repeats": m["repeats"], "episodes": m["episodes"],
                      "shared_goal_parses": m["shared_goal_parses"]},
           "gates": [f"{g['id']} {g['metric']} {g['op']} {g['threshold']}"
                     for g in live["rules"]["gates"]["items"]],
           "task_list_sha256": live["rules"]["task_list"]["sha256"],
           "rubric_sha256": live["rules"]["blind_review"]["sha256"],
           "invariant_problems": check_invariants(live["rules"])}
    if previous:
        out["amended_from"] = previous
        out["amendment_reason_recorded"] = detail
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_state_util(args) -> int:
    """SPEC 11.3/11.4 状态分叉动作适配率: one request per forked context, scored.

    Nothing is executed here — the contexts come from real episodes, the answers
    are only *read*. A low rate is a result, so it exits 0; an unreachable pair is
    a broken measurement, so it does not.
    """
    from .evaluation.sources import LLMDecisionSource, RulePolicySource
    from .evaluation.state_utilization import build_forks, run_state_utilization

    try:
        planner = _planner_for(args.planner, args.model_config)
    except Exception as e:  # noqa: BLE001 - a missing key is a config problem
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    only = [p for p in (args.only or []) if p.strip()] or None
    forks, _ = build_forks(only=only)
    if not forks:
        print("config error: no fork reached any pair", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    out_root = args.out or os.path.join("runs", f"state_util_{args.planner}")
    call_log = os.path.join(out_root, "model_calls.jsonl")
    # a source per context keeps the forks independent (SPEC 11.3), so the planner
    # is captured once and each answer is a fresh request; the same adapter log an
    # episode run writes, so the diagnostic's HTTP cost is auditable in the same way
    def log_call(payload, latency):
        with open(call_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({**payload, "latency_s": round(latency, 3)},
                               ensure_ascii=False, default=str) + "\n")

    def source_factory():
        if planner is None:
            return RulePolicySource()
        return LLMDecisionSource(planner, log=log_call)

    payload = run_state_utilization(source_factory, out_root=out_root, label=args.planner,
                                    only=only, forks=forks)
    s = payload["summary"]
    print(json.dumps({"written": os.path.join(out_root, "state_utilization.json"),
                      "policy": s["policy_label"], "provider": s["policy_provider"],
                      "action_fit_rate": s["action_fit_rate"],
                      "contexts": f'{s["contexts_fitted"]}/{s["contexts_requested"]}',
                      "pairs_with_both_arms_fitting":
                          f'{s["pairs_with_both_arms_fitting"]}/{s["pairs_assessed"]}',
                      "pairs_that_did_not_use_the_distinction":
                          s["pairs_that_did_not_use_the_distinction"],
                      "candidate_choice_not_tested_in":
                          s["pairs_where_candidate_choice_went_untested"],
                      "synthetic_arms": s["synthetic_arms"],
                      "provider_counters": s["provider_counters"],
                      "unreachable_pairs": s["unreachable_pairs"]},
                     ensure_ascii=False, indent=1))
    if s["unreachable_pairs"]:
        print(f"incomplete: {len(s['unreachable_pairs'])} frozen pair(s) reached no context; "
              "the rate above is not the 12-pair diagnostic", file=sys.stderr)
        return EXIT_INFRA_ERROR
    return EXIT_OK


def cmd_vlm_contrast(args) -> int:
    """SPEC-v0.2 §11/§13 P1-f: the privileged-vs-VLM 口径, then what it measures.

    The batteries render frames and read the hidden answer key — the one place a privileged
    snapshot is *allowed*, because it is the comparison and not the agent — and they execute no
    skill. A low rate is a result, so it exits 0; a case set that resolves to nothing is a
    broken measurement, so it does not. `--definitions` prints the 口径 and computes nothing,
    which is the order the module is written in: `--definitions` before any number.
    """
    from .evaluation.vlm_contrast import (DEFINITION_VERSION, METRIC_DEFINITIONS,
                                          ContrastConfig, episode_contrast, measure_set,
                                          render_markdown, write)

    if args.definitions:
        print(json.dumps({"version": DEFINITION_VERSION,
                          "definitions": METRIC_DEFINITIONS}, ensure_ascii=False, indent=1))
        return EXIT_OK
    only = [c for c in (args.cases or []) if c.strip()] or None
    try:
        if args.contrast:
            result = episode_contrast(args.contrast_set, case_ids=only, out_root=args.out,
                                      modes=list(args.contrast_modes),
                                      perceive=args.perceive, frames=not args.no_frames)
            name = f"episode_contrast_{args.contrast_set}"
            summary = {"arms": {arm: b["episodes"] for arm, b in result["arms"].items()},
                       "cases": result["cases"],
                       "paired_cases": result["paired_cases"],
                       "unpaired": result["unpaired"],
                       "not_comparable": result["not_comparable"]}
        else:
            result = measure_set(args.set, case_ids=only, out_dir=args.out,
                                 batteries=list(args.batteries), verbose=not args.quiet)
            name = f"contrast_{args.set}"
            summary = {"coverage": result["coverage"],
                       "metrics": {k: f"{r['numerator']}/{r['denominator']}"
                                   for k, r in result["metrics"].items()}}
    except ContrastConfig as e:
        # only a *configuration* fault reaches here: an arithmetic error inside the module must
        # surface as a traceback, not as a message that blames the caller's arguments
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    path = write(result, args.out, name)
    print(json.dumps({"written": path, "markdown": path[:-5] + ".md",
                      "definition_version": result["definition_version"], **summary},
                     ensure_ascii=False, indent=1))
    if args.markdown:
        print(render_markdown(result))
    return EXIT_OK


def _contested_pointers(run_dir: str) -> list[dict]:
    """The `needs_blind_review` pointers of a finished batch, read from the report
    if one was written and re-derived from the logs otherwise. The two are the same
    deterministic reading; the pointer set must not be a third opinion."""
    path = os.path.join(run_dir, "report.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            behaviour = json.load(f).get("behaviour") or {}
    else:
        from .evaluation.report import behaviour_metrics, load_rows

        behaviour = behaviour_metrics(load_rows(run_dir))
    return [p for arm, v in behaviour.items()
            if arm != "_definitions" and isinstance(v, dict)
            for p in (v.get("needs_blind_review") or [])]


def cmd_blind_review(args) -> int:
    """SPEC 11.4 blind review: print the contested items, or judge them.

    The criteria live in `configs/experiment/` and are *not* selectable here: an
    operator who could pass a different rubric on the command line could move the
    goalposts after the verdicts arrived, which is the one thing the rubric's own
    prohibitions forbid. What is selectable is the human input (`--records`) and
    where the derived artifacts go.

    Exit codes keep the three states a reader must not confuse apart: 0 = measured
    (including "adverse", and including "nothing was contested"), 3 = the criteria or
    the verdict file is not usable as input, 4 = the sheet cannot be built from this
    run directory at all.
    """
    from .evaluation.blind_review import (SHEET_NAME, review_state, write_template)

    if not os.path.isdir(args.run_dir):
        print(f"config error: no run directory at {args.run_dir}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if not os.path.exists(os.path.join(args.run_dir, "episodes.csv")):
        print(f"config error: {args.run_dir} holds no episodes.csv — blind review judges a "
              "finished batch, and an empty run has nothing contested rather than a clean "
              "record", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if args.records and not os.path.exists(args.records):
        print(f"config error: no verdict file at {args.records} (write one from "
              f"'--template <path>')", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        pointers = _contested_pointers(args.run_dir)
    except Exception as e:  # noqa: BLE001 - unreadable artifacts are this command's input
        print(f"config error: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        sheet, state = review_state(args.run_dir, pointers, records_path=args.records)
    except Exception as e:  # noqa: BLE001 - a broken sheet is a finding, not a verdict
        print(f"infrastructure error: the blind sheet could not be built from "
              f"{args.run_dir}: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    rc = EXIT_OK
    if args.sheet:
        out = args.sheet if args.sheet.endswith(".json") else os.path.join(args.sheet, SHEET_NAME)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(sheet, f, ensure_ascii=False, indent=2)
        print(f"sheet -> {out}")
    if args.template:
        os.makedirs(os.path.dirname(os.path.abspath(args.template)), exist_ok=True)
        n = write_template(sheet, args.template)
        print(f"template -> {args.template} ({n} blank rows: {len(sheet['items'])} items x "
              f"{sheet['reviewers']['count']} reviewers; a blank verdict is refused, so "
              f"handing in the form cannot be mistaken for a result)")
        return rc
    print(json.dumps({k: v for k, v in state.items() if k != "prohibitions"},
                     ensure_ascii=False, indent=1, default=str))
    if state.get("refused"):
        print(f"{len(state['refused'])} verdict row(s) refused; they are excluded from the "
              "rates above and the reasons are printed with them — fix the file, do not "
              "resubmit it as is (SPEC 11.4)", file=sys.stderr)
        rc = EXIT_CONFIG_ERROR
    return rc


def cmd_em_pairs(args) -> int:
    """SPEC-v0.2 §5.4/§11 Memory/§13 P3-d: the episodic pair set, its freeze, its two-arm run.

    The order is the one the phase was done in. `--claims` prints the pair arithmetic and each case's
    preflight and runs no episode, so a pre-registered expectation can be read before any number that
    tests it exists, and a pair whose arithmetic no longer supports its expectation refuses to be
    consistent. `--dump`/`--check` freeze that arithmetic to its own file. `--run` drives
    writer→reader per pair per arm through `run_group` — the batch entry, not a harness copy of it —
    with one store per pair, and prints the measurement table; `--measure` re-reads a directory
    written earlier. Zero spend: rule policy, privileged perception, one repeat.

    A `--run` into a directory that already holds a `pairs_run.json` is refused: the store is
    append-only and the reader's claim to test is *one* writer's residue, so a second pass would
    measure a two-row store against a one-row expectation. `--force` says that is intended.
    """
    from .evaluation.episodic_runs import (measure_episodic_pairs, print_pairs,
                                           run_episodic_pairs)
    from .evaluation.episodic_tasks import (EM_FROZEN_PATH, EM_PAIRS, check_em_frozen,
                                            dump_em_frozen, episodic_manifest, pair_claims)

    actions = [args.claims, args.run is not None, args.measure is not None, args.dump,
               args.check is not None, args.memory_definitions,
               args.memory_metrics is not None]
    if sum(bool(a) for a in actions) != 1:
        print("em-pairs needs exactly one of --claims --run --measure --memory-metrics "
              "--memory-definitions --dump --check", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.check:
        ok, msg = check_em_frozen(args.check)
        print(("em set matches: " if ok else "MISMATCH: ") + msg)
        return EXIT_OK if ok else EXIT_CONFIG_ERROR
    if args.dump:
        live = episodic_manifest()
        previous = None
        if os.path.exists(EM_FROZEN_PATH):
            ok, msg = check_em_frozen(EM_FROZEN_PATH)
            if not ok:
                with open(EM_FROZEN_PATH, encoding="utf-8") as f:
                    previous = str(json.load(f).get("sha256", ""))
                if not args.amend:
                    print(f"refusing to overwrite {EM_FROZEN_PATH}: {msg}\n"
                          f"live set hashes to {live['sha256']}. Re-freeze with --amend only as a "
                          f"recorded change to a pre-registered pair set.")
                    return EXIT_CONFIG_ERROR
        path = dump_em_frozen()
        print(json.dumps({"written": path, "sha256": live["sha256"],
                          "cases": len(live["cases"]), "pairs": len(live["pairs"]),
                          **({"amended_from": previous} if previous else {})},
                         ensure_ascii=False, indent=1))
        return EXIT_OK
    if args.claims:
        print(json.dumps({"set": "em", "frozen": EM_FROZEN_PATH, "pairs": pair_claims()},
                         ensure_ascii=False, indent=1, sort_keys=True))
        return EXIT_OK
    if args.measure:
        print_pairs(args.measure)
        return EXIT_OK
    if args.memory_definitions:
        # the 口径 before the numbers: the scorer's own entry point, so the CLI cannot show a
        # definition table that drifted from the one the arithmetic used
        from .evaluation import episodic_metrics
        return EXIT_OK if episodic_metrics.main(["--definitions"]) == 0 else EXIT_CONFIG_ERROR
    if args.memory_metrics:
        from .evaluation import episodic_metrics
        return (EXIT_OK if episodic_metrics.main(["--measure", args.memory_metrics]) == 0
                else EXIT_CONFIG_ERROR)

    ledger = os.path.join(args.run, "pairs_run.json")
    if os.path.exists(ledger) and not args.force:
        print(f"{ledger} already exists; the store beside it is append-only. Pass --force to "
              f"run a second pass over it, or choose a new --run directory.", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if args.pairs:
        unknown = sorted(set(args.pairs) - {p["pair_id"] for p in EM_PAIRS})
        if unknown:
            print(f"unknown pair ids {unknown}; declared: "
                  f"{[p['pair_id'] for p in EM_PAIRS]}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    only = [p for p in EM_PAIRS if not args.pairs or p["pair_id"] in args.pairs]
    try:
        artifact = run_episodic_pairs(args.run, pairs=only, arms=tuple(args.arms),
                                      planner_kind=args.planner,
                                      model_config=args.model_config,
                                      stop_at_requests=args.stop_at_requests)
    except ValueError as exc:
        # Refused here, before any run directory holds an episode — which is why the seat is
        # checked against the policy at this seam rather than left to run_group: a batch that
        # dies mid-way has already written stores, and a half-written pair root is worse than a
        # refusal that names the problem.
        print(str(exc), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    print(f"ran {sum(len(r['batches']) for r in artifact['batches'])} episodes over "
          f"{len(artifact['batches'])} pair-arm stores into {args.run}")
    print(f"seat: planner={artifact['planner']} policy={artifact['policy']} "
          f"model_config={artifact['model_config']} perceive={artifact['perceive']}")
    sr = artifact.get("stop_rule") or {}
    if sr.get("bound_requests") is not None:
        print(f"stop rule: bound {sr['bound_requests']} requests, spent "
              f"{sr.get('spent_requests_when_stopped')}"
              + (f", HALTED BEFORE {sr['halted_before_pair']}"
                 if sr.get("halted_before_pair") else ", completed within bound"))
    if sr.get("halted_before_pair"):
        print(f"  pairs ran    : {artifact.get('pairs_ran')}")
        print(f"  pairs declared: {artifact.get('pairs_declared')}")
    print_pairs(args.run)
    return EXIT_OK


def cmd_skill_metrics(args) -> int:
    """SPEC-v0.2 §11 Skill Acquisition/§13 P4-e: the SKILL-1 口径, then what it measures.

    Both actions delegate to `evaluation/skill_metrics.py`'s own `main`, so the definitions this
    command prints are the definitions its arithmetic counted under — the reason P3-e's
    `--memory-metrics` delegates rather than reimplements. Zero spend either way: `--definitions`
    computes nothing and `--measure` re-reads run directories a batch already wrote.

    The scorer's exit codes pass through unchanged rather than being flattened to 3, because they name
    three different facts and only two of them are the caller's problem: 2 is a usage mistake, 3 means
    a `--measure` target is not a scoreable run directory, 4 means an artifact whose arms contradict its
    own event logs — a refusal to publish. `em-pairs --memory-metrics` collapses all three to a config
    error, which is the shape that lets a broken measurement leave a build with the same code as a typo.
    """
    from .evaluation import skill_metrics

    argv: list[str] = []
    if args.definitions:
        argv.append("--definitions")
    if args.measure:
        argv += ["--measure", *args.measure]
        if args.out:
            argv += ["--out", args.out]
    elif args.out:
        print("config error: skill-metrics --out needs --measure; nothing was written", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    if not argv:
        print("config error: skill-metrics needs --definitions or --measure DIR [DIR ...]",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR
    rc = skill_metrics.main(argv)
    return rc if rc in (EXIT_OK, EXIT_NEEDS_CLARIFICATION, EXIT_CONFIG_ERROR, EXIT_INFRA_ERROR) \
        else EXIT_INFRA_ERROR


def cmd_llm(args) -> int:
    """Every model exchange recorded for one episode — or for one diagnostic run.

    Both write the same `model_calls.jsonl` shape, so the cost of 24 fork requests
    is readable the same way as the cost of an episode (SPEC 7, 11.4). An offline
    run has none, and says so rather than printing an empty block."""
    if not args.file and not args.episode:
        print("config error: llm needs --episode or --file", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    path = args.file or os.path.join(args.episode, "model_calls.jsonl")
    if not os.path.exists(path):
        print(f"no model calls recorded for {path} (an offline run has none)")
        return EXIT_OK
    with open(path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            print(json.dumps(rec, ensure_ascii=False, default=str))
    return EXIT_OK


def main(argv=None):
    # imported here rather than at module scope: the parser needs the canonical
    # frozen path and the set names as defaults, and neither is a second source for
    # a fact the task list already owns (`SET_NAMES` *is* what `build_set` resolves)
    from .evaluation.preregistration import PREREG_PATH
    from .evaluation.tasks import FROZEN_PATH, SET_NAMES

    ap = argparse.ArgumentParser("embodied_agent CLI (continuous-decision experiment)")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="environment precheck")
    d.add_argument("--backend", default="pybullet")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("sim-smoke", help="scene loads and renders")
    s.add_argument("--gui", action="store_true")
    s.set_defaults(fn=cmd_sim_smoke)

    r = sub.add_parser("run", help="one episode, one arm")
    r.add_argument("--task", required=True, metavar="SET[:CASE_ID]",
                   help=f"a set name {SET_NAMES}, optionally narrowed to one case id")
    r.add_argument("--mode", default="B", choices=["A", "B", "C", "a", "b", "c"])
    r.add_argument("--planner", default="rule", choices=PLANNER_KINDS)
    r.add_argument("--repeat", type=int, default=0)
    r.add_argument("--out", default="runs/single")
    r.add_argument("--model-config", default=None,
                   help="which configs/models/*.yaml names the subject; the default is "
                        "configs/models/deepseek.yaml, i.e. the v1 pre-registered model")
    r.add_argument("--no-frames", action="store_true")
    _add_perception_flags(r)
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("evaluate", help="a set through the shared base")
    e.add_argument("--set", required=True, choices=SET_NAMES)
    e.add_argument("--planner", default="rule", choices=PLANNER_KINDS)
    e.add_argument("--modes", default="A,B,C")
    e.add_argument("--repeats", type=int, default=1)
    e.add_argument("--cases", default="")
    e.add_argument("--limit", type=int)
    e.add_argument("--out-root", default="runs")
    e.add_argument("--model-config")
    e.add_argument("--frozen", nargs="?", const=FROZEN_PATH, default=None,
                   metavar="PATH",
                   help=f"refuse to run unless the task list matches this file "
                        f"(default when given without a value: {FROZEN_PATH})")
    e.add_argument("--prereg", nargs="?", const=PREREG_PATH, default=None,
                   metavar="PATH",
                   help=f"refuse to run unless this batch is the matrix and the sampling "
                        f"pre-registered in this file "
                        f"(default when given without a value: {PREREG_PATH})")
    e.add_argument("--no-frames", action="store_true")
    _add_perception_flags(e)
    e.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("report", help="statistics and attribution for a run directory")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--json-only", action="store_true")
    p.set_defaults(fn=cmd_report)

    rp = sub.add_parser("replay", help="read a trajectory back, or re-run a plan")
    rp.add_argument("--run-dir", required=True)
    rp.add_argument("--recorded", action="store_true",
                    help="print the logged event sequence; advances no physics")
    rp.add_argument("--set", help="set the case belongs to (plan replay)")
    rp.set_defaults(fn=cmd_replay)

    fz = sub.add_parser("freeze", help="write or check the frozen task list")
    fz.add_argument("--out", default=FROZEN_PATH)
    fz.add_argument("--amend", action="store_true",
                    help="allow re-freezing over a list the code has drifted from, and "
                         "print the hash it replaced")
    fz.add_argument("--check", nargs="?", const=FROZEN_PATH, default=None, metavar="PATH",
                    help=f"compare the live task list against this file "
                         f"(default when given without a value: {FROZEN_PATH})")
    fz.set_defaults(fn=cmd_freeze)

    pr = sub.add_parser("prereg", help="write or check the P3 pre-registration: the run "
                                      "matrix, prompts, budgets, sampling, statistics and "
                                      "gates a batch has to match")
    pr.add_argument("--out", default=PREREG_PATH)
    pr.add_argument("--amend", action="store_true",
                    help="allow re-freezing over a file the code has drifted from, and "
                         "print the rules hash and the drift it replaced")
    pr.add_argument("--check", nargs="?", const=PREREG_PATH, default=None, metavar="PATH",
                    help=f"compare the live code against this pre-registration "
                         f"(default when given without a value: {PREREG_PATH})")
    pr.set_defaults(fn=cmd_prereg)

    su = sub.add_parser("state-util", help="SPEC 11.3 state-fork action-fit diagnostic "
                                          "(reads contexts, executes nothing)")
    su.add_argument("--planner", choices=PLANNER_KINDS, default="rule",
                    help="rule is the offline control policy; a model planner asks one "
                         "independent request per forked context")
    su.add_argument("--out", default=None, help="where to write state_utilization.json "
                                                "(default: runs/state_util_<planner>)")
    su.add_argument("--model-config", default=None,
                    help="which configs/models/*.yaml names the subject; the default is "
                         "configs/models/deepseek.yaml, i.e. the v1 pre-registered model")
    su.add_argument("--only", nargs="*", default=None, metavar="PAIR_ID",
                    help="score a subset of the 12 frozen pairs (a partial run says so)")
    su.set_defaults(fn=cmd_state_util)

    from .evaluation.vlm_contrast import BATTERIES
    from .evaluation.tasks import SET_NAMES as V02_SET_NAMES

    vx = sub.add_parser("vlm-contrast", help="SPEC-v0.2 §11/§13 P1-f: the privileged-vs-VLM "
                                             "metrics, their 口径 first (renders frames, "
                                             "executes nothing)")
    vx.add_argument("--set", default="all", choices=list(V02_SET_NAMES),
                    help="which frozen task set the perception batteries run over")
    vx.add_argument("--cases", nargs="*", default=None, metavar="CASE_ID",
                    help="a subset of the set; a partial run says so in `coverage`")
    vx.add_argument("--batteries", nargs="*", default=list(BATTERIES), choices=list(BATTERIES),
                    help="which of the four measurement batteries to run")
    vx.add_argument("--out", default="/tmp/v02_p1f_contrast",
                    help="where contrast_<set>.json / .md are written (outside the repo)")
    vx.add_argument("--definitions", action="store_true",
                    help="print the 口径 and compute nothing")
    vx.add_argument("--markdown", action="store_true",
                    help="also print the rendered metric table to stdout")
    vx.add_argument("--quiet", action="store_true", help="no per-case progress line")
    vx.add_argument("--contrast", action="store_true",
                    help="run the episode-level two-arm batch through `run_group` instead of "
                         "the perception batteries")
    vx.add_argument("--contrast-set", default="dev", choices=list(V02_SET_NAMES))
    vx.add_argument("--contrast-modes", nargs="*", default=["B"],
                    help="v0.1 §11.2 modes to pair; the episode contrast is mode B")
    vx.add_argument("--perceive", default="stub",
                    help="the sensing arm of the contrast: stub, or a model channel")
    vx.add_argument("--no-frames", action="store_true",
                    help="episode contrast only: do not keep the rendered frames (the "
                         "artifacts are what make a rate auditable, so this is a size "
                         "trade-off, not a default)")
    vx.set_defaults(fn=cmd_vlm_contrast)

    bz = sub.add_parser("blind-review", help="SPEC 11.4: the contested items of a finished "
                                            "batch, and the human verdicts on them")
    bz.add_argument("--run-dir", required=True)
    bz.add_argument("--sheet", default=None, metavar="PATH",
                    help="write the blind sheet here (a directory gets /blind_review/sheet.json); "
                         "no identity field is on it, by construction")
    bz.add_argument("--template", default=None, metavar="PATH",
                    help="write a blank verdict file (one row per item per reviewer) and stop")
    bz.add_argument("--records", default=None, metavar="PATH",
                    help="a filled verdict file to validate and aggregate "
                         "(default: <run-dir>/blind_review/records.jsonl)")
    bz.set_defaults(fn=cmd_blind_review)

    from .evaluation.episodic_tasks import EM_ARMS, EM_FROZEN_PATH

    em = sub.add_parser("em-pairs", help="SPEC-v0.2 §5.4/§11 Memory/§13 P3-d: the episodic "
                                         "pair set — its arithmetic, its freeze, its two-arm run")
    em.add_argument("--claims", action="store_true",
                    help="print each pair's pre-registered expectation, the case arithmetic that "
                         "supports it and every case's preflight; runs no episode")
    em.add_argument("--run", default=None, metavar="DIR",
                    help="drive writer→reader per pair per arm through `run_group` into DIR "
                         "(one store per pair per arm) and print the measurement table")
    em.add_argument("--measure", default=None, metavar="DIR",
                    help="read a directory an earlier --run wrote and print the table again")
    em.add_argument("--memory-metrics", default=None, metavar="DIR",
                    help="score §11's Memory group (口径 MEM-1: retrieval relevance, successful "
                         "reuse, negative transfer, stale memory usage) over a directory an "
                         "earlier --run wrote, and write episodic_memory_MEM_1.json beside the "
                         "pairs table it reads")
    em.add_argument("--memory-definitions", action="store_true",
                    help="print the MEM-1 definitions as data and compute nothing — the 口径 "
                         "before the numbers, as every §11 group publishes it")
    em.add_argument("--pairs", nargs="*", default=None, metavar="PAIR_ID",
                    help="a subset of the declared pairs; the ledger records which ones ran")
    em.add_argument("--arms", nargs="*", default=list(EM_ARMS), choices=list(EM_ARMS),
                    help="both by default: the memory arm and its `wo_episodic_memory` control")
    em.add_argument("--force", action="store_true",
                    help="let --run write a second pass over a directory that already holds a "
                         "pairs_run.json (the store beside it is append-only, so the reader would "
                         "then face two writer rows where one was pre-registered)")
    em.add_argument("--planner", default="rule", choices=["rule", "deepseek"],
                    help="who decides in the reader episode. 'rule' (default) is the zero-spend "
                         "control: the reader is MemoryPolicy, which reads only the model payload, "
                         "so the two arms differ *only* in memory. 'deepseek' puts the model on "
                         "the reader seat, which is the only way §11's Memory group gets a reading "
                         "on a model decision source — and makes the arms differ in memory AND in "
                         "the draw, so the two seats' tables are not interchangeable. The policy is "
                         "derived from this, not passed: asking for a policy that is also a "
                         "decision maker is unrepresentable, which is the point")
    em.add_argument("--model-config", default=None, metavar="PATH",
                    help="the decision seat's config; required with --planner deepseek, so the "
                         "artifact can name who decided")
    em.add_argument("--stop-at-requests", type=int, default=None, metavar="N",
                    help="halt before starting another pair once the run root's two-ledger spend "
                         "reaches N requests. A pre-registration's bound is only real if the thing "
                         "that spends enforces it, so the check is here rather than in a driver; it "
                         "is read with the two-ledger reader, because on E1 reading only the "
                         "per-episode ledgers undercounted the batch by 356 requests")
    em.add_argument("--dump", action="store_true",
                    help=f"write the set manifest to {EM_FROZEN_PATH}")
    em.add_argument("--amend", action="store_true",
                    help="allow --dump to re-freeze over a file the code has drifted from, and "
                         "print the hash it replaced")
    em.add_argument("--check", nargs="?", const=EM_FROZEN_PATH, default=None, metavar="PATH",
                    help=f"compare the live set against this file (default when given without a "
                         f"value: {EM_FROZEN_PATH})")
    em.set_defaults(fn=cmd_em_pairs)

    sk = sub.add_parser("skill-metrics", help="SPEC-v0.2 §11 Skill Acquisition/§13 P4-e: the SKILL-1 "
                                              "口径 and the rows it measures over finished batches")
    sk.add_argument("--definitions", action="store_true",
                    help="print the SKILL-1 definitions as data and compute nothing — the 口径 "
                         "before the numbers, as every §11 group publishes it")
    sk.add_argument("--measure", nargs="+", default=None, metavar="DIR",
                    help="one or more run directories written by `run --skill-memory`; pooled by "
                         "summing integers, with every batch's own numbers kept beside the pool")
    sk.add_argument("--out", default=None, metavar="DIR",
                    help="write skill_acquisition_SKILL_1.json and skill_acquisition.md into DIR "
                         "(needs --measure; without it nothing is written and this command says so)")
    sk.set_defaults(fn=cmd_skill_metrics)

    ll = sub.add_parser("llm", help="model exchanges recorded for one episode or one "
                                    "diagnostic run")
    ll.add_argument("--episode", default=None)
    ll.add_argument("--file", default=None, help="a model_calls.jsonl to read directly")
    ll.set_defaults(fn=cmd_llm)

    args = ap.parse_args(argv)
    if getattr(args, "mode", None) is not None:
        args.mode = args.mode.upper()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return EXIT_INFRA_ERROR


if __name__ == "__main__":
    sys.exit(main())
