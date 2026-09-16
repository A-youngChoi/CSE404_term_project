"""Provider registry. Sessions choose 'ollama' or 'mock'; nothing else exists."""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import InvalidInputError
from app.llm.base import DebugSink, LocalLLMProvider
from app.llm.mock import DeterministicMockProvider
from app.llm.ollama import OllamaProvider


class ProviderRegistry:
    def __init__(self, settings: Settings, debug_sink: DebugSink | None = None, ollama: LocalLLMProvider | None = None):
        self.settings = settings
        self._mock = DeterministicMockProvider()
        self._ollama = ollama
        self._debug_sink = debug_sink

    def get(self, name: str) -> LocalLLMProvider:
        if name == "mock":
            return self._mock
        if name == "ollama":
            if self._ollama is None:
                self._ollama = OllamaProvider(self.settings, debug_sink=self._debug_sink)
            return self._ollama
        raise InvalidInputError(f"Unknown LLM provider '{name}'. Use 'ollama' or 'mock'.")

    def model_label(self, name: str) -> str:
        return self.get(name).name
