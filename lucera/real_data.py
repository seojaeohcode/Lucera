"""Real Yeongam permit and meeting-record data pipeline.

The public Yeongam register contains parcel addresses but no coordinates.  We
keep that fact explicit: coordinates are derived from the address by a
geocoder, never generated from a township centroid.  Every imported row keeps
the source dataset, snapshot date, geocoder, match score and original address
in ``metadata_json`` so a reviewer can reproduce or replace the enrichment.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db import LuceraDB, stable_id
from .extract import extract_places, redact_sensitive
from .keywords import classify_segment
from .location import normalize_address
from .ordinance import seed_official_rules
from .regions import region_for_name
from .review import rebuild_case_reviews


YEONGAM_DATASET_ID = "15113476"
YEONGAM_DATASET_NAME = "전남광주통합특별시 영암군_태양광허가정보"
YEONGAM_DATASET_URL = "https://www.data.go.kr/data/15113476/fileData.do?recommendDataYn=Y"
YEONGAM_API_URL = "https://api.odcloud.kr/api/15113476/v1/uddi:2b0b3efa-0648-429e-964a-e19a2a0da53d"
GEOCODER_NAME = "ArcGIS World Geocoding Service"
GEOCODER_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
PERMIT_SOURCE_CODE = "yeongam_solar_permit"
PERMIT_SOURCE_NAME = "영암군 태양광 허가정보(공공데이터포털)"
PERMIT_SOURCE_PROVIDER = "전남광주통합특별시 영암군"
CORPUS_SOURCE_CODE = "browser_minutes"


def _text(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _number(value: Any) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        result = float(text.replace(",", ""))
    except ValueError:
        return None
    return result if result == result else None


def _date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", text)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None


def _record_key(row: dict[str, Any]) -> str:
    compact = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(compact.encode("utf-8")).hexdigest()


def load_yeongam_permits(path: str | Path) -> list[dict[str, Any]]:
    """Load the six-column official CSV and reject rows outside Yeongam."""

    path = Path(path)
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            break
        except UnicodeError as exc:
            last_error = exc
    else:
        raise ValueError(f"cannot decode permit CSV: {path}") from last_error

    required = {"시설명", "대표소재지지번주소", "가동상태명", "용량", "설치연도", "데이터기준일자"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"unexpected Yeongam permit columns: {sorted(rows[0]) if rows else []}")

    result: list[dict[str, Any]] = []
    for row in rows:
        address = _text(row.get("대표소재지지번주소"))
        parsed = normalize_address(address or "")
        if parsed.city_county != "영암군" or not address:
            continue
        result.append(
            {
                "source_record_key": _record_key(row),
                "facility_name": _text(row.get("시설명")),
                "jibun_address": address,
                "operation_status": _text(row.get("가동상태명")),
                "capacity_kw": _number(row.get("용량")),
                "install_year": _text(row.get("설치연도")),
                "snapshot_date": _date(row.get("데이터기준일자")),
                "province": parsed.province or "전라남도",
                "city_county": parsed.city_county or "영암군",
                "eup_myeon": parsed.eup_myeon,
                "ri": parsed.ri,
                "raw": dict(row),
            }
        )
    return result


def _group_coordinate_candidates(permits: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in permits:
        group = (str(item.get("eup_myeon") or "기타"), str(item.get("ri") or "리 미상"))
        grouped.setdefault(group, []).append(item)
    candidates_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for group, rows in grouped.items():
        by_address: dict[str, dict[str, Any]] = {}
        for item in rows:
            address = str(item.get("jibun_address") or item.get("source_record_key"))
            current = by_address.get(address)
            if current is None or str(item.get("install_year") or "") > str(current.get("install_year") or ""):
                by_address[address] = item
        candidates_by_group[group] = sorted(
            by_address.values(),
            key=lambda item: (
                str(item.get("install_year") or ""),
                str(item.get("facility_name") or ""),
                str(item.get("jibun_address") or ""),
                str(item.get("source_record_key") or ""),
            ),
        )
    return candidates_by_group


def _evenly_select(candidates: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    if count >= len(candidates):
        return list(candidates)
    stable_target = min(max(count, 6), len(candidates))
    indices = {
        round(index * (len(candidates) - 1) / max(1, stable_target - 1))
        for index in range(stable_target)
    }
    return [candidates[index] for index in sorted(indices)][:count]


def select_coordinate_sample(
    permits: list[dict[str, Any]], per_ri: int = 4, target_count: int | None = None
) -> list[dict[str, Any]]:
    """Select a deterministic, compact map sample per ``리`` from the full register.

    All official rows remain in the database. Only the selected rows receive
    coordinates for the demo map, preventing a 1,549-marker cloud. One row
    per parcel address is preferred because several records can share a site.
    When ``target_count`` is supplied, every ``리`` receives at least one
    coordinate and the remaining slots are allocated in proportion to the
    number of unique permit addresses in that ``리``. This keeps the map
    readable while preserving the detailed-area distribution. The legacy
    ``per_ri`` mode remains available for callers that need a fixed per-리 cap.
    """

    if target_count is not None:
        if target_count <= 0:
            return []
        candidates_by_group = _group_coordinate_candidates(permits)
        total_candidates = sum(len(rows) for rows in candidates_by_group.values())
        target = min(target_count, total_candidates)
        if target <= 0:
            return []
        allocations = {group: 1 for group in candidates_by_group if candidates_by_group[group]}
        if target < len(allocations):
            allocations = {
                group: 1
                for group in sorted(allocations)[:target]
            }
        remaining = target - sum(allocations.values())
        while remaining > 0:
            eligible = [
                group for group, rows in candidates_by_group.items()
                if group in allocations and allocations[group] < len(rows)
            ]
            if not eligible:
                break
            group = max(
                eligible,
                key=lambda item: (
                    len(candidates_by_group[item]) / (allocations[item] + 1),
                    len(candidates_by_group[item]) - allocations[item],
                    tuple(reversed(item)),
                ),
            )
            allocations[group] += 1
            remaining -= 1
        return [
            item
            for group in sorted(allocations)
            for item in _evenly_select(candidates_by_group[group], allocations[group])
        ]

    if per_ri <= 0:
        return list(permits)
    candidates_by_group = _group_coordinate_candidates(permits)
    selected: list[dict[str, Any]] = []
    for group in sorted(candidates_by_group):
        candidates = candidates_by_group[group]
        target = min(per_ri, len(candidates))
        if target:
            selected.extend(_evenly_select(candidates, target))
    return selected


def _valid_coordinate(value: Any, *, latitude: bool) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return 32 <= number <= 39 if latitude else 124 <= number <= 132


def _geocode_one(address: str, timeout: float = 30.0, retries: int = 3) -> dict[str, Any]:
    params = {
        "SingleLine": address,
        "f": "json",
        "maxLocations": 1,
        "outFields": "*",
        "forStorage": "false",
    }
    url = f"{GEOCODER_URL}?{urlencode(params)}"
    errors: list[str] = []
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "Lucera/1.0"})
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            candidates = payload.get("candidates") or []
            if not candidates:
                return {
                    "status": "not_found",
                    "provider": GEOCODER_NAME,
                    "provider_url": GEOCODER_URL,
                    "attempts": attempt,
                    "errors": errors,
                }
            candidate = candidates[0]
            location = candidate.get("location") or {}
            latitude = location.get("y")
            longitude = location.get("x")
            if not (_valid_coordinate(latitude, latitude=True) and _valid_coordinate(longitude, latitude=False)):
                return {
                    "status": "invalid_coordinate",
                    "provider": GEOCODER_NAME,
                    "provider_url": GEOCODER_URL,
                    "score": candidate.get("score"),
                    "matched_address": candidate.get("address"),
                    "attempts": attempt,
                    "errors": errors,
                }
            return {
                "status": "resolved",
                "provider": GEOCODER_NAME,
                "provider_url": GEOCODER_URL,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "score": float(candidate.get("score") or 0),
                "matched_address": candidate.get("address"),
                "attributes": candidate.get("attributes") or {},
                "attempts": attempt,
                "errors": errors,
            }
        except Exception as exc:  # noqa: BLE001 - retry and preserve the reason
            errors.append(f"{type(exc).__name__}: {str(exc)[:180]}")
            if attempt < retries:
                time.sleep(min(2.0, 0.35 * attempt))
    return {
        "status": "request_failed",
        "provider": GEOCODER_NAME,
        "provider_url": GEOCODER_URL,
        "attempts": retries,
        "errors": errors,
    }


def enrich_yeongam_permits(
    permits: list[dict[str, Any]],
    cache_path: str | Path,
    *,
    workers: int = 6,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Resolve unique parcel addresses and persist a resumable cache."""

    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    addresses = sorted({str(item["jibun_address"]) for item in permits})
    pending = [address for address in addresses if address not in cache]
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 12))) as executor:
        futures = {executor.submit(_geocode_one, address, timeout): address for address in pending}
        for future in as_completed(futures):
            address = futures[future]
            try:
                cache[address] = future.result()
            except Exception as exc:  # pragma: no cover - defensive future guard
                cache[address] = {"status": "worker_failed", "errors": [str(exc)[:180]], "provider": GEOCODER_NAME}
            if len(cache) % 50 == 0:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    enriched = 0
    for item in permits:
        geo = cache.get(item["jibun_address"]) or {}
        item["geocode"] = geo
        if geo.get("status") == "resolved":
            item["latitude"] = geo["latitude"]
            item["longitude"] = geo["longitude"]
            enriched += 1
        else:
            item["latitude"] = None
            item["longitude"] = None
    statuses: dict[str, int] = {}
    for address in addresses:
        status = str((cache.get(address) or {}).get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "total_rows": len(permits),
        "unique_addresses": len(addresses),
        "resolved_rows": enriched,
        "unresolved_rows": len(permits) - enriched,
        "cache_path": str(cache_path.resolve()),
        "status_counts": statuses,
    }


def ensure_real_source(db: LuceraDB) -> str:
    source_id = stable_id("source_system", PERMIT_SOURCE_CODE)
    db.conn.execute(
        """INSERT INTO source_system (source_system_id, code, name, provider, base_url, terms_url)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, provider=excluded.provider,
                                           base_url=excluded.base_url, terms_url=excluded.terms_url""",
        (source_id, PERMIT_SOURCE_CODE, PERMIT_SOURCE_NAME, PERMIT_SOURCE_PROVIDER, YEONGAM_DATASET_URL, YEONGAM_DATASET_URL),
    )
    return source_id


def import_yeongam_permits(db: LuceraDB, permits: Iterable[dict[str, Any]]) -> int:
    source_id = ensure_real_source(db)
    count = 0
    for item in permits:
        geo = item.get("geocode") or {"status": "not_sampled"}
        selected_for_map = bool(item.get("map_display"))
        metadata = {
            "data_origin": "public_dataset",
            "source_dataset_id": YEONGAM_DATASET_ID,
            "source_dataset_name": YEONGAM_DATASET_NAME,
            "source_url": YEONGAM_DATASET_URL,
            "api_endpoint": YEONGAM_API_URL,
            "snapshot_date": item.get("snapshot_date"),
            "install_year": item.get("install_year"),
            "official_fields": item.get("raw") or {},
            "coordinate": {
                "status": geo.get("status", "unresolved"),
                "provider": geo.get("provider"),
                "provider_url": geo.get("provider_url"),
                "score": geo.get("score"),
                "matched_address": geo.get("matched_address"),
                "resolution_method": "parcel_address_geocoding",
                "resolved_at": datetime.now(timezone.utc).isoformat() if geo.get("status") == "resolved" else None,
            },
            "geo_precision": "parcel_address" if geo.get("status") == "resolved" else "not_sampled_for_demo",
            "map_display": selected_for_map,
            "meeting_linking": "permit_meeting_link populated from real Yeongam minutes",
        }
        project_id = stable_id("permit_project", PERMIT_SOURCE_CODE, item["source_record_key"])
        db.conn.execute(
            """INSERT INTO permit_project
               (project_id, source_system_id, source_record_key, facility_name,
                company_name, capacity_kw, permit_date, operation_status,
                road_address, jibun_address, province, city_county, eup_myeon, ri,
                latitude, longitude, location_status, metadata_json)
               VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 facility_name=excluded.facility_name, capacity_kw=excluded.capacity_kw,
                 operation_status=excluded.operation_status, jibun_address=excluded.jibun_address,
                 province=excluded.province, city_county=excluded.city_county,
                 eup_myeon=excluded.eup_myeon, ri=excluded.ri, latitude=excluded.latitude,
                 longitude=excluded.longitude, location_status=excluded.location_status,
                 metadata_json=excluded.metadata_json""",
            (
                project_id,
                source_id,
                item["source_record_key"],
                item.get("facility_name"),
                item.get("capacity_kw"),
                item.get("operation_status"),
                item.get("jibun_address"),
                item.get("province"),
                item.get("city_county"),
                item.get("eup_myeon"),
                item.get("ri"),
                item.get("latitude"),
                item.get("longitude"),
                "geocoded" if geo.get("status") == "resolved" else "not_sampled",
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        count += 1
    db.commit()
    return count


def _meeting_bundle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    source_key = first["source_record_key"]
    title = first.get("doc_title") or first.get("meeting_title") or f"영암군의회 회의록 {source_key}"
    context = f"{first.get('province') or ''} {first.get('city_county') or ''} {title}".strip()
    segments: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item.get("ordinal") or 0)):
        text = str(row.get("text_original") or "").strip()
        if not text:
            continue
        classification = classify_segment(text, context)
        issues = []
        for issue in row.get("issues") or classification.get("issues") or []:
            issues.append({**issue, "metadata": {**(issue.get("metadata") or {}), "data_origin": "meeting_record"}})
        page = int(row.get("page_from") or 1)
        segments.append(
            {
                "segment_id": row.get("segment_id") or stable_id("real_segment", source_key, row.get("ordinal")),
                "text_original": text,
                "text_redacted": redact_sensitive(text),
                "page_from": page,
                "page_to": page,
                "segment_type": "paragraph",
                "parse_confidence": 0.92,
                "issues": issues,
                "places": extract_places(text, context),
                "relevant": bool(issues or classification.get("relevant")),
                "metadata": {
                    "data_origin": "meeting_record",
                    "source_corpus": "minutes_corpus.json",
                    "keyword_classifier": {
                        "version": "precision-v2",
                        "solar_related": classification.get("solar_related"),
                        "problem_categories": classification.get("problem_categories"),
                    },
                },
            }
        )
    max_page = max((segment["page_from"] for segment in segments), default=1)
    pages = [{"text_original": "", "text_redacted": "", "parser_name": "normalized-corpus", "parser_version": "1"} for _ in range(max_page)]
    for segment in segments:
        page = pages[segment["page_from"] - 1]
        page["text_original"] += ("\n" if page["text_original"] else "") + segment["text_original"]
        page["text_redacted"] += ("\n" if page["text_redacted"] else "") + segment["text_redacted"]
    region = region_for_name(first.get("city_county")) or {}
    return {
        "source": {
            "system_code": CORPUS_SOURCE_CODE,
            "source_record_key": source_key,
            "document_id": stable_id("document", CORPUS_SOURCE_CODE, source_key),
            "title": title,
            "document_type": "meeting_minutes",
            "source_url": first.get("source_url"),
            "mime_type": "application/json",
            "access_policy": "public",
            "raw_payload": {"corpus": "minutes_corpus.json", "rows": rows},
            "metadata": {
                "data_origin": "meeting_record",
                "provider": "국회도서관 지방의정포털",
                "source_corpus": "minutes_corpus.json",
                "source_record_key": source_key,
            },
        },
        "meeting": {
            "council_level": "local_council",
            "administrative_region_code": region.get("region_code"),
            "assembly_name": first.get("assembly_name"),
            "province": first.get("province"),
            "city_county": first.get("city_county"),
            "meeting_title": first.get("meeting_title") or title,
            "meeting_type": "지방의회 회의록",
            "meeting_date": _date(first.get("meeting_date")),
            "metadata": {"data_origin": "meeting_record", "source_corpus": "minutes_corpus.json"},
        },
        "pages": pages,
        "segments": segments,
    }


def import_yeongam_minutes(db: LuceraDB, corpus_path: str | Path) -> dict[str, int]:
    payload = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    rows = [row for row in payload if isinstance(row, dict) and row.get("city_county") == "영암군"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("source_record_key") or row.get("document_id")), []).append(row)
    for group in grouped.values():
        db.insert_document_bundle(_meeting_bundle(group))
    db.commit()
    review = rebuild_case_reviews(db)
    db.commit()
    return {"segments": len(rows), "documents": len(grouped), "review_cases": int(review.get("cases") or 0)}


def ensure_permit_meeting_table(db: LuceraDB) -> None:
    db.conn.executescript(
        """CREATE TABLE IF NOT EXISTS permit_meeting_link (
               link_id TEXT PRIMARY KEY,
               project_id TEXT NOT NULL REFERENCES permit_project(project_id) ON DELETE CASCADE,
               document_id TEXT NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
               meeting_id TEXT REFERENCES meeting(meeting_id) ON DELETE CASCADE,
               segment_id TEXT REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
               relation_type TEXT NOT NULL,
               match_score REAL NOT NULL,
               issue_codes_json TEXT NOT NULL DEFAULT '[]',
               link_reason TEXT NOT NULL,
               metadata_json TEXT NOT NULL DEFAULT '{}',
               UNIQUE(project_id, segment_id)
           );
           CREATE INDEX IF NOT EXISTS idx_permit_meeting_project ON permit_meeting_link(project_id, match_score DESC);
           CREATE INDEX IF NOT EXISTS idx_permit_meeting_segment ON permit_meeting_link(segment_id);"""
    )


def link_permits_to_minutes(db: LuceraDB, per_project: int = 12) -> int:
    ensure_permit_meeting_table(db)
    db.conn.execute("DELETE FROM permit_meeting_link")
    permits = db.conn.execute(
        "SELECT project_id, eup_myeon, ri FROM permit_project WHERE city_county='영암군'"
    ).fetchall()
    segments = db.conn.execute(
        """SELECT s.segment_id, s.document_id, s.meeting_id, s.text_original,
                  m.meeting_date, COUNT(si.segment_issue_id) AS issue_count
             FROM meeting_segment s
             JOIN meeting m ON m.meeting_id=s.meeting_id
             LEFT JOIN segment_issue si ON si.segment_id=s.segment_id
            WHERE m.city_county='영암군' AND s.relevance_status='relevant'
            GROUP BY s.segment_id
            ORDER BY m.meeting_date DESC, s.ordinal"""
    ).fetchall()
    inserted = 0
    for permit in permits:
        ranked: list[tuple[float, str, Any, list[str]]] = []
        area = str(permit["eup_myeon"] or "")
        ri = str(permit["ri"] or "")
        for segment in segments:
            text = str(segment["text_original"] or "")
            area_hit = bool(area and area in text)
            ri_hit = bool(ri and ri in text)
            solar_hit = any(term in text for term in ("태양광", "태양광발전", "발전소", "간척지"))
            if not solar_hit:
                continue
            if ri_hit:
                score, relation, reason = 1.0, "same_ri", "회의록에 동일 리가 직접 언급됨"
            elif area_hit:
                score, relation, reason = 0.86, "same_eup_myeon", "회의록에 동일 읍·면이 직접 언급됨"
            else:
                score, relation, reason = 0.62, "countywide_context", "영암군 태양광 관련 광역 회의 맥락"
            score += min(0.08, 0.02 * int(segment["issue_count"] or 0))
            ranked.append((score, relation, segment, [str(x[0]) for x in db.conn.execute("SELECT issue_code FROM segment_issue WHERE segment_id=?", (segment["segment_id"],)).fetchall()]))
        ranked.sort(key=lambda item: (-item[0], str(item[2]["meeting_date"] or ""), item[2]["segment_id"]))
        for score, relation, segment, issue_codes in ranked[: max(1, per_project)]:
            link_id = stable_id("permit_meeting_link", permit["project_id"], segment["segment_id"])
            db.conn.execute(
                """INSERT INTO permit_meeting_link
                   (link_id, project_id, document_id, meeting_id, segment_id,
                    relation_type, match_score, issue_codes_json, link_reason, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    link_id,
                    permit["project_id"],
                    segment["document_id"],
                    segment["meeting_id"],
                    segment["segment_id"],
                    relation,
                    round(score, 4),
                    json.dumps(issue_codes, ensure_ascii=False),
                    "회의록 텍스트·읍면/리·태양광 키워드 기반 연결",
                    json.dumps({"data_origin": "derived_link", "algorithm": "area_keyword_v1"}, ensure_ascii=False),
                ),
            )
            inserted += 1
    db.commit()
    return inserted


def rebuild_real_db(
    db_path: str | Path,
    schema_path: str | Path,
    permit_csv: str | Path,
    geo_cache: str | Path,
    minutes_corpus: str | Path,
    *,
    geocode_workers: int = 6,
    map_sample_per_ri: int = 4,
    map_sample_target: int | None = 180,
) -> dict[str, Any]:
    db = LuceraDB(db_path)
    try:
        db.initialize(schema_path)
        rule_count = seed_official_rules(db)
        permits = load_yeongam_permits(permit_csv)
        map_sample = select_coordinate_sample(
            permits,
            per_ri=map_sample_per_ri,
            target_count=map_sample_target,
        )
        for item in permits:
            item["map_display"] = False
            item["geocode"] = {"status": "not_sampled"}
        for item in map_sample:
            item["map_display"] = True
        geo_report = enrich_yeongam_permits(map_sample, geo_cache, workers=geocode_workers)
        permit_count = import_yeongam_permits(db, permits)
        minutes_report = import_yeongam_minutes(db, minutes_corpus)
        link_count = link_permits_to_minutes(db)
        db.commit()
        return {
            "permit_csv": str(Path(permit_csv).resolve()),
            "permit_count": permit_count,
            "map_sample_count": len(map_sample),
            "map_sample_per_ri": map_sample_per_ri,
            "map_sample_target": map_sample_target,
            "siting_rule_count": rule_count,
            "geocode": geo_report,
            "minutes": minutes_report,
            "permit_meeting_links": link_count,
            "stats": db.stats(),
        }
    finally:
        db.close()
