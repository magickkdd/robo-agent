"""§11's VLM and Cost metrics — definitions written before the numbers.

SPEC-v0.2 §11 lists what a run must report, and §13 P1 requires the last bullet of that
phase: a `privileged` vs `VLM` 对照. Nothing in the codebase defined those metrics, and an
undefined metric is worse than a missing one: `work/p1_state_check.py` and
`work/p1_verify_check.py` produced numbers (15/15, 2/60, `cylinder 0/55`) that the phase log
had to explain by hand, and P1-c residual 1 and P1-d residual 4 are the same complaint
recorded twice — *"先写口径再算数"*. This file is that write-up, as data: every rate it emits
is rendered from `METRIC_DEFINITIONS` below, carries its own numerator and denominator in the
output, and is pooled by summing integers rather than by averaging per-case fractions.

## The three decisions this module exists to make

**1. §11's "state change detection" is counted from the perceiver's ledger, never from a
snapshot diff** (P1-c residual 1).

`PerceptWorldStateTracker.ledger` holds the `ChangeRecord`s the assembler reported.
`.audits` holds two kinds of audit row, one of which (`cross_view_check`) exists precisely
because the perceiver *declined* to compare anything. A real placement made while the agent
looked away appears in the second and not the first: P1-c measured `ledger_rows = 0` next to
a recovered `cross_view_disagreements` row. Merging them would make §11's number look better
by answering a different question — §5.1 assigns change detection to the perceiver, so a
comparison the perceiver refused is not a detection it made. Both quantities are therefore
reported side by side under separate ids (`V2.change_detection` / `V2.audit_differences` /
`V2.cross_view_recovery`), the aggregate output carries a `ledger_vs_audits` block whose
`merged` field is always `False`, and the difference between the two accounts is published as
a finding rather than dissolved into a metric.

**2. An over-claim is a sensor `true` where the answer key says not-placed, counted over
every postcondition asked** (P1-d residual 4).

P1-d wrote "2/60" without saying what the 60 was. It is: one `placed:<body>:<tray>` question
per body, per declared view, per physical configuration — three seating geometries plus the
untouched table, where the two whole-table states (`untouched`, `crowded`) are asked of every
body and the two per-body states (`alone`, `wall`) are asked of the body that round seated.
The same frames also answer the *other* bodies of a per-body round, and those 120 rows are
kept as a separate control set (`V3.control_over_claim`): they measure a different physical
situation — something really is in the tray, which is where a support-height channel can
confuse a neighbour's top with a tray floor — and the two are never summed. The answer key is
deliberately *the same conjunction the sensor claims*
(support names the target **and** the orientation-invariant footprint clears the wall by the
declared margin), graded on privileged metres and the body's real orientation — not the
privileged verifier's full verdict, which additionally promises `at_rest` and `not_held`.
Grading against the wider conjunction would turn a body still settling into a recorded
over-claim, i.e. measure layer 1's patience with layer 2's evidence. Two quantities a reader
might want folded into that rate are reported as their own metrics instead:
`V3.withheld_rate` (sensor said `unknown`, §11's unknown-handling success case) and
`V3.conservative_miss_rate` (sensor said `false` where the key says placed — costs a planned
move, wrecks nothing).

Every configuration's table is restored from the case's *declared* poses, captured before any
battery ran. Restoring to a pose read at the time is what a first draft did, and it silently
graded the untouched control on the table the change battery had left behind — a cube seated
in the target, and four collisions attributed to the sensor.

**3. Two arms do not mean the same thing by every column, and the contrast says which**
(P1-e residual 3).

`ARM_ASYMMETRIC_FIELDS` names the episode fields whose *units* change with the channel: the
§6.2 repeated-invalid-action guard is keyed on the observation fingerprint, which P1-e measured
equal for two looks from one camera and 13.0–18.0 mm apart for any two cameras, so a sensing
episode can reset its own repeat count by naming another view and a privileged one cannot;
`http_requests` is enforced on one arm and merely reported on the other. A second list,
`BUDGET_CENSORED_FIELDS`, holds the fields that mean the same thing on both arms but are
*truncated* on one — `decision_rounds` and `skill_calls` both sit at the 24-round cap on 5 of the
8 dev pairs, so their mean difference is a lower bound. Both differences are still printed —
deleting them would be the concealment — but with a `†` / `‡` and the reason beside it, so a
table cannot average a change of instrument, or a censored column, into a difference in
capability.

The distinction is not cosmetic, and this module got it wrong first: an earlier draft called
`objects_completed` the loop's own counter and daggered it. `_csv_row` fills that column from
`score` — layer 3, read off the simulator on both arms — so it is comparable, and the loop's
counter is a *different* field (`result.objects_completed`). Printing one as a difference over the
other would have been exactly the swap the `†` marks exist to catch. They are therefore printed
side by side as `progress_self_report_vs_key`, per arm: on the privileged arm the two instruments
agree on all 8 dev episodes, and on the sensing arm the loop credited itself with one more
completed object than the key did on 4 of 8 — the over-claim lives at progress level, while
`false_finish_attempts` stays 0 because this arm is never allowed to finish (P1-e §4-6).

## What is deliberately *not* a rate, and what is not a difference

`held`, `at_rest` and both speeds have no pixel channel at all. A camera answering `unknown`
for them is a correct statement about the instrument, so `V3.channel_silent` counts them as a
property of the channel and no headline metric divides by it. Same for a body located to no
metres (`V3.unlocated`): §5.1 asks for that row.

`outcome` and `failure_type` are *kinds*, and the episode contrast reports them as kinds
(`CONTRAST_CATEGORIES`): a per-arm value count plus the paired transition count, so the headline
is `success -> BUDGET_EXHAUSTED ×5` rather than a mean over strings. The corresponding trap is
coercion: `episodes.csv` returns an empty cell as `""`, and `float("" or 0)` is 0.0 — on the
first dev run that made two episodes which failed *without a recorded failure type* pair against
the privileged arm's empty one and print `mean Δ 0.0`. A missing kind is named
`ABSENT_CATEGORY` and counted, never averaged.

The same rule then caught its first exception. `agent_claims_success` and
`independent_complete_success` are the two columns the contrast exists to read — what the loop
claimed, and what the answer key graded — and the first real dev run printed them as *excluded*
("not a quantity on every pair", all eight pairs), because `load_rows` parses them into Python
bools and `_number` refuses a bool: `True` is not a 1 that may be averaged. That refusal is
correct, and the exclusion list did exactly what it was built to do — on the one column where "I
cannot take a difference here" leaves the table without a headline. Two-valued fields are kinds
too, so they are cross-tabulated and the run says `true -> false ×8` on both. The contract tests
did not see it because their fixture wrote `1`/`0`: **a fixture more permissive than the producer
tests the fixture**, which is why `_episode` now writes what `load_rows` returns.

## Cost, and what a zero means

§11's Cost group is reported per episode, per decision round and per look. On the stub arm
`look_tokens` and `look_http_requests` are exactly zero — that is *"this arm consulted no
vision model"*, not *"this channel is free"*, and the ids exist separately so the two
sentences cannot be confused in the JSON. Skill-generation cost has no producer before P4 and
is reported as `not_measured`, not as 0.

Cost rows are also the module's only **ratios**: `looks / decision rounds` and
`tokens / episode` have no reason to be ≤ 1, so a Wilson interval is not defined on them and
`_rate` publishes `interval_kind: "ratio_of_counts"` with no interval. Every other id counts a
proportion, and a proportion whose numerator exceeds its denominator is a broken count: it
raises at render time rather than printing a number above 1. The first draft fed 40 looks over
9 rounds to `wilson_interval` and died with `math domain error` inside the production
`vlm-contrast --contrast` path — the shape the cost test now carries.

## §12 compliance, checked against this file

Same case, seed, budgets and decision source on both sides of every contrast (principle 1);
nothing here mixes a public benchmark with the custom task list (principle 2 — every case is
from the frozen custom list, and `coverage` says which); no model explanation text appears in
any numerator (principle 3); a zero token count is never read as capability (principle 5);
and the answer key only ever *grades*. The perception batteries import no runtime at all, and
the episode contrast goes through the production `run_group` rather than a simpler private
loop, so a contrast cannot quietly become a second implementation of the thing it measures.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import time
from typing import Any, Optional, Sequence

import numpy as np

from ..core.contracts import (
    SkillResult,
    SkillStatus,
    Source,
    TaskInput,
    WorldState,
    footprint_half_xy_from_quat,
    orientation_invariant_footprint_half_xy,
)
from ..core.interpreter import interpret
from ..core.verify import (
    build_world_state,
    entity_footprint_half_xy,
    entity_position,
    state_inside,
)
from ..perception.frames import camera_for, capture
from ..perception.observe import PerceptionAssembler, StubReader, TaskContext
from ..perception.verify_percept import DECLINED_FACTS, PerceptVerifier
from ..perception.world_state import (
    ASSUMED_UPRIGHT,
    PerceptWorldStateTracker,
    reconcile,
    world_state_from_percept,
)
from .calibration import cell_catalog, task_context_of
from .report import load_rows, wilson_interval
from .run import build_scene, run_group
from .tasks import SET_NAMES, build_set, find_case

#: v0.2-p1f-2: the over-claim 口径 was split into a primary set (the 60 P1-d graded) and a
#: control set, and every configuration is now restored from the case's declared poses rather
#: than from a pose read mid-run.
#: v0.2-p1f-3: the three `C1` cost metrics are ratios of counts, not proportions, so they no
#: longer carry a Wilson interval (`RATIO_METRICS`), and every proportional row now refuses to
#: render with numerator > denominator. A `v0.2-p1f-1/-2` cost row whose numerator happened to
#: sit below its denominator shows an interval computed on a quantity Wilson cannot describe.
#: v0.2-p1f-4: `X1.contrast` now names the fields the two arms do not mean the same thing by
#: (`ARM_ASYMMETRIC_FIELDS`, P1-e residual 3), and the episode contrast publishes that block with
#: a `†` on the affected rows.
#: v0.2-p1f-5: `outcome` and `failure_type` are cross-tabulated as kinds instead of differenced,
#: and an empty cell is no longer coerced to a measured 0. The first dev contrast printed
#: `failure_type: mean Δ 0.0, n=2` over the two episodes that ended `failed` with *no*
#: failure_type — the coercion had turned the finding into a zero. Bumped so a
#: `p1f-1/-2/-3/-4` artifact cannot be compared with this one by accident.
#: v0.2-p1f-6: the two boolean columns of the contrast — `agent_claims_success` (what the loop
#: said) and `independent_complete_success` (what layer 3's answer key said) — are kinds too.
#: p1f-5 printed them as `not_a_quantity_on_every_pair` over all eight dev pairs, because
#: `load_rows` parses them into Python bools and `_number` rightly refuses a bool: the module
#: honestly reported "I could not take a difference here" about the one pair of columns a reader
#: came for. The contract test missed it for the same reason every such miss happens — its fixture
#: wrote `1`/`0`. No `V*` count moves with this bump; the p1f-5 dev artifact's two dead rows are
#: the only thing that reads differently. Two more corrections in the same bump, both from reading
#: the writer instead of the comment: `objects_completed` is layer 3's count, so it leaves
#: `ARM_ASYMMETRIC_FIELDS` (which was wrong about it) and the loop's own counter is published
#: beside it as `progress_self_report_vs_key`; and the two budget-censored columns get their own
#: list (`BUDGET_CENSORED_FIELDS`, `‡`) instead of sharing the dagger list with the instrument
#: swaps, because a lower bound and a unit change are different caveats.
DEFINITION_VERSION = "v0.2-p1f-6"
DEFAULT_OUT = "/tmp/v02_p1f_contrast"
VIEWS = ("main", "front_high", "overhead")
PLACEMENT_TARGET = "tray_middle"
#: One side of the assembler's own threshold on both halves of V2: 5-25 mm straddle it,
#: 30-60 mm are moves a perceiver is expected to notice.
DISPLACEMENTS_MM = (5, 10, 15, 20, 25, 30, 40, 60)
#: The three seating geometries plus the untouched table, which is asked as a negative control:
#: the case declaration, not this line, decides whether its tray postconditions are false, and
#: the key's answer is published per row.
CONFIGURATIONS = ("alone", "crowded", "wall")
#: The `EntityState` fields §5.1 asks about that no pixel channel answers. Enumerated field
#: by field by `tests/contract/test_v02_perception_world_state.py::
#: test_every_field_the_frame_cannot_answer_is_left_unknown`.
CHANNEL_SILENT_FIELDS = ("held", "at_rest", "linear_speed_mps", "angular_speed_rps")
#: The seven things one sensor verdict can be against the key. The names carry the
#: asymmetry: `dangerous_true` tells the agent a placement succeeded when it did not,
#: `contrary_false` makes it redo work, `honest_decline` costs one more look and nothing.
VERDICT_CLASSES = ("confirmed", "dangerous_true", "contrary_false", "correct_negative",
                   "honest_decline", "declined_negative", "no_answer")
BATTERIES = ("grounding", "claims", "change", "verification")
#: The keys only the seating-height comparison produces. A sensor report carrying one of
#: these would mean the circular check P1-d declined to run came back — the same line
#: `tests/contract/test_v02_percept_verification.py::
#: test_the_seating_height_comparison_never_appears_in_a_sensor_report` holds inside the
#: verifier, at batch scale.
SEATING_HEIGHT_EVIDENCE_KEYS = ("height_err_m", "rest_z", "rest_z_expected",
                                "rest_z_measured", "rest_z_nominal")

# ----------------------------------------------------------------------- 口径 ----

#: Every number this module emits is rendered from one of these entries. The fields are
#: fixed on purpose: a metric that cannot name its unit, both sides of its fraction and
#: which channel holds the answer key has not finished being defined — and P1-f's job was to
#: finish that before computing anything.
METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "V1.grounding_accuracy": {
        "question": "does an attribute phrase bind to the body the answer key binds it to?",
        "unit": "one attribute phrase × one view of one case",
        "numerator": "phrases whose (colour, target) binding set equals the key's exactly",
        "denominator": "every phrase probed: the case utterance, one colour phrase and one "
                       "colour+shape phrase per declared body",
        "truth": "the privileged snapshot of the same scene, through the same `interpret`",
        "forbidden": "`only_percept` (the sensor bound what the key denies) is never inside "
                     "the numerator, and a phrase both channels decline stays in the "
                     "denominator so the rate cannot be inflated by dropping hard cases",
    },
    "V1.both_declined": {
        "question": "how often does the key itself fail to bind the phrase?",
        "unit": "one attribute phrase × one view of one case",
        "numerator": "phrases where neither channel bound anything",
        "denominator": "every phrase probed: the case utterance, one colour phrase and one "
                       "colour+shape phrase per declared body",
        "truth": "the privileged snapshot of the same scene, through the same `interpret`",
        "forbidden": "this is the denominator V1.grounding_accuracy refuses to subtract: a "
                     "phrase the key cannot bind is a probe that measures the probe",
    },
    "V1.grounding_over_claim": {
        "question": "how often does the sensor channel resolve a phrase the key cannot?",
        "unit": "one attribute phrase × one view of one case",
        "numerator": "phrases where the sensor bound ≥1 pair and the key bound none",
        "denominator": "every phrase probed: the case utterance, one colour phrase and one "
                       "colour+shape phrase per declared body",
        "truth": "the privileged snapshot of the same scene, through the same `interpret`",
        "forbidden": "reported only beside V1.grounding_accuracy: the same accuracy with a "
                     "zero here and without one are two different defects",
    },
    "V1.grounding_ambiguity_agreement": {
        "question": "do the two channels ask the same questions?",
        "unit": "one attribute phrase × one view of one case",
        "numerator": "phrases where `ambiguous` has the same value on both channels",
        "denominator": "every phrase probed: the case utterance, one colour phrase and one "
                       "colour+shape phrase per declared body",
        "truth": "both channels' own `ambiguous` flag on the same phrase, from the task "
                 "interpreter run twice over the two snapshots",
        "forbidden": "`only_percept` ambiguity (sensor asks, key acts) is safe and counted "
                     "in the disagreement list separately from `only_privileged` ambiguity, "
                     "which is not: acting on a phrase the key found ambiguous is a binding "
                     "no clarification can undo",
    },
    "V1.shape_vocabulary": {
        "question": "does the channel name shapes as the case declares them?",
        "unit": "one declared body × one view",
        "numerator": "bodies whose `shape` attribute equals the declaration",
        "denominator": "declared bodies × views",
        "truth": "the frozen case declaration",
        "forbidden": "P1-c measured `cylinder 0/55` on this channel and deliberately left "
                     "the 0.05 ambiguity threshold alone; this metric reports the same fact "
                     "per shape, it does not move the threshold that produced it",
    },
    "V2.change_detection": {
        "question": "when the world moved, did the perceiver report it?",
        "unit": "one commanded displacement of one body, seen twice from one camera",
        "numerator": "displacements ≥ the assembler's own `move_threshold_m` with a "
                     "`ChangeRecord` on that body naming its position",
        "denominator": "displacements ≥ that threshold",
        "truth": "the simulator's hand moved the body; the key supplies the commanded "
                 "distance, never the report",
        "forbidden": "only `PerceptWorldStateTracker.ledger` rows count. A "
                     "`cross_view_check` disagreement, an `unreported_differences` audit "
                     "entry and a diff of two snapshots are audits, not detections",
    },
    "V2.change_false_positive": {
        "question": "does the perceiver report a change on a world that did not move?",
        "unit": "one commanded displacement below the threshold, plus the still-world control",
        "numerator": "rows reported for such a displacement on the moved body",
        "denominator": "displacements below the threshold, plus 1 for the repeated baseline",
        "truth": "declared by the experiment: the base pose was not changed, or changed by "
                 "less than the threshold this same metric treats as noise",
        "forbidden": "the threshold is the one `V2.change_detection` uses — a per-metric "
                     "threshold would make the pair uninterpretable",
    },
    "V2.audit_differences": {
        "question": "did the snapshot move without a report saying so?",
        "unit": "one same-view audit row (`reconcile`)",
        "numerator": "`unreported_differences` entries",
        "denominator": "same-view audit rows",
        "truth": "both sides are sensor snapshots — there is no privileged answer here, which "
                 "is exactly what makes this a *consistency* measure and not an accuracy one",
        "forbidden": "never added to V2.change_detection's numerator or denominator",
    },
    "V2.cross_view_recovery": {
        "question": "what does the one permitted cross-view comparison recover?",
        "unit": "one cross-view audit row",
        "numerator": "rows with ≥1 `cross_view_disagreements` entry",
        "denominator": "cross-view audit rows only (same-view rows are excluded: their "
                       "disagreement list is empty by construction)",
        "truth": "the placement the experiment made between the two looks",
        "forbidden": "a recovered row is evidence for a human or for P2's working memory; it "
                     "is not a change detection and does not enter the ledger",
    },
    "V3.over_claim_rate": {
        "question": "how often does layer 2 certify a placement the answer key denies?",
        "unit": "one *primary* `placed:<body>:<tray>` question, per view, per configuration",
        "numerator": "sensor value `true` where the key says not placed",
        "denominator": "every primary question asked: the untouched table and the crowded "
                       "tray are whole-table states, so all bodies are asked (bodies × views "
                       "each); the `alone` and `wall` rounds are per-body states, so each "
                       "round is asked only of the body it seated. bodies × views × 4 "
                       "families = 60 on a 5-body case, which is the same set "
                       "`work/p1_verify_check.py` graded in P1-d",
        "truth": "the key's *same conjunction* — support names the target and the "
                 "orientation-invariant footprint clears the wall by the declared margin — on "
                 "privileged metres and the body's real orientation",
        "forbidden": "`unknown` is not an over-claim and neither is `false`; the tighter "
                     "denominator (questions whose key says not placed) is reported beside "
                     "this as V3.over_claim_among_denials, never instead of it; the extra "
                     "questions about bodies a per-body round did *not* seat are the control "
                     "set and are counted as V3.control_over_claim, never folded in here",
    },
    "V3.control_over_claim": {
        "question": "does the channel certify a body this configuration never placed?",
        "unit": "one *control* question — a body present in the frame that the round did not "
                "seat, asked while another body was in the target",
        "numerator": "sensor value `true` where the key says not placed",
        "denominator": "control questions whose key answer is not placed",
        "truth": "the privileged key's conjunction for that body on the table the round built, "
                 "taken in the same instant as the frame",
        "forbidden": "this is the negative control of the per-body rounds, so it is a "
                     "different physical situation from the `untouched` table (something *is* "
                     "in the tray, which is where a support-height channel can confuse a "
                     "neighbour's top with a tray floor); it is reported separately from "
                     "V3.over_claim_rate and the two are never summed",
    },
    "V3.over_claim_among_denials": {
        "question": "of the cases where the key says *not* placed, how many did the sensor "
                    "certify?",
        "unit": "one primary `placed:<body>:<tray>` question, per view, per configuration",
        "numerator": "primary questions where the sensor said `true` and the key says not "
                     "placed",
        "denominator": "primary questions whose key answer is not placed",
        "truth": "the privileged key's conjunction on the same table as the frame",
        "forbidden": "small denominators are flagged in the row itself "
                     "(`denominator_too_small_for_a_rate`); a rate over 3 rows is not a rate",
    },
    "V3.withheld_rate": {
        "question": "when the frame could not settle a postcondition, did it say so?",
        "unit": "one primary `placed:<body>:<tray>` question, per view, per configuration",
        "numerator": "sensor value `unknown`, whether or not the key answers the same question",
        "denominator": "every primary question asked, per the V3.over_claim_rate denominator",
        "truth": "the sensor report's own `PredicateVerdict.unknown`, which is what §11's "
                 "unknown-handling metric is defined on",
        "forbidden": "`no_answer` (the body was not in the frame at all) is an "
                     "object-detection miss, not a withheld verdict, and is counted under "
                     "V3.unseen_body",
    },
    "V3.conservative_miss_rate": {
        "question": "how much work does the channel refuse that was actually done?",
        "unit": "one primary `placed:<body>:<tray>` question, per view, per configuration",
        "numerator": "sensor value `false` where the key says placed",
        "denominator": "every primary question asked, per the V3.over_claim_rate denominator",
        "truth": "the privileged key's conjunction on the same table as the frame",
        "forbidden": "not merged with the over-claim rate: the two fail in opposite "
                     "directions and one 'accuracy' would hide both",
    },
    "V3.certified_rate": {
        "question": "how much of the completion condition can this channel confirm?",
        "unit": "one primary `placed:<body>:<tray>` question, per view, per configuration",
        "numerator": "sensor value `true` where the key says placed",
        "denominator": "primary questions whose key answer is placed",
        "truth": "the privileged key's conjunction on the same table as the frame",
        "forbidden": "a low value here beside a low over-claim rate is the conservative "
                     "channel P1-d documented; only a high value in both is a defect",
    },
    "V3.resolvable_unknown": {
        "question": "does a withheld verdict name something that could settle it?",
        "unit": "one `unknown` sensor verdict on a body present in the frame",
        "numerator": "verdicts whose `would_resolve` is non-empty (a further look exists)",
        "denominator": "`unknown` verdicts on a body present in the frame",
        "truth": "the declared camera list; no privileged read is involved at all",
        "forbidden": "a refusal pointing back at the camera that just failed does not count "
                     "as resolvable (P1-b §4-5)",
    },
    "V3.channel_silent": {
        "question": "which §5.1 fields does this channel not carry at all?",
        "unit": "one entity × one view × one field the key answers",
        "numerator": "such fields left `unknown`/`None` by the sensor snapshot",
        "denominator": "the same fields where the privileged snapshot answers",
        "truth": "the privileged snapshot",
        "forbidden": "a property of the instrument, not an error: reported, never divided "
                     "into a headline metric",
    },
    "V3.unlocated": {
        "question": "how often is a body seen without metres?",
        "unit": "one entity × one view",
        "numerator": "entities with `pose is None`",
        "denominator": "entities in the snapshot",
        "truth": "not graded — there is no key for a body the frame declined to locate",
        "forbidden": "never counted as a grounding failure: §5.1 asks for this row",
    },
    "V3.unseen_body": {
        "question": "how often is a declared body not reported by a view at all?",
        "unit": "one declared body × one view",
        "numerator": "bodies absent from that view's entity list",
        "denominator": "declared bodies × views",
        "truth": "the case declaration",
        "forbidden": "an occlusion answer is reported beside it (V3.unseen_but_named_blocker), "
                     "because 'hidden behind the yellow cube' and 'not looked for' resolve "
                     "differently for a planner",
    },
    "V3.unseen_but_named_blocker": {
        "question": "of the bodies a view did not report, how many did it say *why*?",
        "unit": "one declared body × one view, among the unseen",
        "numerator": "unseen bodies with a `visibility` row naming an occluder",
        "denominator": "unseen bodies",
        "truth": "the percept's own visibility group",
        "forbidden": "a silent absence and an explained absence are different products; this "
                     "is the ratio between them",
    },
    "C1.looks_per_round": {
        "question": "how much camera does one decision cost?",
        "unit": "one episode's totals",
        "numerator": "looks taken",
        "denominator": "decision rounds",
        "truth": "the episode record; nothing is graded here",
        "forbidden": "an unbudgeted quantity today — no `Budgets` field caps looks — and "
                     "labelled as such rather than as compliant (P1-e residual 1)",
    },
    "C1.model_calls": {
        "question": "how many vision-model requests did the observation channel spend?",
        "unit": "one episode",
        "numerator": "`http_requests` attributed to looks",
        "denominator": "episodes in the arm",
        "truth": "the perceiver's own accounting",
        "forbidden": "0 on the stub arm means no model was consulted, not a cheap channel",
    },
    "C1.tokens": {
        "question": "tokens spent on looks, per episode",
        "unit": "one episode",
        "numerator": "summed prompt+completion tokens of look requests",
        "denominator": "episodes in the arm",
        "truth": "provider usage records",
        "forbidden": "never compared against a capability claim (§12.5)",
    },
    "X1.contrast": {
        # not a fraction: a definition of which two episodes may be subtracted from each other
        "question": "what does the same decision source cost, and fail on, once its state "
                    "comes from a camera instead of from the simulator?",
        "unit": "one (case_id, mode, repeat) triple present in *both* arms",
        "numerator": "pairs whose goal resolution is identical, so their per-field differences "
                     "describe one episode measured two ways",
        "denominator": "pairs attempted — the episodes both arms ran; `unpaired` and "
                       "`not_comparable` rows are excluded from every difference",
        "truth": "the production episode record each arm writes through `run_group`, on the "
                 "same budgets, seeds and task text; the privileged channel is the comparison "
                 "arm, never the evidence of a sensing row",
        "forbidden": "no difference is computed across unpaired episodes or across a pair whose "
                     "goal resolution differs, and neither kind is deleted to make the table "
                     "look complete: both are published as `unpaired` / `not_comparable` lists "
                     "beside the counts they reduce. No mean is taken over a kind — `outcome`, "
                     "`failure_type` and the two booleans that say what the loop claimed and "
                     "what the key graded (`CONTRAST_CATEGORIES`) are cross-tabulated — and an "
                     "empty cell is a missing value, never a zero. Fields whose units change "
                     "with the channel are declared in `ARM_ASYMMETRIC_FIELDS` (`†`) rather than "
                     "subtracted anyway — §6.2's guard among them — and fields that one arm's "
                     "budget truncates are declared in `BUDGET_CENSORED_FIELDS` (`‡`), because a "
                     "lower bound is not a measurement. The loop's own progress counter and the "
                     "key's are never differenced against each other across arms: they are two "
                     "instruments, and each is compared to the other within its own arm "
                     "(`progress_self_report_vs_key`)",
    },
}

#: The definitions whose numerator is not a subset of the denominator: looks per round, calls
#: per episode, tokens per episode. Every other id in `METRIC_DEFINITIONS` counts a proportion,
#: so `numerator <= denominator` holds by construction and a Wilson interval means something.
#: On a ratio it does not: `p = k/n > 1` makes `p*(1-p)` negative and `wilson_interval` raises
#: `math domain error` (found by running `cost_block` on a real episode pair, not by reading it).
#: These rows publish `value` — the arithmetic mean the 口径 asks for — and no interval.
RATIO_METRICS = frozenset({"C1.looks_per_round", "C1.model_calls", "C1.tokens"})


class ContrastConfig(ValueError):
    """The caller asked for a measurement that does not exist. A distinct type so an
    arithmetic fault inside this module cannot be reported to the user as their mistake."""


#: The episode quantities the contrast reports. Every one is a field a production run already
#: writes into `episode_summary.json` / `episodes.csv`; nothing here re-derives a number the
#: loop could have reported better.
CONTRAST_FIELDS = ("outcome", "failure_type", "independent_complete_success",
                   "objects_completed", "objects_total", "agent_claims_success",
                   "decision_rounds", "skill_calls", "http_requests", "prompt_tokens",
                   "completion_tokens", "sim_time_s", "wall_time_s", "false_finish_attempts",
                   "identical_invalid_attempts", "rejected_decisions", "model_errors")
#: The contrast fields that are kinds rather than quantities: they are reported as a per-arm
#: value count and a paired transition count ("success -> BUDGET_EXHAUSTED": 5), never as a
#: difference. `ABSENT_CATEGORY` is what an empty cell is called — on the privileged arm
#: `failure_type` is empty *because it succeeded*, so reading it as 0 would compare a success
#: with a failure and call the result a zero difference.
#: The two booleans belong here for the same reason and were not here in the first draft.
#: `load_rows` parses `independent_complete_success` and `agent_claims_success` into **Python
#: bools**, and `_number` refuses a bool on purpose (`True` is not a 1 that may be averaged): the
#: first dev contrast therefore printed both in `not_a_quantity_on_every_pair` — the module's own
#: honest exclusion list, holding the headline. The contract tests missed it because their fixture
#: wrote `1`/`0`, which *is* a number: a fixture shaped more permissively than the producer tests
#: the fixture (`_episode` now writes bools, like `load_rows` does).
CONTRAST_CATEGORIES = ("outcome", "failure_type", "independent_complete_success",
                       "agent_claims_success")
ABSENT_CATEGORY = "(absent)"
#: The only cell values that are canonicalised before counting, because the two producers of a
#: contrast row spell them differently: the rows `load_rows` returns carry Python bools, whose
#: `str()` is `True`, and a JSON dump of the same fact carries `true`. Nothing else in a record is
#: renamed, so an enum name stays greppable.
BOOLEAN_KIND = {"true": "true", "false": "false"}
DEFAULT_CONTRAST_MODES = ("B",)

#: Which of those fields the two arms do *not* mean the same thing by. P1-e residual 3 assigned
#: this list to P1-f, and every reason below is a measured difference between the arms, not a
#: suspicion: subtracting one from the other produces a number whose units change with the
#: channel, which is the exact failure §12.1 exists to prevent. The fields stay in `pairs` —
#: deleting them would hide the asymmetry — but the contrast publishes this alongside them.
#: `objects_completed` is **not** in here, and an earlier draft of this list said it was. The
#: claim was that the column is the loop's own progress counter; `_csv_row` (`run.py:624`) fills it
#: from `score`, i.e. from layer 3, so it is the one progress number that *is* comparable across
#: arms. The loop's counter is `result.objects_completed`, and it is reported separately, in
#: `progress_self_report_vs_key`. Asserting an instrument without reading the writer is the same
#: mistake as P1-b §4-6, with the roles reversed: the code was right and the comment was wrong.
ARM_ASYMMETRIC_FIELDS = {
    "identical_invalid_attempts":
        "§6.2's guard is keyed on the observation fingerprint, and the fingerprint is equal for "
        "two looks from one camera and different for any two cameras (P1-e: 13.0–18.0 mm on an "
        "untouched table). A sensing episode can therefore reset its own repeat count by naming "
        "another view; a privileged episode has no such move",
    "http_requests":
        "the privileged arm's budget is reported and not enforced (P1-e residual 2) while the "
        "sensing arm's can end an episode (P1-e's 40-looks/9-rounds trip), so equal numbers are "
        "not equal compliance",
    "wall_time_s":
        "P1-e residual 5: one stub episode wrote 196 MB of frames over 105 looks, so rendering "
        "and segmentation are inside the sensing arm's wall time and inside nobody's budget. The "
        "column is also the least reproducible one here: three earlier runs of this same 8-episode "
        "batch summed to 474 s, 574 s and 627 s of sensing wall time on code differing in nothing "
        "but this text, while their simulated time was 246.59 s every time (episodes run one after "
        "another in one process, so wall is machine load). Whatever this row says is a fourth "
        "sample of that spread, and the spread is the point — a difference of tens of seconds is "
        "not an effect of the channel",
}

#: Fields that mean the same thing on both arms but are *truncated* on one: a shared budget the
#: sensing arm spends first. These differences are lower bounds, and a mean printed without this
#: mark reads as a completed measurement.
BUDGET_CENSORED_FIELDS = {
    "decision_rounds":
        "5 of the 8 dev pairs end at the 24-round budget on the sensing arm (failure type "
        "`BUDGET_EXHAUSTED`), so the arm's round count is censored at 24 and the difference is a "
        "lower bound on what it would have spent, not a measurement of it. The privileged arm's "
        "same-episode counts for those cases are 9–11 (P1-e 表 11 measured 11 against 24 on "
        "dev_c5)",
    "skill_calls":
        "the same 5 pairs report exactly 24 skill calls, which is the round cap and not a plan "
        "length; every other field on those rows is truncated by the same event",
}


# -------------------------------------------------------------- plumbing ----


def _rate(definition_id: str, numerator: int, denominator: int, **extra) -> dict[str, Any]:
    """A fraction that cannot be misread: the numbers, the words, and an interval only where an
    interval is defined. A proportion gets Wilson; a ratio of counts gets the mean and no
    interval — see `RATIO_METRICS`."""
    d = METRIC_DEFINITIONS[definition_id]
    n, dn = int(numerator), int(denominator)
    if definition_id in RATIO_METRICS:
        interval, kind = None, "ratio_of_counts"
    else:
        if n > dn:
            raise AssertionError(f"{definition_id}: a proportion cannot have numerator {n} > "
                                 f"denominator {dn}; the counting site is wrong")
        interval = list(wilson_interval(n, dn)) if dn else None
        kind = "proportion"
    return {
        "definition_id": definition_id,
        "numerator": n,
        "denominator": dn,
        "value": (round(numerator / denominator, 4) if denominator else None),
        "wilson_95": interval,
        "interval_kind": kind,
        "denominator_too_small_for_a_rate": 0 < denominator < 10,
        "question": d["question"],
        "unit": d["unit"],
        "as_computed": f"{d['numerator']} / {d['denominator']}",
        **extra,
    }


def _step(counts: dict[str, tuple[int, int]], key: str, num, den) -> None:
    """Accumulate one contribution. Every counted id must have a definition — a count nobody
    defined is a number that will be misread, so this refuses rather than filing it."""
    if key not in METRIC_DEFINITIONS:
        raise KeyError(f"counted metric {key!r} has no entry in METRIC_DEFINITIONS")
    n, d = counts.get(key, (0, 0))
    counts[key] = (n + int(num), d + int(den))


def _merge(target: dict[str, tuple[int, int]], counts: dict[str, tuple[int, int]]) -> None:
    for k, (n, d) in counts.items():
        pn, pd = target.get(k, (0, 0))
        target[k] = (pn + n, pd + d)


def _render(counts: dict[str, tuple[int, int]]) -> dict[str, dict[str, Any]]:
    return {k: _rate(k, n, d) for k, (n, d) in sorted(counts.items())}


def _guard_sensor(state: WorldState, what: str) -> None:
    """A sensor number must come from a sensor snapshot. Cheap enough to run on every row."""
    if state.source is not Source.sensor:
        raise AssertionError(f"{what}: refusing to grade a {state.source.value} snapshot as "
                             f"the sensing channel")


def _look(scene, catalog, reader, assembler, out_dir: str, name: str, view: str, *,
          task: Optional[TaskContext] = None, previous=None, frame_index: int = 0):
    """One rendered frame, one reading, one percept. Pixels only on this side of the call."""
    frame, _, _ = capture(scene, out_dir, camera_for(view), name, sim_time=scene.sim_time)
    reading = reader.read(frame, catalog=catalog, task=task)
    return assembler.assemble(frame, reading, previous=previous, frame_index=frame_index)


def _seen_id(colour: str) -> str:
    return f"seen:{colour}"


def _support_or_absent(state: WorldState, eid: str) -> Optional[str]:
    entity = state.entity(eid) if state.has_entity(eid) else None
    return None if entity is None else entity.supported_by


def _binding(goal, world: WorldState) -> set:
    """`(colour, target_id)` for every assignment of this goal, read back out of the snapshot
    the interpreter just bound against.

    An unresolvable phrase keeps a placeholder row instead of vanishing: "bound to two
    bodies" and "bound to none" are different failures of the state layer, and a report that
    collapsed them could not tell a duplicate from an absence."""
    out = set()
    for a in goal.assignments:
        hits = [e for e in world.entities
                if a.entity.entity_id == e.entity_id
                or (a.entity.attributes and all(
                    e.attributes.get(k) == v for k, v in a.entity.attributes.items()))]
        if len(hits) != 1:
            out.add((f"<{len(hits)} hits {a.entity.attributes or a.entity.entity_id}>",
                     a.target_id))
            continue
        out.add((str(hits[0].attributes.get("color")), a.target_id))
    return out


def _key(scene) -> dict[str, dict]:
    """The answer key, for grading only: colour → simulator id, declared shape, xy."""
    rows = {}
    for eid, d in scene.objects.items():
        pos, _orn = scene.object_pose(eid)
        rows[str(d["attributes"]["color"])] = {
            "entity_id": eid, "shape": str(d["attributes"]["shape"]),
            "xy": [pos[0], pos[1]]}
    return rows


def _seat(scene, entity_id: str, target_id: str, dx: float, dy: float) -> str:
    """Move a body with the simulator's own hand and report its colour.

    This is the key's hand, not the channel's: the measurement needs a placement to have
    *really* happened, and then grades what the pixels said about it."""
    import pybullet as p
    d = scene.objects[entity_id]
    tray = scene.trays[target_id]
    z = float(tray["floor_top"]) + float(d["geometry"].half_h_vertical)
    p.resetBasePositionAndOrientation(
        d["body"], [float(tray["center"][0]) + dx, float(tray["center"][1]) + dy, z],
        p.getQuaternionFromEuler([0, 0, 0.05], physicsClientId=scene.cid),
        physicsClientId=scene.cid)
    for _ in range(40):
        p.stepSimulation(physicsClientId=scene.cid)
    scene.sim_time += 0.4
    return str(d["attributes"]["color"])


def _wall_dx(scene, entity_id: str) -> float:
    """A centre offset leaving the upright footprint 4 mm across the inner wall.

    The declared placement margin is 5 mm and occupancy is tested with it *relaxed*, so this
    one configuration answers `occupies the tray` yes and `placed inside` no at the same time
    — the pair `verify_percept` refuses to confuse."""
    geom = scene.objects[entity_id]["geometry"]
    half = max((geom.half_extents.x, geom.half_extents.y) if geom.half_extents is not None
               else (geom.radius, geom.radius))
    return float(scene.trays[PLACEMENT_TARGET]["inner_half"]) - half + 0.004


def _completed() -> SkillResult:
    """Layer 1 said the actuation finished; only layer 2's evidence varies."""
    return SkillResult(call_id="p1f", status=SkillStatus.completed, t_start=0.0, t_end=1.0,
                       stages_executed=["p1f"])


# ----------------------------------------------------------- 1. grounding ----


def grounding_battery(case, catalog, states: dict[str, WorldState],
                      privileged: WorldState) -> dict[str, Any]:
    """V1, over every attribute phrase this case's own vocabulary supports.

    The utterance is the sentence the episode used; the colour and colour+shape phrases are
    probes, because a batch measured only on utterances would never touch the half of the
    attribute vocabulary a task happens not to name."""
    colours = sorted({str(d["attributes"]["color"]) for d in case.objects})
    declared = {str(d["attributes"]["color"]): str(d["attributes"]["shape"])
                for d in case.objects}
    phrases = ([case.utterance]
               + [catalog.word_for_attributes({"color": c}) for c in colours]
               + [catalog.word_for_attributes({"color": c, "shape": declared[c]})
                  for c in colours])
    task_in = TaskInput(task_id=case.task_id, utterance=case.utterance)
    counts: dict[str, tuple[int, int]] = {}
    rows = []
    for phrase in dict.fromkeys(phrases):
        utterance = phrase if phrase == case.utterance else f"把{phrase}放入中间盘"
        probe = {"utterance": utterance, "views": {}}
        for view, state in states.items():
            _guard_sensor(state, "grounding")
            goal = interpret(TaskInput(task_id=case.task_id, utterance=utterance), state)
            key_goal = interpret(TaskInput(task_id=case.task_id, utterance=utterance),
                                 privileged)
            p_pairs, k_pairs = sorted(_binding(goal, state)), sorted(_binding(key_goal, privileged))
            agree = ("both" if p_pairs and p_pairs == k_pairs else
                     "neither" if not p_pairs and not k_pairs else
                     "only_percept" if p_pairs and not k_pairs else "only_privileged")
            amb = ("both" if goal.ambiguous and key_goal.ambiguous else
                   "neither" if not goal.ambiguous and not key_goal.ambiguous else
                   "only_percept" if goal.ambiguous else "only_privileged")
            probe["views"][view] = {
                "sensor_pairs": p_pairs, "key_pairs": k_pairs,
                "agreement": agree, "ambiguity": amb,
                "sensor_clarification": goal.clarification_request}
            _step(counts, "V1.grounding_accuracy", int(agree == "both"), 1)
            _step(counts, "V1.both_declined", int(agree == "neither"), 1)
            _step(counts, "V1.grounding_over_claim", int(agree == "only_percept"), 1)
            _step(counts, "V1.grounding_ambiguity_agreement",
                  int(amb in ("both", "neither")), 1)
            if amb == "only_privileged":
                probe.setdefault("acted_on_a_key_ambiguous_phrase", []).append(view)
        rows.append(probe)
    return {
        "counts": counts, "n_probes": len(rows), "probes": rows,
        "reference_utterance_pairs": sorted(map(list, _binding(
            interpret(task_in, privileged), privileged))),
        "agreement_by_view": {
            view: {k: sum(1 for r in rows if r["views"][view]["agreement"] == k)
                   for k in ("both", "neither", "only_percept", "only_privileged")}
            for view in states},
        "ambiguity_by_view": {
            view: {k: sum(1 for r in rows if r["views"][view]["ambiguity"] == k)
                   for k in ("both", "neither", "only_percept", "only_privileged")}
            for view in states},
    }


# ------------------------------------------------- 2. claims, unknowns, shapes ----


def claim_battery(case, scene, states: dict[str, WorldState], key: dict,
                  percepts: dict[str, Any]) -> dict[str, Any]:
    """V3's channel properties and the §11 shape-vocabulary table.

    One row per (view, body) with the sensor's answer beside the key's, because the useful
    question is per-field rather than per-average: which facts does a camera not have, and
    which of the ones it claims are wrong."""
    privileged = build_world_state(scene, 1, "obs_key")
    by_colour = {str(e.attributes.get("color")): e for e in privileged.entities}
    counts: dict[str, tuple[int, int]] = {}
    rows, errors = [], []
    for view, state in states.items():
        _guard_sensor(state, "claims")
        for e in state.entities:
            colour = str(e.attributes.get("color"))
            pri = by_colour.get(colour)
            silent = {f for f in CHANNEL_SILENT_FIELDS if getattr(e, f) in ("unknown", None)}
            answered = {f for f in CHANNEL_SILENT_FIELDS if pri is not None
                        and getattr(pri, f) not in ("unknown", None)}
            _step(counts, "V3.channel_silent", len(silent & answered), len(answered))
            shape_seen = str(e.attributes.get("shape"))
            shape_true = (key.get(colour) or {}).get("shape")
            if shape_true is not None:
                _step(counts, "V1.shape_vocabulary", int(shape_seen == shape_true), 1)
            located = e.pose is not None
            _step(counts, "V3.unlocated", int(not located), 1)
            err = (None if not located or pri is None else round(math.hypot(
                entity_position(e)[0] - pri.pose.position.x,
                entity_position(e)[1] - pri.pose.position.y) * 1000, 2))
            if err is not None:
                errors.append(err)
            carried = None if e.geometry is None else entity_footprint_half_xy(e)
            rows.append({
                "view": view, "entity_id": e.entity_id, "colour": colour,
                "shape_seen": shape_seen, "shape_truth": shape_true,
                "position_error_mm": err,
                "channel_silent_fields": sorted(silent & answered),
                "orientation_measured": e.orientation_measured,
                "supported_by_sensor": e.supported_by,
                "supported_by_key": None if pri is None else pri.supported_by,
                "footprint_widening_mm": (None if carried is None or e.geometry is None else
                                          round((carried[0] - footprint_half_xy_from_quat(
                                              e.geometry, ASSUMED_UPRIGHT)[0]) * 1000, 1)),
                "bound_is_orientation_invariant": (
                    None if carried is None or e.geometry is None
                    else carried == orientation_invariant_footprint_half_xy(e.geometry)),
            })
        present = {str(e.attributes.get("color")) for e in state.entities}
        unseen = sorted(set(key) - present)
        _step(counts, "V3.unseen_body", len(unseen), len(key))
        named = [c for c in unseen if any(v.entity_id == _seen_id(c) and v.occluded_by
                                          for v in percepts[view].visibility)]
        _step(counts, "V3.unseen_but_named_blocker", len(named), len(unseen))
    reasons: dict[str, dict[str, int]] = {}
    for view, percept in percepts.items():
        tally: dict[str, int] = {}
        for u in percept.uncertainties:
            tally[u.why] = tally.get(u.why, 0) + 1
        reasons[view] = dict(sorted(tally.items()))
    return {
        "counts": counts, "rows": rows,
        "position_error_mm": {"n": len(errors),
                              "median": round(float(np.median(errors)), 2) if errors else None,
                              "max": max(errors) if errors else None},
        "uncertainty_reasons_by_view": reasons,
        "fields_the_key_answers_and_the_frame_does_not": sorted({
            f for r in rows for f in r["channel_silent_fields"]}),
        "widening_mm_max": max((r["footprint_widening_mm"] for r in rows
                                if r["footprint_widening_mm"] is not None), default=None),
        "bound_not_invariant": sorted({r["entity_id"] for r in rows
                                       if r["bound_is_orientation_invariant"] is False}),
        "relation_disagreements": [[r["view"], r["entity_id"], r["supported_by_sensor"],
                                    r["supported_by_key"]] for r in rows
                                   if r["supported_by_key"] is not None
                                   and r["supported_by_sensor"] != r["supported_by_key"]],
    }


# ------------------------------------------------------------ 3. change ledger ----


def change_battery(case, scene, catalog, reader, assembler, task, key, out_dir: str) -> dict:
    """V2. One commanded displacement, counted in the ledger *and* in the audit — separately.

    The base percept is the baseline for every trial, so each row measures one move against
    one memory of the world, which is what `reconcile` expects. The trials under the
    threshold are the false-positive half of the same instrument and the repeated baseline is
    the control: a table that did not move must produce nothing at all.

    The second half runs the same events through a `PerceptWorldStateTracker` — three views
    twice each, then a placement made while the camera pointed elsewhere — because that is
    where the ledger/audit distinction stops being a reading of a docstring: the placement is
    real, the ledger stays empty, and the audit recovers it."""
    import pybullet as p
    colour, t = max(key.items(), key=lambda kv: abs(kv[1]["xy"][1]))
    entity_id, base_xy = t["entity_id"], list(t["xy"])
    rest_z = float(scene.objects[entity_id]["rest_z_table"])
    subject = _seen_id(colour)

    def move_and_look(xy, name, previous, index):
        d = scene.objects[entity_id]
        p.resetBasePositionAndOrientation(d["body"], [xy[0], xy[1], rest_z],
                                          p.getQuaternionFromEuler([0, 0, 0.05],
                                                                   physicsClientId=scene.cid),
                                          physicsClientId=scene.cid)
        for _ in range(40):
            p.stepSimulation(physicsClientId=scene.cid)
        scene.sim_time += 0.4
        pos, _orn = scene.object_pose(entity_id)
        return pos, _look(scene, catalog, reader, assembler, out_dir, name, "main",
                          task=task, previous=previous, frame_index=index)

    counts: dict[str, tuple[int, int]] = {}
    base_pos, base_obs = move_and_look(base_xy, "chg_base", None, 0)
    base_state = world_state_from_percept(base_obs, catalog, state_version=1)
    threshold_mm = assembler.move_threshold_m * 1000.0
    rows = []
    for i, delta in enumerate(DISPLACEMENTS_MM, start=2):
        pos, obs = move_and_look([base_xy[0], base_xy[1] - delta / 1000.0], f"chg_{delta}",
                                 base_obs, i)
        state = world_state_from_percept(obs, catalog, state_version=i)
        _guard_sensor(state, "change")
        audit = reconcile(base_state, state, obs)
        pos_rows = [c for c in obs.changes if c.subject_id == subject and "position" in c.attribute]
        over = delta >= threshold_mm
        _step(counts, "V2.change_detection" if over else "V2.change_false_positive",
              int(bool(pos_rows)), 1)
        _step(counts, "V2.audit_differences", len(audit["unreported_differences"]), 1)
        rows.append({
            "commanded_mm": delta, "over_threshold": over,
            "true_mm": round(math.hypot(pos[0] - base_pos[0], pos[1] - base_pos[1]) * 1000, 2),
            "ledger_position_rows": [[c.attribute, c.before, c.after] for c in pos_rows],
            "reported_delta_mm": _delta_of(pos_rows),
            "unreported_differences": audit["unreported_differences"],
            "rows_without_a_state_difference": audit["rows_without_a_state_difference"]})
    control = reconcile(base_state, base_state, base_obs)
    _step(counts, "V2.change_false_positive",
          len([c for c in base_obs.changes if c.subject_id == subject]), 1)
    _step(counts, "V2.audit_differences", len(control["unreported_differences"]), 1)

    # ---- the same events through the tracker, which is the owner of the ledger ----
    tracker = PerceptWorldStateTracker(catalog, episode_id="p1f_cycle")
    legs, still_audits = [], 0
    for view in VIEWS:
        for repeat in range(2):
            percept = _look(scene, catalog, reader, assembler, out_dir,
                            f"cycle_{view}_{repeat}", view, task=task,
                            previous=tracker.percept, frame_index=repeat)
            tracker.observe(percept)
            if not tracker.audits:
                continue
            audit = tracker.audits[-1]
            cross = "fields_declined" in audit
            legs.append({
                "kind": "cross_view" if cross else "same_view",
                "leg": (f"{audit.get('previous_camera_id') or view}->{view}" if cross else view),
                "ledger_rows": len(audit["reported_changes"]),
                "unreported_differences": audit["unreported_differences"],
                "cross_view_disagreements": audit["cross_view_disagreements"]})
            still_audits = len(tracker.audits)
    cubes = [c for c, v in key.items() if v["shape"] == "cube"]
    seat_colour = cubes[0] if cubes else sorted(key)[0]
    _cycle_look(tracker, scene, catalog, reader, assembler, out_dir, task, "main",
                "blind_from_main")
    before = tracker.state
    _seat(scene, key[seat_colour]["entity_id"], PLACEMENT_TARGET, 0.0, 0.0)
    _cycle_look(tracker, scene, catalog, reader, assembler, out_dir, task, "overhead",
                "blind_after")
    after, blind = tracker.state, tracker.audits[-1]
    for a in tracker.audits[:still_audits]:
        if "fields_declined" in a:
            _step(counts, "V2.cross_view_recovery", int(bool(a["cross_view_disagreements"])), 1)
    _step(counts, "V2.cross_view_recovery", int(bool(blind["cross_view_disagreements"])), 1)
    still = tracker.audits[:still_audits]
    return {
        "counts": counts, "colour": colour, "entity_id": entity_id,
        "move_threshold_mm": round(threshold_mm, 1),
        "still_world_control": {k: control[k] for k in
                                ("state_differences", "unreported_differences",
                                 "rows_without_a_state_difference")},
        "rows": rows,
        "tracker": {
            "n_audits": len(tracker.audits), "n_still_audits": still_audits,
            "legs": legs,
            "ledger_rows_total": len(tracker.ledger),
            "ledger_rows_on_a_still_table": sum(len(a["reported_changes"]) for a in still),
            "audit_differences_on_a_still_table": sum(len(a["unreported_differences"])
                                                      for a in still),
            "cross_view_rows_on_a_still_table": sum(len(a["cross_view_disagreements"])
                                                    for a in still),
            "blind_spot": {
                "colour": seat_colour, "where_the_key_says": PLACEMENT_TARGET,
                "where_main_left_it": _support_or_absent(before, _seen_id(seat_colour)),
                "where_overhead_says": _support_or_absent(after, _seen_id(seat_colour)),
                "ledger_rows_for_that_look": blind["reported_changes"],
                "cross_view_disagreements": blind["cross_view_disagreements"]},
            # the 口径 as data: what each account holds, and the gap between them, which is
            # published rather than folded into a metric
            "ledger_vs_audits": {
                "ledger_rows": len(tracker.ledger),
                "audit_rows": len(tracker.audits),
                "audit_difference_entries": sum(
                    len(a["unreported_differences"]) + len(a["cross_view_disagreements"])
                    for a in tracker.audits),
                "merged": False,
                "why_not_merged": METRIC_DEFINITIONS["V2.change_detection"]["forbidden"]},
        },
    }


def _cycle_look(tracker, scene, catalog, reader, assembler, out_dir, task, view, name):
    percept = _look(scene, catalog, reader, assembler, out_dir, name, view, task=task,
                    previous=tracker.percept, frame_index=len(tracker.ledger))
    return tracker.observe(percept)


def _delta_of(rows) -> Optional[float]:
    """The distance the perceiver's own row claims, in mm, if a row carries one."""
    if not rows:
        return None
    try:
        before = np.array(ast.literal_eval(rows[0].before), dtype=float)
        after = np.array(ast.literal_eval(rows[0].after), dtype=float)
    except (ValueError, SyntaxError, TypeError):
        return None
    if before.shape != after.shape or before.size < 2:
        return None
    return round(float(math.hypot(*(after[:2] - before[:2]))) * 1000.0, 2)


# --------------------------------------------------------- 4. verification ----


def _key_placed(privileged: WorldState, entity_id: str, target_id: str, margin: float):
    """The key's verdict on *the conjunction the sensor claims*, plus its support word.

    Deliberately narrower than `RuntimeVerifier.verify_placement`, which also promises
    `at_rest` and `not_held`: grading against that wider conjunction would record a body
    still settling as a sensor over-claim, i.e. charge layer 2 for layer 1's patience."""
    entity, region = privileged.entity(entity_id), privileged.target(target_id)
    if entity is None or region is None:
        return False, "absent"
    support = target_id in str(entity.supported_by).split("+")
    return (support and state_inside(entity, region, margin)[0]), str(entity.supported_by)


def verification_battery(case, scene, catalog, reader, assembler, task, out_dir: str, *,
                         baseline: dict) -> dict:
    """V3a/b/c over four physical configurations and every declared view.

    The configurations answer different questions and are not interchangeable: `alone` is
    what a placement skill actually produces; `crowded` packs every body into one tray and
    pushes the last against a wall, the case where a position error becomes a verdict; `wall`
    parks a body straddling the inner wall, so the key says *not placed* while the occupancy
    row still says it blocks; and the untouched table is the negative control, where the tray
    postcondition is false for every body the declared layout left on the table — the key
    says so per row, it is not assumed here.

    `baseline` is the case's *declared* poses, captured before any battery touched the scene.
    A local snapshot of `scene.object_pose` would be taken after `change_battery` had already
    seated a cube in the target, and the untouched control would then be graded against a
    table with an occupant in it — which is how a first draft of this function reported five
    over-claims that the physical scene never contained."""
    ids = sorted(scene.objects)
    if set(baseline) != set(ids):
        raise ValueError("the verification battery's baseline is not this scene's body set: "
                         "the untouched control would be graded on some other table")
    counts: dict[str, tuple[int, int]] = {}
    rows: list[dict] = []
    start = dict(baseline)

    def restore():
        import pybullet as p
        for eid, (pos, orn) in start.items():
            p.resetBasePositionAndOrientation(scene.objects[eid]["body"], list(pos),
                                              list(orn), physicsClientId=scene.cid)
        for _ in range(40):
            p.stepSimulation(physicsClientId=scene.cid)
        scene.sim_time += 0.4

    def ask(view: str, colour: str, entity_id: str, config: str, subject: Optional[str],
            state: WorldState, privileged: WorldState) -> dict:
        """One postcondition, asked of one frame, graded against one frozen key.

        `state` and `privileged` are handed in rather than built here because the unit of
        measurement is a question, not a rendering: the frame that answers five bodies'
        questions is one frame, and the key must describe the table as those pixels saw it."""
        _guard_sensor(state, "verification")
        role = "primary" if subject is None or entity_id == subject else "control"
        eid_seen = _seen_id(colour)
        present = state.has_entity(eid_seen)
        verifier = PerceptVerifier(state, view=view,
                                   alt_views=tuple(v for v in VIEWS if v != view))
        report = verifier.verify_placement(eid_seen, PLACEMENT_TARGET)
        truth, support = _key_placed(privileged, entity_id, PLACEMENT_TARGET,
                                     verifier.config.footprint_margin_m)
        value = report.value.value
        cls = ("no_answer" if not present else
               "confirmed" if (value == "true" and truth) else
               "dangerous_true" if value == "true" else
               "contrary_false" if (value == "false" and truth) else
               "correct_negative" if value == "false" else
               "honest_decline" if truth else "declined_negative")
        if role == "primary":
            _step(counts, "V3.over_claim_rate", int(cls == "dangerous_true"), 1)
            _step(counts, "V3.over_claim_among_denials",
                  int(cls == "dangerous_true"), int(not truth))
            _step(counts, "V3.withheld_rate",
                  int(cls in ("honest_decline", "declined_negative")), 1)
            _step(counts, "V3.conservative_miss_rate", int(cls == "contrary_false"), 1)
            _step(counts, "V3.certified_rate", int(cls == "confirmed"), int(truth))
        else:
            _step(counts, "V3.control_over_claim", int(cls == "dangerous_true"), int(not truth))
        resolves = []
        if role == "primary" and cls in ("honest_decline", "declined_negative"):
            resolves = verifier.would_resolve(next(
                (u for u in report.unmeasured if u not in DECLINED_FACTS),
                "not_in_this_frame"))
            _step(counts, "V3.resolvable_unknown", int(bool(resolves)), 1)
        entity = state.entity(eid_seen) if present else None
        pri_entity = privileged.entity(entity_id)
        return {
            "config": config, "view": view, "colour": colour, "present": present,
            "role": role, "subject": subject,
            "sensor_value": value, "key_value": ("true" if truth else "false"),
            "key_support": support, "class": cls,
            "wall_margin_m": report.evidence.get("wall_margin_m"),
            "occupies_target": report.evidence.get("occupies_target"),
            "centre_error_mm": (None if entity is None or entity.pose is None
                                or pri_entity is None
                                else round(math.hypot(
                                    entity.pose.position.x - pri_entity.pose.position.x,
                                    entity.pose.position.y
                                    - pri_entity.pose.position.y) * 1000, 1)),
            "unmeasured": list(report.unmeasured),
            # the anti-circularity audit, on *evidence* keys only: `verify_percept` names
            # `rest_z_vs_assumed_orientation` in `unmeasured` precisely because it refused to
            # compute it, so the name is the design and a produced number would be the defect
            "seating_height_evidence_keys": [k for k in SEATING_HEIGHT_EVIDENCE_KEYS
                                             if k in report.evidence],
            "would_resolve": list(resolves),
            "outcome_status": (verifier.outcome_status(_completed(), [report])
                               or SkillStatus.completed).value,
        }

    def ask_all(config: str, colour_of: dict[str, str], states: dict[str, WorldState],
                privileged: WorldState, subject: Optional[str]) -> None:
        """Every body's question, from one frame per view.

        The frame is taken once, after the configuration is complete, and reused: capturing
        it per body would either re-render an unchanged table five times or — the failure the
        first draft of this function had — read a frame that predates the seating of a later
        body and score the sensor against a key that does not.

        `subject` is the body this round *acted on*, or None for a whole-table state. It is
        what separates a primary question from a control one at zero extra cost: the same
        frame answers both, and only the primary set is the 口径 P1-d published."""
        for view in VIEWS:
            for eid in ids:
                rows.append(ask(view, colour_of[eid], eid, config, subject, states[view],
                                privileged))

    def sweep(config: str) -> None:
        """One physical configuration, one table of questions.

        `untouched` and `crowded` are whole-table states, so one look per view answers every
        body and every question is primary. `alone` and `wall` are per-body states — seating
        the second body changes the tray the first was asked about — so each gets its own
        restore, seat and look, the body it seated is the primary question, and the other four
        bodies in that same frame become the control set. The key is built after the frame and
        before the questions, so both describe one table.
        """
        colour_of = {eid: str(scene.objects[eid]["attributes"]["color"]) for eid in ids}
        if config in ("untouched", "crowded"):
            restore()
            if config == "crowded":
                for j, other in enumerate(ids):
                    _seat(scene, other, PLACEMENT_TARGET, -0.075 + 0.05 * j, 0.0)
            _ask_configuration(config, colour_of, None)
            return
        for i, eid in enumerate(ids):
            restore()
            _seat(scene, eid, PLACEMENT_TARGET,
                  0.0 if config == "alone" else _wall_dx(scene, eid), 0.0)
            _ask_configuration(f"{config}:{eid}", colour_of, eid)

    def _ask_configuration(name: str, colour_of: dict[str, str],
                           subject: Optional[str]) -> None:
        states = {}
        for view in VIEWS:
            obs = _look(scene, catalog, reader, assembler, out_dir, f"{name}_{view}", view,
                        task=task)
            states[view] = world_state_from_percept(obs, catalog, state_version=len(rows) + 1)
        privileged = build_world_state(scene, len(rows) + 1, f"obs_{name}")
        ask_all(name, colour_of, states, privileged, subject)

    for config in ["untouched", *CONFIGURATIONS]:
        sweep(config)
    restore()
    primary = [r for r in rows if r["role"] == "primary"]
    control = [r for r in rows if r["role"] == "control"]
    per_view = {}
    for view in VIEWS:
        mine = [r for r in primary if r["view"] == view]
        truths = [r["wall_margin_m"] for r in mine if r["sensor_value"] == "true"]
        per_view[view] = {
            "questions": len(mine),
            "classes": {c: sum(1 for r in mine if r["class"] == c) for c in VERDICT_CLASSES},
            "over_claim": _rate("V3.over_claim_rate",
                                sum(1 for r in mine if r["class"] == "dangerous_true"),
                                len(mine)),
            "thinnest_true_margin_mm": (round(min(truths) * 1000, 1) if truths else None),
            "max_centre_error_mm": max((r["centre_error_mm"] for r in mine
                                        if r["centre_error_mm"] is not None), default=None),
            "uncertain_verdicts": sum(1 for r in mine if r["outcome_status"] == "uncertain"),
            "control_questions": sum(1 for r in control if r["view"] == view),
            "control_over_claims": sum(1 for r in control if r["view"] == view
                                       and r["class"] == "dangerous_true")}
    return {"counts": counts, "rows": rows, "per_view": per_view,
            "target": PLACEMENT_TARGET, "bodies": len(ids), "questions": len(primary),
            "control_questions": len(control),
            "configurations": ["untouched", *CONFIGURATIONS],
            "per_view_classes_by_config": {
                config: {c: sum(1 for r in rows if r["config"] == config and r["class"] == c)
                         for c in VERDICT_CLASSES}
                for config in ["untouched", *CONFIGURATIONS]},
            # the anti-circularity verdict: a sensor report that produced a seating-height
            # *number* would mean the declined comparison came back
            "seating_height_compared_anywhere": sorted({
                k for r in rows for k in r["seating_height_evidence_keys"]})}


# ------------------------------------------------------------------ per case ----


def measure_case(case_id: str, *, out_dir: str = DEFAULT_OUT,
                 batteries: Sequence[str] = BATTERIES) -> dict:
    """Every battery, on one frozen case, in one scene."""
    case = find_case(case_id)
    os.makedirs(out_dir, exist_ok=True)
    scene = build_scene(case)
    started = time.perf_counter()
    try:
        #: the case's declared table, before any battery moves a body. `verification_battery`
        # restores to *this*, not to a pose it reads for itself: read for itself it would read
        # the table `change_battery` left, which is a different experiment.
        baseline = {eid: scene.object_pose(eid) for eid in scene.objects}
        catalog = cell_catalog(scene.trays.values())
        assembler = PerceptionAssembler(catalog)
        reader, task = StubReader(), TaskContext(**task_context_of(case))
        key = _key(scene)
        states, percepts = {}, {}
        for i, view in enumerate(VIEWS):
            obs = _look(scene, catalog, reader, assembler, out_dir, f"static_{view}", view,
                        task=task, frame_index=i)
            states[view] = world_state_from_percept(obs, catalog, state_version=i + 1)
            percepts[view] = obs
        privileged = build_world_state(scene, 1, "obs_privileged")
        out: dict[str, Any] = {
            "case_id": case_id, "subset": case.subset, "seed": case.seed,
            "utterance": case.utterance, "bodies": len(key),
            "definition_version": DEFINITION_VERSION,
            "catalog": {"version": catalog.version, "sha256": catalog.sha256()},
            "assembler_thresholds": assembler.thresholds(),
            "views": list(VIEWS),
            "simulator_id_in_a_sensor_snapshot": any("obj_" in s.model_dump_json()
                                                     for s in states.values())}
        counts: dict[str, tuple[int, int]] = {}
        if "grounding" in batteries:
            out["grounding"] = grounding_battery(case, catalog, states, privileged)
            _merge(counts, out["grounding"]["counts"])
        if "claims" in batteries:
            out["claims"] = claim_battery(case, scene, states, key, percepts)
            _merge(counts, out["claims"]["counts"])
        if "change" in batteries:
            out["change"] = change_battery(case, scene, catalog, reader, assembler, task,
                                           key, out_dir)
            _merge(counts, out["change"]["counts"])
        if "verification" in batteries:
            out["verification"] = verification_battery(case, scene, catalog, reader,
                                                       assembler, task, out_dir,
                                                       baseline=baseline)
            _merge(counts, out["verification"]["counts"])
        out["metrics"] = _render(counts)
        out["wall_s"] = round(time.perf_counter() - started, 2)
        return out
    finally:
        scene.close()


def _shape_table(rows: Sequence[dict]) -> dict[str, dict[str, Any]]:
    """Declared shape -> how often this channel named it, and what it named it instead.

    The same accumulator serves the pooled table and the per-view one, so the two cannot drift
    apart: `V1.shape_vocabulary` divides by `declared bodies × views`, and this is that sum with
    the views kept apart. P1-c's `cylinder 0/55` is a number about *one* camera; only the split
    can say so."""
    table: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r["shape_truth"]:
            continue
        v = table.setdefault(r["shape_truth"], {"declared": 0, "named_correctly": 0,
                                                "named_as": {}})
        v["declared"] += 1
        v["named_correctly"] += int(r["shape_seen"] == r["shape_truth"])
        if r["shape_seen"] != r["shape_truth"]:
            v["named_as"][r["shape_seen"]] = v["named_as"].get(r["shape_seen"], 0) + 1
    return table


def measure_set(set_name: str = "all", *, case_ids: Optional[Sequence[str]] = None,
                out_dir: str = DEFAULT_OUT, batteries: Sequence[str] = BATTERIES,
                verbose: bool = True) -> dict:
    """The same batteries over a whole frozen set, pooled by summing integers."""
    cases = build_set(set_name)
    if case_ids:
        cases = [c for c in cases if c.task_id in set(case_ids)]
    if not cases:
        raise ContrastConfig(f"no cases of set {set_name!r} matched {list(case_ids)}")
    per_case: list[dict] = []
    counts: dict[str, tuple[int, int]] = {}
    for case in cases:
        row = measure_case(case.task_id, out_dir=out_dir, batteries=batteries)
        per_case.append(row)
        for k, rate in row["metrics"].items():
            n, d = counts.get(k, (0, 0))
            counts[k] = (n + rate["numerator"], d + rate["denominator"])
        if verbose:
            print(f"  {case.task_id:10s} bodies {row['bodies']}  "
                  + "  ".join(f"{k.rsplit('.', 1)[-1]}={r['numerator']}/{r['denominator']}"
                              for k, r in sorted(row["metrics"].items())), flush=True)
    metrics = {k: _rate(k, n, d, pooled_over_cases=len(per_case))
               for k, (n, d) in sorted(counts.items())}
    shape_rows = [r for c in per_case for r in (c.get("claims") or {}).get("rows", [])]
    #: Per shape, pooled over views — and per view. The second table is the one P1-c residual 6
    #: asked for: its `cylinder 0/55` turned out to be a fact about *one camera*, not about the
    #: channel, and only the split says which. Same rows, same filter, no new arithmetic.
    vocabulary = _shape_table(shape_rows)
    vocabulary_by_view = {v: _shape_table([r for r in shape_rows if r["view"] == v])
                          for v in VIEWS}
    changes = [c for c in per_case if "change" in c]
    verifications = [c for c in per_case if "verification" in c]
    return {
        "set": set_name, "definition_version": DEFINITION_VERSION,
        "definitions": {k: METRIC_DEFINITIONS[k]
                        for k in sorted({r["definition_id"] for r in metrics.values()})},
        "coverage": {
            "cases_planned": len(cases), "cases_measured": len(per_case),
            "case_ids": [c.task_id for c in cases], "batteries": list(batteries),
            "views": list(VIEWS), "model_money_spent": 0,
            "reader": "StubReader — deterministic, no vision model consulted (see C1)"},
        "metrics": metrics,
        "shape_vocabulary": vocabulary,
        "shape_vocabulary_by_view": vocabulary_by_view,
        "position_error_mm": _pooled_errors(per_case),
        "per_view_verification": {
            view: {
                "questions": sum(c["verification"]["per_view"][view]["questions"]
                                 for c in verifications),
                "classes": {k: sum(c["verification"]["per_view"][view]["classes"].get(k, 0)
                                   for c in verifications) for k in VERDICT_CLASSES},
                "over_claims": sum(1 for c in verifications
                                   for r in c["verification"]["rows"]
                                   if r["view"] == view and r["class"] == "dangerous_true"
                                   and r["role"] == "primary"),
                "control_questions": sum(
                    c["verification"]["per_view"][view]["control_questions"]
                    for c in verifications),
                "control_over_claims": sum(
                    c["verification"]["per_view"][view]["control_over_claims"]
                    for c in verifications),
                "thinnest_true_margin_mm": min(
                    (c["verification"]["per_view"][view]["thinnest_true_margin_mm"]
                     for c in verifications
                     if c["verification"]["per_view"][view]["thinnest_true_margin_mm"]
                    is not None), default=None),
                "max_centre_error_mm": max(
                    (c["verification"]["per_view"][view]["max_centre_error_mm"] or 0.0
                     for c in verifications), default=None),
                "uncertain_verdicts": sum(c["verification"]["per_view"][view]
                                          ["uncertain_verdicts"] for c in verifications),
            } for view in VIEWS},
        "ledger_vs_audits": {
            "ledger_rows": sum(c["change"]["tracker"]["ledger_vs_audits"]["ledger_rows"]
                               for c in changes),
            "audit_rows": sum(c["change"]["tracker"]["ledger_vs_audits"]["audit_rows"]
                              for c in changes),
            "audit_difference_entries": sum(
                c["change"]["tracker"]["ledger_vs_audits"]["audit_difference_entries"]
                for c in changes),
            "blind_spots_recovered_by_an_audit": sum(
                1 for c in changes
                if c["change"]["tracker"]["blind_spot"]["cross_view_disagreements"]),
            "blind_spots_present_in_the_ledger": sum(
                1 for c in changes if c["change"]["tracker"]["blind_spot"]
                ["ledger_rows_for_that_look"]),
            "merged": False,
            "rule": METRIC_DEFINITIONS["V2.change_detection"]["forbidden"]},
        "cases": per_case,
    }


def _pooled_errors(per_case: Sequence[dict]) -> dict:
    """Per-body position error in mm, pooled over the set. A distribution, not a rate: it is
    reported with n/median/max so no single number stands in for the shape of it."""
    errs = sorted(r["position_error_mm"] for c in per_case
                  for r in (c.get("claims") or {}).get("rows", [])
                  if r["position_error_mm"] is not None)
    return {"n": len(errs),
            "median": round(float(np.median(errs)), 2) if errs else None,
            "max": errs[-1] if errs else None,
            "all": errs}


# ------------------------------------------------- 5. episode-level contrast ----


def _summary_of(row: dict) -> dict:
    """The full episode record a production run wrote, or `{}` for a row whose directory is
    gone. Reading a flat `episodes.csv` column for `perception` would silently return 0 — the
    csv does not carry it — which is why this goes to the summary file."""
    return row.get("_summary") or {}


def _number(value) -> Optional[float]:
    """A field is a quantity on a pair only if *both* arms wrote a number into it.

    `episodes.csv` hands every column back as a string, so `"8"` is 8.0 and `""` is nothing —
    and the first draft's `float(v or 0)` turned that nothing into a measured zero. The dev
    contrast showed what that fabricates: two sensing episodes ended `failed` with an empty
    `failure_type`, which paired against the privileged arm's empty one and printed
    `mean Δ 0.0, n=2` over a column of kinds. The two rows were the finding; the zero hid it.

    A `bool` is refused for the opposite reason: `load_rows` parses the two verdict columns into
    Python bools, and `True` is not a 1 — averaging it would print a proportion in a table of
    count differences. They are reported as kinds (`CONTRAST_CATEGORIES`)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _category(value) -> str:
    """The same empty cell, read as what it is: a kind, and an absent kind is a nameable one.

    Only the two boolean spellings are canonicalised (`load_rows` hands a verdict column back as a
    Python bool, whose `str()` is `True`, while a JSON dump of the same verdict carries `true` —
    one cross-tab must not hold two buckets for one value). A failure type is a contract enum name
    and is kept verbatim, which is why there is no blanket `.lower()` here."""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() == "none":
        return ABSENT_CATEGORY
    return BOOLEAN_KIND.get(text.lower(), text)


def _progress_row(summary: dict) -> Optional[dict]:
    """One episode, counted twice by two instruments: what the loop said and what the key said.

    `_csv_row` takes `objects_completed` from `score` (layer 3, read off the simulator on both
    arms), while the loop's own counter is `result.objects_completed`, which `_finish_check`
    fills from whatever verifier the arm runs on. On the privileged arm the two are the same
    number by construction; on a sensing arm they are not, and the gap is §11's self-report
    finding — P1-e 表 11's `1/5` against `0/5` is exactly this pair on one episode."""
    if not summary:
        return None
    res, sc = summary.get("result") or {}, summary.get("score") or {}
    loop, key = res.get("objects_completed"), sc.get("objects_completed")
    if loop is None or key is None:
        return None
    return {"loop_self_report": loop, "independent_key": key,
            "objects_total": sc.get("objects_total", res.get("objects_total")),
            "over_claim": loop > key, "under_report": loop < key}


def pair_rows(by_arm: dict[str, Sequence[dict]]) -> dict:
    """Pair two arms' episode rows and keep every exclusion on the record.

    Separate from `episode_contrast` because the pairing is the part that can lie: a
    difference computed over rows that are not the same episode is the contamination §12.1
    forbids, and it is checkable on hand-built rows without running an episode. Categorical
    fields (`CONTRAST_CATEGORIES`) are cross-tabulated as *kinds* instead, because the question
    the contrast asks of them is "what did it fail on, and did it claim so", which no mean
    answers — and `true`/`false` is a kind, not a quantity that happens to be written in 1 bit."""
    index = {arm: {(r["case_id"], r["mode"], r["repeat"]): r for r in rows}
             for arm, rows in by_arm.items()}
    shared = sorted(set(index["privileged"]) & set(index["sensing"]))
    pairs, diffs, categories, not_quantity = [], {}, {}, {}
    progress: dict[str, dict] = {arm: {"episodes": 0, "over_claim_pairs": [],
                                        "under_report_pairs": []}
                                 for arm in ("privileged", "sensing")}
    for k in shared:
        a, b = index["privileged"][k], index["sensing"][k]
        sa, sb = _summary_of(a), _summary_of(b)
        same_goal = (sa.get("goal_resolution") or {}).get("assignments") == \
            (sb.get("goal_resolution") or {}).get("assignments")
        pair = {"key": "|".join(str(x) for x in k), "case_id": k[0], "mode": k[1],
                "goal_identical": bool(same_goal), "comparable": bool(same_goal),
                "privileged": {f: a.get(f) for f in CONTRAST_FIELDS},
                "sensing": {f: b.get(f) for f in CONTRAST_FIELDS},
                "progress": {"privileged": _progress_row(sa), "sensing": _progress_row(sb)},
                "sensing_looks": (sb.get("perception") or {}).get("looks"),
                "sensing_look_http_requests": (sb.get("perception") or {}).get(
                    "look_http_requests"),
                "sensing_look_tokens": (sb.get("perception") or {}).get("look_tokens")}
        pairs.append(pair)
        for arm in ("privileged", "sensing"):
            row = pair["progress"][arm]
            if row is None:
                continue
            bucket = progress[arm]
            bucket["episodes"] += 1
            if row["over_claim"]:
                bucket["over_claim_pairs"].append(pair["key"])
            if row["under_report"]:
                bucket["under_report_pairs"].append(pair["key"])
        if not same_goal:
            # an unpaired-comparison row is still reported above; it just cannot enter a mean
            continue
        for f in CONTRAST_FIELDS:
            if f in CONTRAST_CATEGORIES:
                continue          # cross-tabulated below; a mean of kinds is not a number
            av, bv = _number(a.get(f)), _number(b.get(f))
            if av is None or bv is None:
                not_quantity.setdefault(f, []).append(pair["key"])
                continue
            diffs.setdefault(f, []).append(bv - av)
        for f in CONTRAST_CATEGORIES:
            av = _category(a.get(f))
            bv = _category(b.get(f))
            cat = categories.setdefault(f, {"privileged": {}, "sensing": {}, "transitions": {}})
            for arm, value in (("privileged", av), ("sensing", bv)):
                row = cat[arm]
                row[value] = row.get(value, 0) + 1
            move = f"{av} -> {bv}"
            cat["transitions"][move] = cat["transitions"].get(move, 0) + 1
    return {"paired_cases": len(shared),
            "unpaired": {"privileged_only": sorted(
                "|".join(str(x) for x in k) for k in set(index["privileged"]) - set(shared)),
                         "sensing_only": sorted(
                "|".join(str(x) for x in k) for k in set(index["sensing"]) - set(shared))},
            "not_comparable": [p["key"] for p in pairs if not p["comparable"]],
            "pairs": pairs,
            "categories": categories,
            "progress_self_report_vs_key": progress,
            "not_a_quantity_on_every_pair": not_quantity,
            "paired_diff_sensing_minus_privileged": {
                f: {"mean": round(float(np.mean(v)), 3), "median": round(float(np.median(v)), 3),
                    "min": round(min(v), 3), "max": round(max(v), 3), "n": len(v),
                    "excluded_pairs": not_quantity.get(f, [])}
                for f, v in sorted(diffs.items())}}


def episode_contrast(set_name: str = "dev", *, case_ids: Optional[Sequence[str]] = None,
                     out_root: str = DEFAULT_OUT, modes: Sequence[str] = DEFAULT_CONTRAST_MODES,
                     perceive: str = "stub", frames: bool = True) -> dict:
    """The §13 P1 对照, through the production entry point.

    Two batches, one per arm, over the same cases with the same `rule` decision source and the
    frozen per-case budgets and nothing else varied: `run_group` is the call the CLI makes, so
    a contrast cannot become a private, simpler runtime. Rows are then paired by
    `(case_id, mode, repeat)`; a case present in one arm and not the other is excluded from
    every difference and named, because an asymmetric pair is the contamination this function
    exists to avoid."""
    ids = [c.task_id for c in build_set(set_name)]
    if case_ids:
        ids = [c for c in ids if c in set(case_ids)]
    if not ids:
        raise ContrastConfig(f"no case of set {set_name!r} matched {list(case_ids or [])}: "
                             f"the set holds {len(build_set(set_name))} task ids")
    roots, channel_of = {}, {}
    for arm, channel in (("privileged", "privileged"), ("sensing", perceive)):
        channel_of[arm] = channel
        roots[arm] = run_group(set_name, modes=modes, planner_kind="rule", repeats=1,
                               out_root=out_root, case_ids=ids,
                               frames=frames, perceive=channel)["root"]
    by_arm = {arm: load_rows(root) for arm, root in roots.items()}
    return {
        "definition_id": "X1.contrast",
        "definition": METRIC_DEFINITIONS["X1.contrast"],
        "definition_version": DEFINITION_VERSION,
        "set": set_name, "modes": list(modes),
        "cases": ids, "planner": "rule", "repeats": 1,
        "arms": {arm: {"channel": channel_of[arm], "run_root": roots[arm],
                       "episodes": len(by_arm[arm])} for arm in roots},
        **pair_rows(by_arm),
        "arm_asymmetric_fields": ARM_ASYMMETRIC_FIELDS,
        "budget_censored_fields": BUDGET_CENSORED_FIELDS,
        "cost": cost_block(by_arm),
    }


def cost_block(rows_by_arm: dict[str, Sequence[dict]]) -> dict:
    """§11's Cost group per arm, with the denominators the 口径 names.

    `skill_generation_cost` is `not_measured` rather than 0: no producer exists before P4 and
    a zero would read as a measured saving."""
    out = {}
    for arm, rows in rows_by_arm.items():
        looks, http, tokens = [], [], []
        for r in rows:
            per = _summary_of(r).get("perception") or {}
            looks.append(int(per.get("looks") or 0))
            http.append(int(per.get("look_http_requests") or 0))
            tokens.append(sum(int(v or 0) for v in (per.get("look_tokens") or {}).values()))
        rounds = [int(r.get("decision_rounds") or 0) for r in rows]
        out[arm] = {
            "episodes": len(rows),
            "channel": ((_summary_of(rows[0]).get("perception") or {}).get("channel")
                        if rows else None),
            "looks_total": sum(looks),
            "look_http_requests_total": sum(http),
            "look_tokens_total": sum(tokens),
            "decision_http_requests_total": sum(int(r.get("http_requests") or 0) for r in rows),
            "prompt_tokens_total": sum(int(r.get("prompt_tokens") or 0) for r in rows),
            "completion_tokens_total": sum(int(r.get("completion_tokens") or 0) for r in rows),
            "wall_time_s_total": round(sum(float(r.get("wall_time_s") or 0) for r in rows), 2),
            "sim_time_s_total": round(sum(float(r.get("sim_time_s") or 0) for r in rows), 2),
            "skill_generation_cost": "not_measured (no producer before P4)",
            "looks_per_round": _rate("C1.looks_per_round", sum(looks), sum(rounds)),
            "model_calls_per_episode": _rate("C1.model_calls", sum(http), len(rows)),
            "tokens_per_episode": _rate("C1.tokens", sum(tokens), len(rows))}
    return out


# -------------------------------------------------------------------- output ----


def render_markdown(result: dict) -> str:
    lines = [f"# §11 contrast — `{result.get('set')}`, 口径 {result.get('definition_version')}",
             ""]
    if "metrics" in result:
        lines += ["| metric | value | numerator / denominator | 95 % Wilson | question |",
                  "|---|---|---|---|---|"]
        for k, r in result["metrics"].items():
            interval = ("—" if not r["wilson_95"]
                        else f"{r['wilson_95'][0]:.3f}–{r['wilson_95'][1]:.3f}")
            flag = " ⚠n<10" if r["denominator_too_small_for_a_rate"] else ""
            lines.append(f"| `{k}` | {r['value']}{flag} | {r['numerator']}/{r['denominator']} "
                         f"| {interval} | {r['question']} |")
        lines += ["", f"coverage: `{json.dumps(result['coverage'], ensure_ascii=False)}`", ""]
        la = result["ledger_vs_audits"]
        lines += [f"ledger rows **{la['ledger_rows']}**, audit rows **{la['audit_rows']}**, "
                  f"audit difference entries **{la['audit_difference_entries']}**; merged = "
                  f"`{la['merged']}`. Blind spots recovered by an audit: "
                  f"{la['blind_spots_recovered_by_an_audit']}, of which present in the ledger: "
                  f"{la['blind_spots_present_in_the_ledger']}.", "", "## per view",
                  "",
                  "Primary questions only (the 60-per-5-body-case 口径); the control column is "
                  "the separate set.", "",
                  "| view | questions | dangerous_true | honest_decline | confirmed | "
                  "no_answer | thinnest true margin mm | max centre error mm | "
                  "control questions | control over-claims |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for view, v in (result.get("per_view_verification") or {}).items():
            c = v["classes"]
            lines.append(f"| {view} | {v['questions']} | {c['dangerous_true']} | "
                         f"{c['honest_decline']} | {c['confirmed']} | {c['no_answer']} | "
                         f"{v['thinnest_true_margin_mm']} | {v['max_centre_error_mm']} | "
                         f"{v['control_questions']} | {v['control_over_claims']} |")
        lines += ["", "## shape vocabulary", ""]
        for shape, v in (result.get("shape_vocabulary") or {}).items():
            lines.append(f"- `{shape}`: {v['named_correctly']}/{v['declared']} named as "
                         f"declared; mis-named as `{v['named_as']}`")
        by_view = result.get("shape_vocabulary_by_view") or {}
        if by_view:
            lines += ["", "per view — the same rows, with the cameras kept apart "
                      "(P1-c residual 6)", "",
                      "| declared | " + " | ".join(by_view) + " |",
                      "|---|" + "---|" * len(by_view)]
            shapes = sorted({s for v in by_view.values() for s in v})
            for shape in shapes:
                cells = []
                for v in by_view.values():
                    row = v.get(shape)
                    cells.append("—" if not row else
                                 f"{row['named_correctly']}/{row['declared']} "
                                 f"`{row['named_as']}`")
                lines.append(f"| `{shape}` | " + " | ".join(cells) + " |")
    if "pairs" in result:
        lines += ["", "## episode contrast", ""]
        for arm, block in result["arms"].items():
            lines.append(f"- `{arm}` channel `{block['channel']}`: {block['episodes']} "
                         f"episodes in `{block['run_root']}`")
        lines += ["", f"paired cases: {result['paired_cases']}, not comparable: "
                  f"{result['not_comparable']}, unpaired: {result['unpaired']}", "",
                  f"coverage: {len(result.get('cases') or [])} case(s) of set "
                  f"`{result.get('set')}` × modes {result.get('modes')} × 1 repeat, "
                  f"rule planner, both arms: `{', '.join(result.get('cases') or [])}`", "",
                  "| kind | privileged | sensing | paired transition |", "|---|---|---|---|"]
        for f, cat in (result.get("categories") or {}).items():
            priv = ", ".join(f"{k}: {v}" for k, v in sorted(cat["privileged"].items()))
            sens = ", ".join(f"{k}: {v}" for k, v in sorted(cat["sensing"].items()))
            moves = ", ".join(f"`{k}` ×{v}" for k, v in sorted(cat["transitions"].items()))
            lines.append(f"| `{f}` | {priv} | {sens} | {moves} |")
        lines += ["", "| quantity | mean Δ (sensing − privileged) | median | min | max | n |",
                  "|---|---|---|---|---|---|"]
        for f, v in result["paired_diff_sensing_minus_privileged"].items():
            mark = (" †" if f in (result.get("arm_asymmetric_fields") or {}) else
                    " ‡" if f in (result.get("budget_censored_fields") or {}) else "")
            lines.append(f"| {f}{mark} | {v['mean']} | {v['median']} | {v['min']} | "
                         f"{v['max']} | {v['n']} |")
        excluded = {f: v for f, v in (result.get("not_a_quantity_on_every_pair") or {}).items()
                    if v}
        if excluded:
            lines += ["", "Not a quantity on every pair, so excluded from that row's mean "
                      "(named, not deleted):"]
            lines += [f"* `{f}` — {', '.join(v)}" for f, v in sorted(excluded.items())]
        asym = result.get("arm_asymmetric_fields") or {}
        if asym:
            lines += ["", "`†` — the two arms do not mean the same thing by this field; "
                      "the difference is printed because deleting it would hide the asymmetry, "
                      "not because it is comparable:", ""]
            lines += [f"* `{f}` — {why}" for f, why in sorted(asym.items())]
        censored = result.get("budget_censored_fields") or {}
        if censored:
            lines += ["", "`‡` — comparable, but truncated on one arm by a shared budget, so the "
                      "mean difference is a lower bound:", ""]
            lines += [f"* `{f}` — {why}" for f, why in sorted(censored.items())]
        progress = result.get("progress_self_report_vs_key") or {}
        if progress:
            lines += ["", "## the same episode counted twice", "",
                      "`objects_completed` in the table above is layer 3's count "
                      "(`_csv_row` reads it from `score`); the loop's own counter is "
                      "`result.objects_completed`, filled by the verifier that arm runs on. "
                      "Reporting one as a difference over the other would be the instrument swap "
                      "`†` exists to catch, so they are printed as a pair per arm:", ""]
            for arm, block in progress.items():
                over = block["over_claim_pairs"]
                lines.append(f"- `{arm}`: the loop claims more completed objects than the key "
                             f"credits on {len(over)} of {block['episodes']} episode(s)"
                             + (f" — {', '.join(over)}" if over else "")
                             + (f"; it reports *fewer* on {len(block['under_report_pairs'])}"
                                f" ({', '.join(block['under_report_pairs'])})"
                                if block["under_report_pairs"] else ""))
        lines += ["", "## cost", ""]
        for arm, block in result["cost"].items():
            lines.append(f"- `{arm}`: looks {block['looks_total']}, look http "
                         f"{block['look_http_requests_total']}, look tokens "
                         f"{block['look_tokens_total']}, decision http "
                         f"{block['decision_http_requests_total']}, wall "
                         f"{block['wall_time_s_total']} s, sim {block['sim_time_s_total']} s; "
                         f"looks/round {block['looks_per_round']['value']}")
    return "\n".join(lines) + "\n"


def write(result: dict, out_dir: str, name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(result))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", default="all", choices=list(SET_NAMES))
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--batteries", nargs="*", default=list(BATTERIES), choices=list(BATTERIES))
    ap.add_argument("--contrast", action="store_true",
                    help="also run the episode-level two-arm batch through run_group")
    ap.add_argument("--contrast-set", default="dev")
    ap.add_argument("--contrast-modes", nargs="*", default=list(DEFAULT_CONTRAST_MODES))
    ap.add_argument("--perceive", default="stub", help="the sensing arm of the contrast")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--definitions", action="store_true", help="print the 口径 and exit")
    args = ap.parse_args(argv)
    if args.definitions:
        print(json.dumps({"version": DEFINITION_VERSION,
                          "definitions": METRIC_DEFINITIONS}, indent=1, ensure_ascii=False))
        return 0
    if not args.contrast:
        result = measure_set(args.set, case_ids=args.cases, out_dir=args.out,
                             batteries=args.batteries)
        print(write(result, args.out, f"contrast_{args.set}"))
        print(render_markdown(result))
        return 0
    result = episode_contrast(args.contrast_set, case_ids=args.cases, out_root=args.out,
                              modes=args.contrast_modes, perceive=args.perceive)
    print(write(result, args.out, f"episode_contrast_{args.contrast_set}"))
    print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
