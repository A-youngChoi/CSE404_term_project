from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import SessionMode

CONSENT_NOTICE = (
    "RESEARCH PROTOTYPE: this session records typed conversation turns and derives an audience model "
    "on this computer only. It is intended for consented research testing, not covert listening. "
    "All participants must have agreed to take part."
)


class SessionCreate(BaseModel):
    paper_id: str
    consent_confirmed: bool = Field(
        description="Must be true: every participant has consented to this research-prototype session."
    )
    mode: SessionMode = SessionMode.on_demand
    llm_provider: Literal["ollama", "mock"] | None = Field(
        default=None,
        description="'ollama' (real local model) or 'mock' (explicit deterministic simulation). Defaults to LLM_PROVIDER.",
    )
    cue_language: Literal["en", "ko"] | None = Field(default=None, description="Language of generated cues.")
    label: str | None = Field(default=None, max_length=100, description="Optional non-identifying label.")


class Session(BaseModel):
    id: str
    paper_id: str
    mode: SessionMode
    consent_confirmed: bool
    consent_notice: str
    llm_provider: str
    llm_model: str
    cue_language: str
    label: str | None
    status: str
    turn_count: int
    created_at: str
    updated_at: str
    expires_at: str
    is_expired: bool
    mock_mode: bool


class SessionInventory(BaseModel):
    session: Session
    record_counts: dict[str, int]


class ProfileIn(BaseModel):
    """Manually supplied listener profile. No web lookup is ever performed."""

    research_topics: list[str] = Field(default_factory=list, max_length=15)
    recent_paper_keywords: list[str] = Field(default_factory=list, max_length=20)
    familiar_methods: list[str] = Field(default_factory=list, max_length=15)
    application_domains: list[str] = Field(default_factory=list, max_length=15)
    connection_notes: list[str] = Field(default_factory=list, max_length=10)


class ProfileResult(BaseModel):
    profile_id: str
    hypotheses_created: int
    rejected_fields: list[str]
    confidence_cap: float
    notice: str = "Profile-derived items are uncertain priors. Conversational evidence overrides them."
