"""Regenerate dashboard test fixtures from the real backend (mock mode, synthetic sample data only).

    python scripts/export_dashboard_fixtures.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

OUT = ROOT / "dashboard" / "tests" / "fixtures"


def load(name: str):
    return json.loads((ROOT / "sample_data" / name).read_text(encoding="utf-8"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(_env_file=None, database_path=Path(tmp) / "f.db", llm_provider="mock",
                            embedding_provider="hashing", papers_dir=Path(tmp) / "p",
                            dashboard_dist_dir=Path(tmp) / "none", log_level="WARNING")
        with TestClient(create_app(settings)) as c:
            w = load("ko_walkthrough.json")
            pid = c.post("/api/v1/papers", json=w["paper"]).json()["paper"]["id"]
            sid = c.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True, "llm_provider": "mock",
                                                   "cue_language": "ko", "label": "fixture"}).json()["id"]
            c.post(f"/api/v1/sessions/{sid}/profile",
                   json={**w["profile"], "familiar_methods": ["language models"]})
            c.post(f"/api/v1/sessions/{sid}/turns", json=w["turns"][0])
            c.post(f"/api/v1/sessions/{sid}/turns",
                   json={"speaker": "listener", "text": "I'm not familiar with language models."})
            listener = c.post(f"/api/v1/sessions/{sid}/turns", json=w["turns"][1]).json()
            c.post(f"/api/v1/sessions/{sid}/cue")
            c.post(f"/api/v1/sessions/{sid}/cue")  # duplicate -> no cue
            tid = listener["turn"]["id"]
            fixtures = {
                "status": c.get("/api/v1/config/status").json(),
                "session": c.get(f"/api/v1/sessions/{sid}").json(),
                "paper": c.get(f"/api/v1/papers/{pid}").json(),
                "turns": c.get(f"/api/v1/sessions/{sid}/turns").json(),
                "audience": c.get(f"/api/v1/sessions/{sid}/audience-model").json(),
                "history": c.get(f"/api/v1/sessions/{sid}/audience-model/history").json(),
                "evidence": c.get(f"/api/v1/sessions/{sid}/evidence").json(),
                "turn_trace": c.get(f"/api/v1/sessions/{sid}/turns/{tid}/trace").json(),
                "cues": c.get(f"/api/v1/sessions/{sid}/cues").json(),
            }
    for name, data in fixtures.items():
        (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
