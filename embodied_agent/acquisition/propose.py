"""SPEC-v0.2 §5.6's second box: turn a `SkillGap` into a `SkillCandidate`.

The pipeline reads `gap detection → LLM proposes parameterized skill/program → sandbox`, and this
module is the middle arrow. Two things about it are easy to get wrong, and the whole design is an
attempt not to:

* **A proposal is a *program*, not an answer.** `program.check` is the only thing that decides
  whether the text becomes a `SkillSpec`, and nothing here repairs a proposal that fails it. The
  tempting failure mode is to catch the model's malformed JSON, patch the missing field, and carry
  on — which would put the framework's words inside the object being measured, and make §11's
  `candidate generation rate` a count of what this module was willing to accept.
* **A proposal is *parameterized*.** §5.6 asks for a parameterized skill, and a program with the
  failing object's id baked in is a recording of one attempt, not a skill. So the rule path
  generalizes the observed entity and region into declared parameters and keeps the observed values
  only as `example` — visible, citable, and not what the program binds against.

Two proposers, because the SPEC's box names a model and the experiment needs a zero-spend control
alongside it (the same pairing `planning/arm.py` and `episodic/arm.py` establish with their rule
policies):

| path | who writes the program | spend | used by |
|---|---|---|---|
| `rule_candidate` | a frozen template read off the gap's own `desired_effect` | none | the ablation control, every offline test |
| `model_candidate` | the model, through an `ask` callable the caller supplies | billed | the arm with `--propose model` |

The template table is deliberately small: it covers the two effects the shipped verifier actually
answers (`grasp`, `placed`, from `gap.EFFECT_PREDICATE`) and refuses every other stem rather than
inventing a program for it. A gap about `observe` therefore produces a *rejected* candidate with a
reason, which is a row §11 can count, instead of a silent zero.

Nothing in this module imports a scene, an executor, a runtime or an evaluation set: it turns
records into records. That is what makes the sandbox's prohibitions (§5.6: no teleport, no writing
the world) reachable from here only through P4-b, which is where they are enforced.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from ..core.skills import SkillRegistry
from ..core.v02 import SkillGap, SkillCandidate, SkillSpec
from . import program
from .gap import DETECTOR_VERSION, EFFECT_PREDICATE

#: Which proposal this row is, recorded in `provenance` so a reader never has to guess whether a
# program was written by a model or by the template below.
PROPOSER_VERSION = "propose_v2"

#: The `ask` a model-backed proposal needs: prompt in, answer text out. Injected rather than
# imported, because this module must not know which adapter, key or endpoint a run is billed to.
Asker = Callable[[str], str]


# ------------------------------------------------------------------ templates ----
def _template(stem: str) -> Optional[dict[str, Any]]:
    """The frozen program shape for one effect stem, or None when nothing here can write one.

    `two_argument` is read off the *predicate*, not hard-coded: `grasp:X` takes one argument and
    `placed:X:R` takes two, and the effect the candidate declares has to be the one the verifier is
    asked about later (`sandbox.check_effects`), so getting the arity wrong is a validation that
    silently measures nothing.
    """
    primitive = next((skill for skill, s in EFFECT_PREDICATE.items() if s == stem), None)
    if primitive is None:
        return None
    two = stem == "placed"
    if two:
        return {
            "name": "pick_and_place",
            "description": "move one named object into one named region",
            "parameters": [("object", "entity", "the object to move"),
                           ("target", "region", "the region to put it in")],
            "steps": [("pick", {"object_id": "{object}"}, "retry_once"),
                      ("place", {"object_id": "{object}", "target_id": "{target}"}, "abort")],
            "effects": [f"placed:{{object}}:{{target}}"],
            "preconditions": ["the gripper is empty and the object is reachable from the arm"],
            "termination": ["the placement predicate measures true, or the step budget is spent"],
            "applicability": ["one object and one destination region at a time"],
        }
    return {
        "name": "pick_up",
        "description": "grasp one named object and hold it",
        "parameters": [("object", "entity", "the object to grasp")],
        "steps": [("pick", {"object_id": "{object}"}, "retry_once")],
        "effects": ["grasp:{object}"],
        "preconditions": ["the gripper is empty and the object is reachable from the arm"],
        "termination": ["the grasp predicate measures true, or the step budget is spent"],
        "applicability": ["one object at a time, with nothing already held"],
    }


def _effect_arity(effect: str) -> int:
    return len(str(effect).split(":")) - 1


# ------------------------------------------------------------------ the prompt ----
PROMPT_HEAD = (
    "You are proposing ONE reusable skill for an embodied agent, as a program over existing "
    "primitives. Do not write an action for the current moment and do not name a new primitive.\n"
    "Answer with a single JSON object, no prose, with exactly these keys: name, description, "
    "parameters, procedure, preconditions, expected_effects, termination_conditions, "
    "applicability.\n"
    "  parameters: list of {name, type, description}; type is one of entity|region|number|text.\n"
    "  procedure: list of {skill, args, on_failure}; skill must be one of the primitives listed "
    "below; args values are either literal strings or {parameter} placeholders; on_failure is "
    "'abort' or 'retry_once'.\n"
    "  expected_effects: list of predicate strings, each either 'grasp:{parameter}' or "
    "'placed:{parameter}:{parameter}'. These are the claims a verifier will check, so a claim no "
    "verifier answers will be reported as unmeasurable, not as success.\n"
    "A program with the failing object's id written into it is not a skill: parameterize it.\n")


def prompt_text(gap: SkillGap, *, primitives: Optional[Mapping[str, Any]] = None) -> str:
    """The box-2 prompt: the gap as the detector recorded it, plus the primitive contract.

    The catalogue is printed from `SkillRegistry.CATALOGUE`, which is the same object the executor
    and `program.check` consult. A prompt that paraphrased the primitives would be a second,
    divergible statement of what can be called — and the divergence would show up as a rejected
    candidate blamed on the model.
    """
    catalogue = dict(primitives if primitives is not None else SkillRegistry.CATALOGUE)
    lines = [PROMPT_HEAD,
             f"SKILL GAP (detector {DETECTOR_VERSION})",
             f"  desired effect: {gap.desired_effect}",
             f"  missing because: {gap.missing_because}",
             f"  attempts at it in this episode: {len(gap.evidence_refs)}",
             f"  round it was noticed: {gap.round_index}"]
    if gap.existing_candidates_considered:
        lines.append(f"  entries already claiming this effect: "
                     f"{list(gap.existing_candidates_considered)} — do not re-propose one of these")
    lines.append("PRIMITIVES")
    for name, entry in sorted(catalogue.items()):
        args = ", ".join(f"{k}:{v}" for k, v in sorted((entry or {}).get("args", {}).items()))
        lines.append(f"  {name}({args}) post={list((entry or {}).get('post') or [])}")
    return "\n".join(lines) + "\n"


def prompt_sha256(gap: SkillGap) -> str:
    return hashlib.sha256(prompt_text(gap).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ the parse ----
def parse_proposal(text: str, *, episode_id: Optional[str] = None) -> tuple[Optional[SkillSpec],
                                                                            list[str]]:
    """Model answer → `SkillSpec`, or the reasons it is not one. Never a repaired spec.

    Two separate refusal lists, because they mean different things to §11: a shape error is the
    proposal not being readable, and a `program.check` finding is a readable program that cannot
    run. Both are counted as candidates that did not become skills; only the second one says what was
    wrong with the *skill* rather than with the JSON.
    """
    try:
        doc = json.loads(str(text))
    except json.JSONDecodeError as e:
        return None, [f"the answer is not one JSON object: {e}"]
    if not isinstance(doc, dict):
        return None, [f"the answer is a {type(doc).__name__}, not an object with the named keys"]
    missing = [k for k in ("name", "description", "parameters", "procedure", "preconditions",
                           "expected_effects", "termination_conditions", "applicability")
               if k not in doc]
    if missing:
        return None, [f"the answer carries no {k!r} field"]
    try:
        parameters = [program.param(str(p.get("name", "")), str(p.get("type", "entity")),
                                    description=str(p.get("description", "")))
                      for p in list(doc.get("parameters") or [])]
        procedure = [program.step(str(s.get("skill", "")),
                                  {str(k): str(v) for k, v in dict(s.get("args") or {}).items()},
                                  on_failure=str(s.get("on_failure", "abort")),
                                  step_id=f"s{i}")
                     for i, s in enumerate(list(doc.get("procedure") or []))]
        spec = program.program(str(doc["name"]), str(doc["description"]), parameters, procedure,
                               preconditions=[str(x) for x in doc.get("preconditions") or []],
                               expected_effects=[str(x) for x in doc.get("expected_effects") or []],
                               termination_conditions=[str(x) for x in
                                                       doc.get("termination_conditions") or []],
                               applicability=[str(x) for x in doc.get("applicability") or []],
                               episode_id=episode_id)
    except program.ProgramError as e:
        return None, [str(r) for r in (e.args[0] if e.args else [])] or [str(e)]
    except (TypeError, ValueError, AttributeError, KeyError) as e:
        return None, [f"the answer's fields are not the shapes the program constructor takes: "
                      f"{type(e).__name__}: {e}"]
    return spec, []


# ---------------------------------------------------------------- the proposers ----
def rule_candidate(gap: SkillGap, *, episode_id: Optional[str] = None,
                   library: Sequence[SkillSpec] = ()) -> SkillCandidate:
    """The zero-spend proposal: the gap's effect, read through the frozen template table.

    `proposed_by` says `rule` rather than `model`, and `source_text` says which template and which
    effect produced it, because a control that is not labelled is indistinguishable from a result.
    """
    effect = str(gap.desired_effect or "")
    stem = effect.split(":", 1)[0].strip()
    template = _template(stem)
    if template is None:
        return SkillCandidate(
            gap_id=gap.gap_id, episode_id=episode_id, proposed_by=f"rule:{PROPOSER_VERSION}",
            status="rejected",
            rejection_reasons=[f"no rule template for effect stem {stem!r}; the templates cover the "
                               f"predicates the shipped verifier answers "
                               f"{sorted(set(EFFECT_PREDICATE.values()))}, and inventing a program "
                               f"for a claim nothing measures is the one thing this box may not do"],
            spec=SkillSpec(name="", description="", level="acquired"),
            source_text=f"template lookup miss: {effect}",
            provenance={"proposer": PROPOSER_VERSION, "path": "rule", "gap_id": gap.gap_id,
                        "detector": DETECTOR_VERSION, "effect": effect,
                        "templates": sorted({s: str(t["name"]) for s, t in
                                             ((_stem, _template(_stem))
                                              for _stem in sorted(set(EFFECT_PREDICATE.values())))
                                             if t})})
    spec = _from_template(template, effect, episode_id=episode_id, gap=gap)
    reasons = program.check(spec)
    clash = [s.name for s in library if s.name == spec.name]
    if clash:
        reasons.append(f"the library already holds {clash}; a second program with the same name "
                       f"would be unaddressable")
    if reasons:
        return SkillCandidate(gap_id=gap.gap_id, episode_id=episode_id, spec=spec,
                              proposed_by=f"rule:{PROPOSER_VERSION}", status="rejected",
                              rejection_reasons=reasons,
                              source_text=program.render(spec),
                              provenance={"proposer": PROPOSER_VERSION, "path": "rule",
                                          "template": template["name"], "gap_id": gap.gap_id})
    return SkillCandidate(gap_id=gap.gap_id, episode_id=episode_id, spec=spec,
                          proposed_by=f"rule:{PROPOSER_VERSION}", status="sandbox",
                          source_text=program.render(spec),
                          provenance={"proposer": PROPOSER_VERSION, "path": "rule",
                                      "template": template["name"], "gap_id": gap.gap_id,
                                      "detector": DETECTOR_VERSION, "effect": effect,
                                      "generalized_from": effect})


def model_candidate(gap: SkillGap, *, ask: Asker, episode_id: Optional[str] = None,
                    library: Sequence[SkillSpec] = ()) -> SkillCandidate:
    """Ask the model, and keep whatever comes back — including the refusal.

    The answer is stored verbatim in `source_text` (bounded) precisely because it is not ours: if a
    candidate is later disputed, the row that decides it has to be the text the model sent, not a
    paraphrase of it. A raised `ask` is a rejection with its own reason, so a provider error cannot
    be pooled with 'the model proposed something unrunnable'.
    """
    prompt = prompt_text(gap)
    sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    try:
        answer = ask(prompt)
    except Exception as e:                                    # noqa: BLE001 - reported, not hidden
        return SkillCandidate(gap_id=gap.gap_id, episode_id=episode_id,
                              spec=SkillSpec(name="", description="", level="acquired"),
                              proposed_by=f"model:{PROPOSER_VERSION}", prompt_sha256=sha,
                              status="rejected",
                              rejection_reasons=[f"the proposal call did not answer: "
                                                 f"{type(e).__name__}: {e}"],
                              provenance={"proposer": PROPOSER_VERSION, "path": "model",
                                          "gap_id": gap.gap_id, "prompt_sha256": sha})
    spec, reasons = parse_proposal(str(answer), episode_id=episode_id)
    clash = [s.name for s in library if spec is not None and s.name == spec.name]
    if clash:
        reasons.append(f"the library already holds {clash}")
    if spec is None or reasons:
        return SkillCandidate(gap_id=gap.gap_id, episode_id=episode_id,
                              spec=spec or SkillSpec(name="", description="", level="acquired"),
                              proposed_by=f"model:{PROPOSER_VERSION}", prompt_sha256=sha,
                              status="rejected", rejection_reasons=reasons or ["unreadable answer"],
                              source_text=str(answer)[:4000],
                              provenance={"proposer": PROPOSER_VERSION, "path": "model",
                                          "gap_id": gap.gap_id, "prompt_sha256": sha})
    return SkillCandidate(gap_id=gap.gap_id, episode_id=episode_id, spec=spec,
                          proposed_by=f"model:{PROPOSER_VERSION}", prompt_sha256=sha,
                          status="sandbox", source_text=str(answer)[:4000],
                          provenance={"proposer": PROPOSER_VERSION, "path": "model",
                                      "gap_id": gap.gap_id, "prompt_sha256": sha})


def _from_template(template: Mapping[str, Any], effect: str, *,
                   episode_id: Optional[str], gap: SkillGap) -> SkillSpec:
    """Instantiate one template against one observed effect.

    The observed entity and region become `example` values on the parameters, which is the only
    place they are allowed to appear: `program.check` would reject a step whose args hold the
    literal id as *wrong*, but a template that never saw the id could not say which object the agent
    actually failed on, and §12.3 wants the attempt traceable.
    """
    parts = [p for p in str(effect).split(":")[1:] if p and p != "?"]
    parameters = [program.param(name, kind, description=text,
                                example=parts[index] if index < len(parts) else None)
                  for index, (name, kind, text) in enumerate(template["parameters"])]
    procedure = [program.step(skill, args, on_failure=failure, step_id=f"s{i}")
                 for i, (skill, args, failure) in enumerate(template["steps"])]
    return program.program(
        str(template["name"]), str(template["description"]), parameters, procedure,
        preconditions=list(template["preconditions"]), expected_effects=list(template["effects"]),
        termination_conditions=list(template["termination"]),
        applicability=list(template["applicability"]), episode_id=episode_id,
        provenance={"proposer": PROPOSER_VERSION, "path": "rule", "gap_id": gap.gap_id,
                    "template": str(template["name"]), "observed_effect": str(effect)})


def propose(gaps: Iterable[SkillGap], *, ask: Optional[Asker] = None,
            episode_id: Optional[str] = None,
            library: Sequence[SkillSpec] = ()) -> list[SkillCandidate]:
    """One candidate per gap, in the order the detector found them.

    `ask=None` is the rule path and `ask=<callable>` is the model path — there is no third
    possibility, because a caller that wanted the model and did not supply a way to reach it would
    otherwise get template output filed under `proposed_by="model"`.
    """
    out: list[SkillCandidate] = []
    for gap in gaps:
        if ask is None:
            out.append(rule_candidate(gap, episode_id=episode_id, library=library))
        else:
            out.append(model_candidate(gap, ask=ask, episode_id=episode_id, library=library))
    return out


def proposal_report(candidates: Sequence[SkillCandidate]) -> dict[str, Any]:
    """§11's `candidate generation rate` shape, before any of it is validated.

    Split by `proposed_by` because the two proposers are two different claims: a rule template that
    produced twelve candidates says the detector fires, and a model that produced twelve says the
    loop can write programs. Pooling them would let the first support a conclusion about the second.
    """
    by_path: dict[str, dict[str, int]] = {}
    for candidate in candidates:
        path = str(dict(candidate.provenance or {}).get("path") or "unknown")
        row = by_path.setdefault(path, {"proposed": 0, "executable": 0, "refused": 0})
        row["proposed"] += 1
        row["executable" if candidate.status == "sandbox" else "refused"] += 1
    return {"candidates": len(candidates),
            "executable": sum(1 for c in candidates if c.status == "sandbox"),
            "refused": sum(1 for c in candidates if c.status == "rejected"),
            "by_path": {k: dict(v) for k, v in sorted(by_path.items())},
            "refusal_reasons": sorted({r for c in candidates for r in c.rejection_reasons})[:20],
            "proposer_version": PROPOSER_VERSION}


__all__ = ["PROPOSER_VERSION", "PROMPT_HEAD", "Asker", "prompt_text", "prompt_sha256",
           "parse_proposal", "rule_candidate", "model_candidate", "propose", "proposal_report"]
