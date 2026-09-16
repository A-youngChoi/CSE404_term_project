"""Local-only logging with a content-redaction guard.

Policy: application logs contain identifiers, counts, stage names, and latencies only.
Conversation text, paper text, and profile text must never be passed to a logger.
As a second line of defence, `ContentRedactionFilter` strips any `extra` fields whose
names suggest raw content and truncates suspiciously long messages.
No handler sends logs anywhere except the local stderr stream.
"""

from __future__ import annotations

import logging

_CONTENT_FIELDS = {"text", "content", "turn_text", "prompt", "payload", "quote", "full_text", "abstract"}
_MAX_MESSAGE_CHARS = 300


class ContentRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name in _CONTENT_FIELDS:
            if name in record.__dict__:
                record.__dict__[name] = "[redacted]"
        message = record.getMessage()
        if len(message) > _MAX_MESSAGE_CHARS:
            record.msg = message[:_MAX_MESSAGE_CHARS] + " …[truncated]"
            record.args = ()
        return True


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    root = logging.getLogger("papercue")
    root.setLevel(level.upper())
    if not _configured:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(ContentRedactionFilter())
        root.addHandler(handler)
        _configured = True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"papercue.{name}")
    if not any(isinstance(f, ContentRedactionFilter) for f in logger.filters):
        logger.addFilter(ContentRedactionFilter())
    return logger
