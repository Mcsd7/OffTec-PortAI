"""Universal Translation Engine for OffTec PortAI."""

from .engine import TranslationEngine
from .models import LANGUAGES, TranslationEntry, TranslationResult
from .providers import CommandProvider, OfflineProvider, TranslationProvider

__all__ = [
    "CommandProvider", "LANGUAGES", "OfflineProvider", "TranslationEngine",
    "TranslationEntry", "TranslationProvider", "TranslationResult",
]
