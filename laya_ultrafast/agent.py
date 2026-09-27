"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import os
import time
from pathlib import Path

from .browser import Browser, StalePage
from .laya import LayaPolicy, display, laya, observed, plannable
from .model import (
    action_space,
    choose,
    field_context,
    field_text,
    plan_next_step,
    rescue_agent,
)
from .questions import MAX_STEPS


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, plan=None):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan_list = plan.get("steps", [task]) if plan and plan.get("steps") else [task]
        self.pending_text = None
        # Local Laya decisions by default; DECISION_MODEL=typesafe keeps the hosted Jev policy.
        self.policy = LayaPolicy(task) if os.environ.get("DECISION_MODEL", "laya") == "laya" else None
        if self.policy:
            laya()  # Load and warm the local model before the task clock starts.
            if plan:
                self.policy.plan = plan
                self.policy.plan_meta = {"model": "mcp_client", "latency_ms": 0}
                if "steps" in plan:
                    self.policy.steps = plan["steps"]
                if "is_final_step" in plan:
                    self.policy.is_final_step = bool(plan["is_final_step"])
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan_list),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan_list,
            plan_index=0,
            goal_plan=plan,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def set_plan(self, plan):
        """Set or update the active plan on the agent policy from the controller model."""
        if self.policy:
            self.policy.plan = plan
            self.policy.plan_meta = {"model": "mcp_client", "latency_ms": 0}
            if "steps" in plan:
                self.policy.steps = plan["steps"]
            if "is_final_step" in plan:
                self.policy.is_final_step = bool(plan["is_final_step"])
            self.policy.fields.clear()
            self.policy.met.clear()
            self.policy.skipped.clear()
            self.policy.attempts.clear()
            self.policy.submitted = False
            self.policy.waits = 0
            self.policy.tried.clear()
            self.policy.typed = False
            self.policy.acted = None
            self.policy.search_added = False
            self.policy.frozen.clear()
            self.policy.rescued = False
            self.policy.pending = None
            self.policy.failed.clear()
        self.state["goal_plan"] = plan
        if plan.get("steps"):
            self.state["plan"] = plan["steps"]
        self.state["status"] = "ready"

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def navigate(self, url):
        """Navigate existing browser tab to a new URL and update state for SPAs/links."""
        browser = getattr(self, "browser", None) or self.state.get("browser")
        if hasattr(browser, "navigate"):
            page = browser.navigate(url)
        else:
            browser.call("Page.navigate", url=url)
            page = browser.observe(screenshot=self.screenshots)
        self.state["page"] = page
        self.state["decision"] = None
        self.state["status"] = "ready"
        if getattr(self, "policy", None):
            self.policy.fields.clear()
            self.policy.attempts.clear()
            self.policy.waits = 0
            self.policy.tried.clear()
        return self.snapshot()

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            policy = getattr(self, "policy", None)
            if policy:
                planned = policy.plan is not None
                state["decision"] = policy.choose(state["page"], state["history"])
                if not planned:
                    state["goal_plan"] = policy.plan
                    if policy.steps:
                        state["plan"] = policy.steps
                    state["text_calls"].append({**policy.plan_meta, "field": "goal plan", "value": policy.plan})
            else:
                state["decision"] = choose(state["page"], state["goal"], state["history"])
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = len(state.get("plan", [])) if selected == "DONE" else state.get("plan_index", 0)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            if selected == "STEP":
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                policy = getattr(self, "policy", None)
                curr_idx = getattr(policy, "current_step_index", state.get("plan_index", 0))
                completed_list = getattr(policy, "completed_steps", [])
                curr_finish = (
                    policy.plan.get("finish", f"Step {curr_idx + 1}")
                    if policy and policy.plan
                    else f"Step {curr_idx + 1}"
                )
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                state["history"].append(
                    {
                        "step": len(state["history"]) + 1,
                        "action": f"Step {curr_idx + 1} completed: {curr_finish}",
                        "kind": "step",
                        "choice": "STEP",
                        "probability": decision.get("probabilities", {}).get("STEP", 1.0),
                        "confidence": decision.get("confidence", 1.0),
                        "latency_ms": decision.get("latency_ms", 0),
                        "text": None,
                        "text_helper": None,
                        "text_latency_ms": 0,
                        "operation": "STEP",
                        "target": None,
                        "page_changed": None,
                        "url": page["url"],
                        "usage": decision.get("usage", {}),
                        "executed_ms": state["elapsed_ms"],
                        "elapsed_ms": state["elapsed_ms"],
                    }
                )
                fresh_page = state["browser"].observe(screenshot=self.screenshots)
                state["page"] = fresh_page
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                state["history"][-1].update(
                    page_changed=fresh_page["fingerprint"] != page["fingerprint"],
                    url=fresh_page["url"],
                    elapsed_ms=state["elapsed_ms"],
                )
                if state["record"]:
                    (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                        base64.b64decode(fresh_page["screenshot"])
                    )

                if policy and policy.plan_meta and policy.plan_meta.get("model") == "mcp_client":
                    completed_name = (
                        policy.steps[curr_idx]
                        if policy.steps and curr_idx < len(policy.steps)
                        else curr_finish
                    )
                    policy.completed_steps.append(completed_name)
                    policy.current_step_index = curr_idx + 1
                    state["plan_index"] = policy.current_step_index
                    state["status"] = "step_completed"
                    return self.snapshot()

                fresh_elements = observed(fresh_page)
                labels = list(dict.fromkeys(display(e) for e in fresh_elements if plannable(e)))
                next_plan, next_meta = plan_next_step(
                    state["goal"],
                    completed_list + [curr_finish],
                    curr_idx + 1,
                    fields=labels,
                    page=fresh_page,
                )
                state["text_calls"].append({**next_meta, "field": f"step plan {curr_idx + 2}", "value": next_plan})
                if next_plan.get("all_steps_complete"):
                    state["status"] = "done"
                    state["plan_index"] = len(state.get("plan", []))
                    return self.snapshot()
                if policy:
                    policy.advance_step(next_plan, next_meta)
                    state["plan_index"] = policy.current_step_index
                else:
                    state["plan_index"] = curr_idx + 1
                state["goal_plan"] = next_plan
                state["status"] = "ready"
                return self.snapshot()
            if selected == "RESCUE":
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                policy = getattr(self, "policy", None)
                if policy and policy.plan_meta and policy.plan_meta.get("model") == "mcp_client":
                    state["status"] = "rescue_needed"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    return self.snapshot()
                current_plan = policy.plan if policy and policy.plan else state.get("goal_plan", {})
                elements = observed(page)
                rescue_result, rescue_meta = rescue_agent(
                    state["goal"],
                    current_plan,
                    page,
                    state["history"],
                    problem="Stuck or no progress",
                    candidate_elements=elements,
                )
                state["text_calls"].append({**rescue_meta, "field": "rescue", "value": rescue_result})
                action_kind = rescue_result.get("action", "give_up")
                if action_kind == "give_up":
                    state["status"] = "blocked"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    state["history"].append(
                        {
                            "step": len(state["history"]) + 1,
                            "action": f"Rescue give up: {rescue_result.get('reason', '')}",
                            "kind": "rescue",
                            "choice": "BLOCKED",
                            "probability": 1.0,
                            "confidence": 1.0,
                            "latency_ms": rescue_meta.get("latency_ms", 0),
                            "text": None,
                            "text_helper": rescue_meta.get("model"),
                            "text_latency_ms": rescue_meta.get("latency_ms", 0),
                            "operation": "BLOCKED",
                            "target": None,
                            "page_changed": False,
                            "url": page["url"],
                            "usage": rescue_meta.get("usage", {}),
                            "executed_ms": state["elapsed_ms"],
                            "elapsed_ms": state["elapsed_ms"],
                        }
                    )
                    return self.snapshot()
                if action_kind == "replan":
                    if policy:
                        policy.apply_rescue(rescue_result)
                        if rescue_result.get("revised_plan"):
                            state["goal_plan"] = policy.plan
                    state["status"] = "ready"
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                    state["history"].append(
                        {
                            "step": len(state["history"]) + 1,
                            "action": f"Rescue replan: {rescue_result.get('reason', '')}",
                            "kind": "rescue",
                            "choice": "RESCUE",
                            "probability": 1.0,
                            "confidence": 1.0,
                            "latency_ms": rescue_meta.get("latency_ms", 0),
                            "text": None,
                            "text_helper": rescue_meta.get("model"),
                            "text_latency_ms": rescue_meta.get("latency_ms", 0),
                            "operation": "RESCUE",
                            "target": None,
                            "page_changed": False,
                            "url": page["url"],
                            "usage": rescue_meta.get("usage", {}),
                            "executed_ms": state["elapsed_ms"],
                            "elapsed_ms": state["elapsed_ms"],
                        }
                    )
                    return self.snapshot()
                target_idx = str(rescue_result.get("target") or "").strip()
                target_elem = next((e for e in elements if str(e["index"]) == target_idx), None)
                target_action = None
                fill_text = rescue_result.get("text")
                if target_elem:
                    if action_kind == "fill" and "fill" in target_elem["actions"]:
                        target_action = target_elem["actions"]["fill"]
                    elif action_kind == "click" and "click" in target_elem["actions"]:
                        target_action = target_elem["actions"]["click"]
                    elif target_elem["actions"]:
                        target_action = next(iter(target_elem["actions"].values()))
                if target_action:
                    state["browser"].act(target_action, page, text=fill_text)
                if policy:
                    policy.apply_rescue(rescue_result)
                    if rescue_result.get("revised_plan"):
                        state["goal_plan"] = policy.plan
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                action_label = (
                    target_action["label"]
                    if target_action
                    else f"Rescue {action_kind}: {rescue_result.get('reason', '')}"
                )
                state["history"].append(
                    {
                        "step": len(state["history"]) + 1,
                        "action": action_label,
                        "kind": action_kind,
                        "choice": target_action["id"] if target_action else "RESCUE",
                        "probability": 1.0,
                        "confidence": 1.0,
                        "latency_ms": rescue_meta.get("latency_ms", 0),
                        "text": fill_text,
                        "text_helper": rescue_meta.get("model"),
                        "text_latency_ms": rescue_meta.get("latency_ms", 0),
                        "operation": action_kind.upper(),
                        "target": target_idx if target_elem else None,
                        "page_changed": None,
                        "url": page["url"],
                        "usage": rescue_meta.get("usage", {}),
                        "executed_ms": state["elapsed_ms"],
                        "elapsed_ms": state["elapsed_ms"],
                    }
                )
                fresh_page = state["browser"].observe(screenshot=self.screenshots)
                state["page"] = fresh_page
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                state["history"][-1].update(
                    page_changed=fresh_page["fingerprint"] != page["fingerprint"],
                    url=fresh_page["url"],
                    elapsed_ms=state["elapsed_ms"],
                )
                if state["record"]:
                    (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                        base64.b64decode(fresh_page["screenshot"])
                    )
                state["status"] = "ready"
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill" and decision.get("text") is not None:
                # The goal plan already holds this value; no per-field text call.
                text, helper = decision["text"], {"model": "goal plan", "latency_ms": 0}
            elif action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
