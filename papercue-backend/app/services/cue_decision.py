"""Cue Decision Engine (fully deterministic).

Decides *whether* to intervene and *which* action/target to use. Cue wording is a
separate step. The score combines need, question relevance, grounding strength,
audience-model confidence, novelty, timing, and interruption cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.text import contains_phrase
from app.core.utils import clamp, new_id
from app.models.audience import Belief
from app.models.conversation import ConversationState
from app.models.cue import CueDecision, InterventionScores, RetrievedUnit
from app.models.enums import UNGROUNDED_ACTIONS, CueAction, ListenerAct, Phase, PresenterAct, SessionMode, Speaker

WEIGHTS = {
    "need": 0.30, "relevance": 0.20, "grounding": 0.15, "model_confidence": 0.15,
    "novelty": 0.10, "timing": 0.10, "interruption_cost": -0.20,
}
GROUNDING_MIN = 0.30
ON_DEMAND_MIN = 0.25


@dataclass
class Candidate:
    action: CueAction
    target: str | None
    need: float
    confidence: float
    reason_code: str
    reason: str
    beliefs: list[Belief] = field(default_factory=list)


def _find(beliefs: list[Belief], dim: str, value: str | None = None, min_conf: float = 0.0) -> list[Belief]:
    return sorted(
        (b for b in beliefs if b.dimension.value == dim and (value is None or b.value == value)
         and b.effective_confidence >= min_conf),
        key=lambda b: b.effective_confidence, reverse=True,
    )


class CueDecisionEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    def candidates(self, state: ConversationState, beliefs: list[Belief],
                   retrieved: list[RetrievedUnit]) -> list[Candidate]:
        out: list[Candidate] = []
        latest_listener = state.last_speaker == Speaker.listener
        open_issues = {i.topic for i in state.unresolved_issues if i.status in ("open", "addressed")}

        for b in _find(beliefs, "concern", "raised", 0.5):
            fresh = b.key in state.detected_concerns[-1:] and state.listener_act != ListenerAct.agreement
            if b.key in open_issues or fresh:
                out.append(Candidate(CueAction.address_concern, b.key, 0.9 if fresh else 0.7, b.effective_confidence,
                                     "explicit_concern" if b.lifecycle == "explicitly_confirmed" else "open_concern",
                                     f"The listener raised a {b.key} concern that is still open.", [b]))
                break

        if state.possible_misunderstanding and latest_listener:
            out.append(Candidate(CueAction.recover, state.possible_misunderstanding, 0.85, 0.6, "misunderstanding",
                                 "The listener's words suggest a possible misunderstanding."))

        if state.listener_act == ListenerAct.challenge:
            has_limit = any(u.unit_type == "limitation" and u.score >= GROUNDING_MIN for u in retrieved)
            action = CueAction.acknowledge_limitation if has_limit else CueAction.reframe
            out.append(Candidate(action, state.topic, 0.75, 0.6, "challenge",
                                 "The listener challenged the current point."))

        unfamiliar = _find(beliefs, "familiarity", "unfamiliar", 0.5)
        simple = _find(beliefs, "explanation_level", "simple", 0.45)
        if state.listener_act == ListenerAct.clarification_request or unfamiliar or simple:
            target = unfamiliar[0].key if unfamiliar else state.topic
            need = 0.8 if state.listener_act == ListenerAct.clarification_request and latest_listener else 0.55
            used = (unfamiliar[:1] + simple[:1])
            conf = max((b.effective_confidence for b in used), default=0.5)
            out.append(Candidate(CueAction.simplify, target, need, conf,
                                 "clarification_request" if need == 0.8 else "low_familiarity",
                                 "The listener needs a simpler explanation.", used))

        apps = _find(beliefs, "interest", "interested", 0.45)
        apps = [b for b in apps if b.key == "practical applications"]
        if apps and (state.phase == Phase.application_discussion or state.listener_act == ListenerAct.question):
            out.append(Candidate(CueAction.emphasize, "practical applications", 0.7, apps[0].effective_confidence,
                                 "application_interest", "The listener asked about practical use.", apps[:1]))

        for b in _find(beliefs, "connection", "relevant", 0.35):
            grounded = any(contains_phrase(" ".join([u.title, *u.keywords]), b.key) for u in retrieved)
            need = (0.65 if b.source_type == "conversation" else 0.45) + (0.1 if grounded else 0.0)
            out.append(Candidate(CueAction.connect, b.key, need, b.effective_confidence,
                                 "profile_connection" if b.source_type == "profile" else "stated_connection",
                                 f"The listener's work on {b.key} connects to the paper.", [b]))
            break

        technical = _find(beliefs, "explanation_level", "technical", 0.45)
        if technical:
            out.append(Candidate(CueAction.elaborate, state.topic, 0.6, technical[0].effective_confidence,
                                 "detail_request", "The listener asked for more technical detail.", technical[:1]))

        if state.phase == Phase.closing or state.listener_act == ListenerAct.closing:
            out.append(Candidate(CueAction.close, "main contribution", 0.6, 0.7, "closing",
                                 "The conversation is closing."))

        low_eng = _find(beliefs, "engagement", "low", 0.25)
        if low_eng:
            out.append(Candidate(CueAction.verify, "main concern", 0.5, low_eng[0].effective_confidence,
                                 "low_engagement", "The listener gave minimal replies.", low_eng[:1]))

        if not _find(beliefs, "explanation_level"):
            out.append(Candidate(CueAction.verify, "detail level", 0.3, 0.5, "unknown_detail_level",
                                 "The preferred level of detail is still unknown."))
        else:
            out.append(Candidate(CueAction.verify, "main concern", 0.3, 0.5, "no_clear_need",
                                 "No specific need is evident; checking with the listener."))
        return out

    def decide(self, mode: SessionMode, state: ConversationState, beliefs: list[Belief],
               retrieved: list[RetrievedUnit], recent_decisions: list[tuple[str, str | None]]) -> CueDecision:
        cands = self.candidates(state, beliefs, retrieved)
        best = max(cands, key=lambda c: c.need)  # max() keeps the first (higher-priority) on ties
        relevance = clamp(max((u.similarity for u in retrieved), default=0.0))
        grounding = clamp(max((u.score for u in retrieved), default=0.0))
        reason_code, reason, params = best.reason_code, best.reason, {"target": best.target}

        if best.action not in UNGROUNDED_ACTIONS and grounding < GROUNDING_MIN:
            params = {"target": best.target, "original_action": best.action.value}
            best = Candidate(CueAction.verify, "main concern", best.need * 0.6, 0.5, "insufficient_grounding",
                             "No paper unit supports a direct cue; a verification cue is safer.", best.beliefs)
            reason_code, reason = best.reason_code, best.reason

        repeated = sum(1 for a, t in recent_decisions[-3:] if a == best.action.value and t == best.target)
        novelty = 0.2 if repeated else 1.0
        timing = 1.0 if state.turns_since_last_cue >= 3 or not recent_decisions else state.turns_since_last_cue / 3
        if state.last_speaker == Speaker.presenter and state.presenter_act in (PresenterAct.explanation, PresenterAct.example):
            cost = 0.4
        elif state.last_speaker == Speaker.listener and state.explicit_question:
            cost = 0.1
        else:
            cost = 0.25
        values = {"need": best.need, "relevance": relevance, "grounding": grounding,
                  "model_confidence": best.confidence, "novelty": novelty, "timing": timing,
                  "interruption_cost": cost}
        total = clamp(sum(WEIGHTS[k] * v for k, v in values.items()))
        scores = InterventionScores(**{k: round(v, 4) for k, v in values.items()}, total=round(total, 4))

        threshold = ON_DEMAND_MIN if mode == SessionMode.on_demand else self.settings.auto_candidate_threshold
        should = total >= threshold
        if not should:
            reason_code = "below_threshold"
            reason = f"Intervention score {total:.2f} is below the threshold {threshold:.2f}."
            params = {"score": round(total, 2), "threshold": threshold, "target": best.target}
        conf_basis = best.confidence if best.action in UNGROUNDED_ACTIONS else (best.confidence + grounding) / 2
        return CueDecision(
            id=new_id(), should_intervene=should, action=best.action, target=best.target,
            confidence=round(clamp(conf_basis), 4), short_reason=reason, reason_code=reason_code,
            reason_params=params, beliefs_used=[b.id for b in best.beliefs],
            evidence_used=[e for b in best.beliefs for e in b.evidence_ids[-2:]],
            scores=scores, mode=mode,
        )
