"""Lexicons used by the deterministic mock provider (English + Korean).

These rules exist so the architecture can be exercised and tested without a model.
They are intentionally simple and are NOT a substitute for a language model.
"""

from __future__ import annotations

import re

# Canonical concern keys -> trigger terms.
CONCERN_LEXICON: dict[str, tuple[str, ...]] = {
    "privacy": (
        "privacy", "server", "cloud", "upload", "store", "stored", "storage", "record", "recorded", "recording",
        "data leave", "send our conversation", "개인정보", "서버", "저장", "업로드", "클라우드", "녹음", "유출",
    ),
    "latency": ("latency", "slow", "delay", "lag", "real-time", "real time", "지연", "느리", "실시간"),
    "accuracy": ("accurate", "accuracy", "wrong cue", "hallucinat", "mistake", "error rate", "정확", "틀린", "오류"),
    "distraction": ("distract", "interrupt", "annoying", "cognitive load", "방해", "산만", "주의"),
}

# Korean display labels for canonical keys (used for Korean cues).
KO_LABELS: dict[str, str] = {
    "privacy": "개인정보",
    "latency": "지연 시간",
    "accuracy": "정확도",
    "distraction": "주의 분산",
    "practical applications": "실제 활용",
    "wearable sensing": "웨어러블 센싱",
    "detail level": "설명 수준",
}

QUESTION_WORDS = (
    "what", "how", "why", "does", "do ", "is ", "are ", "can ", "could", "would", "will ", "where", "which",
    "뭐", "무엇", "어떻게", "왜", "나요", "까요", "인가요", "는지", "건가요", "되나요",
)
CLARIFICATION_MARKERS = (
    "simpler", "simply", "plain terms", "what do you mean", "what does that mean", "can you explain", "not sure i follow",
    "lost me", "쉽게", "무슨 뜻", "무슨 말", "다시 설명", "이해가 안",
)
DETAIL_MARKERS = ("more detail", "how exactly", "technically", "under the hood", "in detail", "자세히", "구체적으로", "기술적으로")
APPLICATION_MARKERS = (
    "use case", "application", "in practice", "real world", "real-world", "how would i use", "who would use",
    "example", "어디에 쓰", "활용", "실제로", "적용", "쓸 수 있",
)
CHALLENGE_MARKERS = (
    "how do you know", "isn't that", "doesn't that", "but what if", "what if it fails", "i doubt", "not convinced",
    "limitation", "하지만", "그런데 만약", "한계", "의문",
)
AGREEMENT_MARKERS = ("makes sense", "got it", "i see", "that's clear", "that is clear", "understood", "이해했", "알겠", "그렇군요", "좋네요")
CLOSING_MARKERS = ("have to go", "nice talking", "thanks for explaining", "thank you for explaining", "see you", "감사합니다", "잘 들었", "가봐야")
GREETING_MARKERS = ("hi", "hello", "nice to meet", "안녕", "반갑")
MINIMAL_REPLIES = {"ok", "okay", "hmm", "sure", "right", "uh", "음", "네", "아"}
MISUNDERSTANDING_MARKERS = (
    ("answer for", "cues vs full answers"),
    ("reads the answer", "cues vs full answers"),
    ("speaks for", "cues vs full answers"),
    ("대신 답", "cues vs full answers"),
    ("대신 대답", "cues vs full answers"),
)
DOMAIN_TERMS: dict[str, str] = {
    "wearable": "wearable sensing",
    "웨어러블": "wearable sensing",
    "smart glasses": "smart glasses",
    "education": "education",
    "classroom": "education",
    "교육": "education",
    "robot": "robotics",
    "로봇": "robotics",
}
TECH_TERMS = (
    "embedding", "embeddings", "transformer", "retrieval", "vector search", "diarization", "fine-tuning", "rag",
    "language model", "user model", "bayesian", "pomdp", "임베딩", "트랜스포머", "검색 증강",
)

UNFAMILIAR_PATTERNS = [
    re.compile(r"(?:i'?m|i am) not (?:very |really )?familiar with (?:the )?([a-z0-9][\w\s-]{1,40}?)(?=[.,?!]|$)", re.I),
    re.compile(r"i don'?t know (?:much |anything )?about (?:the )?([a-z0-9][\w\s-]{1,40}?)(?=[.,?!]|$)", re.I),
    re.compile(r"(?:i'?m|i am) new to ([a-z0-9][\w\s-]{1,40}?)(?=[.,?!]|$)", re.I),
    re.compile(r"([가-힣A-Za-z0-9]{2,20})(?:은|는|에 대해서는|에 대해|을|를)?\s*잘 몰라"),
    re.compile(r"([가-힣A-Za-z0-9]{2,20})(?:은|는|에 대해서는|에 대해|을|를)?\s*잘 모르"),
]
FAMILIAR_PATTERNS = [
    re.compile(r"i(?:'ve| have) (?:used|worked with|built) (?:the )?([a-z0-9][\w\s-]{1,40}?)(?=[.,?!]| before|$)", re.I),
    re.compile(r"i know (?:the )?([a-z0-9][\w\s-]{1,40}?) (?:well|quite well)", re.I),
    re.compile(r"([가-힣A-Za-z0-9]{2,20})(?:은|는|을|를)?\s*(?:써 봤|사용해 봤|잘 알)"),
]
WORK_ON_PATTERNS = [
    re.compile(r"i (?:work on|research|study|do research on) ([a-z0-9][\w\s-]{1,40}?)(?=[.,?!]| so| and|$)", re.I),
    re.compile(r"저는\s*([가-힣A-Za-z0-9 ]{2,20}?)\s*(?:을|를)?\s*(?:연구|공부)"),
]


def find_first(text: str, markers: tuple[str, ...]) -> tuple[int, int] | None:
    low = text.lower()
    for marker in markers:
        idx = low.find(marker)
        if idx != -1:
            return idx, idx + len(marker)
    return None


def is_question(text: str) -> bool:
    low = text.lower().strip()
    return "?" in low or low.startswith(QUESTION_WORDS) or any(low.endswith(q) for q in ("나요", "까요", "가요"))


def detect_concern(text: str) -> tuple[str, tuple[int, int]] | None:
    low = text.lower()
    for key, terms in CONCERN_LEXICON.items():
        for term in terms:
            idx = low.find(term)
            if idx != -1:
                return key, (idx, idx + len(term))
    return None


def clean_term(term: str) -> str:
    term = re.sub(r"\s+", " ", term.strip().strip(".,?!")).lower()
    term = re.sub(r"^(the|a|an)\s+", "", term)
    return " ".join(term.split()[:5])
