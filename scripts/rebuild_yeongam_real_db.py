"""Rebuild a clean, real-data-only Yeongam database.

The input CSV is the official 1,549-row Yeongam permit register.  The
rebuild intentionally does not call ``seed_synthetic``: a fresh database is
made from the public permit register, the preserved Yeongam meeting-record
corpus, derived coordinates, and auditable permit↔meeting links.
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

from lucera.paths import DATABASE_PATH
from lucera.real_data import rebuild_real_db


DEFAULT_CSV = ROOT / "data" / "reference" / "yeongam_solar_permits_20260301.csv"
DEFAULT_GEO_CACHE = ROOT / "data" / "reference" / "yeongam_solar_geocoded_20260301.json"
DEFAULT_MINUTES = ROOT / "data" / "reference" / "minutes_corpus.json"
SCHEMA = ROOT / "db" / "schema.sql"


def build(path: Path, args: argparse.Namespace) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    return rebuild_real_db(
        path,
        SCHEMA,
        args.csv,
        args.geo_cache,
        args.minutes,
        geocode_workers=args.geocode_workers,
        map_sample_per_ri=args.map_sample_per_ri,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--geo-cache", type=Path, default=DEFAULT_GEO_CACHE)
    parser.add_argument("--minutes", type=Path, default=DEFAULT_MINUTES)
    parser.add_argument("--geocode-workers", type=int, default=6)
    parser.add_argument(
        "--map-sample-per-ri",
        type=int,
        default=6,
        help="리별 지도용 실제 허가 표본 수(기본 6, 5~7 권장). 0이면 공식 원장 전체를 지오코딩합니다.",
    )
    parser.add_argument("--replace", action="store_true", help="atomically replace --db and retain a backup")
    args = parser.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"permit CSV not found: {args.csv}")
    if not args.minutes.is_file():
        raise SystemExit(f"minutes corpus not found: {args.minutes}")

    if not args.replace:
        report = build(args.db, args)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    temp_path = args.db.with_name(f".{args.db.name}.real-rebuild-{uuid4().hex}.sqlite3")
    backup_dir = args.db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = build(temp_path, args)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        moved: list[str] = []
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(args.db) + suffix)
            if source.exists():
                target = backup_dir / f"{args.db.name}.{stamp}{suffix}.bak"
                os.replace(source, target)
                moved.append(str(target))
        os.replace(temp_path, args.db)
        report = {"replaced": str(args.db.resolve()), "backup_files": moved, **report}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        for leftover in (temp_path, Path(str(temp_path) + "-wal"), Path(str(temp_path) + "-shm")):
            if leftover.exists():
                leftover.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
