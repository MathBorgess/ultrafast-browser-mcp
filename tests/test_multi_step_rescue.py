"""Tests for multi-step planning and rescue mechanism. Offline mocks only, no paid APIs."""

import time
from unittest.mock import Mock

import pytest

from laya_ultrafast import agent as loop
from laya_ultrafast import laya, model


def make_page(actions=None, title="Test Page", text="Test Page Content"):
    actions = actions or [
        {"id": "e1", "kind": "fill", "label": "Destination", "role": "textbox", "value": "", "node": 1},
        {"id": "e2", "kind": "click", "label": "Search", "role": "button", "value": "", "node": 2},
        {"id": "e3", "kind": "click", "label": "Close Modal", "role": "button", "value": "", "node": 3},
        {"id": "wait", "kind": "wait", "label": "Wait"},
    ]
    return {
        "url": "https://example.test/",
        "title": title,
        "text": text,
        "scroll": {"y": 0},
        "fingerprint": "fp_test",
        "actions": actions,
    }


# ---------------------------------------------------------------------------------------------------------
# 1. Model Planning Tests (plan_multi_step_goal, plan_next_step, rescue_agent)
# ---------------------------------------------------------------------------------------------------------


def test_plan_multi_step_goal(monkeypatch):
    mock_response = (
        {
            "steps": ["Step 1: Search flights", "Step 2: Select flight", "Step 3: Fill passenger details"],
            "step_index": 0,
            "requirements": [{"what": "destination", "value": "London"}],
            "open": None,
            "finish": "Flight search results are listed.",
            "is_final_step": False,
        },
        {"model": "test-llm", "latency_ms": 25, "usage": {}},
    )
    monkeypatch.setattr(model, "chat_json", Mock(return_value=mock_response))
    plan, meta = model.plan_multi_step_goal("Book a flight to London", fields=["destination"])

    assert len(plan["steps"]) == 3
    assert plan["step_index"] == 0
    assert plan["is_final_step"] is False
    assert plan["requirements"] == [{"what": "destination", "value": "London"}]
    assert plan["finish"] == "Flight search results are listed."
    assert meta["model"] == "test-llm"


def test_plan_multi_step_goal_fallback_to_plan_goal(monkeypatch):
    calls = []

    def mock_chat_json(system, context):
        calls.append(system)
        if len(calls) == 1:
            # First call for MULTI_STEP_GOAL_PLAN fails with invalid format
            return {"invalid": True}, {"model": "test", "latency_ms": 10}
        # Fallback to single-step plan_goal succeeds
        return (
            {
                "requirements": [{"what": "destination", "value": "London"}],
                "open": None,
                "finish": "Flight search results are listed.",
            },
            {"model": "test", "latency_ms": 10},
        )

    monkeypatch.setattr(model, "chat_json", mock_chat_json)
    plan, meta = model.plan_multi_step_goal("Book a flight to London", attempts=1)

    assert plan["requirements"] == [{"what": "destination", "value": "London"}]
    assert plan["is_final_step"] is True
    assert len(plan["steps"]) == 1


def test_plan_next_step_intermediate(monkeypatch):
    mock_response = (
        {
            "all_steps_complete": False,
            "step_name": "Select nonstop flight",
            "requirements": [],
            "open": "Flight 101",
            "finish": "Flight 101 details are displayed.",
            "is_final_step": True,
        },
        {"model": "test-llm", "latency_ms": 30, "usage": {}},
    )
    monkeypatch.setattr(model, "chat_json", Mock(return_value=mock_response))
    plan, meta = model.plan_next_step(
        "Book a flight to London",
        ["Step 1: Search flights"],
        1,
        fields=["Flight 101"],
        page=make_page(),
    )
    assert plan["all_steps_complete"] is False
    assert plan["step_name"] == "Select nonstop flight"
    assert plan["open"] == "Flight 101"
    assert plan["is_final_step"] is True


def test_plan_next_step_all_complete(monkeypatch):
    mock_response = (
        {
            "all_steps_complete": True,
            "finish": "Booking confirmation is visible.",
        },
        {"model": "test-llm", "latency_ms": 20, "usage": {}},
    )
    monkeypatch.setattr(model, "chat_json", Mock(return_value=mock_response))
    plan, meta = model.plan_next_step(
        "Book a flight to London",
        ["Step 1", "Step 2"],
        2,
        page=make_page(),
    )
    assert plan["all_steps_complete"] is True
    assert plan["is_final_step"] is True


def test_rescue_agent_click(monkeypatch):
    mock_response = (
        {
            "action": "click",
            "target": "3",
            "text": None,
            "revised_plan": None,
            "reason": "Dismiss cookie banner",
        },
        {"model": "test-llm", "latency_ms": 15, "usage": {}},
    )
    monkeypatch.setattr(model, "chat_json", Mock(return_value=mock_response))
    rescue, meta = model.rescue_agent(
        "Book a flight",
        {"requirements": [], "finish": "Done"},
        make_page(),
        [],
        candidate_elements=[{"index": "3", "label": "Close Modal", "role": "button"}],
    )
    assert rescue["action"] == "click"
    assert rescue["target"] == "3"
    assert rescue["reason"] == "Dismiss cookie banner"


def test_rescue_agent_replan(monkeypatch):
    mock_response = (
        {
            "action": "replan",
            "target": None,
            "text": None,
            "revised_plan": {
                "requirements": [{"what": "Airport code", "value": "LHR"}],
                "open": None,
                "finish": "Results for LHR",
            },
            "reason": "Airport code needed instead of city name",
        },
        {"model": "test-llm", "latency_ms": 15, "usage": {}},
    )
    monkeypatch.setattr(model, "chat_json", Mock(return_value=mock_response))
    rescue, meta = model.rescue_agent("Book a flight", {}, make_page(), [])
    assert rescue["action"] == "replan"
    assert rescue["revised_plan"]["requirements"] == [{"what": "Airport code", "value": "LHR"}]


# ---------------------------------------------------------------------------------------------------------
# 2. LayaPolicy Multi-Step & Rescue Tests
# ---------------------------------------------------------------------------------------------------------


class FakeLayaDecision:
    def __init__(self, choices=None):
        self.choices = choices or {}
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        for key, q in questions.items():
            if q["type"] == "choice":
                default_choice = next(iter(q["criteria"]))
                choice_val = self.choices.get(key, default_choice)
                answers[key] = {
                    "choice": choice_val,
                    "confidence": 1.0,
                    "probabilities": {k: float(k == choice_val) for k in q["criteria"]},
                }
        return {"model": "fake-laya", "answers": answers, "usage": {"input_tokens": 10}}


def test_policy_emits_step_when_not_final_step(monkeypatch):
    fake = FakeLayaDecision({"done": "finish"})
    monkeypatch.setattr(laya, "laya", lambda: fake)

    p = laya.LayaPolicy("Book a flight")
    p.plan = {
        "requirements": [],
        "open": None,
        "finish": "Flight search results are listed.",
        "steps": ["Search flights", "Select flight"],
        "step_index": 0,
        "is_final_step": False,
    }
    p.is_final_step = False
    p.steps = p.plan["steps"]

    p_page = make_page()
    decision = p.choose(p_page, [])

    assert decision["operation"] == "STEP"
    assert decision["choice"] == "STEP"


def test_policy_emits_done_when_final_step(monkeypatch):
    fake = FakeLayaDecision({"done": "finish"})
    monkeypatch.setattr(laya, "laya", lambda: fake)

    p = laya.LayaPolicy("Book a flight")
    p.plan = {
        "requirements": [],
        "open": None,
        "finish": "Booking confirmed.",
        "steps": ["Search flights", "Select flight"],
        "step_index": 1,
        "is_final_step": True,
    }
    p.is_final_step = True
    p.steps = p.plan["steps"]

    p_page = make_page()
    decision = p.choose(p_page, [])

    assert decision["operation"] == "DONE"
    assert decision["choice"] == "DONE"


def test_policy_emits_rescue_when_stuck_on_failed_requirements(monkeypatch):
    fake = FakeLayaDecision()
    monkeypatch.setattr(laya, "laya", lambda: fake)

    p = laya.LayaPolicy("Book a flight")
    p.plan = {
        "requirements": [{"what": "nonexistent field", "value": "xyz"}],
        "open": None,
        "finish": "Done.",
        "is_final_step": True,
    }
    # Simulate 3 failed attempts on requirement 0
    p.attempts[0] = 3
    p.skipped.add(0)

    p_page = make_page()
    decision = p.choose(p_page, [])

    assert decision["operation"] == "RESCUE"
    assert decision["choice"] == "RESCUE"
    assert p.rescued is True

    # If choose is called again without clearing rescue state, it falls through to BLOCKED
    decision2 = p.choose(p_page, [])
    assert decision2["operation"] == "BLOCKED"
    assert decision2["choice"] == "BLOCKED"


def test_policy_advance_step():
    p = laya.LayaPolicy("Book a flight")
    p.steps = ["Step 1: Search", "Step 2: Select", "Step 3: Confirm"]
    p.plan = {"requirements": [{"what": "q", "value": "London"}], "finish": "Step 1 finished"}
    p.current_step_index = 0
    p.met.add(0)
    p.rescued = True

    next_plan = {
        "requirements": [],
        "open": "Flight 123",
        "finish": "Flight 123 selected",
        "is_final_step": False,
    }
    p.advance_step(next_plan)

    assert p.current_step_index == 1
    assert p.completed_steps == ["Step 1: Search"]
    assert p.plan == next_plan
    assert p.is_final_step is False
    assert len(p.met) == 0
    assert p.rescued is False


def test_policy_apply_rescue():
    p = laya.LayaPolicy("Book a flight")
    p.plan = {"requirements": [{"what": "q", "value": "London"}], "finish": "Step 1 finished"}
    p.attempts[0] = 3
    p.skipped.add(0)

    rescue_result = {
        "action": "replan",
        "revised_plan": {
            "requirements": [{"what": "destination", "value": "London"}],
            "finish": "Revised finish",
        },
    }
    p.apply_rescue(rescue_result)

    assert p.rescued is True
    assert p.plan["requirements"] == [{"what": "destination", "value": "London"}]
    assert 0 not in p.skipped
    assert len(p.attempts) == 0


# ---------------------------------------------------------------------------------------------------------
# 3. Agent Integration Tests with STEP and RESCUE
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def agent_runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = make_page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p), act=Mock()),
        "page": p,
        "decision": None,
        "goal": "Book a flight from Lisbon to London",
        "history": [],
        "decisions": [],
        "status": "ready",
        "plan": ["Book a flight from Lisbon to London"],
        "plan_index": 0,
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    a.policy = Mock(
        plan={"requirements": [], "finish": "Step 1 finish"},
        steps=["Step 1", "Step 2"],
        current_step_index=0,
        completed_steps=[],
        advance_step=Mock(),
        apply_rescue=Mock(),
    )
    return a


def test_agent_act_step_elevation(agent_runner, monkeypatch):
    next_step_return = (
        {
            "all_steps_complete": False,
            "step_name": "Step 2: Select flight",
            "requirements": [],
            "open": "Flight 1",
            "finish": "Flight 1 selected",
            "is_final_step": True,
        },
        {"model": "test-planner", "latency_ms": 12},
    )
    monkeypatch.setattr(loop, "plan_next_step", Mock(return_value=next_step_return))

    agent_runner.state["decision"] = {
        "choice": "STEP",
        "operation": "STEP",
        "target": None,
        "confidence": 1.0,
        "probabilities": {"STEP": 1.0},
        "latency_ms": 10,
        "usage": {},
    }

    snapshot = agent_runner.command("act", {"fingerprint": agent_runner.state["page"]["fingerprint"]})

    assert snapshot["status"] == "ready"
    assert len(agent_runner.state["history"]) == 1
    assert agent_runner.state["history"][0]["operation"] == "STEP"
    agent_runner.policy.advance_step.assert_called_once()
    assert agent_runner.state["goal_plan"]["finish"] == "Flight 1 selected"


def test_agent_act_step_finalizes_when_all_steps_complete(agent_runner, monkeypatch):
    next_step_return = (
        {
            "all_steps_complete": True,
            "finish": "All steps done",
            "is_final_step": True,
        },
        {"model": "test-planner", "latency_ms": 10},
    )
    monkeypatch.setattr(loop, "plan_next_step", Mock(return_value=next_step_return))

    agent_runner.state["decision"] = {
        "choice": "STEP",
        "operation": "STEP",
        "target": None,
        "confidence": 1.0,
        "probabilities": {"STEP": 1.0},
        "latency_ms": 10,
        "usage": {},
    }

    snapshot = agent_runner.command("act", {"fingerprint": agent_runner.state["page"]["fingerprint"]})

    assert snapshot["status"] == "done"
    assert snapshot["plan_index"] == len(agent_runner.state["plan"])


def test_agent_act_rescue_click(agent_runner, monkeypatch):
    rescue_return = (
        {
            "action": "click",
            "target": "3",
            "text": None,
            "revised_plan": None,
            "reason": "Dismiss consent dialog",
        },
        {"model": "test-rescuer", "latency_ms": 15},
    )
    monkeypatch.setattr(loop, "rescue_agent", Mock(return_value=rescue_return))

    agent_runner.state["decision"] = {
        "choice": "RESCUE",
        "operation": "RESCUE",
        "target": None,
        "confidence": 1.0,
        "probabilities": {"RESCUE": 1.0},
        "latency_ms": 10,
        "usage": {},
    }

    snapshot = agent_runner.command("act", {"fingerprint": agent_runner.state["page"]["fingerprint"]})

    assert snapshot["status"] == "ready"
    agent_runner.state["browser"].act.assert_called_once()
    assert len(agent_runner.state["history"]) == 1
    assert agent_runner.state["history"][0]["kind"] == "click"
    agent_runner.policy.apply_rescue.assert_called_once()


def test_agent_act_rescue_give_up(agent_runner, monkeypatch):
    rescue_return = (
        {
            "action": "give_up",
            "target": None,
            "text": None,
            "revised_plan": None,
            "reason": "Captcha blocks progress",
        },
        {"model": "test-rescuer", "latency_ms": 15},
    )
    monkeypatch.setattr(loop, "rescue_agent", Mock(return_value=rescue_return))

    agent_runner.state["decision"] = {
        "choice": "RESCUE",
        "operation": "RESCUE",
        "target": None,
        "confidence": 1.0,
        "probabilities": {"RESCUE": 1.0},
        "latency_ms": 10,
        "usage": {},
    }

    snapshot = agent_runner.command("act", {"fingerprint": agent_runner.state["page"]["fingerprint"]})

    assert snapshot["status"] == "blocked"
    assert len(agent_runner.state["history"]) == 1
    assert agent_runner.state["history"][0]["operation"] == "BLOCKED"
