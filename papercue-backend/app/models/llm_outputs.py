"""Schemas that every local-model response must validate against.

The model only *proposes*; deterministic code validates and applies.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import CueAction, Dimension, EvidenceType, ListenerAct, Phase, PresenterAct, UnitType


class UnitLabel(BaseModel):
    chunk_index: int = Field(ge=0)
    unit_type: UnitType
    title: str = Field(min_length=1, max_length=120)
    short_explanation: str = Field(min_length=1, max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=8)


class PaperUnitLabelsOutput(BaseModel):
    labels: list[UnitLabel]


class ConversationStateOutput(BaseModel):
    phase: Phase
    topic: str | None = Field(default=None, max_length=80)
    listener_act: ListenerAct = ListenerAct.none
    presenter_act: PresenterAct = PresenterAct.none
    explicit_question: str | None = Field(default=None, max_length=300)
    detected_concern: str | None = Field(default=None, max_length=60)
    unresolved_issue: str | None = Field(default=None, max_length=60)
    possible_misunderstanding: str | None = Field(default=None, max_length=120)
    issue_resolved: bool = False


class ProposedEvidence(BaseModel):
    dimension: Dimension
    key: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=30)
    evidence_type: EvidenceType
    quote: str = Field(min_length=1, max_length=200, description="Exact span copied from the listener's turn.")
    observation: str = Field(min_length=1, max_length=160)


class EvidenceExtractionOutput(BaseModel):
    evidence: list[ProposedEvidence] = Field(default_factory=list, max_length=8)


class ProposedBeliefUpdate(BaseModel):
    evidence_index: int = Field(ge=0)
    dimension: Dimension
    key: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=160)


class AudienceUpdateProposalOutput(BaseModel):
    updates: list[ProposedBeliefUpdate] = Field(default_factory=list, max_length=8)


class CueGenerationOutput(BaseModel):
    cue: str = Field(min_length=1, max_length=120)
    action: CueAction
    grounding_unit_ids: list[str] = Field(default_factory=list, max_length=4)
    audience_evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=160)
