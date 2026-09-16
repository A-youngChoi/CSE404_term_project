"""Initial Audience Model Service: manually supplied profile -> uncertain priors.

No web lookup, scraping, or enrichment is performed. Profile items become evidence of
type `profile` whose confidence is capped at PROFILE_CONFIDENCE_CAP.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.errors import LocalConfigurationError
from app.core.utils import words
from app.models.enums import Dimension, EvidenceType
from app.models.session import ProfileIn, ProfileResult
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.sessions import SessionRepository
from app.safety.sensitive import is_prohibited_profile_text
from app.services.audience_model import AudienceModelService
from app.services.retrieval import RetrievalQuery, RetrievalService

# field -> list of (dimension, value, base confidence, observation template, requires paper relevance)
FIELD_RULES: dict[str, list[tuple[Dimension, str, float, str, bool]]] = {
    "research_topics": [
        (Dimension.interest, "interested", 0.50, "The supplied profile lists '{v}' as a research topic.", False),
        (Dimension.connection, "relevant", 0.45, "The profile topic '{v}' overlaps with units of this paper.", True),
    ],
    "recent_paper_keywords": [
        (Dimension.interest, "interested", 0.40, "The supplied profile lists '{v}' among recent paper keywords.", False),
    ],
    "familiar_methods": [
        (Dimension.familiarity, "familiar", 0.55, "The supplied profile lists '{v}' as a familiar method.", False),
    ],
    "application_domains": [
        (Dimension.interest, "interested", 0.45, "The supplied profile lists '{v}' as an application domain.", False),
        (Dimension.connection, "relevant", 0.40, "The profile domain '{v}' overlaps with units of this paper.", True),
    ],
    "connection_notes": [
        (Dimension.connection, "relevant", 0.40, "A manually supplied connection note mentions '{v}'.", False),
    ],
}
RELEVANCE_MIN_SCORE = 0.3


class ProfileService:
    def __init__(self, settings: Settings, sessions: SessionRepository, audience_repo: AudienceRepository,
                 audience: AudienceModelService, conversation: ConversationRepository, retrieval: RetrievalService):
        self.settings = settings
        self.sessions = sessions
        self.audience_repo = audience_repo
        self.audience = audience
        self.conversation = conversation
        self.retrieval = retrieval

    def _relevant(self, paper_id: str, text: str) -> bool:
        try:
            return bool(self.retrieval.search(paper_id, RetrievalQuery(topic=text), top_k=1, min_score=RELEVANCE_MIN_SCORE))
        except LocalConfigurationError:
            raise
        except Exception:  # noqa: BLE001 - an unindexed paper simply yields no connection prior
            return False

    def apply_profile(self, session: dict, profile: ProfileIn) -> ProfileResult:
        sid = session["id"]
        fields = profile.model_dump()
        rejected: list[str] = []
        clean: dict[str, list[str]] = {}
        for name, values in fields.items():
            kept = []
            for i, raw in enumerate(values):
                value = " ".join(str(raw).split())[:80]
                if not value:
                    continue
                if is_prohibited_profile_text(value):
                    rejected.append(f"{name}[{i}]")  # never echo the rejected content
                    continue
                kept.append(value)
            clean[name] = kept

        # Replacing a profile removes previous priors that never received conversational support.
        self.audience_repo.delete_profile_only_beliefs(sid)
        self.audience_repo.delete_profile_evidence(sid)
        profile_id = self.sessions.save_profile(sid, clean)
        listener_turns = self.conversation.latest_state(sid).listener_turn_count
        cap = self.settings.profile_confidence_cap

        created = 0
        for name, values in clean.items():
            for i, value in enumerate(values):
                key = " ".join(words(value.lower())[:6]) if name == "connection_notes" else value.lower()
                for dim, belief_value, base, template, needs_relevance in FIELD_RULES[name]:
                    if needs_relevance and not self._relevant(session["paper_id"], value):
                        continue
                    ev = self.audience_repo.add_evidence(
                        session_id=sid, turn_id=None, source_type="profile", source_id=f"profile:{name}[{i}]",
                        dimension=dim.value, key=key, value=belief_value, evidence_type=EvidenceType.profile.value,
                        confidence=min(base, cap), quote=None, observation=template.format(v=key),
                        extractor="profile-rules",
                    )
                    change = self.audience.apply(ev, key=key, value=belief_value,
                                                 reason="Profile prior (uncertain hypothesis).",
                                                 listener_turn=listener_turns)
                    if change is not None:
                        created += 1
        self.sessions.touch(sid)
        return ProfileResult(profile_id=profile_id, hypotheses_created=created, rejected_fields=rejected,
                             confidence_cap=cap)
