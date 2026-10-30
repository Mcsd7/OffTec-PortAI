"""APK localization pipeline built around the existing Universal Translation Engine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable
import xml.etree.ElementTree as ET

from portai_translation import LANGUAGES, TranslationEngine, TranslationResult

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID_ATTR = f"{{{ANDROID_NS}}}"
APK_TARGETS = ("en", "es", "ar", "hi")
ABIS = {"armeabi-v7a", "arm64-v8a", "x86", "x86_64", "riscv64"}


class ApkPipelineError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "app"


def inspect_apk(apk: Path) -> dict:
    """Read ZIP-level APK facts without modifying or executing the package."""
    path = apk.expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".apk":
        raise ApkPipelineError("Selecione um arquivo .apk existente.")
    try:
        with zipfile.ZipFile(path) as package:
            corrupt = package.testzip()
            if corrupt:
                raise ApkPipelineError(f"APK/ZIP corrompido no arquivo {corrupt}.")
            names = package.namelist()
    except zipfile.BadZipFile as exc:
        raise ApkPipelineError("O arquivo selecionado não é um APK ZIP válido.") from exc
    if "AndroidManifest.xml" not in names:
        raise ApkPipelineError("O APK não contém AndroidManifest.xml.")
    architectures = sorted({part for name in names for part in name.split("/")[:2] if part in ABIS and name.startswith("lib/")})
    siblings = sorted(str(p) for p in path.parent.glob("*.apk") if p.resolve() != path)
    split_hint = bool(re.search(r"(?:^|[-_])(?:split_config|config\.(?:arm|x86))(?=[-_.]|$)", path.name, re.I))
    return {
        "source_apk": str(path),
        "source_sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "package": None,
        "version_code": None,
        "version_name": None,
        "architectures": architectures,
        "has_dex": any(re.fullmatch(r"classes(?:\d+)?\.dex", Path(name).name) for name in names),
        "split_hint": split_hint,
        "neighboring_apks": siblings,
        "warnings": [],
    }


def read_decoded_manifest(manifest: Path) -> dict[str, str | None]:
    try:
        root = ET.parse(manifest).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ApkPipelineError(f"Não foi possível ler o Manifest decodificado pelo Apktool: {exc}") from exc
    application = next((node for node in root if node.tag.rsplit("}", 1)[-1] == "application"), None)
    return {
        "package": root.attrib.get("package"),
        "version_code": root.attrib.get(ANDROID_ATTR + "versionCode"),
        "version_name": root.attrib.get(ANDROID_ATTR + "versionName"),
        "split": root.attrib.get("split"),
        "application_label": application.attrib.get(ANDROID_ATTR + "label") if application is not None else None,
    }


def find_android_value_files(decoded: Path) -> list[Path]:
    resources = decoded / "res"
    if not resources.is_dir():
        return []
    return sorted(
        path for path in resources.glob("values/*.xml")
        if path.is_file() and not path.is_symlink()
    )


def align_apk(apk: Path, output: Path, tool: str | None, cwd: Path,
              runner: Callable = subprocess.run, timeout: int = 3600) -> tuple[Path, list[str]]:
    """Align an APK without replacing its input, then validate the new file."""
    if not tool:
        raise ApkPipelineError("Dependência obrigatória ausente: zipalign.")
    if apk.resolve() == output.resolve():
        raise ApkPipelineError("O APK alinhado precisa ter um caminho de saída diferente do original.")
    source_hash = inspect_apk(apk)["source_sha256"]
    command = [tool, "-f", "-p", "4", str(apk), str(output)]
    proc = _run_apk_tool(command, cwd, runner=runner, timeout=timeout)
    if not _validate_apk(output):
        raise ApkPipelineError("zipalign não produziu um APK íntegro com Manifest.")
    if inspect_apk(apk)["source_sha256"] != source_hash:
        raise ApkPipelineError("O APK original mudou durante o alinhamento.")
    return output, command


def decode_apk(apk: Path, decoded: Path, tool: str | None, cwd: Path,
               runner: Callable = subprocess.run, timeout: int = 3600) -> list[str]:
    if not tool:
        raise ApkPipelineError("Dependência obrigatória ausente: apktool.")
    command = [tool, "d", "-f", "-o", str(decoded), str(apk)]
    _run_apk_tool(command, cwd, runner=runner, timeout=timeout)
    if not (decoded / "AndroidManifest.xml").is_file():
        raise ApkPipelineError("Apktool não produziu um Manifest decodificado.")
    return command


def rebuild_apk(decoded: Path, output: Path, tool: str | None, cwd: Path,
                runner: Callable = subprocess.run, timeout: int = 3600) -> tuple[Path, list[str]]:
    if not tool:
        raise ApkPipelineError("Dependência obrigatória ausente: apktool.")
    command = [tool, "b", str(decoded), "-o", str(output)]
    _run_apk_tool(command, cwd, runner=runner, timeout=timeout)
    if not _validate_apk(output):
        raise ApkPipelineError("Apktool não produziu um APK válido.")
    return output, command


def sign_apk(apk: Path, output: Path, tool: str | None, keystore: Path, alias: str,
             keystore_password: str, key_password: str, cwd: Path,
             runner: Callable = subprocess.run, timeout: int = 3600) -> tuple[Path, list[list[str]], object]:
    """Sign to a distinct output path and verify the signature without logging secrets."""
    if not tool:
        raise ApkPipelineError("Dependência obrigatória ausente: apksigner.")
    if apk.resolve() == output.resolve():
        raise ApkPipelineError("O APK assinado precisa ter um caminho de saída diferente do original.")
    source_hash = inspect_apk(apk)["source_sha256"]
    env = os.environ.copy()
    env["PORTAI_APK_KS_PASS"] = keystore_password
    env["PORTAI_APK_KEY_PASS"] = key_password
    sign_command = [tool, "sign", "--ks", str(keystore), "--ks-key-alias", alias,
                    "--ks-pass", "env:PORTAI_APK_KS_PASS", "--key-pass", "env:PORTAI_APK_KEY_PASS",
                    "--out", str(output), str(apk)]
    verify_command = [tool, "verify", "--verbose", str(output)]
    try:
        try:
            _run_apk_tool(sign_command, cwd, env=env, runner=runner, timeout=timeout)
        except ApkPipelineError as exc:
            message = str(exc)
            for secret in (keystore_password, key_password):
                if secret:
                    message = message.replace(secret, "[redacted]")
            raise ApkPipelineError(message) from exc
    finally:
        env.pop("PORTAI_APK_KS_PASS", None)
        env.pop("PORTAI_APK_KEY_PASS", None)
    if not _validate_apk(output):
        raise ApkPipelineError("ApkSigner não produziu um APK íntegro com Manifest.")
    verified = _run_apk_tool(verify_command, cwd, runner=runner, timeout=timeout)
    if inspect_apk(apk)["source_sha256"] != source_hash:
        raise ApkPipelineError("O APK original mudou durante a assinatura.")
    return output, [sign_command, verify_command], verified


def _run_apk_tool(command: list[str], cwd: Path, env: dict | None = None,
                  runner: Callable = subprocess.run, timeout: int = 3600):
    try:
        proc = runner(command, cwd=str(cwd), env=env, text=True,
                      capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApkPipelineError(f"Falha executando {Path(command[0]).name}: {exc}") from exc
    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "sem detalhes do processo")[-6000:]
        raise ApkPipelineError(f"{Path(command[0]).name} terminou com código {proc.returncode}: {detail}")
    return proc


class ApkTranslator:
    """Decode, localize, rebuild, sign and validate an APK in a fresh workspace."""

    def __init__(
        self,
        engine: TranslationEngine,
        tools: dict[str, str | None] | None = None,
        timeout: int = 3600,
        runner: Callable = subprocess.run,
    ):
        self.engine = engine
        self.tools = tools or {name: shutil.which(name) for name in ("apktool", "jadx", "apksigner", "zipalign")}
        self.timeout = timeout
        self.runner = runner

    def _run(self, command: list[str], cwd: Path | None = None, env: dict | None = None):
        try:
            proc = self.runner(command, cwd=str(cwd) if cwd else None, env=env, text=True,
                               capture_output=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ApkPipelineError(f"Falha executando {Path(command[0]).name}: {exc}") from exc
        if proc.returncode:
            detail = (proc.stderr or proc.stdout or "sem detalhes do processo")[-6000:]
            raise ApkPipelineError(f"{Path(command[0]).name} terminou com código {proc.returncode}: {detail}")
        return proc

    @staticmethod
    def _write_report(workspace: Path, report: dict) -> Path:
        path = workspace / "apk-translation-report.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        return path

    def run(
        self,
        apk: Path,
        workspace_root: Path,
        targets: list[str],
        reviewer: Callable[[TranslationResult, TranslationEngine, list[str]], None],
        keystore: Path,
        key_alias: str,
        keystore_password: str,
        key_password: str,
        source_language: str = "en",
    ) -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        workspace_root = workspace_root.expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = workspace_root / f"apk-translation-{_safe_name(Path(apk).stem)}-{stamp}"
        workspace.mkdir()
        report = {
            "tool": "OffTec PortAI APK Translator", "version": "0.3.0",
            "status": "in_progress", "workspace": str(workspace), "source_apk": str(apk),
            "package": None, "version_code": None, "version_name": None,
            "architectures": [], "split_apk": False, "provider": self.engine.provider.name,
            "targets": targets, "tools": self.tools.copy(), "modified_files": [],
            "limitations": [
                "This flow does not bypass DRM, licensing, signature checks or other third-party protections.",
                "Split APKs require the complete compatible set; this workflow processes only the selected APK.",
                "Native libraries are carried through Apktool; their machine code is not translated or rebuilt.",
                "JADX output is supplemental analysis and is not used as the rebuild source.",
            ],
            "warnings": [], "errors": [], "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "output_apk": None, "validated": False,
        }
        original_hash = None
        try:
            analysis = inspect_apk(apk)
            original_hash = analysis["source_sha256"]
            report.update({key: analysis[key] for key in ("source_apk", "source_sha256", "size_bytes", "architectures", "split_hint", "neighboring_apks")})
            report["has_dex"] = analysis["has_dex"]
            working_apk = workspace / "input" / Path(analysis["source_apk"]).name
            working_apk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(analysis["source_apk"], working_apk)
            if _sha256(working_apk) != original_hash:
                raise ApkPipelineError("A cópia do APK no workspace não corresponde ao original.")
            report["workspace_input_apk"] = str(working_apk)
            report["split_apk"] = analysis["split_hint"] or bool(analysis["neighboring_apks"])
            if analysis["neighboring_apks"]:
                report["warnings"].append("Há outros APKs ao lado do arquivo; o conjunto pode ser um split APK incompleto.")
            if not analysis["architectures"]:
                report["warnings"].append("Nenhuma ABI nativa foi encontrada no APK; o código pode ser somente DEX ou usar outro empacotamento.")
            if report["split_apk"]:
                report["warnings"].append("Split APKs precisam do conjunto completo para instalação e reconstrução confiáveis.")
            for required in ("apktool", "apksigner", "java"):
                if not self.tools.get(required):
                    raise ApkPipelineError(f"Dependência obrigatória ausente: {required}.")
            keystore = keystore.expanduser().resolve(strict=True)
            if not keystore.is_file():
                raise ApkPipelineError("O arquivo de keystore informado não é um arquivo regular.")

            decoded = workspace / "decoded"
            decode_apk(working_apk, decoded, self.tools.get("apktool"), workspace, self.runner, self.timeout)
            manifest_info = read_decoded_manifest(decoded / "AndroidManifest.xml")
            report.update({key: manifest_info[key] for key in ("package", "version_code", "version_name", "application_label")})
            if manifest_info["split"]:
                report["split_apk"] = True
                report["warnings"].append(f"Manifest declara split={manifest_info['split']}; reconstruir somente este split pode não produzir um app instalável.")
            if "ar" in targets:
                manifest_path = decoded / "AndroidManifest.xml"
                manifest_root = ET.parse(manifest_path).getroot()
                application = next((node for node in manifest_root if node.tag.rsplit("}", 1)[-1] == "application"), None)
                if application is None:
                    report["warnings"].append("Manifest sem elemento application; supportsRtl não pôde ser habilitado automaticamente.")
                elif application.attrib.get(ANDROID_ATTR + "supportsRtl", "false").lower() != "true":
                    backup = workspace / "backups" / "AndroidManifest.xml"
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(manifest_path, backup)
                    application.set(ANDROID_ATTR + "supportsRtl", "true")
                    manifest_path.write_bytes(ET.tostring(manifest_root, encoding="utf-8", xml_declaration=True))
                    report["modified_files"].append("AndroidManifest.xml")

            if self.tools.get("jadx"):
                jadx_dir = workspace / "jadx-analysis"
                try:
                    self._run([self.tools["jadx"], "-d", str(jadx_dir), str(working_apk)], workspace)
                    report["jadx_analysis"] = "concluída"
                except ApkPipelineError as exc:
                    report["jadx_analysis"] = "falhou"
                    report["warnings"].append(f"JADX complementar: {exc}")
            else:
                report["jadx_analysis"] = "indisponível"
                report["warnings"].append("JADX não encontrado; a análise complementar de código foi ignorada.")

            resource_files = find_android_value_files(decoded)
            if (decoded / "res").is_dir() and any((decoded / "res").glob("values-*")):
                report["warnings"].append("Somente recursos base de res/values foram usados como origem; qualifiers de idioma existentes foram preservados.")
            if not resource_files:
                raise ApkPipelineError("Apktool não encontrou XML em res/values* para localizar.")
            by_name: dict[str, list[Path]] = {}
            for path in resource_files:
                by_name.setdefault(path.name, []).append(path)
            collisions = {name: paths for name, paths in by_name.items() if len(paths) > 1}
            if collisions:
                names = ", ".join(sorted(collisions))
                raise ApkPipelineError(f"Arquivos Android com nomes repetidos em qualifiers diferentes não podem ser mesclados com segurança: {names}")

            staging = workspace / "translation-input"
            for path in resource_files:
                relative = path.relative_to(decoded / "res")
                dest = staging / "res" / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            result = self.engine.scan(staging, source_language)
            if not result.entries:
                raise ApkPipelineError("Nenhuma string traduzível encontrada nos recursos values Android.")
            self.engine.translate(result, targets)
            reviewer(result, self.engine, targets)
            approved = [entry for entry in result.entries if any(entry.target_status.get(locale) == "approved" for locale in targets)]
            if not approved:
                raise ApkPipelineError("Nenhuma tradução foi aprovada; APK não reconstruído.")

            locale_output = workspace / "localized-resources"
            translation_report = self.engine.apply(result, staging, locale_output, targets)
            report["translation_report"] = str(translation_report)
            report["translation_summary"] = {
                "entries": len(result.entries),
                "approved": sum(1 for entry in result.entries for locale in targets if entry.target_status.get(locale) == "approved"),
                "untranslated_or_unapproved": sum(1 for entry in result.entries for locale in targets if entry.target_status.get(locale) != "approved"),
                "scan_errors": result.scan_errors,
            }
            for locale in targets:
                android_locale = LANGUAGES[locale]["android"] or locale
                generated = locale_output / locale / f"values-{android_locale}"
                files = sorted(generated.glob("*.xml")) if generated.is_dir() else []
                for localized in files:
                    destination = decoded / "res" / f"values-{android_locale}" / localized.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        backup = workspace / "backups" / destination.relative_to(decoded)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(destination, backup)
                        report["warnings"].append(f"Recurso de destino existente salvo em backup: {backup.relative_to(workspace)}")
                    shutil.copy2(localized, destination)
                    relative = str(destination.relative_to(decoded))
                    if relative not in report["modified_files"]:
                        report["modified_files"].append(relative)

            unsigned = workspace / "unsigned.apk"
            rebuild_apk(decoded, unsigned, self.tools.get("apktool"), workspace, self.runner, self.timeout)
            sign_input = unsigned
            if self.tools.get("zipalign"):
                aligned = workspace / "aligned.apk"
                align_apk(unsigned, aligned, self.tools["zipalign"], workspace, self.runner, self.timeout)
                sign_input = aligned
            else:
                report["warnings"].append("zipalign não encontrado; APK será assinado sem alinhamento otimizado.")

            signed = workspace / "translated-signed.apk"
            _, signing_commands, verified = sign_apk(
                sign_input, signed, self.tools.get("apksigner"), keystore, key_alias,
                keystore_password, key_password, workspace, self.runner, self.timeout,
            )
            if not _validate_apk(signed):
                raise ApkPipelineError("APK assinado falhou na validação ZIP/manifesto.")
            after_hash = _sha256(Path(apk).expanduser().resolve())
            report["source_unchanged"] = after_hash == original_hash
            if not report["source_unchanged"]:
                raise ApkPipelineError("Hash do APK original mudou durante o processamento; resultado não será declarado válido.")
            report["signature_verification"] = (verified.stdout or verified.stderr).strip()[-3000:]
            report["output_apk"] = str(signed)
            report["validated"] = True
            report["status"] = "success"
        except Exception as exc:
            report["status"] = "failed"
            report["errors"].append(str(exc))
            if original_hash and Path(apk).is_file():
                report["source_unchanged"] = _sha256(Path(apk).expanduser().resolve()) == original_hash
        finally:
            report["report_path"] = str(workspace / "apk-translation-report.json")
            self._write_report(workspace, report)
        return report


def _validate_apk(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with zipfile.ZipFile(path) as package:
            return package.testzip() is None and "AndroidManifest.xml" in package.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
