"""DeepSeek adapter (SPEC 8 module map, 10, 11.2).

Three model-facing operations over one HTTP client:

    parse_goal(task, world)  -> GoalSpec   initial task understanding, shared once
    decide(ctx)              -> Decision   mode B, one request per boundary
    plan_from_context(ctx)   -> Plan       mode A, one request at t0

Accounting rules this module exists to enforce:

* Every opened request — transport retry and JSON format repair included —
  increments `http_requests` *before* the outcome is known, so a retry cannot
  hide from the episode ledger (SPEC 6.2: 网络重试不能藏在请求适配器内部).
* The raw response text is returned in full. Cutting it to fit a log line
  destroys the evidence a rejection needs (SPEC 7).
* Structure is validated here (pydantic); semantics are never "fixed". A payload
  that is not a `Decision` raises `DecisionSchemaError` with the field-level
  reasons so the **Runtime** rejects it and charges the bounded semantic-repair
  budget (SPEC 6.1). The adapter does not repair a wrong reference into a
  plausible one, and it never chooses an action on the model's behalf.
* The API key lives in the environment (or a repo-local `.env`) only. It appears
  in no prompt, log record, manifest or exception message; provider error bodies
  are not read into messages at all.
* Model, endpoint, sampling parameters, prompt versions and budgets are config
  injected; no model id is a long-term contract.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from pydantic import ValidationError

from ..core.contracts import (
    Decision,
    DecisionContext,
    DecisionSchemaError,
    GoalSpec,
    Plan,
    TaskInput,
    WorldState,
)
from ..core.interpreter import interpret
from ..core.planner import RulePlanner


class LLMError(Exception):
    """No usable response was produced (transport, API or unparseable output).

    `requests_made` is how many HTTP calls the attempt cycle opened, so a caller
    that accounts per call still sees the retries."""

    def __init__(self, message: str, *, requests_made: int = 0, meta: dict | None = None):
        super().__init__(message)
        self.requests_made = requests_made
        self.meta = meta or {}


# --------------------------------------------------------------- HTTP client --


class DeepSeekAdapter:
    def __init__(self, api_key: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com", temperature: float = 0.2,
                 max_tokens: int = 1200, timeout_s: float = 60.0, max_retries: int = 2,
                 proxy: str | None = "__direct__", pricing_usd_per_mtok: dict | None = None):
        if not api_key:
            raise LLMError("no API key in the environment: online model acceptance requires a real "
                           "key (SPEC 10: fixture doubles are offline-only)")
        self.api_key = api_key
        self.provider = "deepseek"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.pricing = pricing_usd_per_mtok or {}
        # cumulative for the lifetime of this object; the ledger charges deltas
        self.http_requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.transport_retries = 0
        self.format_repairs = 0
        self.api_errors = 0
        # "__direct__" bypasses a stale system proxy that would otherwise swallow
        # every request as a timeout; a URL forces one; None honours the env.
        if proxy == "__direct__":
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif proxy:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
        else:
            self.opener = urllib.request.build_opener()
        self.proxy_kind = ("direct" if proxy == "__direct__"
                           else "configured" if proxy else "from_environment")

    @property
    def sampling(self) -> dict:
        """The numbers every request in this batch carried, for the run manifest
        (SPEC 7: 模型配置与提示版本入清单).

        Stated here rather than copied from the config file because the config is a
        request to the code: what a frozen experiment has to record is what was sent.
        The proxy is recorded as its kind only — a proxy URL can carry credentials."""
        return {"model": self.model, "base_url": self.base_url,
                "temperature": self.temperature, "max_tokens": self.max_tokens,
                "response_format": "json_object", "stream": False,
                "timeout_s": self.timeout_s, "max_retries": self.max_retries,
                "proxy": self.proxy_kind}

    # ---------- one request ----------
    def _post(self, body: dict) -> tuple[dict, int]:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        attempts, last_err = 0, None
        while attempts <= self.max_retries:
            attempts += 1
            self.http_requests += 1
            try:
                with self.opener.open(req, timeout=self.timeout_s) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                usage = payload.get("usage") or {}
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)
                return payload, attempts
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    self.api_errors += 1
                    raise LLMError(f"auth/config error HTTP {e.code} (body not read)") from None
                if e.code not in (408, 429, 500, 502, 503, 504):
                    self.api_errors += 1
                    raise LLMError(f"HTTP {e.code} (body not read)") from None
                last_err = e
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                last_err = e
            if attempts <= self.max_retries:
                self.transport_retries += 1
                time.sleep(1.5 * attempts)
        self.api_errors += 1
        raise LLMError(f"request failed after {attempts} attempts: {type(last_err).__name__}: "
                       f"{last_err}", requests_made=attempts) from last_err

    def chat(self, system: str, user: str, *, max_tokens: int | None = None) -> tuple[str, dict]:
        """One logical completion request -> raw text + provider metadata."""
        t0 = time.time()
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        payload, attempts = self._post(body)
        choice = (payload.get("choices") or [{}])[0]
        raw = (choice.get("message") or {}).get("content") or ""
        return raw, {
            "provider": self.provider,
            "requested_model": self.model,
            "returned_model": payload.get("model"),
            "completion_id": payload.get("id"),
            "created": payload.get("created"),
            "finish_reason": choice.get("finish_reason"),
            "usage": payload.get("usage"),
            "temperature": self.temperature,
            "max_tokens": body["max_tokens"],
            "http_requests_total": self.http_requests,
            "transport_attempts": attempts,
            "transport_retries": max(0, attempts - 1),
            "latency_s": round(time.time() - t0, 3),
            "prompt_chars": len(system) + len(user),
            "raw_chars": len(raw),
        }

    def chat_json(self, system: str, user: str, *, kind: str, prompt_version: str,
                  max_tokens: int | None = None):
        """Request + parse, with at most one *format* repair request.

        The repair re-asks the model for the same object; nothing here edits a
        payload towards validity."""
        raw, meta = self.chat(system, user, max_tokens=max_tokens)
        meta = {**meta, "kind": kind, "prompt_version": prompt_version, "raw_response": raw}
        parsed, err = _loads_lenient(raw)
        if err is None:
            return parsed, meta
        self.format_repairs += 1
        raw2, meta2 = self.chat(system, _repair_prompt(user, raw, err), max_tokens=max_tokens)
        meta2 = {**meta2, "kind": f"{kind}_format_repair", "prompt_version": prompt_version,
                 "raw_response": raw2, "first_parse_error": str(err), "repaired_from": raw}
        parsed2, err2 = _loads_lenient(raw2)
        if err2 is None:
            return parsed2, meta2
        meta2["parse_error"] = str(err2)
        raise LLMError(f"unparseable JSON after 1 format repair: {err2}",
                       requests_made=meta["transport_attempts"] + meta2["transport_attempts"],
                       meta=meta2)

    def cost_estimate(self) -> float | None:
        """Money is reported only when pricing is configured; never guessed."""
        if not self.pricing:
            return None
        pin = float(self.pricing.get("input", 0.0))
        pout = float(self.pricing.get("output", 0.0))
        return round(self.prompt_tokens / 1e6 * pin + self.completion_tokens / 1e6 * pout, 6)


def _loads_lenient(raw: str):
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        first, last = raw.find("{"), raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first: last + 1]), None
            except json.JSONDecodeError as e2:
                return None, e2
        return None, e


def _repair_prompt(user: str, raw: str, err: Exception) -> str:
    return (
        f"{user}\n\n上一次输出无法解析为 JSON（{err}）。"
        "请只输出一个符合该结构的合法 JSON 对象，不要添加解释、Markdown 代码块或注释。"
        f"\n需要修正的输出如下：\n{raw[:4000]}"
    )


# ----------------------------------------------------------------- prompts ---

GOAL_SYSTEM = """你是桌面整理机器人的任务理解器，把用户原话解析成目标指派。

只输出一个 JSON 对象：
{"assignments":[{"entity":{"entity_id":"obj_red_1"},"target_id":"tray_left"}],
 "ambiguous":false,"clarification_request":null,
 "prohibitions":["no stacking","no throwing"]}

规则：
- entity 可以用明确的 entity_id，或者仅用 attributes（color/shape）当且仅当该属性组合在
  当前可见实体里恰好匹配一个对象；
- target_id 必须是"可用目标区域"里存在的 id；
- 只要有任何一项引用无法唯一确定（不存在、匹配 0 个、匹配多个、区域不存在、原话没有给出
  可绑定的指派），就把 ambiguous 置为 true，并在 clarification_request 里写清缺什么；
  不要用猜测补齐剩下的对象，也不要把无法确定的对象悄悄丢掉还宣称明确；
- 只做对象到区域的语言绑定，不要生成动作、顺序或技能；
- 不要发明实体或区域。"""

DECISION_SYSTEM = """你是具身桌面整理机器人的决策器。每一轮你会收到一个 JSON 上下文，
必须只输出一个 JSON 决策对象。

输出结构（只允许这些键）：
{"action":"execute"|"finish"|"blocked"|"clarify",
 "execute":{"skill":"pick"|"place"|"observe"|"safe_retreat",
            "args":{"object_id":"...","target_id":"..."},
            "candidate_id":null,
            "subgoal":"可选的简短子目标"},
 "missing_information":null,
 "evidence_refs":["obs_0007"],
 "rationale":"一句话依据（引用观察到的事实）",
 "expected_effect":"一句话预期"}

规则：
- 四种 action 互斥；action=execute 时必须给 execute，其它三种必须不给 execute；
- 一轮最多提交一个技能动作；finish / blocked / clarify 不是技能；
- object_id 只能取 context.world.entities 里的 entity_id，target_id 只能取
  context.world.targets 里的 target_id；不要发明 id；
- place 可以选择 context.candidates 中的 candidate_id（几何方案引用），也可以只给
  target_id，由执行期各组相同的解析规则选槽位；引用不存在或已失效的候选会被拒绝；
- 前置条件由 world 决定：held_object 已知为 X 时 place X 合法、pick 别的对象不合法；
  held_object 为 unknown 时不得盲目 pick/place，应先 observe 或 safe_retreat 取得新测量；
- 目标进展 progress 里 value=false 或 unknown 的 assignment 都算未完成，包括之前已完成
  后来被环境扰动的项；全部为 true 时才 finish；
- last_feedback / recent_feedbacks / attempts 是事实记录，用来改变你自己的下一步；
  Runtime 不会替你换方案或重试；
- action=clarify 必须写明缺少或冲突的用户信息；
- 不要输出 context_id、goal_ref、based_on_state_version、schema_version，这些由调用方
  按你收到的上下文盖章。"""

PLAN_SYSTEM = """你是具身桌面整理机器人的一次性规划器。给你一个 JSON 上下文（任务、目标、
当前世界状态、候选放置方案、技能目录、预算），输出**一份完整计划**，之后不会再向你询问。

只输出一个 JSON 对象：
{"steps":[{"id":"s0","skill":"pick","args":{"object_id":"obj_red_1"}},
          {"id":"s1","skill":"place","args":{"object_id":"obj_red_1","target_id":"tray_left"}}],
 "clarification_request":null}

规则：
- 每个未完成 assignment 恰好一个 pick 后跟一个 place；只允许 pick/place 两种技能；
- step id 唯一且按执行顺序给出；
- object_id / target_id 必须来自上下文里实际存在的 id；不要发明实体或区域；
- 不要写 candidate_id 或槽位坐标：你无法预知释放时哪个空位还在，槽位由执行期各组相同的
  解析规则决定；
- 存在多个合法顺序时选一个并坚持它，你不能在执行中修改这份计划；
- 如果有任何目标项无法用现有技能表达，steps 给 []，并写 clarification_request。"""

DECISION_USER = """下面是本轮决策上下文（JSON）。请按 system 中的结构只输出一个决策对象。

"""

PLAN_USER = """下面是一次性规划所需的上下文（JSON）。请按 system 中的结构只输出计划对象。

"""

GOAL_USER = """用户原话与场景如下（JSON）。请只输出目标指派对象。

"""


# ---------------------------------------------------------------- planner ---


class DeepSeekPlanner:
    """The online policy interface used by the runtime: goal parse, per-boundary
    decision, and the mode-A one-shot plan.

    It holds no loop state and no strategy: each method answers exactly one
    request about the context it was handed."""

    provider = "deepseek"

    def __init__(self, adapter: DeepSeekAdapter, *, goal_prompt: str = "s2-goal-v1",
                 decision_prompt: str = "s2-decide-v1", plan_prompt: str = "s2-plan-v1",
                 feedback_depth: int = 3):
        self.adapter = adapter
        self.model = adapter.model
        self.goal_prompt = goal_prompt
        self.decision_prompt = decision_prompt
        self.plan_prompt = plan_prompt
        self.feedback_depth = feedback_depth
        self.prompt_version = (f"goal={goal_prompt};decide={decision_prompt};plan={plan_prompt}")

    # ---------- cumulative accounting ----------
    @property
    def http_requests(self) -> int:
        return self.adapter.http_requests

    @property
    def prompt_tokens(self) -> int:
        return self.adapter.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.adapter.completion_tokens

    @property
    def api_errors(self) -> int:
        return self.adapter.api_errors

    @property
    def transport_retries(self) -> int:
        return self.adapter.transport_retries

    @property
    def format_repairs(self) -> int:
        return self.adapter.format_repairs

    @property
    def pricing(self) -> dict:
        return self.adapter.pricing

    def cost_estimate(self) -> float | None:
        return self.adapter.cost_estimate()

    def _snapshot(self, before: int) -> dict:
        return {"http_requests_this_call": self.adapter.http_requests - before,
                "cumulative_http_requests": self.adapter.http_requests}

    # ---------- 1. goal understanding ----------
    def parse_goal(self, task: TaskInput, world: WorldState) -> tuple[GoalSpec, dict]:
        payload = {
            "utterance": task.utterance,
            "declared_constraints": list(task.declared_constraints),
            "entities": [{"entity_id": e.entity_id, "attributes": e.attributes}
                         for e in world.entities],
            "targets": [{"target_id": t.target_id, "label": t.label} for t in world.targets],
        }
        before = self.adapter.http_requests
        raw, meta = self.adapter.chat_json(
            GOAL_SYSTEM, GOAL_USER + json.dumps(payload, ensure_ascii=False),
            kind="goal_parse", prompt_version=self.goal_prompt)
        goal = GoalSpec.model_validate(raw)
        goal, problems = _validate_goal(goal, world)
        goal.based_on_state_version = world.state_version
        meta = {**meta, **self._snapshot(before), "goal_semantic_problems": problems,
                "assignments": [[a.entity.entity_id or a.entity.attributes, a.target_id]
                                for a in goal.assignments],
                "ambiguous": goal.ambiguous}
        return goal, meta

    # ---------- 2. one continuous decision ----------
    def decide(self, ctx: DecisionContext) -> tuple[Decision, dict]:
        before = self.adapter.http_requests
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        raw, meta = self.adapter.chat_json(
            DECISION_SYSTEM, DECISION_USER + json.dumps(payload, ensure_ascii=False),
            kind="decision", prompt_version=self.decision_prompt)
        meta = {**meta, **self._snapshot(before), "round_index": ctx.round_index}
        decision = self._to_decision(raw, ctx, meta)
        meta["action"] = decision.action
        meta["skill"] = decision.execute.skill if decision.execute else None
        return decision, meta

    @staticmethod
    def _to_decision(raw, ctx: DecisionContext, meta: dict) -> Decision:
        """Bind the envelope identifiers the caller issued, then validate.

        Field-level errors are handed to the Runtime as rejection reasons: the
        model gets them in its next feedback and repairs its own output, which is
        the loop SPEC 6.1 wants — an adapter-side rewrite would be the runtime
        choosing the action instead."""
        body = dict(raw or {})
        for key in ("context_id", "goal_ref", "based_on_state_version", "decision_id",
                    "schema_version"):
            body.pop(key, None)
        body.update({"context_id": ctx.context_id, "goal_ref": ctx.goal.goal_id,
                     "based_on_state_version": ctx.state_version})
        try:
            return Decision.model_validate(body)
        except ValidationError as e:
            reasons = [f"{'.'.join(str(e2) for e2 in err['loc']) or '$'}: {err['msg']}"
                       for err in e.errors()]
            meta["schema_errors"] = reasons
            raise DecisionSchemaError(reasons, meta=meta) from e

    # ---------- 3. mode A: one plan, never revised ----------
    def plan_from_context(self, ctx: DecisionContext) -> tuple[Plan, dict]:
        before = self.adapter.http_requests
        payload = ctx.model_payload(feedback_depth=self.feedback_depth)
        raw, meta = self.adapter.chat_json(
            PLAN_SYSTEM, PLAN_USER + json.dumps(payload, ensure_ascii=False),
            kind="one_shot_plan", prompt_version=self.plan_prompt)
        meta = {**meta, **self._snapshot(before)}
        body = dict(raw or {})
        steps = body.pop("steps", None)
        body.pop("plan", None)
        if steps is None and isinstance(body.get("plan"), dict):
            steps = body["plan"].get("steps")
        try:
            plan = Plan.model_validate({"based_on_state_version": ctx.state_version,
                                        "goal_ref": ctx.goal.goal_id, "steps": steps or []})
        except ValidationError as e:
            reasons = [f"{'.'.join(str(e2) for e2 in err['loc']) or '$'}: {err['msg']}"
                       for err in e.errors()]
            meta["schema_errors"] = reasons
            raise DecisionSchemaError(reasons, meta=meta) from e
        if plan.validate_schema():
            reasons = plan.validate_schema()
            meta["schema_errors"] = reasons
            raise DecisionSchemaError(reasons, meta=meta)
        meta["steps"] = [[s.skill, s.args] for s in plan.steps]
        meta["clarification_request"] = (raw or {}).get("clarification_request")
        return plan, meta

    # ---------- construction ----------
    @classmethod
    def from_env(cls, model_config_path: str | None = None) -> "DeepSeekPlanner":
        """configs/models/*.yaml holds the *name* of the env var holding the key;
        the key itself never enters configs, snapshots, prompts or logs (SPEC 7)."""
        import yaml

        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        cfg_path = model_config_path or os.path.join(repo_root, "configs", "models", "deepseek.yaml")
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        key_env = cfg.get("api_key_env", "DEEPSEEK_API_KEY")
        api_key = os.environ.get(key_env, "")
        if not api_key:
            env_file = os.path.join(repo_root, ".env")
            if os.path.exists(env_file):
                with open(env_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key_env}="):
                            api_key = line.split("=", 1)[1].strip()
                            break
        prompts = cfg.get("prompts") or {}
        return cls(
            DeepSeekAdapter(
                api_key=api_key,
                model=os.environ.get("DEEPSEEK_MODEL", cfg.get("model", "deepseek-chat")),
                base_url=os.environ.get("DEEPSEEK_BASE_URL",
                                        cfg.get("base_url", "https://api.deepseek.com")),
                temperature=float(cfg.get("temperature", 0.2)),
                max_tokens=int(cfg.get("max_tokens", 1200)),
                timeout_s=float(cfg.get("timeout_s", 60)),
                max_retries=int(cfg.get("max_retries", 2)),
                proxy=cfg.get("proxy", "__direct__"),
                pricing_usd_per_mtok=cfg.get("pricing_usd_per_mtok"),
            ),
            goal_prompt=prompts.get("goal", "s2-goal-v1"),
            decision_prompt=prompts.get("decision", "s2-decide-v1"),
            plan_prompt=prompts.get("plan", "s2-plan-v1"),
            feedback_depth=int(cfg.get("feedback_depth", 3)),
        )


def _validate_goal(goal: GoalSpec, world: WorldState) -> tuple[GoalSpec, list[str]]:
    """Semantic check of a parsed goal against the snapshot it was parsed from.

    An unresolvable reference makes the goal ambiguous rather than partially
    guessed (SPEC 4.2) — the episode then ends as a question, not as an action."""
    problems: list[str] = []
    known = {e.entity_id for e in world.entities}
    targets = {t.target_id for t in world.targets}
    for a in goal.assignments:
        if a.target_id not in targets:
            problems.append(f"target {a.target_id!r} is not in this world")
        ref = a.entity
        if ref.entity_id is not None:
            if ref.entity_id not in known:
                problems.append(f"entity {ref.entity_id!r} is not in observation "
                                f"{world.observation_ref}")
            continue
        if ref.selector == "rest":
            continue
        if not ref.attributes:
            problems.append("assignment carries neither an entity_id nor attributes")
            continue
        hits = [eid for eid in known
                if all(world.entity(eid).attributes.get(k) == v for k, v in ref.attributes.items())]
        if len(hits) != 1:
            problems.append(f"attributes {ref.attributes} match {len(hits)} entities")
    if problems and not goal.ambiguous:
        goal = goal.model_copy(update={"ambiguous": True, "clarification_request": "; ".join(problems)})
    return goal, problems


class FixturePlanner:
    """OFFLINE test double with the same three methods as `DeepSeekPlanner`.

    It exists so mode A/B/C wiring can be exercised without a network — that is a
    plumbing check, not a model result. SPEC 10: a fixture double never enters an
    online acceptance metric, and `--planner fixture` labels every summary it
    produces as offline."""

    provider = "fixture"
    model = "fixture-rule-interpreter"
    prompt_version = "offline-fixture-no-prompt"
    http_requests = 0
    prompt_tokens = 0
    completion_tokens = 0
    api_errors = 0
    transport_retries = 0
    format_repairs = 0
    pricing: dict = {}

    def __init__(self, config=None):
        self.rule = RulePlanner(config)

    def parse_goal(self, task: TaskInput, world: WorldState) -> tuple[GoalSpec, dict]:
        goal = interpret(task, world)
        goal.based_on_state_version = world.state_version
        return goal, {"kind": "goal_parse", "provider": self.provider, "model": self.model,
                      "prompt_version": self.prompt_version, "http_requests_this_call": 0,
                      "raw_response": json.dumps({"offline": True}),
                      "ambiguous": goal.ambiguous,
                      "assignments": [[a.entity.entity_id or a.entity.attributes, a.target_id]
                                      for a in goal.assignments]}

    def decide(self, ctx: DecisionContext) -> tuple[Decision, dict]:
        decision = self.rule.decide(ctx)
        return decision, {"kind": "decision", "provider": self.provider, "model": self.model,
                          "prompt_version": self.prompt_version, "http_requests_this_call": 0,
                          "raw_response": json.dumps({"offline": True}),
                          "round_index": ctx.round_index, "action": decision.action,
                          "skill": decision.execute.skill if decision.execute else None}

    def plan_from_context(self, ctx: DecisionContext) -> tuple[Plan, dict]:
        plan = self.rule.plan(ctx.goal, ctx.world, plan_id="p_fixture")
        return plan, {"kind": "one_shot_plan", "provider": self.provider, "model": self.model,
                      "prompt_version": self.prompt_version, "http_requests_this_call": 0,
                      "raw_response": json.dumps({"offline": True}),
                      "steps": [[s.skill, s.args] for s in plan.steps]}

    def cost_estimate(self) -> float | None:
        # None, not 0.0: a zero would read as "priced, and free", this as "nothing was priced"
        return None
