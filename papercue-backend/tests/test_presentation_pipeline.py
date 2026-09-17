"""Presentation-support simulation: dataset, pipeline, memory, user model, Judge, prompts, evaluation."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.ollama import OllamaProvider
from app.presentation.evaluation import MATCH_TOLERANCE_S, aggregate, evaluate_steps
from app.presentation.judge import OllamaJudge, RuleBasedJudge
from app.presentation.schemas import DECISIONS, ObservedEvent
from app.presentation.service import PresentationService
from app.services.embeddings import HashingEmbedder

REQUIRED_SCENARIOS = {
    "stable", "missing_key_point", "pace_too_slow", "pace_too_fast", "filler_repetition", "long_silence",
    "audience_confusion", "qa_misunderstanding", "prompt_helped", "unnecessary_prompt", "suppress_after_support",
    "time_pressure",
}


@pytest.fixture
def svc(container) -> PresentationService:
    return container.presentation


def run_all(svc: PresentationService, session_id: str, **kw):
    run = svc.create_run(session_id, **kw)
    svc.seek(run.run_id, run.total)
    return run


def by_event(run, event_id):
    return next(s for s in run.steps if s.event_id == event_id)


# ----------------------------------------------------------------------------- dataset
def test_dataset_covers_required_scenarios_and_languages(svc):
    sessions = svc.dataset.sessions
    assert len(sessions) >= 10
    assert REQUIRED_SCENARIOS <= {s.meta.scenario_type for s in sessions.values()}
    assert {s.meta.language for s in sessions.values()} == {"ko", "en"}
    for s in sessions.values():
        assert len(s.events) >= 5
        assert [e.elapsed_time for e in s.events] == sorted(e.elapsed_time for e in s.events)
        assert set(s.ground_truth) == {e.event_id for e in s.events}
    e = sessions["S02_ko_missing_point"].events[0]
    assert e.slide_title == "방법: 기종별 보정"
    assert e.remaining_time == 540 - e.elapsed_time
    assert e.slide_expected_content and e.timestamp.startswith("2026-09-02T14:02")


def test_ground_truth_is_not_part_of_the_observed_event_or_llm_payload(svc):
    assert not {"ground_truth", "expected_intervention", "expected_prompt_type"} & set(ObservedEvent.model_fields)
    run = svc.create_run("S02_ko_missing_point")
    captured = {}

    class Recorder:
        name = "recorder"

        def generate_structured(self, task, payload, schema):
            captured.update(payload)
            raise RuntimeError("stop")

    run.judge = OllamaJudge(Recorder(), RuleBasedJudge())
    svc.seek(run.run_id, 5)
    text = json.dumps(captured, ensure_ascii=False)
    assert captured and "expected_intervention" not in text and "ground_truth" not in text
    assert "결과 해석에 필수" not in text  # a ground-truth note from S02_e05


# ----------------------------------------------------------------------------- pipeline
def test_missing_key_point_is_prompted_in_korean_and_recovery_updates_memory_and_user_model(svc):
    run = run_all(svc, "S02_ko_missing_point")
    wait = by_event(run, "S02_e04").decision
    assert wait.decision in ("WAIT_AND_OBSERVE", "DO_NOT_INTERVENE")
    step = by_event(run, "S02_e05")
    d = step.decision
    assert d.decision == "INTERVENE_NOW" and d.detected_issue == "missing_key_point" and d.target == "kp_sm_4a"
    assert d.supporting_reasons and d.counter_reasons is not None and d.reason_for_final_decision
    assert "mm_kp_sm_4a" in d.used_knowledge_ids
    assert {c.name for c in d.score_breakdown} >= {"severity", "urgency", "self_recovery_likelihood", "distraction_risk"}
    assert all(c.evidence_refs or c.name in ("knowledge_support",) for c in d.score_breakdown if c.value)
    prompt = next(p for p in run.prompts if p.decision_id == d.decision_id)
    assert prompt.delivered and prompt.language == "ko" and prompt.prompt_type == "content_reminder"
    assert "기준선" in prompt.text and len(prompt.alternatives) == 3
    assert sum(a.selected for a in prompt.alternatives) == 1
    assert prompt.outcome == "recovered" and prompt.outcome_detail["event_id"] == "S02_e06"
    assert step.mobile.text == prompt.text and step.mobile.remaining_seconds > 0

    mem = next(m for m in run.memory.items.values() if m.memory_type == "intervention")
    assert mem.data["outcome"] == "recovered" and "outcome: recovered" in mem.content
    ops = [c.op for c in run.memory.log if c.memory_id == mem.memory_id]
    assert ops[:2] == ["created", "reinforced"]
    resp = [c for c in run.user_model.history if c.attribute == "prompt_responsiveness"]
    assert resp[-1].new_value > resp[0].new_value and prompt.prompt_id in resp[-1].evidence_refs
    missed = run.user_model.attrs["often_missed_content"]
    assert "baseline_comparison" in missed.value and missed.confidence > 0.45  # prior confirmed
    assert any(m.memory_type == "reflection" for m in run.memory.items.values())


def test_every_memory_item_carries_metadata(svc):
    run = run_all(svc, "S09_en_prompt_helped")
    types = {m.memory_type for m in run.memory.items.values()}
    assert {"working", "episodic", "intervention", "reflection"} <= types
    for m in run.memory.items.values():
        assert m.memory_id and m.memory_type and m.reason and m.source_event_ids
        assert 0 <= m.importance <= 1 and 0 <= m.confidence <= 1
    retrieved = [c for c in run.memory.log if c.op == "retrieved"]
    assert retrieved and all(c.reason.startswith("Retrieved by mrq_") for c in retrieved)
    used = [m for m in run.memory.items.values() if m.used_in_decisions]
    assert used
    step = by_event(run, "S09_e07")
    assert step.memory_retrieval and any(h.selected for h in step.memory_retrieval.hits)


def test_stable_and_unnecessary_sessions_stay_silent(svc):
    for sid in ("S01_en_stable", "S10_ko_unnecessary_prompt"):
        run = run_all(svc, sid)
        assert run.delivered == []
    s10 = run_all(svc, "S10_ko_unnecessary_prompt")
    pause = by_event(s10, "S10_e02").decision
    assert pause.decision == "WAIT_AND_OBSERVE"
    rec = next(c for c in pause.score_breakdown if c.name == "self_recovery_likelihood")
    assert rec.value >= 0.8 and "um:tension_pattern" in rec.evidence_refs


def test_recent_intervention_suppresses_follow_up_prompts(svc):
    run = run_all(svc, "S04_ko_too_fast")
    assert by_event(run, "S04_e02").decision.decision == "INTERVENE_NOW"
    sup = by_event(run, "S04_e03")
    assert sup.decision.decision == "SUPPRESS_DUE_TO_RECENT_INTERVENTION"
    assert sup.decision.cooldown_state["in_cooldown"] is True
    assert sup.prompt is not None and not sup.prompt.delivered and sup.prompt.not_delivered_reason
    after = by_event(run, "S04_e04").decision
    assert after.decision == "INTERVENE_NOW" and after.target == "kp_sm_3a"

    s12 = run_all(svc, "S12_ko_time_shortage")
    types = [p.prompt_type for p in s12.delivered]
    assert types == ["time_management", "wrap_up"]
    assert s12.delivered[1].text == "마무리: 보정만으로 저비용 지도" and s12.delivered[1].target == "kp_sm_7a"
    assert by_event(s12, "S12_e04").decision.cooldown_state["redundant_with"] == s12.delivered[0].prompt_id


def test_runs_are_deterministic_and_seek_replays(svc):
    a = run_all(svc, "S05_en_filler_repetition")
    b = run_all(svc, "S05_en_filler_repetition")
    sig = lambda r: [(s.decision.decision_id, s.decision.decision, s.decision.utility,
                      s.prompt.text if s.prompt else None) for s in r.steps]
    assert sig(a) == sig(b)
    before = sig(a)
    svc.seek(a.run_id, 2)
    assert a.cursor == 2
    svc.seek(a.run_id, a.total)
    assert sig(a) == before
    svc.reset(a.run_id)
    assert a.cursor == 0 and a.delivered == []


def test_seeded_noise_is_reproducible(svc):
    a = run_all(svc, "S07_en_audience_confusion", judge_noise=0.1, seed=3)
    b = run_all(svc, "S07_en_audience_confusion", judge_noise=0.1, seed=3)
    c = run_all(svc, "S07_en_audience_confusion", judge_noise=0.1, seed=4)
    ua = [s.decision.utility for s in a.steps]
    assert ua == [s.decision.utility for s in b.steps]
    assert ua != [s.decision.utility for s in c.steps]


def test_stage_failure_does_not_stop_the_simulation(svc):
    run = svc.create_run("S06_ko_silence_block")

    class Broken:
        name = "broken"

        def retrieve(self, kb, query):
            raise RuntimeError("index offline")

    run.retriever = Broken()
    svc.seek(run.run_id, run.total)
    assert run.finished
    errors = [st for s in run.steps for st in s.stages if st.status == "error"]
    assert errors and all(st.name == "knowledge_retrieval" for st in errors)
    assert by_event(run, "S06_e04").decision.decision == "INTERVENE_NOW"  # still decided from signals


def test_embedding_retriever_is_a_drop_in_replacement(svc):
    run = run_all(svc, "S07_en_audience_confusion", retriever="embedding")
    step = by_event(run, "S07_e03")
    assert step.knowledge_retrieval.retriever.startswith("embedding:")
    assert any(h.used for h in step.knowledge_retrieval.hits)


# ----------------------------------------------------------------------------- local LLM adapters
def _fake_ollama(reply):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": reply(system, body)}})
    return httpx.MockTransport(handler)


def test_llm_judge_is_used_but_recency_policy_is_enforced(settings, svc):
    def reply(system, body):
        if "private prompt" in system:
            return json.dumps({"decision": "INTERVENE_NOW", "detected_issue": "pace_too_fast", "severity": 0.9,
                               "urgency": 0.9, "confidence": 0.9, "evidence_event_ids": ["S04_e03", "bogus"],
                               "used_memory_ids": ["mem_9999"], "used_knowledge_ids": [],
                               "supporting_reasons": ["Still fast."], "counter_reasons": [],
                               "reason_for_final_decision": "Remind again.", "recommended_prompt_type": "pace_adjustment",
                               "recommended_prompt_length": "short"})
        return json.dumps({"text": "천천히 말하기"})

    provider = OllamaProvider(settings, client=httpx.Client(transport=_fake_ollama(reply)))
    svc.providers._ollama = provider
    run = run_all(svc, "S04_ko_too_fast", judge_provider="ollama", prompt_provider="ollama")
    assert not run.is_mock
    first = by_event(run, "S04_e01").decision
    assert first.judge.startswith("llm:ollama") and first.decision == "INTERVENE_NOW" and not first.fallback_used
    assert "bogus" not in first.evidence_event_ids and first.used_memory_ids == []
    blocked = by_event(run, "S04_e02").decision
    assert blocked.decision == "SUPPRESS_DUE_TO_RECENT_INTERVENTION" and blocked.policy_overrides
    assert run.delivered[0].text == "천천히 말하기" and run.delivered[0].generator.startswith("llm:")


def test_llm_failures_fall_back_to_rules_and_templates(settings, svc):
    provider = OllamaProvider(settings, client=httpx.Client(
        transport=_fake_ollama(lambda s, b: "not json" if "private prompt" in s else json.dumps({"text": "English text"}))))
    svc.providers._ollama = provider
    run = run_all(svc, "S02_ko_missing_point", judge_provider="ollama", prompt_provider="ollama")
    d = by_event(run, "S02_e05").decision
    assert d.fallback_used and d.fallback_reason == "ModelOutputError" and d.decision == "INTERVENE_NOW"
    p = run.delivered[0]
    assert p.fallback_used and p.generator == "template" and "기준선" in p.text  # English reword rejected for ko


# ----------------------------------------------------------------------------- evaluation
def test_evaluation_matching_and_metrics(svc):
    run = run_all(svc, "S04_ko_too_fast")
    ev = svc.evaluation_for(run)
    assert ev["counts"]["tp"] == 2 and ev["counts"]["fp"] == 0 and ev["counts"]["fn"] == 0
    assert ev["metrics"]["precision"] == 1.0 and ev["metrics"]["recall"] == 1.0
    row = next(r for r in ev["rows"] if r["event_id"] == "S04_e03")
    assert row["decision_agrees"] is True and row["match"] == "TN"

    # Remove the first prompt: its positive becomes a FN; move the second outside the tolerance: FP + FN.
    prompts = [p.model_copy() for p in run.delivered]
    late = prompts[1].model_copy(update={"created_elapsed": prompts[1].created_elapsed + MATCH_TOLERANCE_S + 1})
    ev2 = evaluate_steps(run.steps, run.session.ground_truth, [late], {"language": "ko", "scenario_type": "x"})
    assert (ev2["counts"]["tp"], ev2["counts"]["fp"], ev2["counts"]["fn"]) == (0, 1, 2)
    assert ev2["metrics"]["precision"] == 0.0 and ev2["metrics"]["recall"] == 0.0
    assert ev2["metrics"]["missed_critical_rate"] == 1.0


def test_batch_evaluation_reports_language_and_prompt_type_slices(svc):
    res = svc.evaluate_all()
    assert len(res["sessions"]) == len(svc.dataset.sessions)
    assert set(res["by_language"]) == {"ko", "en"}
    assert "content_reminder" in res["by_prompt_type"] and "method" in res and res["notice"]
    overall = res["overall"]["metrics"]
    assert 0 <= overall["precision"] <= 1 and 0 <= overall["recall"] <= 1
    edge = [s for s in res["sessions"] if s["scenario_type"] == "edge_case"]
    assert edge and any(r["mismatch_notes"] for s in edge for r in s["rows"])
    assert svc.evaluate_all() is res  # cached per configuration
    assert aggregate([])["overall"]["sessions"] == 0


def test_decisions_are_always_from_the_allowed_set(svc):
    res = svc.evaluate_all()
    assert all(r["decision"] in DECISIONS for s in res["sessions"] for r in s["rows"])


def test_hashing_embedder_fixture_is_used(container):
    assert isinstance(container.embedder, HashingEmbedder)
