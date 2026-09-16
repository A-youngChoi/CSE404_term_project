"""Small shared helpers: IDs, UTC timestamps, stage timing, text normalisation."""

from __future__ import annotations

import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

# Unicode-aware so Korean (Hangul) words are counted like English words.
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="milliseconds")


def to_utc_iso(value: datetime | None) -> str:
    if value is None:
        return utcnow_iso()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def word_count(text: str) -> int:
    return len(words(text))


def normalize(text: str) -> str:
    return " ".join(w.lower() for w in words(text))


def token_set(text: str) -> set[str]:
    return {w.lower() for w in words(text)}


def jaccard(a: str, b: str) -> float:
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class StageTimer:
    """Collects per-stage latencies in milliseconds."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self._start = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = round(self.stages.get(name, 0.0) + (time.perf_counter() - t0) * 1000, 2)

    def as_dict(self) -> dict[str, float]:
        out = dict(self.stages)
        out["total"] = round((time.perf_counter() - self._start) * 1000, 2)
        return out
