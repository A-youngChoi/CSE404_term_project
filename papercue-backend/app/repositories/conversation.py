from __future__ import annotations

import numpy as np

from app.core.utils import new_id, utcnow_iso
from app.db.database import Database, dumps, loads
from app.models.conversation import ConversationState, Turn
from app.services.embeddings import from_blob, to_blob


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ turns
    def add_turn(self, session_id: str, speaker: str, text: str, spoken_at: str, input_source: str = "text") -> Turn:
        tid, now = new_id(), utcnow_iso()
        with self.db.transaction() as conn:
            idx = conn.execute(
                "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM conversation_turns WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO conversation_turns(id, session_id, turn_index, speaker, text, spoken_at, input_source, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, session_id, idx, speaker, text, spoken_at, input_source, now),
            )
        return self.get_turn(tid)

    def get_turn(self, turn_id: str) -> Turn | None:
        row = self.db.fetchone("SELECT * FROM conversation_turns WHERE id = ?", (turn_id,))
        return Turn(**{k: row[k] for k in Turn.model_fields}) if row else None

    def list_turns(self, session_id: str) -> list[Turn]:
        rows = self.db.fetchall(
            "SELECT * FROM conversation_turns WHERE session_id = ? ORDER BY turn_index", (session_id,)
        )
        return [Turn(**{k: r[k] for k in Turn.model_fields}) for r in rows]

    def recent_turns(self, session_id: str, limit: int) -> list[Turn]:
        rows = self.db.fetchall(
            "SELECT * FROM conversation_turns WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?", (session_id, limit)
        )
        return [Turn(**{k: r[k] for k in Turn.model_fields}) for r in reversed(rows)]

    def set_status(self, turn_id: str, status: str) -> None:
        self.db.execute("UPDATE conversation_turns SET analysis_status = ? WHERE id = ?", (status, turn_id))

    def unsummarized_outside_window(self, session_id: str, window: int) -> list[Turn]:
        rows = self.db.fetchall(
            "SELECT * FROM conversation_turns WHERE session_id = ? AND summarized = 0 AND turn_index <= "
            "(SELECT MAX(turn_index) FROM conversation_turns WHERE session_id = ?) - ? ORDER BY turn_index",
            (session_id, session_id, window),
        )
        return [Turn(**{k: r[k] for k in Turn.model_fields}) for r in rows]

    # -------------------------------------------------------- summaries
    def add_summary(self, *, session_id: str, turn: Turn, topic: str | None, dialogue_act: str | None,
                    concerns: list[str], gist: str, model_name: str | None, vector: np.ndarray | None) -> str:
        sid = new_id()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO conversation_summaries(id, session_id, turn_id, turn_index, speaker, topic, dialogue_act, "
                "concerns, gist, model_name, vector, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, session_id, turn.id, turn.turn_index, turn.speaker.value, topic, dialogue_act, dumps(concerns),
                 gist, model_name, to_blob(vector) if vector is not None else None, utcnow_iso()),
            )
            conn.execute("UPDATE conversation_turns SET summarized = 1 WHERE id = ?", (turn.id,))
        return sid

    def list_summaries(self, session_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM conversation_summaries WHERE session_id = ? ORDER BY turn_index", (session_id,)
        )
        for r in rows:
            r["concerns"] = loads(r["concerns"], [])
            blob = r.pop("vector")
            r["vector"] = from_blob(blob) if blob else None
        return rows

    # ----------------------------------------------------------- states
    def save_state(self, session_id: str, turn_id: str | None, state: ConversationState) -> str:
        sid = new_id()
        self.db.execute(
            "INSERT INTO conversation_states(id, session_id, turn_id, state, created_at) VALUES (?,?,?,?,?)",
            (sid, session_id, turn_id, state.model_dump_json(), utcnow_iso()),
        )
        return sid

    def latest_state(self, session_id: str) -> ConversationState:
        row = self.db.fetchone(
            "SELECT state FROM conversation_states WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        )
        return ConversationState.model_validate_json(row["state"]) if row else ConversationState()

    def state_for_turn(self, turn_id: str) -> ConversationState | None:
        row = self.db.fetchone(
            "SELECT state FROM conversation_states WHERE turn_id = ? ORDER BY rowid DESC LIMIT 1", (turn_id,)
        )
        return ConversationState.model_validate_json(row["state"]) if row else None

    def state_before_turn(self, session_id: str, turn_index: int) -> ConversationState:
        row = self.db.fetchone(
            "SELECT s.state FROM conversation_states s JOIN conversation_turns t ON t.id = s.turn_id "
            "WHERE s.session_id = ? AND t.turn_index < ? ORDER BY t.turn_index DESC, s.rowid DESC LIMIT 1",
            (session_id, turn_index),
        )
        return ConversationState.model_validate_json(row["state"]) if row else ConversationState()

    def list_states(self, session_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT id, turn_id, state, created_at FROM conversation_states WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
        for r in rows:
            r["state"] = loads(r["state"], {})
        return rows
