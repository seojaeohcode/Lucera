from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from lucera.db import LuceraDB, stable_id
from lucera.ingest import make_clik_bundle, materialize_clik_bundle
from lucera.regions import region_catalog
from lucera.review import rebuild_case_reviews
from lucera.paths import API_JSON_DIR, DATABASE_PATH, DB_SNAPSHOT_DIR, MINUTES_REPORT_DIR


NEGATIVE_MARKERS = (
    "민원", "반대", "반발", "갈등", "분쟁", "피해", "우려", "훼손", "소송",
    "취소", "요구", "불편", "위험", "문제", "대책위", "주민피해",
)


def _db_docids(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT source_record_key FROM source_document WHERE source_record_key IS NOT NULL"
        ).fetchall()
    }


def _db_region_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT administrative_region_code, COUNT(DISTINCT document_id)
          FROM meeting
         WHERE administrative_region_code IS NOT NULL
         GROUP BY administrative_region_code
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _source_rows(conn: sqlite3.Connection, region_code: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT d.source_record_key AS docid, d.raw_payload_json,
               d.original_file_url, d.title, m.meeting_date,
               COUNT(DISTINCT e.episode_id) AS old_episode_count
          FROM source_document d
          JOIN meeting m ON m.document_id=d.document_id
          LEFT JOIN episodes e ON e.document_id=d.document_id
         WHERE m.administrative_region_code=?
         GROUP BY d.document_id
         ORDER BY m.meeting_date DESC, d.source_record_key
        """,
        (region_code,),
    ).fetchall()


def _score_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    issues = [issue for segment in bundle.get("segments", []) for issue in segment.get("issues", [])]
    texts = " ".join(segment.get("text_original", "") for segment in bundle.get("segments", []))
    negative_hits = sorted({term for term in NEGATIVE_MARKERS if term in texts})
    opposition_count = sum(issue.get("polarity") in {"opposition", "mixed"} for issue in issues)
    high_precision_count = sum(
        "standalone_high" in str((issue.get("metadata") or {}).get("rule_id", ""))
        or issue.get("issue_code") == "glare_reflection"
        for issue in issues
    )
    subject_segments = sum(
        bool((segment.get("metadata") or {}).get("keyword_classifier", {}).get("solar_related"))
        for segment in bundle.get("segments", [])
    )
    # A document is eligible for enrichment only if the same sentence-grounded
    # classifier found a concrete issue and there is either dispute polarity,
    # an explicit negative marker, or a standalone high-specificity issue.
    accepted = bool(issues) and bool(opposition_count or negative_hits or high_precision_count)
    score = (
        (1000 if accepted else 0)
        + opposition_count * 30
        + high_precision_count * 20
        + len(negative_hits) * 5
        + len(issues)
        + (2 if bundle["source"].get("original_file_url") else 0)
    )
    return {
        "accepted": accepted,
        "score": score,
        "issue_count": len(issues),
        "issue_codes": sorted({str(issue.get("issue_code")) for issue in issues}),
        "opposition_count": opposition_count,
        "high_precision_count": high_precision_count,
        "negative_hits": negative_hits,
        "subject_segments": subject_segments,
        "old_episode_count": None,
    }


def _api_artifact(bundle: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    docid = str(bundle["source"].get("source_record_key"))
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    document_id = bundle["source"].get("document_id") or stable_id("document", "clik_minutes", docid)
    return {
        "artifact_id": stable_id("artifact", document_id, "api_detail_json", digest),
        # Keep the existing SQLite enum stable.  The metadata explicitly
        # identifies this as the API detail response; the role means the
        # public source artifact, not a claim that a local PDF exists.
        "artifact_role": "official_source",
        "storage_uri": None,
        "source_url": bundle["source"].get("source_url"),
        "mime_type": "application/json",
        "file_name": f"{docid}.json",
        "sha256": digest,
        "file_size_bytes": len(encoded),
        "acquisition_method": "api_local_archive",
        "parser_name": "clik-api",
        "parser_version": "1.0",
        "metadata": {
            "materialized_file": False,
            "payload_location": "source_document.raw_payload_json",
            "note": "공개 API 상세 원문을 로컬 보관 DB에서 재사용한 보강 자료",
        },
    }


def _read_payload(row: sqlite3.Row) -> dict[str, Any] | None:
    try:
        payload = json.loads(row["raw_payload_json"] or "{}")
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("MINTS_HTML") else None


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 지역별 DB 보강 실행 보고서",
        "",
        f"- 실행시각(UTC): {report['started_at']}",
        f"- 종료시각(UTC): {report['finished_at']}",
        f"- 시간예산: {report['time_budget_seconds']}초",
        f"- 실제 소요: {report['elapsed_seconds']}초",
        f"- 선택정책: {report['selection_policy']}",
        "",
        "이 실행은 새 API 호출을 하지 않고 보존된 API 상세 원문만 사용했습니다. PDF가 없는 보강 문서는 API JSON과 파싱된 페이지/문단을 출처로 남기며, 임의 PDF가 있는 것처럼 표시하지 않습니다.",
        "",
        "| 지역 | 시작 문서 | 목표 부족 | 검토 후보 | 고정밀 통과 | 추가 적재 | 종료 문서 | 판정 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["regions"]:
        lines.append(
            f"| {row['region']} | {row['start_documents']} | {row['need']} | {row['source_rows_considered']} | {row['accepted_candidates']} | {row['selected_count']} | {row.get('end_documents', row['start_documents'])} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## 판단 기준",
            "",
            "- `complete`: 지역별 DB 문서가 10건에 도달했습니다.",
            "- `shortfall_verified`: 보존 원문을 모두 검토했지만 문장 단위 쟁점·분쟁 근거가 부족해 임의 문서를 추가하지 않았습니다.",
            "- `shortfall_time_budget`: 시간예산이 먼저 소진되어 남은 원문을 검토하지 못했습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--source-db", type=Path, default=DB_SNAPSHOT_DIR / "lucera_initial.sqlite3")
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--time-budget-seconds", type=int, default=420)
    parser.add_argument("--output", type=Path, default=MINUTES_REPORT_DIR / "regional_enrichment_run.json")
    args = parser.parse_args()
    if args.target <= 0:
        raise ValueError("target must be positive")
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    deadline = started + args.time_budget_seconds

    db = LuceraDB(args.db)
    source = sqlite3.connect(args.source_db)
    source.row_factory = sqlite3.Row
    db_docids = _db_docids(db.conn)
    start_counts = _db_region_counts(db.conn)
    results: list[dict[str, Any]] = []
    total_added = 0
    timed_out = False
    try:
        for region in region_catalog():
            code = str(region["region_code"])
            start_count = start_counts.get(code, 0)
            need = max(0, args.target - start_count)
            row_result: dict[str, Any] = {
                "region_code": code,
                "region": region["name"],
                "start_documents": start_count,
                "target_documents": args.target,
                "need": need,
                "source_rows_considered": 0,
                "accepted_candidates": 0,
                "selected_count": 0,
                "selected": [],
                "rejected_by_precision": [],
                "status": "already_complete" if need == 0 else "pending",
                "end_documents": start_count,
            }
            if need == 0:
                results.append(row_result)
                continue
            if time.monotonic() >= deadline:
                timed_out = True
                row_result["status"] = "shortfall_time_budget"
                results.append(row_result)
                continue
            scored: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
            for source_row in _source_rows(source, code):
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                docid = str(source_row["docid"] or "")
                if not docid or docid in db_docids:
                    continue
                row_result["source_rows_considered"] += 1
                payload = _read_payload(source_row)
                if not payload:
                    row_result["rejected_by_precision"].append({"docid": docid, "reason": "NO_DETAIL_HTML"})
                    continue
                try:
                    bundle = make_clik_bundle(payload)
                    score = _score_bundle(bundle)
                    score["old_episode_count"] = int(source_row["old_episode_count"] or 0)
                    score["source_original_file_url"] = bool(source_row["original_file_url"])
                    if score["accepted"]:
                        scored.append((bundle, payload, score))
                    else:
                        row_result["rejected_by_precision"].append({"docid": docid, **score, "reason": "NO_EXPLICIT_DISPUTE_EVIDENCE"})
                except Exception as exc:
                    row_result["rejected_by_precision"].append({"docid": docid, "reason": f"BUILD_ERROR:{str(exc)[:180]}"})
            row_result["accepted_candidates"] = len(scored)
            scored.sort(key=lambda item: (-item[2]["score"], -item[2]["old_episode_count"], str(item[0]["source"].get("source_record_key"))))
            for bundle, payload, score in scored[:need]:
                docid = str(bundle["source"]["source_record_key"])
                bundle["source"]["metadata"] = {
                    **bundle["source"].get("metadata", {}),
                    "enrichment": {
                        "method": "local_api_raw_pool",
                        "source_database": str(args.source_db),
                        "selection_score": score["score"],
                        "selection_features": score,
                        "pdf_materialized": False,
                    },
                }
                bundle["meeting"]["metadata"] = {
                    **bundle["meeting"].get("metadata", {}),
                    "enrichment_method": "local_api_raw_pool",
                    "pdf_materialized": False,
                }
                # ``make_clik_bundle`` already carries the API JSON artifact.
                # Reuse that row for local-archive enrichment instead of
                # creating a duplicate with the same document/role/checksum.
                api_artifacts = [
                    artifact for artifact in bundle.setdefault("artifacts", [])
                    if artifact.get("artifact_role") == "official_source"
                ]
                if api_artifacts:
                    api_artifacts[0].update(_api_artifact(bundle, payload))
                else:
                    bundle["artifacts"].append(_api_artifact(bundle, payload))
                materialized_path = materialize_clik_bundle(
                    bundle,
                    API_JSON_DIR,
                    region_code=code,
                )
                bundle["source"]["metadata"]["api_detail_file"] = materialized_path
                db.insert_document_bundle(bundle)
                db.commit()
                db_docids.add(docid)
                row_result["selected"].append({"docid": docid, **score})
                row_result["selected_count"] += 1
                total_added += 1
            row_result["end_documents"] = start_count + row_result["selected_count"]
            if row_result["end_documents"] >= args.target:
                row_result["status"] = "completed"
            elif timed_out:
                row_result["status"] = "shortfall_time_budget"
            else:
                row_result["status"] = "shortfall_verified"
            results.append(row_result)
            if timed_out:
                # Keep the remaining regions explicit in the report.
                continue
        review = rebuild_case_reviews(db)
        db.commit()
    finally:
        source.close()
        db.close()
    elapsed = round(time.monotonic() - started, 2)
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "time_budget_seconds": args.time_budget_seconds,
        "timed_out": timed_out,
        "selection_policy": "current precision-v2 sentence classifier + explicit dispute marker/high-specificity issue; no support-only padding",
        "source_db": str(args.source_db),
        "target": args.target,
        "documents_added": total_added,
        "review": review,
        "regions": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "output_md": str(args.output.with_suffix('.md')), "documents_added": total_added, "elapsed_seconds": elapsed, "timed_out": timed_out, "review": review}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
