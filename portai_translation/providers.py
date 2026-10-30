"""Provider abstraction. No network endpoint or credential is assumed."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import importlib
from abc import ABC, abstractmethod

from .models import TranslationEntry


class TranslationProvider(ABC):
    name = "provider"

    @abstractmethod
    def translate(self, entries: list[TranslationEntry], target: str, source: str) -> dict[str, str]:
        """Return translations keyed by entry_id; omit strings that cannot be translated."""


class OfflineProvider(TranslationProvider):
    """Safe default: keep every entry pending until a real provider is configured."""

    name = "offline-review"

    def translate(self, entries: list[TranslationEntry], target: str, source: str) -> dict[str, str]:
        return {}


class CommandProvider(TranslationProvider):
    """Adapter for a user-supplied local model or service bridge executable.

    The command reads a UTF-8 JSON request on stdin and writes a JSON object
    mapping entry IDs to translations on stdout. This is PortAI's adapter
    contract, not a claim about any third-party API.
    """

    name = "command-adapter"

    def __init__(self, command: str, timeout: int = 300):
        self.command = shlex.split(command)
        self.timeout = timeout
        if not self.command:
            raise ValueError("O comando do provedor está vazio.")

    @classmethod
    def from_environment(cls) -> "CommandProvider | None":
        command = os.environ.get("PORTAI_TRANSLATION_COMMAND", "").strip()
        return cls(command) if command else None

    def translate(self, entries: list[TranslationEntry], target: str, source: str) -> dict[str, str]:
        request = {
            "source_language": source,
            "target_language": target,
            "entries": [
                {"id": e.entry_id, "path": e.path, "key": e.key, "format": e.format, "text": e.source, "context": e.context}
                for e in entries
            ],
        }
        result = subprocess.run(
            self.command, input=json.dumps(request, ensure_ascii=False), text=True,
            capture_output=True, timeout=self.timeout, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"Provedor terminou com código {result.returncode}.")
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in response.items()):
            raise ValueError("O provedor deve retornar um objeto JSON de ID para tradução.")
        return response


def configured_provider() -> TranslationProvider:
    """Load a command or Python provider plugin; otherwise stay safely offline."""
    plugin = os.environ.get("PORTAI_TRANSLATION_PROVIDER", "").strip()
    if plugin:
        module_name, separator, object_name = plugin.partition(":")
        if not separator:
            raise ValueError("Use PORTAI_TRANSLATION_PROVIDER=modulo:Classe.")
        provider_type = getattr(importlib.import_module(module_name), object_name)
        provider = provider_type()
        if not isinstance(provider, TranslationProvider):
            raise TypeError("O plugin deve implementar TranslationProvider.")
        return provider
    return CommandProvider.from_environment() or OfflineProvider()
