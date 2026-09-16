from __future__ import annotations

from datetime import datetime, timedelta

from app.core.utils import new_id, utcnow, utcnow_iso
from app.db.database import Database, dumps, loads

# Tables whose rows belong to a session (all cascade on session deletion).
SESSION_TABLES = [
    "listener_profiles",
    "conversation_turns",
    "conversation_summaries",
    "conversation_states",
    "evidence_items",
    "audience_beliefs",
    "audience_belief_history",
    "cue_decisions",
    "generated_cues",
    "pipeline_traces",
    "processing_errors",
    "llm_debug_prompts",
]
# Derived tables cleared by a reset (the session and its profile survive).
RESET_TABLES = [t for t in SESSION_TABLES if t != "listener_profiles"]


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, *, paper_id: str, mode: str, llm_provider: str, cue_language: str, consent_note: str,
               label: str | None, retention_days: int) -> str:
        sid, now = new_id(), utcnow()
        self.db.execute(
            "INSERT INTO sessions(id, paper_id, mode, llm_provider, cue_language, consent_confirmed, consent_note, "
            "label, created_at, updated_at, expires_at) VALUES (?,?,?,?,?,1,?,?,?,?,?)",
            (sid, paper_id, mode, llm_provider, cue_language, consent_note, label, now.isoformat(timespec="milliseconds"),
             now.isoformat(timespec="milliseconds"),
             (now + timedelta(days=retention_days)).isoformat(timespec="milliseconds")),
        )
        return sid

    def get(self, session_id: str) -> dict | None:
        return self.db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def list(self) -> list[dict]:
        return self.db.fetchall("SELECT * FROM sessions ORDER BY created_at DESC")

    def touch(self, session_id: str, turn_count: int | None = None) -> None:
        if turn_count is None:
            self.db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utcnow_iso(), session_id))
        else:
            self.db.execute("UPDATE sessions SET updated_at = ?, turn_count = ? WHERE id = ?",
                            (utcnow_iso(), turn_count, session_id))

    def record_counts(self, session_id: str) -> dict[str, int]:
        return {
            t: self.db.fetchone(f"SELECT COUNT(*) AS n FROM {t} WHERE session_id = ?", (session_id,))["n"]
            for t in SESSION_TABLES
        }

    def delete(self, session_id: str) -> dict[str, int]:
        counts = self.record_counts(session_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            # Belt and braces: cascades should already have removed these.
            for table in SESSION_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        return counts

    def reset(self, session_id: str) -> dict[str, int]:
        counts = {t: c for t, c in self.record_counts(session_id).items() if t in RESET_TABLES}
        with self.db.transaction() as conn:
            for table in ["generated_cues", "cue_decisions", "pipeline_traces", *RESET_TABLES]:
                conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE sessions SET turn_count = 0, updated_at = ? WHERE id = ?", (utcnow_iso(), session_id))
        return counts

    def expired_ids(self, now: datetime | None = None) -> list[str]:
        ts = (now or utcnow()).isoformat(timespec="milliseconds")
        return [r["id"] for r in self.db.fetchall("SELECT id FROM sessions WHERE expires_at <= ?", (ts,))]

    # ---------------------------------------------------------- profile
    def save_profile(self, session_id: str, fields: dict) -> str:
        pid = new_id()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM listener_profiles WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO listener_profiles(id, session_id, fields, created_at) VALUES (?,?,?,?)",
                (pid, session_id, dumps(fields), utcnow_iso()),
            )
        return pid

    def get_profile(self, session_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM listener_profiles WHERE session_id = ?", (session_id,))
        if row:
            row["fields"] = loads(row["fields"], {})
        return row


class AuditRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, event_type: str, subject_id: str, details: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO audit_events(id, event_type, subject_id, details, created_at) VALUES (?,?,?,?,?)",
            (new_id(), event_type, subject_id, dumps(details or {}), utcnow_iso()),
        )

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["details"] = loads(r["details"], {})
        return rows
