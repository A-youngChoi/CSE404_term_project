from __future__ import annotations

import numpy as np

from app.core.utils import new_id, utcnow_iso
from app.db.database import Database, dumps, loads
from app.models.paper import Paper, PaperUnit, PaperUnitIn
from app.services.embeddings import from_blob, to_blob


class PaperRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ papers
    def create(self, *, title: str, abstract: str, full_text: str | None, key_contributions: list[str],
               evidence_results: list[str], limitations: list[str], preferred_terminology: dict[str, str],
               forbidden_claims: list[str]) -> str:
        pid, now = new_id(), utcnow_iso()
        self.db.execute(
            "INSERT INTO papers(id, title, abstract, full_text, key_contributions, evidence_results, limitations, "
            "preferred_terminology, forbidden_claims, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, title, abstract, full_text, dumps(key_contributions), dumps(evidence_results), dumps(limitations),
             dumps(preferred_terminology), dumps(forbidden_claims), now, now),
        )
        return pid

    def get_row(self, paper_id: str) -> dict | None:
        return self.db.fetchone("SELECT * FROM papers WHERE id = ?", (paper_id,))

    def to_model(self, row: dict) -> Paper:
        counts = self.db.fetchone(
            "SELECT (SELECT COUNT(*) FROM paper_units WHERE paper_id = :p) AS units, "
            "(SELECT COUNT(*) FROM paper_embeddings WHERE paper_id = :p) AS indexed",
            {"p": row["id"]},
        )
        return Paper(
            id=row["id"], title=row["title"], abstract=row["abstract"],
            key_contributions=loads(row["key_contributions"], []), evidence_results=loads(row["evidence_results"], []),
            limitations=loads(row["limitations"], []), preferred_terminology=loads(row["preferred_terminology"], {}),
            forbidden_claims=loads(row["forbidden_claims"], []), has_full_text=bool(row["full_text"]),
            unit_count=counts["units"], indexed_unit_count=counts["indexed"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list(self) -> list[Paper]:
        return [self.to_model(r) for r in self.db.fetchall("SELECT * FROM papers ORDER BY created_at DESC")]

    def delete(self, paper_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))

    def session_count(self, paper_id: str) -> int:
        return self.db.fetchone("SELECT COUNT(*) AS n FROM sessions WHERE paper_id = ?", (paper_id,))["n"]

    def touch(self, paper_id: str) -> None:
        self.db.execute("UPDATE papers SET updated_at = ? WHERE id = ?", (utcnow_iso(), paper_id))

    # ------------------------------------------------------------- units
    def add_unit(self, paper_id: str, unit: PaperUnitIn, short_explanation: str, keywords: list[str], origin: str) -> str:
        uid = new_id()
        self.db.execute(
            "INSERT INTO paper_units(id, paper_id, unit_type, title, content, short_explanation, keywords, "
            "source_reference, presenter_priority, origin, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uid, paper_id, unit.unit_type.value, unit.title, unit.content, short_explanation, dumps(keywords),
             unit.source_reference, unit.presenter_priority, origin, utcnow_iso()),
        )
        return uid

    def update_unit(self, unit_id: str, fields: dict) -> None:
        allowed = {"unit_type", "title", "content", "short_explanation", "keywords", "source_reference", "presenter_priority"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?")
            params.append(dumps(v) if k == "keywords" else (v.value if hasattr(v, "value") else v))
        sets.append("origin = 'manual_edit'")
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE paper_units SET {', '.join(sets)} WHERE id = ?", (*params, unit_id))
            conn.execute("DELETE FROM paper_embeddings WHERE unit_id = ?", (unit_id,))

    @staticmethod
    def _unit(row: dict) -> PaperUnit:
        return PaperUnit(
            id=row["id"], paper_id=row["paper_id"], unit_type=row["unit_type"], title=row["title"],
            content=row["content"], short_explanation=row["short_explanation"], keywords=loads(row["keywords"], []),
            source_reference=row["source_reference"], presenter_priority=row["presenter_priority"], origin=row["origin"],
        )

    def list_units(self, paper_id: str) -> list[PaperUnit]:
        rows = self.db.fetchall("SELECT * FROM paper_units WHERE paper_id = ? ORDER BY created_at, rowid", (paper_id,))
        return [self._unit(r) for r in rows]

    def get_unit(self, unit_id: str) -> PaperUnit | None:
        row = self.db.fetchone("SELECT * FROM paper_units WHERE id = ?", (unit_id,))
        return self._unit(row) if row else None

    def get_units(self, unit_ids: list[str]) -> dict[str, PaperUnit]:
        if not unit_ids:
            return {}
        marks = ",".join("?" * len(unit_ids))
        rows = self.db.fetchall(f"SELECT * FROM paper_units WHERE id IN ({marks})", unit_ids)
        return {r["id"]: self._unit(r) for r in rows}

    def indexed_unit_ids(self, paper_id: str) -> set[str]:
        rows = self.db.fetchall("SELECT unit_id FROM paper_embeddings WHERE paper_id = ?", (paper_id,))
        return {r["unit_id"] for r in rows}

    # -------------------------------------------------------- embeddings
    def replace_embeddings(self, paper_id: str, unit_ids: list[str], matrix: np.ndarray, model_name: str) -> None:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM paper_embeddings WHERE paper_id = ?", (paper_id,))
            conn.executemany(
                "INSERT INTO paper_embeddings(unit_id, paper_id, model_name, dim, vector, created_at) VALUES (?,?,?,?,?,?)",
                [(uid, paper_id, model_name, int(matrix.shape[1]), to_blob(vec), now) for uid, vec in zip(unit_ids, matrix)],
            )

    def load_embeddings(self, paper_id: str) -> tuple[list[str], np.ndarray, set[str]]:
        rows = self.db.fetchall(
            "SELECT unit_id, vector, model_name FROM paper_embeddings WHERE paper_id = ? ORDER BY unit_id", (paper_id,)
        )
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32), set()
        return (
            [r["unit_id"] for r in rows],
            np.vstack([from_blob(r["vector"]) for r in rows]),
            {r["model_name"] for r in rows},
        )
