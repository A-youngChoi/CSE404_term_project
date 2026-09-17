"""Loads the synthetic presentation dataset and separates ground truth from observations.

Layout (all synthetic, see sample_data/presentation/README.md):

    knowledge/<kb_id>.json   knowledge base: documents, slide deck with key points, chunks, Q&A, glossary
    presenters.json          fictional presenter profiles with uncertain priors
    sessions/<id>.json       one timestamped event stream per session

Authoring is compact: omitted signal fields get defaults, and slide titles, expected content,
timestamps and remaining time are derived here. Each event's `ground_truth` block is removed
from the observable event and stored separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

from app.core.errors import InvalidInputError, NotFoundError
from app.presentation.schemas import (
    GroundTruth, KnowledgeBase, KnowledgeChunk, ObservedEvent, PresentationSession, PresenterProfile,
    PresenterSignal, SessionMeta,
)

_TENSION_LABELS = ((0.75, "very_tense"), (0.6, "tense"), (0.45, "slightly_tense"), (0.0, "calm"))


def _arousal_label(value: float) -> str:
    return next(label for threshold, label in _TENSION_LABELS if value >= threshold)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidInputError(f"Could not read dataset file '{path.name}'.") from exc


def build_knowledge_base(raw: dict) -> KnowledgeBase:
    kb_id = raw["kb_id"]
    docs = {d["doc_id"]: d for d in raw.get("documents", [])}
    by_type = {d["doc_type"]: d["doc_id"] for d in raw.get("documents", [])}
    chunks: list[KnowledgeChunk] = []
    for c in raw.get("chunks", []):
        chunks.append(KnowledgeChunk(kb_id=kb_id, source_document=docs.get(c["doc_id"], {}).get("title", c["doc_id"]),
                                     **{k: v for k, v in c.items() if k != "doc_id"}))
    slides_doc = docs.get(by_type.get("slide_notes", ""), {}).get("title", "slide notes")
    for s in raw.get("slides", []):
        chunks.append(KnowledgeChunk(
            chunk_id=f"{kb_id}_slide_{s['slide']:02d}", kb_id=kb_id, category="slide_note",
            source_document=slides_doc, title=f"#{s['slide']} {s['title']}", text=s.get("note", ""),
            keywords=[w for kp in s.get("key_points", []) for w in kp["keywords"]], slide=s["slide"]))
        for kp in s.get("key_points", []):
            chunks.append(KnowledgeChunk(
                chunk_id=f"mm_{kp['kp_id']}", kb_id=kb_id, category="must_mention", source_document=slides_doc,
                title=kp["text"], text=kp["text"], keywords=kp["keywords"], slide=s["slide"],
                essential=kp.get("essential", False), cue=kp["cue"], kp_id=kp["kp_id"]))
    qa_doc = docs.get(by_type.get("expected_qa", ""), {}).get("title", "expected questions")
    for q in raw.get("qa", []):
        chunks.append(KnowledgeChunk(
            chunk_id=q["chunk_id"], kb_id=kb_id, category="expected_qa", source_document=qa_doc,
            title=q["question"], text=f"Q: {q['question']} A: {q['answer']}", keywords=q["question_keywords"],
            question=q["question"], answer_keywords=q["answer_keywords"], topic=q["topic"], cue=q["cue"]))
    gl_doc = docs.get(by_type.get("glossary", ""), {}).get("title", "glossary")
    for g in raw.get("glossary", []):
        chunks.append(KnowledgeChunk(
            chunk_id=g["chunk_id"], kb_id=kb_id, category="glossary", source_document=gl_doc,
            title=g["term"], text=f"{g['term']}: {g['gloss']}", keywords=g["keywords"], term=g["term"],
            gloss=g["gloss"], cue=g["gloss"]))
    return KnowledgeBase(kb_id=kb_id, language=raw["language"], paper_title=raw["paper_title"],
                         synthetic=raw.get("synthetic", True), documents=list(docs.values()),
                         slides=raw["slides"], chunks=chunks)


def build_session(raw: dict, kb: KnowledgeBase, presenter: PresenterProfile) -> PresentationSession:
    sid = raw["session_id"]
    start = datetime.fromisoformat(raw["start_time"])
    planned = float(raw["planned_duration_s"])
    excerpt = float(raw.get("excerpt_start_elapsed", 0.0))
    events: list[ObservedEvent] = []
    truth: dict[str, GroundTruth] = {}
    for i, e in enumerate(sorted(raw["events"], key=lambda x: x["elapsed_time"])):
        e = dict(e)
        gt = e.pop("ground_truth", None) or {}
        slide = kb.slide(int(e["current_slide"]))
        if slide is None:
            raise InvalidInputError(f"Session {sid} references unknown slide {e['current_slide']}.")
        elapsed = float(e["elapsed_time"])
        state = e.pop("presenter_state", None) or {}
        arousal = float(state.get("arousal", 0.4))
        event = ObservedEvent(
            session_id=sid,
            index=i,
            timestamp=(start + timedelta(seconds=elapsed)).isoformat(),
            language=raw["language"],
            slide_title=slide.title,
            slide_expected_content=[kp.text for kp in slide.key_points],
            speech_rate_unit=presenter.speech_rate_unit,
            remaining_time=max(0.0, planned - elapsed),
            presenter_state=PresenterSignal(arousal=arousal, label=_arousal_label(arousal)),
            sources={"transcript": "dataset:mock_stt", "slide": "dataset:mock_slide_tracker",
                     "audience": "dataset:mock_audience_sensor", "presenter_state": "dataset:mock_wearable"},
            **e,
        )
        events.append(event)
        truth[event.event_id] = GroundTruth(event_id=event.event_id, **gt)
    meta = SessionMeta(
        session_id=sid, title=raw["title"], title_ko=raw.get("title_ko", raw["title"]), language=raw["language"],
        scenario_type=raw["scenario_type"], presenter_id=raw["presenter_id"], kb_id=raw["kb_id"],
        start_time=raw["start_time"], planned_duration_s=planned, excerpt_start_elapsed=excerpt,
        description=raw.get("description", ""), event_count=len(events),
        duration_s=(events[-1].elapsed_time - excerpt) if events else 0.0,
    )
    return PresentationSession(meta=meta, events=events, ground_truth=truth)


class PresentationDataset:
    """Read-only access to the synthetic sessions. Files are parsed once and cached."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @cached_property
    def knowledge_bases(self) -> dict[str, KnowledgeBase]:
        out = {}
        for path in sorted((self.root / "knowledge").glob("*.json")):
            kb = build_knowledge_base(_load_json(path))
            out[kb.kb_id] = kb
        return out

    @cached_property
    def presenters(self) -> dict[str, PresenterProfile]:
        raw = _load_json(self.root / "presenters.json")
        return {p["presenter_id"]: PresenterProfile(**p) for p in raw["presenters"]}

    @cached_property
    def sessions(self) -> dict[str, PresentationSession]:
        out = {}
        for path in sorted((self.root / "sessions").glob("*.json")):
            raw = _load_json(path)
            kb = self.knowledge_bases.get(raw["kb_id"])
            presenter = self.presenters.get(raw["presenter_id"])
            if kb is None or presenter is None:
                raise InvalidInputError(f"Session file '{path.name}' references an unknown KB or presenter.")
            session = build_session(raw, kb, presenter)
            out[session.meta.session_id] = session
        return out

    def session(self, session_id: str) -> PresentationSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise NotFoundError(f"Presentation session '{session_id}' not found.") from exc

    def kb(self, kb_id: str) -> KnowledgeBase:
        try:
            return self.knowledge_bases[kb_id]
        except KeyError as exc:
            raise NotFoundError(f"Knowledge base '{kb_id}' not found.") from exc

    def presenter(self, presenter_id: str) -> PresenterProfile:
        try:
            return self.presenters[presenter_id]
        except KeyError as exc:
            raise NotFoundError(f"Presenter '{presenter_id}' not found.") from exc
