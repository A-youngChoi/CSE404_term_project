"""Profile priors, evidence extraction, and the deterministic update policy."""

from __future__ import annotations

from app.llm.mock import DeterministicMockProvider
from app.models.conversation import ConversationState, Turn
from app.models.llm_outputs import EvidenceExtractionOutput, ProposedEvidence
from tests.conftest import add_turn, beliefs, find_belief


def test_profile_creates_low_confidence_hypotheses(client, session_id, settings):
    r = client.post(f"/api/v1/sessions/{session_id}/profile",
                    json={"research_topics": ["wearable sensing"], "familiar_methods": ["user studies"]})
    assert r.status_code == 200
    assert r.json()["hypotheses_created"] >= 2
    items = beliefs(client, session_id)
    assert items
    for b in items:
        assert b["source_type"] == "profile"
        assert b["lifecycle"] == "profile_hypothesis"
        assert b["confidence"] <= settings.profile_confidence_cap
        assert b["source_id"].startswith("profile:")
        assert b["evidence_ids"] and b["reason"] and b["updated_at"]


def test_profile_rejects_sensitive_fields_without_echoing_them(client, session_id):
    r = client.post(f"/api/v1/sessions/{session_id}/profile",
                    json={"research_topics": ["wearable sensing", "religion and politics"]})
    body = r.json()
    assert body["rejected_fields"] == ["research_topics[1]"]
    assert "religion" not in str(body)
    assert all("religion" not in b["key"] for b in beliefs(client, session_id))


def test_explicit_statement_overrides_profile_assumption(client, session_id):
    client.post(f"/api/v1/sessions/{session_id}/profile", json={"familiar_methods": ["language models"]})
    prior = find_belief(client, session_id, "familiarity", "language models")
    assert prior["value"] == "familiar" and prior["source_type"] == "profile"

    add_turn(client, session_id, "listener", "I'm not familiar with language models.")
    b = find_belief(client, session_id, "familiarity", "language models")
    assert b["value"] == "unfamiliar"
    assert b["source_type"] == "conversation"
    assert b["confidence"] >= 0.85
    assert b["lifecycle"] == "explicitly_confirmed"
    history = client.get(f"/api/v1/sessions/{session_id}/audience-model/history").json()
    change = [h for h in history if h["key"] == "language models"][-1]
    assert change["change_type"] == "overridden"
    assert change["old_value"] == "familiar" and change["new_value"] == "unfamiliar"
    assert change["old_confidence"] < change["new_confidence"]


def test_ambiguous_question_does_not_create_high_confidence_belief(client, session_id):
    add_turn(client, session_id, "listener", "Hmm, can you explain that more simply?")
    items = beliefs(client, session_id)
    assert items, "a clarification request should produce some evidence"
    assert all(b["confidence"] < 0.6 for b in items)


def test_privacy_question_creates_privacy_concern(client, session_id):
    result = add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    assert any(e["dimension"] == "concern" and e["key"] == "privacy" and e["evidence_type"] == "explicit"
               for e in result["evidence_accepted"])
    b = find_belief(client, session_id, "concern", "privacy")
    assert b["value"] == "raised" and b["confidence"] >= 0.85
    assert b["source_id"] == result["turn"]["id"]
    assert result["state"]["detected_concerns"] == ["privacy"]


def test_repeated_behavioral_evidence_accumulates_and_contradiction_weakens_first(container, session_id):
    from app.models.enums import Dimension, EvidenceType

    svc, repo = container.audience, container.audience_repo

    def ev(value, etype=EvidenceType.behavioral):
        return repo.add_evidence(session_id=session_id, turn_id=None, source_type="conversation", source_id="t",
                                 dimension=Dimension.engagement.value, key="overall", value=value,
                                 evidence_type=etype.value, confidence=0.5, quote=None, observation="obs",
                                 extractor="test")

    first = svc.apply(ev("high"), key="overall", value="high", reason="r", listener_turn=1)
    second = svc.apply(ev("high"), key="overall", value="high", reason="r", listener_turn=2)
    assert first.change_type == "created" and second.change_type == "reinforced"
    assert second.new_confidence > first.new_confidence

    weakened = svc.apply(ev("low"), key="overall", value="low", reason="r", listener_turn=3)
    assert weakened.change_type == "weakened" and weakened.new_value == "high"
    assert weakened.new_confidence < second.new_confidence
    reversed_ = svc.apply(ev("low"), key="overall", value="low", reason="r", listener_turn=4)
    assert reversed_.change_type == "reversed" and reversed_.new_value == "low"


def test_weak_evidence_changes_confidence_gradually(container, session_id):
    from app.models.enums import Dimension, EvidenceType

    repo, svc = container.audience_repo, container.audience
    e = repo.add_evidence(session_id=session_id, turn_id=None, source_type="conversation", source_id="t",
                          dimension=Dimension.interest.value, key="x", value="interested",
                          evidence_type=EvidenceType.weak_inference.value, confidence=0.3, quote=None,
                          observation="o", extractor="test")
    c1 = svc.apply(e, key="x", value="interested", reason="r", listener_turn=1)
    c2 = svc.apply(e, key="x", value="interested", reason="r", listener_turn=2)
    assert c1.new_confidence == 0.3
    assert 0.3 < c2.new_confidence <= 0.5


def test_stale_profile_hypotheses_decay(client, session_id):
    client.post(f"/api/v1/sessions/{session_id}/profile", json={"recent_paper_keywords": ["smart glasses"]})
    before = find_belief(client, session_id, "interest", "smart glasses")["effective_confidence"]
    for _ in range(5):
        add_turn(client, session_id, "listener", "Okay.")
    after = find_belief(client, session_id, "interest", "smart glasses")["effective_confidence"]
    assert after < before


def test_extractor_rejects_unquoted_and_sensitive_evidence(container, session_id):
    turn = Turn(id="t1", session_id=session_id, turn_index=1, speaker="listener", text="Does this use a server?",
                spoken_at="x", input_source="text", analysis_status="pending", created_at="x")
    container.db.execute(
        "INSERT INTO conversation_turns(id, session_id, turn_index, speaker, text, spoken_at, created_at) "
        "VALUES ('t1', ?, 1, 'listener', 'Does this use a server?', 'x', 'x')", (session_id,))
    out = EvidenceExtractionOutput(evidence=[
        ProposedEvidence(dimension="concern", key="privacy", value="raised", evidence_type="explicit",
                         quote="use a server", observation="Asked about servers."),
        ProposedEvidence(dimension="concern", key="privacy", value="raised", evidence_type="explicit",
                         quote="I hate cloud companies", observation="Invented quote."),
        ProposedEvidence(dimension="concern", key="anxiety disorder", value="raised", evidence_type="weak_inference",
                         quote="server", observation="Listener seems anxious."),
        ProposedEvidence(dimension="knowledge", key="general", value="novice", evidence_type="behavioral",
                         quote="server", observation="Asked a basic question."),
        ProposedEvidence(dimension="engagement", key="overall", value="bored", evidence_type="behavioral",
                         quote="server", observation="x"),
    ])
    result = container.extractor.store("test", turn, out)
    codes = [r["code"] for r in result.rejected]
    assert "quote_not_in_turn" in codes and "sensitive_key" in codes and "invalid_value" in codes
    assert len(result.accepted) == 2
    knowledge = [e for e in result.accepted if e.dimension.value == "knowledge"][0]
    assert knowledge.evidence_type.value == "weak_inference"  # single signal downgraded


def test_mock_extractor_never_characterizes_person():
    provider = DeterministicMockProvider()
    out = provider.generate_structured(
        "evidence_extraction",
        {"latest_listener_turn": {"turn_id": "t", "text": "Isn't that just surveillance? I doubt it works."},
         "conversation_state": ConversationState().model_dump(mode="json"), "paper_terms": []},
        EvidenceExtractionOutput,
    )
    for e in out.evidence:
        assert e.dimension.value not in ("knowledge",)
        assert "skeptic" not in e.observation.lower()
