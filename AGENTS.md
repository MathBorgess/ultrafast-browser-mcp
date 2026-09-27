# Ultrafast Browser MCP (MathBorgess/ultrafast-browser-mcp)

Independent repository evolved from `laya-ultrafast` and `jev-ultrafast`.
Keep the loop small: page -> indexed elements -> operation + target -> execution.

### Repository & Remotes Policy
- **Active repository:** `origin` points to `https://github.com/MathBorgess/ultrafast-browser-mcp.git`.
- **Target branches:** Push only to `origin` (e.g. `feat/*`, `main`).
- **NEVER send PRs or push code back to upstream (`ipenywis/laya-ultrafast` or `browser-use/jev-ultrafast`)**. This project is an independent product and all features, fixes, and reports belong exclusively to `MathBorgess/ultrafast-browser-mcp`.

### Core Architecture & Guidelines
- **MCP Server first:** Expose fast local browser capabilities over stdio MCP (`laya_run_task`, `laya_inspect_page`, `laya_rescue_task`, `laya_session_*`).
- **Model-driven planning:** The controller model using the MCP sets the goal and plans steps (`requirements`, `open`, `finish`, `is_final_step`). Do not hardcode site-specific plans or field values.
- **Multi-step & LLM Rescue:** `STEP` transitions control to the calling model for the next milestone. `RESCUE` escalates obstacles (overlays, popups) for diagnosis via `candidate_elements`.
- **SPA & Hydration awareness:** Use `wait_for_settled()` for asynchronous client-side auth/DOM rehydration (React, Supabase, Lovable, Next.js). Reuse active session tabs via `agent.navigate()` instead of destroying sessions.
- **Local Laya decisions:** Laya (421M MLX) answers narrow typed questions locally (~30ms, zero cloud tokens).
- **Safe actions:** Targets must map to observed elements and supported operations. Never emit raw selectors or executable code. Never retry a browser mutation. Log execution before observing results.
- **Reports:** Save experiment summaries and benchmark results in the `reports/` directory.
- **Security & Privacy:** Keep credentials server-side and `.env` ignored. Offline unit tests must not call paid APIs.
- **Commit policy:** Do not commit or push unless explicitly requested by the user.

Checks: uv run ruff check ., uv run pytest, node --check laya_ultrafast/static/app.js, node --check laya_ultrafast/snapshot.js, uv build.

