# Development progress

- Last completed: PORTAI Agent chat UI redesign for OffTec PortAI 0.4.0, committed as `b3a11de` and published to `origin/main`.
- Architecture: Rich remains the application launcher; selecting PORTAI AGENT starts a stdlib HTTP service bound to loopback and opens the responsive local web chat. AgentWebService delegates to the existing Codex runtime, AgentOrchestrator, AgentStore, workspace manager, Build Manager and report writer.
- Implemented: continuous conversations and persistent history; SSE Codex text/activity updates; new/recent conversation navigation; optional project attachment; Codex status and recheck; queue, stop and saved-session resume controls; mobile layout; Markdown/code rendering; loopback/token protections.
- Tests: `python -m unittest discover -s tests` passes 84/84. `tests/test_agent_web.py` passes 6/6, including HTTP API, controlled Codex CLI process, streaming deltas, persistent response/task/report, optional project attachment and responsive/control surface checks. `python -m compileall`, `env -u LD_LIBRARY_PATH node --check portai_agent_web/app.js` and `git diff --check` pass.
- Docs: `docs/AGENT_CHAT.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`; version values updated to 0.4.0. The pre-existing uncommitted README tutorial is preserved and not staged.
- Modified/new implementation files: `portai.py`, `portai_core/agent_web.py`, `portai_core/__init__.py`, `portai_agent_web/{index.html,app.css,app.js}`, `tests/test_agent_web.py`, `CHANGELOG.md`, `PROGRESS.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_CHAT.md`.
- Next exact task: optional browser-level visual testing if a browser automation environment is added; no release action remains.
- Known limits: browser directory picker is not available in this dependency-free local interface; paths can be entered manually or selected from registered projects. Codex must be installed and authenticated for real agent answers. No actual browser automation is configured.
- Pending dependencies: no additional dependencies; compatible SDK/toolchains remain project-specific.
