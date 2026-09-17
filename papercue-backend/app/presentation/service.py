"""Presentation simulation service: run registry, batch evaluation, mobile state, exports.

Runs live in memory (bounded LRU). Because runs are deterministic, a lost run can be
recreated from its session ID and configuration. Completed runs can be written to a local
JSONL log for future timing/state classifiers.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.core.logging import get_logger
from app.llm.factory import ProviderRegistry
from app.presentation import judge as judge_mod
from app.presentation import outcome as outcome_rules
from app.presentation.context import DETECTOR_PARAMS
from app.presentation.dataset import PresentationDataset
from app.presentation.engine import RunConfig, SimulationRun
from app.presentation.evaluation import aggregate, evaluate_steps
from app.presentation.judge import OllamaJudge, RuleBasedJudge
from app.presentation.knowledge import EmbeddingRetriever, KeywordRetriever
from app.presentation import memory as memory_mod
from app.services.system_status import display_path

log = get_logger("presentation.service")


class PresentationService:
    def __init__(self, settings: Settings, providers: ProviderRegistry, embedder):
        self.settings = settings
        self.providers = providers
        self.embedder = embedder
        self.dataset = PresentationDataset(settings.presentation_data_dir)
        self.runs: OrderedDict[str, SimulationRun] = OrderedDict()
        self.active_run_id: str | None = None
        self._counter = 0
        self._lock = threading.RLock()
        self._eval_cache: dict[tuple, dict[str, Any]] = {}
        self._keyword = KeywordRetriever()
        self._embedding: EmbeddingRetriever | None = None

    # ------------------------------------------------------------------ configuration
    def default_config(self, **overrides: Any) -> RunConfig:
        s = self.settings
        values = {"seed": s.presentation_seed, "judge_provider": s.presentation_judge_provider,
                  "prompt_provider": s.presentation_prompt_provider, "retriever": s.presentation_retriever,
                  "judge_noise": s.presentation_judge_noise}
        values.update({k: v for k, v in overrides.items() if v is not None})
        if values["judge_provider"] not in ("mock", "ollama"):
            raise InvalidInputError("judge_provider must be 'mock' or 'ollama'.")
        if values["prompt_provider"] not in ("template", "ollama"):
            raise InvalidInputError("prompt_provider must be 'template' or 'ollama'.")
        if values["retriever"] not in ("keyword", "embedding"):
            raise InvalidInputError("retriever must be 'keyword' or 'embedding'.")
        return RunConfig(**values)

    def config_status(self) -> dict[str, Any]:
        cfg = self.default_config()
        return {
            "defaults": cfg.as_dict(),
            "judge_is_mock": cfg.judge_provider == "mock",
            "ollama_model": self.settings.ollama_model,
            "data_dir": display_path(Path(self.settings.presentation_data_dir)),
            "synthetic_data": True,
            "judge_weights": judge_mod.WEIGHTS,
            "judge_thresholds": {"intervene": judge_mod.T_INTERVENE, "wait": judge_mod.T_WAIT,
                                 "min_confidence": judge_mod.MIN_CONFIDENCE,
                                 "cooldown_short_s": judge_mod.COOLDOWN_SHORT,
                                 "cooldown_medium_s": judge_mod.COOLDOWN_MEDIUM,
                                 "redundancy_window_s": judge_mod.REDUNDANCY_WINDOW,
                                 "override_severity": judge_mod.OVERRIDE_SEVERITY},
            "detector_params": DETECTOR_PARAMS,
            "memory": {"weights": memory_mod.RETRIEVAL_WEIGHTS, "recency_decay": memory_mod.RECENCY_DECAY,
                       "top_k": memory_mod.RETRIEVAL_TOP_K, "min_score": memory_mod.RETRIEVAL_MIN_SCORE,
                       "reflection_threshold": memory_mod.REFLECTION_THRESHOLD},
            "outcome_rules": outcome_rules.RULES_DOC,
            "outcome_window": {"seconds": outcome_rules.WINDOW_SECONDS, "events": outcome_rules.WINDOW_EVENTS},
            "log_dir_enabled": self.settings.presentation_write_logs,
        }

    def _judge(self, cfg: RunConfig):
        rules = RuleBasedJudge(seed=cfg.seed, noise=cfg.judge_noise)
        if cfg.judge_provider == "ollama":
            return OllamaJudge(self.providers.get("ollama"), rules)
        return rules

    def _retriever(self, cfg: RunConfig):
        if cfg.retriever == "embedding":
            if self._embedding is None:
                self._embedding = EmbeddingRetriever(self.embedder)
            return self._embedding
        return self._keyword

    def _build(self, session_id: str, cfg: RunConfig, run_id: str) -> SimulationRun:
        session = self.dataset.session(session_id)
        prompt_provider = self.providers.get("ollama") if cfg.prompt_provider == "ollama" else None
        return SimulationRun(run_id, session, self.dataset.kb(session.meta.kb_id),
                             self.dataset.presenter(session.meta.presenter_id), cfg, self._judge(cfg),
                             self._retriever(cfg), prompt_provider)

    # ------------------------------------------------------------------ dataset views
    def list_sessions(self) -> list[dict[str, Any]]:
        out = []
        for s in self.dataset.sessions.values():
            gt = s.ground_truth.values()
            out.append(s.meta.model_dump() | {
                "gt_positive": sum(1 for g in gt if g.expected_intervention),
                "gt_issues": sum(1 for g in gt if g.issue != "none"),
                "presenter": self.dataset.presenter(s.meta.presenter_id).model_dump(
                    include={"presenter_id", "display_name", "expertise_level"}),
            })
        return out

    def session_detail(self, session_id: str) -> dict[str, Any]:
        s = self.dataset.session(session_id)
        kb = self.dataset.kb(s.meta.kb_id)
        return {
            "meta": s.meta.model_dump(),
            "presenter": self.dataset.presenter(s.meta.presenter_id).model_dump(),
            "knowledge_base": {"kb_id": kb.kb_id, "paper_title": kb.paper_title, "language": kb.language,
                               "slides": [sl.model_dump() for sl in kb.slides]},
            "events": [e.model_dump() for e in s.events],
            "ground_truth": {k: v.model_dump() for k, v in s.ground_truth.items()},
            "ground_truth_notice": "Researcher-only labels; never passed to the Judge.",
        }

    def knowledge(self, kb_id: str | None = None) -> list[dict[str, Any]]:
        kbs = [self.dataset.kb(kb_id)] if kb_id else list(self.dataset.knowledge_bases.values())
        return [{"kb_id": kb.kb_id, "paper_title": kb.paper_title, "language": kb.language,
                 "documents": kb.documents, "slides": [s.model_dump() for s in kb.slides],
                 "chunks": [c.model_dump() for c in kb.chunks]} for kb in kbs]

    # ------------------------------------------------------------------ runs
    def create_run(self, session_id: str, **overrides: Any) -> SimulationRun:
        cfg = self.default_config(**overrides)
        with self._lock:
            self._counter += 1
            run_id = f"run_{self._counter:03d}_{session_id}_s{cfg.seed}"
            run = self._build(session_id, cfg, run_id)
            self.runs[run_id] = run
            while len(self.runs) > self.settings.presentation_max_runs:
                self.runs.popitem(last=False)
            self.active_run_id = run_id
        return run

    def get_run(self, run_id: str) -> SimulationRun:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found (runs are kept in memory; create a new one).")
        self.runs.move_to_end(run_id)
        return run

    def step(self, run_id: str, count: int = 1) -> SimulationRun:
        with self._lock:
            run = self.get_run(run_id)
            for _ in range(max(1, count)):
                if run.step() is None:
                    break
            self._touch(run)
            self._maybe_log(run)
        return run

    def seek(self, run_id: str, index: int) -> SimulationRun:
        with self._lock:
            run = self.get_run(run_id)
            run.run_to(index)
            self._touch(run)
            self._maybe_log(run)
        return run

    def reset(self, run_id: str) -> SimulationRun:
        with self._lock:
            run = self.get_run(run_id)
            run.reset()
            self._touch(run)
        return run

    def set_playback(self, run_id: str, playing: bool, speed: float) -> SimulationRun:
        run = self.get_run(run_id)
        run.playback.update(playing=bool(playing) and not run.finished, speed=float(speed), wall_clock=time.time())
        self.active_run_id = run_id
        return run

    def _touch(self, run: SimulationRun) -> None:
        self.active_run_id = run.run_id
        run.playback["wall_clock"] = time.time()
        if run.finished:
            run.playback["playing"] = False

    def evaluation_for(self, run: SimulationRun) -> dict[str, Any]:
        meta = run.session.meta
        return evaluate_steps(run.steps, run.session.ground_truth, run.prompts, {
            "session_id": meta.session_id, "language": meta.language, "scenario_type": meta.scenario_type,
            "title": meta.title, "title_ko": meta.title_ko, "complete": run.finished})

    def run_view(self, run_id: str, since: int = 0) -> dict[str, Any]:
        run = self.get_run(run_id)
        s = run.session
        return {
            "summary": run.summary(),
            "session": s.meta.model_dump(),
            "presenter": run.presenter.model_dump(),
            "knowledge_base": {"kb_id": run.kb.kb_id, "paper_title": run.kb.paper_title,
                               "slides": [sl.model_dump() for sl in run.kb.slides]},
            "initial": run.initial,
            "steps": [st.model_dump() for st in run.steps[since:]],
            "since": since,
            "prompts": [p.model_dump() for p in run.prompts],
            "issues": run.issues(),
            "memory_log": [c.model_dump() for c in run.memory.log],
            "user_model_history": [c.model_dump() for c in run.user_model.history],
            "mobile": self.mobile_state(run_id),
            "event_index": [{"event_id": e.event_id, "elapsed": e.elapsed_time, "slide": e.current_slide,
                             "type": e.event_type} for e in s.events],
            # Researcher-only labels, kept in their own field and never given to the Judge.
            "ground_truth": {k: v.model_dump() for k, v in s.ground_truth.items()},
            "evaluation": self.evaluation_for(run) if run.cursor else None,
        }

    # ------------------------------------------------------------------ mobile
    def mobile_state(self, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or self.active_run_id
        if not run_id or run_id not in self.runs:
            return {"run_id": None, "text": None, "idle": True, "is_mock": True}
        run = self.runs[run_id]
        state = run.mobile.model_dump()
        pb = run.playback
        now_elapsed = run.mobile.elapsed_time
        if pb.get("playing"):
            now_elapsed += max(0.0, time.time() - pb.get("wall_clock", time.time())) * pb.get("speed", 1.0)
        if state.get("expires_at_elapsed") is not None:
            remaining = state["expires_at_elapsed"] - now_elapsed
            if remaining <= 0:
                state.update(text=None, prompt_id=None, prompt_type=None, priority=None, urgency=None,
                             remaining_seconds=0.0)
            else:
                state["remaining_seconds"] = round(remaining, 1)
        state.update(playing=bool(pb.get("playing")), speed=pb.get("speed", 1.0), wall_clock_updated=pb.get("wall_clock", 0.0),
                     idle=False, session_title=run.session.meta.title_ko, step=run.cursor, total=run.total)
        return state

    # ------------------------------------------------------------------ batch evaluation
    def evaluate_all(self, **overrides: Any) -> dict[str, Any]:
        cfg = self.default_config(**overrides)
        key = tuple(sorted(cfg.as_dict().items()))
        with self._lock:
            if key in self._eval_cache:
                return self._eval_cache[key]
            sessions = []
            for sid in self.dataset.sessions:
                run = self._build(sid, cfg, f"eval_{sid}_s{cfg.seed}")
                run.run_to(run.total)
                ev = self.evaluation_for(run)
                ev["summary"] = run.summary()
                ev["duration_s"] = run.session.meta.duration_s
                ev["presenter_level"] = run.presenter.expertise_level
                sessions.append(ev)
            result = {"config": cfg.as_dict(), "sessions": sessions, **aggregate(sessions),
                      "judge": sessions[0]["summary"]["judge"] if sessions else None,
                      "notice": "Heuristic metrics on synthetic data. Thresholds were tuned while building this "
                                "dataset, so these numbers are optimistic and are not a validation result."}
            self._eval_cache[key] = result
            return result

    # ------------------------------------------------------------------ exports
    def export_rows(self, run_id: str) -> list[dict[str, Any]]:
        """One flat row per event: observable features, system decision, outcome, and labels."""
        run = self.get_run(run_id)
        rows = []
        prompts = {p.prompt_id: p for p in run.prompts}
        for st in run.steps:
            ctx, d, ev = st.context, st.decision, st.event
            gt = run.session.ground_truth.get(st.event_id)
            p = prompts.get(st.prompt.prompt_id) if st.prompt else None
            um = st.user_model_snapshot
            rows.append({
                "run_id": run.run_id, "session_id": run.session.meta.session_id, "event_id": st.event_id,
                "language": ev.language, "elapsed": ev.elapsed_time, "slide": ev.current_slide,
                "event_type": ev.event_type,
                "features": {
                    "speech_rate_ratio": ctx.speech_rate_ratio, "silence_s": ev.silence_duration,
                    "filler_count": ev.filler_count, "filler_window": ctx.filler_window,
                    "confusion": ev.audience_signal.confusion, "attention": ev.audience_signal.attention,
                    "arousal": ev.presenter_state.arousal, "time_on_slide": ctx.time_on_slide,
                    "schedule_lag_s": ctx.schedule_lag_s, "time_budget_ratio": ctx.time_budget_ratio,
                    "remaining_s": ev.remaining_time, "active_issue_types": sorted({i.type for i in ctx.active_issues}),
                    "cognitive_load": um.get("estimated_cognitive_load", {}).get("value"),
                    "tension": um.get("estimated_tension", {}).get("value"),
                    "responsiveness": um.get("prompt_responsiveness", {}).get("value"),
                    "seconds_since_prompt": (ev.elapsed_time - max(prior)) if (prior := [
                        x.created_elapsed for x in run.delivered if x.created_elapsed < ev.elapsed_time]) else None,
                },
                "system": {"decision": d.decision, "issue": d.detected_issue, "severity": d.severity,
                           "urgency": d.urgency, "confidence": d.confidence, "utility": d.utility,
                           "prompt_type": p.prompt_type if p else None, "delivered": bool(p and p.delivered),
                           "outcome": p.outcome if p else None, "judge": d.judge, "fallback": d.fallback_used},
                "labels": gt.model_dump() if gt else None,
            })
        return rows

    def _maybe_log(self, run: SimulationRun) -> None:
        if not (run.finished and self.settings.presentation_write_logs) or run.logged:
            return
        try:
            out_dir = Path(self.settings.presentation_log_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{run.run_id}.jsonl"
            with path.open("w", encoding="utf-8") as fh:
                for row in self.export_rows(run.run_id):
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            run.logged = True
            log.info("wrote simulation log for %s (%d rows)", run.run_id, run.total)
        except OSError as exc:
            log.warning("could not write simulation log: %s", type(exc).__name__)
