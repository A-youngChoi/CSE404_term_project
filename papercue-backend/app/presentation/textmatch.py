"""Bilingual keyword matching used by the context tracker, retriever and outcome estimator."""

from __future__ import annotations

import math
import re

from app.core.text import content_words

_LATIN = re.compile(r"[a-z0-9]")
_HANGUL = re.compile(r"[가-힣]")


def has_hangul(text: str) -> bool:
    return bool(_HANGUL.search(text or ""))


def keyword_hit(text: str, keyword: str) -> bool:
    """Latin keywords match at a word start (so 'regression' matches 'regressions'); others by substring."""
    t, k = (text or "").lower(), (keyword or "").lower().strip()
    if not k:
        return False
    if _LATIN.search(k[0]):
        return re.search(r"(?<![a-z0-9])" + re.escape(k), t) is not None
    return k in t


def hits(text: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if keyword_hit(text, k)]


def terms(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in content_words(text):
        seen.setdefault(w, None)
    return list(seen)


def overlap(query_terms: list[str], text: str) -> float:
    """Cosine-style overlap between a term list and a text, in [0, 1]."""
    doc = set(terms(text))
    q = set(query_terms)
    if not q or not doc:
        return 0.0
    shared = sum(1 for t in q if t in doc or any(len(t) >= 2 and (t in d or d in t) for d in doc if len(d) >= 2))
    return min(1.0, shared / math.sqrt(len(q) * len(doc)))
