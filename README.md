# OffTec PortAI 0.2.1

Assistente experimental de análise e adaptação de projetos Windows para Android, desenvolvido para Termux ARM64.

## Recursos

- Interface de terminal com Rich em tema roxo brilhante.
- Busca automática limitada a diretórios configurados, incluindo `~/simple-calculator` quando existir; não percorre todo o armazenamento.
- Opção para selecionar manualmente um diretório adicional na tela de projetos.
- Descoberta baseada em arquivos de configuração na raiz, com classificação incerta separada como `Precisa de análise`.
- Lista de projetos encontrados para selecionar por número, sem digitar caminho absoluto.
- Exibe arquivos `.exe` detectados como referência; não promete converter binários fechados.
- Análise estática de arquivos de código e build.
- Indicadores heurísticos de APIs Windows, DirectX, Registry e WPF/.NET.
- Relatórios Markdown e JSON.
- Integração opcional com Codex CLI por `codex exec`.
- Cria uma cópia de trabalho isolada em `workspaces/` antes de chamar o Codex.
- Sem root e sem bibliotecas obrigatórias além de Rich.

## Requisitos

- Python 3.10 ou superior.
- Termux recomendado.
- `rich`.
- Para o recurso Codex: Codex CLI instalado, autenticado e com conexão à internet.
- Para compilar APKs: Java e SDK Android/Gradle compatíveis com o projeto adaptado. A opção de build só reporta sucesso com APK não vazio e ZIP válido contendo AndroidManifest.xml.

## Instalação no Termux

```bash
pkg update
pkg install python git
cd ~/OffTec-PortAI
python -m pip install -r requirements.txt
python portai.py
```

Se `pip` recusar instalação por política do ambiente, use um ambiente virtual:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python portai.py
```

## Codex

Confira a autenticação:

```bash
codex login status
```

No menu, selecione `5`, escolha um projeto e confirme a cópia isolada. A primeira análise acontece uma vez; a cópia e o estado ficam em `workspaces/` e são reutilizados quando o mesmo projeto for selecionado novamente. O submenu permite analisar, reconstruir, conversar com o Codex, compilar, ver progresso, abrir o APK e retomar uma sessão quando a CLI disponibilizar um ID.

A conversa interativa retorna ao PortAI quando você sair do Codex. Execuções automatizadas preservam saída, erro, código de retorno e histórico de instruções. Um estado sem ID de sessão continua disponível para novas instruções no mesmo workspace, sem forçar outra cópia ou análise inicial. `Ctrl+C` preserva o workspace e marca a tarefa como interrompida.

O PortAI consulta o Codex através de `codex exec` e, se um ID for retornado, `codex exec resume <ID>`. A compatibilidade exata depende da versão instalada; confira `codex --help`, `codex exec --help` e `codex exec resume --help` no dispositivo. Se a CLI não iniciar, o erro aparece no estado da tarefa.

Se a sua versão do Codex não aceitar alguma opção de `codex exec`, consulte `codex exec --help` e ajuste a integração. A CLI pode mudar entre versões. Não use projetos que contenham credenciais, tokens ou dados privados.

## Limitações importantes

- Esta versão faz análise estática heurística, não uma conversão universal.
- Não transforma um `.exe` fechado em APK.
- Não converte automaticamente DirectX, DRM, drivers, bibliotecas fechadas ou dependências Windows complexas.
- O assistente Codex pode editar a cópia de trabalho, mas o resultado deve ser revisado e testado.
- Um executável Linux ARM64 rodando no Termux não é automaticamente um APK Android.
- APKs nativos exigem estrutura Android, bibliotecas compatíveis, empacotamento e testes no dispositivo.
- A execução do Codex depende da autenticação e da disponibilidade do serviço.

## Pastas

- `portai.py`: aplicativo.
- `reports/`: relatórios gerados.
- `workspaces/`: cópias isoladas para assistência Codex.
- `samples/demo_windows/`: exemplo didático, não executável no Android.

## Licença

MIT. Consulte `LICENSE`.
