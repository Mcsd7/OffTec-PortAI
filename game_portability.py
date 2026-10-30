"""Heuristic game-engine and Android portability analysis; never edits projects."""

from __future__ import annotations

import json
import os
import re
import shutil
import hashlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

IGNORED_DIRS = {
    ".git", ".gradle", ".idea", ".venv", "venv", "node_modules", "__pycache__",
    "build", "dist", "target", "bin", "obj", "Library", "Temp", "Logs",
    "Intermediate", "Binaries", ".godot", ".import", "workspaces",
}
LANGUAGES = {
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++",
    ".hpp": "C++", ".gd": "GDScript", ".gdc": "GDScript bytecode",
    ".gml": "GML", ".py": "Python", ".java": "Java", ".kt": "Kotlin",
    ".js": "JavaScript", ".ts": "TypeScript", ".csharp": "C#", ".shader": "Shader",
    ".hlsl": "HLSL", ".glsl": "GLSL", ".usf": "Unreal Shader (HLSL)",
}
SOURCE_EXTENSIONS = set(LANGUAGES) | {".xml", ".json", ".ini", ".cfg", ".toml", ".yml", ".yaml", ".gradle", ".properties", ".txt", ".uproject", ".yyp", ".godot"}
MAX_FILES = 20000
MAX_TEXT_BYTES = 2_000_000

ENGINE_DEFINITIONS = {
    "Unity": {
        "markers": ("ProjectSettings/ProjectVersion.txt", "ProjectSettings/ProjectSettings.asset"),
        "host": "Unity Editor com Android Build Support em host Linux, Windows ou macOS; exportação Android não é presumida disponível no Termux.",
    },
    "Unreal Engine": {
        "markers": ("*.uproject",),
        "host": "Unreal Editor/toolchain Android em host Linux ou Windows; projetos C++ podem exigir compilador e SDK externos.",
    },
    "Godot": {
        "markers": ("project.godot",),
        "host": "Godot com templates de exportação Android, Java e Android SDK; editor/export templates podem exigir host desktop.",
    },
    "GameMaker": {
        "markers": ("*.yyp",),
        "host": "GameMaker IDE e runtime/export Android compatíveis; normalmente o pipeline de exportação requer host desktop e licença aplicável.",
    },
}

PATTERNS = {
    "keyboard": re.compile(r"\b(Input\.GetKey(?:Down|Up)?|GetKeyState|GetAsyncKeyState|Input\.is_key_pressed|InputEventKey|keyboard_check(?:_pressed|_released)?|IsKeyDown|EKeys::|KeyCode\.)", re.I),
    "mouse": re.compile(r"\b(Input\.mousePosition|Input\.GetMouseButton|InputEventMouse|mouse_check_button(?:_pressed|_released)?|GetMousePosition|MouseX|MouseY|OnMouseDown|WM_MOUSE|DirectInput)", re.I),
    "windows_api": re.compile(r"\b(windows\.h|WinMain|CreateWindowEx|DirectX|D3D11CreateDevice|XInput|Registry|HKEY_CURRENT_USER|user32\.dll|kernel32\.dll)\b", re.I),
    "desktop_file_api": re.compile(r"\b(Environment\.GetFolderPath|FileDialog|OpenFileDialog|SaveFileDialog|System\.Windows\.Forms|java\.awt|javax\.swing)\b", re.I),
    "resolution": re.compile(r"\b(defaultScreenWidth|defaultScreenHeight|Screen\.SetResolution|DisplayServer\.window_set_size|window_width|window_height|r\.SetRes|GameUserSettings|Resolution|fullscreen|windowed)\b", re.I),
    "graphics": re.compile(r"\b(HDRP|HighDefinitionRenderPipeline|URP|UniversalRenderPipeline|Nanite|Lumen|RayTracing|ComputeShader|Forward\+|RenderingMethod|shader|surface_create|Graphics\.Blit)\b", re.I),
    "performance": re.compile(r"\b(qualitySettings|SetQualityLevel|antiAliasing|MSAA|shadowResolution|textureStreaming|mipMap|LODGroup|DrawCall|occlusion|VSync|targetFrameRate|fps_max)\b", re.I),
}


def _walk(root: Path):
    seen = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS and not (base / name).is_symlink()]
        for name in names:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path
            seen += 1
            if seen >= MAX_FILES:
                return


def detect_engine(root: Path, paths: list[Path] | None = None) -> tuple[str, list[str]]:
    paths = paths if paths is not None else list(_walk(root))
    relative = [path.relative_to(root).as_posix() for path in paths]
    detected: list[str] = []
    for engine, definition in ENGINE_DEFINITIONS.items():
        for marker in definition["markers"]:
            if marker.startswith("*"):
                suffix = marker[1:].lower()
                matches = [p for p in relative if p.lower().endswith(suffix)]
            else:
                matches = [p for p in relative if p.lower() == marker.lower()]
            if matches:
                detected.append(engine)
                break
    if len(detected) == 1:
        return detected[0], detected
    if len(detected) > 1:
        return "Projeto híbrido: " + ", ".join(detected), detected
    if any(path.lower().endswith((".sln", ".csproj", ".vcxproj", "cmakelists.txt", "build.gradle", "build.gradle.kts")) for path in relative):
        return "Projeto personalizado", ["Projeto personalizado"]
    if any(path.lower().endswith(tuple(LANGUAGES)) for path in relative):
        return "Projeto personalizado (heurístico)", ["Projeto personalizado"]
    return "Desconhecido", []


def _engine_dependencies(root: Path, engine: str, paths: list[Path]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    relative = {path.relative_to(root).as_posix().lower(): path for path in paths}
    if engine == "Unity" and "packages/manifest.json" in relative:
        try:
            packages = json.loads(relative["packages/manifest.json"].read_text(encoding="utf-8"))
            found.extend({"name": name, "version": str(version), "source": "Packages/manifest.json"} for name, version in packages.get("dependencies", {}).items())
        except (OSError, ValueError, AttributeError):
            found.append({"name": "Unity packages", "version": "não analisado", "source": "Packages/manifest.json inválido"})
    elif engine == "Unreal Engine":
        for project in (p for p in paths if p.suffix.lower() == ".uproject"):
            try:
                data = json.loads(project.read_text(encoding="utf-8"))
                for module in data.get("Modules", []):
                    found.append({"name": module.get("Name", "module"), "version": str(module.get("Type", "unknown")), "source": project.relative_to(root).as_posix()})
                for plugin in data.get("Plugins", []):
                    found.append({"name": plugin.get("Name", "plugin"), "version": str(plugin.get("Enabled", "unknown")), "source": project.relative_to(root).as_posix()})
            except (OSError, ValueError, AttributeError):
                found.append({"name": "Unreal modules/plugins", "version": "não analisado", "source": project.relative_to(root).as_posix()})
    elif engine == "Godot":
        project_file = relative.get("project.godot")
        if project_file:
            try:
                text = project_file.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    match = re.match(r"\s*config/features\s*=\s*(.+)", line)
                    if match:
                        found.append({"name": "Godot features", "version": match.group(1).strip(), "source": "project.godot"})
            except OSError:
                pass
        addons = sorted({p.parts[p.parts.index("addons") + 1] for p in paths if "addons" in p.parts and len(p.parts) > p.parts.index("addons") + 1})
        found.extend({"name": addon, "version": "plugin/addon", "source": "addons/"} for addon in addons)
    elif engine == "GameMaker":
        for project in (p for p in paths if p.suffix.lower() == ".yyp"):
            found.append({"name": "GameMaker project", "version": "project file", "source": project.relative_to(root).as_posix()})
    for rel in relative:
        if rel.endswith(("requirements.txt", "pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "cmakelists.txt")):
            found.append({"name": Path(rel).name, "version": "manifest", "source": rel})
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for item in found:
        unique[(item["name"], item["source"])] = item
    return list(unique.values())


def _classify_portability(engine: str, root: Path, paths: list[Path], text_findings: dict[str, list[str]]) -> dict[str, Any]:
    ext_counts = Counter(path.suffix.lower() for path in paths)
    languages = Counter(LANGUAGES[ext] for ext, count in ext_counts.items() if ext in LANGUAGES for _ in range(count))
    resource_exts = {".png", ".jpg", ".jpeg", ".webp", ".tga", ".wav", ".mp3", ".ogg", ".mp4", ".fbx", ".obj", ".blend", ".asset", ".pak", ".uasset"}
    total_resource_bytes = 0
    largest_assets: list[dict[str, Any]] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if path.suffix.lower() in resource_exts:
            total_resource_bytes += size
            largest_assets.append({"path": path.relative_to(root).as_posix(), "size_bytes": size})
    largest_assets.sort(key=lambda item: item["size_bytes"], reverse=True)

    text_findings = {key: sorted(set(value)) for key, value in text_findings.items() if value}
    android_configs = [p.relative_to(root).as_posix() for p in paths if p.name in {"AndroidManifest.xml", "settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradlew"}]
    has_manifest = any(path.name == "AndroidManifest.xml" for path in paths)
    has_android_gradle = False
    for path in paths:
        if path.name not in {"build.gradle", "build.gradle.kts"}:
            continue
        try:
            if re.search(r"com\.android\.(?:application|library)|com\.android\.tools\.build:gradle", path.read_text(encoding="utf-8", errors="replace")):
                has_android_gradle = True
                break
        except OSError:
            pass
    issues: list[dict[str, str]] = []
    if text_findings.get("windows_api"):
        issues.append({"severity": "high", "area": "APIs Windows/desktop", "evidence": ", ".join(text_findings["windows_api"][:8]), "action": "Substituir por APIs multiplataforma ou implementações Android."})
    if text_findings.get("desktop_file_api"):
        issues.append({"severity": "medium", "area": "Acesso a arquivos/UI desktop", "evidence": ", ".join(text_findings["desktop_file_api"][:8]), "action": "Rever permissões Android, Storage Access Framework e diálogos nativos."})
    if text_findings.get("keyboard") or text_findings.get("mouse"):
        evidence = sorted(set(text_findings.get("keyboard", []) + text_findings.get("mouse", [])))
        issues.append({"severity": "medium", "area": "Controles teclado/mouse", "evidence": ", ".join(evidence[:8]), "action": "Projetar controles touch configuráveis, zonas de toque, gestos e alternativa para teclado/controle externo."})
    if text_findings.get("resolution"):
        issues.append({"severity": "medium", "area": "Resolução e proporção", "evidence": ", ".join(text_findings["resolution"][:8]), "action": "Validar proporções móveis, orientação, áreas seguras, escala de UI e redimensionamento."})
    if text_findings.get("graphics") or text_findings.get("performance"):
        evidence = sorted(set(text_findings.get("graphics", []) + text_findings.get("performance", [])))
        issues.append({"severity": "medium", "area": "Gráficos e desempenho", "evidence": ", ".join(evidence[:8]), "action": "Medir em dispositivo; rever shaders, efeitos, sombras, resolução de texturas, LOD, memória e frame pacing."})
    if total_resource_bytes > 1_500_000_000:
        issues.append({"severity": "medium", "area": "Volume de assets", "evidence": f"{total_resource_bytes} bytes em arquivos de recursos", "action": "Avaliar compressão, streaming, pacotes e armazenamento disponível no dispositivo."})

    definition = ENGINE_DEFINITIONS.get(engine, {})
    engine_actions = {
        "Unity": ["Verificar versão do Editor e módulos Android Build Support/SDK/NDK.", "Auditar plugins nativos e pacotes que não oferecem ABI Android.", "Ajustar Input System para touch e testar URP/shaders no dispositivo."],
        "Unreal Engine": ["Verificar versão do Unreal e toolchain Android/NDK suportada.", "Rever plugins, módulos C++ e configurações de target Android.", "Medir memória, shaders e recursos de renderização no hardware alvo."],
        "Godot": ["Confirmar versão do Godot e templates Android correspondentes.", "Rever renderer e compatibilidade de shaders para o conjunto de dispositivos.", "Adaptar InputMap, gestos, áreas seguras e escala de viewport."],
        "GameMaker": ["Confirmar runtime/export Android e módulos de exportação disponíveis.", "Rever mapeamento de teclado/mouse para controles touch.", "Validar texturas, surfaces, shaders e limites de memória móvel."],
    }.get(engine, ["Identificar o sistema de build e a toolchain Android exigida.", "Separar lógica portátil de APIs desktop e desenhar uma camada de entrada touch.", "Medir gráficos, memória e desempenho em um dispositivo Android real."])
    plan = [
        "Confirmar engine, versão, dependências e licenças dos plugins.",
        "Priorizar incompatibilidades comprovadas no relatório; não substituir APIs apenas por heurística.",
        *engine_actions,
        "Preparar/exportar uma build Android somente com toolchain real compatível e testar em dispositivo.",
    ]
    return {
        "languages": dict(languages.most_common()),
        "extension_counts": dict(sorted(ext_counts.items())),
        "source_files": sum(count for ext, count in ext_counts.items() if ext in LANGUAGES),
        "android_build_configs": android_configs,
        "control_findings": {key: text_findings.get(key, []) for key in ("keyboard", "mouse")},
        "resolution_findings": text_findings.get("resolution", []),
        "graphics_findings": text_findings.get("graphics", []),
        "performance_findings": text_findings.get("performance", []),
        "incompatibilities": issues,
        "resource_bytes": total_resource_bytes,
        "largest_assets": largest_assets[:20],
        "plan": plan,
        "external_build_host": definition.get("host", "Dependente das ferramentas específicas do projeto; não presumir build no Termux."),
        "available_tools": {
            name: shutil.which(name) for name in
            ("java", "gradle", "adb", "godot", "godot4", "unity", "UnrealEditor", "GameMaker")
        },
        "build_ready": bool(has_manifest and has_android_gradle),
    }


def analyze_game_project(project: Path) -> dict[str, Any]:
    root = project.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Projeto precisa ser um diretório.")
    paths = list(_walk(root))
    engine, detected_engines = detect_engine(root, paths)
    findings: dict[str, list[str]] = {key: [] for key in PATTERNS}
    candidates: dict[str, set[str]] = {}
    inspected = 0
    skipped_large = 0
    for path in paths:
        suffix = path.suffix.lower()
        rel = path.relative_to(root).as_posix()
        relevant_config = path.name in {"ProjectSettings.asset", "project.godot", "DefaultEngine.ini", "DefaultGameUserSettings.ini", "project.yyp", "manifest.json", "AndroidManifest.xml"}
        if suffix not in SOURCE_EXTENSIONS and not relevant_config:
            continue
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                skipped_large += 1
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            inspected += 1
        except OSError:
            continue
        matched = []
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                findings[category].append(rel)
                matched.append(category)
        if matched or relevant_config or suffix in {".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".gd", ".gml", ".java", ".kt", ".gradle", ".ini", ".cfg", ".uproject", ".yyp"}:
            reasons = matched or (["configuração de engine/build"] if relevant_config else ["código ou script de jogo"])
            candidates.setdefault(rel, set()).update(reasons)

    primary_engine = detected_engines[0] if len(detected_engines) == 1 else engine
    text_findings = {key: sorted(set(value)) for key, value in findings.items()}
    portability = _classify_portability(primary_engine, root, paths, text_findings)
    dependencies = _engine_dependencies(root, primary_engine, paths)
    directories = sorted({path.relative_to(root).parts[0] for path in paths if path.relative_to(root).parts})
    engine_markers = [path.relative_to(root).as_posix() for path in paths if path.name == "project.godot" or path.suffix.lower() in {".uproject", ".yyp"} or "ProjectSettings/ProjectVersion.txt" in path.relative_to(root).as_posix()]
    for path in paths:
        if path.suffix.lower() in {".uproject", ".yyp"} or path.name in {"project.godot", "ProjectVersion.txt"}:
            candidates.setdefault(path.relative_to(root).as_posix(), set()).add("marcador principal da engine")
    return {
        "tool": "OffTec PortAI Game Recreation Engine",
        "version": "0.3.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project": str(root),
        "engine": engine,
        "detected_engines": detected_engines,
        "engine_markers": engine_markers,
        "files_found": len(paths),
        "text_files_inspected": inspected,
        "large_text_files_skipped": skipped_large,
        "top_level_directories": directories,
        "dependencies": dependencies,
        "findings": text_findings,
        "candidate_files": [
            {"path": name, "reasons": sorted(reasons)}
            for name, reasons in sorted(candidates.items())
        ],
        **portability,
        "limitations": [
            "Análise estática heurística: evidências precisam de revisão no contexto do projeto.",
            "Nenhuma conversão automática universal é prometida; engine, plugins, licenças e assets podem bloquear a portabilidade.",
            "Split builds, DRM, serviços online, anti-cheat e código nativo exigem análise e autorização próprias.",
            "Compilação só é reportada quando uma ferramenta real executa e retorna sucesso; não há build simulado.",
        ],
    }


def save_game_report(report: dict[str, Any], reports_dir: Path) -> tuple[Path, Path]:
    from portai_i18n import translate_text

    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    base = reports_dir / f"game-portability-{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Relatório de Portabilidade de Jogo", "",
        f"- Projeto: `{report['project']}`", f"- Engine detectada: **{report['engine']}**",
        f"- Arquivos encontrados: {report['files_found']}",
        f"- Arquivos de código/configuração lidos: {report['text_files_inspected']}",
        f"- Host de build indicado: {report['external_build_host']}",
        "", "## Linguagens", *([f"- {name}: {count}" for name, count in report["languages"].items()] or ["- Nenhuma reconhecida"]),
        "", "## Dependências e plugins", *([f"- {d['name']} ({d['version']}) — `{d['source']}`" for d in report["dependencies"]] or ["- Nenhuma detectada pelos manifests reconhecidos"]),
        "", "## Controles e compatibilidade",
    ]
    lines += [f"- **{item['severity']} — {item['area']}:** {item['evidence']}. Ação sugerida: {item['action']}" for item in report["incompatibilities"]] or ["- Nenhuma incompatibilidade conhecida detectada; isso não comprova compatibilidade Android."]
    lines += ["", "## Arquivos candidatos à revisão"]
    lines += [f"- `{item['path']}` — {', '.join(item['reasons'])}" for item in report["candidate_files"]] or ["- Nenhum arquivo candidato"]
    lines += ["", "## Plano de portabilidade"] + [f"{index}. {step}" for index, step in enumerate(report["plan"], 1)]
    lines += ["", "## Limitações"] + [f"- {value}" for value in report["limitations"]]
    md_path.write_text(translate_text("\n".join(lines) + "\n"), encoding="utf-8")
    return md_path, json_path


def _relative_workspace_file(workspace: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError(f"Caminho não permitido dentro da workspace: {relative}")
    resolved_workspace = workspace.resolve(strict=True)
    resolved = (resolved_workspace / candidate).resolve(strict=False)
    if resolved != resolved_workspace and resolved_workspace not in resolved.parents:
        raise ValueError(f"Caminho escapou da workspace: {relative}")
    current = resolved_workspace
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Links simbólicos não são autorizados para alterações: {relative}")
    return resolved


def backup_authorized_files(workspace: Path, authorized: list[str], backups_root: Path) -> Path:
    """Back up approved targets outside the Codex-writable workspace."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = backups_root / workspace.name / stamp
    backup.mkdir(parents=True, exist_ok=False)
    entries = []
    for relative in dict.fromkeys(authorized):
        source = _relative_workspace_file(workspace, relative)
        exists = source.is_file()
        backup_rel = Path("files") / relative
        if exists:
            destination = backup / backup_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        else:
            digest = None
        entries.append({"path": relative, "existed": exists, "backup": backup_rel.as_posix() if exists else None, "sha256": digest})
    manifest = {"workspace": str(workspace.resolve()), "created_at": stamp, "entries": entries}
    manifest_path = backup / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def restore_authorized_backup(workspace: Path, manifest_path: Path) -> list[str]:
    """Restore an explicit backup; newly created authorized paths are removed."""
    manifest_path = manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("workspace") != str(workspace.resolve(strict=True)):
        raise ValueError("Este backup pertence a outra workspace.")
    restored = []
    backup_root = manifest_path.parent.resolve(strict=True)
    for entry in manifest.get("entries", []):
        target = _relative_workspace_file(workspace, entry["path"])
        if entry.get("existed"):
            source = (backup_root / entry["backup"]).resolve(strict=True)
            if backup_root not in source.parents:
                raise ValueError("Caminho de backup inválido.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"Não é possível recuperar o arquivo criado: {entry['path']}")
            target.unlink()
        restored.append(entry["path"])
    return restored


def workspace_inventory(workspace: Path) -> dict[str, tuple[int, int]]:
    """Fast change inventory based on relative paths, sizes and mtimes."""
    inventory = {}
    for current, dirs, files in os.walk(workspace, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS and not (base / name).is_symlink()]
        for name in files:
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            relative = path.relative_to(workspace).as_posix()
            if relative == ".portai-state.json":
                continue
            inventory[relative] = (info.st_size, info.st_mtime_ns)
    return inventory
