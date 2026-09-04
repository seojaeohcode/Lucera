"""Persisted complaint intake and conversational RAG state."""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .db import LuceraDB, stable_id
from .location import normalize_address
from .rag import RAGService
from .yeongam import YEONGAM_COUNTY, require_yeongam


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _image_payload(value: Any) -> dict[str, str] | None:
    """Validate an optional user image without storing its bytes in SQLite."""

    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("image must be an object")
    media_type = str(value.get("media_type") or "").lower().strip()
    data = re.sub(r"\s+", "", str(value.get("data") or ""))
    if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError("image media_type must be png, jpeg, webp, or gif")
    if not data or len(data) > 6_000_000:
        raise ValueError("image data must be between 1 byte and 6 MB when encoded")
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", data):
        raise ValueError("image data must be base64")
    return {"media_type": media_type, "data": data}


def _complaint_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "conclusion": analysis.get("conclusion"),
        "conclusion_label": analysis.get("conclusion_label"),
        "issue_counts": analysis.get("issue_counts") or {},
        "geocode_status": (analysis.get("geocode") or {}).get("status"),
        "answer_generation": analysis.get("answer_generation") or {},
    }


def _insert_message(db: LuceraDB, conversation_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> str:
    message_id = str(uuid4())
    db.conn.execute(
        """INSERT INTO chat_message
           (message_id, conversation_id, role, content, metadata_json)
           VALUES (?, ?, ?, ?, ?)""",
        (message_id, conversation_id, role, content, _json(metadata or {})),
    )
    return message_id


def _link_evidence(db: LuceraDB, complaint_id: str, result: dict[str, Any]) -> int:
    links = 0
    for rank, item in enumerate((result.get("retrieval") or {}).get("evidence") or [], start=1):
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        db.conn.execute(
            """INSERT OR IGNORE INTO complaint_evidence
               (complaint_id, evidence_id, evidence_type, rank, metadata_json)
               VALUES (?, ?, 'meeting_evidence', ?, ?)""",
            (complaint_id, evidence_id, rank, _json({"title": item.get("title"), "source": item.get("source")})),
        )
        links += 1
    return links


def create_complaint(db: LuceraDB, payload: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(payload.get("text") or payload.get("message") or "").split())
    address = " ".join(str(payload.get("address") or "").split())
    if len(text) < 5:
        raise ValueError("민원 내용은 5자 이상 입력해 주세요.")
    if len(text) > 10_000:
        raise ValueError("민원 내용은 10,000자 이내로 입력해 주세요.")
    if not address:
        raise ValueError("address is required")
    parsed = normalize_address(address)
    require_yeongam(parsed.city_county, address)
    if payload.get("latitude") is None or payload.get("longitude") is None:
        # The form can ask the address provider, but a complaint is not shown
        # on the map until it has a real coordinate.
        if not payload.get("resolve_address", True):
            raise ValueError("좌표가 필요합니다. 주소 검색을 켜거나 테스트 좌표를 입력해 주세요.")

    image = _image_payload(payload.get("image"))
    complaint_id = str(uuid4())
    conversation_id = str(uuid4())
    rag_payload = dict(payload)
    rag_payload.update({"message": text, "address": address, "scope": "yeongam"})
    rag_payload["resolve_address"] = bool(payload.get("resolve_address", True))
    rag_payload.setdefault("review_mode", "all")
    rag_payload.setdefault("include_comparative", False)
    # 지도 영상은 민원 분석과 챗봇 모두에서 기본 포함한다. VWorld 키가
    # 없으면 RAG 응답에 그 상태를 남기고 로컬 분석으로 안전하게 강등된다.
    rag_payload["include_map_context"] = True
    if image:
        rag_payload["image"] = image
    result = RAGService(db).analyze(rag_payload)
    location = result.get("location") or {}
    require_yeongam(location.get("city_county"), address)
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("주소의 좌표를 확인하지 못했습니다. 더 구체적인 영암군 주소를 입력해 주세요.")
    normalized_address = location.get("normalized_address") or address
    title = " ".join(str(payload.get("title") or "영암군 민원").split())[:200]
    issue_counts = (result.get("analysis") or {}).get("issue_counts") or {}
    db.conn.execute(
        """INSERT INTO complaint_submission
           (complaint_id, conversation_id, raw_text, title, address,
            normalized_address, province, city_county, eup_myeon, ri,
            latitude, longitude, geocode_status, ai_summary, issue_codes_json,
            status, data_origin, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'analyzed', 'user_input', ?)""",
        (
            complaint_id, conversation_id, text, title, address,
            normalized_address, location.get("province"), location.get("city_county"),
            location.get("eup_myeon"), location.get("ri"), latitude, longitude,
            (result.get("geocode") or {}).get("status") or "resolved",
            result.get("answer") or "", _json(list(issue_counts)),
            _json({"image_received": bool(image), "input": {"site_area_sqm": payload.get("site_area_sqm"), "capacity_kw": payload.get("capacity_kw")}}),
        ),
    )
    db.conn.execute(
        """INSERT INTO chat_conversation
           (conversation_id, complaint_id, address, latitude, longitude, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (conversation_id, complaint_id, normalized_address, latitude, longitude, _json({"scope": "yeongam"})),
    )
    _insert_message(db, conversation_id, "user", text, {"kind": "complaint_intake", "image_received": bool(image)})
    _insert_message(db, conversation_id, "assistant", result.get("answer") or "", {"kind": "initial_analysis"})
    links = _link_evidence(db, complaint_id, result)
    db.conn.execute("UPDATE chat_conversation SET updated_at=? WHERE conversation_id=?", (_now(), conversation_id))
    db.commit()
    return {
        "complaint_id": complaint_id,
        "conversation_id": conversation_id,
        "complaint": get_complaint(db, complaint_id),
        "analysis": result,
        "evidence_links": links,
    }


def get_complaint(db: LuceraDB, complaint_id: str) -> dict[str, Any] | None:
    row = db.conn.execute("SELECT * FROM complaint_submission WHERE complaint_id=?", (complaint_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("issue_codes_json", "metadata_json"):
        try:
            item[key.removesuffix("_json")] = json.loads(item.pop(key) or "{}")
        except (TypeError, ValueError):
            item[key.removesuffix("_json")] = {} if key == "metadata_json" else []
    item["county"] = YEONGAM_COUNTY
    return item


def get_conversation(db: LuceraDB, conversation_id: str) -> dict[str, Any] | None:
    row = db.conn.execute("SELECT * FROM chat_conversation WHERE conversation_id=?", (conversation_id,)).fetchone()
    if not row:
        return None
    messages = []
    for message in db.conn.execute(
        "SELECT message_id, role, content, metadata_json, created_at FROM chat_message WHERE conversation_id=? ORDER BY created_at, rowid",
        (conversation_id,),
    ).fetchall():
        item = dict(message)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            item["metadata"] = {}
        messages.append(item)
    return {"conversation": dict(row), "complaint": get_complaint(db, row["complaint_id"]), "messages": messages}


def continue_conversation(db: LuceraDB, conversation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    conversation = get_conversation(db, conversation_id)
    if not conversation:
        raise ValueError("conversation not found")
    message = " ".join(str(payload.get("message") or "").split())
    if len(message) < 1 or len(message) > 10_000:
        raise ValueError("message must be between 1 and 10000 characters")
    source = conversation["conversation"]
    image = _image_payload(payload.get("image"))
    address = " ".join(str(payload.get("address") or source["address"]).split())
    parsed_address = normalize_address(address)
    require_yeongam(parsed_address.city_county, address)
    address_changed = address != source["address"]
    rag_payload = {
        **payload,
        "message": message,
        "address": address,
        "latitude": None if address_changed else source["latitude"],
        "longitude": None if address_changed else source["longitude"],
        "resolve_address": bool(payload.get("resolve_address", address_changed)),
        "scope": "yeongam",
        "include_comparative": False,
        "include_map_context": True,
    }
    if image:
        rag_payload["image"] = image
    result = RAGService(db).analyze(rag_payload)
    resolved = result.get("location") or {}
    if address_changed and (resolved.get("latitude") is None or resolved.get("longitude") is None):
        raise ValueError("새 주소의 좌표를 확인하지 못했습니다. 더 구체적인 영암군 주소를 입력해 주세요.")
    _insert_message(db, conversation_id, "user", message, {"kind": "follow_up", "image_received": bool(image)})
    _insert_message(db, conversation_id, "assistant", result.get("answer") or "", {"kind": "follow_up_analysis"})
    db.conn.execute("UPDATE chat_conversation SET updated_at=? WHERE conversation_id=?", (_now(), conversation_id))
    if address_changed:
        db.conn.execute(
            "UPDATE chat_conversation SET address=?, latitude=?, longitude=?, updated_at=? WHERE conversation_id=?",
            (resolved.get("normalized_address") or address, resolved["latitude"], resolved["longitude"], _now(), conversation_id),
        )
    if conversation.get("complaint"):
        _link_evidence(db, conversation["complaint"]["complaint_id"], result)
    db.commit()
    return {"conversation_id": conversation_id, "analysis": result, "messages": get_conversation(db, conversation_id)["messages"]}


YEONGAM_AREA_ORDER = (
    "영암읍", "삼호읍", "덕진면", "금정면", "신북면", "시종면",
    "도포면", "군서면", "미암면", "학산면", "서호면",
)


def _safe_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _area_summary(pins: list[dict[str, Any]], area: str) -> dict[str, Any]:
    selected = pins if area == "영암군 전체" else [pin for pin in pins if pin.get("eup_myeon") == area]
    permits = [pin for pin in selected if pin.get("kind") == "permit"]
    complaints = [pin for pin in selected if pin.get("kind") == "complaint"]
    issue_counts: dict[str, int] = {}
    for pin in selected:
        for code in pin.get("issues") or []:
            issue_counts[str(code)] = issue_counts.get(str(code), 0) + 1
    coordinates = [
        (float(pin["latitude"]), float(pin["longitude"]))
        for pin in selected
        if pin.get("latitude") is not None and pin.get("longitude") is not None
    ]
    dates = [str(pin.get("created_at")) for pin in selected if pin.get("created_at")]
    status_counts: dict[str, int] = {}
    for pin in selected:
        status = str(pin.get("status") or "상태 미상")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "area": area,
        "pin_count": len(selected),
        "permit_count": len(permits),
        "complaint_count": len(complaints),
        "total_capacity_kw": sum(float(pin.get("capacity_kw") or 0) for pin in permits),
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        "issues": list(sorted(issue_counts, key=lambda code: (-issue_counts[code], code))),
        "status_counts": status_counts,
        "latest_date": max(dates) if dates else None,
        "center": {
            "latitude": round(sum(item[0] for item in coordinates) / len(coordinates), 6) if coordinates else None,
            "longitude": round(sum(item[1] for item in coordinates) / len(coordinates), 6) if coordinates else None,
        },
        "notice": "합성 참고 사업과 사용자가 접수한 영암군 민원을 읍·면 단위로 묶은 화면용 요약입니다.",
    }


def yeongam_pins(db: LuceraDB) -> dict[str, Any]:
    pins: list[dict[str, Any]] = []
    for row in db.conn.execute(
        """SELECT complaint_id AS id, 'complaint' AS kind, title, normalized_address AS address,
                  latitude, longitude, status, issue_codes_json AS issues,
                  province, city_county, eup_myeon, ri, created_at, data_origin
           FROM complaint_submission
           WHERE city_county='영암군' AND latitude IS NOT NULL AND longitude IS NOT NULL
           ORDER BY created_at DESC"""
    ).fetchall():
        item = dict(row)
        item["issues"] = _safe_json(item.get("issues"), [])
        item["data_origin"] = item.get("data_origin") or "user_input"
        pins.append(item)
    for row in db.conn.execute(
        """SELECT project_id AS id, 'permit' AS kind, facility_name AS title,
                  COALESCE(jibun_address, road_address) AS address, latitude,
                  longitude, operation_status AS status, company_name AS company,
                  capacity_kw, province, city_county, eup_myeon, ri,
                  metadata_json AS metadata, permit_date AS created_at, 'synthetic' AS data_origin
           FROM permit_project
           WHERE city_county='영암군' AND latitude IS NOT NULL AND longitude IS NOT NULL
           ORDER BY permit_date DESC"""
    ).fetchall():
        item = dict(row)
        item["metadata"] = _safe_json(item.pop("metadata"), {})
        metadata = item["metadata"]
        item["issues"] = metadata.get("issues") or []
        for key in ("site_area_sqm", "installation_area_min_sqm", "installation_area_max_sqm", "verdict", "evidence", "geo_precision"):
            if key in metadata:
                item[key] = metadata[key]
        pins.append(item)

    areas = [_area_summary(pins, "영암군 전체")]
    known_areas = {str(pin.get("eup_myeon")) for pin in pins if pin.get("eup_myeon")}
    ordered_areas = [area for area in YEONGAM_AREA_ORDER if area in known_areas]
    ordered_areas.extend(sorted(known_areas - set(ordered_areas)))
    areas.extend(_area_summary(pins, area) for area in ordered_areas)
    return {
        "scope": "yeongam",
        "county": YEONGAM_COUNTY,
        "pins": pins,
        "count": len(pins),
        "areas": areas,
        "notice": "영암군만 표시합니다. 지도 핀과 지역 요약은 합성 데이터 또는 사용자가 접수한 데이터입니다.",
    }


def yeongam_area_detail(db: LuceraDB, area: str) -> dict[str, Any] | None:
    payload = yeongam_pins(db)
    summary = next((item for item in payload["areas"] if item["area"] == area), None)
    if not summary:
        return None
    pins = payload["pins"] if area == "영암군 전체" else [pin for pin in payload["pins"] if pin.get("eup_myeon") == area]
    return {
        "scope": payload["scope"],
        "county": payload["county"],
        "area": area,
        "summary": summary,
        "pins": pins,
        "notice": payload["notice"],
    }
