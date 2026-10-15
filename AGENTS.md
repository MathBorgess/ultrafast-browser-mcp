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

<!-- mathai-repo-operations:start -->
## Resource cleanup and review

Existing repository-specific retention, immutable-evidence and review/check requirements remain in force. This section does not override longer retention or stricter preservation rules.

Before cleanup, inspect `git status --short`, `git worktree list`, tracked files, ignored/untracked files and active processes. Read this repository's build configuration and `.gitignore` to identify reproducible outputs. Produce a bounded dry-run list with paths, ownership, active-use checks and a recovery plan; remove only confirmed inactive regenerable residue. A directory being ignored does not make it disposable. Never use broad `git clean`, forced worktree removal or recursive deletion across projects.

Stop only temporary processes created by the current task after they are no longer needed. Keep ordinary operational logs for five days, except evidence needed for an open issue. Preserve sources, credentials, local/ignored work and all experiment/evaluation records awaiting human assessment. Age alone does not authorize deletion of projects or conversations. Use the owning application's archive/recovery mechanism for managed worktrees. Report measured cleanup results and unresolved items in short bullets.

### Code Review Rules

Focus on correctness, data loss, security, permission expansion, costs and broken external contracts. State the concrete trigger and consequence of a finding. Put formatting and deterministic checks in CI. Missing required evidence is unverified, not success. Preserve repository-specific human approval and merge rules; this section grants no new merge authority. Report useful architectural decisions separately from blocking risks, in plain language.

<!-- mathai-repo-operations:end -->
