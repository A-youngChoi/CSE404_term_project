from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import Dimension, EvidenceType


class EvidenceItem(BaseModel):
    id: str
    session_id: str
    turn_id: str | None
    source_type: str  # conversation | profile
    source_id: str
    dimension: Dimension
    key: str
    value: str
    evidence_type: EvidenceType
    confidence: float
    quote: str | None
    observation: str
    extractor: str
    created_at: str


class Belief(BaseModel):
    id: str
    session_id: str
    dimension: Dimension
    key: str
    value: str
    confidence: float
    effective_confidence: float = Field(description="Confidence after decay of stale profile priors.")
    uncertainty: float
    status: str
    lifecycle: str = Field(
        description="profile_hypothesis | conversation_supported | explicitly_confirmed | conflicting | stale"
    )
    source_type: str
    source_id: str
    source_turn_index: int | None = None
    evidence_ids: list[str]
    support_count: int
    contradict_count: int
    pending_value: str | None
    last_evidence_type: EvidenceType
    reason: str
    created_at: str
    updated_at: str


class BeliefChange(BaseModel):
    id: str
    belief_id: str
    dimension: str
    key: str
    change_type: str
    old_value: str | None
    new_value: str
    old_confidence: float | None
    new_confidence: float
    evidence_id: str | None
    source_type: str
    source_id: str
    reason: str
    created_at: str


class AudienceModelView(BaseModel):
    session_id: str
    dimensions: dict[str, list[Belief]]
    unknown_dimensions: list[str]
    overall_uncertainty: float
    listener_turns_observed: int
    notice: str = (
        "Beliefs are uncertain, evidence-backed hypotheses limited to explaining this paper. "
        "They are not judgments about the person."
    )
