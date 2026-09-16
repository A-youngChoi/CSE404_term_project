"""Controlled vocabularies shared across the pipeline."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class UnitType(StrEnum):
    problem = "problem"
    motivation = "motivation"
    research_gap = "research_gap"
    core_idea = "core_idea"
    method = "method"
    evidence = "evidence"
    result = "result"
    contribution = "contribution"
    application = "application"
    limitation = "limitation"
    example = "example"
    connection = "connection"


class Phase(StrEnum):
    opening = "opening"
    problem_framing = "problem_framing"
    method_elaboration = "method_elaboration"
    evidence_discussion = "evidence_discussion"
    application_discussion = "application_discussion"
    connection = "connection"
    challenge = "challenge"
    closing = "closing"


class Speaker(StrEnum):
    listener = "listener"
    presenter = "presenter"


class ListenerAct(StrEnum):
    question = "question"
    clarification_request = "clarification_request"
    self_report = "self_report"
    statement = "statement"
    agreement = "agreement"
    challenge = "challenge"
    backchannel = "backchannel"
    closing = "closing"
    none = "none"


class PresenterAct(StrEnum):
    explanation = "explanation"
    example = "example"
    answer = "answer"
    question = "question"
    acknowledgment = "acknowledgment"
    closing = "closing"
    other = "other"
    none = "none"


class Dimension(StrEnum):
    knowledge = "knowledge"
    familiarity = "familiarity"
    interest = "interest"
    goal = "goal"
    connection = "connection"
    concern = "concern"
    explanation_level = "explanation_level"
    engagement = "engagement"
    unresolved_issue = "unresolved_issue"


# Allowed values per dimension. Anything else proposed by a model is rejected.
DIMENSION_VALUES: dict[Dimension, tuple[str, ...]] = {
    Dimension.knowledge: ("novice", "intermediate", "advanced"),
    Dimension.familiarity: ("unfamiliar", "partial", "familiar"),
    Dimension.interest: ("interested", "not_interested"),
    Dimension.goal: ("present", "absent"),
    Dimension.connection: ("relevant", "not_relevant"),
    Dimension.concern: ("raised", "resolved"),
    Dimension.explanation_level: ("simple", "moderate", "technical"),
    Dimension.engagement: ("low", "medium", "high"),
    Dimension.unresolved_issue: ("open", "resolved"),
}


class EvidenceType(StrEnum):
    explicit = "explicit"            # the listener said it directly ("I'm not familiar with LLMs")
    behavioral = "behavioral"        # observable dialogue behaviour (asked for simpler wording)
    weak_inference = "weak_inference"  # plausible but indirect
    profile = "profile"              # manually supplied profile prior


class CueAction(StrEnum):
    emphasize = "emphasize"
    simplify = "simplify"
    elaborate = "elaborate"
    connect = "connect"
    reframe = "reframe"
    address_concern = "address_concern"
    verify = "verify"
    recover = "recover"
    acknowledge_limitation = "acknowledge_limitation"
    close = "close"


# Actions whose cue may legitimately have no paper grounding.
UNGROUNDED_ACTIONS = {CueAction.verify, CueAction.close}


class SessionMode(StrEnum):
    on_demand = "on_demand"
    auto_candidate = "auto_candidate"
