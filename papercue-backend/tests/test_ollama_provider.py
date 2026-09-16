"""OllamaProvider over a mocked local HTTP transport (no real model or network needed)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import LocalConfigurationError, ModelOutputError
from app.llm.mock import DeterministicMockProvider
from app.llm.ollama import OllamaProvider
from app.main import create_app
from app.models.llm_outputs import CueGenerationOutput
from app.prompts import PROMPTS
from app.services.container import Container
from app.services.embeddings import HashingEmbedder
from tests.conftest import add_turn, load_sample


def _task_from_request(body: dict) -> str:
    system = body["messages"][0]["content"]
    return next(t for t, p in PROMPTS.items() if p.system == system)


def fake_ollama(invalid_tasks: set[str], calls: list[str]):
    """A stand-in Ollama server that answers with mock-rule JSON, or garbage for selected tasks."""
    mock = DeterministicMockProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"}]})
        body = json.loads(request.content)
        assert body["options"]["temperature"] <= 0.2 and body["stream"] is False
        task = _task_from_request(body)
        calls.append(task)
        if task in invalid_tasks:
            return httpx.Response(200, json={"message": {"content": "Sure! Here is your cue: privacy first"}})
        payload = json.loads(body["messages"][1]["content"].split("INPUT JSON:\n", 1)[1])
        content = json.dumps(getattr(mock, f"_task_{task}")(payload))
        return httpx.Response(200, json={"message": {"content": content}})

    return httpx.MockTransport(handler)


def test_invalid_json_fails_safely_after_retry(settings):
    calls: list[str] = []
    provider = OllamaProvider(settings, client=httpx.Client(transport=fake_ollama({"cue_generation"}, calls)))
    with pytest.raises(ModelOutputError):
        provider.generate_structured("cue_generation", {"action": "verify"}, CueGenerationOutput)
    assert calls == ["cue_generation"] * (1 + settings.llm_max_retries)


def test_unreachable_ollama_gives_clear_local_error(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    provider = OllamaProvider(settings, client=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(LocalConfigurationError) as exc:
        provider.generate_structured("cue_generation", {}, CueGenerationOutput)
    assert "ollama serve" in exc.value.message
    assert provider.status()["available"] is False


def test_missing_model_error_mentions_pull(settings):
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "model not found"}))
    provider = OllamaProvider(settings, client=httpx.Client(transport=transport))
    with pytest.raises(LocalConfigurationError) as exc:
        provider.generate_structured("cue_generation", {}, CueGenerationOutput)
    assert "ollama pull" in exc.value.message


def _ollama_client(settings, invalid: set[str], calls: list[str]):
    provider = OllamaProvider(settings, client=httpx.Client(transport=fake_ollama(invalid, calls)))
    container = Container(settings, embedder=HashingEmbedder(), ollama=provider)
    return TestClient(create_app(settings, container))


def test_ollama_session_end_to_end_and_invalid_cue_json(settings):
    calls: list[str] = []
    with _ollama_client(settings, {"cue_generation"}, calls) as client:
        pid = client.post("/api/v1/papers", json=load_sample("papercue_paper.json")).json()["paper"]["id"]
        sid = client.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True,
                                                    "llm_provider": "ollama"}).json()["id"]
        result = add_turn(client, sid, "listener", "Does this send our conversation to a server?")
        assert result["mock_mode"] is False and result["llm_provider"].startswith("ollama:")
        assert {"conversation_state", "evidence_extraction", "audience_update"} <= set(calls)

        r = client.post(f"/api/v1/sessions/{sid}/cue")
        assert r.status_code == 200
        body = r.json()
        assert body["cue"] is None and body["status"] == "format_invalid"
        first_check = body["generated"]["filter_results"][0]
        assert first_check["name"] == "valid_json" and first_check["passed"] is False
        trace = client.get(f"/api/v1/sessions/{sid}/traces").json()[-1]
        stage = next(s for s in trace["stages"] if s["stage_name"] == "cue_candidate")
        assert stage["status"] == "failed" and stage["prompt_version"] == "cue_generation-v1"


def test_ollama_unavailable_during_turn_returns_503(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    provider = OllamaProvider(settings, client=httpx.Client(transport=httpx.MockTransport(refuse)))
    container = Container(settings, embedder=HashingEmbedder(), ollama=provider)
    with TestClient(create_app(settings, container)) as client:
        pid = client.post("/api/v1/papers", json=load_sample("papercue_paper.json")).json()["paper"]["id"]
        sid = client.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True,
                                                    "llm_provider": "ollama"}).json()["id"]
        r = client.post(f"/api/v1/sessions/{sid}/turns", json={"speaker": "listener", "text": "Is it stored?"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "local_model_unavailable"
        turns = client.get(f"/api/v1/sessions/{sid}/turns").json()
        assert turns[0]["analysis_status"] == "failed"
        status = client.get("/api/v1/config/status").json()
        assert "ollama_unavailable" in status["warnings"]
        assert status["recent_errors"][0]["error_code"] == "local_model_unavailable"
