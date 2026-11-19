# OffTec PortAI architecture

`portai.py` remains the Rich CLI and preserves its Codex CLI workflow. `portai_core/` contains side-effect-limited, importable capabilities: platform metadata (`platforms`), bounded static discovery (`analyzer`), conservative source/target rules (`compatibility`), local tool discovery and argv-only builds (`build`), local recent-project metadata (`projects`), and JSON/Markdown exports (`reports`).

The analyzer and compatibility engine provide evidence and recommendations; neither asserts that a port compiles. Console builds require authorized vendor SDKs and are never attempted by generic tool discovery. Codex edits run inside an isolated workspace; managed workspaces are backed up before each task, structured project/tool context is attached, and changed files are compared afterward. The original project remains read-only.

`portai_core.agent` is the task orchestration layer, not a second model integration. It uses command builders from `portai.py`, invokes the same Codex CLI with JSONL output, and persists task, session, project memory, backup and changed-file metadata under the user data directory. `portai_agent_ui.py` owns the separate chat experience and delegates diagnostics, reports and builds to existing core modules. The Codex process runs in a worker thread; output is streamed to Rich and Ctrl+C sends termination. Build requests use the existing Android APK validator or the core local build runner. Task JSON and generated reports use owner-only file permissions.

`portai_core.codex_runtime` resolves the installed CLI to an absolute path and probes it with inherited authentication. If the probe confirms an Android dynamic-loader error caused by inherited `LD_LIBRARY_PATH`, it retries with only that variable removed; the Termux package wrapper then applies its own native-binary library-path handling. `/doctor` performs a real read-only Codex request in a temporary workspace. Both the Agent and legacy PortAI workspace runner use this runtime environment and skip only the Git-trust check for PortAI-authorized workspace copies. Agent runs start a process session so cancellation/timeouts terminate the wrapper and native child together. Likely credential strings are redacted before task logs are persisted.

Core modules use only Python's standard library. Rich remains the CLI's sole required third-party dependency. Console width and `Console` wrapping govern compact mobile layouts.

## PORTAI Agent chat web local

A entrada do PORTAI Agent abre `portai_core.agent_web`, um servidor HTTP da biblioteca padrão vinculado somente a `127.0.0.1` e uma interface estática responsiva em `portai_agent_web/`. O navegador envia mensagens e eventos SSE para uma API local protegida por token aleatório por execução; não há serviço remoto nem dependência web adicional.

`AgentWebService` orquestra uma fila por conversa e persiste mensagens em `PORTAI_AGENT_HOME/conversations/`. Tarefas, sessões Codex, backups e memória por projeto continuam no `AgentStore` e `AgentOrchestrator` existentes. O serviço reutiliza os construtores de comando e o ambiente detectado pelo runtime; saída JSONL do Codex é convertida em deltas, atividade e etapa observável. Projetos externos são associados ao fluxo existente de workspace isolada. Conversas iniciadas sem pasta usam um workspace dedicado vazio e podem receber projeto depois.

A interface oferece conversas anteriores, mensagens contínuas, ações sugeridas, anexação pelo caminho ou projetos recentes, status do runtime, diagnóstico real, interrupção e retomada de sessão Codex. O servidor não aceita conexões remotas e não fornece um seletor de arquivos nativo do navegador: no Termux, o usuário informa o caminho absoluto ou escolhe um projeto já registrado.
