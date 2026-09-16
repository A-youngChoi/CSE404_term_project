from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_container
from app.core.errors import NotFoundError
from app.models.audience import AudienceModelView, EvidenceItem
from app.models.conversation import Turn, TurnIn, TurnResult
from app.models.cue import CueRequest, CueResponse
from app.models.enums import SessionMode
from app.models.session import ProfileIn, ProfileResult, Session, SessionCreate, SessionInventory
from app.models.trace import STAGE_ORDER, PipelineTrace
from app.services.container import Container

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ------------------------------------------------------------------ lifecycle
@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
def create_session(req: SessionCreate, c: Container = Depends(get_container)) -> Session:
    return c.sessions.create(req)


@router.get("", response_model=list[SessionInventory])
def list_sessions(c: Container = Depends(get_container)) -> list[SessionInventory]:
    return c.sessions.list()


@router.get("/{session_id}", response_model=SessionInventory)
def get_session(session_id: str, c: Container = Depends(get_container)) -> SessionInventory:
    return c.sessions.get(session_id)


@router.delete("/{session_id}")
def delete_session(session_id: str, c: Container = Depends(get_container)) -> dict:
    """Permanently delete the session and every record derived from it."""
    return c.sessions.delete(session_id)


@router.post("/{session_id}/reset")
def reset_session(session_id: str, c: Container = Depends(get_container)) -> dict:
    """Remove turns, evidence, beliefs, states, cues and traces. The session and profile remain;
    profile priors are rebuilt from the stored profile."""
    result = c.sessions.reset(session_id)
    profile = c.sessions_repo.get_profile(session_id)
    if profile:
        c.profiles.apply_profile(c.sessions.require(session_id), ProfileIn(**profile["fields"]))
    return result


@router.get("/{session_id}/export")
def export_session(session_id: str, c: Container = Depends(get_container)) -> dict:
    return c.sessions.export(session_id)


# ------------------------------------------------------------------ profile
@router.post("/{session_id}/profile", response_model=ProfileResult)
def set_profile(session_id: str, profile: ProfileIn, c: Container = Depends(get_container)) -> ProfileResult:
    session = c.sessions.require(session_id, for_ingestion=True)
    return c.profiles.apply_profile(session, profile)


@router.get("/{session_id}/profile")
def get_profile(session_id: str, c: Container = Depends(get_container)) -> dict:
    c.sessions.require(session_id)
    profile = c.sessions_repo.get_profile(session_id)
    return {"session_id": session_id, "profile": profile["fields"] if profile else None}


# ------------------------------------------------------------------ turns
@router.post("/{session_id}/turns", response_model=TurnResult, status_code=status.HTTP_201_CREATED)
def add_turn(session_id: str, req: TurnIn, c: Container = Depends(get_container)) -> TurnResult:
    return c.pipeline.process_turn(session_id, req)


@router.get("/{session_id}/turns", response_model=list[Turn])
def list_turns(session_id: str, c: Container = Depends(get_container)) -> list[Turn]:
    c.sessions.require(session_id)
    return c.conversation_repo.list_turns(session_id)


# ------------------------------------------------------------------ model inspection
@router.get("/{session_id}/audience-model", response_model=AudienceModelView)
def audience_model(session_id: str, c: Container = Depends(get_container)) -> AudienceModelView:
    c.sessions.require(session_id)
    return c.audience.view(session_id)


@router.get("/{session_id}/audience-model/history")
def audience_history(session_id: str, c: Container = Depends(get_container)) -> list[dict]:
    c.sessions.require(session_id)
    return c.audience_repo.list_history(session_id)


@router.get("/{session_id}/conversation-state")
def conversation_state(session_id: str, c: Container = Depends(get_container)) -> dict:
    c.sessions.require(session_id)
    return {
        "current": c.conversation_repo.latest_state(session_id).model_dump(mode="json"),
        "recent_window_turn_ids": [t.id for t in c.tracker.recent_window(session_id)],
        "older_summary": c.tracker.older_summary(session_id).model_dump(),
        "history": c.conversation_repo.list_states(session_id),
    }


@router.get("/{session_id}/evidence", response_model=list[EvidenceItem])
def evidence(session_id: str, c: Container = Depends(get_container)) -> list[EvidenceItem]:
    c.sessions.require(session_id)
    return c.audience_repo.list_evidence(session_id)


# ------------------------------------------------------------------ cues
@router.post("/{session_id}/cue", response_model=CueResponse)
def request_cue(session_id: str, req: CueRequest | None = None, c: Container = Depends(get_container)) -> CueResponse:
    """On-demand cue: generated only because the presenter explicitly asked."""
    return c.pipeline.request_cue(session_id, SessionMode.on_demand)


@router.post("/{session_id}/auto-candidate", response_model=CueResponse)
def auto_candidate(session_id: str, c: Container = Depends(get_container)) -> CueResponse:
    """Evaluate whether a cue *would* be useful. The result is never delivered."""
    return c.pipeline.request_cue(session_id, SessionMode.auto_candidate)


@router.get("/{session_id}/cues")
def list_cues(session_id: str, c: Container = Depends(get_container)) -> list[dict]:
    c.sessions.require(session_id)
    turns = {t.id: t for t in c.conversation_repo.list_turns(session_id)}
    out = []
    for d in c.cues_repo.list_with_cues(session_id):
        turn = turns.get(d["trigger_turn_id"])
        d["trigger_turn_index"] = turn.turn_index if turn else None
        d["trigger_turn_text"] = turn.text if turn else None
        out.append(d)
    return out


# ------------------------------------------------------------------ traces
@router.get("/{session_id}/traces", response_model=list[PipelineTrace])
def list_traces(session_id: str, c: Container = Depends(get_container)) -> list[PipelineTrace]:
    c.sessions.require(session_id)
    return c.traces_repo.list(session_id)


@router.get("/{session_id}/turns/{turn_id}/trace")
def turn_trace(session_id: str, turn_id: str, c: Container = Depends(get_container)) -> dict:
    """Everything needed to explain one turn: its trace, any cue traces it triggered, and resolved references."""
    c.sessions.require(session_id)
    turn = c.conversation_repo.get_turn(turn_id)
    if not turn or turn.session_id != session_id:
        raise NotFoundError("Turn not found.", turn_id=turn_id)
    traces = [t for t in c.traces_repo.list(session_id) if t.turn_id == turn_id]
    evidence_items = c.audience_repo.list_evidence(session_id, turn_id)
    ev_ids = {e.id for e in evidence_items}
    history = [h for h in c.audience_repo.list_history(session_id) if h["evidence_id"] in ev_ids]
    window = [t.model_dump(mode="json") for t in c.conversation_repo.list_turns(session_id)
              if turn.turn_index - c.settings.recent_turn_window <= t.turn_index < turn.turn_index]
    unit_ids = {i for t in traces for s in t.stages for i in s.output_reference_ids + s.input_reference_ids}
    units = c.papers_repo.get_units(list(unit_ids))
    after = c.conversation_repo.state_for_turn(turn_id)
    return {
        "stage_order": STAGE_ORDER,
        "turn": turn.model_dump(mode="json"),
        "recent_turns": window,
        "state_before": c.conversation_repo.state_before_turn(session_id, turn.turn_index).model_dump(mode="json"),
        "state_after": after.model_dump(mode="json") if after else None,
        "evidence": [e.model_dump(mode="json") for e in evidence_items],
        "belief_changes": history,
        "units": {k: v.model_dump(mode="json") for k, v in units.items()},
        "traces": [t.model_dump(mode="json") for t in traces],
    }
