"""Structured processing traces for researcher inspection.

A trace stage stores references (IDs), statuses, small structured outputs (enums, scores),
validation error codes and a short rationale. It never stores raw utterances, prompts,
or model reasoning.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PIPELINE_VERSION = "papercue-pipeline-0.2"

ComponentType = Literal["input", "deterministic", "local_llm", "mock_rules", "local_embedding", "database"]
StageStatus = Literal["success", "failed", "skipped", "partial"]

# Canonical stage order shown in the dashboard (turn trace + cue trace merged).
STAGE_ORDER = [
    "input_turn",
    "recent_context",
    "dialogue_act_analysis",
    "evidence_extraction",
    "audience_update_proposal",
    "audience_update_applied",
    "retrieval_query",
    "retrieval",
    "intervention_decision",
    "cue_candidate",
    "safety_grounding_check",
    "final_result",
]


class TraceStage(BaseModel):
    stage_name: str
    component_type: ComponentType
    model_name: str | None = None
    prompt_version: str | None = None
    status: StageStatus = "success"
    duration_ms: float = 0.0
    input_reference_ids: list[str] = Field(default_factory=list)
    output_reference_ids: list[str] = Field(default_factory=list)
    output: dict = Field(default_factory=dict, description="Small structured output: enums, scores, counts, IDs.")
    validation_errors: list[str] = Field(default_factory=list, description="Error codes only.")
    short_rationale: str = ""
    rationale_code: str | None = Field(default=None, description="Stable code the dashboard translates to Korean.")


class PipelineTrace(BaseModel):
    trace_id: str
    session_id: str
    turn_id: str | None
    kind: Literal["turn", "cue", "auto_candidate"]
    decision_id: str | None = None
    pipeline_version: str = PIPELINE_VERSION
    status: str
    started_at: str
    completed_at: str
    stages: list[TraceStage]
