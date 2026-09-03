"""Reorganize the Lucera workspace into a reproducible data layout.

The command is conservative: it refuses to overwrite a destination, makes a
SQLite backup before moving the active database, and updates artifact URIs
after the move. Run without ``--apply`` to inspect the planned operations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import (  # noqa: E402
    API_JSON_DIR,
    API_LISTINGS_DIR,
    ARTIFACTS_DIR,
    DB_BACKUP_DIR,
    DB_DIR,
    DB_SNAPSHOT_DIR,
    DATABASE_PATH,
    GAZETTEER_DIR,
    HWP_DIR,
    HWP_PDF_DIR,
    HTML_DIR,
    HTML_PDF_DIR,
    MINUTES_DIR,
    MINUTES_MANIFEST_DIR,
    MINUTES_QA_DIR,
    MINUTES_REPORT_DIR,
    MINUTES_REJECTED_DIR,
    OPENDATALOADER_DIR,
    OPENDATALOADER_EXPERIMENT_DIR,
    PDF_ORIGINAL_DIR,
    PUBLIC_API_ARCHIVE_DIR,
    SOURCE_MATERIALS_DIR,
    TEMP_DIR,
)


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def _move(source: Path, destination: Path, operations: list[dict[str, str]], *, apply: bool) -> None:
    if not source.exists():
        return
    if destination.exists():
        # A previous interrupted run may have created an empty target parent
        # with the same semantic name. It is safe to reuse only an empty dir.
        if destination.is_dir() and not any(destination.iterdir()):
            if apply:
                destination.rmdir()
        else:
            raise FileExistsError(f"destination already exists: {destination}")
    operations.append({"source": _relative(source), "destination": _relative(destination)})
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def _plan_root_artifacts(old_root: Path, operations: list[dict[str, str]], *, apply: bool) -> None:
    if not old_root.exists():
        return
    for path in sorted(old_root.iterdir(), key=lambda item: item.name):
        if path.is_dir():
            continue
        name = path.name
        if name.startswith("qa_") and path.suffix.lower() == ".png":
            destination = MINUTES_QA_DIR / "pdf" / name
        elif path.suffix.lower() == ".md":
            destination = MINUTES_REPORT_DIR / name
        elif (
            name == "browser_candidates.json"
            or name.startswith("manifest_")
            or name.startswith("html_manifest_")
            or name.startswith("html_missing_manifest_")
            or name.endswith("_manifest.json")
        ):
            destination = MINUTES_MANIFEST_DIR / name
        else:
            destination = MINUTES_REPORT_DIR / name
        _move(path, destination, operations, apply=apply)


def _backup_database(source: Path, *, apply: bool) -> Path | None:
    if not source.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = DB_BACKUP_DIR / f"lucera_minutes_pre_reorganization_{stamp}.sqlite3"
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(source)
        backup_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
            source_conn.close()
    return destination


def _update_database_uris(path: Path, old_root: Path) -> int:
    if not path.exists():
        return 0
    folder_map = {
        "api_raw": Path("original/api_json"),
        "api_listings": Path("original/api_listings"),
        "html": Path("original/html"),
        "raw": Path("original/hwp"),
        "pdf_original": Path("original/pdf"),
        "converted": Path("normalized/pdf_from_hwp"),
        "converted_compact": Path("normalized/pdf_from_html"),
        "opendataloader": Path("extracted/opendataloader"),
        "opendataloader2": Path("extracted/opendataloader_experiment"),
    }
    old_prefix = str(old_root.resolve()).replace("/", "\\").rstrip("\\") + "\\"
    conn = sqlite3.connect(path)
    changed = 0
    try:
        for table in ("source_document", "document_artifact"):
            rows = conn.execute(f"SELECT rowid, storage_uri FROM {table} WHERE storage_uri IS NOT NULL").fetchall()
            for rowid, uri in rows:
                current = str(uri)
                normalized = current.replace("/", "\\")
                updated = current
                if normalized.startswith(old_prefix):
                    relative = normalized[len(old_prefix):]
                    top_level, separator, remainder = relative.partition("\\")
                    target_root = folder_map.get(top_level)
                    if target_root is not None:
                        target = MINUTES_DIR / target_root
                        if separator and remainder:
                            target /= Path(remainder)
                        updated = str(target.resolve())
                if updated != current:
                    conn.execute(f"UPDATE {table} SET storage_uri=? WHERE rowid=?", (updated, rowid))
                    changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제 파일 이동과 DB 경로 갱신을 수행")
    args = parser.parse_args()

    old_data_root = ROOT / "data" / "browser_pdf_test"
    old_db = ROOT / "data" / "browser_minutes.sqlite3"
    operations: list[dict[str, str]] = []

    # Create the target directories first. This is harmless in dry-run mode.
    for path in (
        DB_DIR, DB_SNAPSHOT_DIR, DB_BACKUP_DIR, MINUTES_DIR, API_JSON_DIR,
        API_LISTINGS_DIR, HTML_DIR, HWP_DIR, PDF_ORIGINAL_DIR, HWP_PDF_DIR,
        HTML_PDF_DIR, OPENDATALOADER_DIR, OPENDATALOADER_EXPERIMENT_DIR,
        MINUTES_REJECTED_DIR, MINUTES_MANIFEST_DIR, MINUTES_REPORT_DIR,
        MINUTES_QA_DIR, GAZETTEER_DIR, SOURCE_MATERIALS_DIR,
        PUBLIC_API_ARCHIVE_DIR, ARTIFACTS_DIR, TEMP_DIR,
    ):
        if args.apply:
            path.parent.mkdir(parents=True, exist_ok=True)

    backup = _backup_database(old_db, apply=args.apply)
    if backup:
        operations.append({"source": _relative(old_db), "destination": _relative(backup), "kind": "sqlite_backup"})

    if old_db.exists():
        _move(old_db, DATABASE_PATH, operations, apply=args.apply)
        for suffix in ("-wal", "-shm"):
            _move(old_db.with_name(old_db.name + suffix), DATABASE_PATH.with_name(DATABASE_PATH.name + suffix), operations, apply=args.apply)

    # Preserve all historical DBs, including the original API database.
    for path in sorted((ROOT / "data").glob("*.sqlite3")):
        if path.name == "browser_minutes.sqlite3":
            continue
        destination_name = "lucera_initial.sqlite3" if path.name == "lucera.sqlite3" else path.name
        _move(path, DB_SNAPSHOT_DIR / destination_name, operations, apply=args.apply)
        for suffix in ("-wal", "-shm"):
            _move(path.with_name(path.name + suffix), DB_SNAPSHOT_DIR / f"{destination_name}{suffix}", operations, apply=args.apply)

    # Existing backups are retained, but placed beside the active database.
    _move(ROOT / "data" / "backups", DB_BACKUP_DIR / "legacy_backups", operations, apply=args.apply)

    # Move the dataset by semantic lifecycle rather than by the old collection
    # script's staging names.
    for source_name, destination in (
        ("api_raw", API_JSON_DIR),
        ("html", HTML_DIR),
        ("raw", HWP_DIR),
        ("pdf_original", PDF_ORIGINAL_DIR),
        ("converted", HWP_PDF_DIR),
        ("converted_compact", HTML_PDF_DIR),
        ("opendataloader", OPENDATALOADER_DIR),
        ("opendataloader2", OPENDATALOADER_EXPERIMENT_DIR),
        ("rejected_non_pdf", MINUTES_REJECTED_DIR),
    ):
        _move(old_data_root / source_name, destination, operations, apply=args.apply)
    _plan_root_artifacts(old_data_root, operations, apply=args.apply)

    # Empty region folders were old staging placeholders. They are only removed
    # after all content directories have moved above.
    if old_data_root.exists():
        for empty_dir in sorted(old_data_root.iterdir(), key=lambda item: item.name):
            if empty_dir.is_dir() and not any(empty_dir.iterdir()):
                if args.apply:
                    try:
                        empty_dir.rmdir()
                    except OSError:
                        # OneDrive may keep an empty placeholder directory
                        # undeletable. It is harmless and is reported instead.
                        continue
                operations.append({"source": _relative(empty_dir), "destination": "<removed empty staging directory>", "kind": "empty_dir"})
        if not any(old_data_root.iterdir()):
            if args.apply:
                try:
                    old_data_root.rmdir()
                except OSError:
                    pass
            operations.append({"source": _relative(old_data_root), "destination": "<removed empty staging directory>", "kind": "empty_dir"})

    _move(ROOT / "data" / "gazetteer", GAZETTEER_DIR, operations, apply=args.apply)
    _move(ROOT / "_솔버톤톤_extracted", SOURCE_MATERIALS_DIR / "solverthon_bundle", operations, apply=args.apply)
    _move(ROOT / "_api_build_20260817", PUBLIC_API_ARCHIVE_DIR / "solar_permit_20260817", operations, apply=args.apply)
    _move(ROOT / "_pdf_render", MINUTES_QA_DIR / "legacy_pdf_render", operations, apply=args.apply)
    _move(ROOT / "output", ARTIFACTS_DIR / "output", operations, apply=args.apply)
    _move(ROOT / "tmp", TEMP_DIR, operations, apply=args.apply)

    uri_updates = 0
    if args.apply:
        uri_updates = _update_database_uris(DATABASE_PATH, old_data_root)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": args.apply,
        "active_database": _relative(DATABASE_PATH),
        "database_backup": _relative(backup) if backup else None,
        "database_storage_uri_updates": uri_updates,
        "operations": operations,
    }
    report_path = MINUTES_REPORT_DIR / "workspace_reorganization.json"
    if args.apply:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
