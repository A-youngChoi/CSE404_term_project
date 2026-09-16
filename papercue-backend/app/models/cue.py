from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import CueAction, SessionMode


class CueRequest(BaseModel):
    note: str | None = Field(default=None, max_length=100, description="Optional presenter note, not stored.")


class RetrievedUnit(BaseModel):
    unit_id: str
    unit_type: str
    title: str
    short_explanation: str
    keywords: list[str]
    source_reference: str | None
    score: float
    similarity: float


class InterventionScores(BaseModel):
    need: float
    relevance: float
    grounding: float
    model_confidence: float
    novelty: float
    timing: float
    interruption_cost: float
    total: float


class CueDecision(BaseModel):
    id: str
    should_intervene: bool
    action: CueAction | None
    target: str | None
    confidence: float
    short_reason: str
    reason_code: str = Field(description="Stable code the dashboard translates to Korean.")
    reason_params: dict = Field(default_factory=dict)
    beliefs_used: list[str] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    scores: InterventionScores
    mode: SessionMode


class FilterCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class GeneratedCue(BaseModel):
    id: str
    cue: str | None
    candidate_cue: str | None
    action: CueAction | None
    grounding_unit_ids: list[str]
    audience_evidence_ids: list[str]
    confidence: float
    rationale: str | None
    passed_filter: bool
    filter_results: list[FilterCheck]
    fallback_used: bool
    delivered: bool
    final_status: str
    generator: str
    created_at: str


class CueTrace(BaseModel):
    trigger_turn_id: str | None
    trigger_turn_text: str | None
    conversation_state: dict
    audience_evidence: list[dict]
    beliefs_used: list[dict]
    retrieved_units: list[RetrievedUnit]
    earlier_context: list[dict]
    latency_ms: dict[str, float]


class CueResponse(BaseModel):
    trace_id: str
    status: str = Field(description="deliverable | held_low_confidence | insufficient_grounding | duplicate_recent | "
                                    "sensitive_risk | format_invalid | no_cue_needed | local_model_error")
    delivered: bool
    evaluation_only: bool
    cue: str | None
    decision: CueDecision
    generated: GeneratedCue | None
    trace: CueTrace
    llm_provider: str
    mock_mode: bool
    mock_notice: str | None = None


MOCK_NOTICE = "Deterministic mock mode: this output comes from fixed rules, NOT from a language model."
