from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.services.container import Container

router = APIRouter(tags=["system"])


@router.get("/health")
def health(c: Container = Depends(get_container)) -> dict:
    return {"status": "ok", "local_only": True, "mock_default": c.settings.is_mock}


@router.get("/config/status")
def config_status(c: Container = Depends(get_container)) -> dict:
    """Local configuration status. Contains no secrets, environment dump, or absolute home paths."""
    return c.status.status()


@router.get("/data/inventory")
def data_inventory(c: Container = Depends(get_container)) -> dict:
    """Everything this prototype has stored, as counts per session."""
    return {
        "papers": [p.model_dump() | {"abstract": None} for p in c.papers_repo.list()],
        "sessions": [s.model_dump(mode="json") for s in c.sessions.list()],
        "expired_session_ids": c.sessions.expired(),
    }


@router.get("/retention/expired")
def retention_expired(c: Container = Depends(get_container)) -> dict:
    return {"retention_days": c.settings.retention_days, "expired_session_ids": c.sessions.expired()}


@router.post("/retention/purge")
def retention_purge(c: Container = Depends(get_container)) -> dict:
    """Permanently delete every session past its retention period (manual trigger)."""
    return c.sessions.purge_expired()


@router.get("/audit")
def audit_events(c: Container = Depends(get_container)) -> list[dict]:
    return c.audit_repo.list()
