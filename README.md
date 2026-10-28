# OffTec PortAI

**Assistente experimental para analisar projetos Windows e apoiar sua adaptação para Android no Termux.**

O PortAI identifica tecnologias e dependências que podem exigir ajustes, gera relatórios e, opcionalmente, usa o Codex CLI para trabalhar em uma cópia isolada do projeto.

> O PortAI não converte automaticamente qualquer programa Windows em um aplicativo Android. Os resultados da análise são heurísticos e devem ser revisados.

## Recursos

- Descoberta de projetos em diretórios configurados e seleção manual de pastas.
- Análise estática de arquivos de código e configuração de build.
- Identificação heurística de referências a APIs Windows, DirectX, Registry e WPF/.NET.
- Relatórios em Markdown e JSON.
- Integração opcional com Codex CLI em uma cópia de trabalho isolada.
- Fluxo de build que verifica se o APK gerado é válido como arquivo ZIP e contém `AndroidManifest.xml`.

## Requisitos

- Python 3.10 ou superior.
- Termux em dispositivo ARM64 (ambiente recomendado).
- `rich`, instalado pelo arquivo `requirements.txt`.
- Codex CLI autenticado para os recursos de assistência com Codex.
- Java, Android SDK e Gradle compatíveis com o projeto para compilar APKs.

## Instalação

No Termux:

```bash
pkg update
pkg install python git
git clone https://github.com/Mcsd7/OffTec-PortAI.git
cd OffTec-PortAI
python -m pip install -r requirements.txt
python portai.py
```

Se o ambiente bloquear a instalação global de pacotes, use um ambiente virtual:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python portai.py
```

## Integração com Codex

Instale e autentique o Codex CLI antes de usar esse recurso:

```bash
codex login status
```

No PortAI, abra a opção de assistência com Codex e selecione um projeto. O aplicativo cria ou reutiliza uma cópia em `workspaces/`, preservando os arquivos originais. A partir dela, é possível solicitar análise e alterações, acompanhar a execução e iniciar o build quando o projeto oferecer suporte.

A integração depende da versão instalada do Codex CLI. Consulte `codex exec --help` se a CLI não aceitar algum comando ou opção. Evite selecionar projetos que contenham credenciais, tokens ou outros dados privados.

## Limitações

- A análise é estática e heurística; não substitui compilação, execução ou revisão manual.
- Arquivos `.exe` são exibidos como referência, mas não são convertidos em APK.
- DirectX, DRM, drivers, bibliotecas fechadas e dependências complexas do Windows podem exigir reimplementação.
- Um executável Linux ARM64 não é automaticamente um aplicativo Android.
- Um APK estruturalmente válido ainda precisa ser instalado e testado no dispositivo.
- A assistência com Codex requer autenticação e acesso ao serviço.

## Estrutura do projeto

- `portai.py`: aplicativo de terminal.
- `samples/demo_windows/`: exemplo didático de projeto Windows.
- `tests/`: testes automatizados.
- `reports/`: destino dos relatórios gerados.
- `workspaces/`: cópias de trabalho criadas durante o uso do Codex.

## Licença

Distribuído sob a licença MIT. Consulte [`LICENSE`](LICENSE).
