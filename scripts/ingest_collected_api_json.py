"""Integrate the collected CLiK detail JSON dataset into the canonical DB.

This is intentionally separate from the regional top-up job.  A regional
top-up is allowed to stop at a target count; a rebuild of the collected
dataset must inspect every JSON file currently archived on disk.  Only
documents that pass the same precision-v2 dispute classifier used by the
regional enrichment job are inserted.  Support-only or merely topical
documents are reported, not padded into the conflict database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from lucera.db import LuceraDB
from lucera.ingest import make_clik_bundle, materialize_clik_bundle
from lucera.paths import API_JSON_DIR, DATABASE_PATH, MINUTES_REPORT_DIR
from lucera.review import rebuild_case_reviews
from scripts.enrich_browser_db_from_local_api import _api_artifact, _score_bundle


def _existing_docids(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT source_record_key FROM source_document "
            "WHERE source_record_key IS NOT NULL"
        ).fetchall()
    }


def _region_code(path: Path) -> str:
    parent = path.parent.name
    return parent.removeprefix("region_") if parent.startswith("region_") else "unassigned"


def _load_detail(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("DOCID") or not payload.get("MINTS_HTML"):
        return None
    return payload


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 수집 API 원문 통합 재구축 보고서",
        "",
        f"- 실행시각(UTC): {report['started_at']}",
        f"- 종료시각(UTC): {report['finished_at']}",
        f"- 입력 원문: {report['input_directory']}",
        f"- 전체 JSON: {report['files_seen']}건",
        f"- 신규 적재: {report['documents_added']}건",
        f"- 기존 DB 중복/보존: {report['already_in_db']}건",
        f"- 고정밀 판별 탈락: {report['rejected_count']}건",
        f"- 오류: {report['error_count']}건",
        "",
        "이 실행은 새 API 호출을 하지 않고 현재 디스크에 보존된 API 상세 원문만 사용했습니다. "
        "`MINTS_HTML`을 문단·문장으로 파싱하고 precision-v2 판별을 통과한 문서만 분쟁이력 후보로 적재했습니다. "
        "PDF가 없는 문서는 API JSON 원문으로 남기며 PDF가 있는 것처럼 표시하지 않습니다.",
        "",
        "| 지역 폴더 | 입력 | DB 중복 | 통과 | 탈락 | 오류 | 적재 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for code in sorted(report["regions"]):
        row = report["regions"][code]
        lines.append(
            f"| {code} | {row['files_seen']} | {row['already_in_db']} | "
            f"{row['accepted']} | {row['rejected']} | {row['errors']} | {row['added']} |"
        )
    lines.extend(
        [
            "",
            "## 탈락 사유",
            "",
            "| 사유 | 건수 |",
            "|---|---:|",
        ]
    )
    for reason, count in sorted(report["rejection_reasons"].items()):
        lines.append(f"| {reason} | {count} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--input", type=Path, default=API_JSON_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=MINUTES_REPORT_DIR / "api_dataset_rebuild_20260903.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    files = sorted(args.input.rglob("*.json"))
    report: dict[str, Any] = {
        "started_at": started_at,
        "finished_at": None,
        "elapsed_seconds": None,
        "input_directory": str(args.input),
        "database": str(args.db),
        "dry_run": args.dry_run,
        "files_seen": len(files),
        "documents_added": 0,
        "already_in_db": 0,
        "rejected_count": 0,
        "error_count": 0,
        "rejection_reasons": Counter(),
        "errors": [],
        "selected": [],
        "regions": {},
        "selection_policy": (
            "precision-v2 sentence classifier + explicit dispute marker or "
            "standalone high-specificity issue; no support-only padding"
        ),
    }

    db = LuceraDB(args.db)
    existing = _existing_docids(db.conn)
    seen_docids: set[str] = set()
    try:
        for path in files:
            code = _region_code(path)
            region = report["regions"].setdefault(
                code,
                {"files_seen": 0, "already_in_db": 0, "accepted": 0, "rejected": 0, "errors": 0, "added": 0},
            )
            region["files_seen"] += 1
            payload = _load_detail(path)
            if payload is None:
                region["rejected"] += 1
                report["rejected_count"] += 1
                report["rejection_reasons"]["INVALID_OR_NO_DETAIL_HTML"] += 1
                continue

            docid = str(payload["DOCID"])
            if docid in seen_docids:
                region["rejected"] += 1
                report["rejected_count"] += 1
                report["rejection_reasons"]["DUPLICATE_INPUT_DOCID"] += 1
                continue
            seen_docids.add(docid)
            if docid in existing:
                region["already_in_db"] += 1
                report["already_in_db"] += 1
                continue

            try:
                bundle = make_clik_bundle(payload)
                score = _score_bundle(bundle)
                if not score["accepted"]:
                    region["rejected"] += 1
                    report["rejected_count"] += 1
                    report["rejection_reasons"]["NO_EXPLICIT_DISPUTE_EVIDENCE"] += 1
                    report["selected"].append({"docid": docid, "path": str(path), "status": "rejected", **score})
                    continue

                region["accepted"] += 1
                bundle["source"]["metadata"] = {
                    **bundle["source"].get("metadata", {}),
                    "rebuild": {
                        "method": "collected_api_json_dataset",
                        "input_path": str(path.resolve()),
                        "selection_score": score["score"],
                        "selection_features": score,
                        "pdf_materialized": False,
                    },
                }
                bundle["meeting"]["metadata"] = {
                    **bundle["meeting"].get("metadata", {}),
                    "rebuild_method": "collected_api_json_dataset",
                    "pdf_materialized": False,
                }
                api_artifacts = [
                    artifact
                    for artifact in bundle.setdefault("artifacts", [])
                    if artifact.get("artifact_role") == "official_source"
                ]
                if api_artifacts:
                    api_artifacts[0].update(_api_artifact(bundle, payload))
                else:
                    bundle["artifacts"].append(_api_artifact(bundle, payload))
                materialized_path = materialize_clik_bundle(
                    bundle,
                    args.input,
                    region_code=code,
                )
                bundle["source"]["metadata"]["api_detail_file"] = materialized_path

                record = {"docid": docid, "path": str(path), "status": "accepted", **score}
                if not args.dry_run:
                    db.insert_document_bundle(bundle)
                    db.commit()
                    existing.add(docid)
                    region["added"] += 1
                    report["documents_added"] += 1
                    record["status"] = "added"
                report["selected"].append(record)
            except Exception as exc:  # keep the rebuild auditable and continue
                region["errors"] += 1
                report["error_count"] += 1
                report["errors"].append({"path": str(path), "docid": docid, "error": str(exc)[:500]})

        if not args.dry_run:
            report["review"] = rebuild_case_reviews(db)
            db.commit()
        else:
            report["review"] = {"dry_run": True}
    finally:
        db.close()

    report["rejection_reasons"] = dict(report["rejection_reasons"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "output_md": str(args.output.with_suffix(".md")),
                "dry_run": args.dry_run,
                "files_seen": report["files_seen"],
                "documents_added": report["documents_added"],
                "already_in_db": report["already_in_db"],
                "rejected_count": report["rejected_count"],
                "error_count": report["error_count"],
                "elapsed_seconds": report["elapsed_seconds"],
                "review": report["review"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
