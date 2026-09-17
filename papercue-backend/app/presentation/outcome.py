"""Infers whether a delivered prompt helped, from later observations only (no presenter input).

Each pending prompt is checked on every following event until it resolves or its
observation window (`WINDOW_SECONDS` / `WINDOW_EVENTS`) closes. The rules per prompt type
are listed in `RULES_DOC` and shown in the dashboard.
"""

from __future__ import annotations

from typing import Any

from app.presentation.schemas import GeneratedPrompt, KnowledgeBase, ObservedEvent, PresentationContext
from app.presentation.textmatch import hits

WINDOW_SECONDS = 50.0
WINDOW_EVENTS = 3
RULES_DOC = {
    "content_reminder": "recovered when the target key point is mentioned",
    "next_point_suggestion": "recovered when the target key point is mentioned",
    "silence_recovery": "recovered when speech resumes (and the cue is mentioned, if it had one)",
    "pace_adjustment": "recovered when the rate returns within 15% of typical; partial when it moves toward typical",
    "repetition_alert": "recovered when fillers drop to the typical level without the repeated phrase",
    "clarification_suggestion": "recovered when audience confusion falls to 0.4 or below; partial when it drops",
    "question_reinterpretation": "recovered when the answer contains the expected answer keywords",
    "answer_structure": "recovered when the answer contains the expected answer keywords",
    "time_management": "recovered when the presenter advances a slide or the time budget improves",
    "wrap_up": "recovered when the presenter reaches the final slide",
}


def _kp_keywords(kb: KnowledgeBase, target: str | None) -> list[str]:
    found = kb.key_point(target) if target else None
    return found[1].keywords if found else []


def evaluate(prompt: GeneratedPrompt, history: list[ObservedEvent], ctx: PresentationContext, kb: KnowledgeBase,
             baseline_rate: float, baseline_filler: float, trigger: ObservedEvent) -> tuple[str, dict[str, Any]] | None:
    """Return (outcome, detail) once decided, else None (still pending)."""
    after = [e for e in history if e.elapsed_time > prompt.created_elapsed]
    if not after:
        return None
    latest = after[-1]
    ptype = prompt.prompt_type
    text = latest.transcript_chunk or ""
    detail: dict[str, Any] = {"event_id": latest.event_id, "rule": RULES_DOC.get(ptype, ""),
                              "events_observed": [e.event_id for e in after]}
    first = after[0]
    if len(after) == 1 and prompt.issue_type not in ("long_silence", "filler_repetition"):
        disrupted = first.silence_duration >= 3 or first.filler_count >= max(3, baseline_filler + 2)
        detail["possible_disruption"] = bool(disrupted)
    outcome: str | None = None

    if ptype in ("content_reminder", "next_point_suggestion", "silence_recovery"):
        keywords = _kp_keywords(kb, prompt.target)
        if keywords and hits(text, keywords):
            outcome, detail["reason"] = "recovered", f"target {prompt.target} mentioned"
        elif not keywords and ptype == "silence_recovery" and text and latest.silence_duration < 2:
            outcome, detail["reason"] = "recovered", "speech resumed"
        elif ptype == "silence_recovery" and text and latest.silence_duration < 2:
            detail["partial"] = "speech resumed without the cue"
    elif ptype == "pace_adjustment":
        if latest.speech_rate > 0:
            ratio = latest.speech_rate / baseline_rate
            trigger_ratio = trigger.speech_rate / baseline_rate if trigger.speech_rate else 1.0
            detail["ratio_before"], detail["ratio_after"] = round(trigger_ratio, 3), round(ratio, 3)
            if abs(ratio - 1) <= 0.15:
                outcome, detail["reason"] = "recovered", "rate back in the typical range"
            elif abs(ratio - 1) < abs(trigger_ratio - 1) - 0.05:
                detail["partial"] = "rate moved toward typical"
    elif ptype == "repetition_alert":
        phrase = (prompt.context_used.get("slots") or {}).get("phrase")
        repeated = latest.repeated_phrase is not None and latest.repeated_phrase.text == phrase
        if latest.filler_count <= max(1.0, 1.5 * baseline_filler) and not repeated:
            outcome, detail["reason"] = "recovered", f"{latest.filler_count} filler(s), no repeated phrase"
        elif latest.filler_count < trigger.filler_count:
            detail["partial"] = "fewer fillers"
    elif ptype == "clarification_suggestion":
        c = latest.audience_signal.confusion
        detail["confusion_before"], detail["confusion_after"] = trigger.audience_signal.confusion, c
        if c <= 0.4:
            outcome, detail["reason"] = "recovered", "audience confusion dropped"
        elif c < trigger.audience_signal.confusion - 0.1:
            detail["partial"] = "confusion decreasing"
    elif ptype in ("question_reinterpretation", "answer_structure"):
        chunk = kb.chunk(prompt.target) if prompt.target else None
        if latest.event_type == "qa_answer" and chunk and hits(text, chunk.answer_keywords):
            outcome, detail["reason"] = "recovered", "answer now addresses the question"
    elif ptype == "time_management":
        if latest.current_slide > trigger.current_slide:
            outcome, detail["reason"] = "recovered", f"advanced to slide {latest.current_slide}"
        elif ctx.time_budget_ratio > 0.85:
            outcome, detail["reason"] = "recovered", "time budget restored"
    elif ptype == "wrap_up":
        last = max(s.slide for s in kb.slides if not s.is_qa)
        if latest.current_slide >= last:
            outcome, detail["reason"] = "recovered", "reached the conclusion slide"

    # Partial progress and a possible disruption seen on earlier events stay on the prompt.
    for key in ("partial", "possible_disruption"):
        if detail.get(key):
            prompt.outcome_detail[key] = detail[key]
        elif prompt.outcome_detail.get(key):
            detail[key] = prompt.outcome_detail[key]
    if outcome:
        return outcome, detail
    window_closed = (latest.elapsed_time - prompt.created_elapsed >= WINDOW_SECONDS) or len(after) >= WINDOW_EVENTS
    if window_closed:
        partial = detail.get("partial")
        detail["reason"] = partial or "no change within the observation window"
        return ("partially_recovered" if partial else "not_recovered"), detail
    return None
