"""Task interpreter: natural language -> GoalSpec (spec 4.2, 7).

Fixes the prototype defect "parser guesses id mapping from co-occurrence":
binding is attribute-unique (a colour/shape attribute must match exactly one
visible entity) or explicit logical id. "剩下的/其余" binds all unassigned
entities. Unresolvable references make the goal ambiguous -> needs_clarification
instead of a guessed assignment (spec 4.2).
"""
from __future__ import annotations

import re

from .contracts import EntityRef, GoalAssignment, GoalSpec, TaskInput, WorldState

TARGET_ALIASES = {
    "左边": "tray_left",
    "左侧": "tray_left",
    "左边托盘": "tray_left",
    "中间": "tray_middle",
    "中间托盘": "tray_middle",
    "中间的托盘": "tray_middle",
    "右边": "tray_right",
    "右侧": "tray_right",
    "右边托盘": "tray_right",
    "left": "tray_left",
    "middle": "tray_middle",
    "right": "tray_right",
}

SHAPE_WORDS = {"方块": "cube", "正方体": "cube", "长方体": "cuboid", "圆柱": "cylinder", "圆柱体": "cylinder",
               "cube": "cube", "cuboid": "cuboid", "cylinder": "cylinder"}
COLOR_WORDS = {"红色": "red", "红的": "red", "red": "red",
               "蓝色": "blue", "蓝的": "blue", "blue": "blue",
               "绿色": "green", "绿的": "green", "green": "green",
               "黄色": "yellow", "黄的": "yellow", "yellow": "yellow",
               "紫色": "purple", "紫的": "purple", "purple": "purple"}


def _match_entity(text: str, world: WorldState) -> tuple[EntityRef | None, str | None]:
    """Return (entity_ref, error). error != None means ambiguous/unknown."""
    text = text.strip()
    attrs: dict[str, str] = {}
    for w, v in sorted({**SHAPE_WORDS, **COLOR_WORDS}.items(), key=lambda kv: -len(kv[0])):
        if w in text:
            attrs["shape" if w in SHAPE_WORDS else "color"] = v
    explicit = re.findall(r"obj_[a-z_0-9]+", text)
    if explicit:
        ids = [e for e in world.entities if e.entity_id == explicit[0]]
        return (EntityRef(entity_id=explicit[0]), None) if ids else (None, f"unknown entity {explicit[0]}")
    if not attrs:
        return None, "no usable attribute in reference"
    matches = [e for e in world.entities if all(e.attributes.get(k) == v for k, v in attrs.items())]
    if len(matches) == 1:
        return EntityRef(attributes=attrs), None
    if len(matches) > 1:
        return None, f"attribute {attrs} matches {len(matches)} entities"
    return None, f"no entity matches {attrs}"

# order matters: longer phrases first
_CLAUSE_SPLIT = re.compile(r"[,，;；。、]|然后|再|并且|全部|都")


def _match_target(text: str) -> str | None:
    text = text.strip()
    for k in sorted(TARGET_ALIASES, key=len, reverse=True):
        if k in text:
            return TARGET_ALIASES[k]
    return None


def interpret(task: TaskInput, world: WorldState) -> GoalSpec:
    """Rule interpreter over the declared S1 utterance family. It is explicitly
    a template parser, not a general Chinese NL understanding module."""
    clauses = [c for c in _CLAUSE_SPLIT.split(task.utterance) if c.strip()]
    assignments: list[GoalAssignment] = []
    problems: list[str] = []
    pending: list[str] = []  # entity mentions waiting for a verb clause

    def bind(ent_text: str, tgt: str):
        ref, err = _match_entity(ent_text, world)
        if err:
            problems.append(f"'{ent_text}': {err}")
            return
        assignments.append(GoalAssignment(entity=ref, target_id=tgt))

    for clause in clauses:
        if not clause.strip():
            continue
        if re.search(r"剩下的|其余|others|the rest", clause):
            tgt = _match_target(clause)
            if tgt is None:
                problems.append(f"clause '{clause}': no target")
                continue
            if not any(a.entity.selector == "rest" for a in assignments):
                assignments.append(GoalAssignment(entity=EntityRef(selector="rest"), target_id=tgt))
            continue
        m = re.search(r"(?:(把|将)?\s*([^放扔给]+?)\s*)?(放入|放进|放到|放在|置于|place\s+\w+\s+(?:into|in|on))\s*(.+)", clause)
        if not m:
            txt = clause.strip()
            if _match_target(txt) is None and any(w in txt for w in {**SHAPE_WORDS, **COLOR_WORDS}):
                pending.append(txt)  # "A、B、C 全部放入T"
                continue
            problems.append(f"clause '{clause}': unrecognized")
            continue
        ent_text, tgt_text = m.group(2), m.group(4)
        tgt = _match_target(tgt_text)
        if tgt is None:
            problems.append(f"clause '{clause}': unknown target '{tgt_text}'")
            continue
        texts = pending + ([ent_text] if ent_text and ent_text.strip() else [])
        pending = []
        if not texts:
            problems.append(f"clause '{clause}': no entity to bind")
            continue
        for t in texts:
            if t.strip():
                bind(t.strip(), tgt)
    if pending:
        problems.append("entity mentions without a target")

    assignments = _expand_rest(assignments, world)
    # duplicate bindings of the same entity to different targets are
    # contradictory: the first valid binding wins, later ones are no-ops
    seen: set[str] = set()
    deduped = []
    for a in assignments:
        key = a.entity.entity_id or (
            "attr:" + ",".join(f"{k}={v}" for k, v in sorted(a.entity.attributes.items()))
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    assignments = deduped
    ambiguous = bool(problems) or not assignments
    return GoalSpec(
        assignments=assignments,
        ambiguous=ambiguous,
        clarification_request="; ".join(problems) if problems else None,
        prohibitions=["no stacking", "no throwing"],
    )


def _ref_matches_entity(ref: EntityRef, entity) -> bool:
    if ref.entity_id:
        return ref.entity_id == entity.entity_id
    return bool(ref.attributes) and all(entity.attributes.get(k) == v for k, v in ref.attributes.items())


def _expand_rest(assignments: list[GoalAssignment], world: WorldState) -> list[GoalAssignment]:
    """Expand one 'rest' placeholder into all entities not otherwise bound."""
    if not any(a.entity.selector == "rest" for a in assignments):
        return assignments
    concrete = [a for a in assignments if a.entity.selector != "rest"]
    bound_ids = set()
    for a in concrete:
        for e in world.entities:
            if _ref_matches_entity(a.entity, e):
                bound_ids.add(e.entity_id)
    rest_target = next(a.target_id for a in assignments if a.entity.selector == "rest")
    rest_assignments = [
        GoalAssignment(entity=EntityRef(entity_id=e.entity_id), target_id=rest_target)
        for e in world.entities
        if e.entity_id not in bound_ids
    ]
    return concrete + rest_assignments
