"""The cell's static calibration, handed *into* perception (SPEC-v0.2 §5.1).

This file exists so the information-flow rule of §3.3 has a single, boring answer: the
manual a perceiver is allowed to consult is assembled here, from the task list's own
declarations, and passed in. `embodied_agent/perception` imports no scene and reads no
state — which `tests/contract/test_v02_perception_sensor.py::
test_the_perception_package_reads_no_privileged_state` checks against the source text —
so the numbers have to arrive from a caller that may legitimately know them.

What counts as calibration is decided by one question: *is this the same in every
episode?* Colours, shape dimensions, tray positions and the measured slot capacity are
(checked over the frozen set), so a perceiver may hold them. Which object is where,
what is held, what has moved and what the goal is are not, and appear nowhere here.

`grey` is excluded from the palette on purpose: it is the table and the robot, not an
object. A segmenter keyed on it would return one enormous "detection" covering the
working surface, which is a good way to fail a test and a bad way to see a cell.
"""
from __future__ import annotations

from ..core.scene import TABLE_TOP_Z
from ..perception.catalog import PerceptionCatalog
from .tasks import (COLOR_ZH, COLORS, DIMS, MAX_OBJECTS_PER_REGION, PALETTE, SHAPE_ZH,
                    SHAPES, TARGET_ZH)

CATALOG_VERSION = "calib-v1"


def cell_catalog(trays, *, version: str = CATALOG_VERSION) -> PerceptionCatalog:
    """The manual for this cell, from the declared palette, dimensions and trays.

    `trays` is the sequence of static tray dictionaries the physics scene builds with
    (`scene.trays.values()`): positions, wall heights and the measured interior half
    width. They are identical for every case and every seed, and a test says so —
    `test_the_tray_declaration_is_the_same_in_every_case` — because if a tray moved
    between episodes, handing its position to a perceiver would be smuggling an answer
    in as a manual."""
    return PerceptionCatalog.from_declared(
        palette={name: rgba for name, rgba in PALETTE.items() if name in COLORS},
        dims={name: DIMS[name] for name in SHAPES},
        trays=list(trays), table_top_z=TABLE_TOP_Z, labels=TARGET_ZH,
        colour_words=COLOR_ZH, shape_words=SHAPE_ZH,
        capacity=MAX_OBJECTS_PER_REGION, version=version)


def task_context_of(case) -> dict:
    """The mention-shaped half of a task: what a person asked about, in words.

    Only the utterance and the declared attribute words that appear inside it.
    `case.eval_spec`, the target assignment and the entity ids stay out of it — a
    percept that learned from them would be reading the answer key, and §5.1 forbids
    exactly that substitution.

    The items are named by colour alone where a colour is present, because that is the
    identifying attribute in this cell (colours never repeat within a case) and adding
    a shape word the utterance did not use would put a claim about the shape into the
    question the model is being asked."""
    mentioned = [name for name, zh in COLOR_ZH.items() if zh in case.utterance]
    if not mentioned:                       # an utterance that names bodies by id only
        mentioned = [str(o["attributes"].get("color")) for o in case.objects
                     if o.get("attributes")]
    shapes = {str(o["attributes"].get("color")): str(o["attributes"].get("shape"))
              for o in case.objects if o.get("attributes")}
    items = [f"{COLOR_ZH[c]}{SHAPE_ZH[shapes[c]]}" if shapes.get(c) in SHAPE_ZH
             else COLOR_ZH[c] for c in mentioned]
    regions = [TARGET_ZH[t] for t in TARGET_ZH if TARGET_ZH[t] in case.utterance]
    return {"utterance": case.utterance, "items": items, "regions": regions}
