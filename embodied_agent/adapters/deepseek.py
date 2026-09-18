"""DeepSeek adapter (spec 10).

- Model name / endpoint / auth / temperature / max_tokens / timeout are config
  injected; nothing hardcodes a model id as a long-term contract.
- One structured request returns GoalSpec + Plan. Client-side structure and
  semantic validation are mandatory regardless of provider JSON mode (10.1).
- Failure policy (10.2): JSON parse -> at most 1 repair attempt; network
  timeout / rate limit -> at most 2 backoff retries; auth/config errors ->
  immediate report.
- FixtureLLM is an offline test double ONLY; online acceptance requires real
  calls with provider/model recorded (10.2).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from ..core.contracts import GoalSpec, Plan, TaskInput, WorldState
from ..core.interpreter import interpret
from ..core.planner import RulePlanner


class LLMError(Exception):
    pass


class DeepSeekAdapter:
    def __init__(self, api_key: str, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com",
                 temperature: float = 0.2, max_tokens: int = 1024, timeout_s: float = 30.0,
                 proxy: str | None = "__direct__"):
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY not set; online LLM acceptance requires a real key (spec 10.2)")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.last_usage: dict | None = None
        # proxy policy (spec 3.1: config-injected): "__direct__" bypasses the
        # environment's http_proxy/https_proxy entirely — a stale system proxy
        # would otherwise silently swallow every request as a timeout.
        # Set proxy to a URL string to force one, or None to honour the env.
        if proxy == "__direct__":
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif proxy:
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
        else:
            self.opener = urllib.request.build_opener()

    def chat_json(self, system: str, user: str) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        attempts = 0
        last_err: Exception | None = None
        while attempts < 3:  # initial + 2 backoff retries (10.2)
            try:
                with self.opener.open(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.last_usage = data.get("usage")
                content = data["choices"][0]["message"]["content"]
                return self._parse_with_repair(content)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise LLMError(f"auth/config error HTTP {e.code}: {e.read()[:200]}") from e
                if e.code in (408, 429, 500, 502, 503):
                    last_err = e
                else:
                    raise LLMError(f"HTTP {e.code}: {e.read()[:200]}") from e
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
                last_err = e
            attempts += 1
            time.sleep(1.5 * attempts)  # bounded backoff
        raise LLMError(f"request failed after {attempts} attempts: {last_err}")

    def _parse_with_repair(self, content: str) -> dict:
        """JSON parse with at most one repair attempt (spec 10.2)."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except json.JSONDecodeError as e:
                    raise LLMError(f"unparseable JSON after 1 repair attempt: {e}") from e
            raise LLMError("unparseable JSON after 1 repair attempt (no braces found)")


PLANNING_SYSTEM = """你是一个桌面整理机器人任务的规划器。给定:实体列表(逻辑ID、颜色、形状)、
目标区域标签(tray_left/tray_middle/tray_right)、技能目录(pick/place)。
只输出一个 JSON 对象,结构如下:
{"goal_spec": {"assignments": [{"entity": {"entity_id": "..."}, "target_id": "tray_left"}], "ambiguous": false,
  "clarification_request": null, "prohibitions": ["no stacking"]},
 "plan": {"schema_version": "1", "based_on_state_version": 0, "goal_ref": "g_llm",
  "steps": [{"id": "s1", "skill": "pick", "args": {"object_id": "..."}},
            {"id": "s2", "skill": "place", "args": {"object_id": "...", "target_id": "..."}}]}}
规则:任务含糊或引用不存在实体时,输出 assignments: [] 且 ambiguous: true 并写 clarification_request;
不要发明不存在的实体或目标;不要堆叠。"""


class DeepSeekPlanner:
    def __init__(self, adapter: DeepSeekAdapter, prompt_version: str = "s1-plan-v1"):
        self.adapter = adapter
        self.provider = "deepseek"
        self.model = adapter.model
        self.prompt_version = prompt_version

    @classmethod
    def from_env(cls, model_config_path: str | None = None) -> "DeepSeekPlanner":
        """Build from configs/models/deepseek.yaml + environment. Per spec 16:
        the YAML references the env-var NAME (api_key_env); the key itself never
        enters configs, snapshots, prompts or logs. A repo-local .env is loaded
        if present (development convenience), then os.environ."""
        import yaml

        cfg_path = model_config_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "configs", "models", "deepseek.yaml"
        )
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        key_env = cfg.get("api_key_env", "DEEPSEEK_API_KEY")
        api_key = os.environ.get(key_env, "")
        if not api_key:
            env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
            if os.path.exists(env_file):
                with open(env_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key_env}="):
                            api_key = line.split("=", 1)[1].strip()
                            break
        return cls(
            DeepSeekAdapter(
                api_key=api_key,
                model=os.environ.get("DEEPSEEK_MODEL", cfg.get("model", "deepseek-chat")),
                base_url=os.environ.get("DEEPSEEK_BASE_URL", cfg.get("base_url", "https://api.deepseek.com")),
                temperature=float(cfg.get("temperature", 0.2)),
                timeout_s=float(cfg.get("timeout_s", 60)),
                proxy=cfg.get("proxy", "__direct__"),
            ),
            prompt_version=cfg.get("prompt_version", "s1-plan-v1"),
        )

    def _state_summary(self, world: WorldState, pending: list | None = None,
                       failure: str | None = None, remaining_budget: str | None = None) -> str:
        ents = [
            {"entity_id": e.entity_id, "attributes": e.attributes} for e in world.entities
        ]
        tgts = [{"target_id": t.target_id} for t in world.targets]
        payload = {"entities": ents, "targets": tgts, "held_object": world.held_object}
        if pending is not None:
            payload["pending_goals"] = pending  # satisfied goals are already removed
        if failure:
            payload["failure_summary"] = failure
        if remaining_budget:
            payload["remaining_budget"] = remaining_budget
        return json.dumps(payload, ensure_ascii=False)

    def plan(self, utterance: str, world: WorldState, skill_catalogue: dict, replan: bool = False,
             failure: str | None = None, remaining_budget: str | None = None) -> dict:
        user = (
            f"{'剩余任务重规划' if replan else '任务'}: {utterance}\n"
            f"当前状态摘要: {self._state_summary(world, failure=failure, remaining_budget=remaining_budget)}\n"
            f"技能目录: {json.dumps(skill_catalogue, ensure_ascii=False)}\n"
            f"based_on_state_version 请填 {world.state_version}。"
        )
        raw = self.adapter.chat_json(PLANNING_SYSTEM, user)
        # semantic validation happens here AND again in the runtime PlanValidator
        goal = GoalSpec.model_validate(raw.get("goal_spec", {}))
        plan = Plan.model_validate(raw.get("plan", {}))
        plan.based_on_state_version = world.state_version
        plan.goal_ref = goal.goal_id
        return {"goal_spec": goal.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
                "raw": json.dumps(raw, ensure_ascii=False)[:2000],
                "provider": self.provider, "model": self.model,
                "prompt_version": self.prompt_version,
                "usage": self.adapter.last_usage}


class FixtureLLM:
    """Offline test double: deterministic goal+plan from the same utterance via
    the rule interpreter. Never used for online acceptance metrics."""

    provider = "fixture"
    model = "fixture-interpreter"

    def plan(self, utterance: str, world: WorldState, skill_catalogue: dict, replan: bool = False) -> dict:
        task = TaskInput(task_id="fixture", utterance=utterance)
        goal = interpret(task, world)

        def pending():
            # bind attribute refs to ids without a physics scene
            pairs = []
            bound = set()
            for a in goal.assignments:
                eid = a.entity.entity_id
                if eid is None:
                    for e in world.entities:
                        if e.entity_id not in bound and all(
                            e.attributes.get(k) == v for k, v in a.entity.attributes.items()
                        ):
                            eid = e.entity_id
                            break
                if eid and eid not in bound:
                    bound.add(eid)
                    pairs.append((eid, a.target_id))
            return pairs

        steps = []
        for i, (eid, tid) in enumerate(pending()):
            steps.append({"id": f"s{2 * i}", "skill": "pick", "args": {"object_id": eid}})
            steps.append({"id": f"s{2 * i + 1}", "skill": "place", "args": {"object_id": eid, "target_id": tid}})
        plan = {
            "schema_version": "1",
            "plan_id": "p_fixture",
            "based_on_state_version": world.state_version,
            "goal_ref": goal.goal_id,
            "steps": steps,
        }
        return {
            "goal_spec": goal.model_dump(mode="json"),
            "plan": plan,
            "raw": json.dumps({"goal_spec": goal.model_dump(mode="json"), "plan": plan}, ensure_ascii=False),
            "provider": self.provider,
            "model": self.model,
        }
