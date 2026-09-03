"""Create the pre-check tables that the browser-rebuilt database is missing.

`scripts/build_browser_db.py` rebuilds the meeting-record side of the schema but
does not create `siting_rule` or `case_process_event`.  Without them the chat
pre-check silently degrades: distance rules never run and the complaint-process
timeline is always empty.  This migration is additive and idempotent; it never
drops or rewrites existing rows.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "db" / "schema.sql"
DEFAULT_DB = ROOT / "data" / "db" / "lucera_minutes.sqlite3"

# Only these objects are (re)created.  Anything else in schema.sql is left alone.
WANTED = ("siting_rule", "case_process_event")


def _statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


CREATE_RE = re.compile(
    r"CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE
)


def _object_name(statement: str) -> str | None:
    """Return the created object name, ignoring any leading SQL comments."""

    match = CREATE_RE.search(statement)
    return match.group(1) if match else None


def _relevant(statement: str) -> bool:
    name = _object_name(statement)
    if not name:
        return False
    return any(name == table or name.startswith(f"idx_{table}") for table in WANTED)


def migrate(db_path: Path) -> dict[str, str]:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    result: dict[str, str] = {}
    try:
        for statement in _statements(schema_sql):
            if not _relevant(statement):
                continue
            name = _object_name(statement)
            assert name is not None
            conn.execute(statement)
            result[name] = "already_present" if name in existing else "created"
        conn.commit()
    finally:
        conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    for name, state in migrate(args.db).items():
        print(f"{state:>15}  {name}")


if __name__ == "__main__":
    main()
