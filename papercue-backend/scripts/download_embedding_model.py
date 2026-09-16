"""One-time download of the local embedding model into the Hugging Face cache.

This is the only step that contacts the network, and it transfers model weights only
(no conversation or paper data). Afterwards PaperCue loads the model offline.

    python scripts/download_embedding_model.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")

from app.core.config import get_settings  # noqa: E402


def main() -> int:
    model = get_settings().embedding_model
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("sentence-transformers is not installed. Run: pip install -e '.[embeddings]'")
        return 1
    print(f"Downloading {model} to the local Hugging Face cache ...")
    m = SentenceTransformer(model, device="cpu")
    print(f"OK - embedding dimension {m.get_sentence_embedding_dimension()}. PaperCue will now load it offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
