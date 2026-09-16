"""Local embedding providers and cosine similarity.

* SentenceTransformerEmbedder - a real local sentence-transformer model. Loaded from the
  local Hugging Face cache only, unless EMBEDDING_ALLOW_DOWNLOAD=true.
* HashingEmbedder - deterministic feature hashing for tests and demos (clearly labelled mock).
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Protocol

import numpy as np

from app.core.config import Settings
from app.core.errors import LocalConfigurationError
from app.core.text import content_words


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Bag of content words + character trigrams hashed into a fixed vector. Not semantic."""

    name = "mock:hashing-embedder"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks = content_words(text)
        feats = [f"w:{t}" for t in toks]
        feats += [f"b:{a}_{b}" for a, b in zip(toks, toks[1:])]
        for t in toks:
            padded = f"#{t}#"
            feats += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                weight = 1.0 if feat[0] in "wb" else 0.3
                out[row, idx] += sign * weight
        return normalize_rows(out)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, allow_download: bool) -> None:
        self.model_name = model_name
        self.name = f"sentence_transformers:{model_name}"
        self.allow_download = allow_download
        self._model = None
        self._lock = threading.Lock()
        self.dim = 0

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            os.environ.setdefault("DO_NOT_TRACK", "1")
            if not self.allow_download:
                os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                from sentence_transformers import SentenceTransformer  # heavy import, done lazily
            except ImportError as exc:
                raise LocalConfigurationError(
                    "sentence-transformers is not installed. Run `pip install -e .[embeddings]`, "
                    "or set EMBEDDING_PROVIDER=hashing for mock/demo use.",
                    component="embeddings",
                ) from exc
            try:
                self._model = SentenceTransformer(
                    self.model_name, device="cpu", local_files_only=not self.allow_download
                )
            except Exception as exc:  # noqa: BLE001 - library raises many error types
                raise LocalConfigurationError(
                    f"The local embedding model '{self.model_name}' could not be loaded from the local cache. "
                    "Run `python scripts/download_embedding_model.py` once, or set EMBEDDING_ALLOW_DOWNLOAD=true.",
                    component="embeddings",
                ) from exc
            self.dim = int(self._model.get_sentence_embedding_dimension())
            return self._model

    def is_loaded(self) -> bool:
        return self._model is not None

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    return SentenceTransformerEmbedder(settings.embedding_model, settings.embedding_allow_download)


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def cosine_scores(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    q = query / (np.linalg.norm(query) or 1.0)
    return normalize_rows(matrix) @ q


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")
