"""Consent, deletion, export, retention, logging and binding requirements."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.core.utils import utcnow
from tests.conftest import add_turn


def test_session_creation_fails_without_consent(client, paper_id):
    r = client.post("/api/v1/sessions", json={"paper_id": paper_id, "consent_confirmed": False})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"
    assert client.get("/api/v1/sessions").json() == []


def test_session_shows_consent_notice_and_mock_flag(client, session_id):
    s = client.get(f"/api/v1/sessions/{session_id}").json()["session"]
    assert s["consent_confirmed"] is True
    assert "consented research testing" in s["consent_notice"]
    assert s["mock_mode"] is True
    assert s["llm_model"].startswith("mock:")


def test_turn_rejected_for_unknown_session(client):
    r = client.post("/api/v1/sessions/nope/turns", json={"speaker": "listener", "text": "hello"})
    assert r.status_code == 404


def test_session_deletion_removes_all_dependent_records(client, container, session_id):
    client.post(f"/api/v1/sessions/{session_id}/profile", json={"research_topics": ["wearable sensing"]})
    add_turn(client, session_id, "presenter", "PaperCue gives short cues.")
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    assert client.post(f"/api/v1/sessions/{session_id}/cue").status_code == 200
    counts = client.get(f"/api/v1/sessions/{session_id}").json()["record_counts"]
    assert counts["conversation_turns"] == 2 and counts["evidence_items"] > 0 and counts["pipeline_traces"] >= 3

    r = client.delete(f"/api/v1/sessions/{session_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True

    from app.repositories.sessions import SESSION_TABLES

    for table in SESSION_TABLES:
        n = container.db.fetchone(f"SELECT COUNT(*) AS n FROM {table} WHERE session_id = ?", (session_id,))["n"]
        assert n == 0, table
    assert container.db.fetchone("SELECT COUNT(*) AS n FROM sessions")["n"] == 0
    # The dashboard can no longer read anything for the deleted session.
    for path in ("", "/turns", "/audience-model", "/evidence", "/cues", "/traces", "/export", "/conversation-state"):
        assert client.get(f"/api/v1/sessions/{session_id}{path}").status_code == 404, path
    events = client.get("/api/v1/audit").json()
    assert events[0]["event_type"] == "session_deleted"


def test_reset_keeps_session_and_rebuilds_profile_priors(client, session_id):
    client.post(f"/api/v1/sessions/{session_id}/profile", json={"research_topics": ["wearable sensing"]})
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    r = client.post(f"/api/v1/sessions/{session_id}/reset")
    assert r.status_code == 200 and r.json()["profile_kept"] is True
    counts = client.get(f"/api/v1/sessions/{session_id}").json()["record_counts"]
    assert counts["conversation_turns"] == 0 and counts["pipeline_traces"] == 0
    assert counts["audience_beliefs"] > 0  # profile priors rebuilt


def test_export_contains_session_data_but_no_configuration(client, session_id):
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    data = client.get(f"/api/v1/sessions/{session_id}/export").json()
    assert data["format"] == "papercue-session-export-v1"
    assert len(data["turns"]) == 1 and data["evidence"]
    flat = str(data).lower()
    for forbidden in ("ollama_base_url", "database_path", "debug_prompt", "vector", "/users/"):
        assert forbidden not in flat


def test_retention_purge_removes_expired_sessions(client, container, session_id):
    past = (utcnow() - timedelta(days=1)).isoformat(timespec="milliseconds")
    container.db.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    assert session_id in client.get("/api/v1/retention/expired").json()["expired_session_ids"]
    r = client.post(f"/api/v1/sessions/{session_id}/turns", json={"speaker": "listener", "text": "hi"})
    assert r.status_code == 409
    assert client.post("/api/v1/retention/purge").json()["purged_sessions"] == 1
    assert client.get(f"/api/v1/sessions/{session_id}").status_code == 404


def test_raw_conversation_not_in_logs(client, session_id, caplog):
    secret = "Zebra-quartz confidential remark about our grant"
    caplog.set_level(logging.DEBUG)
    add_turn(client, session_id, "listener", f"{secret}. Does this send our conversation to a server?")
    client.post(f"/api/v1/sessions/{session_id}/cue")
    assert caplog.records, "expected some application log records"
    assert "Zebra-quartz" not in caplog.text
    assert "send our conversation" not in caplog.text


def test_debug_prompts_not_stored_by_default(client, container, session_id):
    add_turn(client, session_id, "listener", "Does this send our conversation to a server?")
    assert container.db.fetchone("SELECT COUNT(*) AS n FROM llm_debug_prompts")["n"] == 0


def test_server_defaults_to_localhost(monkeypatch):
    assert Settings(_env_file=None).papercue_host == "127.0.0.1"
    with pytest.raises(ValueError):
        Settings(_env_file=None, papercue_host="0.0.0.0")
    with pytest.raises(ValueError):
        Settings(_env_file=None, ollama_base_url="http://example.org:11434")
    with pytest.raises(ValueError):
        Settings(_env_file=None, dashboard_origins="*")

    import uvicorn

    from app import main

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: captured.update(kw))
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None))
    main.run()
    assert captured["host"] == "127.0.0.1"


def test_database_file_is_owner_only(settings, container):
    import stat

    mode = stat.S_IMODE(settings.database_path.stat().st_mode)
    assert mode & 0o077 == 0
