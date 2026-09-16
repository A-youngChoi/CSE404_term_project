"""Local LLM provider interface.

Two implementations exist:
  * OllamaProvider            - a real local model served by Ollama on this machine.
  * DeterministicMockProvider - fixed rules for tests and architecture demos (NOT an LLM).

There is intentionally no remote provider.
"""

from __future__ import annotations

import json
from typing import Callable, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from app.core.errors import ModelOutputError
from app.core.logging import get_logger

T = TypeVar("T", bound=BaseModel)
log = get_logger("llm")

# Called with (task, prompt_text, raw_response) when DEBUG_STORE_PROMPTS is on.
DebugSink = Callable[[str, str, str | None], None]


@runtime_checkable
class LocalLLMProvider(Protocol):
    name: str
    is_mock: bool

    def generate_structured(self, task: str, payload: dict, schema: type[T]) -> T: ...

    def status(self) -> dict: ...


def parse_structured(raw: str, schema: type[T]) -> T:
    """Parse a raw model string into `schema`. Tolerates code fences, nothing more."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    data = json.loads(text[start : end + 1])
    return schema.model_validate(data)


def with_retries(task: str, attempts: int, call: Callable[[int], str], schema: type[T]) -> T:
    """Invoke `call` up to `attempts` times until its output validates; then fail safely."""
    last_error = ""
    for attempt in range(attempts):
        raw = call(attempt)
        try:
            return parse_structured(raw, schema)
        except (ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
            # Log only the error class, never the raw output (it may echo conversation text).
            last_error = type(exc).__name__
            log.warning("task=%s attempt=%d invalid structured output (%s)", task, attempt + 1, last_error)
    raise ModelOutputError(
        f"The local model returned invalid output for task '{task}' after {attempts} attempt(s).",
        task=task,
        error=last_error,
    )
