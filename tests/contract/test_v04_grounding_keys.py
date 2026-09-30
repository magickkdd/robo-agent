"""v0.4 R3 contract tests: the grounding bridge grows a second key — (colour, shape).

The finding these hold (v0.3 report §19.3, after the v0.4 P1 re-judgment): the frame
names bodies `seen:<colour>` and can never see the environment's names (`body_id=-1` is
the contract), so when one declared colour covers two bodies the bridge is not a function
and `from_objects` refuses — which is exactly what killed `lh_c3`'s four non-privileged
episodes in E2 (the v0.3 §18.2 correction had it backwards; the original delivery was
right). The frame's own measured `attributes["shape"]` is a legitimate second key: c3's
two yellow bodies are a cube and a cuboid, and the reader records shape on every
detection.

Three degradation rules, all asserted here:

1. colour-unique declarations (all 47 clean/protocol cases, 8 episodic cases) bind
   exactly as before — same `by_colour`, same digest, shape never consulted;
2. a colour declared twice with distinct shapes binds through (colour, shape);
3. a colour that would collide on (colour, shape) is still refused — the refusal narrows,
   it does not disappear, and an `unknown`-shape detection in an ambiguous scene stays
   ungrounded under its frame name rather than silently picking a winner.

The last test is the instrument §18.3 asked for: the (colour, shape)-uniqueness of every
frozen case, scanned and pinned, so E2's preregistration can cite a suite-enforced
precondition instead of a one-off reading.
"""
from __future__ import annotations

import json
import os

import pytest

from embodied_agent.core.contracts import EntityState, WorldState
from embodied_agent.perception.grounding import (
    CONFLICTING,
    GROUNDED,
    GROUNDING,
    GroundingMap,
    SEEN_AS,
    UNGROUNDED,
    ground_state,
)

#: the shape of `lh_c3_capacity_and_shift`'s declaration that killed E2's vlm/stub cells
C3_OBJECTS = [
    {"entity_id": "obj_yellow_1", "attributes": {"color": "yellow"}, "shape": "cube"},
    {"entity_id": "obj_purple_2", "attributes": {"color": "purple"}, "shape": "cube"},
    {"entity_id": "obj_red_3", "attributes": {"color": "red"}, "shape": "cuboid"},
    {"entity_id": "obj_green_4", "attributes": {"color": "green"}, "shape": "cylinder"},
    {"entity_id": "obj_blue_5", "attributes": {"color": "blue"}, "shape": "cube"},
    {"entity_id": "obj_yellow_6", "attributes": {"color": "yellow"}, "shape": "cuboid"},
]


def _detection(entity_id: str, colour: str, shape: str) -> EntityState:
    return EntityState(entity_id=entity_id, attributes={"color": colour, "shape": shape})


def _ground(gmap: GroundingMap, *detections: EntityState) -> WorldState:
    return ground_state(WorldState(state_version=1, sim_time=0.0, wall_time=0.0,
                                   entities=list(detections), targets=[]), gmap)


def test_a_duplicate_colour_with_distinct_shapes_builds_a_bridge():
    """The E2 blocker, at the declaration half: two yellow bodies, a cube and a cuboid,
    must stop being a refusal."""
    gmap = GroundingMap.from_objects(C3_OBJECTS)
    assert gmap.bind("yellow") is None, "an ambiguous colour must not silently pick one"
    assert gmap.bind("yellow", "cube") == "obj_yellow_1"
    assert gmap.bind("yellow", "cuboid") == "obj_yellow_6"
    assert gmap.bind("red") == "obj_red_3", "unique colours keep their colour key"


def test_a_duplicate_colour_without_a_usable_shape_stays_ungrounded():
    """An `unknown`-shape detection in an ambiguous scene names no declared body: it
    keeps its frame name and says so, instead of grounding to whichever yellow the
    lookup happened to return."""
    gmap = GroundingMap.from_objects(C3_OBJECTS)
    state = _ground(gmap, _detection("seen:yellow", "yellow", "unknown"))
    (yellow,) = state.entities
    assert yellow.entity_id == "seen:yellow"
    assert yellow.attributes[GROUNDING] == UNGROUNDED
    assert yellow.attributes[SEEN_AS] == "seen:yellow"


def test_detections_bind_through_the_composite_key_and_record_which_key_spoke():
    """The frame half: two yellow detections, shapes read off the pixels, land on the
    two distinct declared bodies — and each renamed entity records that the composite
    key, not the colour word alone, did the binding."""
    gmap = GroundingMap.from_objects(C3_OBJECTS)
    state = _ground(gmap,
                    _detection("seen:yellow", "yellow", "cube"),
                    _detection("seen:yellow", "yellow", "cuboid"),
                    _detection("seen:red", "red", "cuboid"))
    by_id = {e.entity_id: e for e in state.entities}
    assert by_id["obj_yellow_1"].attributes[SEEN_AS] == "seen:yellow"
    assert by_id["obj_yellow_6"].attributes[SEEN_AS] == "seen:yellow"
    assert by_id["obj_yellow_1"].attributes[GROUNDING] == GROUNDED
    assert by_id["obj_yellow_1"].attributes["grounding_key"] == "colour_shape"
    assert by_id["obj_red_3"].attributes["grounding_key"] == "colour"


def test_two_detections_claiming_one_composite_body_stay_conflicting():
    """The one-claim-per-body rule survives the second key: two cubes claiming yellow
    are conflicting evidence about an unmoved table, so neither gets a task name."""
    gmap = GroundingMap.from_objects(C3_OBJECTS)
    state = _ground(gmap,
                    _detection("seen:yellow", "yellow", "cube"),
                    _detection("seen:yellow", "yellow", "cube"))
    assert len(state.entities) == 2, "both detections stay in the picture"
    for yellow in state.entities:
        assert yellow.attributes[GROUNDING] == CONFLICTING
        assert yellow.entity_id == "seen:yellow"


def test_a_composite_collision_is_still_refused():
    """The refusal narrows; it does not disappear. Two declared yellow cubes would leave
    the bridge a function of nothing the frame can name."""
    with pytest.raises(ValueError, match="yellow.*cube"):
        GroundingMap.from_objects([
            {"entity_id": "a", "attributes": {"color": "yellow"}, "shape": "cube"},
            {"entity_id": "b", "attributes": {"color": "yellow"}, "shape": "cube"},
        ])


def test_a_declared_ambiguous_colour_with_no_shape_is_refused():
    """A declaration that names two yellow bodies but no shapes asks the bridge to
    split a colour it cannot split: that is the c3 refusal, narrowed to the case where
    the declaration itself carries no second key."""
    with pytest.raises(ValueError, match="shape"):
        GroundingMap.from_objects([
            {"entity_id": "a", "attributes": {"color": "yellow"}},
            {"entity_id": "b", "attributes": {"color": "yellow"}},
        ])


def test_colour_unique_declarations_bind_exactly_as_before():
    """Degradation rule 1, the no-regression half: for a colour-unique case the map's
    colour table, its bindings with and without a (mis)read shape, and its digest are
    all byte-identical to today's — the 47 frozen clean/protocol cases and every v0.3
    manifest digest must not move."""
    objects = C3_OBJECTS[:4]  # four distinct colours: the colour-unique shape of things
    gmap = GroundingMap.from_objects(objects)
    legacy = GroundingMap({str(o["attributes"]["color"]): o["entity_id"] for o in objects})
    assert gmap.by_colour == legacy.by_colour
    assert gmap.sha256() == legacy.sha256(), "a colour-unique map keeps its v0.3 digest"
    assert gmap.bind("yellow") == "obj_yellow_1"
    assert gmap.bind("yellow", "sphere") == "obj_yellow_1", \
        "a unique colour does not let a misread shape override the bridge"
    assert all(v == "colour" for v in gmap.summary()["key_of"].values())


def test_the_summary_records_which_key_each_body_needs():
    """`map_sha256` goes into the manifest, so the scheme change must be readable from
    the manifest too: per declared body, whether the colour word alone was a function."""
    gmap = GroundingMap.from_objects(C3_OBJECTS)
    key_of = gmap.summary()["key_of"]
    assert key_of == {"obj_yellow_1": "colour_shape", "obj_purple_2": "colour",
                      "obj_red_3": "colour", "obj_green_4": "colour",
                      "obj_blue_5": "colour", "obj_yellow_6": "colour_shape"}


def _frozen_cases():
    """Every case declaration the three frozen task tables ship, with the file it came
    from — the same tables `freeze --check` pins."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "experiment")
    out = []
    for name in ("frozen_tasks_v1.json", "frozen_episodic_v1.json",
                 "frozen_long_horizon_v1.json"):
        with open(os.path.join(root, name), encoding="utf-8") as f:
            d = json.load(f)
        sets = d.get("sets") or {}
        for set_name, s in sets.items():
            for case in (s or []):
                out.append((name, set_name, case))
        for case in d.get("cases") or []:
            out.append((name, "-", case))
    return out


def test_every_frozen_case_is_groundable_under_the_composite_key():
    """§18.3's instrument, as a suite-enforced precondition: for every case in every
    frozen table, (colour, shape) is a function — so E2's preregistration cites a
    property the suite re-checks on every run, and `lh_c6_two_disruptions` (the second
    duplicate-colour case E2 never reached) is covered by the same sentence."""
    duplicates = []
    for name, set_name, case in _frozen_cases():
        seen: dict[tuple[str, str], str] = {}
        for o in case.get("objects") or []:
            colour = str((o.get("attributes") or {}).get("color") or "").strip().lower()
            shape = str(o.get("shape") or "").strip().lower()
            if not colour:
                continue
            key = (colour, shape)
            if key in seen and seen[key] != o["entity_id"]:
                duplicates.append((name, set_name, case["task_id"], key))
            seen.setdefault(key, o["entity_id"])
    assert duplicates == [], \
        f"a frozen case would still refuse on the composite key: {duplicates}"
