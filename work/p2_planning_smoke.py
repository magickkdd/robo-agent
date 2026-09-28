"""P2-c dev probe: does the planning arm complete an episode, at zero spend, on its own payload?

Read-only against the repository; everything it writes goes under /tmp. It answers three
questions before any contract test is written, in this order:

1. can a policy that reads *only* `ctx.model_payload()` carry an episode to the end, in every one
   of §9's planning settings — i.e. is the view sufficient, and does the gate change the payload
   rather than the decision maker;
2. does the arm's own claim survive `Ablation.violations()` — no record produced by a module that
   was switched off, and no event type outside the vocabulary;
3. what does the `ablation`/`plan`/`working_memory`/`obligation_check` trail actually look like for
   a task whose regions are contended (`dev_c2`, two objects sharing a tray) versus one whose are
   not (`dev_c5`).

Nothing here is a measurement for the report; it is the smoke check that decides what the tests
have to pin.
"""
import json
import os
import sys
import tempfile

from embodied_agent.core.events import EpisodeStore
from embodied_agent.core.fault_injection import EnvironmentController
from embodied_agent.core.interpreter import interpret
from embodied_agent.core.skills import SkillExecutor
from embodied_agent.core.v02 import SCHEMA_VERSION, V02_EVENT_TYPES, ablation
from embodied_agent.core.verify import build_world_state
from embodied_agent.evaluation.run import build_scene, task_input
from embodied_agent.evaluation.tasks import find_case
from embodied_agent.planning.arm import PLANNING_ARMS, build_planning_arm
from embodied_agent.planning.policy import PlanPolicy
from embodied_agent.planning.view import planning_coherence

ROOT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="p2_plan_")
CASES = ("dev_c2", "dev_c5")


def run(case_id: str, condition: str | None):
    case = find_case(case_id)
    scene = build_scene(case)
    try:
        world = build_world_state(scene, 1, "obs_0001", case.verify)
        task = task_input(case)
        goal = interpret(task, world)
        arm = None if condition is None else ablation(condition)
        episode_id = f"{case_id}.{condition or 'unarmed'}"
        ep_dir = os.path.join(ROOT, episode_id)
        os.makedirs(ep_dir, exist_ok=True)
        controller = EnvironmentController(scene, case.fresh_events())
        executor = SkillExecutor(scene, world_provider=lambda: None, config=case.verify)
        store = EpisodeStore(ep_dir, episode_id)
        rt = build_planning_arm(case=case, scene=scene, executor=executor, store=store,
                                run_dir=ep_dir, episode_id=episode_id, budgets=case.budgets,
                                perceive="privileged", ablation=arm, environment=controller)
        executor.world_provider = rt.observe
        policy = PlanPolicy()
        result = rt.run_episode(task, goal, policy, mode="B")

        events = store.read_all()
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        s4 = store.read_all(SCHEMA_VERSION)
        s4_types = sorted({e["type"] for e in s4})
        # which payload sections actually reached the page, per round. The `decision_context` row
        # publishes selected fields, not the payload, so the footprint is read from the arm's own
        # per-round record of what it rendered (`_file_round`), not re-derived from the log.
        sections: dict[str, int] = {}
        for row in rt.rounds:
            key = "+".join(row["sections"]) or "none"
            sections[key] = sections.get(key, 0) + 1
        claims = list(arm.violations(s4_types)) if arm is not None else []
        unknown = sorted(set(s4_types) - set(V02_EVENT_TYPES))
        checks = planning_coherence(events)
        obligation = next((e for e in reversed(s4) if e["type"] == "obligation_check"), None)
        payload = (obligation or {}).get("payload") or {}
        return {
            "case": case_id, "arm": condition or "unarmed",
            "modules_off": list(arm.modules_off) if arm else [],
            "status": str(result.terminal_status), "failure": str(result.failure_type),
            "rounds": result.decision_rounds, "skill_calls": result.skill_calls,
            "http_requests": result.http_requests,
            "prompt_tokens": result.prompt_tokens,
            "policy_http": policy.http_requests,
            "completed": f"{result.objects_completed}/{result.objects_total}",
            "payload_sections": sections,
            "plan_rows_final": len(rt.plan.subgoals) if rt.plan else None,
            "plan_kinds": sorted({s.kind for s in (rt.plan.subgoals if rt.plan else [])}),
            "versions": rt.plan.version if rt.plan else None,
            "events": kinds, "schema4_types": s4_types,
            "unattributed_event_types": unknown,
            "ablation_violations": claims,
            "coherence_findings": checks["findings"],
            "coherence_counts": {k: v for k, v in checks.items() if k != "findings"},
            "obligation_check": {k: payload.get(k) for k in
                                 ("open_commitments", "unrestored_relations",
                                  "unresolved_obligations", "invalid_completion",
                                  "owed_subgoals", "arm", "terminal_status")} if payload else None,
            "policy_unactuated_total": sum(len(t["unactuated"]) for t in policy.trace),
            "last_rationale": (policy.trace[-1]["rationale"][:220] if policy.trace else None),
            "dir": ep_dir,
        }
    finally:
        scene.close()


if __name__ == "__main__":
    print("root:", ROOT, flush=True)
    rows = []
    for case_id in CASES:
        for condition in (None, *PLANNING_ARMS):
            try:
                row = run(case_id, condition)
            except Exception as e:  # noqa: BLE001 - the probe reports, it does not fix
                row = {"case": case_id, "arm": condition or "unarmed",
                       "error": f"{type(e).__name__}: {e}"}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
    with open(os.path.join(ROOT, "p2_planning_smoke.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1, default=str)
    bad = [r for r in rows if r.get("ablation_violations") or r.get("unattributed_event_types")
           or "error" in r]
    print(f"\n{len(rows)} episodes; {len(bad)} with an error, a violation or an unknown type",
          flush=True)
