"""SPEC-v0.2 §5.2/§5.3: the task planner and the working memory that carries its debts.

Two modules, one boundary. `task_planner` owns the *offer* — a versioned `TaskPlan` of
subgoals and dependencies recomputed against the latest snapshot. `working_memory` owns the
*debts* — commitments, temporarily-suspended relations, failed attempts, invalidated
assumptions and unanswered questions, which outlive the feedback window the offer is rendered
into. Neither one picks an action: §3.4 forbids the runtime choosing strategy, and the
distinction is enforced by `arm.py`, which only ever writes records and renders a payload.
"""
