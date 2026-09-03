from __future__ import annotations

import json
import argparse
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.regions import region_catalog
from lucera.paths import (
    DATABASE_PATH,
    HWP_DIR,
    HWP_PDF_DIR,
    HTML_DIR,
    HTML_PDF_DIR,
    MINUTES_DIR,
    MINUTES_REPORT_DIR,
)

DATA_ROOT = MINUTES_DIR
DB_PATH = DATABASE_PATH
REPORT_JSON = MINUTES_REPORT_DIR / "browser_db_validation.json"
REPORT_MD = MINUTES_REPORT_DIR / "브라우저_수집_및_새DB_검증보고서.md"


def file_count(root: Path, pattern: str, region_code: str | None = None) -> int:
    path = root / (f"region_{region_code}" if region_code else "")
    return len(list(path.glob(pattern))) if path.exists() else 0


def raw_count(region_code: str) -> int:
    root = HWP_DIR / f"region_{region_code}"
    return len(list(root.glob("*.hwp"))) + len(list(root.glob("*.hwpx"))) if root.exists() else 0


def converted_hwp_count(region_code: str) -> int:
    root = HWP_PDF_DIR / f"region_{region_code}"
    total = 0
    for path in root.glob("*.pdf") if root.exists() else []:
        if any((HWP_DIR / f"region_{region_code}" / f"{path.stem}{suffix}").exists() for suffix in (".hwp", ".hwpx")):
            total += 1
    return total


def db_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "source_document", "document_artifact", "meeting", "document_page",
        "meeting_segment", "sentences", "keyword_mentions", "episodes",
        "conflict_case", "case_review", "episode_evidence", "case_evidence", "case_segment",
        "canonical_place", "case_location_candidate", "case_link_candidate",
    )
    return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def region_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for region in region_catalog():
        code = region["region_code"]
        db_docs = int(conn.execute(
            "SELECT COUNT(*) FROM meeting WHERE administrative_region_code = ?", (code,)
        ).fetchone()[0])
        db_cases = int(conn.execute(
            """SELECT COUNT(DISTINCT ce.case_id)
                 FROM case_evidence ce
                 JOIN episodes e ON e.episode_id=ce.episode_id
                 JOIN meeting m ON m.document_id=e.document_id
                WHERE m.administrative_region_code=?""", (code,)
        ).fetchone()[0])
        html = file_count(HTML_DIR, "*.html", code)
        html_pdf = file_count(HTML_PDF_DIR, "*.pdf", code)
        hwp = raw_count(code)
        hwp_pdf = converted_hwp_count(code)
        rows.append({
            "region_code": code,
            "name": region["name"],
            "province": region["province"],
            "region_group": region["region_group"],
            "available": bool(region.get("assembly_id")),
            "raw_hwp_or_hwpx": hwp,
            "converted_hwp_pdf": hwp_pdf,
            "html_snapshot": html,
            "html_rendered_pdf": html_pdf,
            "db_documents": db_docs,
            "db_cases_with_evidence": db_cases,
        })
    return rows


def artifact_checks(conn: sqlite3.Connection) -> dict[str, object]:
    role_counts = {
        row[0]: int(row[1])
        for row in conn.execute("SELECT artifact_role, COUNT(*) FROM document_artifact GROUP BY artifact_role")
    }
    source_methods = {
        row[0]: int(row[1])
        for row in conn.execute("SELECT json_extract(metadata_json, '$.acquisition_method'), COUNT(*) FROM meeting GROUP BY 1")
    }
    broken_parent_count = int(conn.execute(
        """SELECT COUNT(*) FROM document_artifact a
             WHERE a.derived_from_artifact_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM document_artifact p WHERE p.artifact_id=a.derived_from_artifact_id)"""
    ).fetchone()[0])
    cross_document_parent_count = int(conn.execute(
        """SELECT COUNT(*) FROM document_artifact a
             JOIN document_artifact p ON p.artifact_id=a.derived_from_artifact_id
            WHERE a.document_id <> p.document_id"""
    ).fetchone()[0])
    official_pdf_count = 0
    for (url,) in conn.execute("SELECT source_url FROM document_artifact WHERE artifact_role='official_source'"):
        if url and re.search(r"\.pdf(?:$|[?#])", str(url), re.I):
            official_pdf_count += 1
    direct_official_pdf_count = int(conn.execute(
        """SELECT COUNT(*) FROM document_artifact
           WHERE artifact_role='original_download'
             AND acquisition_method='browser_official_pdf'"""
    ).fetchone()[0])
    direct_official_pdf_documents = int(conn.execute(
        """SELECT COUNT(DISTINCT document_id) FROM document_artifact
           WHERE artifact_role='original_download'
             AND acquisition_method='browser_official_pdf'"""
    ).fetchone()[0])
    docs_without_artifact = int(conn.execute(
        """SELECT COUNT(*) FROM source_document d
             WHERE NOT EXISTS (SELECT 1 FROM document_artifact a WHERE a.document_id=d.document_id)"""
    ).fetchone()[0])
    storage_missing_paths = 0
    for row in conn.execute(
        "SELECT artifact_id, storage_uri FROM document_artifact WHERE storage_uri IS NOT NULL AND trim(storage_uri)<>''"
    ):
        uri = str(row[1])
        if uri.startswith(("http://", "https://")):
            continue
        if not Path(uri).exists():
            storage_missing_paths += 1
    api_docs_without_artifact = int(conn.execute(
        """SELECT COUNT(*)
             FROM source_document d
             JOIN source_system ss ON ss.source_system_id=d.source_system_id
            WHERE ss.code='clik_minutes'
              AND NOT EXISTS (
                    SELECT 1 FROM document_artifact a
                     WHERE a.document_id=d.document_id
                       AND a.artifact_role='official_source'
                       AND a.mime_type='application/json'
                       AND a.acquisition_method IN ('api_detail_response', 'api_local_archive')
              )"""
    ).fetchone()[0])
    api_artifacts_without_file = int(conn.execute(
        """SELECT COUNT(*)
             FROM source_document d
             JOIN source_system ss ON ss.source_system_id=d.source_system_id
             JOIN document_artifact a ON a.document_id=d.document_id
                                      AND a.artifact_role='official_source'
                                      AND a.mime_type='application/json'
            WHERE ss.code='clik_minutes'
              AND (a.storage_uri IS NULL OR trim(a.storage_uri)='')"""
    ).fetchone()[0])
    api_source_artifact_uri_mismatches = int(conn.execute(
        """SELECT COUNT(*)
             FROM source_document d
             JOIN source_system ss ON ss.source_system_id=d.source_system_id
             JOIN document_artifact a ON a.document_id=d.document_id
                                      AND a.artifact_role='official_source'
                                      AND a.mime_type='application/json'
            WHERE ss.code='clik_minutes'
              AND COALESCE(d.storage_uri, '') <> COALESCE(a.storage_uri, '')"""
    ).fetchone()[0])
    api_metadata_mismatches = int(conn.execute(
        """SELECT COUNT(*)
             FROM source_document d
             JOIN source_system ss ON ss.source_system_id=d.source_system_id
             JOIN meeting m ON m.document_id=d.document_id
            WHERE ss.code='clik_minutes'
              AND (
                   COALESCE(json_extract(d.metadata_json, '$.acquisition_method'), '')
                   <> COALESCE(json_extract(m.metadata_json, '$.acquisition_method'), '')
                   OR COALESCE(d.mime_type, '') <> 'application/json'
              )"""
    ).fetchone()[0])
    browser_docs_without_pdf = int(conn.execute(
        """SELECT COUNT(*)
             FROM source_document d
             JOIN source_system ss ON ss.source_system_id=d.source_system_id
            WHERE ss.code='browser_minutes'
              AND NOT EXISTS (
                    SELECT 1 FROM document_artifact a
                     WHERE a.document_id=d.document_id
                       AND a.artifact_role IN ('original_download', 'rendered_pdf')
                       AND lower(COALESCE(a.mime_type, ''))='application/pdf'
              )"""
    ).fetchone()[0])
    return {
        "artifact_role_counts": role_counts,
        "meeting_acquisition_methods": source_methods,
        "broken_derived_artifact_links": broken_parent_count,
        "cross_document_derived_artifact_links": cross_document_parent_count,
        "documents_without_artifact": docs_without_artifact,
        "artifact_storage_missing_paths": storage_missing_paths,
        "api_documents_without_json_artifact": api_docs_without_artifact,
        "api_artifacts_without_materialized_file": api_artifacts_without_file,
        "api_source_artifact_uri_mismatches": api_source_artifact_uri_mismatches,
        "api_metadata_mismatches": api_metadata_mismatches,
        "browser_documents_without_pdf_artifact": browser_docs_without_pdf,
        "official_pdf_url_artifacts": official_pdf_count,
        "direct_official_pdf_artifacts": direct_official_pdf_count,
        "direct_official_pdf_documents": direct_official_pdf_documents,
    }


def hierarchy_checks(conn: sqlite3.Connection) -> dict[str, object]:
    checks = {
        "foreign_key_violations": "SELECT COUNT(*) FROM pragma_foreign_key_check",
        "integrity_check_failures": "SELECT CASE WHEN (SELECT * FROM pragma_integrity_check)='ok' THEN 0 ELSE 1 END",
        "documents_without_meeting": """SELECT COUNT(*) FROM source_document d WHERE NOT EXISTS (SELECT 1 FROM meeting m WHERE m.document_id=d.document_id)""",
        "meetings_without_pages": """SELECT COUNT(*) FROM meeting m WHERE NOT EXISTS (SELECT 1 FROM document_page p WHERE p.document_id=m.document_id)""",
        "meetings_without_segments": """SELECT COUNT(*) FROM meeting m WHERE NOT EXISTS (SELECT 1 FROM meeting_segment s WHERE s.document_id=m.document_id)""",
        "episodes_without_evidence": """SELECT COUNT(*) FROM episodes e WHERE NOT EXISTS (SELECT 1 FROM episode_evidence x WHERE x.episode_id=e.episode_id)""",
        "cases_without_evidence": """SELECT COUNT(*) FROM conflict_case c WHERE NOT EXISTS (SELECT 1 FROM case_evidence x WHERE x.case_id=c.case_id)""",
        "segments_without_sentences": """SELECT COUNT(*) FROM meeting_segment s WHERE NOT EXISTS (SELECT 1 FROM sentences x WHERE x.paragraph_id=s.segment_id)""",
        "document_case_pairs_without_paragraphs": """SELECT COUNT(*) FROM document_cases WHERE paragraph_count < 1""",
        "cases_with_less_than_two_paragraphs": """SELECT COUNT(*) FROM conflict_case c WHERE (SELECT COUNT(*) FROM case_paragraphs cp WHERE cp.case_id=c.case_id) < 2""",
        "document_case_paragraph_rows_without_source": """SELECT COUNT(*) FROM case_paragraphs cp WHERE NOT EXISTS (SELECT 1 FROM source_document d WHERE d.document_id=cp.document_id)""",
        "cases_without_review": """SELECT COUNT(*) FROM conflict_case c WHERE NOT EXISTS (SELECT 1 FROM case_review r WHERE r.case_id=c.case_id)""",
        "case_review_status_mismatch": """SELECT COUNT(*) FROM conflict_case c JOIN case_review r ON r.case_id=c.case_id
             WHERE (r.decision='eligible' AND c.review_status<>'verified')
                OR (r.decision='needs_review' AND c.review_status<>'pending')
                OR (r.decision='rejected' AND c.review_status<>'rejected')""",
        "shared_trigger_paragraphs": """SELECT COUNT(*) FROM (SELECT paragraph_id FROM case_evidence WHERE evidence_role='trigger_sentence' GROUP BY paragraph_id HAVING COUNT(DISTINCT case_id)>1)""",
        "suffix_false_positive_case_keys": """SELECT COUNT(*) FROM conflict_case WHERE case_key LIKE '%place:보면%' OR case_key LIKE '%place:하면%' OR case_key LIKE '%place:따르면%' OR case_key LIKE '%place:자면%'""",
        "episode_evidence_document_mismatches": """SELECT COUNT(*) FROM episode_evidence ee JOIN episodes e ON e.episode_id=ee.episode_id JOIN meeting_segment s ON s.segment_id=ee.paragraph_id WHERE e.document_id<>s.document_id""",
        "episode_evidence_sentence_parent_mismatches": """SELECT COUNT(*) FROM episode_evidence ee WHERE ee.sentence_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sentences s WHERE s.sentence_id=ee.sentence_id AND s.paragraph_id=ee.paragraph_id)""",
        "case_evidence_document_mismatches": """SELECT COUNT(*) FROM case_evidence ce JOIN episodes e ON e.episode_id=ce.episode_id JOIN meeting_segment s ON s.segment_id=ce.paragraph_id WHERE e.document_id<>s.document_id""",
        "case_evidence_sentence_parent_mismatches": """SELECT COUNT(*) FROM case_evidence ce WHERE ce.sentence_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sentences s WHERE s.sentence_id=ce.sentence_id AND s.paragraph_id=ce.paragraph_id)""",
        "case_evidence_without_case_segment": """SELECT COUNT(*) FROM case_evidence ce WHERE NOT EXISTS (SELECT 1 FROM case_segment cs WHERE cs.case_id=ce.case_id AND cs.segment_id=ce.paragraph_id)""",
    }
    result = {name: int(conn.execute(sql).fetchone()[0]) for name, sql in checks.items()}
    result["document_case_pairs"] = int(conn.execute("SELECT COUNT(*) FROM document_cases").fetchone()[0])
    result["document_case_paragraph_rows"] = int(conn.execute("SELECT COUNT(*) FROM case_paragraphs").fetchone()[0])
    result["review_decisions"] = {
        str(row[0]): int(row[1])
        for row in conn.execute("SELECT decision, COUNT(*) FROM case_review GROUP BY decision")
    }
    return result


def markdown(payload: dict[str, object]) -> str:
    lines = [
        "# 브라우저 수집 및 새 DB 검증 보고서",
        "",
        f"생성 시각: `{payload['generated_at']}`",
        "",
        "## 범위",
        "",
        "광주 5개 자치구와 전남 5개 자치시·17개 자치군, 총 27개 검색 단위를 대상으로 브라우저에서 확보한 HWP/HWPX 원문과 HTML 회의록 화면을 별도 보존하고 SQLite DB에 적재했다. 여기에 공식 홈페이지에서 직접 내려받은 PDF 10건을 별도 원문으로 추가했다.",
        "",
        "이번 DB는 문서 → 회의 → 페이지 → 문단/발언블록 → 문장 → 키워드 발생 → 회의 내 episode → 동일 민원 case의 연결을 유지한다. 자동 위치 결과는 확정 좌표가 아니라 `case_location_candidate` 순위 후보로 저장한다.",
        "제품 조회용으로 `document_cases`(문서별 민원 목록)와 `case_paragraphs`(민원별 문단 목록) view를 제공한다. 따라서 한 문서에서 여러 민원을 조회하고, 각 민원에 속한 여러 문단을 원문 순서대로 바로 펼칠 수 있다.",
        "",
        "## 적재 요약",
        "",
        f"- DB: `{payload['db_path']}`",
        f"- 실제 적재 문서: **{payload['counts']['source_document']}건**",
        f"- 원문·변환·분석 artifact: **{payload['counts']['document_artifact']}건**",
        f"- 문서 페이지: **{payload['counts']['document_page']}건**, 문단/발언블록: **{payload['counts']['meeting_segment']}건**",
        f"- 문장: **{payload['counts']['sentences']}건**, 키워드 발생: **{payload['counts']['keyword_mentions']}건**",
        f"- episode: **{payload['counts']['episodes']}건**, case: **{payload['counts']['conflict_case']}건**",
        f"- case 검토: **{json.dumps(payload['hierarchy_checks'].get('review_decisions', {}), ensure_ascii=False)}**",
        "",
        "## 지역별 상태",
        "",
        "| 구분 | 지역 | HWP 원문 | HWP→PDF | HTML snapshot | HTML→PDF | DB 문서 | 근거 case |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["regions"]:
        lines.append(f"| {row['region_group']} | {row['name']} ({row['region_code']}) | {row['raw_hwp_or_hwpx']} | {row['converted_hwp_pdf']} | {row['html_snapshot']} | {row['html_rendered_pdf']} | {row['db_documents']} | {row['db_cases_with_evidence']} |")
    empty = [row["name"] for row in payload["regions"] if not row["db_documents"]]
    lines += [
        "",
        f"DB 문서가 없는 지역: **{', '.join(empty) if empty else '없음'}**.",
        "이는 해당 지역에 회의록이 존재하지 않는다는 뜻이 아니라, 이번 브라우저 수집 후보와 저장 성공 범위에 없다는 뜻이다. 색인 부재 지역을 다른 지역 문서로 채우지 않았다.",
        "",
        "## 출처와 변환 사슬",
        "",
        "`document_artifact`는 파일을 덮어쓰지 않고 다음 관계를 보존한다.",
        "",
        "```text",
        "browser download (HWP/HWPX) ──> Hancom PDF ──> OpenDataLoader JSON ──> extracted text",
        "browser DOM snapshot (HTML) ──> compact rendered PDF ──> OpenDataLoader JSON ──> extracted text",
        "official PDF download ──> OpenDataLoader JSON ──> extracted text",
        "```",
        "",
        f"artifact 역할별 건수: `{json.dumps(payload['artifact_checks']['artifact_role_counts'], ensure_ascii=False)}`",
        f"문서별 수집 방식: `{json.dumps(payload['artifact_checks']['meeting_acquisition_methods'], ensure_ascii=False)}`",
        f"공식 사이트 직접 PDF 원본 artifact: **{payload['artifact_checks']['direct_official_pdf_artifacts']}건** / 대상 문서 **{payload['artifact_checks']['direct_official_pdf_documents']}건**. 기존 HTML/HWP 변환 결과는 삭제하지 않고 provenance로 유지했다.",
        f"기존 `official_source` URL이 PDF 확장자로 확인된 artifact: **{payload['artifact_checks']['official_pdf_url_artifacts']}건**.",
        "",
        "## 무결성 검사",
        "",
    ]
    for name, value in payload["hierarchy_checks"].items():
        lines.append(f"- `{name}`: **{value}**")
    lines += [
        "",
        "0이 아닌 항목은 원문 누락 또는 파서 결과가 없는 문서를 의미하므로 운영 반영 전에 검수 대상으로 남긴다.",
        "",
        "## 위치 추정 원칙",
        "",
        "주소·지번·건물명·마을·리·읍면을 원문에서 분리해 `canonical_place`와 `place_mention`에 저장하고, case에는 모든 후보를 `case_location_candidate`로 연결한다. 지번/도로명/건물 좌표가 실제로 확보된 경우에만 거리 계산이 가능하며, 행정구역 중심점을 민원 발생지 좌표로 임의 사용하지 않는다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=REPORT_MD)
    args = parser.parse_args()
    db_path = args.db if args.db.is_absolute() else ROOT / args.db
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    counts = db_counts(conn)
    payload: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "counts": counts,
        "regions": region_rows(conn),
        "artifact_checks": artifact_checks(conn),
        "hierarchy_checks": hierarchy_checks(conn),
    }
    conn.close()
    report_json = output_path.with_suffix(".json")
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
