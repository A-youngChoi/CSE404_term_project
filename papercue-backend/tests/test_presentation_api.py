"""HTTP API of the presentation simulation."""

from __future__ import annotations

API = "/api/v1/presentation"
MOBILE_KEYS = {"text", "priority", "urgency", "remaining_seconds", "display_seconds", "prompt_type"}


def test_sessions_knowledge_and_config(client):
    sessions = client.get(f"{API}/sessions").json()
    assert len(sessions) >= 10 and {"gt_positive", "presenter", "scenario_type"} <= set(sessions[0])
    detail = client.get(f"{API}/sessions/S02_ko_missing_point").json()
    assert detail["ground_truth_notice"] and "ground_truth" not in detail["events"][0]
    kbs = client.get(f"{API}/knowledge").json()
    categories = {c["category"] for kb in kbs for c in kb["chunks"]}
    assert {"core_claim", "background", "method", "result", "limitation", "slide_note", "must_mention",
            "expected_qa", "glossary", "related_work"} <= categories
    cfg = client.get(f"{API}/config").json()
    assert cfg["defaults"]["judge_provider"] == "mock" and cfg["judge_is_mock"] is True
    assert "/Users/" not in cfg["data_dir"] and cfg["data_dir"].startswith("./")
    assert client.get(f"{API}/sessions/nope").status_code == 404


def test_run_lifecycle_step_seek_playback_and_mobile(client):
    r = client.post(f"{API}/runs", json={"session_id": "S06_ko_silence_block"})
    assert r.status_code == 201
    view = r.json()
    rid = view["summary"]["run_id"]
    assert view["summary"]["cursor"] == 0 and view["summary"]["is_mock"] is True
    assert view["initial"]["memory_changes"] and view["initial"]["user_model_snapshot"]
    assert set(view["ground_truth"]) == {e["event_id"] for e in view["event_index"]}

    step = client.post(f"{API}/runs/{rid}/step", json={"count": 4}).json()
    assert step["since"] == 0 and len(step["steps"]) == 4
    last = step["steps"][-1]
    assert last["event_id"] == "S06_e04" and last["decision"]["decision"] == "INTERVENE_NOW"
    assert "ground_truth" not in last["event"]
    assert {s["name"] for s in last["stages"]} >= {"context", "memory", "user_model", "knowledge_retrieval", "judge"}
    assert {m["lane"] for m in last["timeline"]} >= {"speech", "issue", "judge", "intervention", "retrieval"}

    mobile = client.get(f"{API}/runs/{rid}/mobile").json()
    assert MOBILE_KEYS <= set(mobile) and mobile["text"] and mobile["priority"] == "high"
    assert client.get(f"{API}/mobile").json()["run_id"] == rid

    pb = client.post(f"{API}/runs/{rid}/playback", json={"playing": True, "speed": 4}).json()
    assert pb["playback"]["playing"] is True and pb["playback"]["speed"] == 4

    more = client.post(f"{API}/runs/{rid}/step").json()
    assert more["since"] == 4 and len(more["steps"]) == 1 and more["prompts"][0]["outcome"] == "recovered"

    back = client.post(f"{API}/runs/{rid}/seek", json={"index": 2}).json()
    assert back["summary"]["cursor"] == 2 and back["prompts"] == []
    done = client.post(f"{API}/runs/{rid}/complete").json()
    assert done["summary"]["finished"] is True and done["summary"]["playback"]["playing"] is False
    assert done["evaluation"]["metrics"]["recall"] == 1.0

    export = client.get(f"{API}/runs/{rid}/export").json()
    assert export["synthetic"] and len(export["rows"]) == done["summary"]["total"]
    assert {"features", "system", "labels"} <= set(export["rows"][0])
    reset = client.post(f"{API}/runs/{rid}/reset").json()
    assert reset["summary"]["cursor"] == 0


def test_completed_run_is_logged_locally(client, settings):
    rid = client.post(f"{API}/runs", json={"session_id": "S01_en_stable"}).json()["summary"]["run_id"]
    client.post(f"{API}/runs/{rid}/complete")
    files = list(settings.presentation_log_dir.glob("*.jsonl"))
    assert [f.stem for f in files] == [rid]
    assert len(files[0].read_text(encoding="utf-8").splitlines()) == 10


def test_evaluation_endpoint_and_errors(client):
    ev = client.get(f"{API}/evaluation").json()
    assert {"overall", "by_language", "by_scenario", "by_prompt_type", "sessions", "method"} <= set(ev)
    assert client.get(f"{API}/runs/missing").status_code == 404
    assert client.post(f"{API}/runs", json={"session_id": "S01_en_stable", "judge_provider": "gpt"}).status_code == 422
    assert client.post(f"{API}/runs", json={"session_id": "unknown"}).status_code == 404
    assert client.get(f"{API}/mobile").json()["idle"] in (True, False)


def test_idle_mobile_state_without_runs(client):
    assert client.get(f"{API}/mobile").json() == {"run_id": None, "text": None, "idle": True, "is_mock": True}
