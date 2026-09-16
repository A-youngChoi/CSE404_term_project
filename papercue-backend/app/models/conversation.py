from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import ListenerAct, Phase, PresenterAct, Speaker


class TurnIn(BaseModel):
    speaker: Speaker
    text: str = Field(min_length=1, max_length=2000)
    timestamp: datetime | None = None


class Turn(BaseModel):
    id: str
    session_id: str
    turn_index: int
    speaker: Speaker
    text: str
    spoken_at: str
    input_source: str
    analysis_status: str
    created_at: str


class UnresolvedIssue(BaseModel):
    topic: str
    raised_turn_id: str
    status: str = "open"  # open | addressed


class ConversationState(BaseModel):
    phase: Phase = Phase.opening
    topic: str | None = None
    listener_act: ListenerAct = ListenerAct.none
    presenter_act: PresenterAct = PresenterAct.none
    explicit_question: str | None = None
    explicit_question_turn_id: str | None = None
    unresolved_issues: list[UnresolvedIssue] = Field(default_factory=list)
    detected_concerns: list[str] = Field(default_factory=list)
    possible_misunderstanding: str | None = None
    recently_explained_unit_ids: list[str] = Field(default_factory=list)
    recent_cues: list[str] = Field(default_factory=list)
    turn_count: int = 0
    listener_turn_count: int = 0
    turns_since_last_cue: int = 0
    last_listener_turn_id: str | None = None
    last_speaker: Speaker | None = None


class OlderContextSummary(BaseModel):
    """Structured digest of turns that have left the recent window."""

    summarized_turns: int = 0
    topics_covered: list[str] = Field(default_factory=list)
    concerns_raised: list[str] = Field(default_factory=list)
    listener_questions: int = 0
    explained_unit_ids: list[str] = Field(default_factory=list)


class MemoryHit(BaseModel):
    summary_id: str
    turn_id: str
    turn_index: int
    speaker: str
    topic: str | None
    gist: str
    score: float


class TurnResult(BaseModel):
    turn: Turn
    analysis_status: str
    state: ConversationState
    evidence_accepted: list[dict] = Field(default_factory=list)
    evidence_rejected: list[dict] = Field(default_factory=list)
    belief_changes: list[dict] = Field(default_factory=list)
    latency_ms: dict[str, float]
    llm_provider: str
    mock_mode: bool
