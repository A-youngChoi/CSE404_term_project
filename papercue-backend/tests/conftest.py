from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.container import Container
from app.services.embeddings import HashingEmbedder

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"

# Tests must never pick up a developer's local .env.
for _key in list(os.environ):
    if _key.startswith(("PAPERCUE_", "OLLAMA_", "EMBEDDING_", "LLM_", "DATABASE_", "DEBUG_")):
        os.environ.pop(_key)


def load_sample(name: str):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=tmp_path / "test.db",
        papers_dir=tmp_path / "papers",
        exports_dir=tmp_path / "exports",
        llm_provider="mock",
        embedding_provider="hashing",
        dashboard_dist_dir=tmp_path / "no-dist",
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    c = Container(settings, embedder=HashingEmbedder())
    yield c
    c.close()


@pytest.fixture
def client(settings: Settings, container: Container) -> TestClient:
    app = create_app(settings, container)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def paper_id(client: TestClient) -> str:
    r = client.post("/api/v1/papers", json=load_sample("papercue_paper.json"))
    assert r.status_code == 201, r.text
    return r.json()["paper"]["id"]


@pytest.fixture
def session_id(client: TestClient, paper_id: str) -> str:
    r = client.post("/api/v1/sessions", json={"paper_id": paper_id, "consent_confirmed": True, "llm_provider": "mock"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_turn(client: TestClient, sid: str, speaker: str, text: str) -> dict:
    r = client.post(f"/api/v1/sessions/{sid}/turns", json={"speaker": speaker, "text": text})
    assert r.status_code == 201, r.text
    return r.json()


def beliefs(client: TestClient, sid: str) -> list[dict]:
    view = client.get(f"/api/v1/sessions/{sid}/audience-model").json()
    return [b for items in view["dimensions"].values() for b in items]


def find_belief(client: TestClient, sid: str, dimension: str, key: str) -> dict | None:
    return next((b for b in beliefs(client, sid) if b["dimension"] == dimension and b["key"] == key), None)
