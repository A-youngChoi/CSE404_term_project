"""Paper ingestion, local file safety, indexing and retrieval."""

from __future__ import annotations

import base64

from app.services.retrieval import RetrievalQuery


def test_paper_ingestion_creates_indexed_units(client, paper_id):
    detail = client.get(f"/api/v1/papers/{paper_id}").json()
    assert detail["unit_count"] >= 7 and detail["indexed_unit_count"] == detail["unit_count"]
    assert all(u["indexed"] for u in detail["units"])
    assert {u["origin"] for u in detail["units"]} == {"presenter"}
    assert detail["forbidden_claims"]
    assert detail["index_model"] == "mock:hashing-embedder"


def test_full_text_is_split_and_labelled(client):
    text = ("# Introduction\n\nPresenters struggle to adapt explanations.\n\n"
            "# Method\n\nWe build an evolving audience model with local retrieval.\n\n"
            "# Limitations\n\nThe prototype cannot process live speech.")
    r = client.post("/api/v1/papers", json={"title": "T", "full_text": text, "unit_labeler": "mock"})
    assert r.status_code == 201
    units = r.json()["paper"]["units"]
    assert {u["unit_type"] for u in units} >= {"motivation", "method", "limitation"}
    assert all(u["origin"] == "mock" for u in units)
    assert r.json()["unit_labeler"].startswith("mock")


def test_manual_unit_edit_is_marked_and_reindexed(client, paper_id):
    unit = client.get(f"/api/v1/papers/{paper_id}").json()["units"][0]
    r = client.patch(f"/api/v1/papers/{paper_id}/units/{unit['id']}", json={"title": "Edited title"})
    assert r.status_code == 200
    edited = next(u for u in r.json()["units"] if u["id"] == unit["id"])
    assert edited["origin"] == "manual_edit" and edited["title"] == "Edited title" and edited["indexed"]


def test_reindex_endpoint(client, paper_id):
    r = client.post(f"/api/v1/papers/{paper_id}/reindex")
    assert r.status_code == 200 and r.json()["indexed_units"] >= 7


def test_retrieval_returns_paper_grounded_units(container, paper_id):
    results = container.retrieval.search(
        paper_id, RetrievalQuery(latest_question="Does this send our conversation to a server?", concerns=["privacy"])
    )
    assert results
    assert results[0].title == "Local processing and data storage"
    known = {u.id for u in container.papers_repo.list_units(paper_id)}
    assert all(r.unit_id in known for r in results)
    assert len(results) <= container.settings.retrieval_top_k
    assert all(r.score >= container.settings.retrieval_min_score for r in results)


def test_local_file_path_traversal_rejected(client, settings):
    settings.papers_dir.mkdir(parents=True, exist_ok=True)
    (settings.papers_dir.parent / "secret.md").write_text("# Secret\n\nprivate", encoding="utf-8")
    for bad in ("../secret.md", "/etc/passwd", "..%2Fsecret.md", "sub/../../secret.md"):
        r = client.post("/api/v1/papers", json={"title": "x", "source_file": bad})
        assert r.status_code in (404, 422), bad
        assert "private" not in r.text


def test_local_file_inside_papers_dir_is_accepted(client, settings):
    settings.papers_dir.mkdir(parents=True, exist_ok=True)
    (settings.papers_dir / "paper.md").write_text("# Method\n\nLocal retrieval of paper units.", encoding="utf-8")
    r = client.post("/api/v1/papers", json={"title": "x", "source_file": "paper.md", "unit_labeler": "mock"})
    assert r.status_code == 201


def _upload(client, name: str, data: bytes):
    return client.post("/api/v1/papers/upload", json={
        "filename": name, "title": "Uploaded", "unit_labeler": "mock",
        "content_base64": base64.b64encode(data).decode(),
    })


def test_upload_rejects_unsafe_names(client):
    for name in ("../evil.md", "a/b.md", "..\\x.md", ".env", "script.sh", "paper.pdf", "x.md\x00.sh"):
        assert _upload(client, name, b"# Title\n\nbody").status_code == 422, name


def test_upload_rejects_oversized_and_non_utf8(client, settings):
    big = b"a" * (settings.max_upload_bytes + 1)
    assert _upload(client, "big.md", big).status_code == 413
    assert _upload(client, "bin.md", b"\xff\xfe\x00\x81").status_code == 422


def test_upload_stores_inert_text_file(client, settings):
    r = _upload(client, "내 논문 v1.md", "# 방법\n\n로컬 처리 방식을 설명한다.".encode())
    assert r.status_code == 201, r.text
    files = list(settings.papers_dir.iterdir())
    assert len(files) == 1 and files[0].suffix == ".md"
    assert (files[0].stat().st_mode & 0o111) == 0


def test_paper_with_sessions_cannot_be_deleted(client, paper_id, session_id):
    assert client.delete(f"/api/v1/papers/{paper_id}").status_code == 409
