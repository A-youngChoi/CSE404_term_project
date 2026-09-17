"""Typed records for the presentation-support simulation.

Everything the Judge may see is in `ObservedEvent`. Ground truth lives in a separate
`GroundTruth` record that only the evaluator reads. Structured rationales replace any
free-form model reasoning: no chain-of-thought is stored or exposed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Language = Literal["ko", "en"]
EventType = Literal["speech", "silence", "audience_question", "qa_answer"]
Decision = Literal["INTERVENE_NOW", "WAIT_AND_OBSERVE", "DO_NOT_INTERVENE", "SUPPRESS_DUE_TO_RECENT_INTERVENTION"]
DECISIONS: tuple[str, ...] = ("INTERVENE_NOW", "WAIT_AND_OBSERVE", "DO_NOT_INTERVENE",
                              "SUPPRESS_DUE_TO_RECENT_INTERVENTION")

IssueType = Literal[
    "none", "missing_key_point", "content_at_risk", "pace_too_fast", "pace_too_slow", "filler_repetition",
    "long_silence", "audience_confusion", "low_engagement", "qa_misunderstanding", "time_pressure",
]
PromptType = Literal[
    "content_reminder", "next_point_suggestion", "time_management", "pace_adjustment", "repetition_alert",
    "silence_recovery", "clarification_suggestion", "question_reinterpretation", "answer_structure", "wrap_up",
]
PROMPT_TYPES: tuple[str, ...] = PromptType.__args__  # type: ignore[attr-defined]
PromptLength = Literal["short", "medium"]
Severity = Literal["none", "low", "medium", "high", "critical"]
MemoryType = Literal["working", "episodic", "semantic", "intervention", "reflection"]
MemoryOp = Literal["created", "reinforced", "weakened", "revised", "deactivated", "retrieved"]
Outcome = Literal["pending", "recovered", "partially_recovered", "not_recovered"]


# --------------------------------------------------------------------------- knowledge base
class KeyPoint(BaseModel):
    kp_id: str
    text: str
    keywords: list[str]
    essential: bool = False
    cue: str
    category: str


class Slide(BaseModel):
    slide: int
    title: str
    planned_seconds: float
    note: str = ""
    is_qa: bool = False
    key_points: list[KeyPoint] = Field(default_factory=list)


class KnowledgeChunk(BaseModel):
    """One retrievable unit. Slide notes, must-mention points, Q&A and glossary are flattened into chunks."""

    chunk_id: str
    kb_id: str
    category: str  # core_claim, background, method, result, limitation, related_work, script, slide_note,
    # must_mention, expected_qa, glossary
    source_document: str
    title: str
    text: str
    keywords: list[str] = Field(default_factory=list)
    slide: int | None = None
    essential: bool = False
    cue: str | None = None
    # expected_qa
    question: str | None = None
    answer_keywords: list[str] = Field(default_factory=list)
    topic: str | None = None
    # glossary
    term: str | None = None
    gloss: str | None = None
    # must_mention
    kp_id: str | None = None


class KnowledgeBase(BaseModel):
    kb_id: str
    language: Language
    paper_title: str
    synthetic: bool = True
    documents: list[dict[str, str]]
    slides: list[Slide]
    chunks: list[KnowledgeChunk]

    def slide(self, number: int) -> Slide | None:
        return next((s for s in self.slides if s.slide == number), None)

    def planned_start(self, number: int) -> float:
        return sum(s.planned_seconds for s in self.slides if s.slide < number and not s.is_qa)

    def key_point(self, kp_id: str) -> tuple[Slide, KeyPoint] | None:
        for s in self.slides:
            for kp in s.key_points:
                if kp.kp_id == kp_id:
                    return s, kp
        return None

    def chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)


# --------------------------------------------------------------------------- presenters / sessions
class PriorEpisode(BaseModel):
    summary: str
    issue: str
    category: str | None = None
    importance: float = 0.5


class PriorTrait(BaseModel):
    summary: str
    attribute: str
    value: Any
    confidence: float = 0.4


class PresenterProfile(BaseModel):
    presenter_id: str
    display_name: str
    preferred_language: Language
    expertise_level: Literal["novice", "intermediate", "expert"]
    baseline_speech_rate: float
    speech_rate_unit: Literal["wpm", "spm"]
    preferred_prompt_length: PromptLength = "short"
    baseline_filler_per_event: float = 1.0
    prior_episodes: list[PriorEpisode] = Field(default_factory=list)
    prior_traits: list[PriorTrait] = Field(default_factory=list)


class AudienceSignal(BaseModel):
    attention: float = 0.7
    confusion: float = 0.15
    reaction: str = "neutral"
    source: str = "mock_audience_sensor"


class PresenterSignal(BaseModel):
    arousal: float = 0.4
    label: str = "calm"
    source: str = "mock_wearable"


class RepeatedPhrase(BaseModel):
    text: str
    count: int


class GroundTruth(BaseModel):
    """Researcher labels. Never passed to the Judge, prompt generator, memory or user model."""

    event_id: str
    issue: str = "none"
    expected_intervention: bool = False
    expected_prompt_type: str | None = None
    expected_decision: str | None = None
    expected_outcome: str | None = None
    severity: Severity = "none"
    target: str | None = None
    note: str | None = None


class ObservedEvent(BaseModel):
    """One timestamped observation, as an input adapter (STT, slide tracker, sensors) would deliver it."""

    event_id: str
    session_id: str
    index: int
    timestamp: str
    elapsed_time: float
    language: Language
    event_type: EventType = "speech"
    current_slide: int
    slide_title: str
    slide_expected_content: list[str]
    transcript_chunk: str = ""
    speech_rate: float = 0.0
    speech_rate_unit: Literal["wpm", "spm"] = "wpm"
    silence_duration: float = 0.5
    filler_count: int = 0
    repeated_phrase: RepeatedPhrase | None = None
    audience_signal: AudienceSignal = Field(default_factory=AudienceSignal)
    audience_question: str | None = None
    remaining_time: float
    presenter_state: PresenterSignal = Field(default_factory=PresenterSignal)
    # Filled by the engine from its own state (the dataset does not script system behaviour).
    previous_interventions: list[dict[str, Any]] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)


class SessionMeta(BaseModel):
    session_id: str
    title: str
    title_ko: str
    language: Language
    scenario_type: str
    presenter_id: str
    kb_id: str
    start_time: str
    planned_duration_s: float
    excerpt_start_elapsed: float = 0.0
    description: str = ""
    event_count: int = 0
    duration_s: float = 0.0
    synthetic: bool = True


class PresentationSession(BaseModel):
    meta: SessionMeta
    events: list[ObservedEvent]
    ground_truth: dict[str, GroundTruth]


# --------------------------------------------------------------------------- runtime state
class Issue(BaseModel):
    issue_id: str
    type: IssueType
    status: Literal["active", "resolved"] = "active"
    severity: float
    urgency: float
    essential: bool = False
    detector_confidence: float = 0.7
    target: str | None = None  # kp_id, chunk_id, phrase, slide number
    target_label: str | None = None
    first_event_id: str
    last_event_id: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    resolved_event_id: str | None = None


class PresentationContext(BaseModel):
    session_id: str
    language: Language
    event_id: str | None = None
    elapsed_time: float = 0.0
    remaining_time: float = 0.0
    current_slide: int = 0
    slide_title: str = ""
    slide_entered_at: float = 0.0
    time_on_slide: float = 0.0
    planned_slide_seconds: float = 0.0
    schedule_lag_s: float = 0.0
    time_needed_s: float = 0.0
    time_budget_ratio: float = 1.0
    expected_content: list[dict[str, Any]] = Field(default_factory=list)
    covered_kp_ids: list[str] = Field(default_factory=list)
    missed_kp_ids: list[str] = Field(default_factory=list)
    recent_transcript: list[dict[str, Any]] = Field(default_factory=list)
    speech_rate: float = 0.0
    speech_rate_ratio: float = 1.0
    silence_duration: float = 0.0
    filler_count: int = 0
    filler_window: int = 0
    repeated_phrase: dict[str, Any] | None = None
    audience: dict[str, Any] = Field(default_factory=dict)
    presenter_signal: dict[str, Any] = Field(default_factory=dict)
    active_question: dict[str, Any] | None = None
    active_issues: list[Issue] = Field(default_factory=list)
    new_issue_ids: list[str] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    recent_interventions: list[dict[str, Any]] = Field(default_factory=list)
    slide_changed: bool = False
    skipped_slides: list[int] = Field(default_factory=list)


class MemoryChange(BaseModel):
    op: MemoryOp
    memory_id: str
    step: int
    event_id: str | None
    reason: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class MemoryItem(BaseModel):
    memory_id: str
    memory_type: MemoryType
    content: str
    created_step: int
    created_elapsed: float
    last_updated_step: int
    last_accessed_elapsed: float
    source_event_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.6
    recency: float = 1.0
    retrieval_score: float = 0.0
    retrieval_count: int = 0
    active: bool = True
    related_slide: int | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str = ""
    used_in_decisions: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["session", "prior"] = "session"


class RetrievalHit(BaseModel):
    id: str
    score: float
    components: dict[str, float] = Field(default_factory=dict)
    selected: bool = False
    used: bool = False
    reason: str = ""


class MemoryRetrieval(BaseModel):
    retrieval_id: str
    query: str
    query_terms: list[str]
    weights: dict[str, float]
    hits: list[RetrievalHit]


class KnowledgeRetrieval(BaseModel):
    retrieval_id: str
    retriever: str
    query: str
    query_terms: list[str]
    hits: list[RetrievalHit]


class UserModelEvidence(BaseModel):
    ref: str  # event / memory / prompt id
    note: str


class UserModelChange(BaseModel):
    change_id: str
    attribute: str
    step: int
    event_id: str | None
    elapsed: float
    old_value: Any = None
    new_value: Any = None
    old_confidence: float | None = None
    new_confidence: float
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class UserModelAttribute(BaseModel):
    key: str
    value: Any
    confidence: float
    source: Literal["prior", "observed", "mixed"] = "prior"
    evidence: list[UserModelEvidence] = Field(default_factory=list)
    updated_step: int = 0


class ScoreComponent(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: str = ""


class JudgeDecision(BaseModel):
    """Structured decision rationale (no chain-of-thought)."""

    decision_id: str
    step: int
    event_id: str
    decision: Decision
    detected_issue: str = "none"
    issue_id: str | None = None
    target: str | None = None
    severity: float = 0.0
    urgency: float = 0.0
    confidence: float = 0.0
    utility: float = 0.0
    evidence_event_ids: list[str] = Field(default_factory=list)
    used_memory_ids: list[str] = Field(default_factory=list)
    used_knowledge_ids: list[str] = Field(default_factory=list)
    used_user_model_attributes: list[str] = Field(default_factory=list)
    supporting_reasons: list[str] = Field(default_factory=list)
    counter_reasons: list[str] = Field(default_factory=list)
    reason_for_final_decision: str = ""
    recommended_prompt_type: str | None = None
    recommended_prompt_length: PromptLength | None = None
    cooldown_seconds: float = 0.0
    score_breakdown: list[ScoreComponent] = Field(default_factory=list)
    raw_signals: dict[str, Any] = Field(default_factory=dict)
    considered_issues: list[dict[str, Any]] = Field(default_factory=list)
    cooldown_state: dict[str, Any] = Field(default_factory=dict)
    policy_overrides: list[str] = Field(default_factory=list)
    judge: str = "mock:rule-based-judge"
    is_mock: bool = True
    fallback_used: bool = False
    fallback_reason: str | None = None
    latency_ms: float = 0.0


class PromptCandidate(BaseModel):
    candidate_id: str
    text: str
    prompt_type: str
    length: PromptLength
    score: float
    score_parts: dict[str, float] = Field(default_factory=dict)
    selected: bool = False
    not_selected_reason: str | None = None


class GeneratedPrompt(BaseModel):
    prompt_id: str
    decision_id: str
    step: int
    event_id: str
    created_elapsed: float
    text: str
    language: Language
    prompt_type: str
    length: PromptLength
    priority: Literal["low", "medium", "high"]
    priority_score: float
    urgency: float
    expires_at_elapsed: float
    display_seconds: float
    purpose: str
    selection_reason: str
    context_used: dict[str, Any] = Field(default_factory=dict)
    personalization: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    alternatives: list[PromptCandidate] = Field(default_factory=list)
    delivered: bool = False
    delivery_status: Literal["delivered", "withheld"] = "withheld"
    not_delivered_reason: str | None = None
    generator: str = "template"
    fallback_used: bool = False
    outcome: Outcome = "pending"
    outcome_detail: dict[str, Any] = Field(default_factory=dict)
    issue_id: str | None = None
    issue_type: str | None = None
    target: str | None = None


class MobileState(BaseModel):
    """What the presenter's phone shows. Deliberately minimal."""

    run_id: str
    session_id: str
    elapsed_time: float
    prompt_id: str | None = None
    text: str | None = None
    language: Language = "en"
    prompt_type: str | None = None
    priority: str | None = None
    urgency: float | None = None
    expires_at_elapsed: float | None = None
    display_seconds: float | None = None
    remaining_seconds: float | None = None
    finished: bool = False
    is_mock: bool = True
    playing: bool = False
    speed: float = 1.0
    wall_clock_updated: float = 0.0


class StageResult(BaseModel):
    name: str
    status: Literal["ok", "fallback", "error", "skipped"] = "ok"
    latency_ms: float = 0.0
    error: str | None = None


class TimelineMark(BaseModel):
    lane: str
    elapsed: float
    step: int
    label: str
    kind: str = ""
    ref: str | None = None


class StepRecord(BaseModel):
    step: int
    event_id: str
    elapsed_time: float
    event: ObservedEvent
    context: PresentationContext
    memory_changes: list[MemoryChange]
    memory_snapshot: list[dict[str, Any]]
    memory_retrieval: MemoryRetrieval | None = None
    user_model_changes: list[UserModelChange]
    user_model_snapshot: dict[str, dict[str, Any]]
    knowledge_retrieval: KnowledgeRetrieval | None = None
    decision: JudgeDecision
    prompt: GeneratedPrompt | None = None
    outcome_updates: list[dict[str, Any]] = Field(default_factory=list)
    mobile: MobileState
    stages: list[StageResult]
    timeline: list[TimelineMark]
