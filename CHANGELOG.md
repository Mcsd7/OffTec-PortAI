# Changelog

## 0.3.1 — Codex runtime fix
- Detect Codex through an absolute executable path and report Node runtime, credential-file presence and connection-test state without exposing credentials.
- Retry the Termux launcher only after confirming that inherited `LD_LIBRARY_PATH` causes a Node linker failure; preserve `HOME`, `PATH`, authentication and all other environment values.
- Add `/doctor` to recheck the environment and make a real read-only connectivity request in a temporary isolated workspace.
- Add `--skip-git-repo-check` for PortAI-authorized isolated workspaces while retaining Codex sandbox policies for normal tasks.
- Stream Codex execution with the corrected environment, redact likely credential values in saved logs, and terminate the complete process group on cancellation/timeouts.
- Apply the same runtime discovery, inherited environment and isolated-workspace trust handling to the existing PortAI Codex workspace runner.
- Validate real authenticated analysis, JSONL streaming, saved session/report/backup, and real process interruption on Termux ARM64.

## 0.4.0 — PORTAI AGENT
- Added a separate conversational PORTAI AGENT experience with welcome screen, Codex status, ten actionable quick prompts, project selection and chat history.
- Reused the existing Codex CLI command builders and sandbox; tasks now stream JSONL output, support interruption and resume saved Codex threads.
- Added private, atomic task and per-project memory persistence, restart recovery, local diagnostics, compatibility context, reports and workspace backups.
- Connected explicit build requests to the existing Android Build Manager or local CMake/Make/Gradle runner; streaming build logs and status are recorded.
- Added tests for task persistence, routing, project selection, diagnostics, Codex execution/resume, streaming, cancellation and security.

## 0.3.0
- Universal Translation Engine modular com extração e saída localizada para JSON, XML/Android, TXT, CSV, PO, RESX, properties, INI e YAML.
- Camada de provedores offline, adaptador por comando e plugin Python sem endpoints ou credenciais embutidos.
- Revisão explícita, validação de placeholders, backups de saídas existentes, metadados RTL e relatório de traduções.
- Tradutor de APK em workspace independente: Apktool, JADX complementar, revisão pelo UTE, rebuild, assinatura com keystore do usuário e validação.
- Diagnóstico de pacote/versão/ABIs, erros, arquivos modificados e limitações de split APKs e código nativo.
- Novo fluxo de tradução no menu principal; preservados os fluxos existentes de sessão, workspace, Codex e relatórios.
- AI Game Recreation Engine: análise heurística de engines, linguagens, dependências, controles, APIs desktop, gráficos e candidatos a adaptação Android; plano e relatórios.
- Integração opcional com sessão Codex persistente na cópia isolada, autorização de arquivos, backup/recuperação e progresso; build Gradle real validada quando aplicável.
- Localização offline centralizada da interface em pt-BR, en, es, ar e hi, com configuração persistente, suporte Unicode e direção bidirecional best-effort no terminal.
- Added modular platform metadata for all requested console generations and host destinations, with explicit SDK and compilation limits.
- Added read-only game-project analyzer, evidence-linked compatibility findings, host tool detection, bounded safe build execution, project registry and JSON/Markdown report writer.
- Core diagnostic modules use only Python's standard library.
- Added responsive Console Porting Studio navigation, project registry workflow, platform comparison and build runner.
- Codex tasks now receive structured diagnostic context and create workspace backups with changed-file reports.
- Workspace import skips symlinks to external files; Android CMake flow runs configure and build before APK verification.
- Added compact localized labels for the expanded mobile navigation in Spanish and Arabic.

## 0.2.1
- Busca automática por projetos de código em pastas comuns do Termux/armazenamento compartilhado.
- Seleção por número em vez de digitação de caminho absoluto.
- Lista `.exe` encontrados com aviso de que binários fechados não são convertidos diretamente em APK.
- Opção de análise e integração Codex usam o mesmo seletor.

## 0.2.0
- Interface Rich, análise estática e integração inicial com Codex CLI.
