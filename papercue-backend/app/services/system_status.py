"""Researcher-facing system status. Never exposes secrets, env dumps, or absolute home paths."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from app.core.config import Settings, is_loopback_host
from app.db.database import SCHEMA_VERSION, Database
from app.llm.factory import ProviderRegistry
from app.models.trace import PIPELINE_VERSION
from app.prompts.v1 import VERSION as PROMPT_VERSION
from app.repositories.cues import TraceRepository
from app.services.embeddings import Embedder, SentenceTransformerEmbedder

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SENSITIVE_TRACKED_PATTERNS = [
    ".env", ".env.*", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "data/*", "*/data/*", "exports/*", "uploads/*",
    "*.log", "*.pem", "*.key", "id_rsa*",
]
ALLOWED_TRACKED = {".env.example"}


def display_path(path: Path) -> str:
    """Show a path relative to the project, never an absolute home-directory path."""
    resolved = path.resolve()
    try:
        return "./" + str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return f"<outside project>/{resolved.name}"


def tracked_sensitive_files() -> dict:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=PROJECT_ROOT, capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
        files = subprocess.run(["git", "ls-files"], cwd=top, capture_output=True, text=True, timeout=5,
                               check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return {"checked": False, "files": []}
    flagged = []
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name in ALLOWED_TRACKED or name == ".gitkeep":
            continue
        if any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(name, p) for p in SENSITIVE_TRACKED_PATTERNS):
            flagged.append(f)
    return {"checked": True, "files": flagged[:20]}


class SystemStatusService:
    def __init__(self, settings: Settings, db: Database, providers: ProviderRegistry, embedder: Embedder,
                 traces: TraceRepository):
        self.settings = settings
        self.db = db
        self.providers = providers
        self.embedder = embedder
        self.traces = traces

    def status(self) -> dict:
        s = self.settings
        try:
            counts = self.db.fetchone(
                "SELECT (SELECT COUNT(*) FROM papers) AS papers, (SELECT COUNT(*) FROM sessions) AS sessions, "
                "(SELECT COUNT(*) FROM sessions WHERE status = 'active') AS active_sessions"
            )
            db_ok = True
        except Exception:  # noqa: BLE001
            counts, db_ok = {"papers": 0, "sessions": 0, "active_sessions": 0}, False
        ollama = self.providers.get("ollama").status()
        if isinstance(self.embedder, SentenceTransformerEmbedder):
            emb = {"provider": "sentence_transformers", "model": s.embedding_model, "loaded": self.embedder.is_loaded(),
                   "allow_download": s.embedding_allow_download, "simulated": False}
        else:
            emb = {"provider": "hashing", "model": self.embedder.name, "loaded": True, "allow_download": False,
                   "simulated": True}
        git = tracked_sensitive_files()
        warnings = []
        if not is_loopback_host(s.papercue_host):
            warnings.append("non_local_bind")
        if s.debug_store_prompts:
            warnings.append("debug_prompts_enabled")
        if not ollama["available"]:
            warnings.append("ollama_unavailable")
        elif not ollama["model_installed"]:
            warnings.append("ollama_model_missing")
        if s.ollama_is_remote:
            warnings.append("remote_model_endpoint")
        if git["files"]:
            warnings.append("sensitive_files_tracked")
        if s.llm_provider == "mock":
            warnings.append("default_mock_mode")
        return {
            "backend": {"status": "ok", "pipeline_version": PIPELINE_VERSION, "prompt_version": PROMPT_VERSION,
                        "schema_version": SCHEMA_VERSION},
            "database": {"ok": db_ok, "location": display_path(s.database_path),
                         "papers": counts["papers"], "sessions": counts["sessions"],
                         "active_sessions": counts["active_sessions"]},
            "llm": {"default_provider": s.llm_provider, "ollama_model": s.ollama_model,
                    "ollama_endpoint_is_local": not s.ollama_is_remote, "ollama_available": ollama["available"],
                    "ollama_model_installed": ollama["model_installed"], "ollama_error_code": ollama["error_code"],
                    "temperature": s.ollama_temperature, "max_retries": s.llm_max_retries,
                    "audience_update_strategy": s.audience_update_strategy},
            "embeddings": emb,
            "network": {"host": s.papercue_host, "port": s.papercue_port,
                        "loopback_only": is_loopback_host(s.papercue_host),
                        "cors_origins": s.cors_origins, "external_services": [],
                        "api_docs_enabled": s.api_docs_enabled},
            "privacy": {"retention_days": s.retention_days, "purge_on_startup": s.purge_expired_on_startup,
                        "debug_store_prompts": s.debug_store_prompts, "papers_dir": display_path(s.papers_dir),
                        "max_upload_bytes": s.max_upload_bytes, "max_turn_chars": s.max_turn_chars},
            "parameters": {"recent_turn_window": s.recent_turn_window,
                           "profile_confidence_cap": s.profile_confidence_cap,
                           "profile_decay_per_turn": s.profile_decay_per_turn,
                           "min_cue_confidence": s.min_cue_confidence,
                           "auto_candidate_threshold": s.auto_candidate_threshold,
                           "cue_max_words": s.cue_max_words, "retrieval_top_k": s.retrieval_top_k},
            "git": git,
            "recent_errors": self.traces.recent_errors(10),
            "warnings": warnings,
        }
