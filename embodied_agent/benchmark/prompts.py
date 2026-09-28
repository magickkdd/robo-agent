"""What the model is asked, and what it is shown, in a text world (SPEC-BST 4, 5.3).

The decision *mechanism* is v0.1's and is unchanged: one JSON decision per round, four
mutually exclusive actions, one skill at most. What differs is the payload, and the
difference is the whole point of this module:

* the raw public sentence is shown, in full, beside the facts read out of it, so an
  unparsable remainder is visible instead of silently absent (SPEC-BST 4.1, 4.4);
* no geometry appears anywhere, because none was measured — an entity has a name and
  the attributes this snapshot stated, not a position (SPEC-BST 4.2);
* `progress` is a single self-describing `unknown`, so the model cannot read a score
  through the back door; the instruction it has to satisfy is the sentence itself;
* no candidate list: this backend has no slot geometry to offer (recorded as a
  disclosed difference from the desktop mechanism, SPEC-BST 4.5).

The prompt states the environment's own rules (one command per skill, the engine
answers, "Nothing happens." means it did not) and nothing about how to solve a task:
no example plans, no admissible-command hints, no task-type labels (SPEC-BST 4.5).
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..core.contracts import DecisionContext

BST_DECISION_SYSTEM = """你是具身文字世界机器人（ALFWorld 家居文本环境）的决策器。每一轮你会收到
一个 JSON 上下文，必须只输出一个 JSON 决策对象。

输出结构（只允许这些键）：
{"action":"execute"|"finish"|"blocked"|"clarify",
 "execute":{"skill":"<目录中的技能名>",
            "args":{"object_id":"mug 2","target_id":"cabinet 5"},
            "candidate_id":null,
            "subgoal":"可选的简短子目标"},
 "missing_information":null,
 "evidence_refs":["obs_0007"],
 "rationale":"一句话依据（引用观察到的事实）",
 "expected_effect":"一句话预期"}

关于这个世界的既定事实：
- 每个技能恰好对应一条原生命令；一轮最多提交一个技能。环境会把它自己的原话返回给你，
  那句原话就是行动的结果，不要假设发生了文本没有说的事情；
- 文本里出现的名字（例如 "cabinet 5"、"mug 2"）是唯一合法的引用方式。object_id 与
  target_id 必须逐字使用某个观察文本或近期反馈里环境亲自写过的名字；不要发明名字、
  不要改数字、不要只写 "the mug"；
- 环境只在你已经到达某个容器时才对它执行 take/open/move 等命令。是否需要先移动或先打开，
  由你根据文本自己判断并分轮执行；系统不会替你补任何动作；
- "Nothing happens." 表示这条命令没有产生效果（可能因为对象不在那里、容器关着、或你不在
  那里）；这句原话会出现在反馈里，它本身就是新信息；
- 每轮的 world 只包含**本轮这段文本明确报告**的事实：上一轮在抽屉里看到的东西，如果这一轮
  没有再报告，就既不在也不不在——需要时你自己用观察类技能去确认。更早的原话保留在
  recent_feedbacks 里，并带着它当时的观察引用；
- progress 恒为 unknown：这个后端没有能判断任务句子是否完成的外部验证器。是否完成由你
  依据环境原话决定；不要把 progress 当成答案，也不要把它当成失败；
- 任务指令是原始英文句子，逐字保留在 task.utterance 与 goal.instruction 中，没有被翻译、
  改写、拆解成子目标或补齐成计划；
- action=finish 会立即结束本集并按官方结果评分，之后你不会再得到任何反馈；
  action=blocked / clarify 同样立即结束本集（没有交互澄清），blocked 不等于任务不可解；
- 没有坐标、没有候选方案、没有合法动作列表、没有奖励或成功标识：这些在这个后端不存在，
  candidate_id 一律为 null；
- 不要输出 context_id、goal_ref、based_on_state_version、decision_id、schema_version，
  这些由调用方按你收到的上下文盖章。"""

BST_DECISION_USER = """下面是本轮决策上下文（JSON）。请按 system 中的结构只输出一个决策对象。

"""

BANNED_IN_PAYLOAD = ("admissible_commands", "expert_plan", "task_type", "reward",
                     "won", "score", "split", "solution")


def prompt_pair() -> tuple[str, str]:
    return BST_DECISION_SYSTEM, BST_DECISION_USER


def prompts_sha256() -> str:
    """Identity of the text prompt actually sent, hashed the same way the desktop
    prompts are: system + user as one string (SPEC-BST 5.5)."""
    return hashlib.sha256((BST_DECISION_SYSTEM + BST_DECISION_USER).encode("utf-8")).hexdigest()


class TextDecisionContext(DecisionContext):
    """The same context object the frozen loop builds, rendered without geometry.

    Only `model_payload` differs from the desktop class: what the loop records, what
    the runtime validates and how the budget is charged are the same mechanism, so a
    benchmark result is about the decision loop rather than about a different view
    of it (SPEC-BST 3.2)."""

    def model_payload(self, feedback_depth: int = 3) -> dict[str, Any]:
        world = self.world
        return {
            "schema_version": self.schema_version,
            "episode_id": self.episode_id,
            "context_id": self.context_id,
            "round_index": self.round_index,
            "state_version": self.state_version,
            "observation_ref": self.observation_ref,
            "task": {"utterance": self.task.utterance,
                     "declared_constraints": list(self.task.declared_constraints)},
            "goal": {"goal_id": self.goal.goal_id,
                     "instruction": getattr(self.goal, "instruction", None),
                     "assignments": [],
                     "assignments_note": "not applicable to this backend; the instruction "
                                         "above is the whole goal",
                     "prohibitions": list(self.goal.prohibitions)},
            "progress": [
                {"entity_id": p.entity_id, "target_id": p.target_id, "value": p.value.value,
                 "unmeasured": list(p.unmeasured), "predicate_id": p.predicate_id}
                for p in self.progress],
            "observation": {
                "observation_ref": world.observation_ref,
                "source": world.source.value,
                "text": world.raw_observation,
                "parse_status": world.parse_status,
                "unparsed": list(world.unparsed),
            },
            "world": {
                "location": world.location,
                "held_object": world.held_object,
                "entities": [
                    {"entity_id": e.entity_id, "attributes": dict(e.attributes),
                     "held": e.held, "supported_by": e.supported_by,
                     "source": e.source.value}
                    for e in world.entities],
                "targets": [],
                "targets_note": "a text world names containers and surfaces in sentences; "
                                "there is no geometric target region to list",
                "facts": [
                    {"fact_id": f.fact_id, "kind": f.kind, "subject": f.subject,
                     "related": f.related, "state": f.state,
                     "observation_ref": f.observation_ref, "text": f.text}
                    for f in world.facts],
                "facts_note": "only what this snapshot's text stated; earlier statements "
                              "live in recent_feedbacks with their own refs",
            },
            "skills": self.skill_catalogue,
            "candidates": [],
            "candidates_note": "no dynamic candidate list is offered by this backend",
            "candidates_truncated": 0,
            "last_feedback": self.last_feedback.short() if self.last_feedback else None,
            "recent_feedbacks": [f.short() for f in self.recent_feedbacks[-feedback_depth:]],
            "attempts": [a.model_dump() for a in self.attempts],
            "current_subgoal": self.current_subgoal,
            "todo_summary": self.todo_summary,
            "budget": dict(self.budget),
        }
