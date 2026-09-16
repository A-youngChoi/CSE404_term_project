"""Paper Ingestion Service: paper -> structured PaperUnits -> local semantic index."""

from __future__ import annotations

import base64
import binascii
import os
import re
from pathlib import Path

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidInputError, LocalConfigurationError, NotFoundError, PayloadTooLargeError
from app.core.logging import get_logger
from app.core.text import chunk_paragraphs, first_sentence, split_sections, top_keywords
from app.core.utils import new_id
from app.llm.factory import ProviderRegistry
from app.models.enums import UnitType
from app.models.llm_outputs import PaperUnitLabelsOutput
from app.models.paper import (
    IngestionReport,
    PaperCreate,
    PaperDetail,
    PaperUnitIn,
    PaperUnitPatch,
    PaperUnitView,
    PaperUpload,
)
from app.repositories.papers import PaperRepository
from app.services.embeddings import Embedder

log = get_logger("paper_ingestion")

ALLOWED_SUFFIXES = {".txt", ".md", ".markdown"}
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9가-힣._-]+")
_LABEL_BATCH = 6
_CHUNK_CHAR_LIMIT = 1500


def unit_embedding_text(title: str, short_explanation: str, keywords: list[str], content: str) -> str:
    return f"{title}. {short_explanation}. {', '.join(keywords)}. {content}"


def safe_filename(name: str) -> str:
    """Reject anything path-like; normalise the rest."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith(".") or "\x00" in name:
        raise InvalidInputError("Invalid file name. Use a plain .txt or .md file name without folders.")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise InvalidInputError("Only .txt and .md files can be imported.")
    stem = _SAFE_NAME_RE.sub("_", Path(name).stem)[:80] or "paper"
    return f"{stem}{suffix}"


def decode_utf8(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidInputError("The file is not valid UTF-8 text.") from exc
    if "\x00" in text:
        raise InvalidInputError("The file contains binary data.")
    return text


class PaperService:
    def __init__(self, settings: Settings, repo: PaperRepository, embedder: Embedder, providers: ProviderRegistry):
        self.settings = settings
        self.repo = repo
        self.embedder = embedder
        self.providers = providers

    # ------------------------------------------------------------ reading
    def get_detail(self, paper_id: str) -> PaperDetail:
        row = self.repo.get_row(paper_id)
        if not row:
            raise NotFoundError("Paper not found.", paper_id=paper_id)
        base = self.repo.to_model(row)
        indexed = self.repo.indexed_unit_ids(paper_id)
        _, _, models = self.repo.load_embeddings(paper_id)
        units = [PaperUnitView(**u.model_dump(), indexed=u.id in indexed) for u in self.repo.list_units(paper_id)]
        return PaperDetail(**base.model_dump(), units=units, index_model=next(iter(models), None))

    # ------------------------------------------------------------ files
    def read_local_file(self, name: str) -> str:
        root = self.settings.papers_dir.resolve()
        candidate = (root / name).resolve()
        if root not in candidate.parents:
            raise InvalidInputError("source_file must be a file inside the configured papers directory.")
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            raise InvalidInputError("Only .txt and .md files can be imported.")
        if not candidate.is_file():
            raise NotFoundError("source_file was not found in the papers directory.")
        if candidate.stat().st_size > self.settings.max_paper_file_bytes:
            raise PayloadTooLargeError("The paper file is too large.")
        return decode_utf8(candidate.read_bytes())

    def upload(self, req: PaperUpload) -> IngestionReport:
        name = safe_filename(req.filename)
        try:
            data = base64.b64decode(req.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidInputError("File content is not valid base64.") from exc
        if len(data) > self.settings.max_upload_bytes:
            raise PayloadTooLargeError(
                f"The file exceeds the upload limit of {self.settings.max_upload_bytes} bytes."
            )
        text = decode_utf8(data)
        root = self.settings.papers_dir
        root.mkdir(parents=True, exist_ok=True)
        stored = root / f"{new_id()[:8]}_{name}"
        # Stored as inert text with owner-only permissions; never executed.
        fd = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.create(
            PaperCreate(
                title=req.title, abstract=req.abstract, full_text=text, key_contributions=req.key_contributions,
                limitations=req.limitations, forbidden_claims=req.forbidden_claims, unit_labeler=req.unit_labeler,
            )
        )

    # ------------------------------------------------------------ create
    def _presenter_units(self, req: PaperCreate) -> list[PaperUnitIn]:
        units = list(req.units)
        if req.abstract:
            units.append(PaperUnitIn(unit_type=UnitType.core_idea, title="Abstract", content=req.abstract,
                                     source_reference="Abstract", presenter_priority=0.8))
        for i, text in enumerate(req.key_contributions, 1):
            units.append(PaperUnitIn(unit_type=UnitType.contribution, title=f"Contribution {i}", content=text,
                                     source_reference="Presenter: key contributions", presenter_priority=0.9))
        for i, text in enumerate(req.evidence_results, 1):
            units.append(PaperUnitIn(unit_type=UnitType.result, title=f"Result {i}", content=text,
                                     source_reference="Presenter: evidence and results", presenter_priority=0.8))
        for i, text in enumerate(req.limitations, 1):
            units.append(PaperUnitIn(unit_type=UnitType.limitation, title=f"Limitation {i}", content=text,
                                     source_reference="Presenter: limitations", presenter_priority=0.7))
        return units

    def _label_chunks(self, full_text: str, labeler: str) -> tuple[list[PaperUnitIn], list[str], str]:
        chunks: list[dict] = []
        for heading, body in split_sections(full_text):
            for piece in chunk_paragraphs(body):
                chunks.append({"chunk_index": len(chunks), "heading": heading, "text": piece})
        provider = self.providers.get(labeler)
        warnings: list[str] = []
        labels: dict[int, object] = {}
        for start in range(0, len(chunks), _LABEL_BATCH):
            batch = chunks[start : start + _LABEL_BATCH]
            payload = {"chunks": [{**c, "text": c["text"][:_CHUNK_CHAR_LIMIT]} for c in batch]}
            out = provider.generate_structured("paper_unit_labeling", payload, PaperUnitLabelsOutput)
            valid = {c["chunk_index"] for c in batch}
            for label in out.labels:
                if label.chunk_index in valid:
                    labels[label.chunk_index] = label
        units: list[PaperUnitIn] = []
        for c in chunks:
            label = labels.get(c["chunk_index"])
            if label is None:
                warnings.append(f"chunk {c['chunk_index']} was not labelled; stored as core_idea with heuristic title")
                units.append(PaperUnitIn(unit_type=UnitType.core_idea, title=c["heading"] or "Untitled section",
                                         content=c["text"], short_explanation=first_sentence(c["text"]),
                                         keywords=top_keywords(c["text"]), source_reference=c["heading"],
                                         presenter_priority=0.4))
                continue
            # Keep only keywords that literally occur in the chunk (grounding check on model labels).
            kws = [k for k in label.keywords if k.lower() in c["text"].lower()] or top_keywords(c["text"])
            units.append(PaperUnitIn(unit_type=label.unit_type, title=label.title, content=c["text"],
                                     short_explanation=label.short_explanation, keywords=kws,
                                     source_reference=c["heading"], presenter_priority=0.5))
        return units, warnings, provider.name

    def create(self, req: PaperCreate) -> IngestionReport:
        full_text = req.full_text
        if req.source_file:
            if full_text:
                raise InvalidInputError("Provide either full_text or source_file, not both.")
            full_text = self.read_local_file(req.source_file)
        labeler_name = "presenter-only"
        extracted: list[PaperUnitIn] = []
        warnings: list[str] = []
        if full_text and req.extract_units_from_text:
            # Label before writing anything, so a local-model failure leaves no partial paper.
            extracted, warnings, labeler_name = self._label_chunks(
                full_text, req.unit_labeler or self.settings.llm_provider
            )
        presenter_units = self._presenter_units(req)
        if not presenter_units and not extracted:
            raise InvalidInputError("The paper has no content: supply an abstract, units, or full text.")

        paper_id = self.repo.create(
            title=req.title, abstract=req.abstract, full_text=full_text, key_contributions=req.key_contributions,
            evidence_results=req.evidence_results, limitations=req.limitations,
            preferred_terminology=req.preferred_terminology, forbidden_claims=req.forbidden_claims,
        )
        origin = "mock" if labeler_name.startswith("mock") else "llm"
        created = self._store_units(paper_id, presenter_units, "presenter")
        created += self._store_units(paper_id, extracted, origin)
        indexed, index_warning = self._try_reindex(paper_id)
        if index_warning:
            warnings.append(index_warning)
        log.info("paper=%s units=%d indexed=%d labeler=%s", paper_id, created, indexed, labeler_name)
        return IngestionReport(paper=self.get_detail(paper_id), units_created=created, units_indexed=indexed,
                               unit_labeler=labeler_name, embedding_model=self.embedder.name, warnings=warnings)

    def _store_units(self, paper_id: str, units: list[PaperUnitIn], origin: str) -> int:
        for u in units:
            short = u.short_explanation or first_sentence(u.content)
            kws = [k.strip() for k in u.keywords if k.strip()] or top_keywords(f"{u.title} {u.content}")
            self.repo.add_unit(paper_id, u, short, kws, origin)
        return len(units)

    def add_units(self, paper_id: str, units: list[PaperUnitIn]) -> IngestionReport:
        self.get_detail(paper_id)
        created = self._store_units(paper_id, units, "presenter")
        indexed, warning = self._try_reindex(paper_id)
        return IngestionReport(paper=self.get_detail(paper_id), units_created=created, units_indexed=indexed,
                               unit_labeler="presenter-only", embedding_model=self.embedder.name,
                               warnings=[warning] if warning else [])

    def update_unit(self, paper_id: str, unit_id: str, patch: PaperUnitPatch) -> PaperDetail:
        unit = self.repo.get_unit(unit_id)
        if not unit or unit.paper_id != paper_id:
            raise NotFoundError("Unit not found.", unit_id=unit_id)
        fields = patch.model_dump(exclude_none=True)
        if not fields:
            raise InvalidInputError("No fields to update.")
        self.repo.update_unit(unit_id, fields)
        self._try_reindex(paper_id)
        return self.get_detail(paper_id)

    # ------------------------------------------------------------ index
    def reindex(self, paper_id: str) -> int:
        units = self.repo.list_units(paper_id)
        if not units:
            return 0
        texts = [unit_embedding_text(u.title, u.short_explanation, u.keywords, u.content) for u in units]
        matrix = self.embedder.embed(texts)
        self.repo.replace_embeddings(paper_id, [u.id for u in units], matrix, self.embedder.name)
        self.repo.touch(paper_id)
        return len(units)

    def _try_reindex(self, paper_id: str) -> tuple[int, str | None]:
        try:
            return self.reindex(paper_id), None
        except LocalConfigurationError as exc:
            return 0, f"Units were stored but not indexed: {exc.message}"

    def delete(self, paper_id: str) -> None:
        self.get_detail(paper_id)
        if self.repo.session_count(paper_id):
            raise ConflictError("Delete the sessions that use this paper first.", paper_id=paper_id)
        self.repo.delete(paper_id)
