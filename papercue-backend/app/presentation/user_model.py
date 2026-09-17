"""Dynamic presenter user model.

Attributes start from uncertain profile priors and are revised by deterministic rules as
events and intervention outcomes arrive. Every revision is a `UserModelChange` that names
the evidence (event, issue, prompt or memory IDs) and the rule that fired.
"""

from __future__ import annotations

from typing import Any

from app.core.utils import clamp
from app.presentation.ids import IdFactory
from app.presentation.schemas import (
    GeneratedPrompt, Issue, ObservedEvent, PresentationContext, PresenterProfile, UserModelAttribute,
    UserModelChange, UserModelEvidence,
)

PRIOR_CONFIDENCE = 0.4
RATE_EMA = 0.15
LOAD_EMA = 0.5
MAX_EVIDENCE = 8

ATTRIBUTES = (
    "preferred_language", "expertise_level", "baseline_speech_rate", "speech_rate_trend", "baseline_filler_rate",
    "frequent_difficulties", "often_missed_content", "tension_pattern", "preferred_prompt_length",
    "prompt_responsiveness", "effective_contexts", "disruptive_contexts", "estimated_cognitive_load",
    "estimated_tension",
)


class PresenterUserModel:
    def __init__(self, presenter: PresenterProfile, ids: IdFactory):
        self.presenter = presenter
        self.ids = ids
        self.attrs: dict[str, UserModelAttribute] = {}
        self.history: list[UserModelChange] = []
        self._pending: list[UserModelChange] = []
        self._issue_counts: dict[str, int] = {}
        self._missed: dict[str, int] = {}
        self._outcomes: list[tuple[str, str, str]] = []  # (prompt_type, length, outcome)
        self._tension_filler_hits = 0
        self.step = 0
        self.elapsed = 0.0
        self.event_id: str | None = None

    # ------------------------------------------------------------------ primitives
    def _set(self, key: str, value: Any, confidence: float, reason: str, refs: list[str], *,
             source: str = "observed", note: str | None = None, force: bool = False) -> None:
        confidence = round(clamp(confidence), 3)
        if isinstance(value, float):
            value = round(value, 3)
        attr = self.attrs.get(key)
        if attr is not None and not force and attr.value == value and abs(attr.confidence - confidence) < 0.005:
            return
        change = UserModelChange(
            change_id=self.ids.next("umc"), attribute=key, step=self.step, event_id=self.event_id,
            elapsed=self.elapsed, old_value=attr.value if attr else None, new_value=value,
            old_confidence=attr.confidence if attr else None, new_confidence=confidence, reason=reason,
            evidence_refs=refs,
        )
        if attr is None:
            attr = UserModelAttribute(key=key, value=value, confidence=confidence, source=source)
            self.attrs[key] = attr
        else:
            attr.value = value
            attr.confidence = confidence
            if attr.source == "prior" and source == "observed":
                attr.source = "mixed"
        attr.updated_step = self.step
        for ref in refs:
            attr.evidence.append(UserModelEvidence(ref=ref, note=note or reason))
        attr.evidence = attr.evidence[-MAX_EVIDENCE:]
        self.history.append(change)
        self._pending.append(change)

    def value(self, key: str, default: Any = None) -> Any:
        attr = self.attrs.get(key)
        return attr.value if attr else default

    def confidence(self, key: str) -> float:
        attr = self.attrs.get(key)
        return attr.confidence if attr else 0.0

    def begin_step(self, step: int, event: ObservedEvent) -> None:
        self.step = step
        self.elapsed = event.elapsed_time
        self.event_id = event.event_id
        self._pending = []

    def drain_changes(self) -> list[UserModelChange]:
        out, self._pending = self._pending, []
        return out

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {k: a.model_dump() for k, a in self.attrs.items()}

    # ------------------------------------------------------------------ priors
    def seed_priors(self) -> None:
        p = self.presenter
        ref = [f"profile:{p.presenter_id}"]
        self._set("preferred_language", p.preferred_language, 0.6, "Profile prior.", ref, source="prior")
        self._set("expertise_level", p.expertise_level, 0.5, "Profile prior (self-reported level).", ref, source="prior")
        self._set("baseline_speech_rate", float(p.baseline_speech_rate), PRIOR_CONFIDENCE,
                  f"Profile prior ({p.speech_rate_unit}).", ref, source="prior")
        self._set("speech_rate_trend", "unknown", 0.2, "No observation yet.", ref, source="prior")
        self._set("baseline_filler_rate", float(p.baseline_filler_per_event), PRIOR_CONFIDENCE,
                  "Profile prior (fillers per event).", ref, source="prior")
        self._set("frequent_difficulties", [e.issue for e in p.prior_episodes], 0.3,
                  "Earlier sessions (prior).", ref, source="prior")
        missed = [t.value for t in p.prior_traits if t.attribute == "often_missed_content"]
        self._set("often_missed_content", missed, max([t.confidence for t in p.prior_traits
                                                       if t.attribute == "often_missed_content"], default=0.2),
                  "Profile prior.", ref, source="prior")
        tension = [t.value for t in p.prior_traits if t.attribute == "tension_pattern"]
        self._set("tension_pattern", tension[0] if tension else "unknown",
                  0.4 if tension else 0.2, "Profile prior.", ref, source="prior")
        self._set("preferred_prompt_length", p.preferred_prompt_length, PRIOR_CONFIDENCE, "Profile prior.", ref,
                  source="prior")
        self._set("prompt_responsiveness", 0.5, 0.2, "Uninformed prior (Beta(1,1) mean).", ref, source="prior")
        self._set("effective_contexts", [], 0.2, "No prompts yet.", ref, source="prior")
        disruptive = ["qa_prompts"] if any(e.issue == "qa_misunderstanding" for e in p.prior_episodes) else []
        self._set("disruptive_contexts", disruptive, 0.35 if disruptive else 0.2, "Earlier sessions (prior).", ref,
                  source="prior")
        self._set("estimated_cognitive_load", 0.3, 0.2, "Uninformed prior.", ref, source="prior")
        self._set("estimated_tension", 0.3, 0.2, "Uninformed prior.", ref, source="prior")

    # ------------------------------------------------------------------ observations
    def observe(self, event: ObservedEvent, ctx: PresentationContext, new_issues: list[Issue]) -> None:
        eid = event.event_id
        if self.value("preferred_language") != event.language or self.confidence("preferred_language") < 0.95:
            self._set("preferred_language", event.language, min(0.98, self.confidence("preferred_language") + 0.1),
                      "Language of the observed speech.", [eid])

        # typical speech rate: only chunks near the current baseline update it (robust EMA)
        if event.event_type in ("speech", "qa_answer") and event.speech_rate > 0:
            base = float(self.value("baseline_speech_rate"))
            ratio = event.speech_rate / base
            trend = "faster" if ratio >= 1.15 else "slower" if ratio <= 0.85 else "typical"
            self._set("speech_rate_trend", trend, min(0.9, 0.4 + 0.1 * len(ctx.recent_transcript)),
                      f"Latest chunk at {ratio:.2f}x the typical rate.", [eid])
            if abs(ratio - 1) < 0.2:
                new = (1 - RATE_EMA) * base + RATE_EMA * event.speech_rate
                self._set("baseline_speech_rate", new, self.confidence("baseline_speech_rate") + 0.05,
                          f"EMA(α={RATE_EMA}) with a typical-range chunk ({event.speech_rate:g}).", [eid])
            if event.filler_count <= 3 * float(self.value("baseline_filler_rate")) + 1:
                f_base = float(self.value("baseline_filler_rate"))
                self._set("baseline_filler_rate", 0.9 * f_base + 0.1 * event.filler_count,
                          self.confidence("baseline_filler_rate") + 0.03, "EMA(α=0.1) of fillers per chunk.", [eid])

        # tension and cognitive load (sensor values are mock)
        arousal = event.presenter_state.arousal
        old_t = float(self.value("estimated_tension"))
        filler_spike = event.filler_count >= 3
        tension = LOAD_EMA * old_t + (1 - LOAD_EMA) * clamp(arousal + (0.1 if filler_spike else 0.0))
        self._set("estimated_tension", tension, min(0.85, self.confidence("estimated_tension") + 0.08),
                  f"EMA of wearable arousal ({arousal:.2f}, mock){' + filler spike' if filler_spike else ''}.", [eid])
        types = {i.type for i in ctx.active_issues}
        load_now = clamp(0.2 + 0.35 * arousal + (0.15 if event.silence_duration >= 3 else 0.0)
                         + 0.15 * min(1.0, event.filler_count / 4) + (0.15 if "time_pressure" in types else 0.0)
                         + (0.1 if event.event_type in ("audience_question", "qa_answer") else 0.0))
        old_l = float(self.value("estimated_cognitive_load"))
        self._set("estimated_cognitive_load", LOAD_EMA * old_l + (1 - LOAD_EMA) * load_now,
                  min(0.8, self.confidence("estimated_cognitive_load") + 0.08),
                  f"Heuristic load {load_now:.2f} from arousal, silence, fillers, time pressure and Q&A.", [eid])
        if filler_spike and arousal >= 0.6:
            self._tension_filler_hits += 1
            if self._tension_filler_hits >= 2:
                self._set("tension_pattern", "fillers_rise_when_tense", min(0.85, 0.45 + 0.1 * self._tension_filler_hits),
                          "Filler spikes co-occurred with high arousal repeatedly.", [eid])

        for issue in new_issues:
            if issue.signals.get("self_recovered"):
                continue
            self._issue_counts[issue.type] = self._issue_counts.get(issue.type, 0) + 1
            ranked = sorted(self._issue_counts.items(), key=lambda x: (-x[1], x[0]))
            prior = [d for d in self.value("frequent_difficulties", []) if d not in self._issue_counts]
            total = sum(self._issue_counts.values())
            self._set("frequent_difficulties", [k for k, _ in ranked][:3] + prior[:1], min(0.85, 0.3 + 0.1 * total),
                      f"New {issue.type} issue ({issue.issue_id}).", [eid, issue.issue_id])
            if issue.type == "missing_key_point" and issue.signals.get("category") and not issue.signals.get("deliberate_skip"):
                cat = issue.signals["category"]
                self._missed[cat] = self._missed.get(cat, 0) + 1
                current = list(self.value("often_missed_content", []))
                confirmed = cat in current
                if not confirmed:
                    current.append(cat)
                conf = self.confidence("often_missed_content") + (0.25 if confirmed else 0.1)
                self._set("often_missed_content", current, min(0.9, conf),
                          ("Prior confirmed: " if confirmed else "New missed category: ") + cat, [eid, issue.issue_id])
            if issue.type == "long_silence" and issue.signals.get("still_silent") and arousal < 0.5 \
                    and self.value("tension_pattern") == "deliberate_pauses":
                self._set("tension_pattern", "deliberate_pauses", min(0.8, self.confidence("tension_pattern") + 0.1),
                          "Calm pause consistent with deliberate pausing.", [eid])

        # expertise: many serious issues lower the confidence of an 'expert' prior; a clean run raises it
        serious = sum(1 for i in new_issues if i.severity >= 0.6 and not i.signals.get("self_recovered"))
        level = self.value("expertise_level")
        if serious:
            self._set("expertise_level", level, self.confidence("expertise_level") - (0.08 if level == "expert" else 0.0)
                      + (0.05 if level == "novice" else 0.0), f"{serious} serious issue(s) observed.", [eid])
        elif not ctx.active_issues:
            self._set("expertise_level", level, min(0.85, self.confidence("expertise_level") + 0.02),
                      "Event without issues.", [eid])

    # ------------------------------------------------------------------ outcomes
    def observe_outcome(self, prompt: GeneratedPrompt, outcome: str, detail: dict[str, Any]) -> None:
        refs = [prompt.prompt_id, detail.get("event_id") or ""]
        refs = [r for r in refs if r]
        self._outcomes.append((prompt.prompt_type, prompt.length, outcome))
        good = sum(1 for o in self._outcomes if o[2] == "recovered") + 0.5 * sum(
            1 for o in self._outcomes if o[2] == "partially_recovered")
        n = len(self._outcomes)
        self._set("prompt_responsiveness", (good + 1) / (n + 2), clamp(1 - 1 / (n + 2)),
                  f"Beta(1,1) posterior mean after {n} outcome(s); latest: {outcome}.", refs)
        context = f"{prompt.issue_type}->{prompt.prompt_type}"
        if outcome in ("recovered", "partially_recovered"):
            eff = list(self.value("effective_contexts", []))
            if context not in eff:
                eff.append(context)
            self._set("effective_contexts", eff, min(0.9, self.confidence("effective_contexts") + 0.2),
                      f"{prompt.prompt_id} was followed by {outcome}.", refs)
            pref = self.value("preferred_prompt_length")
            if prompt.length == pref:
                self._set("preferred_prompt_length", pref, min(0.9, self.confidence("preferred_prompt_length") + 0.1),
                          f"A {prompt.length} prompt worked.", refs)
        else:
            pref = self.value("preferred_prompt_length")
            if prompt.length == "medium":
                self._set("preferred_prompt_length", "short", 0.45,
                          "A medium-length prompt did not help; try shorter prompts.", refs)
            else:
                self._set("preferred_prompt_length", pref, max(0.2, self.confidence("preferred_prompt_length") - 0.05),
                          f"A {prompt.length} prompt did not help.", refs)
        if detail.get("possible_disruption"):
            dis = list(self.value("disruptive_contexts", []))
            if context not in dis:
                dis.append(context)
            self._set("disruptive_contexts", dis, min(0.85, self.confidence("disruptive_contexts") + 0.2),
                      "Pause or filler spike right after the prompt (possible distraction).", refs)
