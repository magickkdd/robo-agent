"""From a percept to a versioned `WorldState` (SPEC-v0.2 §5.1, §7 step 1).

§7's first step is "VLM/Observation 更新 WorldState", and this file is the whole of it:
one `PerceptionObservation` in, one snapshot out. It adds no measurement of its own —
every metre here was produced by `perception/geometry.py` from the depth image, every
word by a reader, and the tray geometry by the declared catalog — so the question this
module answers is a narrower and more testable one:

    which of the six things §5.1 asks a perceiver to say can a v0.1 `WorldState`
    *hold* without being told something it did not measure?

Three rules follow from that question and are the content of the file.

**A field the percept did not answer stays `unknown`.** `held`, `at_rest`, the two
speeds and `held_object` have no pixel channel at all, so they are never filled by a
convenient default here. The v0.1 contract already has the right type for each of them.

**An assumption is not a measurement.** The estimator solves a body's centre as
`measured support z + declared *upright* half-height`, so a back-projected pose carries
an orientation assumption inside its position, and its quaternion is that assumption
written down. Such an entity says `orientation_measured=False`, which
`verify.entity_footprint_half_xy` turns into a bound valid at any orientation instead of
the optimistic upright pair, and which keeps `yaw_rad` out of the model payload. The
same fact is in `attributes` because that is the one place a *model* can read it.

**A body this frame did not see is not an entity.** An entity is groundable: the task
interpreter binds "绿色圆柱" to any entity whose attributes match, with no notion of
visibility. So a `visible=False` row — the frame's answer to a task item it looked for
and did not find — must stay in the percept's `visibility` group and this module's
per-view memory, or a plan would be handed an object to pick up that the picture says is
not there. `test_an_unseen_body_is_not_an_entity_because_grounding_would_bind_to_it`
holds the line.

Relations are stored per view, and a snapshot is built from exactly one frame. Two views
of one still table disagree about what blocks what (`main` says yellow hides red,
`overhead` says purple hides blue), which is correct behaviour; merging them would
assert a relation nobody saw. `observe` therefore compares a new percept only with the
previous percept *of the same camera*, and records an explicit `not_comparable` row
otherwise.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ..core.contracts import (
    EntityState,
    OccupancyRecord,
    Pose,
    Source,
    SupportEvidence,
    TargetRegion,
    Vec3,
    WorldState,
)
from ..core.v02 import ChangeRecord, PerceptionObservation
from ..core.verify import measure_occupancy
from .catalog import PerceptionCatalog

# The quaternion of "we did not look": position is measured, heading is not, and the
# flag on the entity is what stops this from being read as a claim of upright.
ASSUMED_UPRIGHT = (0.0, 0.0, 0.0, 1.0)

# What the state layer carries, and which `ChangeRecord.attribute` names the same fact.
# `position` is deliberately absent: the state holds metres but makes no change claim
# about them, because the estimator's own cross-view noise (18.13 mm worst case over 15
# body/view pairs, `work/p1_perceive_check.py`) is what `move_threshold_m` exists to sit
# above. A diff of two snapshots would either repeat that noise or redefine it.
CHANGE_FIELDS = {
    "shape": "shape",
    "relation": "supported_by",
    "occluded_by": "hidden_behind",
    "next_to": "next_to",
}

# Fields a v0.1 `WorldState` can hold but §5.1's change channel never claims: pairwise
# rows are not per-subject facts, so `PerceptionAssembler._changes` does not diff them.
# Named here so the audit can count them instead of leaving them looking like agreement.
UNCLAIMED_ATTRIBUTES = ("next_to",)

# What may be compared between two snapshots of *different* views, and what may not.
# `supported_by` is the only world-metres fact a snapshot carries that does not depend on
# where the camera was: it is a measured surface height against a declared floor. Shape,
# adjacency and occlusion are all statements about a projection — `work/p1_perceive_check.py`
# measures three blockers named for one still table, one per view — so a difference in
# them across views says nothing about the world, and P1-b measured the position noise
# between views at up to 18.13 mm, which is a body's own width.
VIEW_INVARIANT_FIELDS = {"relation": "supported_by"}
VIEW_DEPENDENT_FIELDS = {
    "shape": "the silhouette fit ranks the declared shapes differently per view: "
             "`main` and `overhead` disagree about one body's shape on a table that "
             "did not move",
    "next_to": "adjacency is a statement about two boxes in one image",
    "occluded_by": "what hides a body is a fact about the camera, not about the body",
}


def _str(value: Any) -> str:
    return str(value).strip().lower() if isinstance(value, bool) else str(value)


def support_relations(percept: PerceptionObservation) -> dict[str, tuple[str, str]]:
    """`{entity_id: (predicate, related_id)}` for the per-subject support rows.

    Read back out of the percept rather than recomputed: `_relations` already decided
    which surface each located body rests on, already compared that against what the
    reader claimed, and already recorded a conflict when the two disagreed. A second
    opinion here would let the state and the percept disagree about the same body.
    Pairwise and unknown rows are excluded for the same reason the change diff
    excludes them — they are not per-subject claims."""
    return {r.subject_id: (_str(r.predicate), _str(r.related_id))
            for r in percept.relations
            if r.predicate not in ("next_to", "blocks", "unknown")
            and r.subject_id and r.related_id}


def neighbour_lists(percept: PerceptionObservation) -> dict[str, list[str]]:
    """Who each body is beside, as the frame that saw them says.

    Both directions of one row are recorded: `next_to` is symmetric in the world and
    one-directional in the record, and a state that listed neighbours only for the first
    subject would make the same pair look different depending on detection order."""
    out: dict[str, list[str]] = {}
    for r in percept.relations:
        if r.predicate != "next_to":
            continue
        for a, b in ((r.subject_id, r.related_id), (r.related_id, r.subject_id)):
            if a and b and b not in out.setdefault(a, []):
                out[a].append(b)
    return {k: sorted(v) for k, v in out.items()}


def hidden_by(percept: PerceptionObservation) -> dict[str, str]:
    """`{hidden_id: blocker_id}` from the visibility group, which is where the assembler
    puts the answer for a body it looked for as well as one it found."""
    return {v.entity_id: v.occluded_by for v in percept.visibility
            if v.occluded_by and v.entity_id}


def entities_from_percept(percept: PerceptionObservation) -> list[EntityState]:
    """One entity per detection — including the ones the depth image could not locate."""
    support = support_relations(percept)
    neighbours = neighbour_lists(percept)
    hidden = hidden_by(percept)
    fits = percept.provenance.get("silhouette_fit") or {}
    out: list[EntityState] = []
    for det in percept.detections:
        eid = det.entity_id
        xyz = det.state_attributes.get("position_xyz_m")
        colour = _str(det.state_attributes.get("color") or "unknown")
        shape = _str(det.state_attributes.get("shape") or "unknown")
        predicate, related = support.get(eid, ("unknown", "unknown"))
        supported_by: str = related if predicate == "in" else (
            "table" if predicate == "on" else "unknown")
        attributes: dict[str, str] = {
            "color": colour, "shape": shape,
            "orientation_measured": "false",
            "shape_source": _str(det.state_attributes.get("shape_source") or "unknown"),
            "how_identified": _str(det.how_identified or "unknown"),
            "partly_hidden": _str(bool(det.state_attributes.get("partly_hidden"))),
            "confidence": f"{float(det.confidence):.3f}",
            # which pixels this claim rests on, so a reviewer can re-open the frame
            "view": str(percept.camera_id or ""),
        }
        if neighbours.get(eid):
            attributes["next_to"] = ",".join(neighbours[eid])
        if hidden.get(eid):
            attributes["hidden_behind"] = hidden[eid]
        claimed = _str(det.state_attributes.get("claimed_on_or_in") or "")
        if claimed and claimed != related:
            # kept as the reader's own word beside the measured one, never instead of it:
            # this is the pair §5.1's uncertain-marking exists to make visible
            attributes["reader_claim"] = claimed
        fit = (fits.get(eid) or {}).get("segmentation") or {}
        for key in ("fill_ratio", "rows_split"):
            if key in fit:
                # no threshold is applied to either number here. They are carried so the
                # consumer that decides "is this outline one object" can exist at all —
                # P1-b left the measurement with no reader (phase log P1-b §6-6).
                attributes[f"mask_{key}"] = str(fit[key])
        margin = (fits.get(eid) or {}).get("margin")
        if margin is not None:
            attributes["shape_margin"] = str(margin)
        if xyz is not None:
            attributes["support_z_m"] = str(det.state_attributes.get("support_z_m"))
        out.append(EntityState(
            entity_id=eid,
            # -1 is the contract's "no simulator body behind this name", and it is true
            # here in the strongest sense: this channel never learns one.
            body_id=-1,
            pose=(None if xyz is None else Pose(
                position=Vec3(x=float(xyz[0]), y=float(xyz[1]), z=float(xyz[2])),
                quaternion_xyzw=ASSUMED_UPRIGHT, frame_id="world")),
            orientation_measured=False,
            geometry=det.extent,
            held="unknown",
            support=SupportEvidence(support_id=supported_by, contact_count=0,
                                    source=Source.sensor),
            supported_by=supported_by,
            at_rest="unknown",
            linear_speed_mps=None,
            angular_speed_rps=None,
            visible=True,
            confidence=round(float(det.confidence), 3),
            attributes=attributes,
            source=Source.sensor,
        ))
    return out


def world_state_from_percept(percept: PerceptionObservation, catalog: PerceptionCatalog,
                             *, state_version: int, occupancy: bool = True) -> WorldState:
    """The snapshot this percept supports, on the same geometry code a privileged
    snapshot runs on.

    `targets` come from the catalog because tray positions, the palette and the declared
    dimensions are the same in every episode — that is what makes them calibration
    (§3.3), and `test_the_tray_declaration_is_the_same_in_every_case` checks it over all
    47 frozen cases rather than asserting it. Which body is where is not calibration, and
    appears only in `entities`.
    """
    targets: list[TargetRegion] = [decl.as_region() for decl in catalog.regions]
    entities = entities_from_percept(percept)
    digest = str(percept.provenance.get("catalog_sha256") or "")
    if digest and digest != catalog.sha256():
        raise ValueError(
            f"this percept was assembled against catalog {digest[:12]} and this tracker "
            f"holds {catalog.sha256()[:12]}: the tray positions in its relations are "
            f"then not the tray positions written into the snapshot")
    records: list[OccupancyRecord] = []
    if occupancy:
        # `measure_occupancy` is the shared function — the privileged snapshot's occupancy
        # rows are made by this same call. Reusing it is the point: a privileged
        # `fully_inside` and a percept `fully_inside` must differ only in the evidence
        # behind them, never in the arithmetic (SPEC-v0.2 §11 measures one against the
        # other).
        records = measure_occupancy(entities, targets)
    return WorldState(
        state_version=int(state_version),
        sim_time=float(percept.timestamp_sim),
        wall_time=float(percept.timestamp_wall) or time.time(),
        entities=entities,
        targets=targets,
        # Nothing in a frame of a table answers what the hand is holding, and the three
        # declared views are not asked to. `None` would be a claim of "empty"; this is
        # the contract's own word for "this snapshot does not know".
        held_object="unknown",
        observation_ref=percept.observation_id,
        occupancy=records,
        source=Source.sensor,
    )


def state_differences(prev: WorldState, now: WorldState) -> list[tuple[str, str, str]]:
    """What one snapshot says changed against another, in the same words
    `ChangeRecord.attribute` uses, so the two can be reconciled without a translator.

    Identity is colour, so a recoloured body arrives as a disappearance and an
    appearance rather than one row — the same consequence `percept_id` accepts inside the
    assembler, and it must be counted the same way in both places or the audit measures
    its own vocabulary."""
    a = {e.entity_id: e for e in prev.entities}
    b = {e.entity_id: e for e in now.entities}
    rows: list[tuple[str, str, str]] = []
    for eid in sorted(set(a) - set(b)):
        rows.append((eid, "visibility", "seen -> not_reported"))
    for eid in sorted(set(b) - set(a)):
        rows.append((eid, "presence", "not_reported -> seen"))
    for eid in sorted(set(a) & set(b)):
        before, after = a[eid], b[eid]
        for attribute, field in CHANGE_FIELDS.items():
            left = (before.supported_by if field == "supported_by"
                    else before.attributes.get(field, ""))
            right = (after.supported_by if field == "supported_by"
                     else after.attributes.get(field, ""))
            if _key(left) != _key(right):
                rows.append((eid, attribute, f"{_key(left)} -> {_key(right)}"))
    return rows


def _key(value: Any) -> str:
    """A comparable form of a carried field: `next_to` holds a list, the rest a word."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value).strip().lower()


def reconcile(prev: WorldState, now: WorldState, percept: PerceptionObservation,
              ) -> dict[str, Any]:
    """Do the reported changes and the resulting snapshots say the same thing?

    Three buckets, because a change row can fail in three different ways:

    * `unreported_differences` — the state moved and nothing was reported. That is the
      one §5.1's change detection is measured on, and it must be empty.
    * `rows_without_a_state_difference` — a report about a fact two snapshots agree on.
      Rows whose subject is not an entity in both snapshots are not counted here: a body
      the frame stopped reporting has no attribute left for the state to carry an answer
      about, and that belongs in `rows_about_bodies_the_state_does_not_carry`, not in a
      bucket that reads as a defect.
    * `unclaimed_attribute_differences` — differences in a field the change channel
      never claims, counted rather than hidden.
    """
    states = state_differences(prev, now)
    claimed = [row for row in states if row[1] not in UNCLAIMED_ATTRIBUTES]
    both = {e.entity_id for e in prev.entities} & {e.entity_id for e in now.entities}
    rows = {(c.subject_id, c.attribute) for c in percept.changes}
    stated = {(s[0], s[1]) for s in claimed}
    not_a_state_claim = {"position", "position_below_threshold"}
    # a row about existence *is* carried: the entity appearing or going is the difference
    existence = {"presence", "visibility"}
    return {
        "camera_id": percept.camera_id,
        "observation_id": percept.observation_id,
        "previous_observation_id": percept.provenance.get("previous_observation_id"),
        "state_differences": [list(s) for s in claimed],
        "reported_changes": [list(r) for r in sorted(rows)],
        "unreported_differences": [list(s) for s in claimed if (s[0], s[1]) not in rows],
        "rows_without_a_state_difference": [list(r) for r in sorted(rows)
                                            if r[1] not in not_a_state_claim
                                            and r not in stated
                                            and (r[0] in both or r[1] in existence)],
        "rows_about_bodies_the_state_does_not_carry": [
            list(r) for r in sorted(rows) if r[0] not in both and r[1] not in existence],
        "position_rows_not_carried_by_the_state": sorted(
            f"{r[0]}:{r[1]}" for r in rows if r[1] in not_a_state_claim),
        "unclaimed_attribute_differences": [list(s) for s in states
                                            if s[1] in UNCLAIMED_ATTRIBUTES],
        # the two audit kinds carry each other's keys so one loop can read a run's whole
        # ledger, and this row is by definition not a cross-view one
        "cross_view_disagreements": [],
    }


def cross_view_check(prev: WorldState, now: WorldState, *, view: str,
                     previous_view: str) -> dict[str, Any]:
    """The one comparison two views *may* make, and the reasons for the three it may not.

    When the agent changes viewpoint, the change channel correctly reports nothing — and
    a placement made in between would otherwise pass without a trace, which is the
    failure this function exists to catch. It is not a `ChangeRecord`: §5.1's change
    detection belongs to the perceiver, §11 counts it from the percept, and a
    comparison the perceiver declined to make must not be laundered into its ledger. It
    is an audit row, and it says plainly that the two answers could be two readings of
    one still world rather than one reading of two worlds.
    """
    a = {e.entity_id: e for e in prev.entities}
    b = {e.entity_id: e for e in now.entities}
    disagreements = []
    for eid in sorted(set(a) & set(b)):
        left, right = str(a[eid].supported_by), str(b[eid].supported_by)
        if "unknown" not in (left, right) and left != right:
            disagreements.append([eid, "relation", f"{left} -> {right}"])
    return {
        "camera_id": view,
        "previous_camera_id": previous_view,
        "not_comparable": (f"no change is reconciled across {previous_view!r} -> "
                           f"{view!r}: what a view says about shape, adjacency and "
                           f"occlusion is a statement about its own image"),
        "fields_compared": sorted(VIEW_INVARIANT_FIELDS),
        "fields_declined": dict(VIEW_DEPENDENT_FIELDS),
        "cross_view_disagreements": disagreements,
        "bodies_compared": len(set(a) & set(b)),
        # the same keys `reconcile` returns, empty here so a consumer reading either
        # kind of audit row cannot crash on a missing key
        "state_differences": [], "unreported_differences": [],
        "rows_without_a_state_difference": [],
        "rows_about_bodies_the_state_does_not_carry": [],
        "position_rows_not_carried_by_the_state": [],
        "unclaimed_attribute_differences": [],
    }


class PerceptWorldStateTracker:
    """Percepts in, versioned snapshots out, one view at a time.

    Owns the two things a single `assemble` call cannot know: which percept came before
    (the assembler takes `previous` as an argument, so *someone* must remember, and a
    change is only worth reporting if the memory behind it is the right one) and which
    `state_version` this snapshot is (§7: the version is assigned by a monotonic ledger,
    never by a caller's guesswork).
    """

    def __init__(self, catalog: PerceptionCatalog, *, episode_id: Optional[str] = None,
                 first_version: int = 1):
        self.catalog = catalog
        self.episode_id = episode_id
        self.version = int(first_version) - 1
        self.percept: Optional[PerceptionObservation] = None
        self.state: Optional[WorldState] = None
        #: the last percept of each camera, keyed by view. Cross-view comparisons are
        # refused, so what they hold is memory of *how things looked*, not a baseline.
        self.by_view: dict[str, PerceptionObservation] = {}
        #: every change row ever reported, oldest first — §11's state-change metric is
        # counted from this ledger, not from a re-derivation.
        self.ledger: list[ChangeRecord] = []
        self.audits: list[dict[str, Any]] = []

    # ---------- the one entry point ----------
    def observe(self, percept: PerceptionObservation) -> WorldState:
        """Adopt one percept and return the snapshot it supports."""
        view = str(percept.camera_id or "")
        same_view = self.percept is not None and self.percept.camera_id == view
        prev_state = self.state
        self.version += 1
        state = world_state_from_percept(percept, self.catalog, state_version=self.version)
        if not same_view and self.percept is not None:
            audit = cross_view_check(prev_state, state, view=view,
                                     previous_view=str(self.percept.camera_id or ""))
            audit["observation_id"] = percept.observation_id
            audit["previous_observation_id"] = self.percept.observation_id
            audit["reported_changes"] = [[c.subject_id, c.attribute]
                                         for c in percept.changes]
            self.audits.append(audit)
        elif prev_state is not None:
            self.audits.append(reconcile(prev_state, state, percept))
        self.percept = percept
        self.state = state
        self.by_view[view] = percept
        self.ledger.extend(percept.changes)
        return state

    def perceive(self, assembler, frame, reading) -> tuple[PerceptionObservation, WorldState]:
        """Assemble against this tracker's own memory and adopt the result.

        The assembler is passed in rather than built here because its thresholds are a
        run's configuration and must be recordable in a manifest; this class is what
        supplies the `previous` argument it demands."""
        percept = assembler.assemble(frame, reading, previous=self.percept,
                                     episode_id=self.episode_id)
        return percept, self.observe(percept)

    # ---------- what a frame did not find ----------
    def unseen(self, view: Optional[str] = None) -> list[tuple[str, bool, Optional[str]]]:
        """`(entity_id, visible, occluded_by)` rows this view reported as not seen.

        Deliberately not entities — see the module docstring — and deliberately kept,
        because "the green cylinder is hidden behind the yellow cube" is the answer §5.1
        asks for and the sentence a clarification or an observation skill is built on."""
        out = []
        percept = self.by_view.get(view) if view else self.percept
        if percept is None:
            return out
        detected = {d.entity_id for d in percept.detections}
        for v in percept.visibility:
            if v.entity_id not in detected and v.visible is not True:
                out.append((v.entity_id, bool(v.visible), v.occluded_by))
        return out

    def unreported(self) -> list[dict[str, Any]]:
        """Audit rows where the world moved under the agent and no change was reported."""
        return [a for a in self.audits if a.get("unreported_differences")]

    def disagreements(self) -> list[dict[str, Any]]:
        """Audit rows where two views answer the one view-invariant question differently.

        Either the world changed while the agent was looking elsewhere — which is the
        case that makes this worth having — or one of the two readings is wrong. The row
        cannot tell them apart and does not claim to; it is the reason §11's change
        metric is counted per view rather than over a run's whole image set."""
        return [a for a in self.audits if a.get("cross_view_disagreements")]
