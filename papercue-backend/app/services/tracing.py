"""Trace builder: times each pipeline stage and records structured, content-free output."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from app.core.errors import PaperCueError
from app.core.utils import new_id, utcnow_iso
from app.models.trace import PipelineTrace, TraceStage
from app.prompts.v1 import VERSION as PROMPT_VERSION


def prompt_version(task: str) -> str:
    return f"{task}-{PROMPT_VERSION}"


def llm_component(provider) -> str:
    return "mock_rules" if getattr(provider, "is_mock", False) else "local_llm"


class TraceBuilder:
    def __init__(self, session_id: str, turn_id: str | None, kind: str):
        self.trace_id = new_id()
        self.session_id = session_id
        self.turn_id = turn_id
        self.kind = kind
        self.decision_id: str | None = None
        self.started_at = utcnow_iso()
        self.stages: list[TraceStage] = []
        self.failed = False

    @contextmanager
    def stage(self, name: str, component: str, *, model: str | None = None, prompt: str | None = None,
              inputs: list[str] | None = None) -> Iterator[TraceStage]:
        st = TraceStage(stage_name=name, component_type=component, model_name=model, prompt_version=prompt,
                        input_reference_ids=list(inputs or []))
        t0 = time.perf_counter()
        try:
            yield st
        except PaperCueError as exc:
            st.status = "failed"
            st.validation_errors.append(exc.code)
            st.rationale_code = exc.code
            st.short_rationale = exc.message[:200]
            self.failed = True
            raise
        except Exception as exc:
            st.status = "failed"
            st.validation_errors.append(type(exc).__name__)
            st.rationale_code = "internal_error"
            self.failed = True
            raise
        finally:
            st.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self.stages.append(st)

    def skip(self, name: str, component: str, code: str, rationale: str) -> None:
        self.stages.append(TraceStage(stage_name=name, component_type=component, status="skipped",
                                      rationale_code=code, short_rationale=rationale))

    def latencies(self) -> dict[str, float]:
        out = {s.stage_name: s.duration_ms for s in self.stages if s.status != "skipped"}
        out["total"] = round(sum(out.values()), 2)
        return out

    def build(self) -> PipelineTrace:
        return PipelineTrace(trace_id=self.trace_id, session_id=self.session_id, turn_id=self.turn_id, kind=self.kind,
                             decision_id=self.decision_id, status="failed" if self.failed else "success",
                             started_at=self.started_at, completed_at=utcnow_iso(), stages=self.stages)
