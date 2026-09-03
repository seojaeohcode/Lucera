"""Build a clean, Yeongam-only demo DB and optionally replace a live DB.

The deployment never uploads a live SQLite WAL database. It ships code and
rebuilds this small fixture database on the host, which makes a clone and a
fresh server deterministic and avoids copying a half-checkpointed WAL file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.db import LuceraDB
from lucera.synthetic import seed_synthetic


SCHEMA = ROOT / "db" / "schema.sql"


def build(path: Path) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = LuceraDB(path)
    try:
        db.initialize(SCHEMA)
        result = seed_synthetic(db)
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.commit()
        return {"database": str(path.resolve()), "seed": result, "stats": db.stats()}
    finally:
        db.close()


def replace_database(db_path: Path, backup_dir: Path) -> dict[str, object]:
    temp_path = db_path.with_name(f".{db_path.name}.rebuild-{uuid4().hex}.sqlite3")
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        built = build(temp_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        moved: list[str] = []
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(db_path) + suffix)
            if source.exists():
                target = backup_dir / f"{db_path.name}.{stamp}{suffix}.bak"
                os.replace(source, target)
                moved.append(str(target))
        os.replace(temp_path, db_path)
        return {"replaced": str(db_path.resolve()), "backup_files": moved, **built}
    finally:
        for leftover in (temp_path, Path(str(temp_path) + "-wal"), Path(str(temp_path) + "-shm")):
            if leftover.exists():
                leftover.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Lucera's Yeongam-only synthetic database")
    parser.add_argument("--db", required=True, type=Path, help="SQLite database path")
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--replace", action="store_true", help="atomically replace the database after building")
    args = parser.parse_args()
    backup_dir = args.backup_dir or args.db.parent / "backups"
    result = replace_database(args.db, backup_dir) if args.replace else build(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
