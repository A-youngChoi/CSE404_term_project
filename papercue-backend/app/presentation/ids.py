"""Stable, run-local identifiers. Re-running a session with the same seed yields the same IDs."""

from __future__ import annotations

from collections import defaultdict


class IdFactory:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)

    def next(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}_{self._counters[prefix]:04d}"
