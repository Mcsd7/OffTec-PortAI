#!/usr/bin/env python3
"""OffTec PortAI — assistente de análise e adaptação de projetos para Android."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich.text import Text
    from rich.traceback import install as install_rich_traceback
except ImportError:
    print("Dependência ausente. Instale com: python -m pip install -r requirements.txt")
    raise SystemExit(1)

install_rich_traceback(show_locals=False)
console = Console()
ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
WORKSPACES = ROOT / "workspaces"
VERSION = "0.2.1"
MAX_FILES = 3000
MAX_TEXT_BYTES = 1_000_000
MAX_SCAN_DIRS = 25000
MAX_CANDIDATES = 80
PROJECT_MARKERS = {
    "CMakeLists.txt", "Makefile", "meson.build", "Cargo.toml", "go.mod",
    "package.json", "pyproject.toml", "requirements.txt", "setup.py",
    "build.gradle", "build.gradle.kts", "pom.xml", "AndroidManifest.xml",
    "*.sln", "*.csproj", "*.vcxproj", "*.vbproj", "*.pro",
}
IGNORED_DIRS = {
    ".git", ".gradle", ".idea", ".venv", "venv", "node_modules",
    "__pycache__", "build", "dist", "target", "bin", "obj",
    ".next", ".pytest_cache", "workspaces", ".cache", ".npm", ".rustup",
    ".cargo", ".sdkman", "android-sdk", "llama.cpp",
}
SOURCE_EXTS = {
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".cs", ".py",
    ".java", ".kt", ".kts", ".js", ".ts", ".rs", ".go", ".html",
    ".xml", ".gradle", ".bat", ".ps1", ".sh", ".json", ".toml",
    ".yaml", ".yml", ".cmake", ".txt", ".md", ".properties",
}
BUILD_NAMES = {
    "CMakeLists.txt", "Makefile", "meson.build", "Cargo.toml",
    "go.mod", "package.json", "pyproject.toml", "requirements.txt",
    "CMakePresets.json",
    "setup.py", "build.gradle", "build.gradle.kts", "pom.xml",
    "AndroidManifest.xml", "README", "README.md", "README.txt",
}
LANGUAGES = {
    ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++",
    ".hpp": "C++ header", ".cxx": "C++", ".cs": "C#",
    ".py": "Python", ".java": "Java", ".kt": "Kotlin",
    ".kts": "Kotlin DSL", ".js": "JavaScript", ".ts": "TypeScript",
    ".rs": "Rust", ".go": "Go", ".html": "HTML", ".xml": "XML",
    ".gradle": "Gradle", ".bat": "Windows Batch", ".ps1": "PowerShell",
    ".sh": "Shell", ".json": "JSON", ".toml": "TOML",
}
PATTERNS = {
    "Windows API / headers": r"\b(windows\.h|WinMain|CreateWindowEx|MessageBoxW)\b",
    "Registry do Windows": r"\b(RegOpenKeyEx|RegSetValueEx|HKEY_CURRENT_USER)\b",
    "Processos Windows": r"\b(CreateProcessW|ShellExecuteW|COMSPEC)\b",
    "DirectX / Direct3D": r"\b(DirectX|Direct3D|D3D11CreateDevice|IDirect3D)\b",
    ".NET UI Windows": r"\b(System\.Windows\.Forms|PresentationFramework|WPF)\b",
    "Caminhos Windows": r"\b[A-Za-z]:\\\\(?:Windows|Program Files|Users)\\",
    "Chamadas possivelmente específicas do Windows": r"\b(GetLastError|LoadLibraryW|GetModuleHandleW|HRESULT)\b",
}

def banner() -> None:
    logo = Text()
    logo.append("OFF", style="bold bright_magenta")
    logo.append("TEC", style="bold magenta")
    logo.append("  PORT", style="bold white")
    logo.append("AI", style="bold bright_magenta")
    subtitle = Text("WINDOWS → ANDROID  •  CODE INTELLIGENCE  •  TERMUX EDITION", style="bright_white")
    content = Align.center(Text.assemble(logo, "\n", subtitle))
    console.print()
    console.print(Panel(content, border_style="bright_magenta", box=box.DOUBLE, padding=(1, 2)))
    console.print(Align.center(Text("Análise estática • cópias isoladas • integração com Codex CLI", style="dim")))
    console.print()

def section(title: str) -> None:
    console.print()
    console.print(Panel(Text(title, style="bold bright_magenta"), border_style="magenta", box=box.ROUNDED))

def yesno(value: bool) -> str:
    return "[bright_green]OK[/]" if value else "[yellow]NÃO ENCONTRADO[/]"

def check_environment() -> None:
    section("DIAGNÓSTICO DO AMBIENTE")
    rows = [
        ("Python", sys.version.split()[0]),
        ("Arquitetura", os.uname().machine if hasattr(os, "uname") else "desconhecida"),
        ("Codex CLI", shutil.which("codex") or "não encontrado"),
        ("Git", shutil.which("git") or "não encontrado"),
        ("Clang", shutil.which("clang") or "não encontrado"),
        ("CMake", shutil.which("cmake") or "não encontrado"),
        ("ANDROID_HOME", os.environ.get("ANDROID_HOME", "(não definido)")),
        ("SDK em ~/android-sdk", "presente" if (Path.home() / "android-sdk").is_dir() else "não encontrado"),
    ]
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold bright_magenta")
    table.add_column("Componente", style="white")
    table.add_column("Estado", style="bright_cyan")
    for a, b in rows:
        table.add_row(a, b)
    console.print(table)
    codex = shutil.which("codex")
    if codex:
        try:
            result = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=10)
            console.print(f"Codex: [bright_green]{(result.stdout or result.stderr).strip() or 'instalado'}[/]")
        except Exception as exc:
            console.print(f"[yellow]Não foi possível consultar o Codex: {exc}[/]")
        console.print("Autenticação: execute [bold]codex login status[/] para conferir.")
    console.print("[dim]A disponibilidade de ferramentas não garante que um projeto possa ser portado.[/]")

def iter_project_files(root: Path):
    count = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        cur = Path(current)
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not (cur / d).is_symlink()]
        for name in names:
            p = cur / name
            if p.is_symlink() or not p.is_file():
                continue
            if p.name in BUILD_NAMES or p.suffix.lower() in SOURCE_EXTS:
                yield p
                count += 1
                if count >= MAX_FILES:
                    return

def scan_roots() -> list[Path]:
    """Retorna apenas locais configurados, sem varrer a Home inteira."""
    home = Path.home()
    possible = [
        home / "simple-calculator",
        home / "storage" / "shared",
        home / "storage" / "downloads",
        home / "projetos",
        home / "Projects",
        home / "Downloads",
        home / "Documentos",
        home / "PORT",
        home / "simple-calculator",
    ]
    # Não varre todos os diretórios de primeiro nível da Home: isso incluía
    # ambientes, caches e ferramentas como se fossem projetos do usuário.
    roots: list[Path] = []
    seen: set[str] = set()
    for root in possible:
        try:
            if root.is_dir():
                resolved = str(root.resolve())
                if resolved not in seen:
                    roots.append(root.resolve())
                    seen.add(resolved)
        except OSError:
            continue
    # Evita raízes duplicadas quando uma pasta está dentro de outra já selecionada.
    compact: list[Path] = []
    for root in roots:
        if not any(root == old or old in root.parents for old in compact):
            compact.append(root)
    return compact


def _desktop_evidence(root: Path, names: list[str]) -> dict[str, str] | None:
    """Classifica pela configuração localizada na raiz, nunca por código solto."""
    marker_names = [n for n in names if n in BUILD_NAMES or n.endswith((".sln", ".csproj", ".vcxproj", ".vbproj", ".pro"))]
    if not marker_names:
        return None
    texts: dict[str, str] = {}
    for name in marker_names:
        path = root / name
        try:
            if not path.is_symlink() and path.stat().st_size <= 250_000:
                texts[name] = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
    body = "\n".join(texts.values())
    low = body.lower()
    if "androidmanifest.xml" in names or re.search(r"com\.android\.(application|library)|android\s*\{", low):
        return None
    if re.search(r"<manifest[^>]+package=|apktool|smali_classes|classes\.dex", low):
        return None

    # Configuração de compilação declara o executável e seu alvo. Strings em .cpp/.h não contam.
    cmake_app = bool(re.search(r"\badd_executable\s*\(", low))
    dotnet_app = any(n.endswith((".csproj", ".vbproj")) for n in marker_names) and bool(re.search(r"<outputtype>winexe</outputtype>|<usewindowsforms>true|<usewpf>true", low))
    electron_app = "package.json" in texts and bool(re.search(r'"(electron|electron-builder|electron-forge)"\s*:', low)) and bool(re.search(r'"main"\s*:', low))
    python_ui = any(n in texts for n in ("pyproject.toml", "setup.py", "setup.cfg")) and bool(re.search(r"(pyqt|pyside|tkinter|wxpython)", low))
    desktop_dep = bool(re.search(r"find_package\s*\(\s*(qt[56]?|sfml|sdl2?|gtk)", low))
    configured_ui = bool(re.search(r"\b(tkinter|pyqt[56]|pyside[236]|wxpython|pygame)\b", low))
    if not (cmake_app or dotnet_app or electron_app or python_ui or (desktop_dep and cmake_app) or configured_ui):
        # Configuração válida, mas sem alvo desktop provado: manter visível para análise.
        technology = "CMake" if "CMakeLists.txt" in texts else ", ".join(Path(n).suffix or n for n in marker_names)
        kind = "Biblioteca/SDK" if re.search(r"\badd_library\s*\(", low) else "Projeto para análise"
        return {"platform": "Precisa de análise", "kind": kind, "technology": technology, "reason": "configuração de projeto encontrada, alvo desktop não confirmado", "source_count": str(sum(Path(n).suffix.lower() in SOURCE_EXTS for n in names))}

    technologies = []
    if re.search(r"find_package\s*\(\s*sfml", low): technologies.append("SFML")
    if re.search(r"find_package\s*\(\s*sdl", low): technologies.append("SDL")
    if re.search(r"find_package\s*\(\s*qt", low): technologies.append("Qt Widgets")
    if re.search(r"find_package\s*\(\s*boost", low): technologies.append("Boost")
    if dotnet_app: technologies.append(".NET desktop")
    if electron_app: technologies.append("Electron")
    if python_ui: technologies.append("Python desktop")
    is_windows = bool(re.search(r"\bwin32\b|\bwin64\b|\bwinexe\b|\busewpf\b|\busewindowsforms\b|\bwin32\s+executable\b|cl\.exe", low))
    if is_windows:
        platform = "Windows desktop"
        reason = "alvo Windows declarado na configuração" + (f"; dependências: {', '.join(technologies)}" if technologies else "")
    elif technologies:
        platform = "desktop multiplataforma"
        reason = "alvo executável e dependência de interface desktop declarados"
    else:
        platform = "Precisa de análise"
        reason = "configuração declara aplicação, mas não identifica com segurança as plataformas"
    project_name = ""
    project_match = re.search(r"\bproject\s*\(\s*([^\s)]+)", low)
    if project_match:
        project_name = project_match.group(1)
    if re.search(r"\b(game|engine|sdk|source[- ]port)\b", project_name):
        kind = "Jogo/motor/SDK/código-fonte de jogo"
    else:
        kind = "Aplicativo desktop independente"
    return {"platform": platform, "kind": kind, "technology": ", ".join(technologies or ["alvo desktop configurado"]), "reason": reason, "source_count": str(sum(Path(n).suffix.lower() in SOURCE_EXTS for n in names))}


def discover_projects(search_roots: list[Path] | None = None) -> tuple[list[dict[str, Any]], list[Path]]:
    """Busca projetos de desktop somente nas raízes configuradas."""
    candidates: dict[str, dict[str, Any]] = {}
    executables: dict[str, Path] = {}
    visited = 0
    configured = search_roots if search_roots is not None else scan_roots()
    # Uma pasta selecionada manualmente pode ser a própria raiz de projeto; quando for
    # diretório de busca, continua a exploração limitada em profundidade.
    for root in configured:
        root = root.expanduser().resolve()
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            if current_path == ROOT or ROOT in current_path.parents:
                dirs[:] = []
                continue
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                depth = 99
            dirs[:] = [d for d in dirs if d.lower() not in IGNORED_DIRS and not (current_path / d).is_symlink()]
            if depth >= 6:
                dirs[:] = []
            visited += 1
            if visited > MAX_SCAN_DIRS:
                break
            for name in names:
                file_path = current_path / name
                if file_path.is_symlink():
                    continue
                if name.lower().endswith(".exe"):
                    executables[str(file_path)] = file_path
            classification = _desktop_evidence(current_path, names)
            if classification:
                resolved = current_path.resolve()
                candidates[str(resolved)] = {"path": resolved, **classification}
            if len(candidates) >= MAX_CANDIDATES:
                break
        if visited > MAX_SCAN_DIRS or len(candidates) >= MAX_CANDIDATES:
            break
    projects = sorted(candidates.values(), key=lambda item: str(item["path"]).lower())
    return projects, sorted(executables.values(), key=lambda item: str(item).lower())[:100]


def select_project() -> Path | None:
    """Mostra projetos encontrados e devolve a escolha do usuário."""
    section("PROJETOS DESKTOP E PROJETOS PARA ANÁLISE")
    console.print("[dim]Procurando aplicativos desktop nas raízes configuradas...[/]")
    projects, executables = discover_projects()
    if not projects:
        console.print("[yellow]Nenhum projeto desktop com evidências suficientes foi encontrado.[/]")
        chosen = Prompt.ask("Informe outra pasta de busca (Enter para voltar)", default="")
        if chosen:
            search = Path(chosen).expanduser()
            if search.is_dir():
                projects, executables = discover_projects([search.resolve()])
            else:
                console.print("[yellow]Pasta inválida.[/]")
        if not projects:
            Prompt.ask("Pressione Enter para voltar ao menu", default="")
            return None
    else:
        table = Table(box=box.ROUNDED, border_style="bright_magenta", header_style="bold bright_magenta")
        table.add_column("Nº", justify="right", style="bright_magenta")
        table.add_column("Projeto", style="white")
        table.add_column("Caminho completo", style="dim")
        table.add_column("Plataforma", style="bright_cyan")
        table.add_column("Tipo", style="white")
        table.add_column("Tecnologia", style="white")
        table.add_column("Evidência", style="green")
        for i, item in enumerate(projects, 1):
            if item["platform"] == "Precisa de análise":
                continue
            path = Path(item["path"])
            table.add_row(str(i), path.name, str(path), item["platform"], item.get("kind", "Projeto para análise"), item["technology"], item["reason"])
        console.print(table)
        uncertain = [(i, item) for i, item in enumerate(projects, 1) if item["platform"] == "Precisa de análise"]
        if uncertain:
            details = "\n".join(f"#{i} {item['path']}\n  Razão: {item['reason']}" for i, item in uncertain)
            console.print(Panel(details, title="Precisa de análise", border_style="yellow"))
        for i, item in enumerate(projects, 1):
            console.print(f"[dim]Raiz completa #{i}: {item['path']}[/]")
    if executables:
        console.print(Panel(
            "\n".join(f"[yellow]•[/] {p}" for p in executables[:12])
            + (f"\n[dim]... e mais {len(executables)-12}[/]" if len(executables) > 12 else ""),
            title=f"Executáveis .EXE encontrados ({len(executables)})",
            border_style="yellow"
        ))
        console.print("[dim]Um .EXE sozinho não vira APK: normalmente precisamos do código-fonte e das dependências.[/]")
    console.print("[dim]Opções numeradas correspondem às linhas da lista.[/]")
    options = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    options.add_column("Opção", style="bright_magenta", justify="right")
    options.add_column("Projeto", style="white")
    for i, item in enumerate(projects, 1):
        path = Path(item["path"])
        try:
            display_path = "~/" + str(path.relative_to(Path.home()))
        except ValueError:
            display_path = str(path)
        options.add_row(str(i), f"{path.name}  [dim]{display_path}[/]")
    options.add_row("0", "Voltar ao menu")
    console.print(options)
    valid = [str(i) for i in range(len(projects) + 1)] + ["a"]
    console.print("[dim]Digite 'a' para buscar em outro diretório; isso não amplia a busca automática.[/]")
    choice = Prompt.ask("Projeto", choices=valid, default="1" if projects else "0")
    if choice == "a":
        chosen = Prompt.ask("Diretório adicional")
        search = Path(chosen).expanduser()
        if search.is_dir():
            extra, extra_executables = discover_projects([search.resolve()])
            existing = {str(item["path"]) for item in projects}
            projects.extend(item for item in extra if str(item["path"]) not in existing)
            executables = sorted({str(p): p for p in executables + extra_executables}.values(), key=lambda p: str(p).lower())[:100]
            if not projects:
                console.print("[yellow]Nenhuma configuração de projeto foi encontrada nesse diretório.[/]")
                return None
            return select_project_from_items(projects)
        console.print("[yellow]Pasta inválida.[/]")
        return None
    if choice == "0":
        return None
    return projects[int(choice) - 1]["path"]


def select_project_from_items(projects: list[dict[str, Any]]) -> Path | None:
    """Mostra lista resultante da busca manual e devolve a escolha correspondente."""
    table = Table(box=box.ROUNDED, header_style="bold bright_magenta")
    table.add_column("Nº", justify="right")
    table.add_column("Raiz identificada")
    table.add_column("Plataforma")
    table.add_column("Tipo")
    table.add_column("Razão")
    for index, item in enumerate(projects, 1):
        table.add_row(str(index), str(item["path"]), item["platform"], item.get("kind", "Projeto para análise"), item["reason"])
    console.print(table)
    choice = Prompt.ask("Projeto", choices=[str(i) for i in range(len(projects) + 1)], default="1")
    return None if choice == "0" else projects[int(choice) - 1]["path"]


def analyze_project(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Diretório não encontrado: {root}")
    files = list(iter_project_files(root))
    counts: dict[str, int] = {}
    build_files: list[str] = []
    hits: dict[str, list[str]] = {k: [] for k in PATTERNS}
    scanned_text = 0
    skipped_large = 0
    for p in files:
        rel = str(p.relative_to(root))
        if p.suffix:
            lang = LANGUAGES.get(p.suffix.lower(), p.suffix.lower())
            counts[lang] = counts.get(lang, 0) + 1
        if p.name in BUILD_NAMES:
            build_files.append(rel)
        if p.suffix.lower() not in SOURCE_EXTS:
            continue
        try:
            size = p.stat().st_size
            if size > MAX_TEXT_BYTES:
                skipped_large += 1
                continue
            content = p.read_text(encoding="utf-8", errors="replace")
            scanned_text += 1
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            if len(hits[label]) < 25 and re.search(pattern, content, re.IGNORECASE):
                hits[label].append(rel)
    findings = {k: v for k, v in hits.items() if v}
    extensions = {p.suffix.lower() for p in files}
    if not files:
        verdict = "Nenhum arquivo de código/build reconhecido"
    elif any(k in findings for k in ("Windows API / headers", "DirectX / Direct3D", ".NET UI Windows", "Registry do Windows")):
        verdict = "Possíveis dependências específicas do Windows: requer adaptação"
    elif extensions & {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}:
        verdict = "Código nativo encontrado: verificar bibliotecas, compilador e sistema de build"
    elif ".py" in extensions:
        verdict = "Python encontrado: verificar dependências e APIs do sistema"
    else:
        verdict = "Compatibilidade indeterminada: revisar dependências e arquitetura"
    return {
        "tool": "OffTec PortAI", "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": str(root), "verdict": verdict, "files_found": len(files),
        "text_files_read": scanned_text, "large_files_skipped": skipped_large,
        "languages": dict(sorted(counts.items())),
        "build_files": sorted(build_files), "windows_indicators": findings,
        "sample_files": [str(p.relative_to(root)) for p in files[:20]],
        "limitations": [
            "Análise estática heurística; não comprova compatibilidade.",
            "Não inspeciona profundamente binários EXE/DLL.",
            "Não gera APK automaticamente nesta versão.",
            "Dependências, licenças, APIs gráficas e arquitetura exigem validação.",
        ],
    }

def save_report(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = REPORTS / f"portai-{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Relatório OffTec PortAI", "",
        f"- Versão: {report['version']}",
        f"- Projeto: `{report['project']}`",
        f"- Arquivos reconhecidos: {report['files_found']}",
        f"- Arquivos de texto lidos: {report['text_files_read']}",
        f"- Avaliação: **{report['verdict']}**", "", "## Linguagens",
    ]
    lines += [f"- {k}: {v}" for k, v in report["languages"].items()] or ["- Nenhuma reconhecida"]
    lines += ["", "## Arquivos de build"]
    lines += [f"- `{x}`" for x in report["build_files"]] or ["- Nenhum reconhecido"]
    lines += ["", "## Indicadores Windows"]
    for key, values in report["windows_indicators"].items():
        lines.append(f"- **{key}:** " + ", ".join(f"`{x}`" for x in values))
    if not report["windows_indicators"]:
        lines.append("- Nenhum indicador conhecido detectado")
    lines += ["", "## Limitações"] + [f"- {x}" for x in report["limitations"]]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path

def show_report(report: dict[str, Any], md: Path, js: Path) -> None:
    console.print(Panel(
        f"[bold white]{report['verdict']}[/]\n\n"
        f"[bright_cyan]Arquivos:[/] {report['files_found']}  "
        f"[bright_cyan]Textos lidos:[/] {report['text_files_read']}  "
        f"[bright_cyan]Indicadores Windows:[/] {len(report['windows_indicators'])}",
        title="RESULTADO DA ANÁLISE", border_style="bright_magenta", box=box.ROUNDED
    ))
    table = Table(title="Linguagens e arquivos", box=box.SIMPLE, header_style="bold bright_magenta")
    table.add_column("Linguagem")
    table.add_column("Quantidade", justify="right")
    for lang, count in report["languages"].items():
        table.add_row(lang, str(count))
    console.print(table)
    if report["windows_indicators"]:
        t = Table(title="Possíveis obstáculos à portabilidade", box=box.SIMPLE)
        t.add_column("Indicador", style="bright_magenta")
        t.add_column("Arquivos")
        for label, paths in report["windows_indicators"].items():
            t.add_row(label, ", ".join(paths[:5]))
        console.print(t)
    console.print(f"[dim]Markdown: {md}[/]\n[dim]JSON: {js}[/]")

def make_workspace(source: Path) -> Path:
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("O caminho informado não é um diretório.")
    if source == ROOT or ROOT in source.parents:
        raise ValueError("Escolha um projeto externo à pasta do próprio OffTec PortAI.")
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = WORKSPACES / f"{source.name}-{stamp}"
    ignored = shutil.ignore_patterns(
        ".git", ".gradle", ".idea", ".venv", "venv", "node_modules",
        "__pycache__", "build", "dist", "target", "bin", "obj",
        ".next", "*.apk", "*.aab", "*.dex", "*.so", "*.class",
    )
    shutil.copytree(source, dest, ignore=ignored, symlinks=False)
    return dest


def workspace_state_path(workspace: Path) -> Path:
    return workspace / ".portai-state.json"


def load_workspace_state(workspace: Path) -> dict[str, Any]:
    path = workspace_state_path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_workspace_state(workspace: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    workspace_state_path(workspace).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_workspace(source: Path) -> Path | None:
    """Find the existing isolated copy associated with this exact source path."""
    if not WORKSPACES.is_dir():
        return None
    source_key = str(source.resolve())
    for candidate in sorted(WORKSPACES.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if candidate.is_dir() and not candidate.is_symlink():
            state = load_workspace_state(candidate)
            if state.get("source_path") == source_key:
                return candidate.resolve()
    return None


def codex_exec_command(codex: str, workspace: Path, prompt: str) -> list[str]:
    """Build argv for codex exec; keep the workspace path as one argument."""
    return [codex, "exec", "--cd", str(workspace), "--sandbox", "workspace-write", prompt]


def codex_resume_command(codex: str, workspace: Path, session_id: str | None = None) -> list[str]:
    command = [codex, "exec", "resume"]
    if session_id:
        command.append(session_id)
    return command + ["--cd", str(workspace)]


def run_codex(codex: str, workspace: Path, prompt: str, timeout: int = 1800, resume_id: str | None = None):
    """Run Codex with inherited credentials/network environment and split output."""
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir() or WORKSPACES.resolve() not in workspace.parents:
        raise ValueError(f"Diretório de trabalho inválido ou fora de workspaces/: {workspace}")
    command = codex_resume_command(codex, workspace, resume_id) if resume_id is not None else codex_exec_command(codex, workspace, prompt)
    return subprocess.run(
        command, cwd=str(workspace), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )


ANDROID_REBUILD_PROMPT = """Reconstruct this project for Android in the current isolated workspace. First inspect its architecture, UI framework, dependencies, build configuration and platform-specific APIs. Reuse portable business logic where possible; adapt or rewrite the UI and replace incompatible libraries when needed. Create the Android project/configuration required by the most suitable strategy available here (native Android/Gradle, NDK/CMake, or an appropriate existing framework). Check available Java, Gradle, Android SDK, NDK and CMake tools before choosing. Install dependencies only when necessary and report permission, network and compatibility failures. Run relevant tests, attempt to build an installable APK, inspect and fix viable build errors, and keep trying while a practical correction remains. Never claim success without an APK file produced by a successful build. Record approach, blockers, commands, test/build results and changed files in ANDROID_PORT_PROGRESS.md. Do not access secrets or modify files outside this workspace."""


def run_build(workspace: Path) -> tuple[int, str, str, Path | None]:
    """Run a locally available Android build and return only a verified APK."""
    env = os.environ.copy()
    sdk = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    if not sdk and (Path.home() / "android-sdk").is_dir():
        sdk = str(Path.home() / "android-sdk")
        env["ANDROID_HOME"] = sdk
    gradlew = workspace / "gradlew"
    gradle = shutil.which("gradle")
    if gradlew.is_file():
        try:
            gradlew.chmod(gradlew.stat().st_mode | 0o111)
        except OSError:
            pass
        command = [str(gradlew), "assembleDebug"]
    elif gradle:
        command = [gradle, "assembleDebug"]
    elif (workspace / "CMakeLists.txt").is_file() and shutil.which("cmake"):
        if not sdk:
            return 127, "", "Android SDK não encontrado (ANDROID_HOME/ANDROID_SDK_ROOT).", None
        ndk = env.get("ANDROID_NDK_HOME") or env.get("ANDROID_NDK_ROOT")
        if not ndk:
            ndks = Path(sdk) / "ndk"
            versions = sorted(ndks.iterdir()) if ndks.is_dir() else []
            ndk = str(versions[-1]) if versions else ""
        if not ndk:
            return 127, "", "Android NDK não encontrado para o projeto CMake.", None
        command = [shutil.which("cmake"), "-S", str(workspace), "-B", str(workspace / "build-android"), "-DANDROID_ABI=arm64-v8a", "-DANDROID_PLATFORM=android-24", f"-DANDROID_NDK={ndk}"]
    else:
        return 127, "", "Não encontrei gradlew, Gradle ou uma configuração Android CMake compilável.", None
    tools = {name: shutil.which(name) for name in ("java", "gradle", "cmake", "ninja", "aapt", "apksigner")}
    console.print(Panel("\n".join(f"{name}: {path or 'não encontrado'}" for name, path in tools.items()) + f"\nAndroid SDK: {sdk or 'não encontrado'}", title="FERRAMENTAS DE BUILD", border_style="cyan"))
    try:
        proc = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=3600, check=False)
        apk = find_apk(workspace) if proc.returncode == 0 else None
        if proc.returncode == 0 and apk is None:
            return proc.returncode, proc.stdout, proc.stderr + "\nBuild terminou sem APK não vazio encontrado.", None
        if apk and not validate_apk(apk):
            return proc.returncode, proc.stdout, proc.stderr + f"\nAPK inválido ou ZIP corrompido: {apk}", None
        return proc.returncode, proc.stdout, proc.stderr, apk
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc), None


def find_apk(workspace: Path) -> Path | None:
    candidates = [p for p in workspace.rglob("*.apk") if p.is_file() and p.stat().st_size > 0 and "build" in p.parts]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def validate_apk(apk: Path) -> bool:
    try:
        if not apk.is_file() or apk.stat().st_size <= 0:
            return False
        with zipfile.ZipFile(apk) as package:
            if package.testzip() is not None:
                return False
            names = set(package.namelist())
            return "AndroidManifest.xml" in names
    except (OSError, zipfile.BadZipFile):
        return False


def show_workspace_progress(workspace: Path, state: dict[str, Any]) -> None:
    changed = subprocess.run(["git", "status", "--short"], cwd=workspace, capture_output=True, text=True) if shutil.which("git") else None
    console.print(Panel(f"Estado: {state.get('status', 'aguardando instruções')}\nOrigem: {state.get('source_path', '?')}\nWorkspace: {workspace}\nSessão Codex: {state.get('session_id') or 'indisponível'}\nÚltimo erro: {state.get('last_error') or 'nenhum'}", title="PROGRESSO", border_style="bright_magenta"))
    if changed:
        console.print(Panel(changed.stdout.strip() or "Git não disponível nesta cópia.", title="ARQUIVOS MODIFICADOS", border_style="cyan"))
    progress = workspace / "ANDROID_PORT_PROGRESS.md"
    if progress.is_file():
        console.print(Panel(progress.read_text(encoding="utf-8", errors="replace")[-10000:], title="RELATÓRIO DE PROGRESSO", border_style="cyan"))


def codex_task(codex: str, workspace: Path, state: dict[str, Any], prompt: str, resume: bool = False) -> None:
    state["status"] = "reconstruindo" if "Reconstruct" in prompt else "analisando" if "analy" in prompt.lower() else "trabalhando com Codex"
    save_workspace_state(workspace, state)
    try:
        proc = run_codex(codex, workspace, prompt, resume_id=state.get("session_id") if resume else None)
        # Codex JSONL output may include a thread.started event even if the CLI
        # version does not expose a separate session flag.
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                thread = event.get("thread_id") or event.get("session_id")
                if not thread and isinstance(event.get("thread"), dict):
                    thread = event["thread"].get("id")
                if thread:
                    state["session_id"] = str(thread)
        if prompt:
            state["history"].append({"at": datetime.now().isoformat(timespec="seconds"), "prompt": prompt[:600], "return_code": proc.returncode})
        console.print(Panel(proc.stdout[-16000:] or "(sem stdout)", title="SAÍDA DO CODEX", border_style="bright_magenta"))
        if proc.stderr:
            console.print(Panel(proc.stderr[-8000:], title="ERROS/AVISOS DO CODEX", border_style="yellow"))
        state["last_return_code"] = proc.returncode
        state["last_output"] = proc.stdout[-4000:]
        if proc.returncode:
            state["last_error"] = proc.stderr[-2000:] or f"Codex terminou com código {proc.returncode}"
            state["status"] = "erro"
        else:
            state["last_error"] = None
            state["status"] = "aguardando instruções"
    except subprocess.TimeoutExpired as exc:
        state["status"] = "interrompido"; state["last_error"] = f"Tempo limite: {exc}"
        console.print(f"[red]Codex excedeu o limite. Estado preservado: {exc}[/]")
    except KeyboardInterrupt:
        state["status"] = "interrompido"; state["last_error"] = "Execução interrompida por Ctrl+C"
        console.print("[yellow]Execução interrompida; workspace e estado foram preservados.[/]")
    except OSError as exc:
        state["status"] = "erro"; state["last_error"] = str(exc)
        console.print(f"[red]Falha ao executar Codex: {exc}[/]")
    finally:
        save_workspace_state(workspace, state)


def reconstruction_menu(source: Path | None = None, workspace: Path | None = None) -> None:
    codex = shutil.which("codex")
    if workspace is None:
        if source is None:
            source = select_project()
            if source is None:
                return
        source = source.resolve(strict=True)
        if source == ROOT or ROOT in source.parents:
            console.print("[red]Escolha um projeto fora da pasta do PortAI.[/]"); return
        workspace = find_workspace(source)
        if workspace is None:
            if not Confirm.ask("Criar uma cópia de trabalho isolada em workspaces/?", default=False):
                return
            workspace = make_workspace(source)
            save_workspace_state(workspace, {"source_path": str(source), "workspace": str(workspace), "status": "aguardando análise", "session_id": None, "analysis_done": False, "history": []})
    state = load_workspace_state(workspace)
    if not state:
        console.print("[red]Estado da workspace não encontrado; não será associada automaticamente a outro projeto.[/]"); return
    state.setdefault("history", [])
    if codex and not state.get("analysis_done"):
        console.print(f"[cyan]Analisando a cópia existente: {workspace}[/]")
        codex_task(codex, workspace, state, "Analyze this project for Android portability. Inspect architecture, UI, dependencies, build configuration and platform blockers. Reuse original logic where practical. Write PORTABILITY_REPORT.md with evidence, feasible Android strategy, required adaptations, available tools, risks and concrete next steps. Do not modify outside this workspace.")
        state["analysis_done"] = state.get("last_return_code") == 0
        state["status"] = "aguardando instruções" if state["analysis_done"] else state.get("status", "erro")
        save_workspace_state(workspace, state)
    while True:
        section("RECONSTRUÇÃO ANDROID — SESSÃO PERSISTENTE")
        console.print(f"Projeto: [cyan]{state.get('source_path')}[/]\nEstado: [yellow]{state.get('status')}[/]  •  Sessão: {state.get('session_id') or 'não disponível'}")
        table = Table(box=box.SIMPLE_HEAVY)
        for n, label in [("1", "Analisar projeto"), ("2", "Reconstruir para Android"), ("3", "Continuar conversa com Codex"), ("4", "Compilar APK"), ("5", "Verificar dependências e corrigir erros"), ("6", "Mostrar arquivos modificados e progresso"), ("7", "Abrir ou instalar APK"), ("8", "Retomar sessão anterior"), ("9", "Voltar ao menu principal")]: table.add_row(n, label)
        console.print(table)
        choice = Prompt.ask("Reconstrução", choices=[str(i) for i in range(1, 10)], default="9")
        if choice == "9": return
        if choice in {"1", "2", "5", "8"}:
            if not codex:
                console.print("[red]Codex CLI não está no PATH.[/]"); continue
            if choice == "1":
                codex_task(codex, workspace, state, "Analyze this project for Android portability. Inspect architecture, dependencies and platform blockers, and update PORTABILITY_REPORT.md with a concrete Android plan.")
                state["analysis_done"] = state.get("last_return_code") == 0
            elif choice == "2": codex_task(codex, workspace, state, ANDROID_REBUILD_PROMPT)
            elif choice == "5": codex_task(codex, workspace, state, "Inspect installed Java/Gradle/Android SDK/NDK/CMake and this project's build. Diagnose the latest compilation error; make viable corrections, run tests and attempt an APK build. Record evidence and remaining blockers in ANDROID_PORT_PROGRESS.md.")
            else:
                if not state.get("session_id"):
                    console.print("[yellow]Não há ID salvo para retomar; use continuar conversa para iniciar uma nova sessão no workspace preservado.[/]"); continue
                codex_task(codex, workspace, state, "", resume=True)
        elif choice == "3":
            if not codex: console.print("[red]Codex CLI não está no PATH.[/]"); continue
            console.print("[cyan]Iniciando Codex interativo no workspace; saia do Codex para retornar aqui.[/]")
            state["status"] = "conversa interativa"; save_workspace_state(workspace, state)
            try:
                interactive = subprocess.run([codex, "--cd", str(workspace)], cwd=workspace, check=False)
                state["last_return_code"] = interactive.returncode
                state["status"] = "aguardando instruções" if interactive.returncode == 0 else "erro"
                state["last_error"] = None if interactive.returncode == 0 else f"Codex interativo terminou com código {interactive.returncode}"
            except (OSError, KeyboardInterrupt) as exc:
                console.print(f"[yellow]Conversa encerrada: {exc}[/]")
                state["status"] = "interrompido"; state["last_error"] = str(exc)
            save_workspace_state(workspace, state)
        elif choice == "4":
            state["status"] = "compilando"; save_workspace_state(workspace, state)
            code, out, err, apk = run_build(workspace)
            if out: console.print(Panel(out[-12000:], title="BUILD STDOUT", border_style="cyan"))
            if err: console.print(Panel(err[-12000:], title="BUILD STDERR", border_style="yellow"))
            state["status"] = "APK gerado" if code == 0 and apk else "erro"
            state["last_error"] = None if apk else (err[-2000:] or "Build falhou ou nenhum APK verificável foi encontrado.")
            state["apk_path"] = str(apk) if apk else None; save_workspace_state(workspace, state)
            console.print(f"[green]APK validado: {apk} ({apk.stat().st_size} bytes)[/]" if apk else f"[red]Sem APK validado (código {code}).[/]")
        elif choice == "6": show_workspace_progress(workspace, state)
        elif choice == "7":
            apk = find_apk(workspace)
            if not apk: console.print("[yellow]Nenhum APK não vazio encontrado.[/]"); continue
            console.print(f"APK: {apk} ({apk.stat().st_size} bytes)")
            opener = shutil.which("termux-open") or shutil.which("am")
            if opener and Confirm.ask("Tentar abrir/instalar este APK?", default=False):
                args = [opener, "--view", str(apk)] if Path(opener).name == "termux-open" else [opener, "start", "-a", "android.intent.action.VIEW", "-d", f"file://{apk}", "-t", "application/vnd.android.package-archive"]
                subprocess.run(args, check=False)
        save_workspace_state(workspace, state)

def codex_port_assist() -> None:
    reconstruction_menu()

def run_analysis() -> None:
    source = select_project()
    if source is None:
        return
    try:
        report = analyze_project(source)
        md, js = save_report(report)
        show_report(report, md, js)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Falha na análise: {exc}[/]")

def menu() -> None:
    while True:
        banner()
        table = Table(box=box.ROUNDED, show_header=False, border_style="magenta", padding=(0, 1))
        table.add_column("Opção", style="bold bright_magenta", width=8)
        table.add_column("Ferramenta", style="white")
        table.add_column("Descrição", style="dim")
        table.add_row(" [1] ", "Encontrar e analisar projetos", "Listar projetos detectados e escolher pelo número")
        table.add_row(" [2] ", "Portar com Codex", "Abrir o ambiente persistente de reconstrução Android")
        table.add_row(" [3] ", "Diagnóstico", "Verificar Python, Codex, Git e SDK Android")
        table.add_row(" [4] ", "Abrir pasta de relatórios", str(REPORTS))
        table.add_row(" [5] ", "Reconstrução Android com Codex", "Ambiente persistente para análise, adaptação e APK")
        table.add_row(" [0] ", "Sair", "Encerrar com segurança")
        console.print(table)
        choice = Prompt.ask("PortAI", choices=["1", "2", "3", "4", "5", "0"], default="1")
        if choice == "0":
            console.print(Panel("[bold bright_magenta]OffTec PortAI encerrado. Até a próxima![/]", border_style="magenta"))
            break
        if choice == "1":
            run_analysis()
        elif choice == "2":
            codex_port_assist()
        elif choice == "3":
            check_environment()
        elif choice == "4":
            REPORTS.mkdir(parents=True, exist_ok=True)
            console.print(f"Pasta: {REPORTS}")
            try:
                files = sorted(REPORTS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                for p in files[:30]:
                    console.print(f" • {p.name}")
                if not files:
                    console.print("[dim]Nenhum relatório ainda.[/]")
            except OSError as exc:
                console.print(f"[red]{exc}[/]")
        elif choice == "5":
            reconstruction_menu()
        Prompt.ask("\nPressione Enter para voltar ao menu", default="")

def main() -> None:
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Operação interrompida pelo usuário.[/]")
    except Exception as exc:
        console.print(f"[red]Erro inesperado: {exc}[/]")
        raise

if __name__ == "__main__":
    main()
