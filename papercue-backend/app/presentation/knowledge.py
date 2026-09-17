"""Knowledge-context retrieval (mock RAG).

`KnowledgeRetriever` is the swap point. `KeywordRetriever` (default) scores chunks by keyword
hits and term overlap; `EmbeddingRetriever` uses the project's local embedder (hashing mock or
sentence-transformers) for the lexical part. Both add the same transparent boosts for the
current slide and for the chunk the detected issue points at, and both report every
candidate with its score components so the dashboard can show unused results too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from app.core.utils import clamp
from app.presentation.schemas import Issue, KnowledgeBase, KnowledgeChunk, KnowledgeRetrieval, RetrievalHit
from app.presentation.textmatch import hits, overlap, terms

TOP_K = 5
MIN_SCORE = 0.3
SLIDE_BOOST = 0.2
TARGET_BOOST = 0.45
# Categories that are relevant evidence for each issue type.
ISSUE_CATEGORIES: dict[str, set[str]] = {
    "missing_key_point": {"must_mention", "slide_note", "script", "method", "result", "limitation", "core_claim"},
    "content_at_risk": {"must_mention", "slide_note", "script"},
    "long_silence": {"must_mention", "slide_note", "script"},
    "audience_confusion": {"glossary", "slide_note", "method"},
    "low_engagement": {"slide_note", "core_claim", "result"},
    "qa_misunderstanding": {"expected_qa"},
    "time_pressure": {"must_mention", "core_claim"},
    "pace_too_fast": {"must_mention", "slide_note"},
    "pace_too_slow": {"slide_note"},
    "filler_repetition": set(),
}
KNOWLEDGE_NOT_REQUIRED = {"pace_too_fast", "pace_too_slow", "filler_repetition", "time_pressure"}


@dataclass
class KnowledgeQuery:
    retrieval_id: str
    slide: int
    slide_title: str
    transcript: str
    issue: Issue | None = None
    question: str | None = None
    extra_terms: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [self.slide_title, self.transcript, self.question or ""]
        if self.issue:
            parts += [self.issue.target_label or "", self.issue.type.replace("_", " ")]
        parts += self.extra_terms
        return " ".join(p for p in parts if p)


class KnowledgeRetriever(Protocol):
    name: str

    def retrieve(self, kb: KnowledgeBase, query: KnowledgeQuery) -> KnowledgeRetrieval: ...


def _boosts(chunk: KnowledgeChunk, query: KnowledgeQuery) -> dict[str, float]:
    out: dict[str, float] = {}
    if chunk.slide is not None and chunk.slide == query.slide:
        out["slide"] = SLIDE_BOOST
    issue = query.issue
    if issue and issue.target and (chunk.kp_id == issue.target or chunk.chunk_id == issue.target):
        out["target"] = TARGET_BOOST
    if issue and chunk.category in ISSUE_CATEGORIES.get(issue.type, set()):
        out["category"] = 0.1
    return out


def _finalise(kb: KnowledgeBase, query: KnowledgeQuery, scored: list[tuple[KnowledgeChunk, dict[str, float]]],
              name: str) -> KnowledgeRetrieval:
    results = []
    for chunk, parts in scored:
        score = round(clamp(sum(parts.values())), 4)
        results.append(RetrievalHit(id=chunk.chunk_id, score=score, components={k: round(v, 3) for k, v in parts.items()}))
    results.sort(key=lambda h: (-h.score, h.id))
    for i, h in enumerate(results):
        if i < TOP_K and h.score >= MIN_SCORE:
            h.selected = True
            h.reason = "retrieved"
        else:
            h.reason = "below threshold" if h.score < MIN_SCORE else "outside top-k"
    q_text = query.text()
    return KnowledgeRetrieval(retrieval_id=query.retrieval_id, retriever=name, query=q_text,
                              query_terms=terms(q_text)[:20], hits=[h for h in results if h.score > 0][:12])


class KeywordRetriever:
    name = "keyword"

    def retrieve(self, kb: KnowledgeBase, query: KnowledgeQuery) -> KnowledgeRetrieval:
        q_text = query.text()
        q_terms = terms(q_text)
        scored = []
        for chunk in kb.chunks:
            kw_hits = hits(q_text, chunk.keywords)
            parts = {"keyword": min(0.6, 0.2 * len(kw_hits)),
                     "overlap": 0.4 * overlap(q_terms, f"{chunk.title} {chunk.text}")}
            parts.update(_boosts(chunk, query))
            scored.append((chunk, parts))
        return _finalise(kb, query, scored, self.name)


class EmbeddingRetriever:
    """Cosine similarity from a local embedder plus the same boosts. Embeddings are cached per KB."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.name = f"embedding:{getattr(embedder, 'name', 'local')}"
        self._cache: dict[str, np.ndarray] = {}

    def retrieve(self, kb: KnowledgeBase, query: KnowledgeQuery) -> KnowledgeRetrieval:
        if kb.kb_id not in self._cache:
            self._cache[kb.kb_id] = self.embedder.embed([f"{c.title}. {c.text} {' '.join(c.keywords)}" for c in kb.chunks])
        matrix = self._cache[kb.kb_id]
        q = self.embedder.embed([query.text()])[0]
        sims = matrix @ q / (np.linalg.norm(matrix, axis=1) * (np.linalg.norm(q) or 1.0) + 1e-9)
        scored = []
        for chunk, sim in zip(kb.chunks, sims):
            parts = {"similarity": 0.7 * max(0.0, float(sim))}
            parts.update(_boosts(chunk, query))
            scored.append((chunk, parts))
        return _finalise(kb, query, scored, self.name)


def mark_usage(retrieval: KnowledgeRetrieval, kb: KnowledgeBase, issue: Issue | None) -> list[str]:
    """Decide which selected chunks actually support the judgement, and say why the others did not."""
    used: list[str] = []
    for h in retrieval.hits:
        if not h.selected:
            continue
        chunk = kb.chunk(h.id)
        if issue is None:
            h.reason = "retrieved; no issue needed knowledge"
            continue
        if issue.type in KNOWLEDGE_NOT_REQUIRED and "target" not in h.components:
            h.reason = "retrieved; this issue type is judged from signals, not content"
            continue
        relevant = "target" in h.components or (chunk and chunk.category in ISSUE_CATEGORIES.get(issue.type, set())
                                                 and ("slide" in h.components or h.score >= 0.5))
        if relevant:
            h.used = True
            h.reason = "used: " + ("the detected issue points at this chunk" if "target" in h.components
                                   else f"{chunk.category} evidence for {issue.type}")
            used.append(h.id)
        else:
            h.reason = f"retrieved but not relevant to {issue.type}"
    return used


def knowledge_support(retrieval: KnowledgeRetrieval, used: list[str], issue: Issue | None) -> float:
    if issue is None or issue.type in KNOWLEDGE_NOT_REQUIRED:
        return 0.5
    scores = [h.score for h in retrieval.hits if h.id in used]
    return round(max(scores), 3) if scores else 0.2
