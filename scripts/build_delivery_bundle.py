"""Build a portable ZIP bundle of the meeting-minutes database and sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import DATABASE_PATH, GAZETTEER_DIR, MINUTES_DIR, ROOT_DIR


ARCHIVE_ROOT = "lucera_meeting_minutes"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_text(value: str) -> str:
    """Convert workspace paths embedded in JSON/metadata to archive paths."""

    replacements = (
        (MINUTES_DIR.resolve(), Path("dataset/minutes")),
        (GAZETTEER_DIR.resolve().parent, Path("reference")),
        ((ROOT_DIR / "data" / "dataset" / "minutes").resolve(), Path("dataset/minutes")),
        ((ROOT_DIR / "data" / "reference").resolve(), Path("reference")),
        (ROOT_DIR / "data" / "dataset" / "minutes", Path("dataset/minutes")),
        (ROOT_DIR / "data" / "reference", Path("reference")),
    )
    rewritten = value
    for source, target in replacements:
        source_text = str(source).replace("/", "\\").rstrip("\\")
        target_text = target.as_posix()
        variants = (
            (source_text, target_text),
            (source_text.replace("\\", "/"), target_text),
            (source_text.replace("\\", "\\\\"), target_text.replace("/", "\\\\")),
        )
        for old, new in variants:
            rewritten = rewritten.replace(old, new)
    # Relative paths recorded by the collectors.
    for old, new in (
        ("data\\dataset\\minutes", "dataset\\minutes"),
        ("data/dataset/minutes", "dataset/minutes"),
        ("data\\reference", "reference"),
        ("data/reference", "reference"),
    ):
        rewritten = rewritten.replace(old, new)
        rewritten = rewritten.replace(old.replace("\\", "\\\\"), new.replace("\\", "\\\\"))
    return rewritten


def make_portable_database(source: Path, target: Path) -> dict[str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()

    storage_updates = 0
    metadata_updates = 0
    minutes_root = MINUTES_DIR.resolve()
    conn = sqlite3.connect(target)
    try:
        for table in ("source_document", "document_artifact"):
            rows = conn.execute(f"SELECT rowid, storage_uri FROM {table} WHERE storage_uri IS NOT NULL").fetchall()
            for rowid, value in rows:
                current = str(value)
                try:
                    relative = Path(current).resolve().relative_to(minutes_root)
                except (ValueError, OSError):
                    continue
                portable = (Path("dataset/minutes") / relative).as_posix()
                if portable != current:
                    conn.execute(f"UPDATE {table} SET storage_uri=? WHERE rowid=?", (portable, rowid))
                    storage_updates += 1
        for table, column in (
            ("source_document", "raw_payload_json"),
            ("source_document", "metadata_json"),
            ("document_artifact", "metadata_json"),
        ):
            rows = conn.execute(f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL").fetchall()
            for rowid, value in rows:
                current = str(value)
                portable = portable_text(current)
                if portable != current:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (portable, rowid))
                    metadata_updates += 1
        conn.commit()
    finally:
        conn.close()
    return {"storage_uri_updates": storage_updates, "metadata_updates": metadata_updates}


def add_tree(archive: zipfile.ZipFile, source: Path, archive_root: str) -> int:
    count = 0
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            archive.write(path, f"{ARCHIVE_ROOT}/{archive_root}/{relative}")
            count += 1
    return count


def build_bundle(output: Path) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing archive: {output}")
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"database not found: {DATABASE_PATH}")
    if not MINUTES_DIR.exists():
        raise FileNotFoundError(f"minutes dataset not found: {MINUTES_DIR}")

    work_dir = output.parent / ".delivery_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    portable_db = work_dir / "lucera_minutes.sqlite3"
    db_updates = make_portable_database(DATABASE_PATH, portable_db)

    readme = """# Lucera 회의록 원문 DB 전달본

이 패키지는 광주·전남 회의록 원문 DB와 원문/변환/추출 artifact를 함께 담은 전달용 묶음입니다.

- `lucera_minutes.sqlite3`: 전달용 SQLite DB
- `dataset/minutes/original/`: API JSON, HTML, HWP/HWPX, 공식 PDF 원문
- `dataset/minutes/normalized/`: HWP·HTML 변환 PDF
- `dataset/minutes/extracted/`: OpenDataLoader JSON/text 분석 결과
- `dataset/minutes/manifests/`, `reports/`, `qa/`: 수집·검증·품질 자료
- `reference/gazetteer/`: 광주·전남 행정구역·지명 사전

DB의 `storage_uri`는 이 패키지 루트 기준 상대경로입니다. 따라서 압축 해제 후 별도 경로 설정 없이 원문과 DB를 함께 전달할 수 있습니다.

API 키와 `config.py`는 포함하지 않았습니다. 이 전달본은 회의록 DB·원문 보존 및 분석 결과 전달을 위한 패키지입니다.
"""
    manifest: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": ARCHIVE_ROOT,
        "portable_database": "lucera_minutes.sqlite3",
        "included": [
            "lucera_minutes.sqlite3",
            "dataset/minutes/**",
            "reference/gazetteer/**",
            "db/schema.sql",
            "db/schema.postgres.sql",
            "docs/DB_구조.md",
            "docs/데이터셋_폴더구조.md",
        ],
        "database_path_rewrites": db_updates,
    }
    readme_path = work_dir / "DELIVERY_README.md"
    manifest_path = work_dir / "DELIVERY_MANIFEST.json"
    readme_path.write_text(readme, encoding="utf-8")

    file_count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.write(portable_db, f"{ARCHIVE_ROOT}/lucera_minutes.sqlite3")
        file_count += 1
        file_count += add_tree(archive, MINUTES_DIR, "dataset/minutes")
        file_count += add_tree(archive, GAZETTEER_DIR, "reference/gazetteer")
        for source, destination in (
            (ROOT_DIR / "db" / "schema.sql", f"{ARCHIVE_ROOT}/db/schema.sql"),
            (ROOT_DIR / "db" / "schema.postgres.sql", f"{ARCHIVE_ROOT}/db/schema.postgres.sql"),
            (ROOT_DIR / "docs" / "DB_구조.md", f"{ARCHIVE_ROOT}/docs/DB_구조.md"),
            (ROOT_DIR / "docs" / "데이터셋_폴더구조.md", f"{ARCHIVE_ROOT}/docs/데이터셋_폴더구조.md"),
        ):
            archive.write(source, destination)
            file_count += 1
        manifest["file_count_before_metadata"] = file_count + 2
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        archive.write(readme_path, f"{ARCHIVE_ROOT}/DELIVERY_README.md")
        archive.write(manifest_path, f"{ARCHIVE_ROOT}/DELIVERY_MANIFEST.json")

    with zipfile.ZipFile(output, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP integrity check failed at {bad_member}")
        archive_members = len(archive.infolist())

    portable_db.unlink(missing_ok=True)
    try:
        work_dir.rmdir()
    except OSError:
        pass
    return {
        "output": str(output.resolve()),
        "size_bytes": output.stat().st_size,
        "sha256": sha256(output),
        "archive_members": archive_members,
        "database_path_rewrites": db_updates,
        "zip_integrity": "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "artifacts" / "lucera_meeting_minutes_delivery_20260903.zip")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build_bundle(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
