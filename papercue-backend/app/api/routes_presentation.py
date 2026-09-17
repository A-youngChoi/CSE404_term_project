"""Presentation-support simulation API (synthetic data only)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.services.container import Container

router = APIRouter(prefix="/presentation", tags=["presentation simulation"])


class RunCreate(BaseModel):
    session_id: str = Field(max_length=100)
    seed: int | None = None
    judge_provider: Literal["mock", "ollama"] | None = None
    prompt_provider: Literal["template", "ollama"] | None = None
    retriever: Literal["keyword", "embedding"] | None = None
    judge_noise: float | None = Field(default=None, ge=0.0, le=0.2)


class StepRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=500)


class SeekRequest(BaseModel):
    index: int = Field(ge=0, le=10_000)


class PlaybackRequest(BaseModel):
    playing: bool
    speed: float = Field(default=1.0, gt=0.0, le=100.0)


@router.get("/config")
def presentation_config(c: Container = Depends(get_container)) -> dict:
    return c.presentation.config_status()


@router.get("/sessions")
def list_sessions(c: Container = Depends(get_container)) -> list[dict]:
    return c.presentation.list_sessions()


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, c: Container = Depends(get_container)) -> dict:
    return c.presentation.session_detail(session_id)


@router.get("/knowledge")
def knowledge(kb_id: str | None = None, c: Container = Depends(get_container)) -> list[dict]:
    return c.presentation.knowledge(kb_id)


@router.post("/runs", status_code=201)
def create_run(body: RunCreate, c: Container = Depends(get_container)) -> dict:
    run = c.presentation.create_run(body.session_id, **body.model_dump(exclude={"session_id"}))
    return c.presentation.run_view(run.run_id)


@router.get("/runs")
def list_runs(c: Container = Depends(get_container)) -> list[dict]:
    return [r.summary() for r in c.presentation.runs.values()]


@router.get("/runs/{run_id}")
def run_view(run_id: str, since: int = Query(default=0, ge=0), c: Container = Depends(get_container)) -> dict:
    return c.presentation.run_view(run_id, since)


@router.post("/runs/{run_id}/step")
def step(run_id: str, body: StepRequest | None = None, c: Container = Depends(get_container)) -> dict:
    run = c.presentation.get_run(run_id)
    before = run.cursor
    c.presentation.step(run_id, (body or StepRequest()).count)
    return c.presentation.run_view(run_id, since=before)


@router.post("/runs/{run_id}/seek")
def seek(run_id: str, body: SeekRequest, c: Container = Depends(get_container)) -> dict:
    c.presentation.seek(run_id, body.index)
    return c.presentation.run_view(run_id)


@router.post("/runs/{run_id}/reset")
def reset(run_id: str, c: Container = Depends(get_container)) -> dict:
    c.presentation.reset(run_id)
    return c.presentation.run_view(run_id)


@router.post("/runs/{run_id}/complete")
def complete(run_id: str, c: Container = Depends(get_container)) -> dict:
    run = c.presentation.get_run(run_id)
    c.presentation.seek(run_id, run.total)
    return c.presentation.run_view(run_id)


@router.post("/runs/{run_id}/playback")
def playback(run_id: str, body: PlaybackRequest, c: Container = Depends(get_container)) -> dict:
    run = c.presentation.set_playback(run_id, body.playing, body.speed)
    return run.summary()


@router.get("/runs/{run_id}/mobile")
def run_mobile(run_id: str, c: Container = Depends(get_container)) -> dict:
    c.presentation.get_run(run_id)
    return c.presentation.mobile_state(run_id)


@router.get("/runs/{run_id}/evaluation")
def run_evaluation(run_id: str, c: Container = Depends(get_container)) -> dict:
    return c.presentation.evaluation_for(c.presentation.get_run(run_id))


@router.get("/runs/{run_id}/export")
def run_export(run_id: str, c: Container = Depends(get_container)) -> dict:
    return {"run_id": run_id, "synthetic": True, "rows": c.presentation.export_rows(run_id)}


@router.get("/mobile")
def active_mobile(c: Container = Depends(get_container)) -> dict:
    """Minimal state for the presenter's phone view: the most recently used run."""
    return c.presentation.mobile_state()


@router.get("/evaluation")
def evaluation(seed: int | None = None, judge_provider: Literal["mock", "ollama"] | None = None,
               retriever: Literal["keyword", "embedding"] | None = None,
               c: Container = Depends(get_container)) -> dict:
    return c.presentation.evaluate_all(seed=seed, judge_provider=judge_provider, retriever=retriever)
