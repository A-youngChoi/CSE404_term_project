"""Mobile prompt generation.

Runs only after the Judge chose INTERVENE_NOW (or, for inspection, to show what a suppressed
decision would have shown). Templates are filled with short cues from the knowledge base;
three candidates are scored against the Judge's recommendation and the user model, and the
best one is selected. An optional local LLM may reword the selected candidate; its output is
validated for length and language and falls back to the template otherwise.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.utils import word_count
from app.presentation.ids import IdFactory
from app.presentation.schemas import (
    GeneratedPrompt, Issue, JudgeDecision, KnowledgeBase, ObservedEvent, PresentationContext, PromptCandidate,
)
from app.presentation.textmatch import has_hangul
from app.presentation.user_model import PresenterUserModel

log = get_logger("presentation.prompts")

MAX_WORDS = 12
MAX_CHARS = 48
DISPLAY_SECONDS = {"short": 8.0, "medium": 12.0}

TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "content_reminder": {"ko": ("빠뜨림: {cue}", "앞 슬라이드 핵심 보충: {cue}"),
                         "en": ("Missed: {cue}", "Add before moving on: {cue}")},
    "next_point_suggestion": {"ko": ("다음: {cue}", "이어서 {cue} 설명하기"),
                              "en": ("Next: {cue}", "Continue with {cue}")},
    "time_management": {"ko": ("시간 부족 · {slide}로", "남은 {remaining}초 · 곁가지 줄이고 {slide}로"),
                        "en": ("Short on time · go to {slide}", "{remaining}s left · cut the aside, go to {slide}")},
    "pace_adjustment:fast": {"ko": ("천천히 · 한 호흡", "속도 줄이기 · {cue} 강조"),
                             "en": ("Slow down · breathe", "Slow down · stress {cue}")},
    "pace_adjustment:slow": {"ko": ("속도 올리기 · {slide}로", "조금 빠르게 · {slide}로 넘어가기"),
                             "en": ("Pick up pace · {slide} next", "Speed up a bit · move to {slide}")},
    "repetition_alert": {"ko": ("‘{phrase}’ 반복 · 잠깐 멈춤", "‘{phrase}’ 대신 짧게 멈추기"),
                         "en": ("Repeating ‘{phrase}’ · pause instead", "Swap ‘{phrase}’ for a short pause")},
    "silence_recovery": {"ko": ("다음: {cue}", "막히면 여기서: {cue}"),
                         "en": ("Next: {cue}", "Pick up here: {cue}")},
    "clarification_suggestion": {"ko": ("{term} = {gloss}", "쉽게 풀기: {term}은 {gloss}"),
                                 "en": ("Define {term}: {gloss}", "Explain simply: {term} = {gloss}")},
    "clarification_suggestion:generic": {"ko": ("예시 하나로 쉽게", "청중 혼란 · 예시 하나로 다시 설명"),
                                         "en": ("Give one concrete example", "Audience looks lost · re-explain with an example")},
    "question_reinterpretation": {"ko": ("질문 핵심: {topic}", "질문은 {topic} · 답: {cue}"),
                                  "en": ("They asked about {topic}", "Question is about {topic} · {cue}")},
    "answer_structure": {"ko": ("결론 먼저 · 근거 하나", "한 문장 결론 → 근거 1개 → 끝"),
                         "en": ("Answer first · one reason", "One-line answer → one reason → stop")},
    "wrap_up": {"ko": ("마무리: {cue}", "지금 결론으로 · {cue}"),
                "en": ("Wrap up: {cue}", "Go to your conclusion · {cue}")},
}
ALTERNATIVE_TYPE = {
    "content_reminder": "next_point_suggestion", "next_point_suggestion": "content_reminder",
    "silence_recovery": "next_point_suggestion", "time_management": "wrap_up", "wrap_up": "time_management",
    "pace_adjustment": "time_management", "repetition_alert": "pace_adjustment",
    "clarification_suggestion": "next_point_suggestion", "question_reinterpretation": "answer_structure",
    "answer_structure": "question_reinterpretation",
}
PURPOSE = {
    "content_reminder": "Recover an essential point that was skipped.",
    "next_point_suggestion": "Point to the next thing to say.",
    "time_management": "Bring the talk back within the time budget.",
    "pace_adjustment": "Bring the speaking pace back to the presenter's typical range.",
    "repetition_alert": "Reduce repeated fillers or phrases.",
    "silence_recovery": "Give a concrete cue to resume after a blank.",
    "clarification_suggestion": "Help the audience with an unfamiliar term.",
    "question_reinterpretation": "Redirect the answer to what was actually asked.",
    "answer_structure": "Give the answer a short structure.",
    "wrap_up": "Move to the conclusion before time runs out.",
}
FILLER_WORD = {"ko": "어", "en": "um"}


class LLMPromptOutput(BaseModel):
    text: str = Field(max_length=120)


def _slots(issue: Issue | None, decision: JudgeDecision, ctx: PresentationContext, kb: KnowledgeBase,
           event: ObservedEvent) -> tuple[dict[str, str], list[str]]:
    lang = event.language
    slots: dict[str, str] = {"remaining": str(int(event.remaining_time))}
    used: list[str] = []
    next_slide = kb.slide(ctx.current_slide + 1)
    last_slide = max((s for s in kb.slides if not s.is_qa), key=lambda s: s.slide)
    slots["slide"] = (next_slide.title if next_slide and not next_slide.is_qa else last_slide.title)
    target = issue.target if issue else None
    if target and (found := kb.key_point(target)):
        slots["cue"] = found[1].cue
        used.append(f"mm_{target}")
    elif target and (chunk := kb.chunk(target)):
        slots["cue"] = chunk.cue or chunk.title
        used.append(chunk.chunk_id)
        if chunk.category == "glossary":
            slots["term"], slots["gloss"] = chunk.term or "", chunk.gloss or ""
        if chunk.category == "expected_qa":
            slots["topic"] = chunk.topic or ""
    if "cue" not in slots:
        open_kp = next((kp for kp in ctx.expected_content if not kp["covered"]), None)
        if open_kp:
            slots["cue"] = open_kp["cue"]
            used.append(f"mm_{open_kp['kp_id']}")
        else:
            slots["cue"] = slots["slide"]
    if "term" not in slots:
        term = next((c for c in kb.chunks if c.chunk_id in decision.used_knowledge_ids and c.category == "glossary"), None)
        slots["term"] = term.term if term else ctx.slide_title
        slots["gloss"] = term.gloss if term else ""  # empty -> generic clarification template
    slots.setdefault("topic", (ctx.active_question or {}).get("topic") or ctx.slide_title)
    phrase = (ctx.repeated_phrase or {}).get("text")
    slots["phrase"] = phrase or FILLER_WORD[lang]
    return slots, used


def _template_key(ptype: str, issue: Issue | None, slots: dict[str, str]) -> str:
    if ptype == "clarification_suggestion" and not slots.get("gloss"):
        return "clarification_suggestion:generic"
    if ptype == "pace_adjustment":
        return "pace_adjustment:slow" if issue and issue.type == "pace_too_slow" else "pace_adjustment:fast"
    return ptype


class PromptGenerator:
    def __init__(self, ids: IdFactory, provider=None):
        self.ids = ids
        self.provider = provider
        self.name = f"llm:{provider.name}" if provider else "template"

    def generate(self, *, decision: JudgeDecision, issue: Issue | None, ctx: PresentationContext, kb: KnowledgeBase,
                 event: ObservedEvent, user_model: PresenterUserModel, step: int) -> GeneratedPrompt:
        lang = event.language
        rec_type = decision.recommended_prompt_type or "next_point_suggestion"
        pref_len = user_model.value("preferred_prompt_length", "short")
        rec_len = decision.recommended_prompt_length or pref_len
        slots, kn_used = _slots(issue, decision, ctx, kb, event)
        alt_type = ALTERNATIVE_TYPE.get(rec_type, "next_point_suggestion")
        specs = [(rec_type, "short"), (rec_type, "medium"), (alt_type, rec_len)]
        candidates: list[PromptCandidate] = []
        for ptype, length in specs:
            template = TEMPLATES[_template_key(ptype, issue, slots)][lang][0 if length == "short" else 1]
            text = template.format(**slots)
            grounded = 1.0 if any(f"{{{s}}}" in template for s in ("cue", "term", "topic")) and kn_used else 0.5
            parts = {
                "type_match": 0.5 * (1.0 if ptype == rec_type else 0.4),
                "length_match": 0.3 * (1.0 if length == rec_len else 0.3),
                "grounded": 0.2 * grounded,
                "brevity": -0.02 * max(0, word_count(text) - 6),
            }
            candidates.append(PromptCandidate(candidate_id=self.ids.next("cand"), text=text, prompt_type=ptype,
                                              length=length, score=round(sum(parts.values()), 3),
                                              score_parts={k: round(v, 3) for k, v in parts.items()}))
        best = max(candidates, key=lambda c: (c.score, c.prompt_type == rec_type))
        for c in candidates:
            c.selected = c is best
            if not c.selected:
                why = []
                if c.prompt_type != rec_type:
                    why.append(f"type {c.prompt_type} differs from the Judge's recommendation {rec_type}")
                if c.length != rec_len:
                    why.append(f"length {c.length} differs from the preferred {rec_len}")
                c.not_selected_reason = f"score {c.score:.2f} < {best.score:.2f}" + (": " + "; ".join(why) if why else "")

        text, generator, fallback = best.text, "template", False
        if self.provider is not None:
            text, generator, fallback = self._reword(best, lang, event, ctx, slots)

        priority_score = round(0.6 * decision.severity + 0.4 * decision.urgency, 3)
        priority = "high" if priority_score >= 0.7 else "medium" if priority_score >= 0.45 else "low"
        display = DISPLAY_SECONDS[best.length] + (2.0 if priority == "high" else 0.0)
        load = float(user_model.value("estimated_cognitive_load", 0.0))
        personalization = [
            {"attribute": "preferred_language", "value": user_model.value("preferred_language"),
             "confidence": user_model.confidence("preferred_language"), "effect": f"{lang} template set"},
            {"attribute": "preferred_prompt_length", "value": pref_len,
             "confidence": user_model.confidence("preferred_prompt_length"),
             "effect": f"length_match favours {rec_len}"},
            {"attribute": "estimated_cognitive_load", "value": round(load, 2),
             "confidence": user_model.confidence("estimated_cognitive_load"),
             "effect": "forced short form" if rec_len == "short" and pref_len != "short" else "no change"},
            {"attribute": "expertise_level", "value": user_model.value("expertise_level"),
             "confidence": user_model.confidence("expertise_level"),
             "effect": f"cooldown {decision.cooldown_seconds:.0f}s"},
        ]
        return GeneratedPrompt(
            prompt_id=self.ids.next("prm"), decision_id=decision.decision_id, step=step, event_id=event.event_id,
            created_elapsed=event.elapsed_time, text=text, language=lang, prompt_type=best.prompt_type,
            length=best.length, priority=priority, priority_score=priority_score, urgency=decision.urgency,
            expires_at_elapsed=event.elapsed_time + display, display_seconds=display,
            purpose=PURPOSE.get(best.prompt_type, ""),
            selection_reason=(f"Highest candidate score {best.score:.2f} "
                              f"(type {best.prompt_type}, {best.length}, preferred length {pref_len})."),
            context_used={"slide": ctx.current_slide, "slide_title": ctx.slide_title, "issue_id": decision.issue_id,
                          "issue": decision.detected_issue, "target": decision.target, "slots": slots,
                          "evidence_event_ids": decision.evidence_event_ids, "memory_ids": decision.used_memory_ids,
                          "cooldown_seconds": decision.cooldown_seconds},
            personalization=personalization,
            knowledge_ids=sorted(set(kn_used) | set(decision.used_knowledge_ids)),
            alternatives=candidates, generator=generator, fallback_used=fallback,
            issue_id=decision.issue_id, issue_type=decision.detected_issue, target=decision.target,
        )

    def _reword(self, best: PromptCandidate, lang: str, event: ObservedEvent, ctx: PresentationContext,
                slots: dict[str, str]) -> tuple[str, str, bool]:
        t0 = time.perf_counter()
        try:
            out = self.provider.generate_structured("presentation_prompt", {
                "language": lang, "prompt_type": best.prompt_type, "length": best.length,
                "template_text": best.text, "slots": slots, "slide_title": ctx.slide_title,
                "latest_transcript": event.transcript_chunk,
            }, LLMPromptOutput)
            text = out.text.strip()
            if not text or word_count(text) > MAX_WORDS or len(text) > MAX_CHARS or has_hangul(text) != (lang == "ko"):
                raise ValueError("prompt failed length/language validation")
            log.info("prompt reworded by local model in %.0f ms", (time.perf_counter() - t0) * 1000)
            return text, self.name, False
        except Exception as exc:  # noqa: BLE001 - template fallback keeps the simulation running
            log.warning("prompt rewording failed (%s); using template", type(exc).__name__)
            return best.text, "template", True
