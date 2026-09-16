"""Cue Generator: decision + retrieved units + evidence -> one short, filtered cue.

The local model only words the cue. Deterministic checks decide whether it is delivered.
If grounding or confidence is insufficient, a fixed verification cue may replace it;
otherwise no cue is returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import ModelOutputError
from app.llm.base import LocalLLMProvider
from app.models.audience import EvidenceItem
from app.models.conversation import ConversationState
from app.models.cue import CueDecision, FilterCheck
from app.models.enums import CueAction
from app.models.llm_outputs import CueGenerationOutput
from app.models.paper import PaperUnit
from app.safety.cue_filter import (
    STATUS_DELIVERABLE,
    STATUS_FORMAT,
    FilterContext,
    fallback_allowed,
    run_cue_filter,
)

UNIT_CONTENT_CHARS = 400

VERIFY_CUES = {
    "en": {"detail level": "check desired detail level", "practical applications": "ask which application matters",
           "_default": "ask their main concern"},
    "ko": {"detail level": "원하는 설명 수준 확인", "practical applications": "관심 있는 적용 분야 묻기",
           "_default": "주요 우려 물어보기"},
}


@dataclass
class GenerationResult:
    status: str
    final_cue: str | None
    candidate: CueGenerationOutput | None
    checks: list[FilterCheck] = field(default_factory=list)
    fallback_used: bool = False
    fallback: CueGenerationOutput | None = None
    fallback_checks: list[FilterCheck] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    generator: str = ""


def grounded_text(unit: PaperUnit) -> str:
    return f"{unit.title} {unit.short_explanation} {' '.join(unit.keywords)} {unit.content}"


class CueGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_payload(self, decision: CueDecision, units: list[PaperUnit], evidence: list[EvidenceItem],
                      state: ConversationState, recent_cues: list[str], paper: dict, language: str) -> dict:
        return {
            "action": decision.action.value,
            "target": decision.target,
            "language": language,
            "decision_reason": decision.short_reason,
            "conversation_state": {"phase": state.phase.value, "topic": state.topic,
                                   "listener_act": state.listener_act.value},
            "units": [
                {"id": u.id, "unit_type": u.unit_type.value, "title": u.title,
                 "short_explanation": u.short_explanation, "keywords": u.keywords,
                 "content_excerpt": u.content[:UNIT_CONTENT_CHARS]}
                for u in units
            ],
            "evidence": [{"id": e.id, "dimension": e.dimension.value, "key": e.key, "value": e.value,
                          "observation": e.observation} for e in evidence],
            "recent_cues": recent_cues,
            "preferred_terminology": paper.get("preferred_terminology", {}),
            "forbidden_claims": paper.get("forbidden_claims", []),
            "max_words": self.settings.cue_max_words,
            "target_words": self.settings.cue_target_words,
        }

    def _context(self, action: CueAction, units: list[PaperUnit], evidence_ids: set[str], recent_cues: list[str],
                 paper: dict) -> FilterContext:
        return FilterContext(
            decided_action=action,
            retrieved_units={u.id: grounded_text(u) for u in units},
            known_evidence_ids=evidence_ids,
            recent_cues=recent_cues,
            forbidden_claims=paper.get("forbidden_claims", []),
            max_words=self.settings.cue_max_words,
            min_confidence=self.settings.min_cue_confidence,
            repeat_similarity=self.settings.cue_repeat_similarity,
        )

    def verification_cue(self, decision: CueDecision, language: str) -> CueGenerationOutput:
        table = VERIFY_CUES.get(language, VERIFY_CUES["en"])
        target = (decision.target or "").lower()
        cue = table.get(target, table["_default"])
        return CueGenerationOutput(cue=cue, action=CueAction.verify, grounding_unit_ids=[], audience_evidence_ids=[],
                                   confidence=0.6, rationale="Verification question used instead of an ungrounded cue.")

    def generate(self, provider: LocalLLMProvider, decision: CueDecision, units: list[PaperUnit],
                 evidence: list[EvidenceItem], session_evidence_ids: set[str], state: ConversationState,
                 recent_cues: list[str], paper: dict, language: str) -> GenerationResult:
        payload = self.build_payload(decision, units, evidence, state, recent_cues, paper, language)
        try:
            candidate = provider.generate_structured("cue_generation", payload, CueGenerationOutput)
        except ModelOutputError as exc:
            # Invalid JSON after retries: fail safely with no cue.
            return GenerationResult(status=STATUS_FORMAT, final_cue=None, candidate=None,
                                    checks=[FilterCheck(name="valid_json", passed=False, detail=str(exc.details.get("error", "")))],
                                    validation_errors=["invalid_model_json"], generator=provider.name)

        ctx = self._context(decision.action, units, session_evidence_ids, recent_cues, paper)
        outcome = run_cue_filter(candidate, ctx)
        checks = [FilterCheck(name="valid_json", passed=True, detail="schema validated"), *outcome.checks]
        result = GenerationResult(status=outcome.status, final_cue=candidate.cue.strip() if outcome.passed else None,
                                  candidate=candidate, checks=checks, validation_errors=outcome.failed_names,
                                  generator=provider.name)
        if outcome.passed or not fallback_allowed(outcome.status):
            return result

        fallback = self.verification_cue(decision, language)
        fb_ctx = self._context(CueAction.verify, units, session_evidence_ids, recent_cues, paper)
        fb_outcome = run_cue_filter(fallback, fb_ctx)
        result.fallback_checks = fb_outcome.checks
        if fb_outcome.passed:
            result.fallback_used = True
            result.final_cue = fallback.cue
            result.status = STATUS_DELIVERABLE
            result.fallback = fallback
        return result
