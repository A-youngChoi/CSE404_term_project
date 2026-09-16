"""Pipeline orchestration: the sense -> predict -> adapt loop.

process_turn  (sense + model update)
    input turn -> recent context -> dialogue-act/state analysis -> evidence extraction
    -> audience-update proposal -> deterministic application
    -> (shadow) retrieval + intervention scoring, never generating or delivering a cue

request_cue   (predict + adapt)
    current state + beliefs -> retrieval -> intervention decision
    -> cue wording (local LLM / mock) -> deterministic safety & grounding checks -> result

Every run stores a PipelineTrace whose stages hold IDs, statuses, scores and short
rationales (no raw utterances, prompts, or model reasoning).
"""

from __future__ import annotations

import re

from app.core.config import Settings
from app.core.context import current_session_id
from app.core.errors import InvalidInputError, InvalidSessionStateError, LocalConfigurationError, PaperCueError
from app.core.logging import get_logger
from app.core.utils import to_utc_iso, utcnow_iso, word_count
from app.llm.base import LocalLLMProvider
from app.llm.factory import ProviderRegistry
from app.models.audience import EvidenceItem
from app.models.conversation import ConversationState, Turn, TurnIn, TurnResult
from app.models.cue import MOCK_NOTICE, CueDecision, CueResponse, CueTrace, GeneratedCue, RetrievedUnit
from app.models.enums import DIMENSION_VALUES, SessionMode, Speaker
from app.models.llm_outputs import AudienceUpdateProposalOutput
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.cues import CueRepository, TraceRepository
from app.repositories.papers import PaperRepository
from app.repositories.sessions import SessionRepository
from app.safety.cue_filter import STATUS_DELIVERABLE, STATUS_MODEL_ERROR, STATUS_NOT_NEEDED
from app.safety.sensitive import sensitive_hits
from app.services.audience_model import AudienceModelService
from app.services.conversation_tracker import ConversationTracker
from app.services.cue_decision import CueDecisionEngine
from app.services.cue_generator import CueGenerator
from app.services.embeddings import Embedder
from app.services.evidence_extractor import EvidenceExtractor
from app.services.retrieval import RetrievalQuery, RetrievalService, build_query
from app.services.session_service import SessionService
from app.services.tracing import TraceBuilder, llm_component, prompt_version

log = get_logger("pipeline")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_turn_text(text: str, limit: int) -> str:
    text = _CONTROL_RE.sub("", text or "").strip()
    if not text:
        raise InvalidInputError("Turn text is empty.")
    if len(text) > limit:
        raise InvalidInputError(f"Turn text exceeds {limit} characters.")
    return text


def state_without_text(state: ConversationState) -> dict:
    return state.model_dump(mode="json", exclude={"explicit_question"})


def unit_brief(units: list[RetrievedUnit]) -> list[dict]:
    return [{"unit_id": u.unit_id, "unit_type": u.unit_type, "title": u.title, "score": u.score,
             "similarity": u.similarity} for u in units]


class PipelineService:
    def __init__(self, settings: Settings, providers: ProviderRegistry, embedder: Embedder,
                 sessions: SessionRepository, session_service: SessionService, papers: PaperRepository,
                 conversation: ConversationRepository, audience_repo: AudienceRepository, cues: CueRepository,
                 traces: TraceRepository, tracker: ConversationTracker, extractor: EvidenceExtractor,
                 audience: AudienceModelService, retrieval: RetrievalService, decision: CueDecisionEngine,
                 generator: CueGenerator):
        self.settings = settings
        self.providers = providers
        self.embedder = embedder
        self.sessions = sessions
        self.session_service = session_service
        self.papers = papers
        self.conversation = conversation
        self.audience_repo = audience_repo
        self.cues = cues
        self.traces = traces
        self.tracker = tracker
        self.extractor = extractor
        self.audience = audience
        self.retrieval = retrieval
        self.decision = decision
        self.generator = generator

    # ================================================================ turns
    def process_turn(self, session_id: str, req: TurnIn, input_source: str = "text") -> TurnResult:
        session = self.session_service.require(session_id, for_ingestion=True)
        text = clean_turn_text(req.text, self.settings.max_turn_chars)
        provider = self.providers.get(session["llm_provider"])
        token = current_session_id.set(session_id)
        spoken_at = to_utc_iso(req.timestamp) if req.timestamp else utcnow_iso()
        turn = self.conversation.add_turn(session_id, req.speaker.value, text, spoken_at, input_source)
        tb = TraceBuilder(session_id, turn.id, "turn")
        accepted: list[EvidenceItem] = []
        rejected: list[dict] = []
        changes: list[dict] = []
        state = ConversationState()
        try:
            state, accepted, rejected, changes = self._run_turn(tb, session, provider, turn)
            self.conversation.set_status(turn.id, "ok")
        except PaperCueError as exc:
            self.conversation.set_status(turn.id, "failed")
            self.traces.add_error(session_id, tb.stages[-1].stage_name if tb.stages else "turn", exc.code, exc.message)
            log.warning("session=%s turn=%s analysis failed code=%s", session_id, turn.id, exc.code)
            raise
        finally:
            self.traces.save(tb.build())
            self.sessions.touch(session_id, turn.turn_index)
            current_session_id.reset(token)
        log.info("session=%s turn=%s speaker=%s evidence=%d changes=%d", session_id, turn.id, turn.speaker.value,
                 len(accepted), len(changes))
        return TurnResult(
            turn=self.conversation.get_turn(turn.id), analysis_status="ok", state=state,
            evidence_accepted=[e.model_dump(mode="json") for e in accepted], evidence_rejected=rejected,
            belief_changes=changes, latency_ms=tb.latencies(), llm_provider=provider.name,
            mock_mode=provider.is_mock,
        )

    def _run_turn(self, tb: TraceBuilder, session: dict, provider: LocalLLMProvider, turn: Turn):
        sid, paper_id = session["id"], session["paper_id"]
        is_listener = turn.speaker == Speaker.listener
        comp = llm_component(provider)

        with tb.stage("input_turn", "input", inputs=[turn.id]) as st:
            st.output_reference_ids = [turn.id]
            st.output = {"speaker": turn.speaker.value, "turn_index": turn.turn_index,
                         "word_count": word_count(turn.text), "input_source": turn.input_source}
            st.rationale_code = "turn_received"
            st.short_rationale = "Typed turn stored locally."

        prev = self.conversation.latest_state(sid)
        with tb.stage("recent_context", "database") as st:
            window = self.tracker.recent_window(sid)
            older = self.tracker.older_summary(sid)
            st.input_reference_ids = [t.id for t in window]
            st.output = {"window_size": self.settings.recent_turn_window,
                         "window_turn_indices": [t.turn_index for t in window],
                         "summarized_turns": older.summarized_turns, "older_topics": older.topics_covered}
            st.rationale_code = "context_window"
            st.short_rationale = "Bounded recent window plus structured summary of older turns."

        with tb.stage("dialogue_act_analysis", comp, model=provider.name,
                      prompt=prompt_version("conversation_state"), inputs=[turn.id]) as st:
            try:
                candidates = self.retrieval.search(paper_id, RetrievalQuery(latest_question=turn.text), top_k=3)
            except InvalidSessionStateError:
                candidates = []
                st.validation_errors.append("paper_not_indexed")
            out = self.tracker.classify(provider, prev, turn, candidates)
            state, errors = self.tracker.merge(prev, turn, out, candidates)
            st.validation_errors += errors
            st.output = {
                "phase_before": prev.phase.value, "phase_after": state.phase.value,
                "topic_before": prev.topic, "topic_after": state.topic,
                "listener_act": state.listener_act.value if is_listener else None,
                "presenter_act": state.presenter_act.value if not is_listener else None,
                "has_explicit_question": bool(state.explicit_question) and state.explicit_question_turn_id == turn.id,
                "detected_concern": out.detected_concern if is_listener else None,
                "unresolved_issues": [i.model_dump() for i in state.unresolved_issues],
                "possible_misunderstanding": state.possible_misunderstanding,
                "explained_unit_ids": state.recently_explained_unit_ids,
                "candidate_topic_unit_ids": [c.unit_id for c in candidates],
            }
            st.output_reference_ids = [c.unit_id for c in candidates]
            st.rationale_code = f"act_{state.listener_act.value}" if is_listener else f"presenter_{state.presenter_act.value}"
            st.short_rationale = "Latest turn classified; state merged by deterministic rules."

        accepted: list[EvidenceItem] = []
        rejected: list[dict] = []
        changes: list[dict] = []
        if is_listener:
            with tb.stage("evidence_extraction", comp, model=provider.name,
                          prompt=prompt_version("evidence_extraction"), inputs=[turn.id]) as st:
                terms = sorted({k for u in self.papers.list_units(paper_id) for k in u.keywords})
                proposal = self.extractor.propose(provider, turn, state, terms)
                result = self.extractor.store(provider.name, turn, proposal)
                accepted, rejected = result.accepted, result.rejected
                st.output_reference_ids = [e.id for e in accepted]
                st.validation_errors = [r["code"] for r in rejected]
                st.status = "partial" if rejected and accepted else ("success" if not rejected else "partial")
                st.output = {
                    "proposed": result.proposed, "accepted": len(accepted), "rejected": rejected,
                    "items": [{"evidence_id": e.id, "dimension": e.dimension.value, "key": e.key, "value": e.value,
                               "evidence_type": e.evidence_type.value, "confidence": e.confidence} for e in accepted],
                }
                st.rationale_code = "evidence_found" if accepted else "no_evidence"
                st.short_rationale = f"{len(accepted)} evidence item(s) accepted, {len(rejected)} rejected."

            proposals = self._propose_updates(tb, provider, sid, accepted)

            with tb.stage("audience_update_applied", "deterministic",
                          inputs=[e.id for e in accepted]) as st:
                listener_turns = state.listener_turn_count
                for ev, key, value, reason in proposals:
                    change = self.audience.apply(ev, key=key, value=value, reason=reason, listener_turn=listener_turns)
                    if change:
                        changes.append({**change.model_dump(mode="json"), "evidence_type": ev.evidence_type.value,
                                        "turn_index": turn.turn_index})
                st.output_reference_ids = [c["belief_id"] for c in changes]
                st.output = {"changes": [{k: c[k] for k in ("belief_id", "dimension", "key", "change_type", "old_value",
                                                            "new_value", "old_confidence", "new_confidence",
                                                            "evidence_id", "evidence_type", "source_type")}
                                        for c in changes]}
                st.rationale_code = "beliefs_updated" if changes else "no_belief_change"
                st.short_rationale = f"{len(changes)} belief change(s) applied by the update policy."
        else:
            for name in ("evidence_extraction", "audience_update_proposal", "audience_update_applied"):
                tb.skip(name, "deterministic", "presenter_turn_no_evidence",
                        "Evidence is only extracted from listener turns.")

        self.conversation.save_state(sid, turn.id, state)
        self.tracker.summarize_old_turns(sid)

        if is_listener and self.settings.shadow_decision_on_turn:
            self._shadow_decision(tb, session, state, turn)
        else:
            code = "presenter_turn_no_decision" if not is_listener else "shadow_disabled"
            for name in ("retrieval_query", "retrieval", "intervention_decision"):
                tb.skip(name, "deterministic", code, "No intervention evaluation for this turn.")
        for name in ("cue_candidate", "safety_grounding_check", "final_result"):
            tb.skip(name, "deterministic", "no_cue_requested",
                    "No cue was requested for this turn; cues are generated only on demand.")
        return state, accepted, rejected, changes

    def _propose_updates(self, tb: TraceBuilder, provider: LocalLLMProvider, sid: str,
                         accepted: list[EvidenceItem]) -> list[tuple[EvidenceItem, str, str, str]]:
        use_llm = self.settings.audience_update_strategy == "llm" and accepted
        comp = llm_component(provider) if use_llm else "deterministic"
        with tb.stage("audience_update_proposal", comp, model=provider.name if use_llm else None,
                      prompt=prompt_version("audience_update") if use_llm else None,
                      inputs=[e.id for e in accepted]) as st:
            proposals: dict[int, tuple[str, str]] = {}
            if use_llm:
                beliefs = self.audience_repo.list_beliefs(sid)
                payload = {
                    "current_beliefs": [{"dimension": b["dimension"], "key": b["key"], "value": b["value"],
                                         "confidence": b["confidence"]} for b in beliefs],
                    "new_evidence": [{"index": i, "dimension": e.dimension.value, "key": e.key, "value": e.value,
                                      "evidence_type": e.evidence_type.value, "observation": e.observation}
                                     for i, e in enumerate(accepted)],
                    "allowed_values": {d.value: list(v) for d, v in DIMENSION_VALUES.items()},
                }
                out = provider.generate_structured("audience_update", payload, AudienceUpdateProposalOutput)
                for up in out.updates:
                    code = None
                    if up.evidence_index >= len(accepted):
                        code = "unknown_evidence_index"
                    elif up.dimension != accepted[up.evidence_index].dimension:
                        code = "dimension_mismatch"
                    elif up.value != accepted[up.evidence_index].value:
                        code = "value_mismatch"
                    elif sensitive_hits(up.key) or sensitive_hits(up.reason):
                        code = "sensitive_content"
                    elif up.evidence_index in proposals:
                        code = "duplicate_proposal"
                    if code:
                        st.validation_errors.append(code)
                        continue
                    proposals[up.evidence_index] = (" ".join(up.key.lower().split())[:60], up.reason[:160])
            result = []
            for i, ev in enumerate(accepted):
                if i not in proposals:
                    if use_llm:
                        st.validation_errors.append("no_proposal_default_applied")
                    proposals[i] = (ev.key, ev.observation)
                key, reason = proposals[i]
                result.append((ev, key, ev.value, reason))
            st.output = {"strategy": "llm" if use_llm else "rules",
                         "proposals": [{"evidence_id": ev.id, "dimension": ev.dimension.value, "key": key,
                                        "value": value} for ev, key, value, _ in result]}
            st.rationale_code = "update_proposed" if result else "nothing_to_propose"
            st.short_rationale = "The model may only map evidence to belief keys; values and confidence are fixed by code."
            return result

    def _shadow_decision(self, tb: TraceBuilder, session: dict, state: ConversationState, turn: Turn) -> None:
        sid = session["id"]
        beliefs = self.audience.beliefs(sid, state.listener_turn_count)
        with tb.stage("retrieval_query", "deterministic") as st:
            query = build_query(state, beliefs, turn.text)
            st.output = query.summary()
            st.rationale_code = "query_built"
            st.short_rationale = "Query combines the question, topic, open issues and concerns."
        with tb.stage("retrieval", "local_embedding", model=self.embedder.name) as st:
            try:
                retrieved = self.retrieval.search(session["paper_id"], query)
            except InvalidSessionStateError:
                retrieved = []
                st.validation_errors.append("paper_not_indexed")
            st.output = {"units": unit_brief(retrieved)}
            st.output_reference_ids = [u.unit_id for u in retrieved]
            st.rationale_code = "units_found" if retrieved else "no_units_found"
            st.short_rationale = f"{len(retrieved)} paper unit(s) above the similarity threshold."
        with tb.stage("intervention_decision", "deterministic",
                      inputs=[b.id for b in beliefs]) as st:
            decision = self.decision.decide(SessionMode.auto_candidate, state, beliefs, retrieved,
                                            self._recent_decisions(sid))
            st.output = {**self._decision_output(decision), "evaluation": "shadow"}
            st.output_reference_ids = decision.beliefs_used
            st.rationale_code = decision.reason_code
            st.short_rationale = "Shadow evaluation only: " + decision.short_reason

    # ================================================================ cues
    def _recent_decisions(self, sid: str) -> list[tuple[str, str | None]]:
        return [(d["action"], d["target"]) for d in self.cues.list_with_cues(sid)
                if d.get("generated") and d["generated"]["delivered"]]

    @staticmethod
    def _decision_output(decision: CueDecision) -> dict:
        return {"should_intervene": decision.should_intervene,
                "action": decision.action.value if decision.action else None, "target": decision.target,
                "confidence": decision.confidence, "scores": decision.scores.model_dump(),
                "reason_code": decision.reason_code, "reason_params": decision.reason_params}

    def _store_model_error(self, sid: str, trigger: Turn, mode: SessionMode, decision: CueDecision,
                           state: ConversationState, tb: TraceBuilder) -> None:
        self.cues.add_decision(
            decision_id=decision.id, session_id=sid, trigger_turn_id=trigger.id, mode=mode.value,
            should_intervene=decision.should_intervene, action=decision.action.value if decision.action else None,
            target=decision.target, confidence=decision.confidence, short_reason=decision.short_reason,
            reason_code=decision.reason_code, final_status=STATUS_MODEL_ERROR, scores=decision.scores.model_dump(),
            trace={"trigger_turn_id": trigger.id, "conversation_state": state_without_text(state)},
            latency=tb.latencies(),
        )

    def request_cue(self, session_id: str, mode: SessionMode) -> CueResponse:
        session = self.session_service.require(session_id, for_ingestion=True)
        provider = self.providers.get(session["llm_provider"])
        state = self.conversation.latest_state(session_id)
        trigger_id = state.last_listener_turn_id
        if trigger_id is None:
            raise InvalidSessionStateError("A cue needs at least one listener turn in this session.")
        trigger = self.conversation.get_turn(trigger_id)
        token = current_session_id.set(session_id)
        tb = TraceBuilder(session_id, trigger_id, "cue" if mode == SessionMode.on_demand else "auto_candidate")
        try:
            return self._run_cue(tb, session, provider, state, trigger, mode)
        except PaperCueError as exc:
            self.traces.add_error(session_id, tb.stages[-1].stage_name if tb.stages else "cue", exc.code, exc.message)
            raise
        finally:
            self.traces.save(tb.build())
            current_session_id.reset(token)

    def _run_cue(self, tb: TraceBuilder, session: dict, provider: LocalLLMProvider, state: ConversationState,
                 trigger: Turn, mode: SessionMode) -> CueResponse:
        sid, paper_id = session["id"], session["paper_id"]
        paper_row = self.papers.get_row(paper_id)
        paper_meta = self.papers.to_model(paper_row).model_dump()

        with tb.stage("input_turn", "database", inputs=[trigger.id]) as st:
            st.output = {"trigger_turn_index": trigger.turn_index, "mode": mode.value}
            st.output_reference_ids = [trigger.id]
            st.rationale_code = "cue_request_trigger"
            st.short_rationale = "The latest listener turn triggers the cue request."
        with tb.stage("recent_context", "database") as st:
            window = self.tracker.recent_window(sid)
            st.input_reference_ids = [t.id for t in window]
            st.output = {"window_turn_indices": [t.turn_index for t in window],
                         "summarized_turns": self.tracker.older_summary(sid).summarized_turns}
            st.rationale_code = "context_window"
        with tb.stage("dialogue_act_analysis", "database") as st:
            st.output = {"phase_after": state.phase.value, "topic_after": state.topic,
                         "listener_act": state.listener_act.value,
                         "unresolved_issues": [i.model_dump() for i in state.unresolved_issues],
                         "possible_misunderstanding": state.possible_misunderstanding,
                         "turns_since_last_cue": state.turns_since_last_cue}
            st.rationale_code = "state_loaded"
            st.short_rationale = "Current conversation state loaded (computed when the turn was entered)."
        trigger_evidence = self.audience_repo.list_evidence(sid, trigger.id)
        with tb.stage("evidence_extraction", "database", inputs=[trigger.id]) as st:
            st.output_reference_ids = [e.id for e in trigger_evidence]
            st.output = {"items": [{"evidence_id": e.id, "dimension": e.dimension.value, "key": e.key,
                                    "value": e.value, "evidence_type": e.evidence_type.value}
                                   for e in trigger_evidence]}
            st.rationale_code = "evidence_loaded"
        tb.skip("audience_update_proposal", "deterministic", "reused_turn_trace",
                "Belief updates were applied when the turn was entered; see the turn trace.")
        beliefs = self.audience.beliefs(sid, state.listener_turn_count)
        with tb.stage("audience_update_applied", "database") as st:
            st.output_reference_ids = [b.id for b in beliefs]
            st.output = {"beliefs": [{"belief_id": b.id, "dimension": b.dimension.value, "key": b.key,
                                      "value": b.value, "effective_confidence": b.effective_confidence,
                                      "lifecycle": b.lifecycle} for b in beliefs]}
            st.rationale_code = "beliefs_loaded"

        with tb.stage("retrieval_query", "deterministic") as st:
            query = build_query(state, beliefs, trigger.text)
            st.output = query.summary()
            st.rationale_code = "query_built"
        with tb.stage("retrieval", "local_embedding", model=self.embedder.name) as st:
            retrieved = self.retrieval.search(paper_id, query)
            memory = self.tracker.search_memory(sid, query.text())
            st.output = {"units": unit_brief(retrieved), "memory_hits": [m.model_dump() for m in memory]}
            st.output_reference_ids = [u.unit_id for u in retrieved]
            st.rationale_code = "units_found" if retrieved else "no_units_found"
        with tb.stage("intervention_decision", "deterministic", inputs=[b.id for b in beliefs]) as st:
            decision = self.decision.decide(mode, state, beliefs, retrieved, self._recent_decisions(sid))
            tb.decision_id = decision.id
            st.output = self._decision_output(decision)
            st.output_reference_ids = decision.beliefs_used
            st.rationale_code = decision.reason_code
            st.short_rationale = decision.short_reason

        recent_cues = self.cues.recent_delivered_cues(sid, self.settings.recent_cue_memory)
        gen = None
        action_units: list[RetrievedUnit] = []
        final_status = STATUS_NOT_NEEDED
        final_cue = None
        candidate_output = None
        if decision.should_intervene:
            with tb.stage("cue_candidate", llm_component(provider), model=provider.name,
                          prompt=prompt_version("cue_generation")) as st:
                action_query = build_query(state, beliefs, trigger.text, decision.action, decision.target)
                action_units = self.retrieval.search(paper_id, action_query)
                units_by_id = self.papers.get_units([u.unit_id for u in action_units])
                units = [units_by_id[u.unit_id] for u in action_units if u.unit_id in units_by_id]
                ev_ids = list(dict.fromkeys(decision.evidence_used + [e.id for e in trigger_evidence]))[:6]
                all_ev = {e.id: e for e in self.audience_repo.list_evidence(sid)}
                gen_evidence = [all_ev[i] for i in ev_ids if i in all_ev]
                st.input_reference_ids = [u.id for u in units] + [e.id for e in gen_evidence]
                try:
                    gen = self.generator.generate(provider, decision, units, gen_evidence, set(all_ev), state,
                                                  recent_cues, paper_meta, session["cue_language"])
                except LocalConfigurationError:
                    # Keep an inspectable record of the failed request, then surface the 503.
                    self._store_model_error(sid, trigger, mode, decision, state, tb)
                    raise
                candidate_output = gen.candidate
                if gen.candidate:
                    st.output = {**gen.candidate.model_dump(mode="json"), "word_count": word_count(gen.candidate.cue)}
                    st.output_reference_ids = gen.candidate.grounding_unit_ids + gen.candidate.audience_evidence_ids
                    st.rationale_code = "candidate_generated"
                    st.short_rationale = gen.candidate.rationale
                else:
                    st.status = "failed"
                    st.validation_errors = gen.validation_errors
                    st.rationale_code = "invalid_model_json"
                    st.short_rationale = "The model output was not valid JSON after retries."
            with tb.stage("safety_grounding_check", "deterministic") as st:
                st.output = {"checks": [c.model_dump() for c in gen.checks],
                             "fallback_used": gen.fallback_used,
                             "fallback_checks": [c.model_dump() for c in gen.fallback_checks],
                             "status": gen.status}
                st.validation_errors = [c.name for c in gen.checks if not c.passed]
                st.status = "success" if not st.validation_errors else "partial"
                st.rationale_code = gen.status if not gen.fallback_used else "fallback_verification"
            final_status, final_cue = gen.status, gen.final_cue
        else:
            tb.skip("cue_candidate", "deterministic", "no_cue_needed", "The decision engine chose not to intervene.")
            tb.skip("safety_grounding_check", "deterministic", "no_cue_needed", "No candidate to check.")

        delivered = mode == SessionMode.on_demand and final_cue is not None and final_status == STATUS_DELIVERABLE
        with tb.stage("final_result", "deterministic") as st:
            st.output = {"status": final_status, "final_cue": final_cue, "delivered": delivered,
                         "evaluation_only": mode != SessionMode.on_demand,
                         "fallback_used": bool(gen and gen.fallback_used)}
            st.rationale_code = final_status
            st.short_rationale = "Cue delivered to the presenter." if delivered else "No cue delivered."

        used_output = gen.fallback if gen and gen.fallback_used else candidate_output
        cue_trace = CueTrace(
            trigger_turn_id=trigger.id, trigger_turn_text=trigger.text,
            conversation_state=state.model_dump(mode="json"),
            audience_evidence=[e.model_dump(mode="json") for e in trigger_evidence],
            beliefs_used=[b.model_dump(mode="json") for b in beliefs if b.id in decision.beliefs_used],
            retrieved_units=action_units or retrieved,
            earlier_context=[m.model_dump() for m in memory],
            latency_ms=tb.latencies(),
        )
        # Persist references only: no utterance text or evidence quotes are duplicated into the trace.
        stored_trace = cue_trace.model_dump(mode="json", exclude={"trigger_turn_text", "audience_evidence"})
        stored_trace["conversation_state"] = state_without_text(state)
        stored_trace["audience_evidence_ids"] = [e.id for e in trigger_evidence]
        stored_trace["reason_params"] = decision.reason_params
        stored_trace["decision_evidence_ids"] = decision.evidence_used
        stored_trace["earlier_context"] = [{k: v for k, v in m.items() if k != "gist"} for m in stored_trace["earlier_context"]]
        self.cues.add_decision(
            decision_id=decision.id, session_id=sid, trigger_turn_id=trigger.id, mode=mode.value,
            should_intervene=decision.should_intervene, action=decision.action.value if decision.action else None,
            target=decision.target, confidence=decision.confidence, short_reason=decision.short_reason,
            reason_code=decision.reason_code, final_status=final_status, scores=decision.scores.model_dump(),
            trace=stored_trace, latency=cue_trace.latency_ms,
        )
        generated = None
        if gen is not None:
            meta = self.cues.add_cue(
                session_id=sid, decision_id=decision.id, cue=final_cue,
                candidate_cue=candidate_output.cue if candidate_output else None,
                action=used_output.action.value if used_output else decision.action.value,
                grounding_unit_ids=used_output.grounding_unit_ids if used_output else [],
                audience_evidence_ids=used_output.audience_evidence_ids if used_output else [],
                confidence=used_output.confidence if used_output else 0.0,
                rationale=used_output.rationale if used_output else None,
                passed_filter=final_status == STATUS_DELIVERABLE,
                filter_results=[c.model_dump() for c in gen.checks + gen.fallback_checks],
                fallback_used=gen.fallback_used, delivered=delivered, final_status=final_status,
                generator=gen.generator,
            )
            generated = GeneratedCue(
                id=meta["id"], cue=final_cue, candidate_cue=candidate_output.cue if candidate_output else None,
                action=used_output.action if used_output else decision.action,
                grounding_unit_ids=used_output.grounding_unit_ids if used_output else [],
                audience_evidence_ids=used_output.audience_evidence_ids if used_output else [],
                confidence=used_output.confidence if used_output else 0.0,
                rationale=used_output.rationale if used_output else None,
                passed_filter=final_status == STATUS_DELIVERABLE, filter_results=gen.checks + gen.fallback_checks,
                fallback_used=gen.fallback_used, delivered=delivered, final_status=final_status,
                generator=gen.generator, created_at=meta["created_at"],
            )
        if delivered:
            new_state = state.model_copy(deep=True)
            new_state.recent_cues = (state.recent_cues + [final_cue])[-self.settings.recent_cue_memory:]
            new_state.turns_since_last_cue = 0
            self.conversation.save_state(sid, None, new_state)
        log.info("session=%s decision=%s status=%s delivered=%s", sid, decision.id, final_status, delivered)
        return CueResponse(
            trace_id=tb.trace_id, status=final_status, delivered=delivered,
            evaluation_only=mode != SessionMode.on_demand, cue=final_cue if delivered else None,
            decision=decision, generated=generated, trace=cue_trace, llm_provider=provider.name,
            mock_mode=provider.is_mock, mock_notice=MOCK_NOTICE if provider.is_mock else None,
        )
