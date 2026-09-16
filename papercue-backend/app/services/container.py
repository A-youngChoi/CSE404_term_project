"""Wires repositories and services together (simple manual dependency injection)."""

from __future__ import annotations

from app.core.config import Settings
from app.core.context import current_session_id
from app.db.database import Database
from app.llm.base import LocalLLMProvider
from app.llm.factory import ProviderRegistry
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository
from app.repositories.cues import CueRepository, TraceRepository
from app.repositories.papers import PaperRepository
from app.repositories.sessions import AuditRepository, SessionRepository
from app.services.audience_model import AudienceModelService
from app.services.conversation_tracker import ConversationTracker
from app.services.cue_decision import CueDecisionEngine
from app.services.cue_generator import CueGenerator
from app.services.embeddings import Embedder, build_embedder
from app.services.evidence_extractor import EvidenceExtractor
from app.services.paper_ingestion import PaperService
from app.services.pipeline import PipelineService
from app.services.profile_service import ProfileService
from app.services.retrieval import RetrievalService
from app.services.session_service import SessionService
from app.services.system_status import SystemStatusService


class Container:
    def __init__(self, settings: Settings, *, embedder: Embedder | None = None,
                 ollama: LocalLLMProvider | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)
        self.embedder = embedder or build_embedder(settings)

        self.papers_repo = PaperRepository(self.db)
        self.sessions_repo = SessionRepository(self.db)
        self.conversation_repo = ConversationRepository(self.db)
        self.audience_repo = AudienceRepository(self.db)
        self.cues_repo = CueRepository(self.db)
        self.traces_repo = TraceRepository(self.db)
        self.audit_repo = AuditRepository(self.db)

        sink = self._debug_sink if settings.debug_store_prompts else None
        self.providers = ProviderRegistry(settings, debug_sink=sink, ollama=ollama)

        self.papers = PaperService(settings, self.papers_repo, self.embedder, self.providers)
        self.retrieval = RetrievalService(settings, self.papers_repo, self.embedder)
        self.audience = AudienceModelService(settings, self.audience_repo, self.conversation_repo)
        self.sessions = SessionService(settings, self.sessions_repo, self.papers_repo, self.conversation_repo,
                                       self.audience_repo, self.cues_repo, self.traces_repo, self.audit_repo,
                                       self.providers)
        self.profiles = ProfileService(settings, self.sessions_repo, self.audience_repo, self.audience,
                                       self.conversation_repo, self.retrieval)
        self.tracker = ConversationTracker(settings, self.conversation_repo, self.audience_repo, self.embedder)
        self.extractor = EvidenceExtractor(self.audience_repo)
        self.decision = CueDecisionEngine(settings)
        self.generator = CueGenerator(settings)
        self.pipeline = PipelineService(
            settings, self.providers, self.embedder, self.sessions_repo, self.sessions, self.papers_repo,
            self.conversation_repo, self.audience_repo, self.cues_repo, self.traces_repo, self.tracker,
            self.extractor, self.audience, self.retrieval, self.decision, self.generator,
        )
        self.status = SystemStatusService(settings, self.db, self.providers, self.embedder, self.traces_repo)

    def _debug_sink(self, task: str, prompt: str, response: str | None) -> None:
        # Only reachable when DEBUG_STORE_PROMPTS=true. Never enable with participant data.
        self.traces_repo.add_debug_prompt(current_session_id.get(), task, prompt, response)

    def close(self) -> None:
        self.db.close()
