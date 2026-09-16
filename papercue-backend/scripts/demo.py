"""PaperCue command-line demonstration.

Default: deterministic MOCK mode (no model download, no network), temporary database.

    python scripts/demo.py                 # English walkthrough, mock mode
    python scripts/demo.py --lang ko       # Korean walkthrough, mock mode
    python scripts/demo.py --ollama        # real local Ollama model + local sentence-transformer
    python scripts/demo.py --keep          # keep the demo database in ./data/demo.db
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402  (in-process client; no network socket)

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

LINE = "─" * 72


def load(name: str):
    return json.loads((ROOT / "sample_data" / name).read_text(encoding="utf-8"))


def show_model(client: TestClient, sid: str) -> None:
    view = client.get(f"/api/v1/sessions/{sid}/audience-model").json()
    print("  audience model (dimension | key = value | confidence | source):")
    for dim, items in view["dimensions"].items():
        for b in items:
            print(f"    {dim:<17} | {b['key']:<24} = {b['value']:<12} | {b['effective_confidence']:.2f} | "
                  f"{b['lifecycle']}")
    print(f"  overall uncertainty: {view['overall_uncertainty']:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ollama", action="store_true", help="use the local Ollama model and local embeddings")
    ap.add_argument("--lang", choices=["en", "ko"], default="en")
    ap.add_argument("--keep", action="store_true", help="keep the database at ./data/demo.db")
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory(prefix="papercue-demo-")
    db_path = ROOT / "data" / "demo.db" if args.keep else Path(tmp.name) / "demo.db"
    provider = "ollama" if args.ollama else "mock"
    settings = Settings(
        _env_file=None if not args.ollama else ".env",
        database_path=db_path,
        llm_provider=provider,
        embedding_provider="sentence_transformers" if args.ollama else "hashing",
        papers_dir=Path(tmp.name) / "papers",
        dashboard_dist_dir=Path(tmp.name) / "no-dist",
        log_level="WARNING",
    )
    if args.lang == "ko":
        w = load("ko_walkthrough.json")
        paper, profile, turns = w["paper"], w["profile"], w["turns"]
    else:
        paper, profile, turns = load("papercue_paper.json"), load("listener_profile.json"), load("demo_conversation.json")

    print(LINE)
    if provider == "mock":
        print("MOCK MODE: outputs below come from deterministic rules, NOT from a language model.")
    else:
        print(f"OLLAMA MODE: local model {settings.ollama_model}")
    print(LINE)

    with TestClient(create_app(settings)) as client:
        r = client.post("/api/v1/papers", json=paper)
        if r.status_code != 201:
            print("paper ingestion failed:", r.json()["error"]["message"])
            return 1
        report = r.json()
        pid = report["paper"]["id"]
        print(f"1) paper created: {report['paper']['title']}")
        print(f"   {report['units_created']} units, {report['units_indexed']} indexed with {report['embedding_model']}")

        sid = client.post("/api/v1/sessions", json={"paper_id": pid, "consent_confirmed": True,
                                                    "llm_provider": provider, "cue_language": args.lang}).json()["id"]
        print(f"2) consented session created: {sid}")

        prof = client.post(f"/api/v1/sessions/{sid}/profile", json=profile).json()
        print(f"3) profile added: {prof['hypotheses_created']} uncertain hypotheses (cap {prof['confidence_cap']})")
        show_model(client, sid)

        print("4) conversation")
        for t in turns:
            r = client.post(f"/api/v1/sessions/{sid}/turns", json=t)
            if r.status_code != 201:
                print("   turn failed:", r.json()["error"]["message"])
                return 1
            res = r.json()
            st = res["state"]
            print(LINE)
            print(f"   [{t['speaker']}] {t['text']}")
            print(f"   phase={st['phase']} topic={st['topic']} act="
                  f"{st['listener_act'] if t['speaker'] == 'listener' else st['presenter_act']}")
            if t["speaker"] == "listener":
                for e in res["evidence_accepted"]:
                    print(f"   evidence: {e['dimension']}/{e['key']}={e['value']} ({e['evidence_type']}) - {e['observation']}")
                for c in res["belief_changes"]:
                    old = "-" if c["old_confidence"] is None else f"{c['old_confidence']:.2f}"
                    print(f"   belief {c['change_type']}: {c['dimension']}/{c['key']} {old} -> {c['new_confidence']:.2f}")
                show_model(client, sid)

        print(LINE)
        print("5) on-demand cue request")
        body = client.post(f"/api/v1/sessions/{sid}/cue").json()
        if "error" in body:
            print("   cue request failed:", body["error"]["message"])
            return 1
        print("   retrieved paper units:")
        for u in body["trace"]["retrieved_units"]:
            print(f"     {u['score']:.3f}  [{u['unit_type']}] {u['title']}")
        print("   evidence from the triggering turn:")
        for e in body["trace"]["audience_evidence"]:
            print(f"     {e['id'][:8]} {e['dimension']}/{e['key']}={e['value']} ({e['evidence_type']})")
        d = body["decision"]
        print(f"   decision: intervene={d['should_intervene']} action={d['action']} target={d['target']} "
              f"confidence={d['confidence']:.2f}")
        print(f"   reason: {d['short_reason']}")
        print("   scores: " + ", ".join(f"{k}={v:.2f}" for k, v in d["scores"].items()))
        if body["generated"]:
            failed = [c["name"] for c in body["generated"]["filter_results"] if not c["passed"]]
            print(f"   filter: {'all checks passed' if not failed else 'failed: ' + ', '.join(failed)}")
        print(f"   status: {body['status']}")
        print(f"   FINAL CUE: {body['cue']!r}")
        print("   latency (ms): " + ", ".join(f"{k}={v}" for k, v in body["trace"]["latency_ms"].items()))
        if body.get("mock_notice"):
            print(f"   note: {body['mock_notice']}")
    print(LINE)
    if not args.keep:
        tmp.cleanup()
        print("demo database removed (temporary).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
