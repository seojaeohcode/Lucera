from __future__ import annotations

import json
import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config

from .db import LuceraDB
from .extract import extract_places, parse_minutes_html, redact_sensitive
from .keywords import classify_segment
from .location import normalize_address
from .paths import API_JSON_DIR
from .regions import UNIFIED_PROVINCE_NAME, province_for_city


def _first_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^0-9]", "", str(value))
    if len(compact) == 8:
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return None


def _assembly_region(assembly_name: str | None) -> tuple[str | None, str | None]:
    value = re.sub(r"(의회|청)$", "", assembly_name or "")
    location = normalize_address(value)
    province = location.province
    city = location.city_county
    if province and city == province:
        city = None
    if province == UNIFIED_PROVINCE_NAME:
        province = province_for_city(city, "전라남도")
    elif city:
        province = province_for_city(city, province)
    return province, city


class ClikMinutesClient:
    """Client for the National Assembly Library local-council minutes API.

    API limits from the official guide are enforced by the caller: listCount is
    capped at 100 and the CLI defaults to a small detail fetch count.
    """

    def __init__(self, api_key: str | None = None, endpoint: str = config.CLIK_MINUTES_ENDPOINT):
        self.api_key = api_key or config.PUBLIC_DATA_KEYS["clik"]
        self.endpoint = endpoint

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {"key": self.api_key, "type": "json", **params}
        request = Request(
            self.endpoint + "?" + urlencode(query),
            headers={"User-Agent": "Lucera/0.1"},
        )
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8"))
        result = _first_payload(payload)
        if result.get("RESULT_CODE") != "SUCCESS":
            raise RuntimeError(f"CLiK API error: {result.get('RESULT_CODE')} {result.get('RESULT_MESSAGE')}")
        return result

    def list_minutes(
        self,
        keyword: str,
        start_count: int = 0,
        list_count: int = 20,
        search_type: str = "ALL",
        assembly_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "displayType": "list",
            "startCount": max(0, int(start_count)),
            "listCount": min(max(1, int(list_count)), 100),
            "searchType": search_type,
            "searchKeyword": keyword,
            "sort": "MTG_DE/DESC",
        }
        if assembly_id:
            params["rasmblyId"] = assembly_id
        result = self._get(params)
        result["LIST"] = [item.get("ROW", item) for item in result.get("LIST", [])]
        return result

    def detail(self, document_id: str) -> dict[str, Any]:
        return _first_payload(self._get({"displayType": "detail", "docid": document_id}))


def make_clik_bundle(detail: dict[str, Any]) -> dict[str, Any]:
    docid = detail.get("DOCID")
    if not docid:
        raise ValueError("CLiK detail has no DOCID")
    assembly_name = detail.get("RASMBLY_NM") or ""
    province, city_county = _assembly_region(assembly_name)
    meeting_date = _iso_date(detail.get("MTG_DE"))
    meeting_title = detail.get("MTGNM") or "지방의회 회의록"
    title = f"{assembly_name} {meeting_date or ''} {meeting_title}".strip()
    raw_html = detail.get("MINTS_HTML") or ""
    full_text, raw_segments = parse_minutes_html(raw_html)
    agenda_text = detail.get("MTR_SJ") or ""
    context = " ".join(value for value in (province, city_county) if value)
    classification_context = f"{meeting_title} {agenda_text}".strip()
    segments = []
    for segment in raw_segments:
        text = segment["text_original"]
        # Keep API-ingested minutes on the same sentence-grounded path as
        # browser PDF/HWP/HTML material.  The stored unit remains a paragraph,
        # while issue evidence is selected from sentences inside it.
        classification = classify_segment(text, classification_context)
        issues = classification["issues"]
        places = extract_places(text, context)
        segment["issues"] = issues
        segment["places"] = places
        segment["relevant"] = bool(classification["relevant"])
        segment["metadata"] = {
            **segment.get("metadata", {}),
            "keyword_classifier": {
                "version": "precision-v2",
                "solar_related": classification["solar_related"],
                "solar_anchor_hits": classification["solar_anchor_hits"],
                "standalone_high_precision_hits": classification["standalone_high_precision_hits"],
                "matched_issue_terms": classification["matched_issue_terms"],
                "admin_support_hits": classification["admin_support_hits"],
                "problem_categories": classification["problem_categories"],
            },
        }
        segments.append(segment)
    return {
        "source": {
            "system_code": "clik_minutes",
            "source_record_key": docid,
            "title": title,
            "document_type": "meeting_minutes",
            "source_url": config.CLIK_MINUTES_ENDPOINT,
            "original_file_url": detail.get("ORGINL_FILE_URL") or None,
            # The transport artifact is the JSON detail response.  The
            # response embeds the actual minutes as MINTS_HTML, which is
            # recorded separately in metadata and parsed into pages/segments.
            "mime_type": "application/json",
            "access_policy": "public",
            "raw_payload": detail,
            "metadata": {
                "provider": "국회도서관 지방의정포털",
                "docid": docid,
                "retrieval_mode": "detail_api",
                "acquisition_method": "api_detail_response",
                "pdf_materialized": False,
                "embedded_content_mime_type": "text/html",
            },
        },
        "meeting": {
            "council_level": "local_council",
            "assembly_id": detail.get("RASMBLY_ID"),
            "assembly_name": assembly_name,
            "province": province,
            "city_county": city_county,
            "session_number": detail.get("RASMBLY_SESN"),
            "assembly_number": detail.get("RASMBLY_NUMPR"),
            "meeting_order": detail.get("MINTS_ODR"),
            "meeting_type": meeting_title,
            "meeting_title": title,
            "meeting_date": meeting_date,
            "agenda_text": agenda_text,
            "metadata": {
                "docid": docid,
                "acquisition_method": "api_detail_response",
                "pdf_materialized": False,
            },
        },
        "page": {
            "text_original": full_text,
            "text_redacted": redact_sensitive(full_text),
            "parser_name": "clik-html",
            "parser_version": "1.0",
        },
        "artifacts": [
            {
                # The artifact is the public API detail response.  It is not a
                # local PDF; the complete payload remains in raw_payload_json.
                "artifact_role": "official_source",
                "storage_uri": None,
                "source_url": config.CLIK_MINUTES_ENDPOINT,
                "mime_type": "application/json",
                "file_name": f"{docid}.json",
                "sha256": hashlib.sha256(
                    json.dumps(detail, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "file_size_bytes": len(
                    json.dumps(detail, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ),
                "acquisition_method": "api_detail_response",
                "parser_name": "clik-api",
                "parser_version": "1.0",
                "metadata": {
                    "materialized_file": False,
                    "payload_location": "source_document.raw_payload_json",
                    "note": "공개 API 상세 원문 artifact이며 PDF artifact가 아님",
                },
            }
        ],
        "segments": segments,
    }


def materialize_clik_bundle(
    bundle: dict[str, Any],
    storage_root: str | Path,
    *,
    region_code: str | None = None,
) -> str | None:
    """Persist the exact API detail response and attach it to the bundle.

    ``source_document.raw_payload_json`` is the canonical DB copy, while this
    file is the reproducible on-disk input used by later re-analysis.  The
    compact, sorted encoding is intentional: its SHA-256 is identical to the
    API artifact checksum, so a file cannot silently diverge from the DB copy.
    """
    source = bundle.get("source") or {}
    payload = source.get("raw_payload")
    docid = source.get("source_record_key")
    if not isinstance(payload, dict) or not docid:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    safe_region = str(region_code or "unassigned").strip() or "unassigned"
    path = Path(storage_root) / f"region_{safe_region}" / f"{docid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != encoded:
        path.write_bytes(encoded)
    path_text = str(path.resolve())
    source.update(
        {
            "storage_uri": path_text,
            "mime_type": "application/json",
            "sha256": digest,
            "file_size_bytes": len(encoded),
        }
    )
    source_metadata = dict(source.get("metadata") or {})
    source_metadata.update(
        {
            "materialized_file": True,
            "materialized_path": path_text,
            "payload_location": "source_document.raw_payload_json",
            "embedded_content_mime_type": "text/html",
        }
    )
    source["metadata"] = source_metadata
    for artifact in bundle.get("artifacts", []):
        if artifact.get("artifact_role") != "official_source":
            continue
        artifact.update(
            {
                "storage_uri": path_text,
                "mime_type": "application/json",
                "sha256": digest,
                "file_size_bytes": len(encoded),
            }
        )
        metadata = dict(artifact.get("metadata") or {})
        metadata.update(
            {
                "materialized_file": True,
                "materialized_path": path_text,
                "payload_location": "source_document.raw_payload_json",
                "embedded_content_mime_type": "text/html",
            }
        )
        artifact["metadata"] = metadata
    return path_text


def ingest_clik(
    db: LuceraDB,
    keyword: str,
    list_count: int = 10,
    start_count: int = 0,
    detail_limit: int | None = None,
    assembly_id: str | None = None,
) -> dict[str, Any]:
    list_count = min(max(1, list_count), 100)
    detail_limit = min(max(1, detail_limit if detail_limit is not None else list_count), list_count)
    rows = ClikMinutesClient().list_minutes(keyword, start_count, list_count, assembly_id=assembly_id).get("LIST", [])
    job_id = str(uuid.uuid4())
    source_system_id = db._source_id("clik_minutes")
    db.conn.execute(
        """INSERT INTO ingestion_job
        (ingestion_job_id, source_system_id, job_type, requested_keyword,
         status, requested_count, started_at)
        VALUES (?, ?, 'clik_minutes', ?, 'running', ?, CURRENT_TIMESTAMP)""",
        (job_id, source_system_id, keyword, len(rows)),
    )
    db.conn.commit()
    processed = 0
    errors: list[dict[str, str]] = []
    for row in rows[:detail_limit]:
        docid = row.get("DOCID")
        if not docid:
            continue
        try:
            bundle = make_clik_bundle(ClikMinutesClient().detail(docid))
            materialize_clik_bundle(
                bundle,
                API_JSON_DIR,
                region_code=assembly_id,
            )
            db.insert_document_bundle(bundle)
            processed += 1
        except Exception as exc:
            errors.append({"docid": docid, "error": str(exc)[:300]})
    db.conn.execute(
        """UPDATE ingestion_job SET status=?, processed_count=?, error_count=?,
        error_message=?, finished_at=CURRENT_TIMESTAMP, metadata_json=?
        WHERE ingestion_job_id=?""",
        (
            "completed_with_errors" if errors else "completed",
            processed,
            len(errors),
            errors[0]["error"] if errors else None,
            json.dumps({"start_count": start_count, "list_count": list_count, "detail_limit": detail_limit}, ensure_ascii=False),
            job_id,
        ),
    )
    db.conn.commit()
    return {"job_id": job_id, "keyword": keyword, "listed": len(rows), "processed": processed, "errors": errors}
