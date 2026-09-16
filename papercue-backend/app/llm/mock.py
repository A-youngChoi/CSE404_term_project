"""DeterministicMockProvider: rule-based stand-in for the local LLM.

It implements the same `generate_structured` contract and its outputs pass through the
same Pydantic validation and deterministic checks as Ollama output. Every artefact it
produces is labelled `mock:deterministic-rules` so it is never mistaken for model output.
"""

from __future__ import annotations

import re

from app.core.text import first_sentence, top_keywords
from app.core.utils import jaccard, token_set
from app.llm import mock_rules as R
from app.llm.base import T
from app.prompts import get_prompt

_UNIT_TYPE_BY_HEADING = [
    (("limitation", "한계"), "limitation"),
    (("related work", "background", "gap", "관련 연구"), "research_gap"),
    (("introduction", "motivation", "서론", "동기"), "motivation"),
    (("problem", "문제"), "problem"),
    (("method", "system", "design", "architecture", "implementation", "방법", "시스템", "설계"), "method"),
    (("evaluation", "study", "experiment", "평가", "실험"), "evidence"),
    (("result", "finding", "결과"), "result"),
    (("contribution", "기여"), "contribution"),
    (("application", "use case", "scenario", "적용", "활용"), "application"),
    (("example", "예시"), "example"),
    (("discussion", "future", "논의"), "connection"),
    (("abstract", "overview", "idea", "요약", "개요"), "core_idea"),
]
_UNIT_TYPE_BY_TERM = [
    (("limitation", "cannot", "does not yet", "한계"), "limitation"),
    (("we propose", "we present", "제안"), "core_idea"),
    (("participants", "study", "evaluated", "참가자"), "evidence"),
    (("for example", "for instance", "예를 들어"), "example"),
]

_PHASE_BY_UNIT_TYPE = {
    "problem": "problem_framing",
    "motivation": "problem_framing",
    "research_gap": "problem_framing",
    "core_idea": "method_elaboration",
    "method": "method_elaboration",
    "evidence": "evidence_discussion",
    "result": "evidence_discussion",
    "contribution": "evidence_discussion",
    "application": "application_discussion",
    "example": "application_discussion",
    "connection": "connection",
    "limitation": "challenge",
}


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(ch, 0, start) for ch in ".!?\n")
    rights = [i for i in (text.find(ch, end) for ch in ".!?\n") if i != -1]
    right = min(rights) + 1 if rights else len(text)
    return text[left + 1 : right].strip() or text[start:end]


class DeterministicMockProvider:
    is_mock = True
    name = "mock:deterministic-rules"

    def status(self) -> dict:
        return {"available": True, "model_installed": True, "error_code": None, "simulated": True}

    def generate_structured(self, task: str, payload: dict, schema: type[T]) -> T:
        get_prompt(task)  # same task registry as the real provider
        handler = getattr(self, f"_task_{task}")
        return schema.model_validate(handler(payload))

    # ------------------------------------------------------------ paper units
    def _task_paper_unit_labeling(self, payload: dict) -> dict:
        labels = []
        for chunk in payload.get("chunks", []):
            heading = (chunk.get("heading") or "").lower()
            text = chunk.get("text", "")
            unit_type = "core_idea"
            for terms, utype in _UNIT_TYPE_BY_HEADING:
                if any(t in heading for t in terms):
                    unit_type = utype
                    break
            else:
                low = text.lower()
                for terms, utype in _UNIT_TYPE_BY_TERM:
                    if any(t in low for t in terms):
                        unit_type = utype
                        break
            title = chunk.get("heading") or " ".join(text.split()[:6])
            labels.append(
                {
                    "chunk_index": chunk["chunk_index"],
                    "unit_type": unit_type,
                    "title": title[:120],
                    "short_explanation": first_sentence(text, 25)[:300] or title,
                    "keywords": top_keywords(text, 5),
                }
            )
        return {"labels": labels}

    # ------------------------------------------------------ conversation state
    def _task_conversation_state(self, payload: dict) -> dict:
        prev = payload.get("previous_state") or {}
        turn = payload["latest_turn"]
        text: str = turn["text"]
        low = text.lower().strip()
        speaker = turn["speaker"]
        candidates = payload.get("candidate_topics") or []
        top = candidates[0] if candidates else None
        phase = prev.get("phase", "opening")
        topic = top["title"] if top else prev.get("topic")
        if top:
            phase = _PHASE_BY_UNIT_TYPE.get(top["unit_type"], phase)
        out: dict = {"phase": phase, "topic": topic}

        if speaker == "presenter":
            if prev.get("turn_count", 0) < 2 and R.find_first(low, R.GREETING_MARKERS):
                out["phase"] = "opening"
                act = "other"
            elif R.find_first(low, ("for example", "for instance", "예를 들어")):
                act = "example"
            elif R.find_first(low, R.CLOSING_MARKERS):
                act, out["phase"] = "closing", "closing"
            elif "?" in low:
                act = "question"
            elif prev.get("listener_act") in ("question", "clarification_request", "challenge"):
                act = "answer"
            else:
                act = "explanation"
            out["presenter_act"] = act
            return out

        words = re.findall(r"[^\W_]+", low)
        concern = R.detect_concern(text)
        question = R.is_question(text)
        if R.find_first(low, R.CLOSING_MARKERS):
            act = "closing"
            out["phase"] = "closing"
        elif R.find_first(low, R.CHALLENGE_MARKERS) and question:
            act = "challenge"
            out["phase"] = "challenge"
        elif R.find_first(low, R.CLARIFICATION_MARKERS):
            act = "clarification_request"
        elif any(p.search(text) for p in R.UNFAMILIAR_PATTERNS + R.FAMILIAR_PATTERNS + R.WORK_ON_PATTERNS) and not question:
            act = "self_report"
        elif question:
            act = "question"
        elif R.find_first(low, R.AGREEMENT_MARKERS):
            act = "agreement"
        elif len(words) <= 2:
            act = "backchannel"
        else:
            act = "statement"
        if prev.get("turn_count", 0) < 2 and R.find_first(low, R.GREETING_MARKERS) and act in ("statement", "backchannel"):
            out["phase"] = "opening"
        out["listener_act"] = act
        if act in ("question", "clarification_request", "challenge"):
            out["explicit_question"] = text[:300]
        if concern:
            out["detected_concern"] = concern[0]
            out["topic"] = concern[0]
            out["unresolved_issue"] = concern[0]
        elif act in ("question", "challenge") and topic:
            out["unresolved_issue"] = topic[:60]
        for marker, label in R.MISUNDERSTANDING_MARKERS:
            if marker in low:
                out["possible_misunderstanding"] = label
                break
        if act == "agreement":
            out["issue_resolved"] = True
        if R.find_first(low, R.APPLICATION_MARKERS) and not concern:
            out["phase"] = "application_discussion"
            out["topic"] = "practical applications"
        return out

    # --------------------------------------------------------------- evidence
    def _task_evidence_extraction(self, payload: dict) -> dict:
        turn = payload["latest_listener_turn"]
        text: str = turn["text"]
        low = text.lower()
        state = payload.get("conversation_state") or {}
        topic = state.get("topic")
        ev: list[dict] = []

        def add(dim, key, value, etype, span, observation):
            ev.append(
                {
                    "dimension": dim,
                    "key": key,
                    "value": value,
                    "evidence_type": etype,
                    "quote": _sentence_around(text, *span)[:200],
                    "observation": observation,
                }
            )

        question = R.is_question(text)
        for pat in R.UNFAMILIAR_PATTERNS:
            m = pat.search(text)
            if m:
                term = R.clean_term(m.group(1))
                add("familiarity", term, "unfamiliar", "explicit", m.span(), f"Listener said they are not familiar with {term}.")
                add("explanation_level", "preferred", "simple", "weak_inference", m.span(),
                    "Self-reported unfamiliarity suggests a simpler explanation may help.")
                break
        for pat in R.FAMILIAR_PATTERNS:
            m = pat.search(text)
            if m:
                term = R.clean_term(m.group(1))
                add("familiarity", term, "familiar", "explicit", m.span(), f"Listener said they have experience with {term}.")
                break
        for pat in R.WORK_ON_PATTERNS:
            m = pat.search(text)
            if m:
                term = R.clean_term(m.group(1))
                term = next((v for k, v in R.DOMAIN_TERMS.items() if k in term), term)
                add("connection", term, "relevant", "explicit", m.span(), f"Listener said they work on {term}.")
                break

        concern = R.detect_concern(text)
        if concern:
            key, span = concern
            etype = "explicit" if question else "behavioral"
            verb = "asked about" if question else "mentioned"
            add("concern", key, "raised", etype, span, f"Listener {verb} {key}-related issues.")
            add("unresolved_issue", key, "open", "behavioral", span, f"The {key} question has not been answered yet.")

        span = R.find_first(low, R.CLARIFICATION_MARKERS)
        if span:
            add("explanation_level", "preferred", "simple", "behavioral", span, "Listener asked for a simpler explanation.")
            if topic and not any(e["dimension"] == "familiarity" for e in ev):
                add("familiarity", R.clean_term(topic), "partial", "weak_inference", span,
                    "A clarification request may indicate partial familiarity with the current topic.")

        span = R.find_first(low, R.DETAIL_MARKERS)
        if span:
            add("explanation_level", "preferred", "technical", "behavioral", span, "Listener asked for more technical detail.")

        span = R.find_first(low, R.APPLICATION_MARKERS)
        if span and not concern:
            add("interest", "practical applications", "interested", "explicit" if question else "behavioral", span,
                "Listener asked about practical use.")
            add("goal", "evaluate applicability", "present", "weak_inference", span,
                "Listener may want to judge where the work applies.")

        if not any(e["dimension"] == "connection" for e in ev):
            for term, domain in R.DOMAIN_TERMS.items():
                idx = low.find(term)
                if idx != -1:
                    add("interest", domain, "interested", "behavioral", (idx, idx + len(term)), f"Listener brought up {domain}.")
                    break

        plain_use = not concern and not R.find_first(low, R.CLARIFICATION_MARKERS)
        if plain_use and not re.search(r"\bwhat(?:'s| is| are)\b", low):
            # Only technical vocabulary counts; everyday paper keywords ("server") do not signal familiarity.
            multiword = {t.lower() for t in payload.get("paper_terms", []) if " " in t.strip()}
            known = (multiword & {t for t in multiword if any(k in t for k in R.TECH_TERMS)}) | set(R.TECH_TERMS)
            for term in sorted(known, key=len, reverse=True):
                if len(term) >= 3 and re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", low):
                    if any(e["dimension"] == "familiarity" and e["key"] == term for e in ev):
                        break
                    idx = low.find(term)
                    add("familiarity", term, "familiar", "behavioral", (idx, idx + len(term)),
                        f"Listener used the term '{term}' in context.")
                    break

        span = R.find_first(low, R.AGREEMENT_MARKERS)
        if span:
            add("engagement", "overall", "high", "behavioral", span, "Listener signalled understanding.")
            for issue in state.get("unresolved_issues", []):
                if issue.get("status") in ("open", "addressed"):
                    add("unresolved_issue", issue["topic"], "resolved", "behavioral", span,
                        "Listener signalled the earlier issue is settled.")
                    break
        words = re.findall(r"[^\W_]+", low)
        if len(words) <= 2 and set(words) <= R.MINIMAL_REPLIES and words:
            add("engagement", "overall", "low", "weak_inference", (0, len(text)), "Listener gave a minimal reply.")
        return {"evidence": ev[:8]}

    # -------------------------------------------------------- audience update
    def _task_audience_update(self, payload: dict) -> dict:
        beliefs = payload.get("current_beliefs", [])
        updates = []
        for item in payload.get("new_evidence", []):
            key = item["key"]
            for b in beliefs:
                if b["dimension"] == item["dimension"] and b["key"] != key and jaccard(b["key"], key) >= 0.5:
                    key = b["key"]
                    break
            updates.append(
                {
                    "evidence_index": item["index"],
                    "dimension": item["dimension"],
                    "key": key,
                    "value": item["value"],
                    "reason": item["observation"][:160],
                }
            )
        return {"updates": updates}

    # ------------------------------------------------------------------- cue
    def _task_cue_generation(self, payload: dict) -> dict:
        action = payload["action"]
        target = (payload.get("target") or "").strip()
        lang = payload.get("language", "en")
        units = payload.get("units", [])
        evidence = payload.get("evidence", [])
        prefs = {k.lower(): v for k, v in (payload.get("preferred_terminology") or {}).items()}
        target_ko = R.KO_LABELS.get(target.lower(), target)
        target_tokens = token_set(target) | token_set(target_ko)

        def keyword() -> tuple[str | None, str | None]:
            for u in units:
                for kw in u.get("keywords", []):
                    if token_set(kw) & target_tokens:
                        continue
                    return prefs.get(kw.lower(), kw), u["id"]
            if units:
                return units[0]["title"], units[0]["id"]
            return None, None

        kw, unit_id = keyword()
        grounding = [unit_id] if unit_id else []
        ev_ids = [e["id"] for e in evidence if token_set(e["key"]) & target_tokens][:2] or [e["id"] for e in evidence][:1]
        confidence = 0.85 if grounding else 0.6
        misunderstanding = "answer" in target.lower()

        en = {
            "address_concern": f"{target} first—{kw}",
            "simplify": f"simplify {target or kw}—use an example",
            "elaborate": f"detail {kw}",
            "connect": f"connect to {target}",
            "reframe": f"reframe around {kw}",
            "recover": "clarify: cues, not answers" if misunderstanding else f"clarify {kw}",
            "acknowledge_limitation": f"acknowledge {kw} limitation",
            "emphasize": f"start with {target or kw}",
            "close": "summarize main contribution",
        }
        ko = {
            "address_concern": f"{target_ko} 먼저—{kw}",
            "simplify": f"{target_ko or kw}, 예시로 쉽게",
            "elaborate": f"{kw} 구체적으로",
            "connect": f"{target_ko} 연구와 연결",
            "reframe": f"{kw} 중심으로 다시 설명",
            "recover": "명확히: 답변 아닌 단서" if misunderstanding else f"{kw} 다시 명확히",
            "acknowledge_limitation": f"{kw} 한계 인정",
            "emphasize": f"{target_ko or kw} 먼저 강조",
            "close": "핵심 기여 요약",
        }
        verify_en = {"detail level": "check desired detail level", "practical applications": "ask which application matters"}
        verify_ko = {"detail level": "원하는 설명 수준 확인", "practical applications": "관심 있는 적용 분야 묻기"}
        if action == "verify":
            table = verify_ko if lang == "ko" else verify_en
            cue = table.get(target.lower(), "주요 우려 물어보기" if lang == "ko" else "ask their main concern")
            grounding, confidence = [], 0.6
        else:
            cue = (ko if lang == "ko" else en)[action]
            if action == "close":
                grounding = grounding[:1]
        if lang == "ko":
            rationale = f"'{target_ko or action}'에 맞춘 단서이며 논문 단위에 근거함." if grounding else "근거가 부족하여 확인 질문을 제안함."
        else:
            rationale = f"Targets '{target or action}' using the retrieved paper unit." if grounding else "Asks a verification question instead of guessing."
        return {
            "cue": cue,
            "action": action,
            "grounding_unit_ids": grounding,
            "audience_evidence_ids": ev_ids,
            "confidence": confidence,
            "rationale": rationale,
        }
