"""What the model is shown, in a world that reports positions (SPEC-v0.2 §10).

Same discipline as the text channel's `prompts.py`, opposite in content: here geometry
*is* measured, so it is shown, with the source of every number named. Three things
are deliberately absent, and their absence is the module's claim:

* no `success`, no `reward`, no `obj_to_target` — the evaluator's numbers live in
  `env.eval_view()` and this payload is built from `WorldState`, which never saw them;
* no expert plan, no waypoint list, no "try aligning along x". The catalogue says what
  each verb physically does and what it costs; nothing says which verb this task wants;
* no admissible-action list. A continuous bench has one, and offering it would be the
  runtime choosing the move set for the round.

The prompt is a *new* constant in a *new* module. `adapters/deepseek.py`'s
`DECISION_SYSTEM`/`PLAN_SYSTEM` and `benchmark/prompts.py`'s text prompt are untouched,
so the frozen desktop and text prompt hashes still read what the v0.2 freeze recorded.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..core.contracts import DecisionContext

MW_DECISION_SYSTEM = """你是具身操作机器人（MetaWorld/MuJoCo Sawyer 台面）的决策器。每一轮你会收到一个
JSON 上下文，必须只输出一个 JSON 决策对象。

输出结构（只允许这些键）：
{"action":"execute"|"finish"|"blocked"|"clarify",
 "execute":{"skill":"observe"|"pick"|"place"|"safe_retreat",
            "args":{"object_id":"peg 1","target_id":"socket 1"},
            "candidate_id":null,
            "subgoal":"可选的简短子目标"},
 "missing_information":null,
 "evidence_refs":["obs_0007"],
 "rationale":"一句话依据（引用测到的坐标或事实）",
 "expected_effect":"一句话预期"}

关于这个世界的既定事实：
- 技能名只有四个，参数只能是**实体名**（形如 "peg 1"、"socket 1"），不能是坐标：坐标由
  传感器测出并写在 world.entities 里，你不需要也不应该发明数字；
- 一次 execute 只执行**一个**技能动作。系统不会替你补接近、对位、抬起或重试；需要分几步
  做完，由你分轮决定；
- 每个动作会消耗若干个 simulator step（台面总 horizon 有限），`observe` 不消耗；
  动作的**结果**不在动作返回里——它写在下一轮快照的测量坐标里；
- world 里的坐标是**这一轮**测到的。上一轮夹住的物体这一轮可能已经掉了：held 与位置都会
  如实重测，不会沿用；
- progress 是几何判断（物体中心到目标中心的距离 ≤ 目标 site 半径），它**不是**基准分数。
  基准是否给分由官方 evaluator 在episode 结束后单独读，你在这里看不到它；
- action=finish 立即结束本集；blocked / clarify 同样立即结束；
- 没有奖励、没有成功标志、没有专家轨迹、没有候选动作列表：candidate_id 恒为 null。"""

MW_DECISION_USER = """下面是本轮决策上下文（JSON）。请按 system 中的结构只输出一个决策对象。

"""

BANNED_IN_PAYLOAD = ("success", "reward", "official", "expert_plan", "admissible",
                     "solution", "obj_to_target", "grasp_success")

#: The name a `model_calls.jsonl` row and the manifest carry. A new constant in a new
#: module on purpose: the desktop `s2-decide-v1` and the text channel's
#: `bst-decide-text-v1` belong to those prompts, and filing this one under either name
#: would make two different questions look like the same one in a later aggregate.
PROMPT_VERSION = "mw-decide-v1"


def prompt_pair() -> tuple[str, str]:
    return MW_DECISION_SYSTEM, MW_DECISION_USER


def prompts_sha256() -> str:
    return hashlib.sha256((MW_DECISION_SYSTEM + MW_DECISION_USER).encode("utf-8")).hexdigest()


class MujocoDecisionContext(DecisionContext):
    """The frozen context object, rendered with measured geometry."""

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
                     "assignments": [{"entity_id": a.entity.entity_id, "target_id": a.target_id}
                                     for a in self.goal.assignments],
                     "prohibitions": list(self.goal.prohibitions)},
            "progress": [{"entity_id": p.entity_id, "target_id": p.target_id,
                          "value": p.value.value, "unmeasured": list(p.unmeasured),
                          "predicate_id": p.predicate_id} for p in self.progress],
            "progress_note": "geometric verdicts against the measured target-site radius; "
                             "not the benchmark score, which this payload never contains",
            "observation": {"observation_ref": world.observation_ref,
                            "source": world.source.value,
                            "text": world.raw_observation,
                            "parse_status": world.parse_status},
            "world": {
                "location": world.location, "held_object": world.held_object,
                "entities": [{"entity_id": e.entity_id,
                              "pose": ([e.pose.position.x, e.pose.position.y, e.pose.position.z]
                                       if e.pose else None),
                              "held": e.held, "supported_by": e.supported_by,
                              "attributes": dict(e.attributes), "source": e.source.value}
                             for e in world.entities],
                "targets": [{"target_id": t.target_id, "label": t.label,
                             "center": [t.center.x, t.center.y, t.center.z],
                             "inner_half_m": t.inner_half} for t in world.targets],
                "facts": [{"fact_id": f.fact_id, "kind": f.kind, "subject": f.subject,
                           "related": f.related, "state": f.state,
                           "observation_ref": f.observation_ref, "text": f.text}
                          for f in world.facts],
                "facts_note": "only what this snapshot measured; nothing is carried over from "
                              "an earlier round unless it is in recent_feedbacks",
            },
            "skills": self.skill_catalogue,
            "candidates": [], "candidates_note": "this backend offers no candidate registry",
            "candidates_truncated": 0,
            "last_feedback": self.last_feedback.short() if self.last_feedback else None,
            "recent_feedbacks": [f.short() for f in self.recent_feedbacks[-feedback_depth:]],
            "attempts": [a.model_dump() for a in self.attempts],
            "current_subgoal": self.current_subgoal,
            "todo_summary": self.todo_summary,
            "budget": dict(self.budget),
        }


__all__ = ["MujocoDecisionContext", "prompt_pair", "prompts_sha256", "BANNED_IN_PAYLOAD",
           "PROMPT_VERSION", "MW_DECISION_SYSTEM", "MW_DECISION_USER"]
