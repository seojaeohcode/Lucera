from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.db import stable_id
from lucera.paths import API_JSON_DIR, DATABASE_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--api-raw-root",
        type=Path,
        default=API_JSON_DIR,
        help="API 상세 JSON을 보존할 디렉터리",
    )
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT d.document_id, d.source_record_key, d.source_url, d.raw_payload_json,
               d.metadata_json AS document_metadata,
               m.metadata_json AS meeting_metadata,
               m.administrative_region_code AS region_code
          FROM source_document d
          JOIN source_system ss ON ss.source_system_id=d.source_system_id
          JOIN meeting m ON m.document_id=d.document_id
         WHERE ss.code='clik_minutes'
        """
    ).fetchall()
    inserted = 0
    updated_meetings = 0
    updated_documents = 0
    updated_artifacts = 0
    for row in rows:
        payload = json.loads(row["raw_payload_json"] or "{}")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        region_code = str(row["region_code"] or "unassigned")
        raw_path = (args.api_raw_root / f"region_{region_code}" / f"{row['source_record_key']}.json").resolve()
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists() or raw_path.read_bytes() != encoded:
            raw_path.write_bytes(encoded)
        artifact_id = stable_id("artifact", row["document_id"], "official_source", digest)
        existing_artifact = conn.execute(
            """SELECT artifact_id, acquisition_method
                 FROM document_artifact
                WHERE document_id=?
                  AND artifact_role='official_source'
                  AND mime_type='application/json'
                ORDER BY CASE acquisition_method
                           WHEN 'api_local_archive' THEN 0
                           WHEN 'api_detail_response' THEN 1
                           ELSE 2
                         END, artifact_id
                LIMIT 1""",
            (row["document_id"],),
        ).fetchone()
        # Preserve the acquisition provenance of the local archive enrichment.
        # A document fetched directly from CLiK is api_detail_response; a row
        # Imported archive documents remain api_local_archive.
        acquisition_method = (
            str(existing_artifact[1])
            if existing_artifact and existing_artifact[1]
            else "api_detail_response"
        )
        artifact_metadata = json.dumps(
            {
                "materialized_file": True,
                "materialized_path": str(raw_path),
                "payload_location": "source_document.raw_payload_json",
                "embedded_content_mime_type": "text/html",
                "note": "공개 API 상세 원문 artifact이며 PDF artifact가 아님",
            },
            ensure_ascii=False,
        )
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO document_artifact
              (artifact_id, document_id, artifact_role, storage_uri, source_url,
               mime_type, file_name, sha256, file_size_bytes, acquisition_method,
               parser_name, parser_version, metadata_json)
            VALUES (?, ?, 'official_source', NULL, ?, 'application/json', ?, ?, ?,
                    ?, 'clik-api', '1.0', ?)
            """,
            (
                artifact_id,
                row["document_id"],
                row["source_url"],
                f"{row['source_record_key']}.json",
                digest,
                len(encoded),
                acquisition_method,
                artifact_metadata,
            ),
        )
        inserted += int(conn.total_changes > before)
        # The old ten-row enrichment had the correct artifact row but the
        # wrong/ambiguous method in the surrounding document metadata.  Make
        # this repair idempotent and keep all three provenance layers aligned.
        conn.execute(
            """UPDATE document_artifact
                  SET storage_uri=?, source_url=?, mime_type='application/json',
                      file_name=?, sha256=?, file_size_bytes=?,
                      acquisition_method=?, parser_name='clik-api',
                      parser_version='1.0', metadata_json=?
                WHERE document_id=?
                  AND artifact_role='official_source'
                  AND mime_type='application/json'""",
            (
                str(raw_path), row["source_url"], f"{row['source_record_key']}.json",
                digest, len(encoded), acquisition_method, artifact_metadata, row["document_id"],
            ),
        )
        updated_artifacts += conn.total_changes > before
        try:
            document_metadata = json.loads(row["document_metadata"] or "{}")
        except (TypeError, ValueError):
            document_metadata = {}
        document_metadata.update(
            {
                "provider": "국회도서관 지방의정포털",
                "docid": row["source_record_key"],
                "retrieval_mode": "detail_api",
                "acquisition_method": acquisition_method,
                "pdf_materialized": False,
                "embedded_content_mime_type": "text/html",
            }
        )
        if acquisition_method == "api_local_archive":
            document_metadata.setdefault("enrichment_method", "local_api_raw_pool")
        conn.execute(
            """UPDATE source_document
                  SET storage_uri=?, mime_type='application/json', sha256=?,
                      file_size_bytes=?, metadata_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE document_id=?""",
            (
                str(raw_path), digest, len(encoded),
                json.dumps(document_metadata, ensure_ascii=False), row["document_id"],
            ),
        )
        updated_documents += 1
        try:
            meeting_metadata = json.loads(row["meeting_metadata"] or "{}")
        except (TypeError, ValueError):
            meeting_metadata = {}
        meeting_metadata.update({"acquisition_method": acquisition_method, "pdf_materialized": False})
        if acquisition_method == "api_local_archive":
            meeting_metadata.setdefault("enrichment_method", "local_api_raw_pool")
        conn.execute(
            "UPDATE meeting SET metadata_json=? WHERE document_id=?",
            (json.dumps(meeting_metadata, ensure_ascii=False), row["document_id"]),
        )
        updated_meetings += 1
    conn.commit()
    print(json.dumps({
        "api_documents": len(rows),
        "artifacts_inserted": inserted,
        "artifacts_updated": updated_artifacts,
        "documents_updated": updated_documents,
        "meetings_updated": updated_meetings,
    }, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
