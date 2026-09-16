"""Delete every session whose retention period (RETENTION_DAYS) has passed.

    python scripts/purge_expired.py            # list expired sessions
    python scripts/purge_expired.py --delete   # permanently delete them
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.database import Database  # noqa: E402
from app.repositories.sessions import AuditRepository, SessionRepository  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    settings = get_settings()
    db = Database(settings.database_path)
    sessions, audit = SessionRepository(db), AuditRepository(db)
    expired = sessions.expired_ids()
    print(f"{len(expired)} expired session(s) (retention {settings.retention_days} days)")
    for sid in expired:
        print(" ", sid)
        if args.delete:
            sessions.delete(sid)
            audit.add("retention_purge", sid, {"retention_days": settings.retention_days})
    if args.delete:
        print("deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
