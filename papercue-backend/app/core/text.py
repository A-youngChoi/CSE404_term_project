"""Deterministic text helpers (keyword extraction, sentence splitting, chunking)."""

from __future__ import annotations

import re
from collections import Counter

from app.core.utils import words

STOPWORDS = set(
    """a an the and or but if then than so to of in on for with without by from as at into onto over under is are
    was were be been being it its this that these those there here we our us i you your they them their he she his
    her not no can could should would will may might must do does did done has have had having such via using use
    used also more most less very much many each other which who whom what when where why how about between within
    across while both either neither all any some one two three first second new based paper system systems approach
    our study work results show shows shown provide provides""".split()
)
# Common Korean particles/endings stripped from the end of a word for keyword matching.
_KO_SUFFIXES = ("에서는", "에서", "으로", "에게", "이다", "한다", "하는", "했다", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도")

_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def strip_ko_suffix(word: str) -> str:
    for suf in _KO_SUFFIXES:
        if len(word) > len(suf) + 1 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def content_words(text: str) -> list[str]:
    out = []
    for w in words(text):
        lw = strip_ko_suffix(w.lower())
        if len(lw) < 2 or lw in STOPWORDS or lw.isdigit():
            continue
        out.append(lw)
    return out


def top_keywords(text: str, limit: int = 5) -> list[str]:
    """Frequent content bigrams first, then unigrams."""
    toks = content_words(text)
    bigrams = Counter(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    unigrams = Counter(toks)
    picked: list[str] = []
    for phrase, count in bigrams.most_common():
        if count < 2 or len(picked) >= limit // 2:
            break
        picked.append(phrase)
    for word, _ in unigrams.most_common():
        if len(picked) >= limit:
            break
        if not any(word in p.split() for p in picked):
            picked.append(word)
    return picked


def first_sentence(text: str, max_words: int = 25) -> str:
    sentence = _SENTENCE_RE.split((text or "").strip(), maxsplit=1)[0]
    toks = sentence.split()
    if len(toks) > max_words:
        return " ".join(toks[:max_words]) + " …"
    return sentence


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split Markdown/plain text into (heading, body) sections."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in (text or "").splitlines():
        m = _HEADING_RE.match(line)
        if m:
            sections.append((m.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    out = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            out.append((heading, body))
    return out


def chunk_paragraphs(body: str, max_words: int = 120) -> list[str]:
    """Group paragraphs into chunks of at most ~max_words words (a long paragraph stays whole)."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for para in paragraphs:
        n = len(para.split())
        if current and count + n > max_words:
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(para)
        count += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def contains_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive phrase containment that respects word boundaries for Latin text."""
    if not phrase:
        return False
    t, p = text.lower(), phrase.lower().strip()
    if re.search(r"[a-z0-9]", p):
        return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", t) is not None
    return p in t
