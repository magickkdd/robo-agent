"""SPEC-v0.2 §5.5/§5.6: an acquired skill as a *program over the frozen primitives*.

§5.6 limits v0.2's skill acquisition to 程序技能生成 — a proposal is a parameterized program,
not a learned controller, and the pipeline it must fit into is fixed:

```text
Task / Experience → Skill Gap Detection → parameterized program → Sandbox execution
                  → Verifier → Cross-instance validation → Skill Memory
```

This module owns the representation and the second step's gate; `gap.py` owns the first.

Why a gate at all. The only thing that makes "the agent acquired a skill" different from "the
framework patched a hole" (§5.6's own worry, written into `core/v02.py:SkillGap`'s docstring) is
that the acquired thing is *held to the same rules as everything else*. Those rules already exist
and are already enforced for the model's own decisions, so a program is expressed as them rather
than beside them:

* every step names a skill in `SkillRegistry.CATALOGUE`, so `SkillName` — a frozen `Literal` — is
  the type-level proof that a program cannot do something the action space does not contain, and
  `teleport` is not in it (`core/skills.py` refuses it the same way it refuses the model). The
  `alfred_*` names in `SkillName` are the text backend's verbs and are refused here too: a
  program's steps run on `SkillExecutor`, which implements `_do_observe/_do_pick/_do_place/
  _do_safe_retreat` and nothing else, so the catalogue — not the type — is the capability set;
* a bound program is a list of ordinary `SkillCall`s (`SkillSpec.bind`) and, for validation, an
  ordinary `Plan` of `PlanStep`s — so `PlanValidator`, `SkillExecutor` and `RuntimeVerifier` judge
  it with no new code path, and there is no second validator to drift;
* the executor is the only way to the physics, so verification cannot teleport, edit the evaluator
  or write the world (§5.6's prohibition) without somebody first changing the executor.

`check` refuses the programs that would *look* valid and behave otherwise. Each rule below is a
promise a `SkillSpec` can make that nothing in this repository currently keeps, so refusing them is
not caution but the absence of an interpreter:

| rule | why a program that breaks it is not a program |
|---|---|
| step names a catalogue primitive | `SkillExecutor.execute` dispatches on `_do_<skill>`; an unknown name returns `rejected/unknown skill` at run time, so the defect would appear as an ordinary failure |
| no undeclared placeholder | `SkillSpec.bind` raises `KeyError`; the frozen docstring calls this out as "a candidate that can never bind" |
| no unexpected / missing required arg | `core/planner.py:REQUIRED_ARGS/OPTIONAL_ARGS` are the validator's own tables; an extra key is dropped by the primitive and silently changes its meaning |
| int-valued args get ints or `number` parameters | `_do_pick` answers anything else with `rejected/grasp_candidate_index must be an integer` |
| `when` / `expect` stay empty | **no consumer evaluates them** — a conditional step is a step that runs anyway, and a program is not allowed to describe a control flow the executor will not perform |
| `on_failure` is `abort` or `retry_once` | `try_next_candidate` / `re_observe` / `ask_model` hand control back to the *decision* layer; a skill that makes decisions is the thing §3.4 and §9 forbid the execution side of doing |
| `preconditions` / `expected_effects` / `termination_conditions` / `applicability` non-empty | §5.5's minimum description; an effect-less program cannot be verified and a termination-less program is the `no_termination` gap answered with the same disease |
| `level` is not `primitive`, name is not a primitive's | primitives ship (§5.5 "保留现有 primitive skills"); shadowing one would make a program unreachable behind a real skill |

What this module deliberately does **not** do: it does not check that a program *succeeds*. That is
the sandbox's and the verifier's job (P4-b, P4-c), and a representation that also claimed to know
whether a program works would let a proposal pass on the strength of its own description.

§11 rows fed (§11 Skill Acquisition): `check`'s refusals are the `invalid` numerator of "unsafe /
invalid skill rate" — a proposal that never became an executable program is the cheapest invalid
skill there is, and it must be counted rather than dropped. `program.name`, `parameters` and
`expected_effects` are the join keys for "skill reuse success" (a reuse is a `SkillCall` whose
`plan_id`/`step_id` trace to a library entry) and for "cross-instance transfer" (an instance is
`unseen` when its entities are not in the proposal's `applicability`).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..core.contracts import Plan, PlanStep, SkillCall
from ..core.planner import OPTIONAL_ARGS, REQUIRED_ARGS
from ..core.skills import SkillRegistry
from ..core.v02 import ProcedureIntent, ProcedureStep, SkillParameter, SkillSpec

#: The frozen model-facing action space. An acquired program may compose these and nothing else.
PRIMITIVES: tuple[str, ...] = tuple(sorted(SkillRegistry.CATALOGUE))

#: `on_failure` values an executor actually implements. The other three transfer control to the
# decision layer, which is not this object's to take.
ON_FAILURE_EXECUTABLE = frozenset({"abort", "retry_once"})

#: Char budget for the view a decision gets. Same reasoning as `episodic/retrieval.py`: an
# unbounded description in a prompt is a budget leak, and a truncated one is visible as such.
RENDER_BUDGET_CHARS = 1200
STEP_LINE_CAP = 12


class ProgramError(ValueError):
    """A proposal that is not an executable program, with every reason it failed."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "invalid program")


# ------------------------------------------------------------------ the tables ----
def declared_args(skill: str) -> tuple[frozenset[str], frozenset[str]]:
    """(required, optional) argument names of one primitive, from the validator's own tables.

    These are `core/planner.py`'s, not a copy: it is the code that actually rejects a model's plan,
    so a program that passes here cannot fail there for a reason this module invented.
    """
    return frozenset(REQUIRED_ARGS.get(skill, set())), frozenset(OPTIONAL_ARGS.get(skill, set()))


def arg_type_text(skill: str, arg: str) -> str:
    """The catalogue's own words about one argument, e.g. `"int (optional, 0..2)"`.

    Read rather than restated, because `SkillRegistry.CATALOGUE` is where the human-written
    declaration lives; `test_the_validator_tables_agree_with_the_catalogue` is what keeps the two
    existing sources from drifting apart silently.
    """
    return str((SkillRegistry.CATALOGUE.get(skill) or {}).get("args", {}).get(arg, ""))


def _placeholder(value: str) -> Optional[str]:
    text = str(value)
    if text.startswith("{") and text.endswith("}") and len(text) > 2:
        return text[1:-1]
    return None


# ------------------------------------------------------------------ construction ----
def param(name: str, type: str = "entity", *, description: str = "", required: bool = True,
          example: Any = None) -> SkillParameter:
    return SkillParameter(name=name, type=type, description=description, required=required,
                          example=example)


def step(skill: str, args: Optional[Mapping[str, str]] = None, *,
         on_failure: str = "abort", timeout_s: float = 30.0,
         step_id: Optional[str] = None) -> ProcedureStep:
    """One primitive call with parameters left symbolic.

    `when` and `expect` are not offered as arguments on purpose: `check` refuses a non-empty one, so
    a signature that accepted them would advertise a capability the module then denies.
    """
    fields: dict[str, Any] = {"skill": skill, "args": {str(k): str(v)
                                                       for k, v in (args or {}).items()},
                              "timeout_s": float(timeout_s)}
    step_fields: dict[str, Any] = {"intent": ProcedureIntent(**fields), "on_failure": on_failure}
    if step_id:
        step_fields["step_id"] = step_id
    return ProcedureStep(**step_fields)


def program(name: str, description: str, parameters: Sequence[SkillParameter],
            procedure: Sequence[ProcedureStep], *,
            preconditions: Sequence[str] = (), expected_effects: Sequence[str] = (),
            termination_conditions: Sequence[str] = (), recovery_hints: Sequence[str] = (),
            applicability: Sequence[str] = (), episode_id: Optional[str] = None,
            provenance: Optional[Mapping[str, Any]] = None) -> SkillSpec:
    """Build a `level="acquired"` `SkillSpec`, or refuse with `ProgramError`.

    The refusal is the point of the constructor: the alternative — build it, let the sandbox fail,
    and read the failure back as a physical one — is how an unbindable template ends up looking
    like evidence that the world is hard.
    """
    spec = SkillSpec(name=name, description=description, parameters=list(parameters),
                     procedure=list(procedure), preconditions=list(preconditions),
                     expected_effects=list(expected_effects),
                     termination_conditions=list(termination_conditions),
                     recovery_hints=list(recovery_hints), applicability=list(applicability),
                     level="acquired", episode_id=episode_id,
                     provenance=dict(provenance or {}))
    reasons = check(spec)
    if reasons:
        raise ProgramError(reasons)
    return spec


# ------------------------------------------------------------------ the gate ----
def check(spec: SkillSpec) -> list[str]:
    """Every reason this proposal is not yet an executable program. Empty means it is one."""
    reasons: list[str] = []
    if spec.level == "primitive":
        reasons.append("level 'primitive' is shipped, not acquired: an acquired program composes "
                       "primitives and does not claim to be one")
    if spec.name in SkillRegistry.CATALOGUE:
        reasons.append(f"name {spec.name!r} shadows a primitive; the executor dispatches on the "
                       f"primitive and this program would never be reached")
    if not spec.name.strip():
        reasons.append("name is empty (it is the key retrieval and reuse are both matched on)")
    for field, label in (("preconditions", "preconditions"), ("expected_effects", "expected_effects"),
                         ("termination_conditions", "termination_conditions"),
                         ("applicability", "applicability")):
        if not [x for x in getattr(spec, field) if str(x).strip()]:
            reasons.append(f"{label} is empty: §5.5 asks for them, and an effect-less program "
                           "cannot be verified while a termination-less one is the very gap "
                           "§5.6 is meant to close")

    names = spec.parameter_names()
    if len(set(names)) != len(names):
        reasons.append(f"duplicate parameter names: {sorted(names)}")
    for p in spec.parameters:
        if not p.name.strip():
            reasons.append("a declared parameter has an empty name")

    if not spec.procedure:
        reasons.append("procedure is empty")
    seen_ids: set[str] = set()
    for s in spec.procedure:
        where = f"step {s.step_id}"
        if s.step_id in seen_ids:
            reasons.append(f"{where}: duplicate step_id (a bound call is identified by it)")
        seen_ids.add(s.step_id)
        if s.intent is None:
            reasons.append(f"{where}: no intent; a hole in the middle of a program is not a no-op, "
                           "it is an unrecorded decision")
            continue
        if s.on_failure not in ON_FAILURE_EXECUTABLE:
            reasons.append(f"{where}: on_failure {s.on_failure!r} is not implemented by "
                           f"SkillExecutor (only {sorted(ON_FAILURE_EXECUTABLE)}); "
                           f"{s.on_failure!r} hands control to the decision layer")
        if s.intent.timeout_s <= 0:
            reasons.append(f"{where}: timeout_s must be positive")
        if s.when or s.expect:
            reasons.append(f"{where}: sets when/expect, which nothing in this repository "
                           "evaluates — a conditional step would run unconditionally")
        skill = s.intent.skill
        if skill not in PRIMITIVES:
            reasons.append(f"{where}: skill {skill!r} is not in the frozen catalogue "
                           f"{list(PRIMITIVES)}; §5.6 keeps v0.2 to programs over existing "
                           "primitives, and the executor answers an unknown name with "
                           "rejected/'unknown skill'")
            continue
        required, optional = declared_args(skill)
        given = dict(s.intent.args)
        for unknown in sorted(set(given) - required - optional):
            reasons.append(f"{where}: {skill} takes {sorted(required | optional)}, not {unknown!r} "
                           "(the primitive ignores an argument it does not read)")
        for missing in sorted(required - set(given)):
            reasons.append(f"{where}: {skill} requires {missing!r} and the step supplies nothing")
        for key, value in sorted(given.items()):
            pname = _placeholder(value)
            if pname is None:
                if arg_type_text(skill, key).lstrip().startswith("int"):
                    try:
                        int(str(value))
                    except ValueError:
                        reasons.append(f"{where}: {skill}.{key} is an integer argument, and "
                                       f"{value!r} is not one and not a {{parameter}}")
                continue
            if pname not in set(names):
                # `SkillSpec.unresolved_parameters` is the frozen finder for this; calling it
                # keeps the message and the check in one place instead of restating the rule.
                reasons.append(f"{where}: placeholder {{{pname}}} names no declared parameter")
            elif arg_type_text(skill, key).lstrip().startswith("int"):
                declared = next((p for p in spec.parameters if p.name == pname), None)
                if declared is not None and declared.type != "number":
                    reasons.append(f"{where}: {skill}.{key} is an integer argument, so the "
                                   f"placeholder must bind a 'number' parameter, not a "
                                   f"{declared.type!r} one")
    for unreachable in sorted(set(spec.unresolved_parameters())):
        reasons.append(f"unbound placeholder {unreachable} (step:parameter; the frozen "
                       "SkillSpec check sees what `bind` would raise)")
    return reasons


def has_unhandled_argument(spec: SkillSpec) -> bool:
    """True when a step passes an argument that the *bound* value cannot satisfy.

    Kept separate from `check` because the answer depends on the binding, not on the template:
    `pick` with `grasp_candidate_index="{index}"` is a legal program and an illegal call when
    `index` is `"left"`. P4-b runs it here before it spends a sandbox episode.
    """
    for s in spec.procedure:
        if s.intent is None:
            continue
        for key, value in s.intent.args.items():
            if arg_type_text(s.intent.skill, key).lstrip().startswith("int"):
                try:
                    int(str(value))
                except ValueError:
                    return True
    return False


# ------------------------------------------------------------------ binding ----
def max_calls(spec: SkillSpec) -> int:
    """How many `SkillCall`s one binding of this program can produce, counting a retry.

    Recorded because a program is invisible to `Budgets` otherwise: a six-step program that retries
    every step spends twelve calls inside what reads as one decision, and §11's cost rows would
    attribute that to something else.
    """
    total = 0
    for s in spec.procedure:
        if s.intent is None:
            continue
        total += 1 + (1 if s.on_failure == "retry_once" else 0)
    return total


def used_parameters(spec: SkillSpec) -> set[str]:
    """The parameter names the procedure actually interpolates.

    A declared parameter that no step uses is documentation; a used one that the caller omits is a
    `KeyError` from `bind`. Only the second is refused, which is why this exists rather than
    `parameter_names()`.
    """
    used: set[str] = set()
    for s in spec.procedure:
        if s.intent is None:
            continue
        for value in s.intent.args.values():
            name = _placeholder(value)
            if name is not None:
                used.add(name)
    return used


def bind_calls(spec: SkillSpec, plan_id: str, params: Mapping[str, Any]) -> list[SkillCall]:
    """Instantiate the template as ordinary calls, or say precisely what is missing.

    `SkillSpec.bind` raises `KeyError` for an undeclared placeholder; a caller that caught that and
    ran anyway would execute `pick` with no object and read the rejection as physics. So the
    parameter check happens first and the result is re-checked against the catalogue, which is
    belt-and-braces only if `check` already ran — cheap, and it makes `bind_calls` safe on a spec
    that came from a file rather than from `program()`.
    """
    need = {p.name for p in spec.parameters if p.required} | used_parameters(spec)
    missing = sorted(n for n in need if params.get(n) in (None, ""))
    if missing:
        raise ProgramError([f"{spec.name}: parameters the procedure interpolates and the call did "
                            f"not supply: {missing}"])
    calls = spec.bind(plan_id, dict(params))
    reasons = []
    for call in calls:
        if call.skill not in SkillRegistry.CATALOGUE:
            reasons.append(f"{call.step_id}: bound skill {call.skill!r} is not a primitive")
        required, optional = declared_args(call.skill)
        for key in sorted(set(call.args) - required - optional):
            reasons.append(f"{call.step_id}: {call.skill} does not read {key!r}")
        for key in sorted(required - set(call.args)):
            reasons.append(f"{call.step_id}: {call.skill} requires {key!r}")
        if call.skill == "pick" and "grasp_candidate_index" in call.args:
            try:
                int(call.args["grasp_candidate_index"])
            except ValueError:
                reasons.append(f"{call.step_id}: grasp_candidate_index bound to a non-integer "
                               f"{call.args['grasp_candidate_index']!r}")
    if reasons:
        raise ProgramError(reasons)
    return calls


def as_plan(spec: SkillSpec, plan_id: str, params: Mapping[str, Any], *,
            based_on_state_version: int, goal_ref: str) -> Plan:
    """The bound program as the object the production validator already judges.

    This is why an acquired skill needs no interpreter: `PlanValidator.validate` sees a plan of
    `PlanStep`s with `SkillName` skills, exactly as it sees the model's, and says the same things
    about it. P4-b runs a program through this rather than through a bespoke checker.
    """
    calls = bind_calls(spec, plan_id, params)
    return Plan(plan_id=plan_id, based_on_state_version=int(based_on_state_version),
                goal_ref=goal_ref,
                steps=[PlanStep(id=c.step_id, skill=c.skill, args=dict(c.args)) for c in calls])


# ------------------------------------------------------------------ the view ----
def render(spec: SkillSpec, *, budget: int = RENDER_BUDGET_CHARS) -> str:
    """The program as a decision gets it: what it is for, what it needs, what it does, what then.

    Bounded and truncation-honest, like `episodic/retrieval.py`'s renderer — the last line says how
    many steps were dropped, because a program shown in half is a different program.
    """
    head = [f"skill {spec.name} (acquired): {spec.description}"]
    if spec.parameters:
        head.append("  parameters: " + ", ".join(
            f"{p.name}:{p.type}{'' if p.required else '?'}" for p in spec.parameters))
    if spec.preconditions:
        head.append("  preconditions: " + "; ".join(spec.preconditions[:4]))
    body: list[str] = []
    dropped = 0
    for index, s in enumerate(spec.procedure):
        if index >= STEP_LINE_CAP:
            dropped += 1
            continue
        if s.intent is None:
            body.append(f"    ({s.step_id}: no intent)")
            continue
        args = ", ".join(f"{k}={v}" for k, v in sorted(s.intent.args.items()))
        retry = "" if s.on_failure == "abort" else f" [{s.on_failure}]"
        body.append(f"    {index + 1}. {s.intent.skill}({args}){retry}")
    if dropped:
        body.append(f"    … {dropped} further steps not shown")
    tail = []
    if spec.expected_effects:
        tail.append("  effects: " + "; ".join(spec.expected_effects[:3]))
    if spec.termination_conditions:
        tail.append("  stops when: " + "; ".join(spec.termination_conditions[:3]))
    if spec.applicability:
        tail.append("  applies to: " + "; ".join(spec.applicability[:3]))
    lines = head + body + tail
    text = "\n".join(lines)
    if len(text) > budget:
        text = text[:budget - 1] + "…"
    return text
