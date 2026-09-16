"""Session lifecycle: consent-gated creation, inspection, export, reset, deletion, retention."""

from __future__ import annotations

from datetime import datetime

from app.core.config import Settings
from app.core.errors import ConsentRequiredError, InvalidSessionStateError, NotFoundError
from app.core.logging import get_logger
from app.core.utils import utcnow, utcnow_iso
from app.llm.factory import ProviderRegistry
from app.models.session import CONSENT_NOTICE, Session, SessionCreate, SessionInventory
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.cues import CueRepository, TraceRepository
from app.repositories.papers import PaperRepository
from app.repositories.sessions import AuditRepository, SessionRepository

log = get_logger("sessions")

EXPORT_FORMAT = "papercue-session-export-v1"


class SessionService:
    def __init__(self, settings: Settings, sessions: SessionRepository, papers: PaperRepository,
                 conversation: ConversationRepository, audience: AudienceRepository, cues: CueRepository,
                 traces: TraceRepository, audit: AuditRepository, providers: ProviderRegistry):
        self.settings = settings
        self.sessions = sessions
        self.papers = papers
        self.conversation = conversation
        self.audience = audience
        self.cues = cues
        self.traces = traces
        self.audit = audit
        self.providers = providers

    # ------------------------------------------------------------ helpers
    def to_model(self, row: dict) -> Session:
        expired = datetime.fromisoformat(row["expires_at"]) <= utcnow()
        return Session(
            id=row["id"], paper_id=row["paper_id"], mode=row["mode"], consent_confirmed=bool(row["consent_confirmed"]),
            consent_notice=row["consent_note"], llm_provider=row["llm_provider"],
            llm_model=self.providers.model_label(row["llm_provider"]), cue_language=row["cue_language"],
            label=row["label"], status=row["status"], turn_count=row["turn_count"], created_at=row["created_at"],
            updated_at=row["updated_at"], expires_at=row["expires_at"], is_expired=expired,
            mock_mode=row["llm_provider"] == "mock",
        )

    def require(self, session_id: str, *, for_ingestion: bool = False) -> dict:
        row = self.sessions.get(session_id)
        if not row:
            raise NotFoundError("Session not found.", session_id=session_id)
        if for_ingestion:
            if not row["consent_confirmed"]:
                raise ConsentRequiredError("Conversation ingestion requires confirmed consent.")
            if self.to_model(row).is_expired:
                raise InvalidSessionStateError("This session has passed its retention period. Export or delete it.")
        return row

    # ------------------------------------------------------------ lifecycle
    def create(self, req: SessionCreate) -> Session:
        if not req.consent_confirmed:
            raise ConsentRequiredError(
                "consent_confirmed must be true. PaperCue sessions are for consented research testing only."
            )
        if not self.papers.get_row(req.paper_id):
            raise NotFoundError("Paper not found.", paper_id=req.paper_id)
        sid = self.sessions.create(
            paper_id=req.paper_id, mode=req.mode.value,
            llm_provider=req.llm_provider or self.settings.llm_provider,
            cue_language=req.cue_language or self.settings.default_cue_language,
            consent_note=CONSENT_NOTICE, label=req.label, retention_days=self.settings.retention_days,
        )
        log.info("session=%s created mode=%s", sid, req.mode.value)
        return self.to_model(self.sessions.get(sid))

    def get(self, session_id: str) -> SessionInventory:
        row = self.require(session_id)
        return SessionInventory(session=self.to_model(row), record_counts=self.sessions.record_counts(session_id))

    def list(self) -> list[SessionInventory]:
        return [SessionInventory(session=self.to_model(r), record_counts=self.sessions.record_counts(r["id"]))
                for r in self.sessions.list()]

    def reset(self, session_id: str) -> dict:
        self.require(session_id)
        profile = self.sessions.get_profile(session_id)
        counts = self.sessions.reset(session_id)
        self.audit.add("session_reset", session_id, {"removed": counts})
        log.info("session=%s reset", session_id)
        return {"session_id": session_id, "removed": counts, "profile_kept": bool(profile)}

    def delete(self, session_id: str) -> dict:
        self.require(session_id)
        counts = self.sessions.delete(session_id)
        self.audit.add("session_deleted", session_id, {"removed": counts})
        log.info("session=%s deleted", session_id)
        return {"session_id": session_id, "deleted": True, "removed": counts, "deleted_at": utcnow_iso()}

    def purge_expired(self) -> dict:
        ids = self.sessions.expired_ids()
        for sid in ids:
            self.sessions.delete(sid)
            self.audit.add("retention_purge", sid, {"retention_days": self.settings.retention_days})
        return {"purged_sessions": len(ids), "session_ids": ids}

    def expired(self) -> list[str]:
        return self.sessions.expired_ids()

    # ------------------------------------------------------------ export
    def export(self, session_id: str) -> dict:
        row = self.require(session_id)
        summaries = self.conversation.list_summaries(session_id)
        for s in summaries:
            s.pop("vector", None)
        profile = self.sessions.get_profile(session_id)
        self.audit.add("session_exported", session_id)
        # Deliberately excluded: environment configuration, debug prompts, embeddings, paper full text.
        return {
            "format": EXPORT_FORMAT,
            "exported_at": utcnow_iso(),
            "notice": CONSENT_NOTICE,
            "session": self.to_model(row).model_dump(mode="json"),
            "paper": {"id": row["paper_id"], "title": (self.papers.get_row(row["paper_id"]) or {}).get("title")},
            "profile": profile["fields"] if profile else None,
            "turns": [t.model_dump(mode="json") for t in self.conversation.list_turns(session_id)],
            "conversation_states": self.conversation.list_states(session_id),
            "conversation_summaries": summaries,
            "evidence": [e.model_dump(mode="json") for e in self.audience.list_evidence(session_id)],
            "audience_beliefs": self.audience.list_beliefs(session_id),
            "audience_belief_history": self.audience.list_history(session_id),
            "cue_decisions": self.cues.list_with_cues(session_id),
            "pipeline_traces": [t.model_dump(mode="json") for t in self.traces.list(session_id)],
        }
