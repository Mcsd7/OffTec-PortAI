# OffTec PortAI architecture

`portai.py` remains the Rich CLI and preserves its Codex CLI workflow. `portai_core/` contains side-effect-limited, importable capabilities: platform metadata (`platforms`), bounded static discovery (`analyzer`), conservative source/target rules (`compatibility`), local tool discovery and argv-only builds (`build`), local recent-project metadata (`projects`), and JSON/Markdown exports (`reports`).

The analyzer and compatibility engine provide evidence and recommendations; neither asserts that a port compiles. Console builds require authorized vendor SDKs and are never attempted by generic tool discovery. Codex edits continue to run inside a user-approved isolated workspace; the original project remains read-only.

Core modules use only Python's standard library. Rich remains the CLI's sole required third-party dependency. Console width and `Console` wrapping govern compact mobile layouts.
