# OffTec PortAI architecture

`portai.py` remains the Rich CLI and preserves its Codex CLI workflow. `portai_core/` contains side-effect-limited, importable capabilities: platform metadata (`platforms`), bounded static discovery (`analyzer`), conservative source/target rules (`compatibility`), local tool discovery and argv-only builds (`build`), local recent-project metadata (`projects`), and JSON/Markdown exports (`reports`).

The analyzer and compatibility engine provide evidence and recommendations; neither asserts that a port compiles. Console builds require authorized vendor SDKs and are never attempted by generic tool discovery. Codex edits run inside an isolated workspace; managed workspaces are backed up before each task, structured project/tool context is attached, and changed files are compared afterward. The original project remains read-only.

`portai_core.agent` is the task orchestration layer, not a second model integration. It uses command builders from `portai.py`, invokes the same Codex CLI with JSONL output, and persists task, session, project memory, backup and changed-file metadata under the user data directory. `portai_agent_ui.py` owns the separate chat experience and delegates diagnostics, reports and builds to existing core modules. The Codex process runs in a worker thread; output is streamed to Rich and Ctrl+C sends termination. Build requests use the existing Android APK validator or the core local build runner. Task JSON and generated reports use owner-only file permissions.

Core modules use only Python's standard library. Rich remains the CLI's sole required third-party dependency. Console width and `Console` wrapping govern compact mobile layouts.
