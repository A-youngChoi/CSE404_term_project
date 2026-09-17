"""Intervention Judge.

The Judge first decides *whether* to intervene; prompt wording is a separate step.

* `RuleBasedJudge` (mock, default) - transparent weighted utility plus recency policy.
* `OllamaJudge` - a local LLM receives the same observable features and returns a structured
  decision; the recency policy is still enforced in code, and any failure falls back to the
  rule-based Judge (recorded as `fallback_used`).

Both return a `JudgeDecision`: evidence IDs, used memory/knowledge/user-model attributes,
supporting and counter reasons, scores with their evidence links, and the final reason.
No chain-of-thought is requested, stored or shown.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.utils import clamp
from app.presentation.schemas import (
    DECISIONS, GeneratedPrompt, Issue, JudgeDecision, KnowledgeRetrieval, MemoryItem, MemoryRetrieval,
    ObservedEvent, PresentationContext, ScoreComponent,
)
from app.presentation.user_model import PresenterUserModel

log = get_logger("presentation.judge")

WEIGHTS = {
    "severity": 0.28, "urgency": 0.22, "essentialness": 0.14, "knowledge_support": 0.10, "persistence": 0.08,
    "expected_benefit": 0.10, "self_recovery_likelihood": -0.25, "distraction_risk": -0.20,
}
T_INTERVENE = 0.36
T_WAIT = 0.12
MIN_CONFIDENCE = 0.5
COOLDOWN_SHORT = 30.0
COOLDOWN_MEDIUM = 40.0
REDUNDANCY_WINDOW = 90.0
OVERRIDE_SEVERITY = 0.85
FINAL_SECONDS = 30.0

PROMPT_FOR_ISSUE = {
    "missing_key_point": "content_reminder",
    "content_at_risk": "next_point_suggestion",
    "pace_too_fast": "pace_adjustment",
    "pace_too_slow": "pace_adjustment",
    "filler_repetition": "repetition_alert",
    "long_silence": "silence_recovery",
    "audience_confusion": "clarification_suggestion",
    "low_engagement": "next_point_suggestion",
    "qa_misunderstanding": "question_reinterpretation",
    "time_pressure": "time_management",
}
BASE_SELF_RECOVERY = {
    "missing_key_point": 0.2, "content_at_risk": 0.55, "pace_too_fast": 0.35, "pace_too_slow": 0.35,
    "filler_repetition": 0.45, "long_silence": 0.25, "audience_confusion": 0.4, "low_engagement": 0.5,
    "qa_misunderstanding": 0.2, "time_pressure": 0.2,
}
UM_ATTRIBUTE_FOR_ISSUE = {
    "pace_too_fast": "baseline_speech_rate", "pace_too_slow": "baseline_speech_rate",
    "filler_repetition": "baseline_filler_rate", "long_silence": "tension_pattern",
    "missing_key_point": "often_missed_content", "content_at_risk": "often_missed_content",
}


@dataclass
class JudgeInput:
    decision_id: str
    step: int
    event: ObservedEvent
    ctx: PresentationContext
    user_model: PresenterUserModel
    memory_retrieval: MemoryRetrieval | None
    memories: dict[str, MemoryItem]
    knowledge_retrieval: KnowledgeRetrieval | None
    used_knowledge_ids: list[str]
    knowledge_support: float
    prompts: list[GeneratedPrompt]  # delivered prompts so far
    issue_age: dict[str, int]
    top_issue: Issue | None
    considered: list[dict[str, Any]] = field(default_factory=list)


class Judge(Protocol):
    name: str
    is_mock: bool

    def decide(self, inp: JudgeInput) -> JudgeDecision: ...


# --------------------------------------------------------------------------- shared policy helpers
def wrap_needed(issue: Issue | None) -> bool:
    return bool(issue and issue.type == "time_pressure" and issue.signals.get("wrap_up_needed"))


def recommended_type(issue: Issue | None) -> str | None:
    if issue is None:
        return None
    if wrap_needed(issue):
        return "wrap_up"
    if issue.type == "qa_misunderstanding" and issue.signals.get("mode") == "unfocused":
        return "answer_structure"
    return PROMPT_FOR_ISSUE.get(issue.type)


def rank_issues(ctx: PresentationContext, prompts: list[GeneratedPrompt]) -> tuple[Issue | None, list[dict[str, Any]]]:
    """Order active issues by priority; issues that were already prompted are discounted."""
    considered = []
    for issue in ctx.active_issues:
        priority = 0.6 * issue.severity + 0.4 * issue.urgency
        prompted = any(p.issue_id == issue.issue_id or (p.target and p.target == issue.target and p.issue_type == issue.type)
                       for p in prompts)
        escalate = wrap_needed(issue) and not any(p.prompt_type == "wrap_up" for p in prompts)
        if prompted and not escalate:
            priority *= 0.6
        considered.append({"issue_id": issue.issue_id, "type": issue.type, "target": issue.target,
                           "severity": round(issue.severity, 3), "urgency": round(issue.urgency, 3),
                           "priority": round(priority, 3), "already_prompted": prompted})
    considered.sort(key=lambda c: (-c["priority"], c["issue_id"]))
    if not considered:
        return None, []
    top_id = considered[0]["issue_id"]
    return next(i for i in ctx.active_issues if i.issue_id == top_id), considered


def recency_policy(inp: JudgeInput, issue: Issue, utility: float) -> tuple[str | None, list[str], dict[str, Any]]:
    """Returns (forced decision or None, reasons, cooldown state)."""
    elapsed = inp.event.elapsed_time
    last = inp.prompts[-1] if inp.prompts else None
    state: dict[str, Any] = {"last_prompt_id": None, "seconds_since_last": None, "cooldown_seconds": None,
                             "in_cooldown": False, "redundant_with": None}
    if last is None:
        return None, [], state
    since = elapsed - last.created_elapsed
    cooldown = float(last.context_used.get("cooldown_seconds", COOLDOWN_SHORT))
    state.update(last_prompt_id=last.prompt_id, seconds_since_last=round(since, 1), cooldown_seconds=cooldown,
                 in_cooldown=since < cooldown)
    if utility < T_WAIT:
        return None, [], state
    if since < cooldown:
        override = issue.severity >= OVERRIDE_SEVERITY and issue.type != last.issue_type and utility >= T_INTERVENE + 0.1
        if not override:
            return ("SUPPRESS_DUE_TO_RECENT_INTERVENTION",
                    [f"Prompt {last.prompt_id} was shown {since:.0f}s ago (cooldown {cooldown:.0f}s)."], state)
        return None, [f"Cooldown overridden: severe, different issue ({issue.type})."], state
    for p in reversed(inp.prompts):
        if elapsed - p.created_elapsed > REDUNDANCY_WINDOW:
            break
        same = p.issue_type == issue.type or (p.target and p.target == issue.target)
        if not same:
            continue
        state["redundant_with"] = p.prompt_id
        if wrap_needed(issue) and p.prompt_type != "wrap_up":
            return None, [f"Escalating from {p.prompt_type} to wrap_up: the talk is about to overrun."], state
        if p.outcome == "not_recovered" and issue.severity >= 0.7:
            return None, [f"{p.prompt_id} did not help and the issue is still severe; trying again."], state
        return ("SUPPRESS_DUE_TO_RECENT_INTERVENTION",
                [f"Same issue was already prompted {elapsed - p.created_elapsed:.0f}s ago ({p.prompt_id}, "
                 f"outcome: {p.outcome})."], state)
    return None, [], state


# --------------------------------------------------------------------------- rule-based judge
class RuleBasedJudge:
    name = "mock:rule-based-judge"
    is_mock = True

    def __init__(self, seed: int = 0, noise: float = 0.0):
        self.seed = seed
        self.noise = noise

    def features(self, inp: JudgeInput) -> tuple[list[ScoreComponent], dict[str, Any]]:
        issue = inp.top_issue
        assert issue is not None
        um = inp.user_model
        ev = inp.event
        age = inp.issue_age.get(issue.issue_id, 1)
        persistence = min(1.0, (age - 1) / 2)
        level = um.value("expertise_level")
        load = float(um.value("estimated_cognitive_load", 0.3))
        mem_ids = [h.id for h in (inp.memory_retrieval.hits if inp.memory_retrieval else []) if h.selected]
        issue_ref = [issue.issue_id, *issue.evidence_event_ids]

        # essentialness
        ess = {"missing_key_point": 1.0 if issue.essential else 0.6, "content_at_risk": 1.0,
               "qa_misunderstanding": 1.0, "long_silence": 0.9 if issue.essential else 0.5,
               "time_pressure": 0.9 if wrap_needed(issue) else 0.7, "audience_confusion": 0.5,
               "pace_too_fast": 0.4, "pace_too_slow": 0.4, "filler_repetition": 0.3, "low_engagement": 0.3}[issue.type]
        ess_note = "essential content" if issue.essential else f"{issue.type} default"
        if issue.signals.get("deliberate_skip"):
            ess, ess_note = 0.3, "skipped deliberately (time pressure or explicit skip)"

        # self-recovery likelihood
        rec = BASE_SELF_RECOVERY[issue.type]
        rec_notes = [f"base {rec:.2f} for {issue.type}"]
        rec_refs: list[str] = []
        if issue.type == "long_silence" and issue.signals.get("silence_s", 0) < 6:
            rec = 0.7
            rec_notes = ["base 0.70 for a short pause"]
        if level == "expert":
            rec += 0.15
            rec_notes.append("+0.15 expert presenter")
            rec_refs.append("um:expertise_level")
        elif level == "novice":
            rec -= 0.1
            rec_notes.append("-0.10 novice presenter")
            rec_refs.append("um:expertise_level")
        if persistence >= 0.5:
            rec -= 0.1
            rec_notes.append("-0.10 issue persisted")
        elif issue.type not in ("missing_key_point", "qa_misunderstanding", "time_pressure", "long_silence"):
            rec += 0.1
            rec_notes.append("+0.10 first observation; may self-correct")
        if issue.type == "long_silence":
            if ev.presenter_state.arousal < 0.45:
                rec += 0.1
                rec_notes.append("+0.10 presenter calm (mock wearable)")
            if ev.audience_signal.attention >= 0.8:
                rec += 0.1
                rec_notes.append("+0.10 audience attentive")
            if um.value("tension_pattern") == "deliberate_pauses":
                rec += 0.1
                rec_notes.append("+0.10 known deliberate pauses")
                rec_refs.append("um:tension_pattern")
        if issue.signals.get("deliberate_skip"):
            rec += 0.3
            rec_notes.append("+0.30 skip looked intentional")
        rec = clamp(rec, 0.05, 0.95)

        # distraction risk
        dist = 0.15 + 0.25 * load
        dist_notes = [f"0.15 + 0.25 x cognitive load {load:.2f}"]
        dist_refs = ["um:estimated_cognitive_load"]
        if ev.event_type in ("audience_question", "qa_answer"):
            dist += 0.15
            dist_notes.append("+0.15 presenter is answering a question")
            if "qa_prompts" in (um.value("disruptive_contexts") or []):
                dist += 0.15
                dist_notes.append("+0.15 Q&A prompts were distracting before")
                dist_refs.append("um:disruptive_contexts")
        if ev.remaining_time < FINAL_SECONDS and issue.type != "time_pressure":
            dist += 0.3
            dist_notes.append("+0.30 final seconds of the talk")
        dist = clamp(dist)

        # expected benefit from past outcomes (user model + memories)
        resp = float(um.value("prompt_responsiveness", 0.5))
        mem_signal, mem_refs = 0.5, []
        for mid in mem_ids:
            m = inp.memories.get(mid)
            if m is None or issue.type not in m.tags:
                continue
            if "effective" in m.tags or m.data.get("outcome") in ("recovered", "partially_recovered"):
                mem_signal, mem_refs = 0.9, [mid]
            elif "ineffective" in m.tags or m.data.get("outcome") == "not_recovered":
                mem_signal, mem_refs = 0.2, [mid]
        benefit = 0.5 * resp + 0.5 * mem_signal

        ks = inp.knowledge_support
        comps = [
            ("severity", issue.severity, issue_ref, issue.description),
            ("urgency", issue.urgency, issue_ref, "; ".join(f"{k}={v}" for k, v in list(issue.signals.items())[:4])),
            ("essentialness", ess, [issue.target or issue.issue_id], ess_note),
            ("knowledge_support", ks, inp.used_knowledge_ids,
             "not required for this issue type" if ks == 0.5 else "max score of used knowledge chunks"),
            ("persistence", persistence, [issue.issue_id], f"active for {age} event(s)"),
            ("expected_benefit", benefit, ["um:prompt_responsiveness", *mem_refs],
             f"0.5 x responsiveness {resp:.2f} + 0.5 x memory signal {mem_signal:.2f}"),
            ("self_recovery_likelihood", rec, rec_refs, "; ".join(rec_notes)),
            ("distraction_risk", dist, dist_refs, "; ".join(dist_notes)),
        ]
        components = [ScoreComponent(name=n, value=round(v, 3), weight=WEIGHTS[n], contribution=round(WEIGHTS[n] * v, 4),
                                     evidence_refs=[r for r in refs if r], explanation=note)
                      for n, v, refs, note in comps]
        um_attr = UM_ATTRIBUTE_FOR_ISSUE.get(issue.type, "expertise_level")
        det = issue.detector_confidence
        evidence_norm = min(1.0, len(issue.evidence_event_ids) / 3)
        confidence = clamp(0.35 * det + 0.2 * evidence_norm + 0.25 * ks + 0.2 * um.confidence(um_attr))
        extra = {"confidence": round(confidence, 3), "memory_refs": mem_refs, "um_attr": um_attr,
                 "confidence_parts": {"detector": det, "evidence": round(evidence_norm, 2), "knowledge": ks,
                                      f"user_model:{um_attr}": um.confidence(um_attr)},
                 "rec_notes": rec_notes, "dist_notes": dist_notes, "benefit": benefit}
        return components, extra

    def decide(self, inp: JudgeInput) -> JudgeDecision:
        t0 = time.perf_counter()
        ev, ctx, issue = inp.event, inp.ctx, inp.top_issue
        raw = raw_signals(ev, ctx)
        base = dict(decision_id=inp.decision_id, step=inp.step, event_id=ev.event_id, raw_signals=raw,
                    considered_issues=inp.considered, judge=self.name, is_mock=True)
        if issue is None:
            reasons = ["No active issue in the presentation context."]
            if ctx.resolved_issue_ids:
                reasons.append(f"Resolved this event: {', '.join(ctx.resolved_issue_ids)}.")
            decision = JudgeDecision(
                decision="DO_NOT_INTERVENE", evidence_event_ids=[ev.event_id], supporting_reasons=reasons,
                counter_reasons=[], confidence=0.8,
                reason_for_final_decision="Nothing needs support right now; staying silent avoids distraction.",
                used_user_model_attributes=[], **base)
            decision.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            return decision

        components, extra = self.features(inp)
        utility = sum(c.contribution for c in components)
        if self.noise:
            rng = random.Random(f"{self.seed}:{inp.decision_id}")
            utility += rng.uniform(-self.noise, self.noise)
        utility = round(utility, 4)
        confidence = extra["confidence"]
        comp = {c.name: c for c in components}
        supporting = [issue.description]
        if comp["essentialness"].value >= 0.9:
            supporting.append("The content is marked as essential in the knowledge base.")
        if comp["persistence"].value >= 0.5:
            supporting.append(f"The issue has persisted for {inp.issue_age.get(issue.issue_id, 1)} events.")
        if inp.used_knowledge_ids:
            supporting.append(f"Knowledge supports the reminder ({', '.join(inp.used_knowledge_ids[:3])}).")
        if comp["expected_benefit"].value >= 0.6:
            supporting.append("Similar prompts helped this presenter before.")
        counter = []
        if comp["self_recovery_likelihood"].value >= 0.4:
            counter.append(f"The presenter may recover alone ({extra['rec_notes'][-1]}).")
        if comp["distraction_risk"].value >= 0.3:
            counter.append(f"A prompt could distract ({extra['dist_notes'][-1]}).")
        if issue.signals.get("deliberate_skip"):
            counter.append("The skip looked intentional.")
        if confidence < MIN_CONFIDENCE:
            counter.append(f"Low confidence ({confidence:.2f}).")

        forced, policy_reasons, cooldown_state = recency_policy(inp, issue, utility)
        if forced:
            decision, final = forced, policy_reasons[0]
            counter += policy_reasons
        elif utility >= T_INTERVENE and confidence >= MIN_CONFIDENCE:
            decision = "INTERVENE_NOW"
            final = (f"Utility {utility:.2f} ≥ {T_INTERVENE} and confidence {confidence:.2f} ≥ {MIN_CONFIDENCE}: "
                     f"{issue.type} needs support now.")
            supporting += policy_reasons
        elif utility >= T_WAIT:
            decision = "WAIT_AND_OBSERVE"
            final = (f"Utility {utility:.2f} is between {T_WAIT} and {T_INTERVENE}"
                     + (f" (or confidence {confidence:.2f} too low)" if utility >= T_INTERVENE else "")
                     + "; watching the next event before interrupting.")
        else:
            decision = "DO_NOT_INTERVENE"
            final = f"Utility {utility:.2f} < {T_WAIT}: the expected benefit does not justify an interruption."

        length = inp.user_model.value("preferred_prompt_length", "short")
        if ev.event_type in ("audience_question", "qa_answer") or float(inp.user_model.value("estimated_cognitive_load", 0)) >= 0.6:
            length = "short"
        um_attrs = sorted({r.removeprefix("um:") for c in components for r in c.evidence_refs if r.startswith("um:")}
                          | {extra["um_attr"], "preferred_prompt_length"})
        result = JudgeDecision(
            decision=decision, detected_issue=issue.type, issue_id=issue.issue_id, target=issue.target,
            severity=round(issue.severity, 3), urgency=round(issue.urgency, 3), confidence=confidence, utility=utility,
            evidence_event_ids=list(issue.evidence_event_ids),
            used_memory_ids=[h.id for h in (inp.memory_retrieval.hits if inp.memory_retrieval else []) if h.selected],
            used_knowledge_ids=list(inp.used_knowledge_ids), used_user_model_attributes=um_attrs,
            supporting_reasons=supporting, counter_reasons=counter, reason_for_final_decision=final,
            recommended_prompt_type=recommended_type(issue), recommended_prompt_length=length,
            cooldown_seconds=cooldown_for(length, inp.user_model), score_breakdown=components,
            cooldown_state=cooldown_state | {"confidence_parts": extra["confidence_parts"]}, **base,
        )
        result.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return result


def cooldown_for(length: str, um: PresenterUserModel) -> float:
    seconds = COOLDOWN_MEDIUM if length == "medium" else COOLDOWN_SHORT
    if um.value("expertise_level") == "novice":
        seconds -= 5
    return seconds


def raw_signals(ev: ObservedEvent, ctx: PresentationContext) -> dict[str, Any]:
    return {
        "event_type": ev.event_type, "slide": ev.current_slide, "elapsed_s": ev.elapsed_time,
        "remaining_s": ev.remaining_time, "speech_rate": ev.speech_rate, "speech_rate_ratio": ctx.speech_rate_ratio,
        "silence_s": ev.silence_duration, "filler_count": ev.filler_count, "filler_window": ctx.filler_window,
        "repeated_phrase": ctx.repeated_phrase, "audience": ctx.audience, "presenter_signal": ctx.presenter_signal,
        "time_on_slide_s": ctx.time_on_slide, "schedule_lag_s": ctx.schedule_lag_s,
        "time_budget_ratio": ctx.time_budget_ratio, "slide_changed": ctx.slide_changed,
        "skipped_slides": ctx.skipped_slides, "missed_kp_ids": ctx.missed_kp_ids,
        "active_question": (ctx.active_question or {}).get("qa_chunk_id"),
    }


# --------------------------------------------------------------------------- local LLM judge
class LLMJudgeOutput(BaseModel):
    decision: str
    detected_issue: str = "none"
    severity: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_event_ids: list[str] = Field(default_factory=list)
    used_memory_ids: list[str] = Field(default_factory=list)
    used_knowledge_ids: list[str] = Field(default_factory=list)
    supporting_reasons: list[str] = Field(default_factory=list, max_length=5)
    counter_reasons: list[str] = Field(default_factory=list, max_length=5)
    reason_for_final_decision: str = Field(max_length=300)
    recommended_prompt_type: str | None = None
    recommended_prompt_length: str | None = None


class OllamaJudge:
    """Asks a local model for the decision; code keeps the recency policy and validates every ID."""

    is_mock = False

    def __init__(self, provider, fallback: RuleBasedJudge):
        self.provider = provider
        self.fallback = fallback
        self.name = f"llm:{provider.name}"

    def payload(self, inp: JudgeInput, components: list[ScoreComponent]) -> dict[str, Any]:
        issue = inp.top_issue
        return {
            "language": inp.event.language,
            "event": {"event_id": inp.event.event_id, "type": inp.event.event_type,
                      "transcript": inp.event.transcript_chunk, "audience_question": inp.event.audience_question},
            "signals": raw_signals(inp.event, inp.ctx),
            "slide": {"number": inp.ctx.current_slide, "title": inp.ctx.slide_title,
                      "expected_content": inp.ctx.expected_content},
            "candidate_issue": issue.model_dump() if issue else None,
            "other_issues": inp.considered[1:],
            "deterministic_features": [{"name": c.name, "value": c.value} for c in components],
            "memories": [{"id": m.memory_id, "type": m.memory_type, "content": m.content}
                         for m in (inp.memories[h.id] for h in (inp.memory_retrieval.hits if inp.memory_retrieval else [])
                                   if h.selected and h.id in inp.memories)],
            "knowledge": [{"id": h.id, "score": h.score} for h in (inp.knowledge_retrieval.hits if inp.knowledge_retrieval else [])
                          if h.selected],
            "user_model": {k: {"value": a.value, "confidence": a.confidence} for k, a in inp.user_model.attrs.items()},
            "recent_prompts": [{"id": p.prompt_id, "type": p.prompt_type, "issue": p.issue_type,
                                "seconds_ago": round(inp.event.elapsed_time - p.created_elapsed, 1), "outcome": p.outcome}
                               for p in inp.prompts[-3:]],
            "allowed_decisions": list(DECISIONS),
            "allowed_prompt_types": sorted(set(PROMPT_FOR_ISSUE.values()) | {"wrap_up", "answer_structure"}),
        }

    def decide(self, inp: JudgeInput) -> JudgeDecision:
        base = self.fallback.decide(inp)  # deterministic features and a safe answer if the model fails
        if inp.top_issue is None:
            base.judge, base.is_mock = self.name + " (no issue: rules)", False
            return base
        t0 = time.perf_counter()
        try:
            out = self.provider.generate_structured("presentation_judge", self.payload(inp, base.score_breakdown),
                                                    LLMJudgeOutput)
            if out.decision not in DECISIONS:
                raise ValueError("decision outside the allowed set")
        except Exception as exc:  # noqa: BLE001 - any model failure falls back to the rule-based judge
            log.warning("llm judge failed (%s); using rule-based fallback", type(exc).__name__)
            base.fallback_used = True
            base.fallback_reason = type(exc).__name__
            return base
        known_events = {inp.event.event_id, *inp.top_issue.evidence_event_ids}
        known_mem = {h.id for h in (inp.memory_retrieval.hits if inp.memory_retrieval else [])}
        known_kn = {h.id for h in (inp.knowledge_retrieval.hits if inp.knowledge_retrieval else [])}
        overrides: list[str] = []
        decision = out.decision
        forced, reasons, state = recency_policy(inp, inp.top_issue, base.utility)
        if decision == "INTERVENE_NOW" and forced:
            overrides.append(f"LLM chose INTERVENE_NOW; recency policy forced {forced}: {reasons[0]}")
            decision = forced
        ptype = out.recommended_prompt_type if out.recommended_prompt_type in PROMPT_FOR_ISSUE.values() or \
            out.recommended_prompt_type in ("wrap_up", "answer_structure") else base.recommended_prompt_type
        return base.model_copy(update={
            "decision": decision,
            "severity": out.severity, "urgency": out.urgency, "confidence": out.confidence,
            "evidence_event_ids": [e for e in out.evidence_event_ids if e in known_events] or base.evidence_event_ids,
            "used_memory_ids": [m for m in out.used_memory_ids if m in known_mem],
            "used_knowledge_ids": [k for k in out.used_knowledge_ids if k in known_kn],
            "supporting_reasons": out.supporting_reasons, "counter_reasons": out.counter_reasons + reasons,
            "reason_for_final_decision": out.reason_for_final_decision if not overrides else overrides[0],
            "recommended_prompt_type": ptype,
            "recommended_prompt_length": out.recommended_prompt_length
            if out.recommended_prompt_length in ("short", "medium") else base.recommended_prompt_length,
            "policy_overrides": overrides, "cooldown_state": base.cooldown_state | state,
            "judge": self.name, "is_mock": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        })
