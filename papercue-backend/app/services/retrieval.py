"""Local Retrieval Service.

Hybrid score = cosine similarity (local embeddings)
             + keyword bonus (unit keywords literally present in the query)
             + action/type bonus (unit types that suit the intended cue action)
             + small presenter-priority bonus.
Only a handful of units are returned; the full paper is never passed to the cue generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import InvalidSessionStateError, LocalConfigurationError
from app.core.text import contains_phrase
from app.models.audience import Belief
from app.models.conversation import ConversationState
from app.models.cue import RetrievedUnit
from app.models.enums import CueAction
from app.repositories.papers import PaperRepository
from app.services.embeddings import Embedder, cosine_scores

ACTION_UNIT_TYPES: dict[CueAction | None, set[str]] = {
    CueAction.address_concern: {"method", "core_idea", "limitation", "contribution"},
    CueAction.simplify: {"example", "core_idea", "application"},
    CueAction.elaborate: {"method", "evidence", "result"},
    CueAction.connect: {"connection", "application"},
    CueAction.reframe: {"core_idea", "contribution", "problem"},
    CueAction.emphasize: {"application", "contribution", "example"},
    CueAction.recover: {"core_idea", "method"},
    CueAction.acknowledge_limitation: {"limitation"},
    CueAction.close: {"contribution"},
    CueAction.verify: set(),
    None: set(),
}
KEYWORD_BONUS = 0.15
TYPE_BONUS = 0.08
PRIORITY_WEIGHT = 0.05


@dataclass
class RetrievalQuery:
    latest_question: str | None = None
    topic: str | None = None
    unresolved_issues: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    action: CueAction | None = None
    target: str | None = None

    def text(self) -> str:
        parts = [self.latest_question or "", self.topic or "", " ".join(self.unresolved_issues),
                 " ".join(self.concerns), self.target or ""]
        return " ".join(p for p in parts if p).strip()

    def summary(self) -> dict:
        """Structured description of the query without the raw question text."""
        return {
            "has_latest_question": bool(self.latest_question),
            "topic": self.topic,
            "unresolved_issues": self.unresolved_issues,
            "concerns": self.concerns,
            "action": self.action.value if self.action else None,
            "target": self.target,
        }


def build_query(state: ConversationState, beliefs: list[Belief], latest_listener_text: str | None,
                action: CueAction | None = None, target: str | None = None) -> RetrievalQuery:
    concerns = [b.key for b in beliefs if b.dimension.value == "concern" and b.value == "raised"
                and b.effective_confidence >= 0.4]
    return RetrievalQuery(
        latest_question=state.explicit_question or latest_listener_text,
        topic=state.topic,
        unresolved_issues=[i.topic for i in state.unresolved_issues if i.status == "open"],
        concerns=concerns,
        action=action,
        target=target,
    )


class RetrievalService:
    def __init__(self, settings: Settings, repo: PaperRepository, embedder: Embedder):
        self.settings = settings
        self.repo = repo
        self.embedder = embedder

    def search(self, paper_id: str, query: RetrievalQuery, top_k: int | None = None,
               min_score: float | None = None) -> list[RetrievedUnit]:
        text = query.text()
        if not text:
            return []
        ids, matrix, models = self.repo.load_embeddings(paper_id)
        if not ids:
            raise InvalidSessionStateError("The paper has no local index. Rebuild it with the reindex endpoint.")
        if models != {self.embedder.name}:
            raise LocalConfigurationError(
                "The paper index was built with a different embedding model. Rebuild the local index.",
                component="retrieval",
            )
        sims = cosine_scores(self.embedder.embed([text])[0], matrix)
        units = self.repo.get_units(ids)
        preferred = ACTION_UNIT_TYPES.get(query.action, set())
        scored = []
        for uid, sim in zip(ids, sims):
            unit = units.get(uid)
            if unit is None:
                continue
            kw_hits = sum(1 for k in unit.keywords if contains_phrase(text, k))
            score = float(sim) + min(2, kw_hits) * KEYWORD_BONUS + PRIORITY_WEIGHT * unit.presenter_priority
            if unit.unit_type.value in preferred:
                score += TYPE_BONUS
            scored.append((score, float(sim), unit))
        scored.sort(key=lambda t: t[0], reverse=True)
        threshold = self.settings.retrieval_min_score if min_score is None else min_score
        k = top_k or self.settings.retrieval_top_k
        return [
            RetrievedUnit(unit_id=u.id, unit_type=u.unit_type.value, title=u.title,
                          short_explanation=u.short_explanation, keywords=u.keywords,
                          source_reference=u.source_reference, score=round(s, 4), similarity=round(sim, 4))
            for s, sim, u in scored[:k]
            if s >= threshold
        ]
