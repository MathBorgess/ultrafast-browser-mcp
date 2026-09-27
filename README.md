# Ultrafast Browser MCP ⚡

**A fast, low-cost Browser MCP Server that empowers any intelligent agent to navigate the web at machine speed.**

> [!NOTE]
> This project originated as a fork of **[laya-ultrafast](https://github.com/ipenywis/laya-ultrafast)** (which itself ported **[jev-ultrafast](https://github.com/browser-use/jev-ultrafast) by [Browser Use](https://github.com/browser-use)** to local MLX). It now evolves independently as a dedicated, universal **Model Context Protocol (MCP)** server. While acknowledging the great foundation of the original works, this repository re-architects the loop for modern agent systems: external LLM agents retain full reasoning control (multi-step planning, page inspection, and obstacle rescue), while Laya runs locally to execute DOM actions at millisecond speeds with zero cloud per-step token cost.

> [!IMPORTANT]
> **Apple Silicon only.** Laya runs through [laya-mlx](https://github.com/mizorewww/laya-mlx), which needs an M-series Mac, macOS 14+, and Python 3.11+ (this project uses 3.12+). If you're looking to run it on a different OS/machine, check the original [Laya](https://github.com/NandhaKishorM/laya).

## Why Laya Ultrafast MCP?

Existing browser agents are either slow, expensive (burning frontier LLM tokens on every DOM click/scroll), or black boxes that run brittle static scripts. This project bridges the gap:

- **Ultra-low Cost & Zero Cloud Decision Overhead**: Decisions run locally via Laya on MLX (~33 ms per action on an M1 Max). No per-step API calls to frontier vision/multimodal models for clicking or typing.
- **Model-Driven Multi-Step Planning**: The controller model using the MCP is responsible for setting the goal and structuring the multi-step plan (`requirements`, `open`, `finish`, `is_final_step`). When an intermediate step completes (`status: "step_completed"`), control yields back to the controller model to review the page and plan the next step.
- **LLM Rescue on Obstacles**: When the local agent encounters obstacles (unexpected cookie banners, popups, or unfamiliar dynamic forms), it reports `status: "rescue_needed"` with candidate interactive elements, allowing the reasoning LLM to prescribe targeted corrective actions or update the plan.
- **Plug-and-Play MCP Interface**: Exposes standardized tools (`laya_run_task`, `laya_inspect_page`, `laya_rescue_task`, `laya_session_*`) over stdio MCP for instant integration into Cursor, Claude Desktop, Antigravity, or custom agent frameworks.
- **Hosted Fallback**: Set `DECISION_MODEL=typesafe` to switch back to the original hosted Jev policy if desired.

## Laya setup

Laya itself is documented in **[mizorewww/laya-mlx](https://github.com/mizorewww/laya-mlx)**, an independent MLX port of [Convai Innovations' Laya](https://github.com/NandhaKishorM/laya). Read it for requirements, checkpoints, benchmarks and troubleshooting.

This project installs `laya-mlx` as a dependency and uses the **`aac6fef/laya-typed-decisions-mlx`** checkpoint (421M parameters, 1,024-token context). Download it once:

```bash
uv sync                                          # installs laya-mlx and the `hf` command
uv run hf download aac6fef/laya-typed-decisions-mlx
```

Later runs load it from the Hugging Face cache and need no network for decisions. To use another checkpoint, set `LAYA_MODEL`. The laya-mlx README lists the options and their limits: for example, the English `laya` checkpoint has only a 512-token context.

## Quick start

```bash
git clone https://github.com/MathBorgess/ultrafast-browser-mcp.git
cd ultrafast-browser-mcp
uv sync
uv run hf download aac6fef/laya-typed-decisions-mlx
cp .env.example .env    # then set TEXT_MODEL_API_KEY, or point TEXT_MODEL_BASE_URL at a local server
uv run laya
```

Open **http://127.0.0.1:8766**, choose a scenario, and click **Start demo → Run automatically**.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), just as in jev-ultrafast. Enable remote debugging at `chrome://inspect/#remote-debugging`, and run `uv run browser-harness --doctor` if the connection fails.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DECISION_MODEL` | `laya` | `laya` for local decisions, `typesafe` for the original hosted Jev policy |
| `LAYA_MODEL` | `aac6fef/laya-typed-decisions-mlx` | Laya checkpoint, from the Hub or a local path |
| `TEXT_MODEL_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint. `localhost` endpoints need no key |
| `TEXT_MODEL` | `inception/mercury-2.5` | Model that plans the task once per run |
| `TEXT_MODEL_API_KEY` | — | Required for remote endpoints |
| `TEXT_MODEL_REASONING` | `none` | Turns reasoning off for faster planning |
| `TYPESAFE_API_KEY`, `TYPESAFE_MODEL` | — | Only for `DECISION_MODEL=typesafe` |

For a fully offline setup with [Ollama](https://ollama.com):

```bash
TEXT_MODEL_BASE_URL=http://localhost:11434/v1
TEXT_MODEL=gemma4:latest
```

## Demos

| Scenario | Command |
| --- | --- |
| Google Flights (checks the final page) | `uv run --env-file .env python examples/flights.py --date 2026-10-20 --keep-open` |
| Skyscanner (checks the final page) | `uv run --env-file .env python examples/skyscanner.py --date 2026-10-20 --keep-open` |
| Any site and goal | `uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'` |

Flight sites only offer future dates, so pass `--date`. It defaults to 30 days ahead. The examples never select or book a flight.

**Skyscanner** may show an "Are you a person or a robot?" check, especially to automated or headless browsers. The agent does not try to get past it. Run it in your everyday Chrome and solve the check yourself if it appears. Skyscanner also ticks "Add a place to stay" by default, so its goal says "without adding a place to stay".

## MCP Server (Model Context Protocol)

Connect your external AI agents (e.g. Claude Desktop, Cursor, Antigravity, Cline, Windsurf) to Laya Ultrafast over stdio MCP:

```bash
uv run laya-mcp
# or:
uv run python -m laya_ultrafast.mcp
```

### Configuration for Claude Desktop / Cursor / Antigravity

Add to your MCP settings file (e.g. `claude_desktop_config.json` or `.gemini/antigravity-ide/mcp_config.json`):

```json
{
  "mcpServers": {
    "ultrafast-browser": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/ultrafast-browser-mcp",
        "run",
        "laya-mcp"
      ]
    }
  }
}
```

### How It Works: The Calling Model Plans, Laya Executes

When using the MCP server, **you (the external model using the MCP) are in control of setting the goal and making the plan**:
1. **Inspect / Probe**: Call `laya_inspect_page` or `laya_run_task` without a plan. Laya loads the page and returns interactive form fields, buttons, and visible text with `status: "plan_needed"`.
2. **Plan**: As the reasoning model, you define the plan: `requirements` (concrete values to fill/select), `open` (specific item/link to click), `finish` (visible completion condition), and `is_final_step`.
3. **Execute**: Call `laya_run_task` with your `goal` and `plan`. Laya's local MLX model executes the fast browser loop locally (~20ms per action, zero cloud tokens).
4. **Multi-Step & Rescue**: When a step finishes (`status: "step_completed"`), Laya returns control so you can plan the next step. If an obstacle or dialog blocks progress (`status: "rescue_needed"`), inspect candidate elements and call `laya_rescue_task` to dismiss or reroute.

### Available Tools

- **`laya_run_task`**: Execute a browser task on any website using Laya's ultrafast local decisions. The calling model provides the goal and plan.
- **`laya_inspect_page`**: Navigate to any URL (or inspect an active session) and return accessible form fields, buttons, page title, URL, and visible text excerpt.
- **`laya_rescue_task`**: Prescribe a corrective action (`click`, `fill`, `wait`, or `replan`) when a task reports `status: "rescue_needed"`.
- **`laya_session_start`**: Start an interactive browser session on any website with a goal for step-by-step navigation.
- **`laya_session_step`**: Execute one step (`tick`) in an active browser session.
- **`laya_session_get_state`**: Inspect current page title, URL, visible text excerpt, and action history.
- **`laya_session_close`**: Close an active session and release browser resources.
- **`laya_list_sessions`**: List all open interactive browser sessions.

## Measurements

These were measured on an M1 Max with `inception/mercury-2.5` on OpenRouter as the text model. Timing includes the planning call (~1–1.5 s):

| Task | Result | Time |
| --- | --- | --- |
| Google Flights, one way Zürich → London, checked by `examples/flights.py` | 5/5 passed | 7.5–12.1 s |
| Wikipedia: open the Gödel's incompleteness theorems article | 2/2 | ~3–5 s |
| Local hotel fixture: filters, search, open Casa Flora | 2/2 | ~1.7 s |
| Local reading-room fixture: open the matching article | 2/2 | ~1 s |
| Skyscanner | Form filled end to end, then blocked by the robot check in headless testing | — |

This is a small set of repeated tasks, not a general reliability benchmark. The original Jev measurements, video and methodology are in [jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

## Limitations

- Runs only on Apple Silicon, because Laya runs through MLX.
- The Laya policy is new and tested on few sites. The original jev-ultrafast limits still apply: shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling and arbitrary keyboard widgets are out of scope. See [its README](https://github.com/browser-use/jev-ultrafast#evidence-and-limits).
- A `DONE` decision is not proof of success. The examples check the final page independently.
- Planning quality depends on the text model. `inception/mercury-2.5` sometimes returns malformed JSON, so the planner retries up to 3 times.

## Everything else

The action space, DOM snapshot, executor, freshness and occlusion checks, the inspector, and the performance work all come from jev-ultrafast. **See [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** for how they work, the design notes, and the original evidence. The files in [`docs/`](docs/) are the original project's records and describe the hosted Jev runs.

Development checks are the same as upstream:

```bash
uv run ruff check .
uv run pytest            # offline: a fake stands in for Laya; no downloads or paid calls
node --check laya_ultrafast/static/app.js
node --check laya_ultrafast/snapshot.js
uv build
```

## Credits

- **[jev-ultrafast](https://github.com/browser-use/jev-ultrafast)** by [Browser Use](https://github.com/browser-use): the original project. This repository is a clone and a port of it.
- **[Browser Harness](https://github.com/browser-use/browser-harness)** by Browser Use: the Chrome connection.
- **[laya-mlx](https://github.com/mizorewww/laya-mlx)**: the MLX runtime and converted checkpoints for Laya.
- **[Laya](https://github.com/NandhaKishorM/laya)** by Convai Innovations and contributors: the model and its weights.
- **[TypeSafe Jev](https://docs.typesafe.ai/introduction)**: the hosted policy the original project uses, still available here as `DECISION_MODEL=typesafe`.

## License

[MIT](LICENSE), unchanged from the original: Copyright (c) 2026 Browser Use. Laya and laya-mlx are Apache-2.0 under their own licenses. See their repositories.
