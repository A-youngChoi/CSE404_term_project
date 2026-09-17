"""Run the synthetic presentation sessions from the command line (no server needed).

    python scripts/presentation_sim.py                      # all sessions, summary table
    python scripts/presentation_sim.py --session S02_ko_missing_point --trace
    python scripts/presentation_sim.py --judge ollama --seed 3
    python scripts/presentation_sim.py --out data/presentation_logs/batch   # JSONL rows + evaluation JSON

Mock mode (default) needs no model. `--judge ollama` uses the local Ollama server and falls back
to the rule-based Judge on any model failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.llm.factory import ProviderRegistry  # noqa: E402
from app.presentation.service import PresentationService  # noqa: E402
from app.services.embeddings import HashingEmbedder, build_embedder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="run one session and print its decisions")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--judge", choices=["mock", "ollama"])
    ap.add_argument("--prompts", choices=["template", "ollama"])
    ap.add_argument("--retriever", choices=["keyword", "embedding"])
    ap.add_argument("--trace", action="store_true", help="print the structured rationale of every step")
    ap.add_argument("--out", type=Path, help="directory for JSONL rows and evaluation.json")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(database_path=Path(tmp) / "sim.db", presentation_write_logs=False)
        embedder = HashingEmbedder() if settings.embedding_provider == "hashing" or args.retriever != "embedding" \
            else build_embedder(settings)
        svc = PresentationService(settings, ProviderRegistry(settings), embedder)
        overrides = {"seed": args.seed, "judge_provider": args.judge, "prompt_provider": args.prompts,
                     "retriever": args.retriever}
        session_ids = [args.session] if args.session else list(svc.dataset.sessions)
        evaluations = []
        for sid in session_ids:
            run = svc.create_run(sid, **overrides)
            svc.seek(run.run_id, run.total)
            ev = svc.evaluation_for(run)
            evaluations.append(ev)
            if args.session or args.trace:
                print(f"\n=== {sid} ({run.summary()['judge']}) ===")
                for st in run.steps:
                    d = st.decision
                    line = f"{st.step:>2} {st.event_id:<24} {d.decision:<36} {d.detected_issue:<20} u={d.utility:+.2f} c={d.confidence:.2f}"
                    if st.prompt:
                        line += f"  {'->' if st.prompt.delivered else 'x '} {st.prompt.text}"
                    print(line)
                    if args.trace and d.issue_id:
                        print(f"     reason: {d.reason_for_final_decision}")
                        for c in d.score_breakdown:
                            print(f"       {c.name:<26} {c.value:.2f} x {c.weight:+.2f} = {c.contribution:+.3f}  refs={c.evidence_refs}")
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                with (args.out / f"{sid}.jsonl").open("w", encoding="utf-8") as fh:
                    for row in svc.export_rows(run.run_id):
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        from app.presentation.evaluation import aggregate

        agg = aggregate(evaluations)
        print(f"\n{'session':<32} {'lang':<4} {'TP':>3} {'FP':>3} {'FN':>3} {'decision agree':>15}")
        for ev in evaluations:
            m, c = ev["metrics"], ev["counts"]
            agree = "-" if m["decision_agreement"] is None else f"{m['decision_agreement']:.2f}"
            print(f"{ev['session_id']:<32} {ev['language']:<4} {c['tp']:>3} {c['fp']:>3} {c['fn']:>3} {agree:>15}")
        print("\noverall:", json.dumps(agg["overall"]["metrics"], ensure_ascii=False))
        print("note: heuristic metrics on synthetic data; S01-S12 were written alongside the thresholds.")
        if args.out:
            (args.out / "evaluation.json").write_text(
                json.dumps({"sessions": evaluations, **agg}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"wrote {len(evaluations)} JSONL file(s) and evaluation.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
