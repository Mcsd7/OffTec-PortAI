"""Translation workflow: discovery, extraction, review, output and reporting."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from .formats import PLACEHOLDER_RE, extract, is_translatable, project_files, rebuild
from .models import LANGUAGES, TranslationEntry, TranslationResult
from .providers import TranslationProvider


def _semantic_context(path: str, key: str) -> str:
    words = f"{path} {key}".lower()
    categories = (
        ("dialogue", ("dialog", "conversation", "speaker", "subtitle", "story")),
        ("mission", ("mission", "quest", "objective", "task")),
        ("menu", ("menu", "button", "title", "navigation")),
        ("settings", ("setting", "config", "option", "preference")),
        ("message", ("message", "error", "warning", "notification", "hint")),
    )
    for category, terms in categories:
        if any(term in words for term in terms):
            return category
    return "general"


def _protect(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []
    def replace(match):
        token = f"__PORTAI_KEEP_{len(tokens)}__"
        tokens.append(match.group(0))
        return token
    return PLACEHOLDER_RE.sub(replace, text), tokens


def _restore_and_validate(source: str, translated: str, tokens: list[str]) -> str | None:
    for index, original in enumerate(tokens):
        token = f"__PORTAI_KEEP_{index}__"
        if translated.count(token) != 1:
            return None
        translated = translated.replace(token, original)
    if not translated.strip():
        return None
    if Counter(PLACEHOLDER_RE.findall(source)) != Counter(PLACEHOLDER_RE.findall(translated)):
        return None
    return translated


class TranslationEngine:
    def __init__(self, provider: TranslationProvider, max_files: int = 10000):
        self.provider = provider
        self.max_files = max_files

    def scan(self, project: Path, source_language: str = "en") -> TranslationResult:
        root = project.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("O projeto precisa ser um diretório.")
        entries: list[TranslationEntry] = []
        discovered: list[str] = []
        scan_errors: list[dict[str, str]] = []
        for path in project_files(root, self.max_files):
            try:
                fmt, values = extract(path, root)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                scan_errors.append({"path": path.relative_to(root).as_posix(), "error": str(exc)})
                continue
            rel = path.relative_to(root).as_posix()
            if not values:
                continue
            discovered.append(rel)
            for key, text, context in values:
                entry_id = f"{rel}::{key}"
                semantic = _semantic_context(rel, key)
                if semantic != "general":
                    context = f"{context};{semantic}"
                entry = TranslationEntry(entry_id, rel, fmt, key, text, context)
                if context == "po-plural-unsupported":
                    entry.status = "unsupported"
                    entry.error = "Entradas PO plurais precisam de tratamento específico de pluralização."
                entries.append(entry)
        return TranslationResult(
            project=str(root), source_language=source_language, targets=list(LANGUAGES),
            provider=self.provider.name, discovered_files=discovered, entries=entries,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            scan_errors=scan_errors,
        )

    def translate(self, result: TranslationResult, targets: list[str] | None = None) -> TranslationResult:
        targets = targets or result.targets
        invalid = set(targets) - set(LANGUAGES)
        if invalid:
            raise ValueError(f"Idiomas não suportados: {', '.join(sorted(invalid))}")
        result.targets = list(dict.fromkeys(targets))
        for target in result.targets:
            pending = [entry for entry in result.entries if entry.status not in {"approved", "unsupported"} and target not in entry.translations]
            if not pending:
                continue
            protected: dict[str, tuple[str, list[str]]] = {}
            request_entries = []
            for entry in pending:
                masked, tokens = _protect(entry.source)
                clone = TranslationEntry(entry.entry_id, entry.path, entry.format, entry.key, masked, entry.context)
                protected[entry.entry_id] = (entry.source, tokens)
                request_entries.append(clone)
            try:
                proposed = self.provider.translate(request_entries, target, result.source_language)
            except Exception as exc:
                for entry in pending:
                    entry.error = str(exc)
                continue
            for entry in pending:
                raw = proposed.get(entry.entry_id)
                if raw is None:
                    entry.error = "Provedor não retornou tradução."
                    continue
                original, tokens = protected[entry.entry_id]
                safe = _restore_and_validate(original, raw, tokens)
                if safe is None:
                    entry.error = "Placeholder ausente/duplicado ou tradução vazia; revisão necessária."
                    continue
                entry.translations[target] = safe
                entry.target_status[target] = "review"
                entry.status = "review"
                entry.error = None
        return result

    @staticmethod
    def approve(result: TranslationResult, decisions: dict[tuple[str, str], bool], targets: list[str] | None = None) -> None:
        targets = targets or result.targets
        for entry in result.entries:
            if entry.status == "unsupported":
                continue
            for target in targets:
                decision = decisions.get((entry.entry_id, target))
                if decision is not None:
                    entry.target_status[target] = "approved" if decision else "rejected"
            statuses = [entry.target_status.get(target) for target in targets if target in entry.translations]
            entry.status = "approved" if statuses and all(status == "approved" for status in statuses) else "review"

    def apply(self, result: TranslationResult, project: Path, output_dir: Path, targets: list[str] | None = None) -> Path:
        root = project.expanduser().resolve(strict=True)
        output = output_dir.expanduser().resolve()
        if output == root or root in output.parents:
            raise ValueError("A localização deve ser escrita fora do projeto de origem.")
        output.mkdir(parents=True, exist_ok=True)
        targets = targets or result.targets
        grouped: dict[tuple[str, str], dict[str, str]] = {}
        for entry in result.entries:
            for target in targets:
                if entry.target_status.get(target, entry.status) != "approved":
                    continue
                translated = entry.translations.get(target)
                if translated is not None:
                    grouped.setdefault((target, entry.path), {})[entry.key] = translated
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        for (target, rel), translations in grouped.items():
            source = root / rel
            android_resources = rel.endswith(".xml") and "/res/values" in f"/{rel}/"
            if android_resources:
                android_locale = LANGUAGES[target]["android"] or ""
                locale_root = output / target / (f"values-{android_locale}" if android_locale else "values")
            else:
                locale_root = output / target / Path(rel).parent
            dest = locale_root / Path(rel).name
            _assert_safe_output_path(output, dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                backup = output / "backups" / stamp / target / Path(rel)
                _assert_safe_output_path(output, backup)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup)
            _atomic_write(dest, rebuild(source, root, translations))
        result.output_dir = str(output)
        report = output / f"translation-report-{stamp}.json"
        _assert_safe_output_path(output, report)
        result.report_path = str(report)
        _atomic_write(report, (json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return report


def _assert_safe_output_path(output: Path, dest: Path) -> None:
    """Reject output symlinks that could redirect a write back into source files."""
    try:
        parts = dest.relative_to(output).parts
    except ValueError as exc:
        raise ValueError("Destino de localização fora da pasta de saída.") from exc
    cursor = output
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Destino de saída por link simbólico não permitido: {cursor}")
    if output not in dest.parent.resolve().parents and dest.parent.resolve() != output:
        raise ValueError("Diretório de saída escapou da pasta configurada.")


def _atomic_write(path: Path, content: bytes) -> None:
    """Replace a generated file atomically, breaking any pre-existing hard links."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
