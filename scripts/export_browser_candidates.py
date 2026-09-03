from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import DATABASE_PATH, MINUTES_MANIFEST_DIR


DB_PATH = DATABASE_PATH
OUT_PATH = MINUTES_MANIFEST_DIR / "browser_candidates.json"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    regions = conn.execute(
        """
        SELECT region_code, region_name, region_group, region_type
        FROM administrative_region
        WHERE json_extract(metadata_json, '$.is_collection_target') = 1
        ORDER BY region_code
        """
    ).fetchall()
    output: dict[str, dict[str, object]] = {}
    for region in regions:
        rows = conn.execute(
            """
            SELECT d.source_record_key AS docid, d.title, d.source_url,
                   d.original_file_url, m.meeting_date,
                   COUNT(DISTINCT e.episode_id) AS episode_count,
                   MAX(e.confidence) AS confidence
            FROM source_document d
            JOIN meeting m ON m.document_id = d.document_id
            LEFT JOIN episodes e ON e.document_id = d.document_id
            WHERE m.administrative_region_code = ?
            GROUP BY d.document_id
            HAVING COUNT(DISTINCT e.episode_id) > 0
            ORDER BY
              CASE WHEN COUNT(DISTINCT e.episode_id) > 0 THEN 0 ELSE 1 END,
              MAX(e.confidence) DESC,
              COUNT(DISTINCT e.episode_id) DESC,
              m.meeting_date DESC
            """,
            (region["region_code"],),
        ).fetchall()
        output[region["region_code"]] = {
            "region_name": region["region_name"],
            "region_group": region["region_group"],
            "region_type": region["region_type"],
            "items": [dict(row) for row in rows],
        }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"path": str(OUT_PATH), "regions": len(output), "items": sum(len(x["items"]) for x in output.values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
