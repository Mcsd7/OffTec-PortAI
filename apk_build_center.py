"""APK build operations coordinated through PortAI's existing build and APK tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from portai_apk_translator import (
    ApkPipelineError, align_apk, decode_apk, inspect_apk, rebuild_apk, sign_apk,
)


def detect_gradle_projects(root: Path) -> list[Path]:
    """Find distinct project roots that contain either supported Gradle build file."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        return []
    projects = set()
    for name in ("build.gradle", "build.gradle.kts"):
        for build_file in root.rglob(name):
            if build_file.is_file() and not any(
                part in {".git", ".gradle", "build", "node_modules", "workspaces"}
                for part in build_file.relative_to(root).parts
            ):
                project = build_file.parent
                for parent in (project, *project.parents):
                    if parent == root.parent:
                        break
                    if any((parent / name).is_file() for name in ("settings.gradle", "settings.gradle.kts", "gradlew")):
                        project = parent
                        break
                projects.add(project.resolve())
    return sorted(projects, key=lambda path: (len(path.parts), str(path).lower()))


class ApkBuildCenter:
    def __init__(self, root: Path, workspaces: Path, reports: Path,
                 run_build: Callable, validate_apk: Callable,
                 ask_prompt: Callable, ask_confirm: Callable, console):
        self.root = root
        self.workspaces = workspaces
        self.reports = reports
        self.run_build = run_build
        self.validate_apk = validate_apk
        self.ask_prompt = ask_prompt
        self.ask_confirm = ask_confirm
        self.console = console

    def _workspace(self, operation: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.workspaces / f"apk-build-center-{operation}-{stamp}"
        path.mkdir(parents=True, exist_ok=False)
        return path

    def _history(self, operation: str, project: str | None, commands: list[list[str]],
                 result: str, apk: Path | None, details: dict | None = None) -> Path:
        self.reports.mkdir(parents=True, exist_ok=True)
        history_path = self.reports / "apk-build-center-history.json"
        if history_path.is_symlink():
            raise OSError("O arquivo de histórico não pode ser um link simbólico.")
        records = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        record = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "operation": operation, "project": project,
            "commands": commands, "result": result,
            "apk_path": str(apk) if apk else None,
            "details": details or {},
        }
        records.append(record)
        temp = history_path.with_suffix(".json.tmp")
        if history_path.exists():
            backup = self.reports / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / history_path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(history_path, backup)
        temp.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(history_path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        report_json = self.reports / f"apk-build-{stamp}.json"
        report_md = self.reports / f"apk-build-{stamp}.md"
        report_json.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_md.write_text(
            "# PortAI APK Build Center\n\n"
            f"- Data: {record['created_at']}\n- Operação: {operation}\n"
            f"- Projeto: {project or 'N/A'}\n- Resultado: {result}\n"
            f"- APK: {record['apk_path'] or 'N/A'}\n"
            "- Comandos: " + ("; ".join(" ".join(command) for command in commands) or "nenhum") + "\n",
            encoding="utf-8",
        )
        return report_json

    def compile_project(self) -> None:
        raw = self.ask_prompt("Pasta do projeto Android")
        source = Path(raw).expanduser().resolve()
        projects = detect_gradle_projects(source)
        if not projects:
            self.console.print("[red]Não encontrei build.gradle ou build.gradle.kts nesta pasta.[/]")
            return
        self.console.print("Projetos Gradle encontrados:")
        for index, project in enumerate(projects, 1):
            self.console.print(f"{index}. {project}")
        choice = self.ask_prompt("Projeto para compilar", choices=[str(i) for i in range(1, len(projects) + 1)], default="1")
        project = projects[int(choice) - 1]
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not sdk and (Path.home() / "android-sdk").is_dir():
            sdk = str(Path.home() / "android-sdk")
        available = {name: shutil.which(name) for name in ("java", "gradle", "cmake", "aapt", "apksigner")}
        missing = [name for name in ("java", "Android SDK") if not (available.get(name) if name != "Android SDK" else sdk and Path(sdk).is_dir())]
        has_wrapper = (project / "gradlew").is_file()
        if not has_wrapper and not available["gradle"]:
            missing.append("Gradle/gradlew")
        if missing:
            self.console.print(f"[yellow]Dependências ausentes: {', '.join(missing)}. A compilação não foi iniciada.[/]")
            return
        build_files = sorted(
            path.relative_to(project).as_posix()
            for path in project.rglob("build.gradle*")
            if path.is_file() and not any(part in {"build", ".gradle"} for part in path.parts)
        )
        settings_present = any((project / name).is_file() for name in ("settings.gradle", "settings.gradle.kts"))
        manifest_present = any(project.rglob("AndroidManifest.xml"))
        android_plugin = False
        for relative in build_files:
            try:
                source_text = (project / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            android_plugin = android_plugin or "com.android.application" in source_text or "com.android.tools.build:gradle" in source_text
        config_warnings = []
        if not settings_present:
            config_warnings.append("settings.gradle/settings.gradle.kts ausente; projeto pode ser de módulo único.")
        if not manifest_present:
            config_warnings.append("AndroidManifest.xml não encontrado.")
        if not android_plugin:
            config_warnings.append("Plugin Android não identificado nos build files; confira plugins de convenção.")
        if not (Path(sdk) / "platforms").is_dir() or not any((Path(sdk) / "platforms").iterdir()):
            missing.append("Android SDK Platform")
        if missing:
            self.console.print(f"[yellow]Dependências ou configuração ausentes: {', '.join(missing)}. A compilação não foi iniciada.[/]")
            self._history("compile", str(project), [], "missing dependencies", None,
                          {"missing": missing, "config_warnings": config_warnings})
            return
        self.console.print(f"Java: {available['java']}\nGradle: {available['gradle'] or 'wrapper local (se disponível)'}\nAndroid SDK: {sdk}")
        self.console.print(f"Build files: {', '.join(build_files)}")
        for warning in config_warnings:
            self.console.print(f"[yellow]Aviso: {warning}[/]")
        if not self.ask_confirm("Executar assembleDebug neste projeto?", default=False):
            return
        workspace = self._workspace("compile")
        build_project = workspace / "project"
        shutil.copytree(
            project, build_project, symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git", ".gradle", ".idea", ".venv", "venv", "node_modules",
                "__pycache__", "build", "dist", "target", "bin", "obj", "*.apk", "*.aab",
            ),
        )
        code, stdout, stderr, apk = self.run_build(build_project)
        ok = code == 0 and apk is not None and self.validate_apk(apk)
        command = [str(build_project / "gradlew"), "assembleDebug"] if (build_project / "gradlew").is_file() else [available["gradle"] or "gradle", "assembleDebug"]
        report = self._history("compile", str(project), [command], "success" if ok else f"failed ({code})", apk if ok else None,
                               {"workspace": str(workspace), "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:], "available_tools": available, "android_sdk": sdk, "source_preserved": True})
        self.console.print(f"[green]APK compilado e validado: {apk}[/]" if ok else f"[red]Build não concluído (código {code}): {stderr[-1500:]}[/]")
        self.console.print(f"Relatório: {report}")

    def rebuild_apk(self) -> None:
        source = Path(self.ask_prompt("Caminho do APK original").strip()).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != ".apk":
            self.console.print("[red]APK original não encontrado.[/]")
            return
        tool = shutil.which("apktool")
        java = shutil.which("java")
        if not tool or not java:
            self.console.print(f"[yellow]Dependências ausentes: {', '.join(name for name, value in (( 'apktool', tool), ('java', java)) if not value)}. APK preservado; rebuild não iniciado.[/]")
            return
        source_hash = inspect_apk(source)["source_sha256"]
        workspace = self._workspace("rebuild")
        copied = workspace / source.name
        shutil.copy2(source, copied)
        commands: list[list[str]] = []
        try:
            decoded = workspace / "decoded"
            commands.append(decode_apk(copied, decoded, tool, workspace))
            output = workspace / "rebuilt-unsigned.apk"
            _, rebuild_command = rebuild_apk(decoded, output, tool, workspace)
            commands.append(rebuild_command)
            if inspect_apk(source)["source_sha256"] != source_hash:
                raise ApkPipelineError("O APK original mudou durante a reconstrução.")
            result = "success"
            self.console.print(f"[green]APK reconstruído: {output}[/]")
        except (OSError, subprocess.TimeoutExpired, ApkPipelineError) as exc:
            output = None
            result = f"failed: {exc}"
            self.console.print(f"[red]Reconstrução falhou: {exc}[/]")
        report = self._history("rebuild", str(source), commands, result, output, {"workspace": str(workspace), "source_preserved": True})
        self.console.print(f"Relatório: {report}")

    def sign_apk(self) -> None:
        from getpass import getpass
        from portai_i18n import translate_text
        source = Path(self.ask_prompt("Caminho do APK de entrada").strip()).expanduser().resolve()
        keystore = Path(self.ask_prompt("Caminho da sua keystore").strip()).expanduser().resolve()
        if not source.is_file() or not self.validate_apk(source) or not keystore.is_file():
            self.console.print("[red]APK válido e keystore existente são necessários.[/]")
            return
        tool = shutil.which("apksigner")
        if not tool:
            self.console.print("[yellow]apksigner ausente; assinatura não iniciada.[/]")
            return
        alias = self.ask_prompt("Alias da chave")
        store_pass = getpass(translate_text("Senha da keystore: "))
        key_pass = getpass(translate_text("Senha da chave: "))
        workspace = self._workspace("sign")
        output = workspace / f"{source.stem}-signed.apk"
        commands: list[list[str]] = []
        try:
            if not self.ask_confirm("Assinar para um novo arquivo neste workspace?", default=False):
                return
            signed, commands, _ = sign_apk(source, output, tool, keystore, alias, store_pass, key_pass, workspace)
            result = "success"
            self.console.print(f"[green]APK assinado e verificado: {signed}[/]")
        except (OSError, ApkPipelineError) as exc:
            signed, result = None, f"failed: {exc}"
            self.console.print(f"[red]Assinatura falhou: {exc}[/]")
        finally:
            store_pass = ""
            key_pass = ""
        report = self._history("sign", str(source), commands, result, signed, {"keystore": str(keystore), "alias": alias})
        self.console.print(f"Relatório: {report}")

    def verify_apk(self) -> None:
        from portai_apk_translator import _validate_apk
        source = Path(self.ask_prompt("Caminho do APK para verificar").strip()).expanduser().resolve()
        try:
            info = inspect_apk(source)
            info["integrity_valid"] = _validate_apk(source)
            tool = shutil.which("apksigner")
            commands = [[tool, "verify", "--verbose", str(source)]] if tool else []
            if tool:
                proc = subprocess.run(commands[0], capture_output=True, text=True, timeout=300, check=False)
                info["signature_valid"] = proc.returncode == 0
                info["signature_details"] = (proc.stdout or proc.stderr)[-2000:]
            else:
                info["signature_valid"] = None
                info["signature_details"] = "apksigner ausente"
            info["warnings"] = []
            if info.get("split_hint") or info.get("neighboring_apks"):
                info["warnings"].append("O arquivo pode fazer parte de um split APK; verifique o conjunto completo.")
            if not info.get("architectures"):
                info["warnings"].append("Nenhuma arquitetura de biblioteca nativa detectada.")
            if info["signature_valid"] is False:
                info["warnings"].append("A assinatura APK não foi validada pelo apksigner.")
            elif info["signature_valid"] is None:
                info["warnings"].append("Assinatura não verificada: apksigner não está instalado.")
            apktool = shutil.which("apktool")
            if apktool and info["integrity_valid"]:
                workspace = self._workspace("inspect")
                command = [apktool, "d", "-f", "-s", "-o", str(workspace / "decoded"), str(source)]
                proc = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=600, check=False)
                commands.append(command)
                if proc.returncode == 0:
                    from portai_apk_translator import read_decoded_manifest
                    info.update(read_decoded_manifest(workspace / "decoded" / "AndroidManifest.xml"))
                else:
                    info["manifest_details_error"] = (proc.stderr or proc.stdout)[-2000:]
                    info["warnings"].append("package name e versão não puderam ser decodificados.")
            elif not apktool:
                info["warnings"].append("package name e versão não decodificados: apktool não está instalado.")
            result = "success" if info["integrity_valid"] else "invalid APK"
            report = self._history("verify", str(source), commands, result, source if info["integrity_valid"] else None, info)
            self.console.print(json.dumps(info, ensure_ascii=False, indent=2))
            self.console.print(f"Relatório: {report}")
        except (OSError, ValueError, ApkPipelineError, subprocess.TimeoutExpired) as exc:
            self._history("verify", str(source), [], f"failed: {exc}", None, {"error": str(exc)})
            self.console.print(f"[red]Verificação falhou: {exc}[/]")

    def align_apk(self) -> None:
        source = Path(self.ask_prompt("Caminho do APK de entrada").strip()).expanduser().resolve()
        tool = shutil.which("zipalign")
        if not source.is_file() or not self.validate_apk(source):
            self.console.print("[red]APK de entrada inválido.[/]")
            return
        if not tool:
            self.console.print("[yellow]zipalign ausente; alinhamento não iniciado.[/]")
            return
        workspace = self._workspace("align")
        output = workspace / f"{source.stem}-aligned.apk"
        try:
            aligned, command = align_apk(source, output, tool, workspace)
            result = "success"
            self.console.print(f"[green]APK alinhado e validado: {aligned}[/]")
        except (OSError, ApkPipelineError) as exc:
            aligned, command, result = None, [], f"failed: {exc}"
            self.console.print(f"[red]Alinhamento falhou: {exc}[/]")
        report = self._history("align", str(source), [command] if command else [], result, aligned, {"source_preserved": True})
        self.console.print(f"Relatório: {report}")

    def show_history(self) -> None:
        path = self.reports / "apk-build-center-history.json"
        if not path.is_file():
            self.console.print("Ainda não há builds registrados.")
            return
        for item in json.loads(path.read_text(encoding="utf-8"))[-20:]:
            self.console.print(f"{item['created_at']} — {item['operation']} — {item['result']}\n  Projeto: {item.get('project')}\n  APK: {item.get('apk_path')}")

    def menu(self) -> None:
        actions = {
            "1": ("Compilar projeto Android", self.compile_project),
            "2": ("Reconstruir APK com Apktool", self.rebuild_apk),
            "3": ("Assinar APK com sua keystore", self.sign_apk),
            "4": ("Verificar integridade e assinatura", self.verify_apk),
            "5": ("Alinhar APK com zipalign", self.align_apk),
            "6": ("Histórico de builds", self.show_history),
        }
        while True:
            self.console.print("\n[bold bright_magenta]APK BUILD CENTER[/]")
            for number, (label, _) in actions.items():
                self.console.print(f"{number}. {label}")
            self.console.print("0. Voltar")
            choice = self.ask_prompt("APK Build Center", choices=[*actions, "0"], default="0")
            if choice == "0":
                return
            actions[choice][1]()
