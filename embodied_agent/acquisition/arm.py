"""SPEC-v0.2 §5.6's five boxes connected to the decision loop, and gated like every other module
(SPEC-v0.2 §5.6, §7, §9, §12.3).

The pipeline is `Task/Experience → Skill Gap Detection → parameterized program → Sandbox →
Verifier → Cross-instance validation → Skill Memory`. P4-a/P4-b/P4-c built the boxes; this module is
the wire from the last one back to the first, plus the one place the whole run is *recorded*. Four
seams, all of them slots the loop already had:

* **the trigger** — `acquire_from_episode(summary)`, called by `evaluation/run.py` after
  `episode_end` and after `write_experience`, next to the other post-episode write. It is
  post-episode because §5.6's own input is an *experience*: a gap is a fact about what a finished
  run's records show, and detecting it mid-episode would mean a runtime repairing its own capability
  set while it is still acting — §9's prohibition wearing a lab coat.
* **the offer** — `_skill_catalogue()`, the v0.1 seam whose result is already copied into
  `DecisionContext.model_payload()["skills"]`. With an empty library it returns
  `SkillRegistry.CATALOGUE` itself, so the payload is *byte-identical* to the P3 arm's; a program is
  added only when a program exists to add.
* **the record** — the four frozen `skill_*` types (`core/v02.py:EVENT_MODULE` already assigns them
  to `skill_acquisition`), filed through `PlanningMixin._file`/`_file_record`, so they carry
  `contract_version="4"` and the arm's own condition word. One row per stage: `skill_candidate` is
  filed once, at proposal, and the `sandbox → library` transition is carried by the `skill_library`
  row rather than by a second candidate row, so a count of candidate records is a count of candidates.
* **the measurement** — reuse, read off `events.jsonl` by the same functions an offline reader calls,
  from the actions that actually executed and the verifier's own predicate values (§12.3).

## What the gate is allowed to switch

`wo_skill_acquisition` turns off the module that owns `skill_gap`, `skill_candidate`,
`skill_validation` and `skill_library`, so on that arm this mixin files none of the four, proposes
nothing, validates nothing, admits nothing and offers no `acquired_programs` key. What remains is
the P3 arm exactly — same payload keys, same records — which is what makes a `full` vs
`wo_skill_acquisition` difference attributable to acquisition rather than to two different loops.
`Ablation.violations()` is the check, and it is a check on the *log*, not on this file's shape, so
one filed `skill_*` record on the disarmed arm is a failed episode rather than a hidden defect.

The disarmed arm still returns a row, with `"module_off": true` and the pipeline stages listed as
`not_attempted`. §5.1: absence is a name. An episode that reported nothing would make "the module
was switched off" and "the module found no gap" unreadable apart, which is the exact pair §11's
candidate-generation rate exists to distinguish. Its reuse rows are still computed, over the library
that is *installed* and withheld, each carrying `library_offered: false` — the contrast is one episode
set with an offer and without one, so a difference in what the actions looked like has to survive
being read off both sides, and only the offer differs.

## Why a stored program is *offered* and never *executed as one call* — D49

This was decided by the frozen contract, not by preference. `core/contracts.py:SkillName` is a
`Literal` of `observe/pick/place/safe_retreat` (plus the text backend's verbs), and it is the type
of `DecisionExecute.skill`, `SkillCall.skill` and `PlanStep.skill`; `core/planner.py:REQUIRED_ARGS`
refuses any other name with `unknown skill <name>`. So no decision, no call and no plan step can
name an acquired program, and widening `SkillName` would move the schema fingerprint, the registry
hash and the v0.1 regression baseline while §14 excludes new low-level control. The library
therefore reaches behaviour the way every other v0.2 module does: **through the page**. The offer
carries each program's bound procedure — the primitive steps in order — and the agent's own next
decisions are where those steps either appear or do not.

That constrains what may be claimed. §11's `skill reuse success` is reported as three separate
readings, because only the middle two are facts about the actions:

| field | what it can honestly say |
|---|---|
| `reuse_named` | a decision whose executed skill is a library name — structurally 0 here, and reported with the frozen `SkillName` as its reason rather than dropped |
| `procedure_matched` + `reuse_effect_achieved` | the episode's executed primitive actions contain this program's procedure, in order, under one consistent parameter binding, **and** the program's declared effect under that binding was measured `true` by this episode's own verifier |
| `counts_as_reuse` | the claim a reader will want, and the one this arm withholds: false on a batch whose decision maker cannot choose, with `not_measured_reason` beside it |

A procedure match is *procedural recurrence*, not evidence that the agent chose to reuse: the rule
policy does `pick` then `place` whatever the page says. Turning a match into a reuse claim needs a
decision maker that can be shown the library and can decline it, which is P5's `vlm` channel. What
this arm *can* measure at zero spend is the ablation contrast itself: the same episode set run with
and without an offered library, with the gaps, candidates, admissions, refusals and costs on both
sides.

One fact here is measured rather than argued, because it decides what P4-d's batch can show: no
shipped control policy reads the `skills` section at all — `grep` for `skills` in
`planning/policy.py` and `episodic/policy.py` returns nothing, and both answer from the plan, the
ledger and the debts. So on `--policy rule|payload|memory` the offer changes the *page* (its bytes,
`offer_rounds`, `chars` against `budget`) and not the behaviour, and `summary["skill_acquisition"]`
reports that as `offer_read_by: []` rather than letting a zero reuse rate imply the agent was shown
a library and ignored it. The offer is wired because §5.6's pipeline has to end somewhere the model
can see; it is *measured* as consumed only on a channel that can consume it.

## Cost, because a validation is not free

One cross-instance validation is four real scene builds and four real program runs. Measured on
`smoke_clean` (seed 51) in P4-c: wall 3.852 s, simulator 24.758 s, 8 executor calls, 0 model calls,
for the four instances `object`/`layout`/`parameters`/`repeat`. `validation_budget` therefore caps
how many candidates one episode validates, and every candidate it does not reach is written out as a
`not_validated` row with its reason — the budget is a denominator, not a silence. §11's
`skill-generation cost` row is read from `validation.cost` (`wall_s`, `sim_s`, `calls`,
`model_calls`), which is the only place the frozen model allows it, plus this arm's own
per-episode totals.

An episode's own validation is cheaper than that ceiling whenever an axis cannot be built. Measured
on this arm, on `lh_c6_two_disruptions` mode B (two repeats, `full`, zero spend): one `no_termination`
gap on `grasp:obj_green_1` per episode, one `pick_up` candidate, and a matrix of **three** ran
instances — the `parameters` axis reported `unavailable`, because a one-parameter program has no
second value to bind — at wall 1.214 s / 1.169 s, simulator 6.438 s each, 3 executor calls, 0 model
calls. Both validations were `passed_frozen_rule: true` and `passed_strict_reading: false`, and both
were refused admission on exactly that difference, which is §11's two-counts-apart row arriving as a
measurement rather than as a design note.

## Three limitations, named here rather than found later

* **A cold library cannot be reused.** The first episode of a batch has nothing on its page, so
  §11's reuse rows are about the episodes *after* an admission. `library_at_start` is recorded per
  episode for exactly that reason, and a reuse denominator that ignored it would report a cold start
  as a failure to reuse.
* **the gap is detected from one episode's own records.** `gap.detect_gaps` needs a recurrence
  inside a single episode; a capability that fails once per episode across ten episodes produces no
  gap at all. A cross-episode gap index is a change to §5.6's first box, not to this wire, and P4-e
  reports the episodes whose failures were too sparse to fire.
* **the parameters a rule candidate carries are the *example* values from the failing episode.**
  That is what makes the proposal's reference configuration (`validation.Proposal`) and therefore
  what every "unseen" axis differs from. It is not a leak of the answer key — the values are entity
  and region ids the agent observed itself — but it does mean a program proposed from `obj_green_1`
  is validated *against* `obj_green_1` as its reference, and `default_matrix` reports any axis whose
  alternative the world does not offer as `unavailable` rather than faking it.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Mapping, Optional, Sequence

from ..core.runtime import Runtime
from ..core.v02 import SkillGap, SkillSpec
from ..episodic.arm import (
    DEFAULT_LIMIT,
    EPISODIC_ARMS,
    EpisodicMixin,
    ExperiencedPerceptRuntime,
    ExperiencedPlannedRuntime,
)
from ..planning.working_memory import RECALL_BUDGET_CHARS, RENDER_BUDGET_CHARS
from . import gap as G
from . import memory as M
from . import program
from . import propose as P
from . import validation as V
from .memory import SkillMemory
from .sandbox import SandboxError, SandboxScene
from .validation import Proposal, ValidationError_

#: every arm this phase can be checked against at zero spend: §9's `w/o Skill Acquisition` plus
# everything P2 and P3 already reached, because the acquisition arm is stacked on the memory arm and
# a contrast has to be reachable from one condition word.
SKILL_ARMS = EPISODIC_ARMS + ("wo_skill_acquisition",)

#: The key the library is offered under, *inside* the `skills` section of the payload. Chosen to be
# unmistakably not an imperative verb like the four primitives, because a decision maker that mistook
# it for a callable skill gets `unknown skill` from the validator and a wasted round.
CATALOGUE_KEY = "acquired_programs"

#: The one claim the offer has to carry with it, in the payload itself. This key is the whole of D49
# as far as the decision maker is concerned: it is told the program exists, told its steps, and told
# that the steps are what it may name.
OFFER_DISCLAIMER_KEY = "how_to_use_it"
OFFER_DISCLAIMER = ("not_an_action_name: this is a program over the skills above, not a skill you "
                    "can name in a decision. A decision whose skill is this name is refused as "
                    "'unknown skill'. Its steps are what to do, in order.")

#: Character budget for the whole offer. Same reasoning as `episodic/retrieval.py` and
# `program.render`: an unbounded library in a prompt is a budget leak, and a dropped program is
# visible as a count rather than as an absence.
OFFER_BUDGET_CHARS = 2000

#: Candidates one episode may validate. 1, because the measured price is ~4 scene builds and ~3.9 s
# wall per candidate and a batch that validated every gap would spend the run budget on sandbox
# physics rather than on episodes. The candidates it does not reach are reported.
VALIDATION_BUDGET = 1

#: Which stage of §5.6's pipeline a row belongs to, filed with every row. Free, and the difference
# between "the pipeline stopped" being readable and being a guess.
PIPELINE = ("gap_detection", "proposal", "sandbox_and_verifier", "cross_instance_validation",
            "skill_memory")

ARM_OFF_REASON = ("the skill_acquisition module is switched off for this episode "
                  "(§9's w/o Skill Acquisition), so no gap was looked for, nothing was proposed, "
                  "no sandbox was opened and nothing was offered")

#: Filed with every reuse row. The frozen `SkillName` is the reason `reuse_named` is 0, and a reader
# who is not told that will read the 0 as a failure to reuse.
REUSE_NAMING_REASON = (
    "a decision cannot name an acquired program: core/contracts.py:SkillName is a frozen Literal of "
    "the shipped primitives and core/planner.py refuses any other name, so reuse is measured as "
    "procedural recurrence in the actual action order plus the verifier's own effect verdict, never "
    "as a choice to call a library entry")

#: Which shipped control policies read the `skills` section of the payload: none, as a contract test
# on the two policy modules' sources proves. An empty value here is not an oversight to fix but the
# reason `counts_as_reuse` is withheld on a rule batch — an offer nobody reads cannot be declined,
# and a rate over it would be a measurement of the policy's parser, not of reuse.
OFFER_READ_BY: tuple[str, ...] = ()

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# ------------------------------------------------------------------ record readers ----
def executed_actions(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every action that reached the executor, in loop order, as `(skill, args)` plus a citation.

    `executed is True` is the whole filter. A decision the validator refused never became an action
    (§12.3: 以实际动作为准), and a `finish_accepted` feedback is a control outcome, not a move. The
    args are rebuilt from `entity_id`/`target_id`, which is what the feedback records about the call
    it answers — not from the decision's payload, so a `grasp_candidate_index` the feedback does not
    corroborate is absent rather than assumed.
    """
    out: list[dict[str, Any]] = []
    for e in events:
        if e.get("type") != "execution_feedback":
            continue
        fb = (e.get("payload") or {}).get("feedback") or {}
        if not fb.get("executed") or not fb.get("skill"):
            continue
        args: dict[str, str] = {}
        if fb.get("entity_id"):
            args["object_id"] = str(fb["entity_id"])
        if fb.get("target_id"):
            args["target_id"] = str(fb["target_id"])
        out.append({"skill": str(fb["skill"]), "args": args,
                    "status": str(fb.get("status") or ""),
                    "decision_id": str(fb.get("decision_id") or ""),
                    "event_id": str(e.get("event_id") or "")})
    return out


def measured_predicates(events: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """`predicate_id` -> the last verdict this episode's verifier actually returned.

    Read from `execution_feedback.verification.reports` and from `finish_check.reports` — the two
    places the shipped verifier's answers reach the log. Last-wins in file order because a placement
    made, undone and made again ends the episode `true`, and that is the reading §12.3 asks for
    ("以实际动作与终态为准"). A predicate nobody measured is *absent* here, which is a different
    answer from `false`, and `effects[].measured` below preserves the difference.
    """
    values: dict[str, str] = {}
    for e in events:
        etype = e.get("type")
        payload = e.get("payload") or {}
        if etype == "execution_feedback":
            reports = ((payload.get("feedback") or {}).get("verification") or {}).get("reports") or []
        elif etype == "finish_check":
            reports = payload.get("reports") or []
        else:
            continue
        for report in reports:
            pid = str((report or {}).get("predicate_id") or "")
            if pid:
                values[pid] = str((report or {}).get("value") or "unknown")
    return values


# ------------------------------------------------------------------ the match ----
def _placeholder(value: Any) -> Optional[str]:
    text = str(value)
    if text.startswith("{") and text.endswith("}") and len(text) > 2:
        return text[1:-1]
    return None


def procedure_steps(spec: SkillSpec) -> list[tuple[str, dict[str, str], str]]:
    """The program's steps as `(skill, args-with-placeholders, on_failure)`, in declared order.

    `on_failure` is kept because a step that declares `retry_once` is the reason the matcher below is
    a subsequence matcher and not a contiguous one; dropping it would make the row say "no match"
    about a program whose first attempt failed and whose second did not.
    """
    return [(str(s.intent.skill), dict(s.intent.args or {}), str(s.on_failure))
            for s in spec.procedure if s.intent is not None]


def bind_effect(effect: str, params: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Substitute a binding into one declared effect, or say which placeholder has no value.

    The result must contain no placeholder: an effect string still carrying `{object}` is not a
    predicate anyone can measure, and counting it as one would turn an unbound template into a
    verdict — the exact move §12.4 refuses.
    """
    text = str(effect)
    for name, value in sorted(params.items()):
        text = text.replace("{" + str(name) + "}", str(value))
    return text, sorted(set(_PLACEHOLDER.findall(text)))


def _consistent(action: Mapping[str, Any], args: Mapping[str, str],
                params: Mapping[str, Any]) -> bool:
    """Would binding this action's arguments contradict what the program's placeholders mean?"""
    action_args = dict(action.get("args") or {})
    for key, template in args.items():
        name = _placeholder(template)
        value = action_args.get(key)
        if name is None:
            if str(template) != str(value or ""):
                return False
        elif value and str(params.get(name, value)) != str(value):
            return False
    return True


def _record_binding(action: Mapping[str, Any], args: Mapping[str, str],
                    params: dict[str, str]) -> None:
    action_args = dict(action.get("args") or {})
    for key, template in args.items():
        name = _placeholder(template)
        value = action_args.get(key)
        if name and value:
            params.setdefault(name, str(value))


def match_procedure(spec: SkillSpec,
                    actions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Does this episode's actual action order contain the program, and under what binding?

    Ordered-subsequence, not contiguous: a program whose `pick` step declares `retry_once` can be
    carried out as pick, pick, place, and contiguity would call that no match. Every parameter must
    bind consistently across the whole program (`{object}` may not be two objects), and a step whose
    args hold a literal must find that literal. The *first* consistent match from the earliest start
    is returned, because choosing a later one would be choosing with an eye on the outcome.

    A step's own `status` does not filter it: an attempt that reached the executor and failed *is*
    the program's step being carried out (that is what `retry_once` means), and whether the program
    achieved anything is the effect row's question, answered by the verifier and not by the matcher.
    """
    steps = procedure_steps(spec)
    found: dict[str, Any] = {"matched": False, "steps": len(steps), "params": {},
                              "action_event_ids": [], "unbound_parameters": [],
                              "binding_complete": False, "attempted_from": 0, "why_not": ""}
    if not steps:
        found["why_not"] = "the program declares no executable step"
        return found
    for start in range(len(actions)):
        params: dict[str, str] = {}
        indices: list[int] = []
        cursor = start
        ok = True
        for skill, args, _failure in steps:
            hit = None
            while cursor < len(actions):
                action = actions[cursor]
                if str(action.get("skill")) == skill and _consistent(action, args, params):
                    hit = cursor
                    _record_binding(action, args, params)
                    break
                cursor += 1
            if hit is None:
                ok = False
                break
            indices.append(hit)
            cursor = hit + 1
        if not ok:
            continue
        missing = sorted(set(spec.parameter_names()) - set(params))
        return {"matched": True, "steps": len(steps), "params": dict(sorted(params.items())),
                "action_event_ids": [str(actions[i].get("event_id") or "") for i in indices],
                "unbound_parameters": missing, "attempted_from": start,
                "binding_complete": not missing, "why_not": ""}
    found["why_not"] = (f"no ordered run of these {len(steps)} primitive step(s) exists in the "
                        f"{len(actions)} action(s) this episode executed")
    return found


# ---------------------------------------------------------------- the offer ----
def offer_rows(specs: Sequence[SkillSpec], *,
               budget: int = OFFER_BUDGET_CHARS) -> dict[str, Any]:
    """The library as the payload's `skills` section can carry it, inside a character budget.

    Structured *and* rendered: `procedure` is the machine-readable step list (a model that wants to
    follow the program needs the argument names), `view` is `program.render`'s bounded text (a model
    that wants to skim the library needs one line per program). Neither is a paraphrase of the
    other — both are read off the stored `SkillSpec`, and no field is added that the spec does not
    declare.
    """
    programs: dict[str, Any] = {}
    chars = 0
    dropped: list[str] = []
    for spec in sorted(specs, key=lambda s: s.name):
        row = {
            "description": spec.description,
            "parameters": [{"name": p.name, "type": p.type, "required": p.required}
                           for p in spec.parameters],
            "procedure": [{"skill": skill, "args": dict(args), "on_failure": failure}
                          for skill, args, failure in procedure_steps(spec)],
            "preconditions": list(spec.preconditions),
            "expected_effects": list(spec.expected_effects),
            "termination_conditions": list(spec.termination_conditions),
            "applicability": list(spec.applicability),
            OFFER_DISCLAIMER_KEY: OFFER_DISCLAIMER,
            "view": program.render(spec),
        }
        cost = len(json.dumps(row, ensure_ascii=False, sort_keys=True))
        if programs and chars + cost > budget:
            dropped.append(spec.name)
            continue
        programs[spec.name] = row
        chars += cost
    return {"section": ({CATALOGUE_KEY: programs} if programs else {}),
            "offered": sorted(programs), "dropped": dropped, "chars": chars, "budget": budget}


class AcquisitionMixin(EpisodicMixin):
    """`EpisodicMixin` with §5.6's pipeline hung on the end of the episode and the library on the page.

    Construct it through `AcquiredPlannedRuntime` / `build_skill_arm`. Like the other two mixins it
    adds no strategic choice: the gap detector's rows, the proposer's programs, the sandbox's verdicts
    and the library's contents are all *records about* capability, and the only channel into a round
    is `skills`, which the v0.1 loop already filled from `_skill_catalogue()`.
    """

    #: the store of validated programs. `None` is not an ablation, it is the absence of the module:
    # an arm with no skill memory cannot offer or admit anything, and says so.
    skill_memory: Optional[SkillMemory] = None
    #: the world a candidate is proposed *for*, and the reference every "unseen" axis differs from.
    # Owned by `build_skill_arm`, which is the only place that knows the task case.
    sandbox_scene: Optional[SandboxScene] = None
    #: which proposer this episode uses. `model` also needs `ask`; `propose.propose` refuses to
    # file template output under `proposed_by="model"`, so a half-wired model path is a rejected
    # candidate with a reason, not a silent rule run.
    proposer: str = "rule"
    ask: Optional[P.Asker] = None
    validation_budget: int = VALIDATION_BUDGET
    catalogue_budget: int = OFFER_BUDGET_CHARS
    #: the library installed when this episode *started*. Recorded because "the store has 2 skills
    # now" and "this episode was offered 0" are different facts, and the reuse denominator is the
    # second one; `offer_rounds` is what says whether the installed set reached a page.
    library_at_start: list[str] = []
    #: whether this episode's arm was allowed to offer anything. `False` with a non-empty
    # `library_at_start` is the ablation, and the only state in which a stored program can be
    # present-but-withheld.
    offer_armed: bool = False
    offer: dict[str, Any] = {}

    # ------------------------------------------------------------------ episode ----
    def _begin_episode(self) -> None:
        super()._begin_episode()
        armed = bool(self.skill_memory is not None and self._on("skill_acquisition"))
        # The *installed* library, not the offered one: the reuse rows below are a claim about which
        # programs existed when this episode ran, and §9's `wo_skill_acquisition` contrast needs the
        # same rows computed on the arm that was denied the offer. Whether any of them reached a page
        # is a separate fact — `offer_rounds` and the `library_offered` flag on each row say it.
        self.library_at_start = (sorted(self.skill_memory.names())
                                 if self.skill_memory is not None else [])
        self.offer_armed = armed
        self.offer = {}
        self.offer_rounds = 0
        self.gap_events = 0
        self.candidate_events = 0
        self.validation_events = 0
        self.library_events = 0
        self.acquisition_cost = {"wall_s": 0.0, "sim_s": 0.0, "calls": 0, "model_calls": 0,
                                 "sandbox_runs": 0, "validated_candidates": 0,
                                 "gap_detection_wall_s": 0.0}

    # ------------------------------------------------------- §7 step 2: the offer ----
    def _skill_catalogue(self) -> dict:
        """The shipped primitives, plus the library only when there is a library to show.

        The cold case returns `SkillRegistry.CATALOGUE` — the very object the base loop hands back —
        so a `full` episode before any admission and a `wo_skill_acquisition` episode have identical
        payloads, and any later difference between them is attributable to a program rather than to a
        key. Nothing here ranks, selects or recommends: `SkillMemory.applicable` is deliberately
        unranked for the same reason (§9), and the order is alphabetical so a page cannot depend on
        which admission happened to come first.
        """
        base = super()._skill_catalogue()
        if self.skill_memory is None or not self._on("skill_acquisition"):
            return base
        specs = [entry.spec for entry in self.skill_memory.entries if entry.spec.enabled]
        if not specs:
            return base
        offered = offer_rows(specs, budget=self.catalogue_budget)
        self.offer = {"offered": offered["offered"], "dropped": offered["dropped"],
                      "chars": offered["chars"], "budget": offered["budget"]}
        self.offer_rounds += 1
        out = dict(base)
        out.update(offered["section"])
        return out

    # -------------------------------------------------- §5.6 boxes 1-6, after the run ----
    def acquire_from_episode(self, summary: dict[str, Any], *,
                             run_ref: str = "") -> Optional[dict[str, Any]]:
        """Run the whole §5.6 pipeline over the episode that just finished, and file every row.

        Returns the report rather than raising: a post-episode acquisition that crashed would be
        caught by `run_group` and recorded as an infra error, and an episode whose *physics* finished
        is not an infra error because the library could not be built. Each stage therefore reports
        its own failure in `stage_failures`, and the episode keeps its verdict.
        """
        if self.skill_memory is None:
            return None
        armed = bool(self._on("skill_acquisition"))
        episode_dir = (run_ref or str((summary.get("artifacts") or {})
                                      .get("episode_dir") or ""))
        events = self.store.read_all()
        report: dict[str, Any] = {
            "arm": self._arm, "armed": armed, "module_off": not armed,
            "pipeline": list(PIPELINE), "proposer": self.proposer,
            "episode_id": self.episode_id, "run_ref": episode_dir,
            "library_at_start": list(self.library_at_start),
            "offer": dict(self.offer), "offer_rounds": self.offer_rounds,
            "offer_read_by": list(OFFER_READ_BY),
            "stage_failures": [], "gaps": [], "candidates": [], "validations": [],
            "admissions": [], "refusals": [], "not_validated": [],
            "reuse": [], "reuse_not_measured_reason": REUSE_NAMING_REASON,
            "detector": G.DETECTOR_VERSION, "proposer_version": P.PROPOSER_VERSION,
        }
        # Reuse is measured on both arms, before the gate returns: it is a claim about what the
        # episode did, and the disarmed arm's rows are the contrast's other half. `library_offered`
        # carries the difference — a program that was installed and withheld is not a program the
        # episode could have followed, and a row that conflated them would let the ablated arm
        # report a reuse the armed arm never offered.
        report["reuse"] = self._reuse_rows(events, offered=(armed
                                                           and bool(self.library_at_start)))
        if not armed:
            report["not_attempted"] = list(PIPELINE)
            report["why"] = ARM_OFF_REASON
            report["cost"] = dict(self.acquisition_cost)
            # The two counts the armed path ends with, filed as zeros and as the store's own audit.
            # A report that omitted them would make `full` vs `wo_skill_acquisition` a comparison of
            # two *shapes*, and whoever summed the arms per key would read the absence as the arm
            # having written nothing rather than as this row not saying.
            report["events_filed"] = {"skill_gap": self.gap_events,
                                      "skill_candidate": self.candidate_events,
                                      "skill_validation": self.validation_events,
                                      "skill_library": self.library_events}
            report["skill_memory_audit"] = self.skill_memory.audit()
            if self.skill_memory.path:
                report["skill_memory_path"] = self.skill_memory.path
            return report

        # ---- box 1: gap detection, from this episode's own records ----
        t0 = time.time()
        try:
            gaps = G.detect_gaps(events, summary=summary, library=self.skill_memory.specs())
        except Exception as e:  # noqa: BLE001 - a detector crash is reported, not run around
            report["stage_failures"].append(f"gap detection raised {type(e).__name__}: {e}")
            gaps = []
        report["acquisition_wall_s"] = round(time.time() - t0, 3)
        for row in gaps:
            self._file_record("skill_gap", row, detector=G.DETECTOR_VERSION,
                              library_at_start=list(self.library_at_start), run_ref=episode_dir)
            self.gap_events += 1
            report["gaps"].append(_gap_row(row))
        report["episodes_with_an_open_gap"] = 1 if gaps else 0

        # ---- box 2: a parameterized program for each gap nothing in the library covers ----
        uncovered = [row for row in gaps if not (row.provenance or {}).get("covered")]
        report["gaps_already_covered"] = [str(row.gap_id) for row in gaps
                                          if (row.provenance or {}).get("covered")]
        ask = self.ask if self.proposer == "model" else None
        if self.proposer == "model" and ask is None:
            report["stage_failures"].append(
                "proposer='model' was asked for with no `ask` callable, so nothing was proposed: "
                "a template answer filed under proposed_by='model' would credit a candidate that no "
                "model wrote")
            candidates: list[Any] = []
        else:
            try:
                candidates = P.propose(uncovered, ask=ask, episode_id=self.episode_id,
                                       library=self.skill_memory.specs())
            except Exception as e:  # noqa: BLE001
                report["stage_failures"].append(f"proposal raised {type(e).__name__}: {e}")
                candidates = []
        report["proposal_report"] = P.proposal_report(candidates)
        for candidate in candidates:
            self._file_record("skill_candidate", candidate,
                              proposer_path=str((candidate.provenance or {}).get("path") or ""),
                              run_ref=episode_dir)
            self.candidate_events += 1
            report["candidates"].append(_candidate_row(candidate))

        # ---- boxes 3-5: sandbox, verifier and the cross-instance matrix ----
        executable = [c for c in candidates if c.status == "sandbox"]
        report["validation_budget"] = int(self.validation_budget)
        report["candidates_executable"] = len(executable)
        for candidate in executable[int(self.validation_budget):]:
            report["not_validated"].append({
                "candidate_id": candidate.candidate_id, "skill_name": candidate.spec.name,
                "gap_id": candidate.gap_id,
                "why": f"the per-episode validation budget of {int(self.validation_budget)} "
                       f"candidate(s) is spent; this one was never put in a sandbox"})
        for candidate in executable[:max(0, int(self.validation_budget))]:
            validation = self._validate(candidate, report, episode_dir=episode_dir)
            if validation is None:
                continue
            self._file_record("skill_validation", validation.report,
                              matrix=(validation.matrix.as_dict() if validation.matrix else None),
                              strict_findings=list(validation.strict),
                              passed_strict_reading=bool(validation.passed_strict),
                              run_ref=episode_dir)
            self.validation_events += 1
            report["validations"].append(_validation_row(candidate, validation, episode_dir))
            cost = dict(validation.report.cost or {})
            self.acquisition_cost["wall_s"] = round(
                self.acquisition_cost["wall_s"] + float(cost.get("wall_s") or 0.0), 3)
            self.acquisition_cost["sim_s"] = round(
                self.acquisition_cost["sim_s"] + float(cost.get("sim_s") or 0.0), 3)
            self.acquisition_cost["calls"] += int(cost.get("calls") or 0)
            self.acquisition_cost["model_calls"] += int(cost.get("model_calls") or 0)
            self.acquisition_cost["sandbox_runs"] += int(cost.get("sandbox_runs") or 0)
            self.acquisition_cost["validated_candidates"] += 1
            # ---- box 6: the library, which is the only place `passed` becomes a right ----
            self._admit(candidate, validation, report, episode_dir=episode_dir)

        report["skill_memory_audit"] = self.skill_memory.audit()
        report["cost"] = dict(self.acquisition_cost)
        report["events_filed"] = {"skill_gap": self.gap_events,
                                  "skill_candidate": self.candidate_events,
                                  "skill_validation": self.validation_events,
                                  "skill_library": self.library_events}
        if self.skill_memory.path:
            report["skill_memory_path"] = self.skill_memory.path
        return report

    # ------------------------------------------------------------- one candidate ----
    def _validate(self, candidate, report: dict[str, Any], *, episode_dir: str):
        """Boxes 3-5 for one candidate: the proposal's own configuration as the reference, then the
        pre-registered matrix on top of it.

        Every failure mode returns None and appends a reason, because a candidate that could not be
        validated must still appear in the row list — §11's validation-success rate is over the
        candidates that were *tried*, and a silently dropped one would raise the rate without doing
        anything.
        """
        spec = candidate.spec
        params = {p.name: p.example for p in spec.parameters if p.example not in (None, "")}
        if self.sandbox_scene is None:
            report["stage_failures"].append(
                f"{spec.name}: this arm has no `sandbox_scene`, so there is no configuration to "
                f"call unseen and no validation to run")
            return None
        missing = sorted(set(spec.parameter_names()) - set(params))
        try:
            proposal = Proposal(scene=self.sandbox_scene, params=params, gap_id=candidate.gap_id,
                                subgoal_id=_subgoal_of(report, candidate))
            matrix = V.default_matrix(spec, proposal)
            if missing:
                matrix.unavailable.setdefault("binding", []).append(
                    f"{spec.name} declares {missing} with no observed value in the failing episode, "
                    f"so no instance can bind it and every run on it is a refusal")
            validation = V.validate_candidate(spec, proposal, matrix,
                                              candidate_id=candidate.candidate_id)
        except (ValidationError_, SandboxError) as e:
            report["stage_failures"].append(f"{spec.name}: validation refused: {e}")
            return None
        except Exception as e:  # noqa: BLE001 - a sandbox crash is this row's finding, not the run's
            report["stage_failures"].append(f"{spec.name}: validation raised "
                                           f"{type(e).__name__}: {e}")
            return None
        try:
            validation.artifact = V.write_validation(validation, episode_dir)
        except OSError as e:
            report["stage_failures"].append(f"{spec.name}: validation artifact unwritten: {e}")
            validation.artifact = ""
        return validation

    def _admit(self, candidate, validation, report: dict[str, Any], *,
               episode_dir: str) -> None:
        """The last box, through the only door it has: `SkillMemory.admit(candidate, report)`.

        `admit` writes its own refusal row before it raises — that is where the report's id and the
        candidate's id are known together — so this handler records the *event* and the report row and
        does **not** call `SkillMemory.refuse`. Calling it would double the store's refusal count, and
        §11's unsafe/invalid-skill rate is a rate over that count: a measured 2 attempts per 1 trial
        would be an artifact of this file rather than a fact about the library. `refuse` is for the
        refusals `admit` never sees — the representation gate and the sandbox report — and is called
        from there.

        Nothing here retries with a cheaper gate.
        """
        spec = candidate.spec
        try:
            entry = self.skill_memory.admit(candidate, validation.report)
        except M.SkillMemoryError as e:
            report["refusals"].append({"candidate_id": candidate.candidate_id,
                                        "skill_name": spec.name, "reasons": list(e.reasons),
                                        "passed_frozen_rule": bool(validation.report.passed)})
            self._file("skill_library", {
                "episode_id": self.episode_id, "admitted": False,
                "candidate_id": candidate.candidate_id, "skill_name": spec.name,
                "skill_id": spec.skill_id, "report_id": validation.report.report_id,
                "reasons": list(e.reasons), "run_ref": episode_dir,
                "passed_frozen_rule": bool(validation.report.passed),
                "strict_findings": list(validation.strict),
                "store_path": self.skill_memory.path,
                "store_size": len(self.skill_memory.entries)})
            self.library_events += 1
            return
        promoted = M.promote(candidate, validation.report)
        report["admissions"].append({"candidate_id": candidate.candidate_id,
                                     "promoted_status": promoted.status,
                                     "skill_name": entry.skill_name,
                                     "skill_id": entry.skill_id,
                                     "report_id": validation.report.report_id,
                                     "axes_covered": list(entry.admission.get("axes_covered") or []),
                                     "kinds_covered": list(entry.admission.get("kinds_covered") or []),
                                     "admission": dict(entry.admission)})
        self._file("skill_library", {
            "episode_id": self.episode_id, "admitted": True,
            "candidate_id": candidate.candidate_id, "promoted_candidate_id": promoted.candidate_id,
            "promoted_status": promoted.status,
            "skill_name": entry.skill_name, "skill_id": entry.skill_id,
            "report_id": validation.report.report_id,
            "admission": dict(entry.admission), "run_ref": episode_dir,
            "store_path": self.skill_memory.path, "store_size": len(self.skill_memory.entries),
            "library_after": sorted(self.skill_memory.names())})
        self.library_events += 1

    # ---------------------------------------------------------------- reuse ----
    def _reuse_rows(self, events: Sequence[Mapping[str, Any]], *,
                    offered: bool) -> list[dict[str, Any]]:
        """§11's `skill reuse success`, over the library installed when the episode began.

        Computed on both arms, so `library_offered` is the field that tells them apart: on the armed
        arm the program was on the page, on §9's ablated arm it existed and was withheld.

        Three readings kept apart on purpose — see the table in this module's docstring. `reuse_named`
        is computed from the actual executed skills rather than asserted, so the day `SkillName`
        widens the number changes; `procedure_matched` and `reuse_effect_achieved` are facts about
        the actions and the verifier's own reports; and `counts_as_reuse` stays false on a batch whose
        decision maker cannot choose, with the reason filed beside it.
        """
        stored = {entry.spec.name: entry.spec for entry in
                  (self.skill_memory.entries if self.skill_memory else [])}
        specs = [stored[name] for name in self.library_at_start if name in stored]
        if not specs:
            return []
        actions = executed_actions(events)
        predicates = measured_predicates(events)
        executed_names = {str(a.get("skill")) for a in actions}
        rows: list[dict[str, Any]] = []
        for spec in specs:
            match = match_procedure(spec, actions)
            effects: list[dict[str, Any]] = []
            for declared in spec.expected_effects:
                bound, unbound = bind_effect(declared, match["params"])
                verdict = predicates.get(bound)
                effects.append({"declared": declared, "bound": bound,
                                "unbound_parameters": unbound, "measured": verdict,
                                "true": verdict == "true",
                                "why": (f"no verifier report for {bound} in this episode"
                                        if verdict is None and not unbound else
                                        (f"unbound: {unbound}" if unbound else ""))})
            achieved = bool(match["matched"] and match["binding_complete"] and effects
                            and all(e["true"] for e in effects))
            rows.append({"skill_name": spec.name, "skill_id": spec.skill_id,
                         "library_offered": offered,
                         "reuse_named": bool(spec.name in executed_names),
                         "procedure_matched": bool(match["matched"]),
                         "binding_complete": bool(match["binding_complete"]),
                         "bound_parameters": dict(match["params"]),
                         "matched_action_event_ids": list(match["action_event_ids"]),
                         "unbound_parameters": list(match["unbound_parameters"]),
                         "why_not_matched": str(match["why_not"]),
                         "effects": effects,
                         "reuse_effect_achieved": achieved,
                         "counts_as_reuse": False,
                         "not_measured_reason": REUSE_NAMING_REASON})
        return rows


def _subgoal_of(report: dict[str, Any], candidate) -> Optional[str]:
    """The subgoal the candidate's gap was work for, read from the gap rows filed above."""
    for row in report.get("gaps") or []:
        if row.get("gap_id") == candidate.gap_id:
            return row.get("subgoal_id")
    return None


def _gap_row(row: SkillGap) -> dict[str, Any]:
    provenance = dict(row.provenance or {})
    return {"gap_id": row.gap_id, "round_index": row.round_index,
            "subgoal_id": row.subgoal_id, "desired_effect": row.desired_effect,
            "missing_because": row.missing_because, "attempts": len(row.evidence_refs),
            "considered": list(row.existing_candidates_considered),
            "covered": list(provenance.get("covered") or []),
            "rule": str(provenance.get("rule") or ""), "event_ids": list(row.evidence_refs)}


def _candidate_row(candidate) -> dict[str, Any]:
    spec = candidate.spec
    return {"candidate_id": candidate.candidate_id, "gap_id": candidate.gap_id,
            "skill_name": spec.name, "status": candidate.status,
            "proposed_by": candidate.proposed_by,
            "path": str((candidate.provenance or {}).get("path") or ""),
            "parameters": list(spec.parameter_names()),
            "steps": len(procedure_steps(spec)),
            "expected_effects": list(spec.expected_effects),
            "reasons": list(candidate.rejection_reasons)}


def _validation_row(candidate, validation, episode_dir: str) -> dict[str, Any]:
    report = validation.report
    cost = dict(report.cost or {})
    matrix = validation.matrix.as_dict() if validation.matrix else {}
    artifact = str(getattr(validation, "artifact", "") or "")
    return {"candidate_id": candidate.candidate_id, "report_id": report.report_id,
            "skill_name": report.skill_name, "passed_frozen_rule": bool(report.passed),
            "passed_strict_reading": bool(validation.passed_strict),
            "frozen_reasons": list(report.reasons), "strict_findings": list(validation.strict),
            "instances": len(report.instances),
            "instances_ran": int(cost.get("instances_ran") or 0),
            "kinds_covered": report.kinds_covered(),
            "axes_covered": list(cost.get("axes_covered") or []),
            "axes_unavailable": dict(matrix.get("unavailable") or {}),
            "fully_measured_true": int(cost.get("fully_measured_true") or 0),
            "matrix_fingerprint": str(cost.get("matrix_fingerprint") or ""),
            "cost": {k: cost.get(k) for k in ("wall_s", "sim_s", "calls", "model_calls",
                                              "sandbox_runs")},
            "artifact": os.path.relpath(artifact, episode_dir) if artifact and episode_dir else ""}


class AcquiredPlannedRuntime(AcquisitionMixin, ExperiencedPlannedRuntime):
    """The privileged loop with the plan, the ledger, a memory *and* the §5.6 pipeline installed.

    The base is `ExperiencedPlannedRuntime` rather than a second spelling of `EpisodicMixin,
    PlanningMixin, Runtime`, and the reason is a constructor fact discovered by running it: the
    episodic arm's `__init__` is what accepts `experience_store`, `memory_task_kind` and
    `memory_limit` and what turns them into the attributes `EpisodicMixin` reads, so an acquisition
    runtime that skipped that class would either raise `Runtime.__init__() got an unexpected keyword
    argument 'experience_store'` or re-implement three assignments that the class above already
    owns. Subclassing also says the right thing in the class name: §9's `full` row for this phase is
    the memory arm *plus* the library, and `wo_skill_acquisition` differs from it in one gate.

    The linearization is AcquisitionMixin → ExperiencedPlannedRuntime → EpisodicMixin → PlanningMixin
    → Runtime — `AcquisitionMixin` sits on `EpisodicMixin`, so C3 puts the more derived mixin first
    and every `_begin_episode`/`_skill_catalogue` override the mixin above defines chains to the
    class that already had the behaviour.

    `__init__` sets its own keywords *after* `super().__init__()`, which is the house order
    (`PlannedRuntime`, `ExperiencedPlannedRuntime`): the base loop must not see a half-installed arm,
    and `_begin_episode` — the only reader of `skill_memory` before the first round — runs after
    construction either way.
    """

    def __init__(self, *args, ablation=None, skill_memory: Optional[SkillMemory] = None,
                 sandbox_scene: Optional[SandboxScene] = None, proposer: str = "rule",
                 ask: Optional[P.Asker] = None, validation_budget: int = VALIDATION_BUDGET,
                 catalogue_budget: int = OFFER_BUDGET_CHARS, **kw):
        super().__init__(*args, **kw)
        self.ablation = ablation
        self.skill_memory = skill_memory
        self.sandbox_scene = sandbox_scene
        self.proposer = str(proposer)
        self.ask = ask
        self.validation_budget = int(validation_budget)
        self.catalogue_budget = int(catalogue_budget)


class AcquiredPerceptRuntime(AcquisitionMixin, ExperiencedPerceptRuntime):
    """§13 P5's closed loop: a camera, a plan, a ledger, a memory *and* the §5.6 pipeline.

    The base is `ExperiencedPerceptRuntime` for the constructor reason
    `AcquiredPlannedRuntime` records, and the linearization is AcquisitionMixin →
    ExperiencedPerceptRuntime → EpisodicMixin → PlanningMixin → PerceptRuntime → Runtime.
    `PerceptRuntime.__init__` is what checks the arm's claim against the channel
    (`arm_coherence`) and files the `ablation` record, so a `stub`-channel episode here cannot
    call itself `full` any more than a privileged one can — the join adds a capability and
    removes no gate.

    This class exists because P5's `full` row is the whole §7 loop, and the honest alternative —
    running §11's acquisition rows on the privileged channel and §11's VLM rows on a loop with no
    library — leaves the two groups unjoinable: neither was measured with the other present.
    """

    def __init__(self, *args, skill_memory: Optional[SkillMemory] = None,
                 sandbox_scene: Optional[SandboxScene] = None, proposer: str = "rule",
                 ask: Optional[P.Asker] = None, validation_budget: int = VALIDATION_BUDGET,
                 catalogue_budget: int = OFFER_BUDGET_CHARS, **kw):
        super().__init__(*args, **kw)
        self.skill_memory = skill_memory
        self.sandbox_scene = sandbox_scene
        self.proposer = str(proposer)
        self.ask = ask
        self.validation_budget = int(validation_budget)
        self.catalogue_budget = int(catalogue_budget)


def build_skill_arm(*, case, scene, executor, store, run_dir: str, episode_id: str, budgets,
                    skill_memory: Optional[SkillMemory] = None, task_id: Optional[str] = None,
                    proposer: str = "rule", ask: Optional[P.Asker] = None,
                    validation_budget: int = VALIDATION_BUDGET,
                    catalogue_budget: int = OFFER_BUDGET_CHARS,
                    perceive: str = "privileged", ablation=None, experience_store=None,
                    task_kind: str = "", memory_limit: int = DEFAULT_LIMIT,
                    perceiver=None, gmap=None, environment=None, task_planner=None,
                    memory_render_budget: int = RENDER_BUDGET_CHARS,
                    memory_recall_budget: int = RECALL_BUDGET_CHARS) -> Runtime:
    """Assemble the acquisition arm in one place, so a runner cannot build a half-connected loop.

    Two things are built here that a caller would otherwise have to guess at, which is the whole
    reason this function exists rather than a constructor call in `run.py`:

    * **the proposal's world.** `SandboxScene.from_task(task_id)` is the configuration a candidate is
      proposed *for*, and every `unseen` claim in P4-c's matrix is a difference from it. It is derived
      from `case.task_id` unless the caller names a different task — which is legitimate only when the
      batch means it (validate on a set the agent is not running), and the resulting label is in
      every validation row, so the two are distinguishable afterwards.
    * **the proposer.** `proposer='model'` needs `ask`, and passing one is a billing decision, not a
      configuration detail. This function does not reach for an adapter, does not read a key and does
      not spend anything: it stores the callable it was handed, or none.

    `experience_store` is accepted and passed through, so `full` on this arm is the whole v0.2 system
    the §9 table asks for — plan, ledger, memory, library — and a `wo_skill_acquisition` episode
    differs from it in the acquisition module and nothing else. The camera channel takes
    `perceiver`/`gmap` built by `perception/arm.py:build_perceiver`, the same requirement
    `build_episodic_arm` states: the grounding table says which body each percept names, and an arm
    that rebuilt it from the scene would hold a second, disagreeable account of the same table.
    """
    if skill_memory is None:
        raise ValueError("build_skill_arm needs a SkillMemory: an arm with no library can neither "
                         "offer nor admit, and would measure a cold start under the name of "
                         "acquisition")
    acquisition_kwargs = dict(skill_memory=skill_memory,
                              sandbox_scene=SandboxScene.from_task(task_id or case.task_id),
                              proposer=proposer, ask=ask, validation_budget=validation_budget,
                              catalogue_budget=catalogue_budget)
    if perceive == "privileged":
        return AcquiredPlannedRuntime(
            scene, executor, run_dir, budgets, episode_id, config=case.verify,
            environment=environment, store=store, ablation=ablation, task_planner=task_planner,
            memory_render_budget=memory_render_budget,
            memory_recall_budget=memory_recall_budget,
            experience_store=experience_store, memory_task_kind=task_kind,
            memory_limit=memory_limit, **acquisition_kwargs)
    if perceiver is None or gmap is None:
        raise ValueError(
            f"the camera skill arm needs both `perceiver` and `gmap` (got "
            f"perceiver={type(perceiver).__name__}, gmap={type(gmap).__name__}) for channel "
            f"{perceive!r}: build them with `perception/arm.py:build_perceiver` and pass them in, "
            f"or run the acquisition contrast on `perceive='privileged'`, which spends nothing. "
            f"Refusing here rather than offering onto a half-built page is the point — an episode "
            f"that quietly ignored the library would report a cold start as an ablation.")
    return AcquiredPerceptRuntime(
        scene, executor, run_dir, budgets, episode_id, perceiver=perceiver, gmap=gmap,
        ablation=ablation, config=case.verify, environment=environment, store=store,
        task_planner=task_planner, memory_render_budget=memory_render_budget,
        memory_recall_budget=memory_recall_budget, experience_store=experience_store,
        memory_task_kind=task_kind, memory_limit=memory_limit, **acquisition_kwargs)


__all__ = ["ARM_OFF_REASON", "AcquiredPerceptRuntime", "AcquiredPlannedRuntime",
           "AcquisitionMixin", "CATALOGUE_KEY",
           "OFFER_BUDGET_CHARS", "OFFER_DISCLAIMER", "OFFER_DISCLAIMER_KEY", "OFFER_READ_BY",
           "PIPELINE", "REUSE_NAMING_REASON", "SKILL_ARMS", "VALIDATION_BUDGET", "bind_effect",
           "build_skill_arm", "executed_actions", "match_procedure", "measured_predicates",
           "offer_rows", "procedure_steps"]
