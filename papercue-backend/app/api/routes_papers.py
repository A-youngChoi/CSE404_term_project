from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_container
from app.models.paper import IngestionReport, Paper, PaperCreate, PaperDetail, PaperUnitIn, PaperUnitPatch, PaperUpload
from app.services.container import Container

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("", response_model=IngestionReport, status_code=status.HTTP_201_CREATED)
def create_paper(req: PaperCreate, c: Container = Depends(get_container)) -> IngestionReport:
    return c.papers.create(req)


@router.post("/upload", response_model=IngestionReport, status_code=status.HTTP_201_CREATED)
def upload_paper(req: PaperUpload, c: Container = Depends(get_container)) -> IngestionReport:
    """Import a local .txt/.md file (base64). Size, name, and UTF-8 are validated; content is never executed."""
    return c.papers.upload(req)


@router.get("", response_model=list[Paper])
def list_papers(c: Container = Depends(get_container)) -> list[Paper]:
    return c.papers_repo.list()


@router.get("/{paper_id}", response_model=PaperDetail)
def get_paper(paper_id: str, c: Container = Depends(get_container)) -> PaperDetail:
    return c.papers.get_detail(paper_id)


@router.delete("/{paper_id}")
def delete_paper(paper_id: str, c: Container = Depends(get_container)) -> dict:
    c.papers.delete(paper_id)
    c.audit_repo.add("paper_deleted", paper_id)
    return {"paper_id": paper_id, "deleted": True}


@router.post("/{paper_id}/units", response_model=IngestionReport, status_code=status.HTTP_201_CREATED)
def add_units(paper_id: str, units: list[PaperUnitIn], c: Container = Depends(get_container)) -> IngestionReport:
    return c.papers.add_units(paper_id, units)


@router.patch("/{paper_id}/units/{unit_id}", response_model=PaperDetail)
def edit_unit(paper_id: str, unit_id: str, patch: PaperUnitPatch, c: Container = Depends(get_container)) -> PaperDetail:
    return c.papers.update_unit(paper_id, unit_id, patch)


@router.post("/{paper_id}/reindex")
def reindex(paper_id: str, c: Container = Depends(get_container)) -> dict:
    c.papers.get_detail(paper_id)
    count = c.papers.reindex(paper_id)
    return {"paper_id": paper_id, "indexed_units": count, "embedding_model": c.embedder.name}
