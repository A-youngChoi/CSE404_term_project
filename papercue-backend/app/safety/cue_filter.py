"""Deterministic grounding and safety filter applied to every generated cue candidate."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.utils import jaccard, word_count
from app.models.cue import FilterCheck
from app.models.enums import UNGROUNDED_ACTIONS, CueAction
from app.models.llm_outputs import CueGenerationOutput
from app.safety import sensitive

# Final statuses (stable codes; the dashboard translates them to Korean).
STATUS_DELIVERABLE = "deliverable"
STATUS_LOW_CONFIDENCE = "held_low_confidence"
STATUS_NO_GROUNDING = "insufficient_grounding"
STATUS_DUPLICATE = "duplicate_recent"
STATUS_SENSITIVE = "sensitive_risk"
STATUS_FORMAT = "format_invalid"
STATUS_NOT_NEEDED = "no_cue_needed"
STATUS_MODEL_ERROR = "local_model_error"

# Check name -> status category when the check fails.
CHECK_CATEGORY = {
    "action_matches_decision": STATUS_FORMAT,
    "word_limit": STATUS_FORMAT,
    "cue_not_answer": STATUS_FORMAT,
    "not_vague": STATUS_FORMAT,
    "grounding_ids_valid": STATUS_NO_GROUNDING,
    "no_unsupported_claims": STATUS_NO_GROUNDING,
    "no_forbidden_claims": STATUS_NO_GROUNDING,
    "audience_claims_supported": STATUS_SENSITIVE,
    "no_sensitive_inference": STATUS_SENSITIVE,
    "no_manipulation": STATUS_SENSITIVE,
    "not_repeated": STATUS_DUPLICATE,
    "confidence_threshold": STATUS_LOW_CONFIDENCE,
}
_STATUS_PRIORITY = [STATUS_SENSITIVE, STATUS_FORMAT, STATUS_NO_GROUNDING, STATUS_DUPLICATE, STATUS_LOW_CONFIDENCE]

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%?")
_NUMBER_WORDS = ("percent", "percentage", "twice", "half", "퍼센트", "두 배", "절반")
_CLAIM_WORDS = (
    "outperform", "significant", "proven", "prove", "guarantee", "always", "never", "best", "state-of-the-art",
    "state of the art", "better than", "faster than", "accurate", "보장", "항상", "최고", "입증", "유의미", "능가",
)
_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|we|we're|our|ours|my|me|us)\b", re.I)
_KO_FIRST_PERSON = ("저는", "제가", "저희", "우리는", "우리가", "제 ")


@dataclass
class FilterContext:
    decided_action: CueAction
    retrieved_units: dict[str, str]  # unit_id -> grounded text (title + content + explanation)
    known_evidence_ids: set[str]
    recent_cues: list[str]
    forbidden_claims: list[str] = field(default_factory=list)
    max_words: int = 12
    min_confidence: float = 0.45
    repeat_similarity: float = 0.6


@dataclass
class FilterOutcome:
    passed: bool
    checks: list[FilterCheck]
    status: str

    @property
    def failed_names(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]


def _check(name: str, passed: bool, detail: str = "") -> FilterCheck:
    return FilterCheck(name=name, passed=passed, detail=detail)


def _terms_absent(cue: str, terms: tuple[str, ...], grounded: str) -> list[str]:
    low, g = cue.lower(), grounded.lower()
    return [t for t in terms if t in low and t not in g]


def run_cue_filter(candidate: CueGenerationOutput, ctx: FilterContext) -> FilterOutcome:
    cue = candidate.cue.strip()
    low = cue.lower()
    checks: list[FilterCheck] = []

    checks.append(_check("action_matches_decision", candidate.action == ctx.decided_action,
                         f"decided={ctx.decided_action.value} returned={candidate.action.value}"))

    n = word_count(cue)
    checks.append(_check("word_limit", 1 <= n <= ctx.max_words, f"{n} words (max {ctx.max_words})"))

    sentences = [s for s in re.split(r"[.!?。]\s+", cue) if s.strip()]
    first_person = bool(_FIRST_PERSON.search(cue)) or any(k in cue for k in _KO_FIRST_PERSON)
    answer_like = len(sentences) > 1 or first_person or "\n" in cue or '"' in cue or "http" in low or "`" in cue
    checks.append(_check("cue_not_answer", not answer_like,
                         "first-person or multi-sentence text" if answer_like else "short cue form"))

    checks.append(_check("not_vague", not sensitive.is_vague(cue), "vague instruction" if sensitive.is_vague(cue) else ""))

    ids = candidate.grounding_unit_ids
    unknown = [i for i in ids if i not in ctx.retrieved_units]
    needs_grounding = candidate.action not in UNGROUNDED_ACTIONS
    grounded_ok = not unknown and (bool(ids) or not needs_grounding)
    detail = (
        f"unknown unit ids: {len(unknown)}" if unknown else ("no grounding unit" if not grounded_ok else f"{len(ids)} unit(s)")
    )
    checks.append(_check("grounding_ids_valid", grounded_ok, detail))

    grounded_text = " ".join(ctx.retrieved_units[i] for i in ids if i in ctx.retrieved_units)
    bad_numbers = [m.group(0).strip() for m in _NUMBER_RE.finditer(cue) if m.group(0).strip() not in grounded_text]
    bad_terms = _terms_absent(cue, _NUMBER_WORDS + _CLAIM_WORDS, grounded_text)
    unsupported = bad_numbers + bad_terms
    checks.append(_check("no_unsupported_claims", not unsupported,
                         f"{len(unsupported)} unsupported number/claim term(s)" if unsupported else ""))

    forbidden = [c for c in ctx.forbidden_claims if c and (c.lower() in low or jaccard(c, cue) >= 0.5)]
    checks.append(_check("no_forbidden_claims", not forbidden, f"{len(forbidden)} forbidden claim match(es)" if forbidden else ""))

    unknown_ev = [e for e in candidate.audience_evidence_ids if e not in ctx.known_evidence_ids]
    trait = sensitive.characterization_hits(cue)
    checks.append(_check("audience_claims_supported", not unknown_ev and not trait,
                         ("unknown evidence ids " if unknown_ev else "") + ("characterizes the listener" if trait else "")))

    sens = sensitive.sensitive_hits(cue) + sensitive.sensitive_hits(candidate.rationale)
    checks.append(_check("no_sensitive_inference", not sens, "sensitive attribute term" if sens else ""))

    manip = sensitive.manipulation_hits(cue)
    checks.append(_check("no_manipulation", not manip, "manipulative wording" if manip else ""))

    best = max((jaccard(cue, prev) for prev in ctx.recent_cues), default=0.0)
    exact = any(cue.lower() == prev.lower() for prev in ctx.recent_cues)
    repeated = exact or best >= ctx.repeat_similarity
    checks.append(_check("not_repeated", not repeated, f"max similarity {best:.2f}"))

    checks.append(_check("confidence_threshold", candidate.confidence >= ctx.min_confidence,
                         f"{candidate.confidence:.2f} (min {ctx.min_confidence:.2f})"))

    failed = {CHECK_CATEGORY[c.name] for c in checks if not c.passed}
    status = next((s for s in _STATUS_PRIORITY if s in failed), STATUS_DELIVERABLE)
    return FilterOutcome(passed=not failed, checks=checks, status=status)


def fallback_allowed(status: str) -> bool:
    """A verification cue may replace the candidate only when grounding/confidence was the problem."""
    return status in (STATUS_NO_GROUNDING, STATUS_LOW_CONFIDENCE)
