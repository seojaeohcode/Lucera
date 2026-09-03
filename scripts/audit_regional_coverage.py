from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from lucera.regions import region_catalog
from lucera.paths import DATABASE_PATH, DB_SNAPSHOT_DIR, MINUTES_DIR, MINUTES_REPORT_DIR


def _load_candidates(data_root: Path) -> dict[str, list[dict[str, Any]]]:
    path = data_root / "manifests" / "browser_candidates.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(code): [dict(item) for item in value.get("items", [])]
        for code, value in payload.items()
        if isinstance(value, dict)
    }


def _local_pdf_docids(data_root: Path, region_code: str) -> set[str]:
    """Return document IDs with a usable locally materialized PDF.

    Converted HWP PDFs are counted only when their source HWP/HWPX is present;
    this prevents an old/aborted conversion from being mistaken for a complete
    acquisition. HTML-print PDFs are counted independently because the HTML
    snapshot is their source artifact.
    """
    result: set[str] = set()
    hwp_dir = data_root / "normalized" / "pdf_from_hwp" / f"region_{region_code}"
    raw_dir = data_root / "original" / "hwp" / f"region_{region_code}"
    for pdf in hwp_dir.glob("*.pdf") if hwp_dir.exists() else []:
        if any((raw_dir / f"{pdf.stem}{suffix}").exists() for suffix in (".hwp", ".hwpx")):
            result.add(pdf.stem)
    html_dir = data_root / "normalized" / "pdf_from_html" / f"region_{region_code}"
    for pdf in html_dir.glob("*.pdf") if html_dir.exists() else []:
        if pdf.stat().st_size > 0:
            result.add(pdf.stem)
    return result


def _region_db_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    rows = conn.execute(
        """
        SELECT m.administrative_region_code AS region_code,
               COUNT(DISTINCT d.document_id) AS documents,
               COUNT(DISTINCT CASE WHEN m.meeting_id IS NOT NULL THEN d.document_id END) AS meetings,
               COUNT(DISTINCT CASE WHEN s.relevance_status='relevant' THEN d.document_id END) AS relevant_documents,
               COUNT(DISTINCT CASE WHEN e.episode_id IS NOT NULL THEN d.document_id END) AS episode_documents,
               COUNT(DISTINCT CASE WHEN a.artifact_id IS NOT NULL THEN d.document_id END) AS artifact_documents
          FROM source_document d
          JOIN meeting m ON m.document_id=d.document_id
          LEFT JOIN meeting_segment s ON s.meeting_id=m.meeting_id
          LEFT JOIN episodes e ON e.document_id=d.document_id
          LEFT JOIN document_artifact a ON a.document_id=d.document_id
         WHERE m.administrative_region_code IS NOT NULL
         GROUP BY m.administrative_region_code
        """
    ).fetchall()
    return {
        str(row[0]): {
            "documents": int(row[1]),
            "meetings": int(row[2]),
            "relevant_documents": int(row[3]),
            "episode_documents": int(row[4]),
            "artifact_documents": int(row[5]),
        }
        for row in rows
    }


def _source_db_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT m.administrative_region_code, COUNT(DISTINCT d.document_id)
          FROM source_document d
          JOIN meeting m ON m.document_id=d.document_id
         WHERE m.administrative_region_code IS NOT NULL
         GROUP BY m.administrative_region_code
        """
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _status(
    *,
    db_documents: int,
    target: int,
    candidate_pool: int,
    raw_api_pool: int,
    available: bool,
) -> tuple[str, str]:
    if db_documents >= target:
        return "complete", "지역별 DB 문서 목표 충족"
    if candidate_pool >= target:
        return "collection_gap", "정밀 후보는 충분하지만 PDF/변환/DB 적재가 목표에 미달"
    if not available:
        return "no_source", "공개 API에서 해당 지역 의회 식별자가 확인되지 않음"
    if raw_api_pool >= target:
        return "insufficient_pool", "보존 API 원문은 있으나 현재 정밀 민원 후보가 목표 미만; 일반·지원 문서는 숫자 보강에 사용하지 않음"
    if candidate_pool > 0 or raw_api_pool > 0:
        return "insufficient_pool", "현재 확보된 정밀 후보/원문 모수가 목표 미만"
    return "insufficient_pool", "현재 확보된 정밀 색인·원문 모수가 0건"


def _pdf_status(
    *,
    local_pdf_materialized: int,
    candidate_pool: int,
    target: int,
    available: bool,
) -> str:
    if local_pdf_materialized >= target:
        return "complete_pdf"
    if candidate_pool >= target:
        return "pdf_collection_gap"
    if not available and local_pdf_materialized == 0:
        return "pdf_no_source"
    return "pdf_pool_shortage"


def build_report(
    *,
    db_path: Path,
    source_db_path: Path,
    data_root: Path,
    target: int,
    time_budget_seconds: int,
    phase: str,
    api_status: str | None = None,
) -> dict[str, Any]:
    db = sqlite3.connect(db_path)
    source = sqlite3.connect(source_db_path)
    try:
        db_counts = _region_db_counts(db)
        raw_counts = _source_db_counts(source)
    finally:
        db.close()
        source.close()
    candidates = _load_candidates(data_root)
    rows: list[dict[str, Any]] = []
    for region in region_catalog():
        code = str(region["region_code"])
        local_pdfs = _local_pdf_docids(data_root, code)
        candidate_items = candidates.get(code, [])
        candidate_ids = {str(item.get("docid")) for item in candidate_items if item.get("docid")}
        local_pdf_count = len(candidate_ids & local_pdfs)
        status, reason = _status(
            db_documents=db_counts.get(code, {}).get("documents", 0),
            target=target,
            candidate_pool=len(candidate_ids),
            raw_api_pool=raw_counts.get(code, 0),
            available=bool(region.get("available")),
        )
        rows.append(
            {
                "region_code": code,
                "region": region["name"],
                "region_group": region["region_group"],
                "available": bool(region.get("available")),
                "target_documents": target,
                "db_documents": db_counts.get(code, {}).get("documents", 0),
                "db_relevant_documents": db_counts.get(code, {}).get("relevant_documents", 0),
                "db_episode_documents": db_counts.get(code, {}).get("episode_documents", 0),
                "db_artifact_documents": db_counts.get(code, {}).get("artifact_documents", 0),
                "candidate_pool": len(candidate_ids),
                "local_pdf_materialized": local_pdf_count,
                "pdf_status": _pdf_status(
                    local_pdf_materialized=local_pdf_count,
                    candidate_pool=len(candidate_ids),
                    target=target,
                    available=bool(region.get("available")),
                ),
                "raw_api_pool": raw_counts.get(code, 0),
                "shortfall": max(0, target - db_counts.get(code, {}).get("documents", 0)),
                "status": status,
                "reason": reason,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "target_documents_per_region": target,
        "time_budget_seconds": time_budget_seconds,
        "api_status": api_status,
        "db_path": str(db_path),
        "source_db_path": str(source_db_path),
        "data_root": str(data_root),
        "regions": rows,
        "summary": {
            "regions": len(rows),
            "complete": sum(row["status"] == "complete" for row in rows),
            "collection_gap": sum(row["status"] == "collection_gap" for row in rows),
            "insufficient_pool": sum(row["status"] == "insufficient_pool" for row in rows),
            "no_source": sum(row["status"] == "no_source" for row in rows),
            "complete_pdf": sum(row["pdf_status"] == "complete_pdf" for row in rows),
            "pdf_collection_gap": sum(row["pdf_status"] == "pdf_collection_gap" for row in rows),
            "pdf_pool_shortage": sum(row["pdf_status"] == "pdf_pool_shortage" for row in rows),
            "pdf_no_source": sum(row["pdf_status"] == "pdf_no_source" for row in rows),
            "db_documents": sum(row["db_documents"] for row in rows),
            "db_shortfall_total": sum(row["shortfall"] for row in rows),
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 광주·전남 지역별 데이터 보강 감사",
        "",
        f"- 기준시각(UTC): {report['generated_at']}",
        f"- 단계: `{report['phase']}`",
        f"- 지역별 목표: {report['target_documents_per_region']}개 문서",
        f"- 보강 시간예산: {report['time_budget_seconds']}초",
        f"- API 상태: {report.get('api_status') or '기록 없음'}",
        "",
        "`candidate_pool`은 기존 정밀 태양광 후보, `raw_api_pool`은 로컬에 보존된 API 상세 원문 전체, `local_pdf_materialized`는 실제 분석 가능한 로컬 PDF가 있는 후보 수입니다.",
        "`collection_gap`은 원문 후보가 있는데 PDF/변환/DB 적재가 부족한 경우, `insufficient_pool`은 현재 확보한 정밀 후보 자체가 목표 미만인 경우입니다. 이는 후보를 임의로 민원으로 부풀리지 않기 위한 구분입니다.",
        "",
        "| 지역 | 목표 | DB 문서 | 민원성 문서 | 후보 풀 | 로컬 PDF | API 원문 | 부족 | DB 판정 | PDF 판정 | 사유 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in report["regions"]:
        lines.append(
            f"| {row['region']} | {row['target_documents']} | {row['db_documents']} | {row['db_relevant_documents']} | {row['candidate_pool']} | {row['local_pdf_materialized']} | {row['raw_api_pool']} | {row['shortfall']} | {row['status']} | {row['pdf_status']} | {row['reason']} |"
        )
    lines.extend(
        [
            "",
            "## 합계",
            "",
            f"- 완료 지역: {report['summary']['complete']} / {report['summary']['regions']}",
            f"- 수집 공백: {report['summary']['collection_gap']}개 지역",
            f"- 현재 모수 부족: {report['summary']['insufficient_pool']}개 지역",
            f"- 출처 식별 불가: {report['summary']['no_source']}개 지역",
            f"- PDF까지 목표 충족: {report['summary']['complete_pdf']}개 지역",
            f"- PDF 수집 공백: {report['summary']['pdf_collection_gap']}개 지역",
            f"- PDF 후보 부족: {report['summary']['pdf_pool_shortage']}개 지역",
            f"- PDF 출처 식별 불가: {report['summary']['pdf_no_source']}개 지역",
            f"- DB 문서 합계: {report['summary']['db_documents']}건",
            f"- 지역별 DB 문서 부족 합계: {report['summary']['db_shortfall_total']}건",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--source-db", type=Path, default=DB_SNAPSHOT_DIR / "lucera_initial.sqlite3")
    parser.add_argument("--data-root", type=Path, default=MINUTES_DIR)
    parser.add_argument("--target", type=int, default=10)
    parser.add_argument("--time-budget-seconds", type=int, default=480)
    parser.add_argument("--phase", default="audit")
    parser.add_argument("--api-status", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = build_report(
        db_path=args.db,
        source_db_path=args.source_db,
        data_root=args.data_root,
        target=args.target,
        time_budget_seconds=args.time_budget_seconds,
        phase=args.phase,
        api_status=args.api_status,
    )
    output_json = args.output or MINUTES_REPORT_DIR / f"regional_coverage_{args.phase}.json"
    output_md = output_json.with_suffix(".md")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(output_json), "markdown": str(output_md), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
