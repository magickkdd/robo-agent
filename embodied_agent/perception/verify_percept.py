"""Layer-2 verification from percept evidence alone (SPEC-v0.2 §5.8 layer 2, §7 step 9).

§5.8 splits verification into three layers whose *information sources* must stay apart:
`SkillResult` (what the actuators did), runtime verification (whether current evidence
supports the postcondition), and the independent evaluator (task scoring). This module is
the second layer reading the first through a camera. P1-c produced a `WorldState` from a
percept; the question here is which of that snapshot's fields are allowed to answer a
postcondition, and what the layer must say when the frame cannot.

Three things are refused, and they are the whole content of the file.

**The seating-height comparison is declined, not passed.** `verify.state_supported_on`
compares an entity's z against `seated_rest_z(geometry, region, entity.pose.quaternion)`.
For a percept entity both sides of that subtraction come from the same upright assumption
(P1-c: `position = measured support z + declared *upright* half-height`, and the quaternion
*is* that assumption), so the difference is ~0 by construction — the check would confirm
the assumption that produced it. A verification that cannot fail is not a verification.
The sub-fact is therefore named in `unmeasured` and the numbers it would have used are
never computed here; `test_the_declined_fact_is_never_even_computed` pins that by looking
for the key only that comparison produces.

**Absence is `unknown`, never `false`.** A body this frame did not see, or saw without
locating, gives no verdict about where it rests. `false` is reserved for positive contrary
evidence — a *measured* surface that is not the target — because a false postcondition
makes the agent re-do work, while an unknown one only makes it look again. That asymmetry
is the same one P1-c measured in the placement verdicts (three `conservative`, zero
`dangerous` — at the occupancy level, on one body in an otherwise empty tray; the crowded
configuration `work/p1_verify_check.py` runs is where the third refusal below was found),
and it is why `fully_inside` being False on the widened footprint is *not* evidence of
being outside: the bound can only over-claim *extent*. A wrong *position* is a different
error, and no widening protects against it.

**Occupancy is not placement.** The first draft of this file graded `placed` on the
snapshot's own `fully_inside` occupancy row, which is the wrong predicate: occupancy is
computed with a *relaxed* margin precisely so that "an object jammed against a wall still
occupies the region it physically blocks" holds. A body straddling a wall both blocks the
tray and has not been put in it, and that gap is where the channel's one measured over-claim
came from — a view whose centre estimate fell inward of the true position by more than the
footprint widening reached outward, so a relaxed test passed on a body the answer key put
outside. The bar is therefore `state_inside` with the *tightened* declared margin, the same
call the privileged verifier makes, and `occupies_target` is reported beside it as the
separate, differently-answered question it is.

`outcome_status` is where `SkillStatus.uncertain` finally gets a producer (P0 §3 kept the
enum member alive for exactly this). It is the channel that decides, not the loop:
`RuntimeVerifier.outcome_status` returns `None`, so a v0.1 privileged snapshot still
reports `completed` — the sealed baseline's status vocabulary is not quietly widened —
while this channel answers "executed, and the current evidence neither supports nor
contradicts what was attempted".

**What the refusals buy, and what they do not, is measured** by
`work/p1_verify_check.py` on `dev_c5`, 5 bodies × 3 declared views × 4 configurations =
60 placement verdicts, against the privileged answer key: 15/15 `true` and 0 uncertain on a
single body seated alone in an empty tray; 8 `true` and 7 `unknown` with every body crowded
into one tray; 15 `false` and 0 `unknown` for a tray question about a body resting on the
table; 13 `unknown` and 2 `true` for a body parked with its footprint across the inner wall.
Those two are the finding this file cannot decline away, and both are `front_high`: claimed
wall clearance 25.1 mm against a measured centre error of 38.9 mm, so the view put a body
*inside* that the answer key has outside. The occupancy bar this file first used had already
produced one such row in the crowded configuration — a 16.9 mm inward error on a body whose
true footprint crossed the wall by 0.6 mm — which is what the tightening above was for; it
shrunk the exposure, it did not close it, and no already-declared threshold would.
`wall_margin_m` is published in the evidence precisely so that a `true` won by 25 mm on a
view whose error reaches 39 mm is auditable rather than trusted. The over-claim rate is
therefore §11's to count (P1-f), not a property this layer can promise; what it can promise
is the four rules above, and `tests/contract/test_v02_percept_verification.py` pins those,
never a rate.
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..core.contracts import (
    PredicateReport,
    PredicateVerdict,
    SkillStatus,
    Source,
    WorldState,
)
from ..core.verify import RuntimeVerifier, state_inside

# What a camera frame cannot answer about a placement, named so §11's unknown-handling
# metric has something to count. These are not failures of this verifier; they are the
# facts the channel does not have, and they stay out of the verdict in both directions.
DECLINED_FACTS = ("rest_z_vs_assumed_orientation", "hold_state", "at_rest")

# Which sub-fact is missing when the answer is `unknown` for a reason that is about the
# frame rather than about the world. Kept separate from DECLINED_FACTS because "this
# channel never measures velocity" and "this frame did not find the body" resolve
# differently: one needs a different sensor, the other needs a different look.
UNANSWERABLE = ("not_in_this_frame", "position_unmeasured", "relation_unmeasured")


def _occupancy_record(world: WorldState, entity_id: str, target_id: str):
    return next((o for o in world.occupancy if o.entity_id == entity_id
                 and o.target_id == target_id), None)


class PerceptVerifier(RuntimeVerifier):
    """Answers a postcondition from one percept-derived snapshot, `Source.sensor`.

    Constructed with the view it is answering *for* so that a refusal can name a view
    which would resolve it — and never the view being asked (P1-b §4-5: a refusal that
    points back at the camera that just failed is worse than no refusal)."""

    def __init__(self, world: WorldState, config=None, *, view: Optional[str] = None,
                 alt_views: Sequence[str] = ()):
        super().__init__(world, config, source=Source.sensor)
        self.view = view
        self.alt_views = [v for v in alt_views if v and v != view]

    # ---------- what this channel will not pretend to know ----------
    def would_resolve(self, reason: str) -> list[str]:
        """Where a *further look* could answer the sub-fact `reason`, as recorded names.

        Only the frame-side names are resolvable by looking again; the declined facts
        need a different instrument, and saying so is the point of keeping the two lists
        apart. An empty list is the honest answer for "no camera will settle this"."""
        if reason in DECLINED_FACTS:
            return []
        return [f"re_observe:{v}" for v in self.alt_views]

    def outcome_status(self, result, reports: list[PredicateReport]):
        """`uncertain` when the postcondition was verified and every answer is `unknown`."""
        if not reports or result is None or result.status != SkillStatus.completed:
            return None
        if all(r.value == PredicateVerdict.unknown for r in reports):
            return SkillStatus.uncertain
        return None

    # ---------- the placement postcondition, sensor version ----------
    def verify_placement(self, eid: str, target_id: str) -> PredicateReport:
        """`placed:<eid>:<target>` from the three facts a frame can actually supply.

        The relation row (which surface this body's support height matched) and the
        footprint test are the whole of it; `not_held`/`at_rest`/`rest_z` are named and
        left. `state_inside` is the *same* call the privileged verifier makes for its
        `inside` sub-fact — same region, same declared margin — so the two channels differ
        only in the evidence behind the answer, never in the arithmetic. A `fully_inside`
        occupancy row is deliberately not the bar: "occupies the region it physically
        blocks" is a statement about blocking, and an object jammed against a wall both
        blocks the tray and has not been placed in it."""
        pid = f"placed:{eid}:{target_id}"
        region = self.world.target(target_id)
        if region is None:
            return self._unknown(pid, "target not present in this frame", ("target",))
        if not self.world.has_entity(eid):
            # the frame did not report the body at all: nothing here speaks about where
            # it is, so a `false` would be a claim about an unobserved world
            return self._unknown(pid, "not reported by this view",
                                 ("not_in_this_frame",) + DECLINED_FACTS)
        entity = self.world.entity(eid)
        if entity.pose is None:
            return self._unknown(pid, "seen but not located: no metres to check",
                                 ("position_unmeasured",) + DECLINED_FACTS)
        parts: dict[str, float] = {"claim_confidence": float(entity.confidence), "located": 1.0}
        surfaces = ([str(entity.supported_by)] if entity.supported_by not in (None, "unknown")
                    else [])
        relations_right = target_id in sum((s.split("+") for s in surfaces), [])
        parts["relation_matches_target"] = float(relations_right)
        inside, geometry = state_inside(entity, region, self.config.footprint_margin_m)
        parts.update({k: float(v) for k, v in geometry.items()})
        parts["wall_margin_m"] = round(min(
            geometry["allow_x"] - geometry["xy_err_x"] - geometry["footprint_half_x"],
            geometry["allow_y"] - geometry["xy_err_y"] - geometry["footprint_half_y"]), 4)
        record = _occupancy_record(self.world, eid, target_id)
        parts["occupies_target"] = float(bool(record is not None and record.fully_inside))
        if entity.supported_by in (None, "unknown"):
            unmeasured = ["relation_unmeasured"] + list(DECLINED_FACTS)
        elif relations_right:
            unmeasured = list(DECLINED_FACTS)
        else:
            unmeasured = ["relation_other_surface"] + list(DECLINED_FACTS)
        if relations_right and inside:
            value = PredicateVerdict.true
            why = ("resting on the target surface and its orientation-invariant footprint "
                   "clears the wall by the declared margin; hold, rest and seating height "
                   "unmeasured")
        elif relations_right:
            # the widened bound failing is *not* evidence of being outside — it is the
            # bound being conservative about a body whose heading was never measured
            value = PredicateVerdict.unknown
            why = ("on the target surface, but the widened footprint does not clear the "
                   "wall: conservative, not contrary")
            unmeasured = ["inside_unproven_at_invariant_bound"] + list(DECLINED_FACTS)
        elif entity.supported_by not in (None, "unknown"):
            value = PredicateVerdict.false
            why = f"measured support is {entity.supported_by}, not {target_id}"
        else:
            value = PredicateVerdict.unknown
            why = "no support surface was measured for this body in this frame"
        return PredicateReport(
            predicate_id=pid, description=why, value=value, evidence=parts,
            unmeasured=unmeasured,
            evidence_refs=[self.world.observation_ref or ""], source=self.source)
