"""§11's Skill Acquisition group — 口径 written before the numbers (SPEC-v0.2 §11, §13 P4-e).

SPEC-v0.2 §11 lists five rows under *Skill Acquisition*: candidate generation rate, validation
success, cross-instance transfer, skill reuse success, unsafe/invalid skill rate. Every one of them is
a claim about a pipeline that runs **after** an episode is over
(`acquisition/arm.py:acquire_from_episode`), from records the episode already wrote, and §5.6 fixes
the order of the boxes those rows are read off: gap → proposal → sandbox/verifier → cross-instance
matrix → skill memory. The five groups below follow that order, one per row — a table that pooled them
would report "the module works" out of a batch in which nothing was ever sandboxed.

The house rule is P1-f's, P2-e's and P3-e's (`evaluation/vlm_contrast.py`, `evaluation/long_horizon.py`,
`evaluation/episodic_metrics.py`): every rate is rendered from `METRIC_DEFINITIONS` below, carries its
numerator and denominator in the output, and is pooled by summing integers rather than by averaging
per-episode fractions. The row envelope is **key-for-key identical** to MEM-1's and LH-2's, so one
reader can print §11's three behaviour groups in P5's report. What follows are the five decisions this
module exists to make.

**1. `wo_skill_acquisition` is not measured on S1-S3 and S5, and is the control of S4.**

The disarmed arm never calls `detect_gaps`, never proposes and never opens a sandbox
(`acquisition/arm.py`, the `if not armed` branch), so those four groups have no denominator on it —
publishing 0.0 there would credit the arm that removed the gate with "generated no bad candidates",
which is MEM-1's unfalsifiable switch again, one module later. The *reuse* rows are different: the arm
computes them on both sides, over the same installed program and this episode's own verifier, and
`library_offered` is the only field that differs. They are therefore counted into two ids —
`S4.recurrence_with_effect_measured` for the arm that was shown the program,
`S4.withheld_and_recurred` for the arm that had it installed and off the page — and never pooled: §9's
row is a claim that having a library on the page changes what happens, and a sum of the two arms has no
arm to attribute it to.

**2. `validation success` is never merged with `cross-instance transfer`.**

The first is "did the program do anything anywhere" — instances ran, and the effects it declared
measured true. The second is "did it do it on configurations it was not written for" — how many of
§5.6's three axes a report actually varied, and the names of the ones it could not. A report can be
strong on one and empty on the other, and P4-c measured both shapes on real physics: a candidate whose
four instances all ran and all measured true, and a candidate that covered two of three axes and
`passed` under the rule frozen in `core/v02.py`. `S2` and `S3` are therefore never summed and never
averaged into one "validated" share.

**3. `skill reuse success` has three readings and the published one is the weakest-looking.**

§12.3 (以实际动作和最终状态为准) rules out the obvious candidate — a program is not reused because a
rationale names it, and the shipped control policies cannot name one at all (§9 forbids the runtime
making strategic choices; `SkillName` in `core/contracts.py` has no member for a stored program, and
`acquisition/arm.py:OFFER_READ_BY` is empty, which `test_v02_acquisition_arm.py` proves by AST). So
`S4` publishes recurrence — the program's primitive sequence appears in order among the actions this
episode really executed, with a complete binding (`S4.procedure_recurred`), and recurrence *plus* the
verifier's own `true` for every declared effect (`S4.recurrence_with_effect_measured`) — while the row
that would read §11's sentence literally, `S4.counted_as_reuse`, is published as `not_measured` with
the arm's own reason beside it. The behavioural pair is what §11's row can honestly carry on a
zero-spend batch; the self-report it replaces is `reuse_named`, filed false for every program on such
a batch and shown in `per_episode` so nobody mistakes that zero for a finding about models.

**4. The two readings of the validation gate are published apart, and the library records which it saw.**

`core/v02.py:SkillValidationReport._derive_passed` froze three rules (≥ `min_instances` ran, ≥ 2
distinct `instance_kind`s, nothing unsafe) at P0 and §12.2 forbids loosening a gate after results
exist. §5.6's sentence is stricter in two respects the frozen rule does not see — it names *对象、布局、
参数*, three axes rather than "any two kinds", and it implies a verdict, which `unknown` is not. So
every report row carries `passed_frozen_rule`, `frozen_reasons`, `passed_strict_reading` and
`strict_findings`, and `S2` publishes the two shares separately. `S5.invalid_skill_admitted` then counts
against the number the library *honours*: an admission whose own stored provenance says the gate did
not pass is the only way a bad program gets in, and `SkillMemory.admit` refuses on exactly that, so the
honest expectation is 0 — which is only a finding because the denominator is the admission count.

**5. `unsafe/invalid skill rate` counts every candidate that was tried, and no unrun one.**

§12.4's rule (没跑的不能计入闸门) is structural rather than policed here: a `ValidationInstance` that did
not run cannot carry a verdict at all (`_unrun_cannot_have_result`), so it cannot be counted. What
§11's row needs beyond that is the candidates that never reached a sandbox. Those are three different
zeros and this file keeps them apart: `S1.not_executable` is a proposal that *cannot* run (the
representation gates in `acquisition/program.py` refused it), `S1.budget_left_unvalidated` is one that
was *not asked to* run (beyond `--validation-budget`), and `S5.refused_after_validation` is one that
ran, was measured, and was still not admitted. Pooling them gives a rate that moves when a budget flag
moves, which is an artifact of the harness rather than a property of the module.

Zero spend: everything here is read from artifacts a rule-policy, privileged-perception batch wrote
(`episodes/*/episode_summary.json`, its `events.jsonl`, its `validation/*.json`, and `manifest.json`).
No scene is opened, no model is called, no store is mutated, and no number is recomputed from a live
library.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Any

from .report import wilson_interval

#: SKILL-1 is the first publication of §11's Skill Acquisition group, so no earlier version is being
# replaced. If a definition changes after an artifact is delivered, the version changes with it and the
# old artifact is named rather than re-scored (§12.2: 门槛正式前冻结, 事后不调低).
METRIC_VERSION = "SKILL-1"

#: §5.6's three cross-instance axes, in the order the sentence names them.
UNSEEN_AXES = ("object", "layout", "parameters")

#: the four event types §9's registry assigns to this module, read from the frozen registry itself so
# this file cannot drift from it without a test failing
OWNED_EVENT_TYPES = ("skill_gap", "skill_candidate", "skill_validation", "skill_library")

#: the record that marks one decision round: `decision_context` is filed once per round for which a
# page was built, which is the same population `offer_rounds` counts over
ROUND_EVENT = "decision_context"

#: §11's row each id answers, in the order the SPEC's five rows (then the Cost group's) are written
SPEC_ROWS = (
    ("candidate generation rate", ("S1.gap_episodes", "S1.candidates_per_gap",
                                   "S1.covered_by_the_library", "S1.not_executable",
                                   "S1.budget_left_unvalidated")),
    ("validation success", ("S2.frozen_rule_share", "S2.strict_reading_share", "S2.instances_ran",
                            "S2.measured_true_per_ran_instance")),
    ("cross-instance transfer", ("S2.all_three_axes", "S3.axes_at_least_two",
                                 "S3.axes_left_unvaried", "S3.axes_named_unavailable",
                                 "S3.distinct_matrices", "S3.transfer_claim_repeats")),
    ("skill reuse success", ("S4.rounds_offering_a_program", "S4.procedure_recurred",
                             "S4.recurrence_with_effect_measured", "S4.withheld_and_recurred",
                             "S4.counted_as_reuse")),
    ("unsafe/invalid skill rate", ("S5.refused_after_validation", "S5.unsafe_findings",
                                   "S5.invalid_skill_admitted")),
    ("model calls", ("C2.model_calls",)),
    ("simulator time", ("C2.simulator_seconds_per_sandbox_run",)),
    ("latency", ("C2.generation_wall_seconds_per_candidate",)),
    ("skill-generation cost", ("C2.sandbox_runs_per_candidate",
                               "C2.generation_share_of_episode_wall")),
)

#: count-per-unit rows: published without a Wilson interval, and allowed above 1.0
RATIO_METRICS = frozenset({"S1.candidates_per_gap", "S4.rounds_offering_a_program",
                           "C2.model_calls", "C2.simulator_seconds_per_sandbox_run",
                           "C2.generation_wall_seconds_per_candidate",
                           "C2.sandbox_runs_per_candidate",
                           "C2.generation_share_of_episode_wall"})

#: rows whose numerator is defined *relative to the batch*, so summing them across batches would turn a
# within-batch uniqueness into a cross-batch one that no reader can interpret. Measured and published
# per batch; `measure_roots` refuses to pool them.
PER_BATCH_METRICS = frozenset({"S3.distinct_matrices", "S3.transfer_claim_repeats"})

#: §11's two groups this 口径 is written against, in the order the SPEC writes their bullets. A contract
# test reads these names out of the SPEC file itself, so a row cannot disappear from either side without
# one failing — which is what makes "28 ids 铺满 §11 的两组" a checked claim, not a sentence.
SKILL_GROUP_ROWS = ("candidate generation rate", "validation success", "cross-instance transfer",
                    "skill reuse success", "unsafe/invalid skill rate")
COST_GROUP_ROWS = ("model calls", "tokens", "latency", "simulator time", "skill-generation cost")

#: the one §11 Cost row with no id here, named rather than left out. `tokens` is a bill for a model
# call, and the only calls this module can see are the proposer's: `--propose rule` files none, so a
# token row in SKILL-1 would be a second name for `C2.model_calls` rather than a measurement. The
# episode-level row is owned by `evaluation/report.py`'s Costs table, which sums the ledger's own
# `prompt_tokens`/`completion_tokens` (the counters declared in `evaluation/run.py:PROLOGUE_COUNTERS`).
ROWS_WITHOUT_AN_ID = {
    "tokens": ("not an id in SKILL-1: the skill pipeline's only token-bearing calls are the proposer's, "
               "and the arm that runs offline files zero of them (`C2.model_calls`), so a token row here "
               "would restate that row rather than measure anything. §11's `tokens` row is answered at "
               "episode level by `evaluation/report.py`'s Costs table "
               "(`prompt_tokens_total`/`completion_tokens_total`, from `run.py:PROLOGUE_COUNTERS`), and "
               "by P5's benchmark report on the arms that do bill")}

#: why `S4.counted_as_reuse` has no denominator on a batch that can run: `arm.py` withholds
# `counts_as_reuse` on every policy whose decision cannot name a program, and this is the arm's own
# sentence, copied rather than re-derived.
REUSE_NAMING_REASON = ("the decision maker on this batch is a rule policy that cannot name a stored "
                       "program (§9: no runtime strategic choice, and `SkillName` has no member for "
                       "one), so `counts_as_reuse` is withheld by acquisition/arm.py — §11's reuse row "
                       "on a zero-spend batch is carried by the behavioural pair beside it")

_METRIC_KEYS = ("question", "unit", "numerator", "denominator", "truth", "forbidden")

METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------ candidate generation rate ----
    "S1.gap_episodes": {
        "question": "does the detector fire on episodes that struggle?",
        "unit": "one armed episode",
        "numerator": "episodes whose report lists at least one SkillGap",
        "denominator": "armed episodes — every episode that ran the gap detector at all",
        "truth": "the episode's own `events.jsonl`, read by `gap.detect_gaps` after the loop ended",
        "forbidden": "reading the denominator as 'episodes': a `wo_skill_acquisition` episode files no "
                     "gap of any kind (§9), so pooling the arms would report the module as roughly "
                     "half as good as it is"},
    "S1.candidates_per_gap": {
        "question": "how many programs does one gap produce?",
        "unit": "one gap on an armed episode",
        "numerator": "candidate rows filed across those episodes",
        "denominator": "gap rows filed across the same episodes",
        "truth": "`acquisition/arm.py`'s report lists, one entry per record it filed",
        "forbidden": "reading a value below 1.0 as a failure of the proposer: proposing is gated on "
                     "the gap being `uncovered`, and gaps the library already covers are counted by "
                     "`S1.covered_by_the_library`"},
    "S1.covered_by_the_library": {
        "question": "how often was the capability already there?",
        "unit": "one gap on an armed episode",
        "numerator": "gaps whose `covered` list is non-empty — an installed program already declares "
                     "the desired effect",
        "denominator": "all gap rows",
        "truth": "`program.declared_effects` matched against the gap's `desired_effect` at detection "
                 "time, by `gap.py` and not by this reader",
        "forbidden": "dropping a covered gap from `S1.candidates_per_gap`'s denominator: the detector "
                     "reported it, and a covered gap is a fact about the library's shape"},
    "S1.not_executable": {
        "question": "how many proposals were not programs?",
        "unit": "one candidate",
        "numerator": "candidates whose `status` never reached `sandbox` — the representation gates in "
                     "`acquisition/program.py` refused them",
        "denominator": "all candidates proposed, by any proposer path",
        "truth": "`candidate.status` and `candidate.rejection_reasons`, both written by the proposer",
        "forbidden": "summing with `S1.budget_left_unvalidated`: these are candidates that `cannot` "
                     "run and candidates that were `not asked to` run, and the two have opposite fixes"},
    "S1.budget_left_unvalidated": {
        "question": "how much of the pipeline did the batch decline to buy?",
        "unit": "one candidate that could have been validated",
        "numerator": "candidates named in `not_validated` — beyond `--validation-budget`, so never "
                     "sandboxed",
        "denominator": "the candidates the arm called executable, which is the population the "
                       "withheld ones are drawn from: every one it could have put in a sandbox",
        "truth": "`arm.py`'s per-episode `validation_budget`, filed in the same report",
        "forbidden": "reading a withheld candidate as an invalid one (§5.1: 缺失是一个名字). This row "
                     "exists so that a budget of 0 cannot silently raise `S2`"},
    # ------------------------------------------------------------ validation success ----
    "S2.frozen_rule_share": {
        "question": "of the candidates that were validated, how many passed the gate as it is frozen?",
        "unit": "one validation report",
        "numerator": "reports with `passed_frozen_rule` true",
        "denominator": "validation reports filed",
        "truth": "`core/v02.py:SkillValidationReport._derive_passed`, computed by the record itself "
                 "from its instances and never assigned by a caller",
        "forbidden": "publishing this one number as 'validation success': it is the P0-frozen reading, "
                     "and §5.6's sentence is stricter in the two places `S2`/`S3` count separately"},
    "S2.strict_reading_share": {
        "question": "how many passed §5.6's sentence read literally?",
        "unit": "one validation report",
        "numerator": "reports with `passed_strict_reading` true — no finding from "
                     "`validation.strict_findings`",
        "denominator": "validation reports filed",
        "truth": "`validation.py:strict_findings`, which asks about the three named axes and about "
                 "whether a counted instance carries a measured verdict at all",
        "forbidden": "treating the two shares as one metric's before-and-after, or taking their "
                     "intersection as 'the real rate': each is a reading of the same instances, and the "
                     "difference between them is the finding"},
    "S2.instances_ran": {
        "question": "did the runs a report lists actually happen?",
        "unit": "one proposed instance",
        "numerator": "instances the sandbox ran, summed over reports",
        "denominator": "instances listed in those reports, summed",
        "truth": "`cost.instances_ran`, which counts executor returns rather than matrix rows",
        "forbidden": "reading a shortfall as a failure verdict: an instance that did not run cannot "
                     "carry one (§12.4) and is not evidence either"},
    "S2.measured_true_per_ran_instance": {
        "question": "when the sandbox ran the program, did the world agree?",
        "unit": "one ran instance",
        "numerator": "`fully_measured_true` — ran, every declared effect measured, every measurement "
                     "`true`",
        "denominator": "ran instances, summed",
        "truth": "`evaluator.check_verify_claims` on the sandbox's own observations — the same "
                 "predicate machinery that scores the episode",
        "forbidden": "reading this as a gate. The gate is `S2.frozen_rule_share`; an `unknown` claim "
                     "lowers this row without raising any failure count anywhere"},
    # -------------------------------------------------------- cross-instance transfer ----
    "S2.all_three_axes": {
        "id_note": "the id keeps the S2 prefix because the row is a property of the same report; the "
                   "§11 row it answers is `cross-instance transfer`",
        "question": "how often were all three of §5.6's unseen axes varied?",
        "unit": "one validation report",
        "numerator": "reports whose ran instances cover `object`, `layout` and `parameters`",
        "denominator": "validation reports filed",
        "truth": "`cost.axes_covered`, in §5.6's sentence order, assembled from the matrix that ran",
        "forbidden": "reading a low value as a broken program: it says the `harness` did not vary that "
                     "axis, and which axis that was is `S3.axes_named_unavailable`"},
    "S3.axes_at_least_two": {
        "question": "what did the frozen rule see instead?",
        "unit": "one validation report",
        "numerator": "reports whose ran instances cover at least 2 distinct `instance_kind`s",
        "denominator": "validation reports filed",
        "truth": "`SkillValidationReport.kinds_covered()`, the same accessor the gate consults",
        "forbidden": "publishing this as the transfer row: `>= 2 kinds` is the P0 rule, and an "
                     "object+layout pair satisfies it while leaving §5.6's named parameter axis "
                     "untested"},
    "S3.axes_left_unvaried": {
        "question": "how often did the evidence stop at two of the three axes?",
        "unit": "one validation report",
        "numerator": "reports that ran, covered at least one axis, and cover fewer than all three",
        "denominator": "validation reports filed",
        "truth": "`cost.axes_covered`, read as the complement of `S2.all_three_axes` among reports that "
                 "varied anything",
        "forbidden": "adding it to `S3.axes_named_unavailable`: one is an axis the matrix could not "
                     "vary and still said so, the other is the arithmetic of what was covered"},
    "S3.axes_named_unavailable": {
        "question": "when an axis could not be varied, did anyone say which and why?",
        "unit": "one validation report",
        "numerator": "reports whose matrix declared at least one axis unavailable, with its reason",
        "denominator": "validation reports filed",
        "truth": "`matrix.unavailable`, filled where the program has no parameter to vary or the "
                 "failing episode offered no value to bind",
        "forbidden": "reading a high value as bad news: it is the `honest` row. A missing axis that "
                     "nobody named is the hole, and it is invisible in every other number here"},
    "S3.distinct_matrices": {
        "question": "is a transfer claim carried by more than one configuration?",
        "unit": "one validation report",
        "numerator": "reports whose `matrix_fingerprint` is non-empty and new within this batch",
        "denominator": "validation reports in that batch",
        "truth": "`validation.py`'s sha256 over the matrix rows, seeds and scene labels included. Because "
                 "the numerator is a uniqueness `within` a batch, `measure_roots` publishes this row from "
                 "`batches[].metrics` and leaves the pooled copy unmeasured",
        "forbidden": "pooling across batches: a fingerprint is only unique `within` a batch by "
                     "construction, so a sum over batches would always approach 1.0 and mean nothing"},
    "S3.transfer_claim_repeats": {
        "question": "how much of the evidence is the same configuration measured again?",
        "unit": "one validation report",
        "numerator": "reports whose matrix fingerprint repeats an earlier one in the same batch",
        "denominator": "validation reports in that batch",
        "truth": "the same fingerprint, read the other way round, so the pair cannot be quietly merged",
        "forbidden": "reading 0 as independence: two different fingerprints can still share one scene, "
                     "and nothing in the artifact rules that out"},
    # ------------------------------------------------------------ skill reuse success ----
    "S4.rounds_offering_a_program": {
        "question": "was a stored program actually on the page the decision was made from?",
        "unit": "one decision round of an armed episode that began with a non-empty library",
        "numerator": "rounds recorded in `offer_rounds`",
        "denominator": "`decision_context` records in the same episodes — one per round a page was built",
        "truth": "`arm.py::_skill_catalogue`, which copies the offered rows into the payload's `skills` "
                 "section, counted against the runtime's own per-round record",
        "forbidden": "reading the rate up for episodes whose library was cold: those contribute to no "
                     "denominator here, because there was nothing that could have been offered"},
    "S4.procedure_recurred": {
        "question": "did the program's primitive sequence appear in what the episode really did?",
        "unit": "one program offered to an armed episode from the library it began with",
        "numerator": "reuse rows with `procedure_matched` and `binding_complete`",
        "denominator": "programs installed when those armed episodes began and offered on its page",
        "truth": "`program.match_procedure` over `skill_call` events that were executed, in event order",
        "forbidden": "calling it reuse: the episode did not choose the program (§9), and this row says "
                     "the actions recurred, which is the half of §11's row this batch can measure"},
    "S4.recurrence_with_effect_measured": {
        "question": "and did the effect that program declares hold in that episode?",
        "unit": "one program offered to an armed episode from the library it began with",
        "numerator": "reuse rows with `reuse_effect_achieved` — recurrence, a complete binding, and "
                     "every declared effect measured `true`",
        "denominator": "the same programs",
        "truth": "`verifier_report`/`execution_feedback` events of that episode, bound through the "
                 "match's own parameters",
        "forbidden": "pooling it with `S4.withheld_and_recurred` into one 'reuse' figure: the two arms' "
                     "rows are the contrast §9 asks for, and a sum of them has no arm to attribute it "
                     "to"},
    "S4.withheld_and_recurred": {
        "question": "what did the same program do on the arm whose page withheld it?",
        "unit": "one program installed but not offered, on a `wo_skill_acquisition` episode",
        "numerator": "reuse rows on the disarmed arm with `reuse_effect_achieved` true",
        "denominator": "programs installed when those disarmed episodes began",
        "truth": "the same `_reuse_rows` computation the armed arm files, over the same store and the "
                 "same verifier reports, with `library_offered` false",
        "forbidden": "reading a value equal to `S4.recurrence_with_effect_measured` as proof the offer "
                     "does nothing: on a deterministic batch the two must be equal, because the policy "
                     "cannot read the section either way (`arm.py:OFFER_READ_BY` is empty). The row is "
                     "here so that the day a model policy runs, the difference is already named and "
                     "already has a denominator"},
    "S4.counted_as_reuse": {
        "question": "would a reader of §11's sentence accept this batch's rows?",
        "unit": "one program installed when an episode began",
        "numerator": "reuse rows with `counts_as_reuse` true",
        "denominator": "filed as zero on a batch whose decision maker cannot choose, so the row is not "
                       "measured",
        "truth": "the arm's own `not_measured_reason`, copied rather than re-derived",
        "forbidden": "publishing 0.0 as the value. The numerator is withheld by `arm.py`, so this row "
                     "carries `measured: false` and the reason beside it — and it becomes countable on "
                     "the day a model policy is the one choosing, which is P5's batch"},
    # ------------------------------------------------------ unsafe / invalid skill rate ----
    "S5.refused_after_validation": {
        "question": "of what the gate looked at, how much did it refuse?",
        "unit": "one validation report",
        "numerator": "refusals filed by `SkillMemory.admit`, one row per candidate it tried to admit",
        "denominator": "validation reports filed",
        "truth": "`SkillMemoryError.reasons`, copied into the episode's report before the exception was "
                 "swallowed. A refusal row that also says `passed_frozen_rule` true is the strict "
                 "reading stopping a program the frozen rule would have admitted",
        "forbidden": "summing the store's `refusals_in_store` instead: that field is the live store's "
                     "running total across a batch, so the per-episode lists are the numerator and the "
                     "store field is only an audit of them"},
    "S5.unsafe_findings": {
        "question": "did any ran instance report something unsafe?",
        "unit": "one validation report",
        "numerator": "reports whose own artifact holds at least one ran instance with a non-empty "
                     "`unsafe` list",
        "denominator": "validation reports whose artifact is present in the episode directory",
        "truth": "`ValidationInstance.unsafe` as written by the sandbox — the same field the frozen "
                 "gate reads, which fails a report on its own",
        "forbidden": "reading 0 as 'the module is safe': nothing on a privileged single-arm batch "
                     "reaches a contact finding, so this row's content is that the channel exists and "
                     "the count is empty. A report whose artifact is missing leaves `this` denominator "
                     "rather than pretending to be clean"},
    "S5.invalid_skill_admitted": {
        "question": "did anything get into the library against its own gate?",
        "unit": "one admission",
        "numerator": "admissions whose stored provenance says `passed_frozen_rule` false, or whose "
                     "`strict_findings` is non-empty",
        "denominator": "admissions filed",
        "truth": "`SkillMemory.admit`'s admission block, written at the moment of admitting and read "
                 "back off the store line",
        "forbidden": "reading a 0/0 as a pass. With no admissions the row is not measured; the expected "
                     "0 becomes evidence only once a batch has admitted something"},
    # ------------------------------------------------------------------ cost (§11) ----
    "C2.model_calls": {
        "question": "what did the pipeline cost in model calls?",
        "unit": "one armed episode",
        "numerator": "`cost.model_calls` summed over that episode's validated candidates",
        "denominator": "armed episodes",
        "truth": "the sandbox's own counters; `proposed_by='rule'` draws nothing, so the honest value "
                 "on this batch is 0 — a number, not an absence",
        "forbidden": "generalising it to §5.6's proposal stage: `--propose model` is refused at the CLI "
                     "precisely because it would make this row non-zero and unattributable"},
    "C2.simulator_seconds_per_sandbox_run": {
        "question": "how much simulator time does one sandbox run buy?",
        "unit": "one sandbox run",
        "numerator": "`cost.sim_s`, summed",
        "denominator": "`cost.sandbox_runs`, summed",
        "truth": "the scene's own sim clock rather than wall time, so a loaded machine does not move it",
        "forbidden": "adding the episode's own simulator time to it: the validation runs are a separate "
                     "purchase and §11 asks for both, apart"},
    "C2.generation_wall_seconds_per_candidate": {
        "question": "what did generating and checking a candidate take in real time?",
        "unit": "one candidate that reached a sandbox",
        "numerator": "`cost.wall_s` from the validation rows, summed",
        "denominator": "validated candidates, summed",
        "truth": "the arm's own wall clock around the validation stage",
        "forbidden": "reading it as a per-episode cost: episodes that produced no candidate contribute "
                     "nothing here and are reported by `S1.gap_episodes`"},
    "C2.sandbox_runs_per_candidate": {
        "question": "how many executions does one candidate's evidence take?",
        "unit": "one validated candidate",
        "numerator": "`cost.sandbox_runs`, summed",
        "denominator": "validated candidates, summed",
        "truth": "one executor run per matrix row, counted by the sandbox",
        "forbidden": "treating it as fixed by the matrix: an instance refused before executing leaves "
                     "this denominator short, which is `S2.instances_ran`'s business"},
    "C2.generation_share_of_episode_wall": {
        "question": "how much of an episode's real time went into acquiring a skill?",
        "unit": "one armed episode",
        "numerator": "`cost.wall_s` for that episode",
        "denominator": "`wall_time_s` of the same episode",
        "truth": "both sides from the same `episode_summary.json`",
        "forbidden": "pooling this across arms: a `wo_skill_acquisition` episode spends 0.0 by "
                     "construction and is not in this denominator"},
}

#: one sentence per id that has a no-denominator case; a row missing from here falls back to the
# generic sentence rather than printing `None` where a reason belongs
_NOT_MEASURED = {
    "S1.gap_episodes": "no armed episode ran the detector",
    "S1.candidates_per_gap": "no gap was filed, so there was nothing to propose for",
    "S1.covered_by_the_library": "no gap was filed",
    "S1.not_executable": "no candidate was proposed",
    "S1.budget_left_unvalidated": "no candidate was executable and none was withheld",
    "S2.frozen_rule_share": "no candidate was put in a sandbox",
    "S2.strict_reading_share": "no candidate was put in a sandbox",
    "S2.instances_ran": "no validation report was filed",
    "S2.measured_true_per_ran_instance": "no instance ran",
    "S2.all_three_axes": "no validation report was filed",
    "S3.axes_at_least_two": "no validation report was filed",
    "S3.axes_left_unvaried": "no validation report was filed",
    "S3.axes_named_unavailable": "no validation report was filed",
    "S3.distinct_matrices": "no validation report in any batch",
    "S3.transfer_claim_repeats": "no validation report in any batch",
    "S4.rounds_offering_a_program": "every armed episode began with an empty library, or was disarmed",
    "S4.procedure_recurred": "no program was installed when any armed episode began",
    "S4.recurrence_with_effect_measured": "no program was installed when any armed episode began",
    "S4.withheld_and_recurred": "no `wo_skill_acquisition` episode ran with a library installed — the "
                                "control of this contrast needs a warm library handed to the arm that "
                                "does not read it",
    "S4.counted_as_reuse": REUSE_NAMING_REASON,
    "S5.refused_after_validation": "no candidate reached the library door",
    "S5.unsafe_findings": "no validation artifact was found beside the reports it scores",
    "S5.invalid_skill_admitted": "nothing was admitted by anything, so the gate had no decision to "
                                 "be caught ignoring",
    "C2.model_calls": "no armed episode",
    "C2.simulator_seconds_per_sandbox_run": "no sandbox run",
    "C2.generation_wall_seconds_per_candidate": "no candidate was validated",
    "C2.sandbox_runs_per_candidate": "no candidate was validated",
    "C2.generation_share_of_episode_wall": "no armed episode spent wall time on validation",
}


# ---------------------------------------------------------------------------- counting ----
def _count(counts: dict[str, list[float]], metric: str, num: float = 0, den: float = 0) -> None:
    """Accumulate one contribution, and refuse an id nobody defined: a number that appears in a table
    with no 口径 next to it is a number that will be misread."""
    if metric not in METRIC_DEFINITIONS:
        raise KeyError(f"{metric!r} has no entry in METRIC_DEFINITIONS")
    slot = counts.setdefault(metric, [0.0, 0.0])
    slot[0] += num
    slot[1] += den


def _rate(metric: str, numerator: float, denominator: float, **extra) -> dict[str, Any]:
    """One published fraction, carrying the definition it was counted under.

    MEM-1's envelope key for key (`long_horizon._rate`'s shape, which MEM-1 copied) so that §11's three
    behaviour groups render from one reader in P5's report. `measured` is the field that keeps a zero
    denominator from becoming a zero rate, and `interval_kind` separates the count-per-unit rows that
    get no Wilson interval at all.
    """
    d = METRIC_DEFINITIONS[metric]
    n, dn = numerator, denominator
    if metric in RATIO_METRICS:
        interval, kind = None, "count_per_unit"
    else:
        n, dn = int(n), int(dn)
        if n > dn:
            raise AssertionError(f"{metric}: a proportion cannot have numerator {n} > denominator "
                                 f"{dn}; the counting site is wrong")
        interval = list(wilson_interval(n, dn)) if dn else None
        kind = "proportion"
    return {"metric": metric, "numerator": n, "denominator": dn,
            "value": (round(n / dn, 4) if dn else None),
            "wilson_95": interval, "interval_kind": kind,
            "measured": bool(dn),
            "not_measured_reason": (None if dn else _NOT_MEASURED.get(
                metric, "no unit fell in this row's denominator")),
            "denominator_too_small_for_a_rate": 0 < dn < 10,
            "spec_row": next((row for row, ids in SPEC_ROWS if metric in ids), None),
            "question": d["question"], "unit": d["unit"],
            "as_computed": f"{d['numerator']} / {d['denominator']}", **extra}


def _render(counts: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    """Every defined id, whether or not a counting site reached it.

    Iterating over `METRIC_DEFINITIONS` rather than over `counts` is what keeps `render_table` from
    KeyErroring on a single-arm batch (no disarmed episode ⇒ no `S4.withheld_and_recurred` accumulation
    slot) and keeps such a row printing `not measured` with its own reason instead of being absent — an
    absent row is read as "not part of the 口径" by every reader, which is how a denominator goes
    missing quietly.
    """
    return {metric: _rate(metric, *counts.get(metric, [0.0, 0.0]))
            for metric in sorted(METRIC_DEFINITIONS)}


def _merge(target: dict[str, list[float]], counts: dict[str, list[float]]) -> None:
    for metric, (num, den) in counts.items():
        _count(target, metric, num, den)


# ------------------------------------------------------------------- reading a batch ----
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_batch(root: str) -> dict[str, Any]:
    """One run directory, as the §11 rows need it: the manifest's arm and the episodes' reports.

    Read-only, and the ledger is trusted only as far as it can be checked — a directory whose episodes
    carry no `skill_acquisition` block raises instead of contributing zeros, because "this loop ran
    with no library installed" is a different fact from "the module was switched off", and neither is a
    number that may be averaged into §11's.
    """
    if not os.path.isdir(root):
        raise ValueError(f"{root!r} is not a run directory")
    manifest = _load(os.path.join(root, "manifest.json"))
    paths = sorted(glob.glob(os.path.join(root, "episodes", "*", "episode_summary.json")))
    if not paths:
        raise ValueError(f"{root}: a run directory with no episodes has no §11 rows")
    episodes = []
    for path in paths:
        summary = _load(path)
        block = summary.get("skill_acquisition")
        if not isinstance(block, dict):
            raise ValueError(f"{path}: no `skill_acquisition` block — the module was never installed "
                             f"here, which is not the same arm and is not scoreable as one")
        directory = os.path.dirname(path)
        episodes.append({"dir": directory, "path": path,
                         "episode_id": summary.get("episode_id", ""),
                         "repeat": summary.get("repeat"),
                         "wall_time_s": summary.get("wall_time_s"),
                         "result": summary.get("result") or {}, "report": block})
    return {"root": os.path.abspath(root), "run_id": manifest.get("run_id", os.path.basename(root)),
            "set": manifest.get("set"), "planner": manifest.get("planner"),
            "modes": manifest.get("modes"),
            "manifest_acquisition": manifest.get("skill_acquisition"), "episodes": episodes}


def _event_types(ep_dir: str) -> list[str]:
    path = os.path.join(ep_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [str(json.loads(line).get("type")) for line in fh if line.strip()]


def score_rounds(batch: dict) -> dict[str, Any]:
    """The arm's arithmetic for one batch, with the per-episode rows it was summed from.

    Every counting site names its metric id, so the contract test's AST walk can require each id to
    appear here and nowhere else — a second counting site would double count, and a defined id with no
    counting site would be a row that prints `not_measured` forever.
    """
    counts: dict[str, list[float]] = {}
    per_episode: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    naming_reasons: list[str] = []
    unsafe_reports = unsafe_denominator = 0

    for ep in batch["episodes"]:
        r = ep["report"]
        armed = bool(r.get("armed"))
        gaps, candidates = r.get("gaps") or [], r.get("candidates") or []
        executable = int(r.get("candidates_executable") or 0)
        withheld = r.get("not_validated") or []
        reports, refusals = r.get("validations") or [], r.get("refusals") or []
        admissions = r.get("admissions") or []
        reuse, cost = r.get("reuse") or [], r.get("cost") or {}
        rounds = _decision_rounds(ep)
        # the arm's own sentence for why `counts_as_reuse` is withheld, taken off the rows it filed
        naming_reasons += [str(x.get("not_measured_reason") or "") for x in reuse
                           if x.get("not_measured_reason")]

        for report_row in reports:
            found, present = _unsafe_of(ep["dir"], report_row)
            if present:
                unsafe_denominator += 1
                unsafe_reports += 1 if found else 0

        # S4's rows exist on both arms, into two ids (decision 1): the armed arm's programs were on the
        # page, the control's were installed and withheld, and pooling the two would produce a "reuse"
        # figure with no arm to attribute it to.
        recurred = sum(1 for x in reuse if x["procedure_matched"] and x["binding_complete"])
        achieved = sum(1 for x in reuse if x["reuse_effect_achieved"])
        if armed:
            _count(counts, "S4.procedure_recurred", recurred, len(reuse))
            _count(counts, "S4.recurrence_with_effect_measured", achieved, len(reuse))
        else:
            _count(counts, "S4.withheld_and_recurred", achieved, len(reuse))

        if armed:
            _count(counts, "S1.gap_episodes", 1 if gaps else 0, 1)
            _count(counts, "S1.candidates_per_gap", len(candidates), len(gaps))
            _count(counts, "S1.covered_by_the_library",
                   sum(1 for g in gaps if g.get("covered")), len(gaps))
            _count(counts, "S1.not_executable", len(candidates) - executable, len(candidates))
            # `not_validated` is `executable[budget:]` (`arm.py`), so the withheld rows are a subset of
            # the executable population rather than an addition to it: adding them to the denominator
            # would let the numerator inflate the divisor and report "half declined" for a batch that
            # declined everything it could have bought.
            _count(counts, "S1.budget_left_unvalidated", len(withheld), executable)
            _count(counts, "S2.frozen_rule_share",
                   sum(1 for v in reports if v["passed_frozen_rule"]), len(reports))
            _count(counts, "S2.strict_reading_share",
                   sum(1 for v in reports if v["passed_strict_reading"]), len(reports))
            _count(counts, "S2.instances_ran",
                   sum(int(v.get("instances_ran") or 0) for v in reports),
                   sum(int(v.get("instances") or 0) for v in reports))
            _count(counts, "S2.measured_true_per_ran_instance",
                   sum(int(v.get("fully_measured_true") or 0) for v in reports),
                   sum(int(v.get("instances_ran") or 0) for v in reports))
            covered_axes = [set(v.get("axes_covered") or []) for v in reports]
            _count(counts, "S2.all_three_axes",
                   sum(1 for a in covered_axes if a >= set(UNSEEN_AXES)), len(reports))
            _count(counts, "S3.axes_at_least_two",
                   sum(1 for v in reports if len(v.get("kinds_covered") or []) >= 2), len(reports))
            _count(counts, "S3.axes_left_unvaried",
                   sum(1 for a in covered_axes if a and not a >= set(UNSEEN_AXES)), len(reports))
            _count(counts, "S3.axes_named_unavailable",
                   sum(1 for v in reports if v.get("axes_unavailable")), len(reports))
            _count(counts, "S5.refused_after_validation", len(refusals), len(reports))
            _count(counts, "S5.invalid_skill_admitted",
                   sum(1 for a in admissions if _admitted_against_its_gate(a)), len(admissions))
            _count(counts, "S4.rounds_offering_a_program", int(r.get("offer_rounds") or 0),
                   rounds if r.get("library_at_start") else 0)
            # Decision 3: the numerator is withheld by the arm, so this reader files a zero
            # *denominator* and lets `not_measured_reason` carry the sentence. The two rows above are
            # the honest content of §11's reuse row on a batch that cannot choose.
            _count(counts, "S4.counted_as_reuse",
                   sum(1 for x in reuse if x["counts_as_reuse"]), 0)
            _count(counts, "C2.model_calls", int(cost.get("model_calls") or 0), 1)
            _count(counts, "C2.simulator_seconds_per_sandbox_run",
                   float(cost.get("sim_s") or 0.0), int(cost.get("sandbox_runs") or 0))
            _count(counts, "C2.generation_wall_seconds_per_candidate",
                   float(cost.get("wall_s") or 0.0), int(cost.get("validated_candidates") or 0))
            _count(counts, "C2.sandbox_runs_per_candidate",
                   int(cost.get("sandbox_runs") or 0), int(cost.get("validated_candidates") or 0))
            _count(counts, "C2.generation_share_of_episode_wall",
                   float(cost.get("wall_s") or 0.0), float(ep.get("wall_time_s") or 0.0))
            fingerprints += [str(v.get("matrix_fingerprint") or "") for v in reports]

        per_episode.append({
            "episode_id": ep["episode_id"], "arm": r.get("arm"), "armed": armed,
            "gaps": len(gaps), "candidates": len(candidates),
            "candidates_executable": executable, "not_validated": len(withheld),
            "validation_reports": len(reports), "refusals": len(refusals),
            "refusals_in_store": r.get("refusals_in_store"), "admissions": len(admissions),
            "frozen_passes": sum(1 for v in reports if v["passed_frozen_rule"]),
            "strict_passes": sum(1 for v in reports if v["passed_strict_reading"]),
            "library_at_start": list(r.get("library_at_start") or []),
            "library_offered": bool(r.get("offer")),
            "offer_rounds": int(r.get("offer_rounds") or 0), "decision_rounds": rounds,
            "programs_installed": len(reuse),
            "recurred": sum(1 for x in reuse if x["procedure_matched"] and x["binding_complete"]),
            "effect_measured": sum(1 for x in reuse if x["reuse_effect_achieved"]),
            "reuse_named": sum(1 for x in reuse if x["reuse_named"]),
            "counts_as_reuse": sum(1 for x in reuse if x["counts_as_reuse"]),
            "reuse_binding": [x.get("bound_parameters") or {} for x in reuse],
            "events_filed": dict(r.get("events_filed") or {}),
            "cost": {k: cost.get(k) for k in ("wall_s", "sim_s", "calls", "model_calls",
                                              "sandbox_runs", "validated_candidates")},
        })

    # S5's unsafe row is over reports with an artifact, so its denominator cannot be per-episode.
    _count(counts, "S5.unsafe_findings", unsafe_reports, unsafe_denominator)

    # A fingerprint identifies a matrix *within a batch*, so the repeat rows are computed per batch and
    # never summed across ones (see `S3.distinct_matrices`'s forbidden cell).
    used: set[str] = set()
    distinct = repeats = 0
    for fp in fingerprints:
        if not fp:
            continue
        if fp in used:
            repeats += 1
        else:
            distinct += 1
            used.add(fp)
    _count(counts, "S3.distinct_matrices", distinct, len(fingerprints))
    _count(counts, "S3.transfer_claim_repeats", repeats, len(fingerprints))

    return {"root": batch["root"], "run_id": batch["run_id"], "set": batch["set"],
            "counts": counts, "metrics": _render(counts), "per_episode": per_episode,
            "reuse_naming_reason": naming_reasons[0] if naming_reasons else "",
            "manifest_acquisition": batch["manifest_acquisition"]}


def _decision_rounds(ep: dict) -> int:
    """Rounds this episode actually decided over, counted from its own event log.

    `offer_rounds` is the arm's count of pages it built, and the denominator has to be the same kind of
    thing from an independent record: `decision_context` is filed once per round a page was built, so a
    round that asked for nothing lowers both sides together rather than inflating the rate.
    """
    cached = ep.get("decision_rounds")
    if cached is not None:
        return int(cached)
    rounds = sum(1 for t in _event_types(ep["dir"]) if t == ROUND_EVENT)
    ep["decision_rounds"] = rounds
    return rounds


def _unsafe_of(ep_dir: str, report_row: dict) -> tuple[bool, bool]:
    """(found an unsafe finding, is the evidence present) for one report row.

    The episode summary carries the *count* of instances, not their findings, so the unsafe channel is
    read from the validation artifact the arm wrote beside the episode — and a report whose artifact is
    missing leaves this row's denominator rather than being counted as clean. That distinction is the
    whole content of `S5.unsafe_findings`: 0 with a denominator means "looked, nothing", and this
    function is the only place the difference can be lost.
    """
    relative = str(report_row.get("artifact") or "")
    if not relative:
        return False, False
    path = os.path.join(ep_dir, relative)
    if not os.path.exists(path):
        return False, False
    try:
        artifact = _load(path)
    except (json.JSONDecodeError, OSError):
        return False, False
    instances = ((artifact.get("report") or {}).get("instances")) or []
    return any(i.get("unsafe") for i in instances if i.get("ran")), True


def _admitted_against_its_gate(admission: dict) -> bool:
    """An admission whose own stored provenance contradicts it.

    `SkillMemory.admit` writes `passed_frozen_rule` and `strict_findings` into the admission block at
    the moment of admitting, so this is the library's own record being read back rather than a second
    opinion computed here.
    """
    block = dict(admission.get("admission") or {})
    return block.get("passed_frozen_rule") is False or bool(block.get("strict_findings"))


# ------------------------------------------------------------------------ pooling ----
def measure_roots(roots: list[str]) -> dict[str, Any]:
    """Every §11 row over a set of batches, pooled by summing integers.

    Per-batch numbers stay in `batches`, because one pooled figure across a cold-library batch and a
    warm-library one would be the merge P2-e and P3-e both refused: the two have different denominators
    by design, and the difference between them is the experiment.
    """
    counts: dict[str, list[float]] = {}
    batches = []
    for root in roots:
        scored = score_rounds(read_batch(root))
        _merge(counts, scored["counts"])
        batches.append(scored)
    rows = _render(counts)
    if len(batches) > 1:
        # Two rows' own `forbidden` cells refuse the pooling every other row is built on, so the pooled
        # table says so explicitly rather than publishing a number the 口径 declares meaningless.
        for metric in sorted(PER_BATCH_METRICS):
            rows[metric] = dict(_rate(metric, 0, 0), not_measured_reason=(
                f"a matrix fingerprint is unique within one batch by construction; pooled over "
                f"{len(batches)} batches this row approaches 1.0 and says nothing, so it is published "
                f"per batch in `batches[].metrics` instead"))
    copied = next((b["reuse_naming_reason"] for b in batches if b["reuse_naming_reason"]), "")
    if copied:
        # `S4.counted_as_reuse`'s definition promises the arm's own sentence, so the sentence the
        # artifact carries is the one `arm.py` wrote on the reuse row — `REUSE_NAMING_REASON` is only
        # the fallback for a batch that filed no reuse row to copy from.
        rows["S4.counted_as_reuse"] = dict(rows["S4.counted_as_reuse"], not_measured_reason=copied)
    return {
        "kind": "skill_acquisition_metrics",
        "metric_version": METRIC_VERSION,
        "spec": "SPEC-v0.2 §11 Skill Acquisition + Cost, §5.6's pipeline, §13 P4-e",
        "pooled_by": "summing integers across batches; never averaging per-episode fractions",
        "offline": True,
        "cost_estimate_usd": None,
        "roots": [b["root"] for b in batches],
        "batches": [{"root": b["root"], "run_id": b["run_id"], "set": b["set"],
                     "metrics": b["metrics"], "per_episode": b["per_episode"],
                     "reuse_naming_reason": b["reuse_naming_reason"],
                     "manifest_acquisition": b["manifest_acquisition"]} for b in batches],
        "metrics": rows,
        "definitions": METRIC_DEFINITIONS,
        "spec_rows": {label: list(ids) for label, ids in SPEC_ROWS},
        "rows_without_an_id": dict(ROWS_WITHOUT_AN_ID),
        "not_measured": {k: _NOT_MEASURED[k] for k in sorted(_NOT_MEASURED)},
        "ratio_metrics": sorted(RATIO_METRICS),
        "per_batch_metrics": sorted(PER_BATCH_METRICS),
        "metric_envelope": ("every row carries `measured`, `not_measured_reason`, "
                            "`denominator_too_small_for_a_rate`, `interval_kind`, `wilson_95`, "
                            "`spec_row`, `question`, `unit`, `as_computed` — LH-2's and MEM-1's shape, "
                            "so §11's three behaviour groups are read the same way in P5's report"),
        "notes": {
            "reuse_row": {"why_the_behavioural_pair_instead": REUSE_NAMING_REASON,
                          "self_report": "`reuse_named` is false for every program on a batch whose "
                                         "policy cannot name one — see per_episode[].reuse_named"},
            "refusals_in_store": "the live store's running total across a batch, filed per episode for "
                                 "audit; `S5.refused_after_validation` sums the per-episode lists",
            "gate_readings": "`S2.frozen_rule_share` and `S2.strict_reading_share` are two readings of "
                             "the same instances, not a rate and its check",
            "admission_gate": ("the library's door is the conjunction: `memory.admission_findings` "
                               "refuses on the frozen rule and on the strict reading, so an admission "
                               "needs both and `S2.frozen_rule_share` is not an admission rate. Where "
                               "the two disagree, `batches[].per_episode[].refusals` is the count of "
                               "programs the strict reading stopped and the frozen rule would have let "
                               "in — the only place this artifact records what §12.2's frozen gate "
                               "would have cost"),
            "arms": "`S1`-`S3`/`S5` have no denominator on `wo_skill_acquisition`, which is why the "
                    "reuse group is counted into one id per arm rather than pooled",
            "unarmed_episodes": "an episode whose summary has no `skill_acquisition` block makes this "
                                "reader refuse (§5.1: the absence has a name)",
            "interval_note": "a Wilson interval on these denominators is the width of one count, not an "
                             "uncertainty on an effect size",
        },
    }


def _definitions_payload() -> dict:
    """The 口径 as one object, so `--definitions`, the artifact and the table cannot drift apart."""
    return {"metric_version": METRIC_VERSION,
            "spec": "SPEC-v0.2 §11 Skill Acquisition + Cost, §5.6's pipeline, §13 P4-e",
            "spec_rows": {label: list(ids) for label, ids in SPEC_ROWS},
            "rows_without_an_id": dict(ROWS_WITHOUT_AN_ID),
            "definitions": METRIC_DEFINITIONS,
            "ratio_metrics": sorted(RATIO_METRICS),
            "per_batch_metrics": sorted(PER_BATCH_METRICS),
            "not_measured": {k: _NOT_MEASURED[k] for k in sorted(_NOT_MEASURED)}}


def check_arms(measured: dict[str, Any]) -> list[str]:
    """The gate checked against the records rather than assumed.

    `wo_skill_acquisition` is a module-level ablation only if no `skill_*` record was filed, and `full`
    is armed only if the four records the registry owns are in its log in the numbers its own report
    claims. Both are read from the episode's `events.jsonl`, because the report that would assert them
    is the thing under test. Returns a list of problems: empty means the artifact's arms mean what they
    say.
    """
    problems: list[str] = []
    for entry in measured["batches"]:
        for ep in entry["per_episode"]:
            directory = os.path.join(entry["root"], "episodes", str(ep["episode_id"]))
            types = _event_types(directory)
            filed = {t: sum(1 for x in types if x == t) for t in OWNED_EVENT_TYPES}
            claimed = ep.get("events_filed") or {}
            if ep["armed"]:
                if claimed and claimed != filed:
                    problems.append(f"{ep['episode_id']}: report claims {claimed}, its own log holds "
                                    f"{filed}")
                if ep["gaps"] and not any(filed.values()):
                    problems.append(f"{ep['episode_id']}: a gap is in the report and no `skill_gap` "
                                    f"record is in the log, so the row describes a stage that never "
                                    f"filed anything")
            elif any(filed.values()):
                problems.append(f"{ep['episode_id']}: filed {filed} on the arm that switches the "
                                f"module off")
    return problems


# ------------------------------------------------------------------------ printing ----
def render_definitions() -> str:
    """The 口径 as it ships: one block per id, in §11's row order, six 格 each."""
    out = [f"SPEC-v0.2 §11 Skill Acquisition 口径 — {METRIC_VERSION}",
           f"{len(METRIC_DEFINITIONS)} 个 id 铺满 §11 Skill Acquisition 的 {len(SKILL_GROUP_ROWS)} 行；"
           f"Cost 组 {len(COST_GROUP_ROWS)} 行里 "
           f"{len(COST_GROUP_ROWS) - len(ROWS_WITHOUT_AN_ID)} 行有 id，其余在末尾按行名声明归谁测。"
           "每一格都写它拒绝被读成什么。",
           ""]
    for row, ids in SPEC_ROWS:
        out.append(f"### §11 行: {row}")
        for metric in ids:
            d = METRIC_DEFINITIONS[metric]
            out.append(f"- `{metric}` — {d['question']}")
            for key in _METRIC_KEYS:
                if key != "question":
                    out.append(f"    - {key}: {d[key]}")
            out.append("    - interval: none (count per unit)" if metric in RATIO_METRICS
                       else "    - interval: Wilson 95%")
        out.append("")
    if ROWS_WITHOUT_AN_ID:
        out.append("### §11 Cost 组里本口径不设 id 的行")
        for name in COST_GROUP_ROWS:
            if name in ROWS_WITHOUT_AN_ID:
                out.append(f"- `{name}` — {ROWS_WITHOUT_AN_ID[name]}")
        out.append("")
    return "\n".join(out)


def _num(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def render_table(measured: dict[str, Any]) -> str:
    out = [f"§11 Skill Acquisition — {METRIC_VERSION}（pooled over {len(measured['roots'])} batch(es): "
           + ", ".join(os.path.basename(r) for r in measured["roots"]) + "）", "",
           "| §11 行 | metric | 值 | as_computed | 分子/分母 | 95% Wilson | measured |",
           "|---|---|---|---|---|---|---|"]
    for row, ids in SPEC_ROWS:
        for metric in ids:
            r = measured["metrics"][metric]
            interval = ("—" if not r["wilson_95"]
                        else f"{r['wilson_95'][0]:.3f}–{r['wilson_95'][1]:.3f}")
            value = "not measured" if not r["measured"] else f"{r['value']:.4g}"
            out.append(f"| {row} | `{metric}` | {value} | {r['as_computed']} | "
                       f"{_num(r['numerator'])} / {_num(r['denominator'])} | {interval} | "
                       f"{'yes' if r['measured'] else 'no'} |")
    out.append("")
    for metric, r in measured["metrics"].items():
        if not r["measured"]:
            out.append(f"- `{metric}` 未测量：{r['not_measured_reason']}")
    return "\n".join(out)


def render_episodes(measured: dict[str, Any]) -> str:
    header = ("| episode | arm | gaps | cands | exec | not-val | reports | refr | in-store | adm | "
              "recurred/installed | effect | named | offer rounds/rounds | wall_s | sim_s | runs |")
    out = ["per episode（池化之前的原始计数；每一集一行）", "", header,
           "|" + "---|" * 17]
    for entry in measured["batches"]:
        for ep in entry["per_episode"]:
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {}/{} | {} | {} | {}/{} | "
                       "{} | {} | {} |".format(
                           ep["episode_id"], ep["arm"], ep["gaps"], ep["candidates"],
                           ep["candidates_executable"], ep["not_validated"],
                           ep["validation_reports"], ep["refusals"], ep["refusals_in_store"],
                           ep["admissions"], ep["recurred"], ep["programs_installed"],
                           ep["effect_measured"], ep["reuse_named"], ep["offer_rounds"],
                           ep["decision_rounds"], _num(ep["cost"]["wall_s"]),
                           _num(ep["cost"]["sim_s"]), ep["cost"]["sandbox_runs"]))
    return "\n".join(out)


def render_markdown(measured: dict[str, Any]) -> str:
    """The whole group as one block: the rows, the raw counts they were summed from, the notes.

    No `*` appears anywhere in here, because a markdown table cell containing one is read as emphasis
    by the renderer P5's report is opened in and the digits disappear — the defect P2-e caught at
    report time rather than at write time, so the rule is asserted in the contract tests, not here.
    """
    audit = measured.get("arm_audit") or {}
    out = ["## §11 Skill Acquisition — " + METRIC_VERSION, ""]
    if audit:
        out.append(("arm enforcement: OK" if audit.get("ok") else "arm enforcement: FAILED")
                   + f" — {len(measured['roots'])} batch(es) re-checked against their own event logs")
        out.append("")
    out += [render_table(measured), ""]
    without = measured.get("rows_without_an_id") or {}
    if without:
        # printed, not just filed: a reader of the table alone must be able to see that §11's Cost group
        # has five rows and that the fifth is answered elsewhere rather than forgotten here
        out += ["### §11 Cost 组里本口径不设 id 的行", ""]
        out += [f"- `{name}` — {without[name]}" for name in COST_GROUP_ROWS if name in without]
        out.append("")
    out += [render_episodes(measured), "", "### notes", ""]
    for key in sorted(measured["notes"]):
        value = measured["notes"][key]
        if isinstance(value, dict):
            out.append(f"- `{key}`")
            out += [f"    - {k}: {v}" for k, v in sorted(value.items())]
        else:
            out.append(f"- `{key}`: {value}")
    for problem in audit.get("problems") or []:
        out.append(f"- ARM PROBLEM: {problem}")
    return "\n".join(out)


def write(result: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"skill_acquisition_{METRIC_VERSION.replace('-', '_')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    with open(os.path.join(out_dir, "skill_acquisition.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(result))
    return path


def main(argv: list[str] | None = None) -> int:
    """`--definitions` prints the 口径; `--measure DIR [DIR ...] [--out DIR]` prints what it computes.

    One entry point for both, so what the CLI prints and what the arithmetic used cannot fork — the
    reason P3-e's `--memory-metrics` delegates rather than reimplements. Exit codes: 2 is a usage
    problem, 3 means a `--measure` target is not a scoreable run directory, 4 means the artifacts' arms
    do not mean what their own logs say — a refusal to publish, not a table with a caveat.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: skill-metrics --definitions | --measure DIR [DIR ...] [--out DIR]"
    # a flag, not a positional: `--measure DIR --definitions` and `--definitions --measure DIR` are the
    # same request, and refusing one of the two orders would be a trap rather than a rule
    want_definitions = "--definitions" in argv
    argv = [a for a in argv if a != "--definitions"]
    if "--out" in argv and "--measure" not in argv:
        print(usage)
        print("`--out` is only meaningful with `--measure`; nothing was written.")
        return 2
    if want_definitions and "--measure" not in argv:
        print(json.dumps(_definitions_payload(), ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    if "--measure" not in argv:
        print(usage)
        return 2
    roots: list[str] = []
    out_dir = None
    rest = argv[argv.index("--measure") + 1:]
    while rest:
        token = rest.pop(0)
        if token == "--out":
            if not rest:
                print(usage)
                print("`--out` needs a directory.")
                return 2
            out_dir = rest.pop(0)
        elif token.startswith("--"):
            print(usage)
            print(f"unknown flag for --measure: {token}")
            return 2
        else:
            roots.append(token)
    if not roots:
        print(usage)
        print("--measure needs at least one run directory.")
        return 2
    try:
        measured = measure_roots(roots)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"--measure refused: {type(e).__name__}: {e}")
        return 3
    problems = check_arms(measured)
    measured["arm_audit"] = {"ok": not problems, "problems": problems}
    if want_definitions:
        # both at once is the order the deliverable is read in: the 口径, then the rows under it
        print(render_definitions())
    print(render_markdown(measured))
    if problems:
        # Nothing is written, because the file's own table would be a §11 measurement whose arms its
        # event logs contradict — and an artifact named `skill_acquisition_SKILL_1.json` is read as a
        # deliverable long after the line saying FAILED scrolled past. The rows stay on stdout.
        print("arm enforcement FAILED: nothing was written")
        for problem in problems:
            print(f"  - {problem}")
        return 4
    if out_dir:
        print(f"\nwrote {write(measured, out_dir)}")
    print(f"arm enforcement: OK — {len(measured['batches'])} batch(es), "
          f"{sum(len(b['per_episode']) for b in measured['batches'])} episode(s) re-checked against "
          f"their own event logs")
    return 0


__all__ = ["METRIC_VERSION", "UNSEEN_AXES", "OWNED_EVENT_TYPES", "ROUND_EVENT", "SPEC_ROWS",
           "SKILL_GROUP_ROWS", "COST_GROUP_ROWS", "ROWS_WITHOUT_AN_ID",
           "RATIO_METRICS", "PER_BATCH_METRICS", "METRIC_DEFINITIONS", "REUSE_NAMING_REASON",
           "read_batch", "score_rounds", "measure_roots", "check_arms", "render_definitions",
           "render_table", "render_episodes", "render_markdown", "write", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
