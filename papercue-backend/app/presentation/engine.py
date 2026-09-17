"""Simulation engine: replays one session event by event through the full pipeline.

    event ─► context ─► outcome check ─► memory ─► user model ─► issue ranking
          ─► knowledge + memory retrieval ─► Judge ─► prompt ─► mobile delivery ─► StepRecord

Every stage is timed and isolated: an exception is recorded on the step and a safe default
is used, so one failure never stops the run. The run is deterministic for a given seed and
configuration, which is also how seeking works (reset and replay).
"""

from __future__ import annotations

import time
import traceback
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from app.core.logging import get_logger
from app.presentation import outcome as outcome_rules
from app.presentation.context import ContextTracker
from app.presentation.ids import IdFactory
from app.presentation.inputs import DatasetEventSource
from app.presentation.judge import Judge, JudgeInput, RuleBasedJudge, rank_issues
from app.presentation.knowledge import KnowledgeQuery, KnowledgeRetriever, knowledge_support, mark_usage
from app.presentation.memory import MemoryStore
from app.presentation.prompt_generator import PromptGenerator
from app.presentation.schemas import (
    GeneratedPrompt, JudgeDecision, KnowledgeBase, MobileState, PresentationContext, PresentationSession,
    PresenterProfile, StageResult, StepRecord, TimelineMark,
)
from app.presentation.user_model import PresenterUserModel

log = get_logger("presentation.engine")


@dataclass(frozen=True)
class RunConfig:
    seed: int
    judge_provider: str
    prompt_provider: str
    retriever: str
    judge_noise: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, "judge_provider": self.judge_provider, "prompt_provider": self.prompt_provider,
                "retriever": self.retriever, "judge_noise": self.judge_noise}


class SimulationRun:
    def __init__(self, run_id: str, session: PresentationSession, kb: KnowledgeBase, presenter: PresenterProfile,
                 config: RunConfig, judge: Judge, retriever: KnowledgeRetriever, prompt_provider=None):
        self.run_id = run_id
        self.session = session
        self.kb = kb
        self.presenter = presenter
        self.config = config
        self.judge = judge
        self.fallback_judge = RuleBasedJudge(seed=config.seed, noise=config.judge_noise)
        self.retriever = retriever
        self.prompt_provider = prompt_provider
        self.source = DatasetEventSource(session)
        self.playback = {"playing": False, "speed": 1.0, "wall_clock": 0.0}
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        self.ids = IdFactory()
        self.tracker = ContextTracker(self.session.meta, self.kb, self.presenter, self.ids)
        self.memory = MemoryStore(self.presenter, self.ids)
        self.user_model = PresenterUserModel(self.presenter, self.ids)
        self.generator = PromptGenerator(self.ids, self.prompt_provider)
        self.memory.seed_priors()
        self.user_model.seed_priors()
        self.initial = {
            "memory_changes": [c.model_dump() for c in self.memory.drain_changes()],
            "user_model_changes": [c.model_dump() for c in self.user_model.drain_changes()],
            "memory_snapshot": self.memory.snapshot(),
            "user_model_snapshot": self.user_model.snapshot(),
        }
        self.steps: list[StepRecord] = []
        self.prompts: list[GeneratedPrompt] = []
        self.delivered: list[GeneratedPrompt] = []
        self.issue_age: Counter = Counter()
        self.ctx = PresentationContext(session_id=self.session.meta.session_id, language=self.session.meta.language)
        self.mobile = MobileState(run_id=self.run_id, session_id=self.session.meta.session_id,
                                  elapsed_time=self.session.meta.excerpt_start_elapsed,
                                  language=self.session.meta.language, is_mock=self.is_mock)
        self.playback["playing"] = False
        self.logged = False

    @property
    def is_mock(self) -> bool:
        return bool(getattr(self.judge, "is_mock", True)) and self.prompt_provider is None

    @property
    def cursor(self) -> int:
        return len(self.steps)

    @property
    def total(self) -> int:
        return len(self.source)

    @property
    def finished(self) -> bool:
        return self.cursor >= self.total

    def run_to(self, index: int) -> None:
        index = max(0, min(index, self.total))
        if index < self.cursor:
            self.reset()
        while self.cursor < index:
            self.step()

    # ------------------------------------------------------------------ one step
    def step(self) -> StepRecord | None:
        if self.finished:
            return None
        idx = self.cursor
        step_no = idx + 1
        event = self.source.get(idx)
        event.previous_interventions = [
            {"prompt_id": p.prompt_id, "prompt_type": p.prompt_type, "issue": p.issue_type, "elapsed": p.created_elapsed,
             "outcome": p.outcome} for p in self.delivered]
        stages: list[StageResult] = []
        marks: list[TimelineMark] = []

        @contextmanager
        def stage(name: str) -> Iterator[StageResult]:
            result = StageResult(name=name)
            t0 = time.perf_counter()
            try:
                yield result
            except Exception as exc:  # noqa: BLE001 - keep the simulation alive
                result.status = "error"
                result.error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("stage %s failed at %s: %s", name, event.event_id, type(exc).__name__)
                log.debug("%s", traceback.format_exc())
            finally:
                result.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                stages.append(result)

        self.memory.begin_step(step_no, event)
        self.user_model.begin_step(step_no, event)
        prev_slide = self.ctx.current_slide
        lane = {"speech": "speech", "qa_answer": "speech", "silence": "speech", "audience_question": "speech"}[event.event_type]
        marks.append(TimelineMark(lane=lane, elapsed=event.elapsed_time, step=step_no, kind=event.event_type,
                                  label=event.event_type, ref=event.event_id))
        if event.current_slide != prev_slide:
            marks.append(TimelineMark(lane="slide", elapsed=event.elapsed_time, step=step_no, kind="slide_change",
                                      label=f"#{event.current_slide} {event.slide_title}", ref=str(event.current_slide)))

        # 1. presentation context
        with stage("context") as st:
            self.ctx = self.tracker.update(
                event, baseline_rate=float(self.user_model.value("baseline_speech_rate")),
                baseline_filler=float(self.user_model.value("baseline_filler_rate")),
                interventions=[{"prompt_type": p.prompt_type, "elapsed": p.created_elapsed, "issue": p.issue_type}
                               for p in self.delivered])
        if st.status == "error":  # minimal context so later stages still run
            self.ctx = self.ctx.model_copy(update={"event_id": event.event_id, "elapsed_time": event.elapsed_time,
                                                   "current_slide": event.current_slide, "active_issues": [],
                                                   "new_issue_ids": [], "resolved_issue_ids": []})
        for iid in self.ctx.new_issue_ids:
            issue = self.tracker.issue_by_id(iid)
            if issue:
                marks.append(TimelineMark(lane="issue", elapsed=event.elapsed_time, step=step_no, kind=issue.type,
                                          label=issue.type, ref=iid))
        for issue in self.ctx.active_issues:
            self.issue_age[issue.issue_id] += 1

        # 2. outcomes of earlier prompts
        outcome_updates: list[dict[str, Any]] = []
        with stage("outcome"):
            history = self.tracker.history
            for p in self.delivered:
                if p.outcome != "pending":
                    continue
                trigger = self.source.get(next(i for i, e in enumerate(self.session.events) if e.event_id == p.event_id))
                result = outcome_rules.evaluate(p, history, self.ctx, self.kb,
                                                float(self.user_model.value("baseline_speech_rate")),
                                                float(self.user_model.value("baseline_filler_rate")), trigger)
                if result is None:
                    continue
                p.outcome, detail = result
                p.outcome_detail = detail
                self.memory.record_outcome(p, p.outcome, detail)
                self.user_model.observe_outcome(p, p.outcome, detail)
                outcome_updates.append({"prompt_id": p.prompt_id, "outcome": p.outcome, **detail})
                marks.append(TimelineMark(lane="outcome", elapsed=event.elapsed_time, step=step_no, kind=p.outcome,
                                          label=p.outcome, ref=p.prompt_id))

        # 3. memory
        with stage("memory"):
            new_issues = [i for i in (self.tracker.issue_by_id(x) for x in self.ctx.new_issue_ids) if i]
            resolved = [i for i in (self.tracker.issue_by_id(x) for x in self.ctx.resolved_issue_ids) if i]
            counts = Counter(i.type for i in self.tracker.all_issues() if not i.signals.get("self_recovered"))
            self.memory.observe(event, self.ctx, new_issues, resolved, counts)
            self.memory.reflect(force=idx == self.total - 1)

        # 4. user model
        with stage("user_model"):
            self.user_model.observe(event, self.ctx, [i for i in (self.tracker.issue_by_id(x)
                                                                  for x in self.ctx.new_issue_ids) if i])

        # 5. retrieval
        top, considered = rank_issues(self.ctx, self.delivered)
        kr = None
        mr = None
        used_kn: list[str] = []
        ks = 0.5
        with stage("knowledge_retrieval"):
            kq = KnowledgeQuery(retrieval_id=self.ids.next("krq"), slide=event.current_slide,
                                slide_title=event.slide_title, transcript=event.transcript_chunk,
                                issue=top, question=(self.ctx.active_question or {}).get("text"))
            kr = self.retriever.retrieve(self.kb, kq)
            used_kn = mark_usage(kr, self.kb, top)
            ks = knowledge_support(kr, used_kn, top)
            marks.append(TimelineMark(lane="retrieval", elapsed=event.elapsed_time, step=step_no, kind="knowledge",
                                      label=f"{sum(h.selected for h in kr.hits)} hits / {len(used_kn)} used",
                                      ref=kr.retrieval_id))
        if top is not None:
            with stage("memory_retrieval"):
                query = " ".join(filter(None, [top.type.replace("_", " "), top.target_label, event.slide_title]))
                mr = self.memory.retrieve(self.ids.next("mrq"), query, [top.type, top.target or ""])

        # 6. judge
        decision_id = self.ids.next("jdg")
        inp = JudgeInput(decision_id=decision_id, step=step_no, event=event, ctx=self.ctx, user_model=self.user_model,
                         memory_retrieval=mr, memories=self.memory.items, knowledge_retrieval=kr,
                         used_knowledge_ids=used_kn, knowledge_support=ks, prompts=self.delivered,
                         issue_age=dict(self.issue_age), top_issue=top, considered=considered)
        decision: JudgeDecision | None = None
        with stage("judge") as st:
            decision = self.judge.decide(inp)
        if decision is None:
            try:
                decision = self.fallback_judge.decide(inp)
            except Exception:  # noqa: BLE001 - last resort: stay silent
                decision = JudgeDecision(decision_id=decision_id, step=step_no, event_id=event.event_id,
                                         decision="DO_NOT_INTERVENE",
                                         reason_for_final_decision="Judge unavailable; staying silent.")
            decision.fallback_used = True
            decision.fallback_reason = st.error
        self.memory.mark_used(decision.used_memory_ids, decision_id)
        marks.append(TimelineMark(lane="judge", elapsed=event.elapsed_time, step=step_no, kind=decision.decision,
                                  label=decision.decision, ref=decision_id))

        # 7. prompt generation and delivery
        prompt: GeneratedPrompt | None = None
        if decision.decision in ("INTERVENE_NOW", "SUPPRESS_DUE_TO_RECENT_INTERVENTION"):
            with stage("prompt_generation"):
                prompt = self.generator.generate(decision=decision, issue=top, ctx=self.ctx, kb=self.kb, event=event,
                                                 user_model=self.user_model, step=step_no)
            if prompt is not None:
                self._deliver(prompt, decision, event.elapsed_time)
                self.prompts.append(prompt)
                marks.append(TimelineMark(
                    lane="intervention", elapsed=event.elapsed_time, step=step_no,
                    kind="delivered" if prompt.delivered else "suppressed", label=prompt.text, ref=prompt.prompt_id))
        self._refresh_mobile(event.elapsed_time)

        mem_changes = self.memory.drain_changes()
        if mem_changes:
            marks.append(TimelineMark(lane="memory", elapsed=event.elapsed_time, step=step_no, kind="memory",
                                      label=", ".join(f"{k}×{v}" for k, v in Counter(c.op for c in mem_changes).items())))
        record = StepRecord(
            step=step_no, event_id=event.event_id, elapsed_time=event.elapsed_time, event=event,
            context=self.ctx, memory_changes=mem_changes, memory_snapshot=self.memory.snapshot(),
            memory_retrieval=mr, user_model_changes=self.user_model.drain_changes(),
            user_model_snapshot=self.user_model.snapshot(), knowledge_retrieval=kr, decision=decision,
            prompt=prompt.model_copy(deep=True) if prompt else None, outcome_updates=outcome_updates,
            mobile=self.mobile.model_copy(), stages=stages, timeline=marks,
        )
        self.steps.append(record)
        return record

    def _deliver(self, prompt: GeneratedPrompt, decision: JudgeDecision, elapsed: float) -> None:
        if decision.decision != "INTERVENE_NOW":
            prompt.delivery_status = "withheld"
            prompt.not_delivered_reason = decision.reason_for_final_decision
            return
        showing = self.mobile.prompt_id and (self.mobile.expires_at_elapsed or 0) > elapsed
        current = next((p for p in self.delivered if p.prompt_id == self.mobile.prompt_id), None)
        if showing and current and current.priority_score > prompt.priority_score:
            prompt.delivery_status = "withheld"
            prompt.not_delivered_reason = f"A higher-priority prompt ({current.prompt_id}) is still on screen."
            return
        prompt.delivered = True
        prompt.delivery_status = "delivered"
        self.delivered.append(prompt)
        self.memory.record_intervention(prompt, decision)
        self.mobile = self.mobile.model_copy(update={
            "prompt_id": prompt.prompt_id, "text": prompt.text, "language": prompt.language,
            "prompt_type": prompt.prompt_type, "priority": prompt.priority, "urgency": prompt.urgency,
            "expires_at_elapsed": prompt.expires_at_elapsed, "display_seconds": prompt.display_seconds,
        })

    def _refresh_mobile(self, elapsed: float) -> None:
        update: dict[str, Any] = {"elapsed_time": elapsed, "finished": self.cursor + 1 >= self.total}
        if self.mobile.prompt_id and (self.mobile.expires_at_elapsed or 0) <= elapsed:
            update.update(prompt_id=None, text=None, prompt_type=None, priority=None, urgency=None,
                          expires_at_elapsed=None, display_seconds=None, remaining_seconds=None)
        elif self.mobile.prompt_id:
            update["remaining_seconds"] = round((self.mobile.expires_at_elapsed or 0) - elapsed, 1)
        self.mobile = self.mobile.model_copy(update=update)

    # ------------------------------------------------------------------ views
    def issues(self) -> list[dict[str, Any]]:
        return [i.model_dump() for i in self.tracker.all_issues()]

    def summary(self) -> dict[str, Any]:
        decisions = Counter(s.decision.decision for s in self.steps)
        return {
            "run_id": self.run_id, "session_id": self.session.meta.session_id, "cursor": self.cursor,
            "total": self.total, "finished": self.finished, "config": self.config.as_dict(), "is_mock": self.is_mock,
            "judge": getattr(self.judge, "name", "unknown"), "retriever": getattr(self.retriever, "name", "unknown"),
            "prompt_generator": self.generator.name, "decisions": dict(decisions),
            "delivered_prompts": len(self.delivered),
            "fallbacks": sum(1 for s in self.steps if s.decision.fallback_used)
            + sum(1 for s in self.steps if s.prompt and s.prompt.fallback_used),
            "stage_errors": sum(1 for s in self.steps for st in s.stages if st.status == "error"),
            "playback": dict(self.playback),
        }
