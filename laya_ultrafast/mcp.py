"""Model Context Protocol (MCP) server for Laya Ultrafast.

Exposes Laya Ultrafast browser automation over stdio JSON-RPC 2.0, allowing external AI agents
(e.g., Claude, Cursor, Antigravity, OpenAI Codex, Windsurf) to navigate any website and execute
arbitrary natural-language goals autonomously or step-by-step.

The calling model is responsible for setting the goal and making the plan (requirements, item to open,
and finish condition). Laya executes the fast browser loop locally using open weights on Apple Silicon.
"""

import atexit
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from .agent import Agent
from .laya import display, observed, plannable
from .questions import MAX_STEPS

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "laya-ultrafast"
SERVER_VERSION = "0.1.0"


def load_environment():
    """Load variables from .env in the current or parent directory if present."""
    search_dirs = [Path.cwd(), Path(__file__).resolve().parent.parent]
    for directory in search_dirs:
        env_file = directory / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            break


TOOLS = [
    {
        "name": "laya_run_task",
        "description": (
            "Execute a browser task on any website using Laya's ultrafast local decisions. "
            "You (the model) are responsible for setting the goal and making the plan (requirements, "
            "open item, and finish condition). Laya executes the fast browser loop locally, mapping fields "
            "and submitting, then returns the result when complete ('done'), when a step is finished "
            "('step_completed'), or when an obstacle requires diagnosis ('rescue_needed' or 'blocked')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to (optional if continuing an existing session_id).",
                },
                "goal": {
                    "type": "string",
                    "description": "Natural-language goal to accomplish on that site.",
                },
                "plan": {
                    "type": "object",
                    "description": (
                        "The plan created by you (the model). Defines concrete requirements to set, "
                        "item to open, and visible finish condition."
                    ),
                    "properties": {
                        "requirements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "what": {
                                        "type": "string",
                                        "description": (
                                            "Field or setting name to set (e.g. 'departure', 'destination', 'search')."
                                        ),
                                    },
                                    "value": {
                                        "type": "string",
                                        "description": "Exact value to set (e.g. 'London', '2027-05-10', 'checked').",
                                    },
                                },
                                "required": ["what", "value"],
                            },
                            "description": "List of concrete field values to set.",
                        },
                        "open": {
                            "type": ["string", "null"],
                            "description": "Title or name of a specific item/result to open, or null.",
                        },
                        "finish": {
                            "type": "string",
                            "description": (
                                "One sentence describing what the page visibly shows when this step is complete."
                            ),
                        },
                        "is_final_step": {
                            "type": "boolean",
                            "description": (
                                "True if this step completes the overall goal. False if subsequent steps remain."
                            ),
                            "default": True,
                        },
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of short descriptions of each planned sequential step.",
                        },
                    },
                    "required": ["requirements", "finish"],
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Optional session identifier to continue an ongoing multi-step task (default: 'default')."
                    ),
                    "default": "default",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum number of browser actions before returning (default: 30, max: 60).",
                    "default": 30,
                },
                "screenshots": {
                    "type": "boolean",
                    "description": "Whether to capture base64 screenshot frames in the history (default: false).",
                    "default": False,
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "laya_inspect_page",
        "description": (
            "Navigate to any URL (or inspect current page in an active session) and return "
            "accessible form fields, buttons, page title, URL, and visible text excerpt. "
            "Call this first so you (the model) can inspect the actual fields on the page before making the plan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to and inspect.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session identifier to keep the browser open (default: 'default').",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "laya_rescue_task",
        "description": (
            "Prescribe a corrective action or revised plan when a task reports status 'rescue_needed' "
            "(e.g., dismissing an unexpected cookie overlay, clicking a popup close button, or updating requirements)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The active session identifier to rescue (default: 'default').",
                    "default": "default",
                },
                "action": {
                    "type": "string",
                    "description": "Corrective action: 'click', 'fill', 'wait', or 'replan'.",
                    "enum": ["click", "fill", "wait", "replan"],
                },
                "target": {
                    "type": "string",
                    "description": "Element index to click or fill (from candidate_elements).",
                },
                "text": {
                    "type": "string",
                    "description": "Text to enter if action is 'fill'.",
                },
                "revised_plan": {
                    "type": "object",
                    "description": "Revised plan object with requirements, open, and finish if action is 'replan'.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "laya_session_start",
        "description": (
            "Start an interactive browser session on any website with a goal for step-by-step execution. "
            "Allows the agent to inspect the page and execute actions incrementally."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The starting website URL.",
                },
                "goal": {
                    "type": "string",
                    "description": "The natural-language goal for this session.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session identifier (default: 'default').",
                    "default": "default",
                },
            },
            "required": ["url", "goal"],
        },
    },
    {
        "name": "laya_session_step",
        "description": (
            "Execute one step (predict + act) in an active interactive browser session. "
            "Returns the action performed, the new page state, and whether the task has completed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session identifier to step (default: 'default').",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "laya_session_get_state",
        "description": (
            "Get the current page state, title, URL, visible text excerpt, and executed action history "
            "for an active session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session identifier to query (default: 'default').",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "laya_session_close",
        "description": "Close an active browser session and release all browser resources.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session identifier to close (default: 'default').",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "laya_list_sessions",
        "description": "List all currently open interactive browser sessions and their statuses.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class LayaMCPServer:
    def __init__(self):
        self.sessions: dict[str, Agent] = {}
        self.raw_stdout = sys.stdout

    def log(self, message: str):
        """Write debug/log information to stderr so stdout remains clean JSON-RPC."""
        sys.stderr.write(f"[laya-mcp] {message}\n")
        sys.stderr.flush()

    def close_all_sessions(self):
        """Close all running browser sessions."""
        for session_id, agent in list(self.sessions.items()):
            try:
                self.log(f"Closing session '{session_id}'...")
                agent.close()
            except Exception as e:
                self.log(f"Error closing session '{session_id}': {e}")
        self.sessions.clear()

    # Tool Implementations ------------------------------------------------------------------------------------

    def tool_run_task(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        url = args.get("url", "").strip()
        goal = args.get("goal", "").strip()
        plan = args.get("plan")
        max_steps = min(int(args.get("max_steps", 30)), MAX_STEPS)
        screenshots = bool(args.get("screenshots", False))

        agent = self.sessions.get(session_id)
        if agent is None:
            if not url:
                raise ValueError("Parameter 'url' is required to start a new task")
            if not goal:
                raise ValueError("Parameter 'goal' is required")
            self.log(f"Starting agent for session '{session_id}' on {url}: {goal!r}")
            agent = Agent(url, goal, screenshots=screenshots, plan=plan)
            if getattr(agent, "policy", None):
                agent.policy.plan_meta = {"model": "mcp_client", "latency_ms": 0}
            self.sessions[session_id] = agent
        else:
            if url and hasattr(agent, "navigate") and url != agent.state.get("page", {}).get("url"):
                self.log(f"Navigating session '{session_id}' to {url}")
                agent.navigate(url)
            if goal:
                agent.state["goal"] = goal
                if getattr(agent, "policy", None):
                    agent.policy.goal = goal

        if plan:
            if hasattr(agent, "set_plan"):
                agent.set_plan(plan)
            elif getattr(agent, "policy", None):
                agent.policy.plan = plan
            if agent.state.get("status") in {"step_completed", "rescue_needed"}:
                agent.state["status"] = "ready"

        # The model using the MCP is responsible for setting the goal and making the plan.
        # If no plan has been provided yet by the model, return the loaded page state with status "plan_needed".
        has_plan = (
            getattr(agent, "policy", None) and agent.policy.plan is not None
        ) or agent.state.get("goal_plan") is not None
        if not has_plan:
            page = agent.state.get("page", {})
            fresh_elements = observed(page)
            fields_on_page = list(dict.fromkeys(display(e) for e in fresh_elements if plannable(e)))
            return {
                "status": "plan_needed",
                "session_id": session_id,
                "goal": agent.state.get("goal"),
                "message": (
                    "Page loaded. As the controller model using this MCP, you are responsible for making the plan. "
                    "Please call 'laya_run_task' with session_id and a 'plan' containing 'requirements' "
                    "and 'finish' (and optionally 'open', 'is_final_step'). "
                    "Observed interactive fields are listed in 'fields_on_page'."
                ),
                "url": page.get("url"),
                "title": page.get("title"),
                "fields_on_page": fields_on_page,
                "page_text_excerpt": page.get("text", "")[:2000],
            }

        # Run ticks
        step_count = 0
        while (
            agent.state["status"] not in {"done", "blocked", "step_completed", "rescue_needed"}
            and step_count < max_steps
        ):
            agent.command("tick")
            step_count += 1

        status = agent.state["status"]
        page = agent.state.get("page", {})
        history = agent.state.get("history", [])
        actions_summary = [
            {
                "step": h.get("step"),
                "action": h.get("action"),
                "kind": h.get("kind"),
                "choice": h.get("choice"),
                "page_changed": h.get("page_changed"),
            }
            for h in history
        ]

        fresh_elements = observed(page)
        fields_on_page = list(dict.fromkeys(display(e) for e in fresh_elements if plannable(e)))

        result = {
            "status": status,
            "session_id": session_id,
            "goal": agent.state.get("goal"),
            "final_url": page.get("url"),
            "page_title": page.get("title"),
            "steps_completed": agent.state.get("plan_index", 0),
            "total_actions": len(history),
            "actions": actions_summary,
            "plan": agent.state.get("goal_plan", agent.state.get("plan", [agent.state.get("goal")])),
            "page_text_excerpt": page.get("text", "")[:2500],
            "fields_on_page": fields_on_page,
            "elapsed_ms": agent.state.get("elapsed_ms", 0),
        }

        if status == "step_completed":
            result["message"] = (
                "Step completed! Requirements were set and verified. "
                "Inspect 'fields_on_page' and provide the plan for the next step using 'laya_run_task'."
            )
        elif status == "rescue_needed":
            result["message"] = (
                "Agent is stuck on an obstacle. Inspect 'candidate_elements' and prescribe a corrective action "
                "with 'laya_rescue_task' or provide a revised plan."
            )
            result["candidate_elements"] = [
                {"index": str(e.get("index")), "label": str(e.get("label", ""))[:60], "role": str(e.get("role", ""))}
                for e in fresh_elements[:25]
            ]
        elif status == "done":
            result["message"] = "Task completed successfully!"
            try:
                agent.close()
            except Exception:
                pass
            self.sessions.pop(session_id, None)

        return result

    def tool_inspect_page(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        url = args.get("url", "").strip()
        agent = self.sessions.get(session_id)
        if agent is None:
            if not url:
                raise ValueError("Parameter 'url' is required to inspect a new page")
            agent = Agent(url, "Inspect page")
            self.sessions[session_id] = agent
        elif url and url != agent.state.get("page", {}).get("url"):
            if hasattr(agent, "navigate"):
                agent.navigate(url)
            else:
                agent.close()
                agent = Agent(url, "Inspect page")
                self.sessions[session_id] = agent

        page = agent.state.get("page", {})
        fresh_elements = observed(page)
        fields_on_page = list(dict.fromkeys(display(e) for e in fresh_elements if plannable(e)))
        return {
            "session_id": session_id,
            "url": page.get("url"),
            "title": page.get("title"),
            "fields_on_page": fields_on_page,
            "page_text_excerpt": page.get("text", "")[:2500],
            "message": "Page inspected. Formulate your goal and plan, then call 'laya_run_task'.",
        }

    def tool_rescue_task(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        agent = self.sessions.get(session_id)
        if not agent:
            raise ValueError(f"No active session '{session_id}' found to rescue")
        action = args.get("action", "click").lower()
        target = args.get("target")
        text = args.get("text")
        revised_plan = args.get("revised_plan")

        page = agent.state.get("page", {})
        elements = observed(page)
        target_elem = next((e for e in elements if str(e["index"]) == str(target)), None) if target else None

        if action in ("click", "fill") and target_elem:
            act_obj = target_elem["actions"].get(action) or next(iter(target_elem["actions"].values()), None)
            if act_obj:
                agent.state["browser"].act(act_obj, page, text=text)
                agent.state["history"].append(
                    {
                        "step": len(agent.state["history"]) + 1,
                        "action": act_obj["label"],
                        "kind": action,
                        "choice": act_obj["id"],
                    }
                )
                agent.state["page"] = agent.state["browser"].observe(screenshot=agent.screenshots)

        if revised_plan:
            agent.set_plan(revised_plan)
        else:
            agent.state["status"] = "ready"
            if agent.policy:
                agent.policy.rescued = True

        return {
            "session_id": session_id,
            "status": agent.state["status"],
            "url": agent.state["page"]["url"],
            "title": agent.state["page"]["title"],
            "message": "Rescue action executed. Continue task with 'laya_run_task'.",
        }

    def tool_session_start(self, args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url", "").strip()
        goal = args.get("goal", "").strip()
        session_id = args.get("session_id", "default").strip() or "default"
        if not url:
            raise ValueError("Parameter 'url' is required")
        if not goal:
            raise ValueError("Parameter 'goal' is required")

        if session_id in self.sessions:
            self.log(f"Closing existing session '{session_id}' before replacement")
            try:
                self.sessions[session_id].close()
            except Exception:
                pass
            del self.sessions[session_id]

        self.log(f"Starting session '{session_id}' on {url}: {goal!r}")
        agent = Agent(url, goal)
        if getattr(agent, "policy", None):
            agent.policy.plan_meta = {"model": "mcp_client", "latency_ms": 0}
        self.sessions[session_id] = agent
        page = agent.state.get("page", {})
        actions = page.get("actions", [])
        return {
            "session_id": session_id,
            "status": agent.state["status"],
            "url": page.get("url", url),
            "title": page.get("title", ""),
            "interactive_elements_count": len(actions),
            "message": f"Session '{session_id}' started. Call 'laya_session_step' to advance.",
        }

    def tool_session_step(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        agent = self.sessions.get(session_id)
        if not agent:
            raise ValueError(f"No active session '{session_id}'. Call 'laya_session_start' first.")

        if agent.state["status"] in {"done", "blocked"}:
            return {
                "session_id": session_id,
                "status": agent.state["status"],
                "message": f"Session has already reached terminal status '{agent.state['status']}'.",
                "final_url": agent.state["page"]["url"],
                "page_title": agent.state["page"]["title"],
            }

        snapshot = agent.command("tick")
        last_action = agent.state["history"][-1] if agent.state["history"] else None
        page = agent.state.get("page", {})
        return {
            "session_id": session_id,
            "status": snapshot["status"],
            "latest_action": (
                {
                    "step": last_action.get("step"),
                    "action": last_action.get("action"),
                    "kind": last_action.get("kind"),
                    "choice": last_action.get("choice"),
                    "page_changed": last_action.get("page_changed"),
                }
                if last_action
                else None
            ),
            "url": page.get("url"),
            "title": page.get("title"),
            "total_actions": len(agent.state["history"]),
            "plan_index": agent.state.get("plan_index", 0),
            "page_text_excerpt": page.get("text", "")[:1000],
        }

    def tool_session_get_state(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        agent = self.sessions.get(session_id)
        if not agent:
            raise ValueError(f"No active session '{session_id}'. Call 'laya_session_start' first.")

        page = agent.state.get("page", {})
        history = agent.state.get("history", [])
        return {
            "session_id": session_id,
            "goal": agent.state.get("goal"),
            "status": agent.state.get("status"),
            "url": page.get("url"),
            "title": page.get("title"),
            "total_actions": len(history),
            "plan": agent.state.get("plan", []),
            "plan_index": agent.state.get("plan_index", 0),
            "history": [
                {
                    "step": h.get("step"),
                    "action": h.get("action"),
                    "kind": h.get("kind"),
                    "choice": h.get("choice"),
                }
                for h in history
            ],
            "page_text_excerpt": page.get("text", "")[:2000],
        }

    def tool_session_close(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.get("session_id", "default").strip() or "default"
        agent = self.sessions.pop(session_id, None)
        if not agent:
            return {"session_id": session_id, "closed": False, "message": "Session was not active."}
        agent.close()
        return {"session_id": session_id, "closed": True, "message": f"Session '{session_id}' closed."}

    def tool_list_sessions(self, _args: dict[str, Any]) -> dict[str, Any]:
        sessions_info = []
        for session_id, agent in self.sessions.items():
            page = agent.state.get("page", {})
            sessions_info.append(
                {
                    "session_id": session_id,
                    "goal": agent.state.get("goal"),
                    "status": agent.state.get("status"),
                    "url": page.get("url"),
                    "title": page.get("title"),
                    "total_actions": len(agent.state.get("history", [])),
                }
            )
        return {"sessions": sessions_info, "count": len(sessions_info)}

    # Request Dispatcher --------------------------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Dispatch a tool call and return (result_text, is_error)."""
        handlers = {
            "laya_run_task": self.tool_run_task,
            "laya_inspect_page": self.tool_inspect_page,
            "laya_rescue_task": self.tool_rescue_task,
            "laya_session_start": self.tool_session_start,
            "laya_session_step": self.tool_session_step,
            "laya_session_get_state": self.tool_session_get_state,
            "laya_session_close": self.tool_session_close,
            "laya_list_sessions": self.tool_list_sessions,
        }
        handler = handlers.get(name)
        if not handler:
            return f"Error: Unknown tool '{name}'", True
        try:
            result = handler(arguments)
            return json.dumps(result, indent=2), False
        except Exception as e:
            self.log(f"Error executing tool '{name}': {e}")
            return f"Error executing {name}: {e}", True

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request and produce a response dictionary, or None for notifications."""
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}

        # Handle notifications (no response needed)
        if req_id is None:
            if method == "notifications/initialized":
                self.log("Client sent notifications/initialized")
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            content_text, is_error = self.call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": content_text}],
                    "isError": is_error,
                },
            }

        elif method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}

        elif method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }

    def run_stdio(self):
        """Run the stdio JSON-RPC loop until stdin is closed."""
        # Redirect sys.stdout to sys.stderr so any stray prints from dependencies go to stderr
        self.raw_stdout = sys.stdout
        sys.stdout = sys.stderr

        atexit.register(self.close_all_sessions)

        def signal_handler(_sig, _frame):
            self.log("Received termination signal, cleaning up...")
            self.close_all_sessions()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        self.log(f"{SERVER_NAME} v{SERVER_VERSION} MCP server listening on stdio...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as err:
                self.log(f"Invalid JSON received: {err}")
                error_response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                self.raw_stdout.write(json.dumps(error_response) + "\n")
                self.raw_stdout.flush()
                continue

            response = self.handle_request(request)
            if response is not None:
                self.raw_stdout.write(json.dumps(response) + "\n")
                self.raw_stdout.flush()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("Laya Ultrafast MCP Server")
        print("\nUsage:")
        print("  laya-mcp                Run the MCP server over stdio")
        print("  python -m laya_ultrafast.mcp")
        print("\nTools provided:")
        for tool in TOOLS:
            print(f"  - {tool['name']}: {tool['description'][:80]}...")
        sys.exit(0)

    load_environment()
    server = LayaMCPServer()
    server.run_stdio()


if __name__ == "__main__":
    main()
