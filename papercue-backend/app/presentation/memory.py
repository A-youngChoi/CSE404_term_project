"""Layered memory in the spirit of Generative Agents and Memoro.

* working       - the current slide and the last few transcript chunks (replaced each event)
* episodic      - notable events of this and earlier presentations
* semantic      - generalised presenter traits (priors and patterns confirmed in-session)
* intervention  - delivered prompts and what happened afterwards
* reflection    - higher-level patterns synthesised from several memories (deterministic rules)

Retrieval score = w_rel * relevance + w_imp * importance + w_rec * recency, where
recency = RECENCY_DECAY ** (seconds since last access). Every create/update/retrieve is
logged as a `MemoryChange` with before/after values so the dashboard can show diffs.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.core.utils import clamp
from app.presentation.ids import IdFactory
from app.presentation.schemas import (
    Issue, MemoryChange, MemoryItem, MemoryRetrieval, ObservedEvent, PresentationContext, PresenterProfile,
    RetrievalHit,
)
from app.presentation.textmatch import overlap, terms

RETRIEVAL_WEIGHTS = {"relevance": 0.5, "importance": 0.25, "recency": 0.25}
RECENCY_DECAY = 0.995
RETRIEVAL_TOP_K = 4
RETRIEVAL_MIN_SCORE = 0.3
REFLECTION_THRESHOLD = 1.2
_DIFF_FIELDS = ("content", "importance", "confidence", "active", "retrieval_count", "data")


def _view(m: MemoryItem) -> dict[str, Any]:
    return {k: getattr(m, k) for k in _DIFF_FIELDS}


class MemoryStore:
    def __init__(self, presenter: PresenterProfile, ids: IdFactory):
        self.presenter = presenter
        self.ids = ids
        self.items: dict[str, MemoryItem] = {}
        self.log: list[MemoryChange] = []
        self._pending: list[MemoryChange] = []
        self._working: dict[str, str] = {}
        self._importance_since_reflection = 0.0
        self._reflected_keys: set[str] = set()
        self.step = 0
        self.elapsed = 0.0
        self.event_id: str | None = None

    # ------------------------------------------------------------------ primitives
    def _record(self, op: str, item: MemoryItem, reason: str, before: dict | None) -> None:
        change = MemoryChange(op=op, memory_id=item.memory_id, step=self.step, event_id=self.event_id,
                              reason=reason, before=before, after=_view(item) if op != "retrieved" else None)
        self.log.append(change)
        self._pending.append(change)

    def create(self, memory_type: str, content: str, *, reason: str, importance: float, confidence: float,
               source_event_ids: list[str] | None = None, evidence: list[str] | None = None,
               related_slide: int | None = None, tags: list[str] | None = None, data: dict | None = None,
               origin: str = "session") -> MemoryItem:
        item = MemoryItem(
            memory_id=self.ids.next("mem"), memory_type=memory_type, content=content, created_step=self.step,
            created_elapsed=self.elapsed, last_updated_step=self.step, last_accessed_elapsed=self.elapsed,
            source_event_ids=source_event_ids or ([self.event_id] if self.event_id else []),
            evidence=evidence or [], importance=clamp(importance), confidence=clamp(confidence),
            related_slide=related_slide, tags=tags or [], reason=reason, data=data or {}, origin=origin,
        )
        self.items[item.memory_id] = item
        self._record("created", item, reason, None)
        if memory_type in ("episodic", "intervention"):
            self._importance_since_reflection += item.importance
        return item

    def update(self, memory_id: str, op: str, reason: str, **changes: Any) -> MemoryItem:
        item = self.items[memory_id]
        before = _view(item)
        for k, v in changes.items():
            if k in ("importance", "confidence"):
                v = clamp(v)
            if k == "data":
                v = {**item.data, **v}
            setattr(item, k, v)
        item.last_updated_step = self.step
        if self.event_id and self.event_id not in item.source_event_ids and op not in ("deactivated", "retrieved"):
            item.source_event_ids.append(self.event_id)
        self._record(op, item, reason, before)
        return item

    def begin_step(self, step: int, event: ObservedEvent) -> None:
        self.step = step
        self.elapsed = event.elapsed_time
        self.event_id = event.event_id
        self._pending = []

    def drain_changes(self) -> list[MemoryChange]:
        out, self._pending = self._pending, []
        return out

    def recency(self, item: MemoryItem) -> float:
        return round(RECENCY_DECAY ** max(0.0, self.elapsed - item.last_accessed_elapsed), 4)

    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        for m in self.items.values():
            m.recency = self.recency(m)
            out.append(m.model_dump(exclude={"data"}) | {"data": m.data})
        return out

    def find(self, memory_type: str, key: str) -> MemoryItem | None:
        return next((m for m in self.items.values() if m.memory_type == memory_type and m.data.get("key") == key), None)

    # ------------------------------------------------------------------ priors
    def seed_priors(self) -> None:
        for i, ep in enumerate(self.presenter.prior_episodes):
            self.create("episodic", ep.summary, reason="Loaded from the presenter's earlier sessions (prior).",
                        importance=ep.importance, confidence=0.6, source_event_ids=[f"prior:{self.presenter.presenter_id}:{i}"],
                        evidence=[ep.summary], tags=[ep.issue] + ([ep.category] if ep.category else []),
                        data={"key": f"prior_episode:{i}", "issue": ep.issue, "category": ep.category}, origin="prior")
        for i, tr in enumerate(self.presenter.prior_traits):
            self.create("semantic", tr.summary, reason="Prior trait from the presenter profile (uncertain).",
                        importance=0.6, confidence=tr.confidence, source_event_ids=[f"prior:{self.presenter.presenter_id}:trait{i}"],
                        evidence=[tr.summary], tags=[tr.attribute, str(tr.value)],
                        data={"key": f"trait:{tr.attribute}:{tr.value}", "attribute": tr.attribute, "value": tr.value},
                        origin="prior")
        self._importance_since_reflection = 0.0

    # ------------------------------------------------------------------ per-event updates
    def observe(self, event: ObservedEvent, ctx: PresentationContext, new_issues: list[Issue],
                resolved_issues: list[Issue], issue_counts: Counter) -> None:
        # working memory: one slide item and one transcript item, revised in place
        slide_text = f"Slide {ctx.current_slide} '{ctx.slide_title}': " + "; ".join(
            f"{'✓' if kp['covered'] else '○'} {kp['cue']}" for kp in ctx.expected_content)
        transcript = " / ".join(t["text"] for t in ctx.recent_transcript[-3:] if t["text"])
        for key, content, tags in (("slide", slide_text, ["slide", f"slide{ctx.current_slide}"]),
                                   ("transcript", transcript or "(silence)", terms(transcript)[:12])):
            mid = self._working.get(key)
            if mid is None:
                item = self.create("working", content, reason="Working memory initialised.", importance=0.3,
                                   confidence=1.0, related_slide=ctx.current_slide, tags=tags, data={"key": f"working:{key}"})
                self._working[key] = item.memory_id
            elif self.items[mid].content != content:
                self.update(mid, "revised", "Replaced by the latest observation.", content=content, tags=tags,
                            related_slide=ctx.current_slide, last_accessed_elapsed=self.elapsed)

        for issue in new_issues:
            importance = clamp(0.3 + 0.6 * issue.severity)
            self_recovered = bool(issue.signals.get("self_recovered"))
            content = issue.description + (" The presenter resumed without help." if self_recovered else "")
            self.create("episodic", content, reason=f"New {issue.type} issue detected ({issue.issue_id}).",
                        importance=importance * (0.6 if self_recovered else 1.0), confidence=issue.detector_confidence,
                        source_event_ids=list(issue.evidence_event_ids), evidence=[issue.description],
                        related_slide=ctx.current_slide,
                        tags=[issue.type, issue.target or "", *(terms(issue.target_label or "")[:4])],
                        data={"key": f"issue:{issue.issue_id}", "issue_id": issue.issue_id, "issue": issue.type,
                              "target": issue.target, "self_recovered": self_recovered})
            self._reinforce_semantic(issue, issue_counts)
        for issue in resolved_issues:
            mem = self.find("episodic", f"issue:{issue.issue_id}")
            if mem and not mem.data.get("resolved"):
                self.update(mem.memory_id, "revised", f"Issue {issue.issue_id} resolved at {self.event_id}.",
                            content=mem.content + f" Resolved at {self.event_id}.",
                            data={"resolved": True, "resolved_event_id": self.event_id})
        if event.event_type == "audience_question" and event.audience_question:
            self.create("episodic", f"Audience asked: {event.audience_question}", reason="Audience question observed.",
                        importance=0.6, confidence=0.9, related_slide=ctx.current_slide,
                        tags=["question", *terms(event.audience_question)[:8]],
                        data={"key": f"question:{event.event_id}", "qa_chunk_id": (ctx.active_question or {}).get("qa_chunk_id")})

    def _reinforce_semantic(self, issue: Issue, issue_counts: Counter) -> None:
        category = None
        if issue.type == "missing_key_point" and issue.signals.get("slide") is not None:
            category = issue.signals.get("category")
        # Confirm a prior trait when the same kind of content is missed again.
        for m in self.items.values():
            if m.memory_type != "semantic" or m.data.get("attribute") != "often_missed_content":
                continue
            if issue.type == "missing_key_point" and category and m.data.get("value") == category:
                self.update(m.memory_id, "reinforced", f"Prior trait confirmed by {issue.issue_id}.",
                            confidence=m.confidence + 0.2, importance=m.importance + 0.1)
        if issue_counts[issue.type] >= 2:
            key = f"pattern:{issue.type}"
            existing = self.find("semantic", key)
            content = f"Recurring difficulty in this talk: {issue.type} ({issue_counts[issue.type]} times)."
            if existing:
                self.update(existing.memory_id, "reinforced", f"Seen again ({issue.issue_id}).", content=content,
                            confidence=existing.confidence + 0.1)
            else:
                self.create("semantic", content, reason=f"{issue.type} observed at least twice in this session.",
                            importance=0.6, confidence=0.55, tags=[issue.type, "recurring"],
                            evidence=[issue.issue_id], data={"key": key, "attribute": "frequent_difficulties",
                                                             "value": issue.type})

    def record_intervention(self, prompt: Any, decision: Any) -> MemoryItem:
        return self.create(
            "intervention",
            f"Prompted '{prompt.text}' ({prompt.prompt_type}) for {decision.detected_issue}; outcome pending.",
            reason=f"Prompt {prompt.prompt_id} delivered after decision {decision.decision_id}.",
            importance=clamp(0.4 + 0.5 * decision.severity), confidence=0.7,
            source_event_ids=list(decision.evidence_event_ids), evidence=list(decision.supporting_reasons[:2]),
            tags=[decision.detected_issue, prompt.prompt_type, "intervention"],
            data={"key": f"prompt:{prompt.prompt_id}", "prompt_id": prompt.prompt_id, "issue": decision.detected_issue,
                  "prompt_type": prompt.prompt_type, "length": prompt.length, "outcome": "pending",
                  "decision_id": decision.decision_id},
        )

    def record_outcome(self, prompt: Any, outcome: str, detail: dict[str, Any]) -> None:
        mem = self.find("intervention", f"prompt:{prompt.prompt_id}")
        if mem is None:
            return
        good = outcome in ("recovered", "partially_recovered")
        self.update(mem.memory_id, "reinforced" if good else "weakened",
                    f"Outcome of {prompt.prompt_id}: {outcome} ({detail.get('reason', '')}).",
                    content=f"Prompted '{prompt.text}' ({prompt.prompt_type}) for {prompt.issue_type}; outcome: {outcome}.",
                    confidence=mem.confidence + (0.15 if good else -0.2),
                    importance=mem.importance + (0.1 if good else 0.0),
                    data={"outcome": outcome, "outcome_event_id": detail.get("event_id")})
        self._importance_since_reflection += 0.4

    # ------------------------------------------------------------------ reflection
    def reflect(self, force: bool = False) -> list[MemoryItem]:
        if not force and self._importance_since_reflection < REFLECTION_THRESHOLD:
            return []
        created: list[MemoryItem] = []
        episodes = [m for m in self.items.values() if m.memory_type == "episodic" and m.origin == "session"]
        interventions = [m for m in self.items.values() if m.memory_type == "intervention"]

        by_issue: dict[str, list[MemoryItem]] = {}
        for m in episodes:
            if m.data.get("issue"):
                by_issue.setdefault(m.data["issue"], []).append(m)
        for issue, ms in by_issue.items():
            if len(ms) >= 2:
                created += self._reflection(
                    f"recurring:{issue}", f"Pattern: {issue} occurred {len(ms)} times in this talk.",
                    [m.memory_id for m in ms], importance=0.7, confidence=min(0.9, 0.5 + 0.1 * len(ms)), tags=[issue])
        for m in interventions:
            outcome = m.data.get("outcome")
            if outcome in ("recovered", "partially_recovered"):
                created += self._reflection(
                    f"effective:{m.data['prompt_type']}",
                    f"Pattern: {m.data['length']} {m.data['prompt_type']} prompts were followed by recovery.",
                    [m.memory_id], importance=0.65, confidence=0.6, tags=[m.data["issue"], m.data["prompt_type"], "effective"])
            elif outcome == "not_recovered":
                created += self._reflection(
                    f"ineffective:{m.data['prompt_type']}",
                    f"Pattern: a {m.data['prompt_type']} prompt did not change the behaviour; prefer a different form.",
                    [m.memory_id], importance=0.6, confidence=0.5, tags=[m.data["issue"], m.data["prompt_type"], "ineffective"])
        tense_fillers = [m for m in episodes if m.data.get("issue") == "filler_repetition"]
        prior_filler = [m for m in self.items.values() if m.origin == "prior" and "filler_repetition" in m.tags]
        if tense_fillers and prior_filler:
            created += self._reflection(
                "prior_confirmed:filler", "Pattern: fillers rise under pressure, as in earlier sessions.",
                [tense_fillers[0].memory_id, prior_filler[0].memory_id], importance=0.7, confidence=0.7,
                tags=["filler_repetition", "tension"])
        prior_missing = [m for m in self.items.values() if m.origin == "prior" and "missing_key_point" in m.tags]
        missing_now = by_issue.get("missing_key_point", [])
        if prior_missing and missing_now:
            created += self._reflection(
                "prior_confirmed:missing", "Pattern: the presenter again moved on without an essential point.",
                [missing_now[0].memory_id, prior_missing[0].memory_id], importance=0.7, confidence=0.65,
                tags=["missing_key_point"])
        self._importance_since_reflection = 0.0
        return created

    def _reflection(self, key: str, content: str, sources: list[str], **kw: Any) -> list[MemoryItem]:
        existing = self.find("reflection", key)
        if existing:
            if existing.content != content or set(sources) - set(existing.evidence):
                self.update(existing.memory_id, "reinforced", "Reflection re-derived with new evidence.",
                            content=content, confidence=existing.confidence + 0.05,
                            evidence=sorted(set(existing.evidence) | set(sources)))
            return []
        return [self.create("reflection", content, reason="Synthesised from " + ", ".join(sources) + ".",
                            evidence=sources, data={"key": key}, **kw)]

    # ------------------------------------------------------------------ retrieval
    def retrieve(self, retrieval_id: str, query: str, focus_tags: list[str]) -> MemoryRetrieval:
        q_terms = terms(query)
        hits: list[RetrievalHit] = []
        for m in self.items.values():
            if not m.active or m.memory_type == "working":
                continue
            tag_match = 0.5 if any(t and t in m.tags for t in focus_tags) else 0.0
            relevance = clamp(overlap(q_terms, " ".join([m.content, *m.tags])) + tag_match)
            recency = self.recency(m)
            score = (RETRIEVAL_WEIGHTS["relevance"] * relevance + RETRIEVAL_WEIGHTS["importance"] * m.importance
                     + RETRIEVAL_WEIGHTS["recency"] * recency)
            hits.append(RetrievalHit(id=m.memory_id, score=round(score, 4),
                                     components={"relevance": round(relevance, 3), "importance": round(m.importance, 3),
                                                 "recency": recency}))
        hits.sort(key=lambda h: (-h.score, h.id))
        for i, h in enumerate(hits):
            if i < RETRIEVAL_TOP_K and h.score >= RETRIEVAL_MIN_SCORE:
                h.selected = True
                h.reason = "top-ranked"
                item = self.items[h.id]
                item.retrieval_score = h.score
                self.update(h.id, "retrieved", f"Retrieved by {retrieval_id} (score {h.score:.2f}).",
                            retrieval_count=item.retrieval_count + 1, last_accessed_elapsed=self.elapsed)
            else:
                h.reason = "below threshold" if h.score < RETRIEVAL_MIN_SCORE else "outside top-k"
        return MemoryRetrieval(retrieval_id=retrieval_id, query=query, query_terms=q_terms,
                               weights=RETRIEVAL_WEIGHTS, hits=hits[:12])

    def mark_used(self, memory_ids: list[str], decision_id: str) -> None:
        for mid in memory_ids:
            item = self.items.get(mid)
            if item and decision_id not in item.used_in_decisions:
                item.used_in_decisions.append(decision_id)
