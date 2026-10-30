"""Shared data structures and supported language metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LANGUAGES = {
    "pt-BR": {"name": "Português brasileiro", "android": "pt-rBR", "direction": "ltr"},
    "en": {"name": "English", "android": "en", "direction": "ltr"},
    "es": {"name": "Español", "android": "es", "direction": "ltr"},
    "ar": {"name": "العربية", "android": "ar", "direction": "rtl"},
    "hi": {"name": "हिन्दी", "android": "hi", "direction": "ltr"},
}


@dataclass
class TranslationEntry:
    """One translatable string, addressed by file and stable structural key."""

    entry_id: str
    path: str
    format: str
    key: str
    source: str
    context: str = "general"
    translations: dict[str, str] = field(default_factory=dict)
    target_status: dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranslationResult:
    project: str
    source_language: str
    targets: list[str]
    provider: str
    discovered_files: list[str]
    entries: list[TranslationEntry]
    created_at: str
    output_dir: str | None = None
    report_path: str | None = None
    scan_errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "source_language": self.source_language,
            "targets": self.targets,
            "provider": self.provider,
            "discovered_files": self.discovered_files,
            "entries": [entry.to_dict() for entry in self.entries],
            "created_at": self.created_at,
            "output_dir": self.output_dir,
            "report_path": self.report_path,
            "scan_errors": self.scan_errors,
        }
