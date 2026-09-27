"""Tests for the Laya Ultrafast MCP server."""

import json
from unittest.mock import Mock

import pytest

from laya_ultrafast.mcp import TOOLS, LayaMCPServer


@pytest.fixture
def mcp_server():
    server = LayaMCPServer()
    yield server
    server.close_all_sessions()


def test_mcp_initialize(mcp_server):
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    res = mcp_server.handle_request(req)
    assert res["jsonrpc"] == "2.0"
    assert res["id"] == 1
    assert res["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in res["result"]["capabilities"]
    assert res["result"]["serverInfo"]["name"] == "laya-ultrafast"


def test_mcp_ping(mcp_server):
    req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    res = mcp_server.handle_request(req)
    assert res["id"] == 2
    assert res["result"] == {}


def test_mcp_tools_list(mcp_server):
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    res = mcp_server.handle_request(req)
    assert res["id"] == 3
    tools = res["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "laya_run_task" in tool_names
    assert "laya_inspect_page" in tool_names
    assert "laya_rescue_task" in tool_names
    assert "laya_session_start" in tool_names
    assert "laya_session_step" in tool_names
    assert "laya_session_get_state" in tool_names
    assert "laya_session_close" in tool_names
    assert "laya_list_sessions" in tool_names
    assert len(tools) == len(TOOLS)


def test_mcp_unknown_method(mcp_server):
    req = {"jsonrpc": "2.0", "id": 4, "method": "nonexistent_method"}
    res = mcp_server.handle_request(req)
    assert res["id"] == 4
    assert res["error"]["code"] == -32601
    assert "Method not found" in res["error"]["message"]


def test_mcp_notification_returns_none(mcp_server):
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    res = mcp_server.handle_request(req)
    assert res is None


def test_mcp_call_unknown_tool(mcp_server):
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "invalid_tool", "arguments": {}},
    }
    res = mcp_server.handle_request(req)
    assert res["id"] == 5
    assert res["result"]["isError"] is True
    assert "Unknown tool" in res["result"]["content"][0]["text"]


def test_mcp_run_task_with_model_plan(mcp_server, monkeypatch):
    mock_agent = Mock()
    mock_agent.state = {
        "status": "ready",
        "goal": "Search flight to London",
        "page": {
            "url": "https://example.test/result",
            "title": "Flight Results",
            "text": "London flights are listed.",
            "actions": [],
        },
        "history": [
            {"step": 1, "action": "Type London", "kind": "fill", "choice": "e1", "page_changed": True},
            {"step": 2, "action": "Click Search", "kind": "click", "choice": "e2", "page_changed": True},
        ],
        "plan_index": 1,
        "plan": ["Search flight"],
        "elapsed_ms": 1250,
    }
    mock_agent.policy = Mock(plan=None)

    def fake_tick():
        mock_agent.state["status"] = "done"

    def apply_plan(p):
        mock_agent.policy.plan = p

    mock_agent.command = Mock(side_effect=lambda name: fake_tick() if name == "tick" else None)
    mock_agent.close = Mock()
    mock_agent.set_plan = Mock(side_effect=apply_plan)

    monkeypatch.setattr("laya_ultrafast.mcp.Agent", Mock(return_value=mock_agent))

    plan = {
        "requirements": [{"what": "Where to?", "value": "London"}],
        "open": None,
        "finish": "London flights are listed.",
        "is_final_step": True,
    }

    req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "laya_run_task",
            "arguments": {
                "url": "https://example.test",
                "goal": "Search flight to London",
                "plan": plan,
                "max_steps": 5,
            },
        },
    }
    res = mcp_server.handle_request(req)
    assert res["id"] == 6
    assert res["result"]["isError"] is False
    content = json.loads(res["result"]["content"][0]["text"])
    assert content["status"] == "done"
    assert content["final_url"] == "https://example.test/result"
    assert content["page_title"] == "Flight Results"
    assert content["total_actions"] == 2
    mock_agent.close.assert_called_once()


def test_mcp_run_task_multi_step_flow(mcp_server, monkeypatch):
    mock_agent = Mock()
    mock_agent.state = {
        "status": "ready",
        "goal": "Search flight and select cheapest",
        "page": {
            "url": "https://example.test/results",
            "title": "Flight Results",
            "text": "Flight 101 - 85 EUR. Flight 202 - 110 EUR.",
            "actions": [
                {"id": "e3", "kind": "click", "label": "Select Flight 101", "role": "button", "node": 10},
            ],
        },
        "history": [
            {"step": 1, "action": "Search", "kind": "click", "choice": "e2", "page_changed": True},
        ],
        "plan_index": 0,
        "plan": ["Search flight", "Select flight"],
        "elapsed_ms": 600,
    }
    mock_agent.policy = Mock(plan=None)

    # Step 1 execution yields step_completed
    def fake_step_1():
        mock_agent.state["status"] = "step_completed"

    def apply_plan(p):
        mock_agent.policy.plan = p
        mock_agent.state["status"] = "ready"

    mock_agent.command = Mock(side_effect=lambda name: fake_step_1() if name == "tick" else None)
    mock_agent.close = Mock()
    mock_agent.set_plan = Mock(side_effect=apply_plan)

    monkeypatch.setattr("laya_ultrafast.mcp.Agent", Mock(return_value=mock_agent))

    step_1_plan = {
        "requirements": [{"what": "Where to?", "value": "London"}],
        "open": None,
        "finish": "Flight search results are listed.",
        "is_final_step": False,
    }

    # Model calls laya_run_task with Step 1 plan
    req1 = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "laya_run_task",
            "arguments": {
                "session_id": "flight-session",
                "url": "https://example.test",
                "goal": "Search flight and select cheapest",
                "plan": step_1_plan,
            },
        },
    }
    res1 = mcp_server.handle_request(req1)
    data1 = json.loads(res1["result"]["content"][0]["text"])
    assert data1["status"] == "step_completed"
    assert data1["session_id"] == "flight-session"
    assert "Select Flight 101" in data1["fields_on_page"]
    # Session must remain open for step 2
    mock_agent.close.assert_not_called()

    # Model reviews results and calls laya_run_task with Step 2 plan
    def fake_step_2():
        mock_agent.state["status"] = "done"

    mock_agent.command = Mock(side_effect=lambda name: fake_step_2() if name == "tick" else None)
    step_2_plan = {
        "requirements": [],
        "open": "Flight 101",
        "finish": "Flight 101 is selected.",
        "is_final_step": True,
    }
    req2 = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {
            "name": "laya_run_task",
            "arguments": {
                "session_id": "flight-session",
                "goal": "Search flight and select cheapest",
                "plan": step_2_plan,
            },
        },
    }
    res2 = mcp_server.handle_request(req2)
    data2 = json.loads(res2["result"]["content"][0]["text"])
    assert data2["status"] == "done"
    mock_agent.set_plan.assert_called_with(step_2_plan)
    mock_agent.close.assert_called_once()


def test_mcp_inspect_page(mcp_server, monkeypatch):
    mock_agent = Mock()
    mock_agent.state = {
        "page": {
            "url": "https://example.test/search",
            "title": "Flight Search",
            "text": "Origin: Lisbon. Destination: London.",
            "actions": [
                {"id": "e1", "kind": "fill", "label": "Origin", "role": "textbox", "node": 1},
                {"id": "e2", "kind": "fill", "label": "Destination", "role": "textbox", "node": 2},
                {"id": "e3", "kind": "click", "label": "Search Flights", "role": "button", "node": 3},
            ],
        }
    }
    monkeypatch.setattr("laya_ultrafast.mcp.Agent", Mock(return_value=mock_agent))

    req = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "laya_inspect_page",
            "arguments": {
                "url": "https://example.test/search",
                "session_id": "inspect-test",
            },
        },
    }
    res = mcp_server.handle_request(req)
    assert res["result"]["isError"] is False
    data = json.loads(res["result"]["content"][0]["text"])
    assert data["session_id"] == "inspect-test"
    assert "Origin" in data["fields_on_page"]
    assert "Destination" in data["fields_on_page"]
    assert "Search Flights" in data["fields_on_page"]

    # In modern SPAs, navigating to a new URL in the same session must reuse the tab
    def fake_navigate(new_url):
        mock_agent.state["page"] = {
            "url": new_url,
            "title": "Flight Results",
            "text": "Results loaded",
            "actions": [{"id": "e4", "kind": "click", "label": "Select", "role": "button", "node": 4}],
        }
        return mock_agent.state

    mock_agent.navigate = Mock(side_effect=fake_navigate)
    mock_agent.close = Mock()

    req2 = {
        "jsonrpc": "2.0",
        "id": 92,
        "method": "tools/call",
        "params": {
            "name": "laya_inspect_page",
            "arguments": {
                "url": "https://example.test/results",
                "session_id": "inspect-test",
            },
        },
    }
    res2 = mcp_server.handle_request(req2)
    assert res2["result"]["isError"] is False
    data2 = json.loads(res2["result"]["content"][0]["text"])
    assert data2["url"] == "https://example.test/results"
    assert "Select" in data2["fields_on_page"]
    mock_agent.navigate.assert_called_once_with("https://example.test/results")
    mock_agent.close.assert_not_called()


def test_mcp_session_lifecycle(mcp_server, monkeypatch):
    mock_agent = Mock()
    mock_agent.state = {
        "status": "ready",
        "goal": "Search for books",
        "page": {
            "url": "https://example.test/books",
            "title": "Book Search",
            "text": "Welcome to Book Store",
            "actions": [{"id": "1", "label": "Search Box"}],
        },
        "history": [],
        "plan": ["Search for books"],
        "plan_index": 0,
    }
    mock_agent.command = Mock(
        return_value={
            "status": "ready",
            "page": mock_agent.state["page"],
            "elements": [],
        }
    )
    mock_agent.close = Mock()

    monkeypatch.setattr("laya_ultrafast.mcp.Agent", Mock(return_value=mock_agent))

    # 1. Start session
    start_req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "laya_session_start",
            "arguments": {
                "url": "https://example.test/books",
                "goal": "Search for books",
                "session_id": "test-session",
            },
        },
    }
    res = mcp_server.handle_request(start_req)
    assert res["result"]["isError"] is False
    start_data = json.loads(res["result"]["content"][0]["text"])
    assert start_data["session_id"] == "test-session"
    assert start_data["status"] == "ready"

    # 2. List sessions
    list_req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "laya_list_sessions", "arguments": {}},
    }
    res = mcp_server.handle_request(list_req)
    list_data = json.loads(res["result"]["content"][0]["text"])
    assert list_data["count"] == 1
    assert list_data["sessions"][0]["session_id"] == "test-session"

    # 3. Step session
    mock_agent.state["history"].append(
        {"step": 1, "action": "Click Search", "kind": "click", "choice": "e1", "page_changed": True}
    )
    step_req = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "laya_session_step",
            "arguments": {"session_id": "test-session"},
        },
    }
    res = mcp_server.handle_request(step_req)
    step_data = json.loads(res["result"]["content"][0]["text"])
    assert step_data["latest_action"]["action"] == "Click Search"
    assert step_data["total_actions"] == 1

    # 4. Get session state
    state_req = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {
            "name": "laya_session_get_state",
            "arguments": {"session_id": "test-session"},
        },
    }
    res = mcp_server.handle_request(state_req)
    state_data = json.loads(res["result"]["content"][0]["text"])
    assert state_data["goal"] == "Search for books"
    assert len(state_data["history"]) == 1

    # 5. Close session
    close_req = {
        "jsonrpc": "2.0",
        "id": 14,
        "method": "tools/call",
        "params": {
            "name": "laya_session_close",
            "arguments": {"session_id": "test-session"},
        },
    }
    res = mcp_server.handle_request(close_req)
    close_data = json.loads(res["result"]["content"][0]["text"])
    assert close_data["closed"] is True
    mock_agent.close.assert_called_once()


def test_mcp_run_task_returns_plan_needed_when_no_plan(mcp_server, monkeypatch):
    """When a model calls laya_run_task without a plan, it must return plan_needed so the model makes the plan."""
    mock_agent = Mock()
    mock_agent.state = {
        "status": "ready",
        "goal": "Search flight to Paris",
        "page": {
            "url": "https://example.test/flights",
            "title": "Flight Booking",
            "text": "Where to? From: NYC. To: Paris.",
            "actions": [
                {"id": "e1", "kind": "fill", "label": "Where to?", "role": "combobox", "node": 1},
                {"id": "e2", "kind": "click", "label": "Search", "role": "button", "node": 2},
            ],
        },
        "history": [],
        "plan": ["Search flight to Paris"],
        "plan_index": 0,
        "elapsed_ms": 100,
    }
    mock_agent.policy = Mock(plan=None)
    mock_agent.command = Mock()
    monkeypatch.setattr("laya_ultrafast.mcp.Agent", Mock(return_value=mock_agent))

    req = {
        "jsonrpc": "2.0",
        "id": 15,
        "method": "tools/call",
        "params": {
            "name": "laya_run_task",
            "arguments": {
                "url": "https://example.test/flights",
                "goal": "Search flight to Paris",
                "session_id": "paris-session",
            },
        },
    }
    res = mcp_server.handle_request(req)
    assert res["result"]["isError"] is False
    data = json.loads(res["result"]["content"][0]["text"])
    assert data["status"] == "plan_needed"
    assert data["session_id"] == "paris-session"
    assert data["goal"] == "Search flight to Paris"
    assert "Where to?" in data["fields_on_page"]
    # Internal loop must NOT have ticked
    mock_agent.command.assert_not_called()


def test_mcp_rescue_task(mcp_server, monkeypatch):
    """Test corrective action execution with laya_rescue_task."""
    mock_agent = Mock()
    mock_browser = Mock()
    mock_agent.screenshots = False
    mock_agent.state = {
        "status": "rescue_needed",
        "goal": "Accept cookies and proceed",
        "browser": mock_browser,
        "page": {
            "url": "https://example.test",
            "title": "Welcome",
            "text": "Please accept cookies",
            "actions": [
                {"id": "btn-cookie", "kind": "click", "label": "Accept All", "role": "button", "node": 5},
            ],
        },
        "history": [],
    }
    mock_agent.policy = Mock(rescued=False)
    mock_browser.observe = Mock(return_value=mock_agent.state["page"])
    mcp_server.sessions["rescue-session"] = mock_agent

    req = {
        "jsonrpc": "2.0",
        "id": 16,
        "method": "tools/call",
        "params": {
            "name": "laya_rescue_task",
            "arguments": {
                "session_id": "rescue-session",
                "action": "click",
                "target": "1",
            },
        },
    }
    res = mcp_server.handle_request(req)
    assert res["result"]["isError"] is False
    data = json.loads(res["result"]["content"][0]["text"])
    assert data["status"] == "ready"
    assert mock_agent.policy.rescued is True
    mock_browser.act.assert_called_once()

