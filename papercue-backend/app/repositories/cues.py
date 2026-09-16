from __future__ import annotations

from app.core.utils import new_id, utcnow_iso
from app.db.database import Database, dumps, loads
from app.models.trace import PipelineTrace


class CueRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_decision(self, *, decision_id: str, session_id: str, trigger_turn_id: str | None, mode: str,
                     should_intervene: bool, action: str | None, target: str | None, confidence: float,
                     short_reason: str, reason_code: str, final_status: str, scores: dict, trace: dict,
                     latency: dict) -> None:
        self.db.execute(
            "INSERT INTO cue_decisions(id, session_id, trigger_turn_id, mode, should_intervene, action, target, "
            "confidence, short_reason, reason_code, final_status, scores, trace, latency_ms, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, session_id, trigger_turn_id, mode, int(should_intervene), action, target, confidence,
             short_reason, reason_code, final_status, dumps(scores), dumps(trace), dumps(latency), utcnow_iso()),
        )

    def add_cue(self, *, session_id: str, decision_id: str, cue: str | None, candidate_cue: str | None,
                action: str | None, grounding_unit_ids: list[str], audience_evidence_ids: list[str],
                confidence: float, rationale: str | None, passed_filter: bool, filter_results: list[dict],
                fallback_used: bool, delivered: bool, final_status: str, generator: str) -> dict:
        cid, now = new_id(), utcnow_iso()
        self.db.execute(
            "INSERT INTO generated_cues(id, session_id, decision_id, cue, candidate_cue, action, grounding_unit_ids, "
            "audience_evidence_ids, confidence, rationale, passed_filter, filter_results, fallback_used, delivered, "
            "final_status, generator, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, session_id, decision_id, cue, candidate_cue, action, dumps(grounding_unit_ids),
             dumps(audience_evidence_ids), confidence, rationale, int(passed_filter), dumps(filter_results),
             int(fallback_used), int(delivered), final_status, generator, now),
        )
        return {"id": cid, "created_at": now}

    @staticmethod
    def _decode_cue(r: dict) -> dict:
        for k in ("grounding_unit_ids", "audience_evidence_ids", "filter_results"):
            r[k] = loads(r[k], [])
        for k in ("passed_filter", "fallback_used", "delivered"):
            r[k] = bool(r[k])
        return r

    @staticmethod
    def _decode_decision(r: dict) -> dict:
        r["should_intervene"] = bool(r["should_intervene"])
        for k in ("scores", "trace", "latency_ms"):
            r[k] = loads(r[k], {})
        return r

    def list_with_cues(self, session_id: str) -> list[dict]:
        decisions = self.db.fetchall(
            "SELECT * FROM cue_decisions WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
        )
        cues = self.db.fetchall("SELECT * FROM generated_cues WHERE session_id = ?", (session_id,))
        by_decision = {c["decision_id"]: self._decode_cue(c) for c in cues}
        out = []
        for d in decisions:
            d = self._decode_decision(d)
            d["generated"] = by_decision.get(d["id"])
            out.append(d)
        return out

    def recent_delivered_cues(self, session_id: str, limit: int) -> list[str]:
        rows = self.db.fetchall(
            "SELECT cue FROM generated_cues WHERE session_id = ? AND delivered = 1 AND cue IS NOT NULL "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, limit),
        )
        return [r["cue"] for r in rows]


class TraceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, trace: PipelineTrace) -> None:
        self.db.execute(
            "INSERT INTO pipeline_traces(id, session_id, turn_id, kind, decision_id, pipeline_version, status, stages, "
            "started_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (trace.trace_id, trace.session_id, trace.turn_id, trace.kind, trace.decision_id, trace.pipeline_version,
             trace.status, dumps([s.model_dump() for s in trace.stages]), trace.started_at, trace.completed_at),
        )

    @staticmethod
    def _decode(r: dict) -> PipelineTrace:
        return PipelineTrace(
            trace_id=r["id"], session_id=r["session_id"], turn_id=r["turn_id"], kind=r["kind"],
            decision_id=r["decision_id"], pipeline_version=r["pipeline_version"], status=r["status"],
            started_at=r["started_at"], completed_at=r["completed_at"], stages=loads(r["stages"], []),
        )

    def list(self, session_id: str) -> list[PipelineTrace]:
        rows = self.db.fetchall(
            "SELECT * FROM pipeline_traces WHERE session_id = ? ORDER BY started_at, rowid", (session_id,)
        )
        return [self._decode(r) for r in rows]

    def get(self, trace_id: str) -> PipelineTrace | None:
        row = self.db.fetchone("SELECT * FROM pipeline_traces WHERE id = ?", (trace_id,))
        return self._decode(row) if row else None

    # ------------------------------------------------------------ errors
    def add_error(self, session_id: str | None, stage: str, error_code: str, message: str) -> None:
        self.db.execute(
            "INSERT INTO processing_errors(id, session_id, stage, error_code, message, created_at) VALUES (?,?,?,?,?,?)",
            (new_id(), session_id, stage, error_code, message[:300], utcnow_iso()),
        )

    def recent_errors(self, limit: int = 10) -> list[dict]:
        return self.db.fetchall(
            "SELECT id, session_id, stage, error_code, message, created_at FROM processing_errors "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def add_debug_prompt(self, session_id: str | None, task: str, prompt: str, response: str | None) -> None:
        self.db.execute(
            "INSERT INTO llm_debug_prompts(id, session_id, task, prompt, response, created_at) VALUES (?,?,?,?,?,?)",
            (new_id(), session_id, task, prompt, response, utcnow_iso()),
        )
