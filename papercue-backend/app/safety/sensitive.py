"""Prohibited-inference and characterization detection (English + Korean).

PaperCue must never infer or voice sensitive personal attributes, psychological
judgments, or manipulative strategies. These lists are deliberately conservative.
"""

from __future__ import annotations

import re

SENSITIVE_TERMS = (
    # political / religious / ethnic / sexual
    "political", "politics", "liberal", "conservative", "religion", "religious", "christian", "muslim", "jewish",
    "buddhist", "atheist", "ethnic", "ethnicity", "race", "racial", "sexuality", "sexual orientation", "gay",
    "lesbian", "bisexual", "transgender",
    # health / psychological
    "health condition", "illness", "disease", "disability", "disorder", "diagnosis", "diagnosed", "adhd", "autism",
    "autistic", "depression", "depressed", "anxiety", "anxious", "bipolar", "mental health", "psychological",
    "neurotic", "narcissist", "personality type", "introvert", "extrovert", "mbti",
    # Korean
    "정치", "종교", "민족", "인종", "성적 지향", "성소수자", "질병", "장애", "진단", "우울", "불안", "정신",
    "성격 유형", "심리 상태",
)

# Judgments about the person (as opposed to their current conversational need).
CHARACTERIZATION_TERMS = (
    "skeptical", "sceptical", "stupid", "dumb", "incompetent", "ignorant", "weak", "hostile", "arrogant",
    "insecure", "lazy", "naive", "clueless", "negative person", "technically weak",
    "회의적", "부정적", "무능", "무지", "적대적", "거만",
)

MANIPULATION_TERMS = (
    "convince", "persuade them", "pressure", "exploit", "manipulat", "flatter", "their fear", "their anxiety",
    "guilt", "trick", "설득", "압박", "이용해", "조종", "두려움",
)

VAGUE_CUES = {
    "explain better", "be clear", "be clearer", "do better", "explain more", "say more", "keep going",
    "더 잘 설명", "명확하게", "잘 설명",
}

_PERSON_ASSERTION = re.compile(
    r"\b(they|they're|listener|he|she|he's|she's|user|audience)\b\s*(are|is|seems?|looks?|appears?|feels?)?\b", re.I
)
_KO_PERSON_ASSERTION = re.compile(r"(청중|이 사람|상대|그는|그녀는)(은|는|이|가)?\s*\S*(이다|입니다|하다|같다|보인다)")


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    low = text.lower()
    found = []
    for term in terms:
        if re.search(r"[a-z]", term):
            if re.search(r"(?<![a-z])" + re.escape(term), low):
                found.append(term)
        elif term in low:
            found.append(term)
    return found


def sensitive_hits(text: str) -> list[str]:
    return _hits(text, SENSITIVE_TERMS)


def characterization_hits(text: str, include_assertions: bool = True) -> list[str]:
    """Trait words; with include_assertions, also sentences asserting what the person *is* (used for cues)."""
    hits = _hits(text, CHARACTERIZATION_TERMS)
    if not include_assertions:
        return hits
    if _KO_PERSON_ASSERTION.search(text or ""):
        hits.append("person_assertion")
    m = _PERSON_ASSERTION.search(text or "")
    if m and m.group(2):
        hits.append("person_assertion")
    return hits


def manipulation_hits(text: str) -> list[str]:
    return _hits(text, MANIPULATION_TERMS)


def is_vague(text: str) -> bool:
    return (text or "").strip().lower().rstrip(".!") in VAGUE_CUES


def is_prohibited_profile_text(text: str) -> bool:
    return bool(sensitive_hits(text) or characterization_hits(text, include_assertions=False))
