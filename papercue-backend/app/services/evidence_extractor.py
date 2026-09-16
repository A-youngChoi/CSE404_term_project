"""Evidence Extractor: listener turn -> validated, typed evidence items.

The local model proposes evidence; this module keeps only items that
  * use an allowed dimension/value,
  * quote an exact span of the listener's own words,
  * avoid sensitive attributes and personal characterisations,
and assigns confidence from the evidence type (never from the model).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.utils import normalize
from app.llm.base import LocalLLMProvider
from app.models.audience import EvidenceItem
from app.models.conversation import ConversationState, Turn
from app.models.enums import DIMENSION_VALUES, Dimension, EvidenceType, Speaker
from app.models.llm_outputs import EvidenceExtractionOutput, ProposedEvidence
from app.repositories.audience import AudienceRepository
from app.safety.sensitive import characterization_hits, sensitive_hits
from app.services.audience_model import POLICIES

MAX_ITEMS = 6


@dataclass
class ExtractionResult:
    accepted: list[EvidenceItem] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # {"index", "dimension", "code"} - no content
    proposed: int = 0


def normalize_key(key: str) -> str:
    return " ".join(key.lower().split())[:60]


class EvidenceExtractor:
    def __init__(self, repo: AudienceRepository):
        self.repo = repo

    def propose(self, provider: LocalLLMProvider, turn: Turn, state: ConversationState,
                paper_terms: list[str]) -> EvidenceExtractionOutput:
        payload = {
            "latest_listener_turn": {"turn_id": turn.id, "text": turn.text},
            "conversation_state": {
                "phase": state.phase.value,
                "topic": state.topic,
                "listener_act": state.listener_act.value,
                "unresolved_issues": [i.model_dump() for i in state.unresolved_issues],
            },
            "paper_terms": paper_terms[:40],
            "allowed_values": {d.value: list(v) for d, v in DIMENSION_VALUES.items()},
        }
        return provider.generate_structured("evidence_extraction", payload, EvidenceExtractionOutput)

    def validate(self, item: ProposedEvidence, turn: Turn) -> str | None:
        if item.value not in DIMENSION_VALUES[item.dimension]:
            return "invalid_value"
        if not normalize(item.quote) or normalize(item.quote) not in normalize(turn.text):
            return "quote_not_in_turn"
        if item.evidence_type == EvidenceType.profile:
            return "profile_type_not_allowed_for_turn"
        for text, code in ((item.key, "sensitive_key"), (item.observation, "sensitive_observation")):
            if sensitive_hits(text):
                return code
            if characterization_hits(text, include_assertions=False):
                return "personal_characterization"
        return None

    def store(self, provider_name: str, turn: Turn, output: EvidenceExtractionOutput) -> ExtractionResult:
        result = ExtractionResult(proposed=len(output.evidence))
        if turn.speaker != Speaker.listener:
            return result
        seen: set[tuple[str, str, str]] = set()
        for idx, item in enumerate(output.evidence):
            code = self.validate(item, turn)
            key = normalize_key(item.key)
            sig = (item.dimension.value, key, item.value)
            if code is None and sig in seen:
                code = "duplicate"
            if code is None and len(result.accepted) >= MAX_ITEMS:
                code = "too_many_items"
            if code:
                result.rejected.append({"index": idx, "dimension": item.dimension.value, "code": code})
                continue
            etype = item.evidence_type
            # A single non-explicit signal must not become a broad knowledge judgment.
            if item.dimension == Dimension.knowledge and etype != EvidenceType.explicit:
                etype = EvidenceType.weak_inference
            seen.add(sig)
            result.accepted.append(
                self.repo.add_evidence(
                    session_id=turn.session_id, turn_id=turn.id, source_type="conversation", source_id=turn.id,
                    dimension=item.dimension.value, key=key, value=item.value, evidence_type=etype.value,
                    confidence=POLICIES[etype].initial, quote=item.quote[:200], observation=item.observation[:160],
                    extractor=provider_name,
                )
            )
        return result
