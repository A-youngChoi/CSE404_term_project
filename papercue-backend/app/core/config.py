"""Application configuration.

All settings come from environment variables (or a local `.env` file). Defaults are
chosen to be safe for a local research prototype: loopback binding, local Ollama,
local embeddings, no prompt storage.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def is_loopback_host(host: str) -> bool:
    host = (host or "").strip().strip("[]")
    if host in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    papercue_host: str = "127.0.0.1"
    papercue_port: int = 8000
    papercue_allow_non_local_bind: bool = False
    # Browser origins allowed by CORS (the Vite dev server). The built dashboard is same-origin.
    dashboard_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    # FastAPI's Swagger page loads its JS/CSS from a public CDN (no data is sent). Disable if undesired.
    api_docs_enabled: bool = True
    dashboard_dist_dir: Path = Path("./dashboard/dist")

    # Storage
    database_path: Path = Path("./data/papercue.db")
    papers_dir: Path = Path("./data/papers")
    exports_dir: Path = Path("./data/exports")
    retention_days: int = Field(default=7, ge=0)
    purge_expired_on_startup: bool = False
    max_paper_file_bytes: int = 2_000_000
    max_turn_chars: int = 2_000
    max_upload_bytes: int = 1_000_000

    # Local LLM
    llm_provider: Literal["ollama", "mock"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_timeout_seconds: float = 60.0
    ollama_temperature: float = 0.1
    ollama_allow_remote: bool = False
    llm_max_retries: int = Field(default=1, ge=0, le=3)

    # Embeddings
    embedding_provider: Literal["sentence_transformers", "hashing"] = "sentence_transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_allow_download: bool = False

    # Reasoning parameters
    recent_turn_window: int = Field(default=6, ge=2, le=30)
    profile_confidence_cap: float = Field(default=0.65, gt=0.0, lt=1.0)
    profile_decay_per_turn: float = Field(default=0.97, gt=0.0, le=1.0)
    min_cue_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    auto_candidate_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    cue_max_words: int = Field(default=12, ge=2, le=12)
    cue_target_words: int = 7
    retrieval_top_k: int = Field(default=4, ge=1, le=10)
    retrieval_min_score: float = 0.15
    explained_unit_threshold: float = 0.45
    recent_cue_memory: int = 5
    cue_repeat_similarity: float = 0.6
    audience_update_strategy: Literal["rules", "llm"] = "llm"
    # After every listener turn, run retrieval + intervention scoring (deterministic, no generation, no delivery)
    # so each turn trace shows what the decision engine would have done.
    shadow_decision_on_turn: bool = True
    default_cue_language: Literal["ko", "en"] = "en"

    # Debug
    debug_store_prompts: bool = False
    log_level: str = "INFO"

    @field_validator("profile_confidence_cap")
    @classmethod
    def _cap_is_prior(cls, v: float) -> float:
        if v > 0.8:
            raise ValueError("PROFILE_CONFIDENCE_CAP must stay a weak prior (<= 0.8)")
        return v

    @model_validator(mode="after")
    def _enforce_local_only(self) -> "Settings":
        if not is_loopback_host(self.papercue_host) and not self.papercue_allow_non_local_bind:
            raise ValueError(
                f"PAPERCUE_HOST={self.papercue_host!r} is not a loopback address. PaperCue binds to "
                "127.0.0.1 only. Set PAPERCUE_ALLOW_NON_LOCAL_BIND=true to override (not for participant data)."
            )
        for origin in self.cors_origins:
            if origin == "*" or not is_loopback_host(urlparse(origin).hostname or ""):
                raise ValueError("DASHBOARD_ORIGINS may only list loopback origins (no wildcard).")
        parsed = urlparse(self.ollama_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"OLLAMA_BASE_URL is not a valid URL: {self.ollama_base_url!r}")
        if not is_loopback_host(parsed.hostname) and not self.ollama_allow_remote:
            raise ValueError(
                "OLLAMA_BASE_URL must point to a local Ollama instance (127.0.0.1/localhost). "
                "Remote model hosts are refused unless OLLAMA_ALLOW_REMOTE=true."
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.dashboard_origins.split(",") if o.strip()]

    @property
    def ollama_is_remote(self) -> bool:
        return not is_loopback_host(urlparse(self.ollama_base_url).hostname or "")

    @property
    def llm_label(self) -> str:
        return "mock:deterministic-rules" if self.llm_provider == "mock" else f"ollama:{self.ollama_model}"

    @property
    def embedding_label(self) -> str:
        if self.embedding_provider == "hashing":
            return "mock:hashing-embedder"
        return f"sentence_transformers:{self.embedding_model}"

    @property
    def is_mock(self) -> bool:
        return self.llm_provider == "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
