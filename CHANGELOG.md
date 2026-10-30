# Changelog

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

## 0.2.1
- Busca automática por projetos de código em pastas comuns do Termux/armazenamento compartilhado.
- Seleção por número em vez de digitação de caminho absoluto.
- Lista `.exe` encontrados com aviso de que binários fechados não são convertidos diretamente em APK.
- Opção de análise e integração Codex usam o mesmo seletor.

## 0.2.0
- Interface Rich, análise estática e integração inicial com Codex CLI.
