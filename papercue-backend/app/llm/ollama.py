"""Ollama provider: a real language model running locally on this machine.

Requests go only to OLLAMA_BASE_URL, which configuration restricts to loopback.
There is no fallback to any remote service.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.errors import LocalConfigurationError
from app.core.logging import get_logger
from app.llm.base import DebugSink, T, with_retries
from app.prompts import get_prompt

log = get_logger("llm.ollama")


class OllamaProvider:
    is_mock = False

    def __init__(self, settings: Settings, debug_sink: DebugSink | None = None, client: httpx.Client | None = None):
        self.settings = settings
        self.model = settings.ollama_model
        self.name = f"ollama:{self.model}"
        self.base_url = settings.ollama_base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=settings.ollama_timeout_seconds, trust_env=False)
        self._debug_sink = debug_sink

    # -- health -------------------------------------------------------------
    def status(self) -> dict:
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=3.0)
            resp.raise_for_status()
            names = [m.get("name", "") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return {"available": False, "model_installed": False, "error_code": "ollama_unreachable"}
        installed = any(n == self.model or n.split(":")[0] == self.model for n in names) or self.model in names
        return {
            "available": True,
            "model_installed": installed,
            "error_code": None if installed else "model_not_pulled",
        }

    # -- generation ---------------------------------------------------------
    def _chat(self, task: str, payload: dict, schema: type[T], attempt: int) -> str:
        prompt = get_prompt(task)
        user = prompt.render_user(payload)
        if attempt > 0:
            user += "\n\nYour previous output was not valid JSON for the schema. Return only the JSON object."
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": self.settings.ollama_temperature},
        }
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise LocalConfigurationError(
                "Ollama is not reachable at the configured local address. Start it with `ollama serve`.",
                provider="ollama",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LocalConfigurationError(
                "The local Ollama model timed out. Increase OLLAMA_TIMEOUT_SECONDS or use a smaller model.",
                provider="ollama",
            ) from exc
        if resp.status_code == 404:
            raise LocalConfigurationError(
                f"The local model '{self.model}' is not installed. Run `ollama pull {self.model}`.",
                provider="ollama",
            )
        if resp.status_code >= 400:
            raise LocalConfigurationError(
                f"Ollama returned HTTP {resp.status_code}.", provider="ollama", status=resp.status_code
            )
        try:
            content = resp.json().get("message", {}).get("content", "")
        except ValueError:
            content = ""
        if self._debug_sink is not None:
            self._debug_sink(task, f"{prompt.system}\n---\n{user}", content)
        log.info("task=%s attempt=%d model=%s status=ok", task, attempt + 1, self.model)
        return content

    def generate_structured(self, task: str, payload: dict, schema: type[T]) -> T:
        attempts = 1 + self.settings.llm_max_retries
        return with_retries(task, attempts, lambda a: self._chat(task, payload, schema, a), schema)
