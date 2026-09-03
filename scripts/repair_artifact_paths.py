"""Repair and verify canonical artifact paths after workspace moves."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import (
    ARTIFACTS_DIR,
    DB_BACKUP_DIR,
    DATABASE_PATH,
    MINUTES_DIR,
    PUBLIC_API_ARCHIVE_DIR,
    REFERENCE_DIR,
    SOURCE_MATERIALS_DIR,
)


def backup_database(source: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = DB_BACKUP_DIR / f"lucera_minutes_pre_path_repair_{stamp}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return target


def _rewrite_text(value: str) -> str:
    old_root = str(ROOT / "data" / "browser_pdf_test").replace("/", "\\").rstrip("\\")
    new_root = str(MINUTES_DIR.resolve()).replace("/", "\\").rstrip("\\")
    old_root_forward = old_root.replace("\\", "/")
    new_root_forward = new_root.replace("\\", "/")
    folder_map = {
        "api_raw": "original/api_json",
        "api_listings": "original/api_listings",
        "html": "original/html",
        "raw": "original/hwp",
        "pdf_original": "original/pdf",
        "converted": "normalized/pdf_from_hwp",
        "converted_compact": "normalized/pdf_from_html",
        "opendataloader": "extracted/opendataloader",
        "opendataloader2": "extracted/opendataloader_experiment",
    }
    rewritten = value
    for old_folder, new_folder in folder_map.items():
        new_folder_backslash = new_folder.replace("/", "\\")
        pairs = (
            (f"{old_root}\\{old_folder}\\", f"{new_root}\\{new_folder_backslash}\\"),
            (f"{old_root}/{old_folder}/", f"{new_root}/{new_folder}/"),
            (f"{old_root_forward}/{old_folder}/", f"{new_root_forward}/{new_folder}/"),
        )
        for old_path, new_path in pairs:
            # JSON metadata stores Windows backslashes escaped as ``\\``.
            rewritten = rewritten.replace(old_path, new_path)
            rewritten = rewritten.replace(old_path.replace("\\", "\\\\"), new_path.replace("\\", "\\\\"))
        # Some historical JSON metadata mixed separators immediately before
        # ``data``. Replace the stable ASCII dataset suffix as a fallback.
        rewritten = rewritten.replace(
            f"data/browser_pdf_test/{old_folder}/",
            f"data/dataset/minutes/{new_folder}/",
        )
        old_suffix = f"data\\{old_folder}\\"
        new_suffix = f"data\\dataset\\minutes\\{new_folder_backslash}\\"
        old_legacy_suffix = f"data\\browser_pdf_test\\{old_folder}\\"
        rewritten = rewritten.replace(old_suffix, new_suffix)
        rewritten = rewritten.replace(old_legacy_suffix, new_suffix)
        rewritten = rewritten.replace(
            old_legacy_suffix.replace("\\", "\\\\"),
            new_suffix.replace("\\", "\\\\"),
        )
        rewritten = rewritten.replace(old_suffix.replace("\\", "\\\\"), new_suffix.replace("\\", "\\\\"))
    rewritten = rewritten.replace("\\extracted\\extracted\\", "\\extracted\\")
    rewritten = rewritten.replace("/extracted/extracted/", "/extracted/")
    simple_pairs = (
        ("data\\browser_minutes.sqlite3", "data\\db\\lucera_minutes.sqlite3"),
        ("data/browser_minutes.sqlite3", "data/db/lucera_minutes.sqlite3"),
        ("data\\lucera.sqlite3", "data\\db\\snapshots\\lucera_initial.sqlite3"),
        ("data/lucera.sqlite3", "data/db/snapshots/lucera_initial.sqlite3"),
        ("data\\browser_pdf_test", "data\\dataset\\minutes"),
        ("data/browser_pdf_test", "data/dataset/minutes"),
    )
    for old_path, new_path in simple_pairs:
        rewritten = rewritten.replace(old_path, new_path)
        rewritten = rewritten.replace(old_path.replace("\\", "\\\\"), new_path.replace("\\", "\\\\"))
    legacy_roots = (
        (ROOT / "_솔버톤톤_extracted", SOURCE_MATERIALS_DIR / "solverthon_bundle"),
        (ROOT / "_api_build_20260817", PUBLIC_API_ARCHIVE_DIR / "solar_permit_20260817"),
    )
    for old_path, new_path in legacy_roots:
        old_actual = str(old_path.resolve())
        new_actual = str(new_path.resolve())
        for old_form, new_form in (
            (old_actual, new_actual),
            (old_actual.replace("\\", "/"), new_actual.replace("\\", "/")),
            (old_actual.replace("\\", "\\\\"), new_actual.replace("\\", "\\\\")),
        ):
            rewritten = rewritten.replace(old_form, new_form)
    return rewritten


def repair(database: Path) -> dict[str, int]:
    conn = sqlite3.connect(database)
    updated = 0
    json_updated = 0
    missing_before = 0
    missing_after = 0
    try:
        for table in ("source_document", "document_artifact"):
            rows = conn.execute(f"SELECT rowid, storage_uri FROM {table} WHERE storage_uri IS NOT NULL").fetchall()
            for rowid, uri in rows:
                current = str(uri)
                if not Path(current).exists():
                    missing_before += 1
                fixed = _rewrite_text(current)
                if fixed != current:
                    conn.execute(f"UPDATE {table} SET storage_uri=? WHERE rowid=?", (fixed, rowid))
                    updated += 1
                if not Path(fixed).exists():
                    missing_after += 1
        for table, column in (
            ("source_document", "raw_payload_json"),
            ("source_document", "metadata_json"),
            ("document_artifact", "metadata_json"),
        ):
            rows = conn.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
            for rowid, value in rows:
                current = str(value)
                fixed = _rewrite_text(current)
                if fixed != current:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (fixed, rowid))
                    json_updated += 1
        conn.commit()
    finally:
        conn.close()
    return {"storage_uri_updated": updated, "json_fields_updated": json_updated, "missing_before": missing_before, "missing_after": missing_after}


def repair_text_files() -> int:
    updated = 0
    roots = (MINUTES_DIR, REFERENCE_DIR, ARTIFACTS_DIR)
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name == "workspace_reorganization.json":
                continue
            if path.suffix.lower() not in {".json", ".md", ".txt", ".csv"}:
                continue
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fixed = _rewrite_text(current)
            if fixed != current:
                path.write_text(fixed, encoding="utf-8")
                updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"DB not found: {args.db}")
    backup = None if args.no_backup else backup_database(args.db)
    print({"backup": str(backup) if backup else None, "text_files_updated": repair_text_files(), **repair(args.db)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
