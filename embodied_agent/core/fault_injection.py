"""Experiment-environment events (SPEC 8 fault_injection, 10.1, 10.3).

Everything here is on the *environment* side of the agent boundary:

* an event is a real physical action (a measured impulse over simulated time, or
  an actuator calibration error applied to the actuated descent/release target),
  never an edit of a skill result or a returned flag (SPEC 8, 10.3);
* triggers are declared in advance by the scenario and fire on a *scene
  condition* — a skill kind, an entity, a target region, the N-th such call, a
  decision round. Nothing fires "because the model is about to fail"
  (SPEC 10.3);
* the agent receives none of it. Evidence goes to the episode log only; what the
  model may learn is whatever the next measurement shows.

Two scene-owned errors implement "execution deviation":

* `grasp_calibration_offset` — the actuated grasp point is off; the request
  stays as the model asked, and the attempt can still end with the object held
  (SPEC 10.2);
* `place_calibration_offset` — the actuated transfer point / release height is
  off, so the object is handed off somewhere other than the named candidate.

Whether such an event fired is recorded, including the episodes where it never
fired: those are kept and reported as trigger coverage, not discarded
(SPEC 10.3).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from .contracts import SkillCall, SkillStatus


class EventTrigger(str, enum.Enum):
    BEFORE_SKILL = "before_skill"   # arm something for this skill call
    AFTER_SKILL = "after_skill"     # act once the call has returned
    ON_ROUND = "on_round"           # fire at a fixed decision round


class EventAction(str, enum.Enum):
    IMPULSE = "impulse"                       # measured force/displacement disturbance
    GRASP_CALIBRATION = "grasp_calibration"   # actuator offset on the next descent
    PLACE_CALIBRATION = "place_calibration"   # actuator offset on the next release


@dataclass
class EnvironmentEvent:
    """One frozen disturbance.

    `skill` / `when_entity_id` / `when_target_id` / `at_match` describe *when* it
    applies; `entity_id` / `target_id` describe *what is affected*. They are
    separate on purpose: the main demo disturbs the object already resting in a
    tray while a different object is being released (SPEC 10.1), so the trigger
    cannot name the victim.

    `entity_id=None` with a `target_id` resolves the affected object from the
    scene at firing time ("whatever is measurably resting in that region"),
    which keeps the policy the same for every mode even though the modes act in
    different orders.
    """

    event_id: str
    trigger: EventTrigger
    action: EventAction
    entity_id: Optional[str] = None
    target_id: Optional[str] = None
    skill: Optional[str] = None
    when_entity_id: Optional[str] = None
    when_target_id: Optional[str] = None
    at_match: int = 1
    impulse_xy: list[float] = field(default_factory=lambda: [0.0, 0.0])
    duration_s: float = 0.05
    offset: Optional[list[float]] = None
    at_round: int = 0
    once: bool = True
    fired: bool = False
    matches_seen: int = 0
    fired_at: dict = field(default_factory=dict)

    def trigger_matches(self, *, skill: str | None, entity_id: str | None,
                        target_id: str | None) -> bool:
        if self.skill and skill is not None and self.skill != skill:
            return False
        if self.when_entity_id and self.when_entity_id != entity_id:
            return False
        if self.when_target_id and self.when_target_id != target_id:
            return False
        return True


class EnvironmentController:
    """Applies declared events to the scene between skill boundaries.

    It is constructed by the experiment runner from the frozen scenario
    definition. The Runtime only offers it `SkillCall`/`SkillResult` objects and
    writes the returned evidence to the log; no strategy, target or hint derived
    from it reaches the model."""

    def __init__(self, scene, events: list[EnvironmentEvent] | None = None):
        self.scene = scene
        self.events = list(events or [])
        self.store = None
        self.round_index = 0
        self.applied: list[dict] = []

    def bind_store(self, store) -> None:
        self.store = store

    # ---------- hooks the runtime calls ----------
    def before_skill(self, call: SkillCall) -> list[dict]:
        return self._fire(EventTrigger.BEFORE_SKILL, call, result=None)

    def after_skill(self, call: SkillCall, result) -> list[dict]:
        self.round_index += 1
        evidence = self._fire(EventTrigger.AFTER_SKILL, call, result=result)
        evidence += self._fire(EventTrigger.ON_ROUND, None, result=None)
        if call.skill == "pick":
            # an armed actuator error applied to that descent and no further
            self.disarm("grasp")
        if call.skill == "place":
            self.disarm("place")
        return evidence

    def unfired(self) -> list[dict]:
        """Declared-but-never-triggered events, with the reason stated as a fact
        (SPEC 10.3: report coverage and cause, keep the episode)."""
        return [
            {"event_id": ev.event_id, "action": ev.action.value,
             "skill": ev.skill, "when_target_id": ev.when_target_id,
             "matches_seen": ev.matches_seen,
             "reason": ("no call matched the declared scene condition"
                        if ev.matches_seen == 0 else "retired without acting")}
            for ev in self.events if not ev.fired
        ]

    # ---------- firing ----------
    def _fire(self, trigger: EventTrigger, call: SkillCall | None, result) -> list[dict]:
        out = []
        for ev in self.events:
            if ev.trigger != trigger or (ev.fired and ev.once):
                continue
            if self.round_index < ev.at_round:
                continue
            skill = call.skill if call else None
            entity_id = call.args.get("object_id") if call else None
            target_id = call.args.get("target_id") if call else None
            if not ev.trigger_matches(skill=skill, entity_id=entity_id, target_id=target_id):
                continue
            ev.matches_seen += 1
            if ev.matches_seen < ev.at_match:
                continue
            if trigger is EventTrigger.AFTER_SKILL and result is not None \
                    and result.status == SkillStatus.rejected:
                continue  # a refused action moved nothing; firing would be a lie
            affected = self._affected(ev, call)
            evidence = self._apply(ev, affected)
            if evidence is None:
                continue
            ev.fired = True
            ev.fired_at = {"round": self.round_index, "call_id": getattr(call, "call_id", None),
                           **evidence}
            self.applied.append({"event_id": ev.event_id, "action": ev.action.value, **evidence})
            out.append({"event_id": ev.event_id, "action": ev.action.value, **evidence})
        return out

    def _affected(self, ev: EnvironmentEvent, call: SkillCall | None) -> str | None:
        if ev.entity_id and ev.target_id:
            # A named victim in a named region: it may only be disturbed once it
            # is actually resting there. Which object that is must not depend on
            # the order the agent happened to choose, but disturbing an object
            # that is still on the table — or still in the fingers — would.
            return ev.entity_id if ev.target_id in self.scene.contact_partners(ev.entity_id) else None
        if ev.entity_id:
            return ev.entity_id
        if ev.target_id:
            released = call.args.get("object_id") if call else None
            resting = sorted(
                eid for eid in self.scene.objects
                if eid != released and ev.target_id in self.scene.contact_partners(eid))
            return resting[0] if resting else None
        return call.args.get("object_id") if call else None

    def _apply(self, ev: EnvironmentEvent, affected: str | None) -> dict | None:
        if ev.action is EventAction.IMPULSE:
            if affected not in self.scene.objects:
                return None
            measured = self.scene.apply_environment_impulse(affected, ev.impulse_xy, ev.duration_s)
            measured["event_id"] = ev.event_id
            return measured
        if ev.action is EventAction.GRASP_CALIBRATION:
            # the *actuated* descent target is off by this frozen amount; the
            # request and every returned value stay untouched
            self.scene.grasp_calibration_offset = list(ev.offset or [0.0, 0.0, 0.0])
            self.scene.grasp_calibration_event = ev.event_id
            return {"event_id": ev.event_id, "entity_id": affected,
                    "offset": list(self.scene.grasp_calibration_offset)}
        if ev.action is EventAction.PLACE_CALIBRATION:
            self.scene.place_calibration_offset = list(ev.offset or [0.0, 0.0, 0.0])
            self.scene.place_calibration_event = ev.event_id
            return {"event_id": ev.event_id, "entity_id": affected,
                    "offset": list(self.scene.place_calibration_offset)}
        return None

    def disarm(self, which: str = "both") -> None:
        if which in ("grasp", "both"):
            self.scene.grasp_calibration_offset = None
            self.scene.grasp_calibration_event = None
        if which in ("place", "both"):
            self.scene.place_calibration_offset = None
            self.scene.place_calibration_event = None

    # ---------- reporting ----------
    def declared(self) -> list[dict]:
        """The frozen scenario definition, written to the run manifest so a
        reader can see what was planned, not only what happened."""
        return [
            {"event_id": ev.event_id, "trigger": ev.trigger.value, "action": ev.action.value,
             "skill": ev.skill, "entity_id": ev.entity_id, "target_id": ev.target_id,
             "when_entity_id": ev.when_entity_id, "when_target_id": ev.when_target_id,
             "at_match": ev.at_match, "impulse_xy": list(ev.impulse_xy),
             "offset": list(ev.offset) if ev.offset else None, "fired": ev.fired,
             "once": ev.once,
             "matches_seen": ev.matches_seen}
            for ev in self.events
        ]
