from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import UnitType


class PaperUnitIn(BaseModel):
    unit_type: UnitType
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=8000)
    short_explanation: str | None = Field(default=None, max_length=400)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    source_reference: str | None = Field(default=None, max_length=200)
    presenter_priority: float = Field(default=0.6, ge=0.0, le=1.0)


class PaperUnit(BaseModel):
    id: str
    paper_id: str
    unit_type: UnitType
    title: str
    content: str
    short_explanation: str
    keywords: list[str]
    source_reference: str | None = None
    presenter_priority: float
    origin: str


class PaperCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    abstract: str = Field(default="", max_length=10_000)
    full_text: str | None = Field(default=None, max_length=400_000, description="Pasted full text (plain text or Markdown).")
    source_file: str | None = Field(
        default=None,
        description="Name of a .txt/.md file inside PAPERS_DIR to read as the full text. Paths outside PAPERS_DIR are refused.",
    )
    key_contributions: list[str] = Field(default_factory=list, max_length=30)
    evidence_results: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    preferred_terminology: dict[str, str] = Field(
        default_factory=dict, description="Map of term to the presenter's preferred wording."
    )
    forbidden_claims: list[str] = Field(default_factory=list, max_length=30, description="Claims cues must never make.")
    units: list[PaperUnitIn] = Field(default_factory=list, description="Presenter-written units (highest trust).")
    extract_units_from_text: bool = Field(
        default=True, description="Split the full text into units and label them with the local model (or mock rules)."
    )
    unit_labeler: Literal["ollama", "mock"] | None = Field(
        default=None, description="Who labels extracted text chunks. Defaults to LLM_PROVIDER."
    )


class PaperUpload(BaseModel):
    """A local text/Markdown file read by the browser and sent to this local backend."""

    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=3_000_000)
    title: str = Field(min_length=1, max_length=300)
    abstract: str = Field(default="", max_length=10_000)
    key_contributions: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    forbidden_claims: list[str] = Field(default_factory=list, max_length=30)
    unit_labeler: Literal["ollama", "mock"] | None = None


class PaperUnitPatch(BaseModel):
    unit_type: UnitType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    short_explanation: str | None = Field(default=None, min_length=1, max_length=400)
    keywords: list[str] | None = Field(default=None, max_length=20)
    source_reference: str | None = Field(default=None, max_length=200)
    presenter_priority: float | None = Field(default=None, ge=0.0, le=1.0)


class Paper(BaseModel):
    id: str
    title: str
    abstract: str
    key_contributions: list[str]
    evidence_results: list[str]
    limitations: list[str]
    preferred_terminology: dict[str, str]
    forbidden_claims: list[str]
    has_full_text: bool
    unit_count: int
    indexed_unit_count: int
    created_at: str
    updated_at: str


class PaperUnitView(PaperUnit):
    indexed: bool


class PaperDetail(Paper):
    units: list[PaperUnitView]
    index_model: str | None = None


class IngestionReport(BaseModel):
    paper: PaperDetail
    units_created: int
    units_indexed: int
    unit_labeler: str
    embedding_model: str
    warnings: list[str] = Field(default_factory=list)
