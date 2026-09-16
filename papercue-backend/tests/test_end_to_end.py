"""End-to-end mock-mode pipeline, traces, and explainability."""

from __future__ import annotations

from app.models.trace import STAGE_ORDER
from tests.conftest import add_turn, find_belief, load_sample


def test_privacy_walkthrough_end_to_end(client, session_id):
    client.post(f"/api/v1/sessions/{session_id}/profile", json={"research_topics": ["wearable sensing"]})
    assert find_belief(client, session_id, "interest", "wearable sensing")["source_type"] == "profile"

    result = add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    concern = find_belief(client, session_id, "concern", "privacy")
    assert concern["value"] == "raised" and concern["confidence"] >= 0.85

    r = client.post(f"/api/v1/sessions/{session_id}/cue")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "deliverable" and body["delivered"] is True
    assert body["cue"] == "privacy first—local processing"
    assert body["decision"]["action"] == "address_concern" and body["decision"]["target"] == "privacy"
    assert body["mock_mode"] is True and "NOT from a language model" in body["mock_notice"]

    top = body["trace"]["retrieved_units"][0]
    assert top["title"] == "Local processing and data storage"
    assert body["generated"]["grounding_unit_ids"] == [top["unit_id"]]
    assert set(body["generated"]["audience_evidence_ids"]) <= {e["id"] for e in result["evidence_accepted"]}
    assert body["trace"]["trigger_turn_id"] == result["turn"]["id"]
    assert concern["id"] in body["decision"]["beliefs_used"]
    assert {"retrieval", "intervention_decision", "cue_candidate", "safety_grounding_check"} <= set(body["trace"]["latency_ms"])
    assert all(c["passed"] for c in body["generated"]["filter_results"])

    cues = client.get(f"/api/v1/sessions/{session_id}/cues").json()
    assert cues[0]["generated"]["cue"] == "privacy first—local processing"
    assert cues[0]["final_status"] == "deliverable"


def test_korean_walkthrough(client):
    w = load_sample("ko_walkthrough.json")
    pid = client.post("/api/v1/papers", json=w["paper"]).json()["paper"]["id"]
    sid = client.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True, "llm_provider": "mock",
                                                "cue_language": "ko"}).json()["id"]
    client.post(f"/api/v1/sessions/{sid}/profile", json=w["profile"])
    for t in w["turns"]:
        add_turn(client, sid, t["speaker"], t["text"])
    body = client.post(f"/api/v1/sessions/{sid}/cue").json()
    assert body["cue"] == w["expected"]["cue"]
    assert body["decision"]["action"] == w["expected"]["action"]
    assert body["trace"]["retrieved_units"][0]["title"] == w["expected"]["retrieved_unit_title"]
    assert find_belief(client, sid, "concern", w["expected"]["concern_key"])["confidence"] >= 0.85


def test_repeated_cue_request_is_suppressed(client, session_id):
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    first = client.post(f"/api/v1/sessions/{session_id}/cue").json()
    second = client.post(f"/api/v1/sessions/{session_id}/cue").json()
    assert first["delivered"] and not second["delivered"]
    assert second["status"] == "duplicate_recent" and second["cue"] is None


def test_auto_candidate_never_delivers(client, session_id):
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    body = client.post(f"/api/v1/sessions/{session_id}/auto-candidate").json()
    assert body["evaluation_only"] is True and body["delivered"] is False and body["cue"] is None
    assert body["decision"]["mode"] == "auto_candidate"
    assert client.post(f"/api/v1/sessions/{session_id}/cue").json()["delivered"] is True


def test_cue_requires_listener_turn(client, session_id):
    add_turn(client, session_id, "presenter", "Welcome to the poster.")
    r = client.post(f"/api/v1/sessions/{session_id}/cue")
    assert r.status_code == 409


def test_no_grounding_yields_verification_or_no_cue(client):
    pid = client.post("/api/v1/papers", json={
        "title": "Unrelated", "extract_units_from_text": False,
        "units": [{"unit_type": "method", "title": "Soil sampling", "content": "We sampled soil moisture in fields.",
                   "keywords": ["soil"]}],
    }).json()["paper"]["id"]
    sid = client.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True,
                                                "llm_provider": "mock"}).json()["id"]
    add_turn(client, sid, "listener", "Does this send our conversation to a server?")
    body = client.post(f"/api/v1/sessions/{sid}/cue").json()
    assert body["decision"]["action"] == "verify"
    assert body["decision"]["reason_code"] == "insufficient_grounding"
    assert body["cue"] in ("ask their main concern", None)


def test_full_demo_conversation_and_memory(client, session_id, settings):
    client.post(f"/api/v1/sessions/{session_id}/profile", json=load_sample("listener_profile.json"))
    turns = load_sample("demo_conversation.json")
    for t in turns + turns[:4]:
        add_turn(client, session_id, t["speaker"], t["text"])
    state = client.get(f"/api/v1/sessions/{session_id}/conversation-state").json()
    assert len(state["recent_window_turn_ids"]) == settings.recent_turn_window
    assert state["older_summary"]["summarized_turns"] == len(turns) + 4 - settings.recent_turn_window
    unfamiliar = find_belief(client, session_id, "familiarity", "language models")
    assert unfamiliar["value"] == "unfamiliar"
    connection = find_belief(client, session_id, "connection", "wearable sensing")
    assert connection["source_type"] == "conversation" and connection["confidence"] >= 0.85


def test_turn_trace_contains_all_stages(client, session_id):
    result = add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    client.post(f"/api/v1/sessions/{session_id}/cue")
    view = client.get(f"/api/v1/sessions/{session_id}/turns/{result['turn']['id']}/trace").json()
    assert view["stage_order"] == STAGE_ORDER and len(STAGE_ORDER) == 12
    kinds = {t["kind"] for t in view["traces"]}
    assert kinds == {"turn", "cue"}
    turn_trace = next(t for t in view["traces"] if t["kind"] == "turn")
    assert [s["stage_name"] for s in turn_trace["stages"]] == STAGE_ORDER
    cue_trace = next(t for t in view["traces"] if t["kind"] == "cue")
    assert {s["stage_name"] for s in cue_trace["stages"]} == set(STAGE_ORDER)
    for trace in view["traces"]:
        assert trace["pipeline_version"] and trace["started_at"] and trace["completed_at"]
        for s in trace["stages"]:
            assert {"stage_name", "component_type", "status", "duration_ms", "input_reference_ids",
                    "output_reference_ids", "validation_errors", "short_rationale"} <= set(s)
    evidence_stage = next(s for s in turn_trace["stages"] if s["stage_name"] == "evidence_extraction")
    assert evidence_stage["component_type"] == "mock_rules"
    assert evidence_stage["prompt_version"] == "evidence_extraction-v1"
    assert evidence_stage["output_reference_ids"] == [e["id"] for e in view["evidence"]]
    assert view["belief_changes"] and view["belief_changes"][0]["old_confidence"] is None


def test_traces_do_not_duplicate_raw_utterances(client, container, session_id):
    text = "Does this send our conversation to a server? Kiwi-marker-7731"
    add_turn(client, session_id, "listener", text)
    client.post(f"/api/v1/sessions/{session_id}/cue")
    rows = container.db.fetchall("SELECT stages FROM pipeline_traces WHERE session_id = ?", (session_id,))
    decisions = container.db.fetchall("SELECT trace FROM cue_decisions WHERE session_id = ?", (session_id,))
    for r in rows + decisions:
        blob = r.get("stages") or r.get("trace")
        assert "Kiwi-marker-7731" not in blob
