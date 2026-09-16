from __future__ import annotations

from app.core.utils import new_id, utcnow_iso
from app.db.database import Database, dumps, loads
from app.models.audience import BeliefChange, EvidenceItem


class AudienceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --------------------------------------------------------- evidence
    def add_evidence(self, *, session_id: str, turn_id: str | None, source_type: str, source_id: str, dimension: str,
                     key: str, value: str, evidence_type: str, confidence: float, quote: str | None,
                     observation: str, extractor: str) -> EvidenceItem:
        eid, now = new_id(), utcnow_iso()
        self.db.execute(
            "INSERT INTO evidence_items(id, session_id, turn_id, source_type, source_id, dimension, key, value, "
            "evidence_type, confidence, quote, observation, extractor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, session_id, turn_id, source_type, source_id, dimension, key, value, evidence_type, confidence, quote,
             observation, extractor, now),
        )
        return EvidenceItem(id=eid, session_id=session_id, turn_id=turn_id, source_type=source_type, source_id=source_id,
                            dimension=dimension, key=key, value=value, evidence_type=evidence_type,
                            confidence=confidence, quote=quote, observation=observation, extractor=extractor,
                            created_at=now)

    def list_evidence(self, session_id: str, turn_id: str | None = None) -> list[EvidenceItem]:
        if turn_id:
            rows = self.db.fetchall("SELECT * FROM evidence_items WHERE session_id = ? AND turn_id = ? ORDER BY rowid",
                                    (session_id, turn_id))
        else:
            rows = self.db.fetchall("SELECT * FROM evidence_items WHERE session_id = ? ORDER BY rowid", (session_id,))
        return [EvidenceItem(**r) for r in rows]

    def evidence_ids(self, session_id: str) -> set[str]:
        return {r["id"] for r in self.db.fetchall("SELECT id FROM evidence_items WHERE session_id = ?", (session_id,))}

    def delete_profile_evidence(self, session_id: str) -> None:
        self.db.execute("DELETE FROM evidence_items WHERE session_id = ? AND source_type = 'profile'", (session_id,))

    # ---------------------------------------------------------- beliefs
    def get_belief(self, session_id: str, dimension: str, key: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT * FROM audience_beliefs WHERE session_id = ? AND dimension = ? AND key = ?",
            (session_id, dimension, key),
        )
        if row:
            row["evidence_ids"] = loads(row["evidence_ids"], [])
        return row

    def list_beliefs(self, session_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM audience_beliefs WHERE session_id = ? ORDER BY dimension, confidence DESC", (session_id,)
        )
        for r in rows:
            r["evidence_ids"] = loads(r["evidence_ids"], [])
        return rows

    def insert_belief(self, row: dict) -> str:
        bid, now = new_id(), utcnow_iso()
        self.db.execute(
            "INSERT INTO audience_beliefs(id, session_id, dimension, key, value, confidence, status, source_type, "
            "source_id, evidence_ids, support_count, contradict_count, pending_value, last_evidence_type, reason, "
            "turn_index, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, row["session_id"], row["dimension"], row["key"], row["value"], row["confidence"], row["status"],
             row["source_type"], row["source_id"], dumps(row["evidence_ids"]), row["support_count"],
             row["contradict_count"], row.get("pending_value"), row["last_evidence_type"], row["reason"],
             row["turn_index"], now, now),
        )
        return bid

    def update_belief(self, belief_id: str, row: dict) -> None:
        self.db.execute(
            "UPDATE audience_beliefs SET value = ?, confidence = ?, status = ?, source_type = ?, source_id = ?, "
            "evidence_ids = ?, support_count = ?, contradict_count = ?, pending_value = ?, last_evidence_type = ?, "
            "reason = ?, turn_index = ?, updated_at = ? WHERE id = ?",
            (row["value"], row["confidence"], row["status"], row["source_type"], row["source_id"],
             dumps(row["evidence_ids"]), row["support_count"], row["contradict_count"], row.get("pending_value"),
             row["last_evidence_type"], row["reason"], row["turn_index"], utcnow_iso(), belief_id),
        )

    def delete_profile_only_beliefs(self, session_id: str) -> None:
        self.db.execute("DELETE FROM audience_beliefs WHERE session_id = ? AND source_type = 'profile'", (session_id,))

    # ---------------------------------------------------------- history
    def add_history(self, *, session_id: str, belief_id: str, dimension: str, key: str, change_type: str,
                    old_value: str | None, new_value: str, old_confidence: float | None, new_confidence: float,
                    evidence_id: str | None, source_type: str, source_id: str, reason: str) -> BeliefChange:
        hid, now = new_id(), utcnow_iso()
        self.db.execute(
            "INSERT INTO audience_belief_history(id, session_id, belief_id, change_type, old_value, new_value, "
            "old_confidence, new_confidence, evidence_id, source_type, source_id, reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (hid, session_id, belief_id, change_type, old_value, new_value, old_confidence, new_confidence,
             evidence_id, source_type, source_id, reason, now),
        )
        return BeliefChange(id=hid, belief_id=belief_id, dimension=dimension, key=key, change_type=change_type,
                            old_value=old_value, new_value=new_value, old_confidence=old_confidence,
                            new_confidence=new_confidence, evidence_id=evidence_id, source_type=source_type,
                            source_id=source_id, reason=reason, created_at=now)

    def list_history(self, session_id: str) -> list[dict]:
        return self.db.fetchall(
            "SELECT h.*, b.dimension, b.key, t.turn_index FROM audience_belief_history h "
            "JOIN audience_beliefs b ON b.id = h.belief_id "
            "LEFT JOIN evidence_items e ON e.id = h.evidence_id "
            "LEFT JOIN conversation_turns t ON t.id = e.turn_id "
            "WHERE h.session_id = ? ORDER BY h.rowid",
            (session_id,),
        )
