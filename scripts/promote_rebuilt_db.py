"""Promote a validated rebuild DB to the canonical Lucera database path."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import DB_BACKUP_DIR, DATABASE_PATH, MINUTES_REPORT_DIR


def check_database(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(path)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        documents = int(conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0])
        cases = int(conn.execute("SELECT COUNT(*) FROM conflict_case").fetchone()[0])
        artifacts = int(conn.execute("SELECT COUNT(*) FROM document_artifact").fetchone()[0])
    finally:
        conn.close()
    return {
        "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "source_documents": documents,
        "conflict_cases": cases,
        "artifacts": artifacts,
    }


def sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuilt", type=Path, required=True)
    parser.add_argument("--promote", action="store_true", help="검증 통과 후 canonical DB로 교체")
    args = parser.parse_args()
    rebuilt = args.rebuilt if args.rebuilt.is_absolute() else Path.cwd() / args.rebuilt
    if not rebuilt.exists():
        raise SystemExit(f"rebuilt DB not found: {rebuilt}")
    if rebuilt.resolve() == DATABASE_PATH.resolve():
        raise SystemExit("rebuilt DB must be different from canonical DB")

    rebuilt_check = check_database(rebuilt)
    if rebuilt_check["integrity_check"] != "ok" or rebuilt_check["foreign_key_violations"] != 0:
        raise SystemExit(f"rebuilt DB failed integrity checks: {rebuilt_check}")
    result: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "rebuilt_db": str(rebuilt.resolve()),
        "canonical_db": str(DATABASE_PATH.resolve()),
        "rebuilt_check": rebuilt_check,
        "promoted": False,
    }
    if args.promote:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = DB_BACKUP_DIR / f"lucera_minutes_pre_rebuild_{stamp}.sqlite3"
        if DATABASE_PATH.exists():
            sqlite_backup(DATABASE_PATH, backup)
            result["previous_db_backup"] = str(backup.resolve())
        os.replace(rebuilt, DATABASE_PATH)
        result["promoted"] = True
        result["canonical_check"] = check_database(DATABASE_PATH)
    report_path = MINUTES_REPORT_DIR / "db_promotion_20260903.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
