from __future__ import annotations

import json
import hashlib
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from .db import LuceraDB
from .ingest import ClikMinutesClient, make_clik_bundle, materialize_clik_bundle
from .paths import API_JSON_DIR, API_LISTINGS_DIR
from .regions import target_regions
from .review import rebuild_case_reviews


# These are deliberately the high-precision families.  Generic words such as
# 주민/환경/안전 are not used as standalone collection queries.
REGIONAL_QUERY_TERMS: tuple[str, ...] = (
    "태양광",
    "태양광 민원",
    "태양광 주민반대",
    "태양광 집단민원",
    "태양광 주민설명회",
    "태양광 개발행위허가",
    "태양광 이격거리",
    "태양광 빛반사",
    "태양광 경관",
    "태양광 환경영향",
    "태양광 안전",
    "태양광 보상",
    "태양광 발전수익",
    "수상태양광",
    "영농형태양광",
    "빛반사",
    "반사광",
    "눈부심",
    "염해농지",
    "햇빛연금",
)


class ApiBudgetExceeded(RuntimeError):
    pass


def _emit(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _existing_docids(db: LuceraDB, assembly_id: str) -> set[str]:
    rows = db.conn.execute(
        """SELECT d.source_record_key
             FROM source_document d
             JOIN meeting m ON m.document_id=d.document_id
            WHERE m.assembly_id=? AND d.source_record_key IS NOT NULL""",
        (assembly_id,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _region_rows(rows: Iterable[dict[str, Any]], assembly_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        docid = row.get("DOCID")
        if not docid or str(row.get("RASMBLY_ID") or assembly_id) != assembly_id or docid in seen:
            continue
        seen.add(docid)
        result.append(row)
    return result


def _materialize_listing_response(
    response: dict[str, Any],
    storage_root: Path,
    *,
    region_code: str,
    keyword: str,
    assembly_id: str,
) -> str:
    """Keep the exact list response used to choose detail documents."""
    listing_root = API_LISTINGS_DIR / f"region_{region_code}"
    listing_root.mkdir(parents=True, exist_ok=True)
    query_hash = hashlib.sha1(f"{assembly_id}|{keyword}".encode("utf-8")).hexdigest()[:16]
    path = listing_root / f"{query_hash}.json"
    payload = {
        "request": {
            "assembly_id": assembly_id,
            "region_code": region_code,
            "keyword": keyword,
            "display_type": "list",
            "list_count": 100,
        },
        "response": response,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if not path.exists() or path.read_bytes() != encoded:
        path.write_bytes(encoded)
    return str(path.resolve())


def _is_quota_error(exc: Exception) -> bool:
    """Recognize CLiK's daily/key quota failures without hiding other errors."""
    message = str(exc).upper()
    return "ERROR09" in message or "호출횟수" in message or "QUOTA" in message


def collect_regional(
    db: LuceraDB,
    *,
    target_count: int = 10,
    region_names: list[str] | None = None,
    kinds: set[str] | None = None,
    max_api_calls: int = 880,
    sleep_seconds: float = 0.05,
    detail_workers: int = 6,
    raw_storage_root: str | Path | None = None,
) -> dict[str, Any]:
    """Collect full CLiK detail documents by local council.

    Listing results are only candidates; only successful detail responses are
    inserted into the evidence DB.  Existing DOCIDs are skipped, so the run is
    resumable after a quota/network interruption.  The collector intentionally
    reports shortages for councils with no high-precision solar records.
    """
    if target_count <= 0 or target_count > 100:
        raise ValueError("target_count must be between 1 and 100")
    if max_api_calls <= 0:
        raise ValueError("max_api_calls must be positive")
    if detail_workers <= 0 or detail_workers > 12:
        raise ValueError("detail_workers must be between 1 and 12")
    requested_names = set(region_names or [])
    selected = [
        region for region in target_regions()
        if (not requested_names or region["name"] in requested_names)
        and (not kinds or region["kind"] in kinds)
    ]
    unknown_names = requested_names - {region["name"] for region in selected}
    if unknown_names:
        raise ValueError(f"unknown region: {', '.join(sorted(unknown_names))}")

    client = ClikMinutesClient()
    raw_root = Path(raw_storage_root) if raw_storage_root else API_JSON_DIR
    calls = 0
    calls_lock = Lock()
    results: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    listing_files: list[str] = []

    def call_list(keyword: str, assembly_id: str) -> dict[str, Any]:
        nonlocal calls
        with calls_lock:
            if calls >= max_api_calls:
                raise ApiBudgetExceeded("collector API call budget reached")
            calls += 1
        response = client.list_minutes(keyword, 0, 100, assembly_id=assembly_id)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        return response

    def call_detail(docid: str) -> dict[str, Any]:
        nonlocal calls
        with calls_lock:
            if calls >= max_api_calls:
                raise ApiBudgetExceeded("collector API call budget reached")
            calls += 1
        response = client.detail(docid)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        return response

    for region in selected:
        name = region["name"]
        assembly_id = region.get("assembly_id")
        row_result: dict[str, Any] = {
            "region_code": region["region_code"],
            "region": name,
            "province": region["province"],
            "kind": region["kind"],
            "assembly_id": assembly_id,
            "target_count": target_count,
            "existing_count": 0,
            "listed_unique": 0,
            "api_total_by_query": {},
            "processed_details": 0,
            "failed_details": [],
            "shortfall": target_count,
            "status": "unavailable" if not assembly_id else "pending",
        }
        if not assembly_id:
            row_result["reason"] = "CLiK에서 해당 지역 의회 ID가 확인되지 않음"
            results.append(row_result)
            continue
        existing_ids = _existing_docids(db, assembly_id)
        row_result["existing_count"] = len(existing_ids)
        if len(existing_ids) >= target_count:
            row_result["shortfall"] = 0
            row_result["status"] = "target_already_met"
            results.append(row_result)
            continue

        candidates: dict[str, dict[str, Any]] = {}
        try:
            for keyword in REGIONAL_QUERY_TERMS:
                if len(existing_ids) + len(candidates) >= target_count:
                    break
                response = call_list(keyword, assembly_id)
                listing_files.append(
                    _materialize_listing_response(
                        response,
                        raw_root,
                        region_code=region["region_code"],
                        keyword=keyword,
                        assembly_id=assembly_id,
                    )
                )
                row_result["api_total_by_query"][keyword] = int(response.get("TOTAL_COUNT") or 0)
                for row in _region_rows(response.get("LIST", []), assembly_id):
                    docid = str(row["DOCID"])
                    if docid not in existing_ids:
                        candidates.setdefault(docid, {**row, "_collection_query": keyword})
        except ApiBudgetExceeded as exc:
            stopped_reason = str(exc)
        except Exception as exc:
            row_result["failed_details"].append({"phase": "list", "error": str(exc)[:300]})
            if _is_quota_error(exc):
                stopped_reason = f"CLiK API quota error: {str(exc)[:250]}"

        row_result["listed_unique"] = len(candidates)
        need = max(0, target_count - len(existing_ids))
        detail_rows = list(candidates.values())[:need]
        # Detail calls are I/O-bound. A small bounded pool shortens a regional
        # run substantially while the locked counter still enforces the one
        # process API budget. SQLite writes remain serialized in this thread.
        with ThreadPoolExecutor(max_workers=min(detail_workers, max(1, len(detail_rows)))) as pool:
            futures = {
                pool.submit(call_detail, str(row["DOCID"])): str(row["DOCID"])
                for row in detail_rows
            }
            for future in as_completed(futures):
                docid = futures[future]
                try:
                    detail = future.result()
                    bundle = make_clik_bundle(detail)
                    materialized_path = materialize_clik_bundle(
                        bundle,
                        raw_root,
                        region_code=region["region_code"],
                    )
                    bundle["source"]["metadata"] = {
                        **bundle["source"].get("metadata", {}),
                        "collection_scope": "광주·전남 27개 지역",
                        "collection_region_code": region["region_code"],
                        "collection_region_group": region["region_group"],
                        "collection_query": next(
                            str(row.get("_collection_query"))
                            for row in detail_rows
                            if str(row.get("DOCID")) == docid
                        ),
                        "collection_method": "regional_high_precision_query",
                        "api_detail_file": materialized_path,
                    }
                    db.insert_document_bundle(bundle)
                    db.commit()
                    row_result["processed_details"] += 1
                    if row_result["processed_details"] % 10 == 0:
                        _emit(f"{name}: {row_result['processed_details']}/{need} details")
                except ApiBudgetExceeded as exc:
                    stopped_reason = str(exc)
                except Exception as exc:
                    db.conn.rollback()
                    row_result["failed_details"].append({"docid": docid, "error": str(exc)[:300]})
                    if _is_quota_error(exc):
                        stopped_reason = f"CLiK API quota error: {str(exc)[:250]}"
                if stopped_reason:
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    break
        final_count = len(existing_ids) + row_result["processed_details"]
        row_result["shortfall"] = max(0, target_count - final_count)
        row_result["status"] = "completed" if row_result["shortfall"] == 0 else "shortfall"
        if stopped_reason:
            row_result["status"] = "paused_quota"
            results.append(row_result)
            break
        results.append(row_result)

    by_status = defaultdict(int)
    for item in results:
        by_status[item["status"]] += 1
    review_counts = rebuild_case_reviews(db)
    db.commit()
    return {
        "scope": "regional_high_precision_minutes",
        "target_count_per_region": target_count,
        "regions_requested": len(selected),
        "regions_completed": sum(1 for item in results if item["status"] in {"completed", "target_already_met"}),
        "documents_processed": sum(item["processed_details"] for item in results),
        "shortfall_total": sum(item["shortfall"] for item in results),
        "api_calls_used": calls,
        "api_call_budget": max_api_calls,
        "stopped_reason": stopped_reason,
        "status_counts": dict(by_status),
        "review": review_counts,
        "raw_storage_root": str(raw_root.resolve()),
        "listing_files": len(listing_files),
        "regions": results,
    }
