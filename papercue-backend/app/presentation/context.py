"""Presentation Context: short-lived, real-time state rebuilt on every observed event.

The tracker keeps content coverage per slide, timing against the slide plan, speech and
audience signals, the active question, and a set of *issues* produced by deterministic
detectors. Issues persist across events (keyed by type + target) until a resolution rule
fires, so the Judge can reason about persistence. Thresholds live in `DETECTOR_PARAMS`.
"""

from __future__ import annotations

from typing import Any

from app.core.utils import clamp
from app.presentation.ids import IdFactory
from app.presentation.schemas import (
    Issue, KnowledgeBase, ObservedEvent, PresentationContext, PresenterProfile, SessionMeta,
)
from app.presentation.textmatch import hits

DETECTOR_PARAMS: dict[str, float] = {
    "fast_ratio": 1.25,
    "slow_ratio": 0.8,
    "rushed_slide_fraction": 0.45,
    "at_risk_slide_fraction": 0.8,
    "silence_seconds": 3.0,
    "confusion_level": 0.6,
    "low_attention": 0.35,
    "time_ratio": 0.8,
    "time_watch_remaining_s": 240.0,
    "wrap_up_remaining_s": 75.0,
    "wrap_up_ratio": 0.55,
    "filler_event_min": 3.0,
    "filler_window_min": 8.0,
    "phrase_window_min": 3.0,
    "window_events": 3.0,
}
SKIP_MARKERS = ("넘어가", "시간 관계상", "생략하", "skip", "for time", "in the interest of time")


class ContextTracker:
    def __init__(self, meta: SessionMeta, kb: KnowledgeBase, presenter: PresenterProfile, ids: IdFactory):
        self.meta = meta
        self.kb = kb
        self.presenter = presenter
        self.ids = ids
        self.covered: dict[str, str] = {}  # kp_id -> event_id where first covered
        self.slide_entered: dict[int, float] = {}
        self.slide_transcripts: dict[int, list[str]] = {}
        self.history: list[ObservedEvent] = []
        self.rate_ratios: list[float] = []
        self.issues: dict[str, Issue] = {}  # key -> latest issue for that key
        self.archive: list[Issue] = []  # resolved issues replaced by a newer one with the same key
        self.active_question: dict[str, Any] | None = None
        self.ctx = PresentationContext(session_id=meta.session_id, language=meta.language)

    # ------------------------------------------------------------------ helpers
    def _issue(self, key: str, event: ObservedEvent, **fields: Any) -> Issue:
        existing = self.issues.get(key)
        if existing and existing.status == "active":
            existing.severity = fields.get("severity", existing.severity)
            existing.urgency = fields.get("urgency", existing.urgency)
            existing.signals = fields.get("signals", existing.signals)
            existing.detector_confidence = fields.get("detector_confidence", existing.detector_confidence)
            existing.description = fields.get("description", existing.description)
            existing.last_event_id = event.event_id
            if event.event_id not in existing.evidence_event_ids:
                existing.evidence_event_ids.append(event.event_id)
            return existing
        if existing:
            self.archive.append(existing)
        issue = Issue(issue_id=self.ids.next("iss"), first_event_id=event.event_id, last_event_id=event.event_id,
                      evidence_event_ids=list(fields.pop("evidence_event_ids", [])) or [event.event_id], **fields)
        if event.event_id not in issue.evidence_event_ids:
            issue.evidence_event_ids.append(event.event_id)
        self.issues[key] = issue
        self._new.append(issue.issue_id)
        return issue

    def _resolve(self, key: str, event: ObservedEvent) -> None:
        issue = self.issues.get(key)
        if issue and issue.status == "active":
            issue.status = "resolved"
            issue.resolved_event_id = event.event_id
            self._resolved.append(issue.issue_id)

    def _slide_kps(self, number: int):
        slide = self.kb.slide(number)
        return slide.key_points if slide else []

    def persistence(self, issue: Issue) -> int:
        return len(issue.evidence_event_ids)

    def _events_since_prompt(self, prompt_types: set[str], interventions: list[dict[str, Any]]) -> list[ObservedEvent]:
        last = max((i["elapsed"] for i in interventions if i["prompt_type"] in prompt_types), default=None)
        window = int(DETECTOR_PARAMS["window_events"])
        recent = self.history[-window:]
        return [e for e in recent if last is None or e.elapsed_time > last]

    # ------------------------------------------------------------------ main update
    def update(self, event: ObservedEvent, *, baseline_rate: float, baseline_filler: float,
               interventions: list[dict[str, Any]]) -> PresentationContext:
        P = DETECTOR_PARAMS
        self._new: list[str] = []
        self._resolved: list[str] = []
        prev_slide = self.ctx.current_slide
        slide = self.kb.slide(event.current_slide)
        self.history.append(event)
        text = " ".join(filter(None, [event.transcript_chunk]))

        # --- slide progress and timing
        slide_changed = bool(prev_slide) and event.current_slide != prev_slide
        if event.current_slide not in self.slide_entered:
            first = not self.slide_entered
            self.slide_entered[event.current_slide] = self.meta.excerpt_start_elapsed if first else event.elapsed_time
        entered = self.slide_entered[event.current_slide]
        time_on_slide = max(0.0, event.elapsed_time - entered)
        planned = slide.planned_seconds if slide else 0.0
        schedule_lag = (entered - self.kb.planned_start(event.current_slide)) + max(0.0, time_on_slide - planned)
        later = sum(s.planned_seconds for s in self.kb.slides if s.slide > event.current_slide and not s.is_qa)
        time_needed = 0.0 if (slide and slide.is_qa) else max(0.0, planned - time_on_slide) + later
        ratio = (event.remaining_time / time_needed) if time_needed > 0 else 9.99

        # --- content coverage (a key point counts as covered wherever it is mentioned)
        if text:
            self.slide_transcripts.setdefault(event.current_slide, []).append(text)
            for s in self.kb.slides:
                for kp in s.key_points:
                    if kp.kp_id not in self.covered and hits(text, kp.keywords):
                        self.covered[kp.kp_id] = event.event_id
                        self._resolve(f"missing_key_point:{kp.kp_id}", event)
                        self._resolve(f"content_at_risk:{kp.kp_id}", event)

        # --- missing key points on slides that were left or skipped
        skipped: list[int] = []
        time_pressure_before = any(i.type == "time_pressure" and i.status == "active" for i in self.issues.values())
        deliberate = any(m in text.lower() for m in SKIP_MARKERS) or time_pressure_before
        if slide_changed and event.current_slide > prev_slide:
            skipped = [n for n in range(prev_slide + 1, event.current_slide) if self.kb.slide(n)]
            left_time = event.elapsed_time - self.slide_entered.get(prev_slide, event.elapsed_time)
            left_planned = self.kb.slide(prev_slide).planned_seconds if self.kb.slide(prev_slide) else 0.0
            for number, was_skipped in [(prev_slide, False)] + [(n, True) for n in skipped]:
                for kp in self._slide_kps(number):
                    if not kp.essential or kp.kp_id in self.covered:
                        continue
                    self._resolve(f"content_at_risk:{kp.kp_id}", event)
                    severity = 0.7 if was_skipped else 0.8
                    if deliberate:
                        severity = 0.35
                    self._issue(
                        f"missing_key_point:{kp.kp_id}", event, type="missing_key_point", severity=severity,
                        urgency=0.75, essential=True, detector_confidence=0.8, target=kp.kp_id, target_label=kp.cue,
                        evidence_event_ids=[e.event_id for e in self.history if e.current_slide == number][-3:],
                        signals={"slide": number, "category": kp.category, "skipped_slide": was_skipped, "deliberate_skip": deliberate,
                                 "time_on_left_slide": round(left_time, 1), "planned_seconds": left_planned},
                        description=f"Essential point not mentioned on slide {number}: {kp.text}",
                    )
            self._rushed = left_planned > 0 and left_time < P["rushed_slide_fraction"] * left_planned
        else:
            self._rushed = False

        # --- content at risk on the current slide
        if slide and not slide.is_qa and time_on_slide >= P["at_risk_slide_fraction"] * planned:
            for kp in slide.key_points:
                if kp.essential and kp.kp_id not in self.covered:
                    self._issue(
                        f"content_at_risk:{kp.kp_id}", event, type="content_at_risk", severity=0.45, urgency=0.4,
                        essential=True, detector_confidence=0.6, target=kp.kp_id, target_label=kp.cue,
                        signals={"time_on_slide": round(time_on_slide, 1), "planned_seconds": planned},
                        description=f"Slide time almost used and essential point still open: {kp.text}",
                    )
                    break

        # --- speech rate
        rate_ratio = self.ctx.speech_rate_ratio
        speaking = event.event_type in ("speech", "qa_answer") and event.speech_rate > 0
        if speaking and baseline_rate > 0:
            rate_ratio = event.speech_rate / baseline_rate
            prev_ratio = self.rate_ratios[-1] if self.rate_ratios else 1.0
            self.rate_ratios.append(rate_ratio)
            if rate_ratio >= P["fast_ratio"]:
                sustained = prev_ratio >= P["fast_ratio"] - 0.05 or self._rushed
                sev = (0.45 + (rate_ratio - P["fast_ratio"]) * 2.5 + (0.1 if self._rushed else 0.0)) if sustained \
                    else 0.3 + (rate_ratio - P["fast_ratio"]) * 2.0
                self._issue("pace_too_fast", event, type="pace_too_fast", severity=clamp(sev, 0, 0.9),
                            urgency=0.6 if sustained else 0.35, detector_confidence=0.75 if sustained else 0.55,
                            target="pace", target_label="pace",
                            signals={"speech_rate": event.speech_rate, "baseline": round(baseline_rate, 1),
                                     "ratio": round(rate_ratio, 3), "sustained": sustained, "rushed_slide": self._rushed},
                            description=f"Speech rate {rate_ratio:.2f}x the presenter's typical rate.")
            else:
                self._resolve("pace_too_fast", event)
            if rate_ratio <= P["slow_ratio"]:
                sustained = prev_ratio <= P["slow_ratio"] + 0.05 or schedule_lag >= 20
                sev = 0.3 + (P["slow_ratio"] - rate_ratio) * 1.5 + max(0.0, schedule_lag) / 100 if sustained else 0.3
                self._issue("pace_too_slow", event, type="pace_too_slow", severity=clamp(sev, 0, 0.9),
                            urgency=clamp(0.3 + max(0.0, schedule_lag) / 100),
                            detector_confidence=0.75 if sustained else 0.5, target="pace", target_label="pace",
                            signals={"speech_rate": event.speech_rate, "baseline": round(baseline_rate, 1),
                                     "ratio": round(rate_ratio, 3), "schedule_lag_s": round(schedule_lag, 1),
                                     "sustained": sustained},
                            description=f"Speech rate {rate_ratio:.2f}x typical, {schedule_lag:.0f}s behind the slide plan.")
            else:
                self._resolve("pace_too_slow", event)

        # --- fillers and repeated phrases (window restarts after a repetition prompt)
        window = self._events_since_prompt({"repetition_alert"}, interventions)
        filler_window = sum(e.filler_count for e in window)
        phrase = event.repeated_phrase.text if event.repeated_phrase else None
        phrase_window = sum(e.repeated_phrase.count for e in window
                            if e.repeated_phrase and phrase and e.repeated_phrase.text == phrase)
        excess = filler_window - P["window_events"] * baseline_filler
        filler_issue = (
            (event.filler_count >= max(P["filler_event_min"], 2.5 * baseline_filler) and phrase_window >= P["phrase_window_min"])
            or filler_window >= max(P["filler_window_min"], 5 * baseline_filler)
            or phrase_window >= P["phrase_window_min"] + 1
        )
        if filler_issue:
            existing = self.issues.get("filler_repetition")
            sustained = bool(existing and existing.status == "active")
            self._issue("filler_repetition", event, type="filler_repetition",
                        severity=clamp(0.25 + 0.06 * max(0.0, excess) + 0.08 * max(0, phrase_window - 2), 0, 0.85),
                        urgency=clamp(0.35 + (0.15 if sustained else 0.0) + max(0.0, 0.7 - event.audience_signal.attention) * 0.5),
                        detector_confidence=0.8, target=phrase or "filler", target_label=phrase or "filler words",
                        signals={"filler_count": event.filler_count, "filler_window": filler_window,
                                 "phrase": phrase, "phrase_window": phrase_window,
                                 "baseline_per_event": round(baseline_filler, 2)},
                        description=f"{filler_window} fillers in the last {len(window)} events; '{phrase}' x{phrase_window}.")
        else:
            self._resolve("filler_repetition", event)

        # --- silence
        silence_key = f"long_silence:{event.current_slide}"
        if event.silence_duration >= P["silence_seconds"]:
            s = event.silence_duration
            stuck = event.event_type == "silence" or not text
            open_kps = sorted((kp for kp in (slide.key_points if slide else []) if kp.kp_id not in self.covered),
                              key=lambda kp: not kp.essential)
            open_kp = open_kps[0] if open_kps else None
            issue = self._issue(
                silence_key, event, type="long_silence",
                severity=clamp(0.2 + (s - 3) * 0.1 + (0.15 if event.presenter_state.arousal >= 0.7 else 0.0)),
                urgency=clamp(0.3 + (s - 3) * 0.08 + (0.15 if stuck else 0.0)),
                essential=bool(open_kp and open_kp.essential), detector_confidence=0.85,
                target=open_kp.kp_id if open_kp else f"slide:{event.current_slide}",
                target_label=open_kp.cue if open_kp else (slide.title if slide else ""),
                signals={"silence_s": s, "still_silent": stuck, "arousal": event.presenter_state.arousal,
                         "attention": event.audience_signal.attention},
                description=f"{s:.1f}s silence" + (" and no speech yet." if stuck else " before speech resumed."),
            )
            if not stuck:  # the presenter resumed on their own within the same chunk
                self._resolve(silence_key, event)
                issue.signals["self_recovered"] = True
        elif text:
            for key in [k for k in self.issues if k.startswith("long_silence:")]:
                self._resolve(key, event)

        # --- audience confusion / engagement
        aud = event.audience_signal
        prev_aud = self.history[-2].audience_signal if len(self.history) > 1 else None
        if aud.confusion >= P["confusion_level"]:
            sustained = bool(prev_aud and prev_aud.confusion >= P["confusion_level"])
            recent_text = " ".join(e.transcript_chunk for e in self.history[-2:])
            term = next((c for c in self.kb.chunks if c.category == "glossary" and hits(recent_text, c.keywords)), None)
            self._issue("audience_confusion", event, type="audience_confusion",
                        severity=clamp((aud.confusion - 0.4) * 1.5 + (0.15 if sustained else 0.0)),
                        urgency=clamp(0.3 + max(0.0, 0.7 - aud.attention) * 0.8 + (0.2 if sustained else 0.0)),
                        detector_confidence=0.7 if sustained else 0.55,
                        target=term.chunk_id if term else f"slide:{event.current_slide}",
                        target_label=term.term if term else (slide.title if slide else ""),
                        signals={"confusion": aud.confusion, "attention": aud.attention, "reaction": aud.reaction,
                                 "sustained": sustained, "term": term.term if term else None},
                        description=f"Audience confusion {aud.confusion:.2f} (attention {aud.attention:.2f}).")
        else:
            self._resolve("audience_confusion", event)
        if aud.attention <= P["low_attention"]:
            self._issue("low_engagement", event, type="low_engagement", severity=clamp(0.3 + (0.35 - aud.attention)),
                        urgency=0.4, detector_confidence=0.5, target=f"slide:{event.current_slide}",
                        target_label=slide.title if slide else "",
                        signals={"attention": aud.attention, "reaction": aud.reaction},
                        description=f"Audience attention {aud.attention:.2f}.")
        else:
            self._resolve("low_engagement", event)

        # --- Q&A
        qa_chunks = [c for c in self.kb.chunks if c.category == "expected_qa"]
        if event.event_type == "audience_question" and event.audience_question:
            scored = sorted(((len(hits(event.audience_question, c.keywords)), c) for c in qa_chunks),
                            key=lambda x: -x[0])
            best = scored[0] if scored and scored[0][0] > 0 else None
            self.active_question = {"event_id": event.event_id, "text": event.audience_question,
                                    "qa_chunk_id": best[1].chunk_id if best else None,
                                    "topic": best[1].topic if best else None,
                                    "match_hits": best[0] if best else 0, "answered": False}
            self._resolve("qa_misunderstanding", event)
        elif event.event_type == "qa_answer" and self.active_question and self.active_question.get("qa_chunk_id"):
            expected = next(c for c in qa_chunks if c.chunk_id == self.active_question["qa_chunk_id"])
            expected_hits = hits(text, expected.answer_keywords)
            others = {c.chunk_id: len(hits(text, c.answer_keywords)) for c in qa_chunks if c.chunk_id != expected.chunk_id}
            best_other = max(others.items(), key=lambda x: x[1], default=(None, 0))
            if expected_hits:
                self.active_question["answered"] = True
                self._resolve("qa_misunderstanding", event)
            else:
                mode = "different_question" if best_other[1] > 0 else "unfocused"
                self._issue("qa_misunderstanding", event, type="qa_misunderstanding",
                            severity=0.8 if mode == "different_question" else 0.55,
                            urgency=0.85 if mode == "different_question" else 0.6,
                            essential=True, detector_confidence=0.75 if mode == "different_question" else 0.55,
                            target=expected.chunk_id, target_label=expected.topic,
                            evidence_event_ids=[self.active_question["event_id"]],
                            signals={"mode": mode, "expected_qa": expected.chunk_id,
                                     "answer_matches": best_other[0] if best_other[1] else None,
                                     "expected_keyword_hits": 0, "other_keyword_hits": best_other[1]},
                            description=f"Answer does not address the question topic '{expected.topic}'.")

        # --- time pressure
        watch = slide is not None and not slide.is_qa and event.remaining_time < P["time_watch_remaining_s"]
        if watch and ratio < P["time_ratio"]:
            wrap = event.remaining_time <= P["wrap_up_remaining_s"] or ratio <= P["wrap_up_ratio"]
            last_slide = max(s.slide for s in self.kb.slides if not s.is_qa)
            final_kp = next((kp for kp in self._slide_kps(last_slide) if kp.essential), None)
            self._issue("time_pressure", event, type="time_pressure",
                        severity=clamp(0.3 + (0.85 - ratio) * 1.6 + max(0.0, schedule_lag) / 200),
                        urgency=clamp(1 - event.remaining_time / 300), essential=wrap, detector_confidence=0.85,
                        target=final_kp.kp_id if wrap and final_kp else f"slide:{event.current_slide + 1}",
                        target_label=final_kp.cue if wrap and final_kp else None,
                        signals={"remaining_s": event.remaining_time, "time_needed_s": round(time_needed, 1),
                                 "budget_ratio": round(ratio, 3), "schedule_lag_s": round(schedule_lag, 1),
                                 "wrap_up_needed": wrap},
                        description=f"{event.remaining_time:.0f}s left for about {time_needed:.0f}s of planned content.")
        else:
            self._resolve("time_pressure", event)

        # --- assemble the context snapshot
        c = self.ctx
        c.event_id = event.event_id
        c.elapsed_time = event.elapsed_time
        c.remaining_time = event.remaining_time
        c.current_slide = event.current_slide
        c.slide_title = event.slide_title
        c.slide_entered_at = entered
        c.time_on_slide = round(time_on_slide, 1)
        c.planned_slide_seconds = planned
        c.schedule_lag_s = round(schedule_lag, 1)
        c.time_needed_s = round(time_needed, 1)
        c.time_budget_ratio = round(min(ratio, 9.99), 3)
        c.expected_content = [{"kp_id": kp.kp_id, "text": kp.text, "essential": kp.essential, "cue": kp.cue,
                               "covered": kp.kp_id in self.covered, "covered_in": self.covered.get(kp.kp_id)}
                              for kp in (slide.key_points if slide else [])]
        c.covered_kp_ids = sorted(self.covered)
        c.missed_kp_ids = sorted(i.target for i in self.issues.values()
                                 if i.type == "missing_key_point" and i.status == "active" and i.target)
        c.recent_transcript = [{"event_id": e.event_id, "elapsed": e.elapsed_time, "slide": e.current_slide,
                                "type": e.event_type, "text": e.transcript_chunk or e.audience_question or ""}
                               for e in self.history[-4:]]
        c.speech_rate = event.speech_rate
        c.speech_rate_ratio = round(rate_ratio, 3)
        c.silence_duration = event.silence_duration
        c.filler_count = event.filler_count
        c.filler_window = filler_window
        c.repeated_phrase = {"text": phrase, "window_count": phrase_window} if phrase else None
        c.audience = aud.model_dump()
        c.presenter_signal = event.presenter_state.model_dump()
        c.active_question = dict(self.active_question) if self.active_question else None
        c.active_issues = [i.model_copy(deep=True) for i in self.issues.values() if i.status == "active"]
        c.new_issue_ids = list(self._new)
        c.resolved_issue_ids = list(self._resolved)
        c.recent_interventions = interventions[-3:]
        c.slide_changed = slide_changed
        c.skipped_slides = skipped
        return c.model_copy(deep=True)

    def all_issues(self) -> list[Issue]:
        return sorted([*self.archive, *self.issues.values()], key=lambda i: i.issue_id)

    def issue_by_id(self, issue_id: str) -> Issue | None:
        return next((i for i in self.all_issues() if i.issue_id == issue_id), None)
