from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Any

from .db import LuceraDB, now_utc, stable_id
from .location import normalize_address


ATTACHMENT_TYPES = (
    "business_plan",
    "profit_loss_plan",
    "funding_plan",
    "land_use_consent",
    "building_register",
    "land_register",
    "cadastral_map",
    "land_use_plan",
    "structural_safety_certificate",
)

ATTACHMENT_STATUSES = {"not_started", "queued", "extracted", "failed", "reviewed"}
ATTACHMENT_REQUIRED_TYPES = {"business_plan", "funding_plan", "land_use_consent", "land_register", "cadastral_map"}
PROJECT_SCHEMA_VERSION = "project-intake-v2"
INTAKE_CHANNELS = {"web_form", "api", "import", "draft_save"}
GENERIC_PROJECT_NAMES = {"태양광", "태양광발전", "태양광발전시설", "태양광발전소", "발전시설", "발전사업"}

STAGES = (
    ("application", 1),
    ("power_generation_permit", 2),
    ("development_environment_review", 3),
    ("resident_consultation_complaint", 4),
    ("construction_completion", 5),
    ("grid_connection_operation", 6),
)

NUMERIC_FIELDS = {
    "installed_capacity_kw": "kw",
    "module_count": "count",
    "module_capacity_w": "w",
    "inverter_count": "count",
    "inverter_capacity_kva": "kva",
    "installation_height_m": "m",
    "installation_area_sqm": "sqm",
    "total_project_cost_krw": "krw",
    "construction_cost_per_kw": "krw_per_kw",
    "annual_generation_mwh": "mwh",
    "annual_transmission_mwh": "mwh",
    "lease_fee_krw": "krw",
    "resident_revenue_share": "percent",
    "operation_period_years": "years",
    "connection_voltage_v": "v",
}

DATE_FIELDS = {
    "permit_application_date",
    "permit_date",
    "construction_start_date",
    "expected_completion_date",
    "business_start_date",
}

DATE_FIELDS_ORDER = (
    "permit_application_date",
    "permit_date",
    "construction_start_date",
    "expected_completion_date",
    "business_start_date",
)

COUNT_FIELDS = {"module_count", "inverter_count"}

BOOLEAN_FIELDS = {
    "development_permit_required",
    "urban_management_plan_required",
    "construction_plan_report",
    "environmental_assessment_required",
    "structural_safety_review",
    "resident_consent_required",
    "construction_consent",
    "complaint_occurred",
    "complaint_stop_commitment",
    "removal_commitment",
}

TEXT_FIELDS = {
    "project_name",
    "business_type",
    "permit_type",
    "applicant_name",
    "applicant_type",
    "corporate_name",
    "contractor_name",
    "site_address",
    "lot_number",
    "land_category",
    "building_address",
    "building_use",
    "grid_connection_point",
    "transformer_info",
    "power_purchase_method",
    "complaint_type",
}

FIELD_UNITS = {**NUMERIC_FIELDS}


def _value(payload: dict[str, Any], key: str) -> Any:
    """Read both the flat API shape and nested form sections."""
    if key in payload:
        return payload[key]
    for section in payload.values():
        if isinstance(section, dict) and key in section:
            return section[key]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    result = float(str(value).replace(",", "").replace("₩", "").replace("원", "").strip())
    if not math.isfinite(result):
        raise ValueError("number must be finite")
    return result


def _boolean(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return int(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "예", "네", "필요", "있음", "완료"}:
        return 1
    if normalized in {"false", "0", "no", "n", "아니오", "아니요", "불필요", "없음", "미완료"}:
        return 0
    raise ValueError("boolean must be true/false")


def _iso_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    result = str(value).strip()
    date.fromisoformat(result)
    return result


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("integer cannot be boolean")
    number = float(str(value).replace(",", "").strip())
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError("integer must be finite and whole")
    return int(number)


def _safe_confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return number


def _attachment_items(payload: dict[str, Any], document_type: str) -> list[Any]:
    raw_items = _value(payload, document_type)
    if raw_items is None:
        return []
    return raw_items if isinstance(raw_items, list) else [raw_items]


def _location_overrides(payload: dict[str, Any], relation: str) -> dict[str, Any]:
    """Accept optional coordinates without making them mandatory input fields."""
    prefix = {
        "subject_site": "site",
        "building_site": "building",
        "grid_connection_point": "grid",
    }.get(relation, relation)
    result: dict[str, Any] = {}
    locations = payload.get("locations")
    if isinstance(locations, dict):
        section = locations.get(prefix)
        if isinstance(section, dict):
            result.update(section)
    for key in (f"{prefix}_latitude", f"{prefix}_longitude", f"{prefix}_geo_precision", f"{prefix}_resolution_method"):
        if key in payload:
            result[key.removeprefix(f"{prefix}_")] = payload[key]
    return result


def validate_project_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Normalize the form once, returning values, hard errors, and warnings."""
    if not isinstance(payload, dict):
        raise ValueError("project payload must be an object")
    values: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for field in TEXT_FIELDS:
        values[field] = _text(_value(payload, field))
    for field in NUMERIC_FIELDS:
        try:
            values[field] = _number(_value(payload, field))
            if field in COUNT_FIELDS and values[field] is not None:
                if not values[field].is_integer():
                    raise ValueError("count must be whole")
                values[field] = int(values[field])
        except (TypeError, ValueError):
            errors.append(f"{field} must be numeric")
            values[field] = None
    for field in BOOLEAN_FIELDS:
        try:
            values[field] = _boolean(_value(payload, field))
        except (TypeError, ValueError):
            errors.append(f"{field} must be boolean")
            values[field] = None
    for field in DATE_FIELDS:
        try:
            values[field] = _iso_date(_value(payload, field))
        except (TypeError, ValueError):
            errors.append(f"{field} must be YYYY-MM-DD")
            values[field] = None

    if not values["project_name"]:
        errors.append("project_name is required")
    if not values["site_address"]:
        errors.append("site_address is required")

    for field, unit in NUMERIC_FIELDS.items():
        value = values[field]
        if value is not None and value < 0:
            errors.append(f"{field} cannot be negative")
        if field == "resident_revenue_share" and value is not None and not 0 <= value <= 100:
            errors.append("resident_revenue_share must be between 0 and 100")
        if field == "operation_period_years" and value is not None and value == 0:
            errors.append("operation_period_years must be greater than zero")

    for earlier, later in zip(DATE_FIELDS_ORDER, DATE_FIELDS_ORDER[1:]):
        if values[earlier] and values[later] and values[earlier] > values[later]:
            warnings.append(f"{earlier}가 {later}보다 늦습니다")
    if values["complaint_occurred"] == 1 and not values["complaint_type"]:
        warnings.append("complaint_occurred=true인데 complaint_type이 비어 있습니다")
    if values["structural_safety_review"] == 1 and not _value(payload, "structural_safety_certificate"):
        warnings.append("구조안전 검토가 필요하지만 구조안전확인서가 첨부되지 않았습니다")
    if values["environmental_assessment_required"] == 1 and not _value(payload, "land_use_plan"):
        warnings.append("환경영향 검토가 필요하지만 토지이용계획서가 첨부되지 않았습니다")

    values["intake_channel"] = _text(_value(payload, "intake_channel")) or "web_form"
    if values["intake_channel"] not in INTAKE_CHANNELS:
        errors.append("intake_channel is invalid")
        values["intake_channel"] = "web_form"

    capacity = values["installed_capacity_kw"]
    module_count = values["module_count"]
    module_w = values["module_capacity_w"]
    if capacity and module_count and module_w:
        calculated = module_count * module_w / 1000
        if abs(calculated - capacity) > max(0.01, capacity * 0.03):
            warnings.append("설비용량과 모듈 수×모듈 용량이 3% 이상 차이납니다")

    for document_type in ATTACHMENT_TYPES:
        for item in _attachment_items(payload, document_type):
            if not isinstance(item, dict):
                continue
            status = _text(item.get("extraction_status")) or "not_started"
            if status not in ATTACHMENT_STATUSES:
                errors.append(f"{document_type}.extraction_status is invalid")
            try:
                if item.get("file_size_bytes") is not None:
                    if _safe_int(item.get("file_size_bytes")) is not None and _safe_int(item.get("file_size_bytes")) < 0:
                        errors.append(f"{document_type}.file_size_bytes cannot be negative")
            except (TypeError, ValueError):
                errors.append(f"{document_type}.file_size_bytes must be an integer")
            for fact in (item.get("extracted_facts") or [] if isinstance(item.get("extracted_facts"), list) else []):
                if isinstance(fact, dict) and fact.get("confidence") is not None:
                    try:
                        _safe_confidence(fact.get("confidence"))
                    except (TypeError, ValueError):
                        errors.append(f"{document_type}.extracted_facts.confidence must be between 0 and 1")

    values["project_status"] = _text(_value(payload, "project_status")) or "submitted"
    if values["project_status"] not in {"draft", "submitted", "under_review", "completed", "archived"}:
        errors.append("project_status is invalid")
        values["project_status"] = "submitted"
    return values, errors, warnings


def _place_payload(project_id: str, relation: str, raw_address: str, overrides: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    location = normalize_address(raw_address)
    overrides = overrides or {}
    latitude = overrides.get("latitude")
    longitude = overrides.get("longitude")
    try:
        latitude = float(latitude) if latitude is not None else None
        longitude = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        latitude = longitude = None
    if latitude is not None and not 32 <= latitude <= 39:
        latitude = None
    if longitude is not None and not 124 <= longitude <= 132:
        longitude = None
    precision = _text(overrides.get("geo_precision")) or location.precision
    if precision not in {"parcel", "building", "road_address", "jibun_address", "village", "ri", "eup_myeon", "city_county", "province", "unknown"}:
        precision = location.precision
    place_id = stable_id(
        "project_input_place",
        location.normalized_address,
        location.road_address,
        location.jibun_address,
        latitude,
        longitude,
    )
    return place_id, {
        "place_id": place_id,
        "raw_name": raw_address,
        "normalized_name": location.normalized_address,
        "road_address": location.road_address,
        "jibun_address": location.jibun_address,
        "province": location.province,
        "city_county": location.city_county,
        "eup_myeon": location.eup_myeon,
        "ri": location.ri,
        "admin_code": location.admin_code,
        "latitude": latitude,
        "longitude": longitude,
        "geo_precision": precision,
        "geocode_confidence": 0.95 if latitude is not None and longitude is not None else location.confidence,
        "location_status": "confirmed" if latitude is not None and longitude is not None else "candidate",
        "geo_provider": _text(overrides.get("geo_provider")) or location.provider,
        "resolution_method": _text(overrides.get("resolution_method")) or ("project_input_coordinate" if latitude is not None and longitude is not None else "project_input_rule"),
        "relation_type": relation,
        "distance_status": "unknown",
    }


def _insert_project_place(db: LuceraDB, project_id: str, application_id: str, place: dict[str, Any]) -> str:
    db.conn.execute(
        """INSERT INTO canonical_place
           (place_id, place_type, raw_name, normalized_name, road_address,
            jibun_address, province, city_county, eup_myeon, ri, admin_code,
            latitude, longitude, geo_precision, geocode_confidence, location_status,
            resolution_method, geo_provider, metadata_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(place_id) DO UPDATE SET
             raw_name=excluded.raw_name, normalized_name=excluded.normalized_name,
             road_address=excluded.road_address, jibun_address=excluded.jibun_address,
             province=excluded.province, city_county=excluded.city_county,
             eup_myeon=excluded.eup_myeon, ri=excluded.ri,
             admin_code=excluded.admin_code, geo_precision=excluded.geo_precision,
             latitude=excluded.latitude, longitude=excluded.longitude,
             geocode_confidence=excluded.geocode_confidence,
             location_status=excluded.location_status,
             resolution_method=excluded.resolution_method,
             geo_provider=excluded.geo_provider,
             metadata_json=excluded.metadata_json, updated_at=CURRENT_TIMESTAMP""",
        (
            place["place_id"],
            place["geo_precision"] if place["geo_precision"] in {"parcel", "building", "road_address", "jibun_address", "village", "ri", "eup_myeon", "city_county", "province"} else "unknown",
            place["raw_name"],
            place["normalized_name"],
            place["road_address"],
            place["jibun_address"],
            place["province"],
            place["city_county"],
            place["eup_myeon"],
            place["ri"],
            place["admin_code"],
            place["latitude"],
            place["longitude"],
            place["geo_precision"],
            place["geocode_confidence"],
            place["location_status"],
            place["resolution_method"],
            place["geo_provider"],
            json.dumps({"source": "project_input", "relation_type": place["relation_type"]}, ensure_ascii=False),
        ),
    )
    db.conn.execute(
        """INSERT INTO project_location_link
           (project_location_link_id, project_id, application_id, place_id,
            relation_type, raw_query, candidate_rank, resolution_method,
            geo_provider, resolved_at, confidence, distance_status,
            review_status, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            stable_id("project_location_link", application_id, place["place_id"], place["relation_type"]),
            project_id,
            application_id,
            place["place_id"],
            place["relation_type"],
            place["raw_name"],
            1,
            place["resolution_method"],
            place["geo_provider"],
            now_utc() if place["latitude"] is not None and place["longitude"] is not None else None,
            place["geocode_confidence"],
            place["distance_status"],
            json.dumps({"raw_address": place["raw_name"]}, ensure_ascii=False),
        ),
    )
    return place["place_id"]


def _address_type(location: Any, *, has_lot_number: bool = False) -> str:
    if location.precision == "road_address":
        return "road"
    if has_lot_number or location.jibun_address:
        return "jibun"
    if location.precision in {"province", "city_county", "eup_myeon", "ri"}:
        return "administrative_only"
    return "unknown"


def _address_resolution_status(location: Any, place: dict[str, Any]) -> str:
    if place.get("latitude") is not None and place.get("longitude") is not None:
        return "resolved"
    if location.status in {"parsed", "resolved"}:
        return "parsed"
    return "unresolved"


def _insert_fact(
    db: LuceraDB,
    application_id: str,
    field_name: str,
    value: Any,
    *,
    source_kind: str = "user_input",
    source_field: str | None = None,
    source_attachment_id: str | None = None,
    source_artifact_id: str | None = None,
    source_document_id: str | None = None,
    source_paragraph_id: str | None = None,
    source_page: int | None = None,
    source_char_start: int | None = None,
    source_char_end: int | None = None,
    source_excerpt: str | None = None,
    extraction_method: str = "form_input",
    extraction_model: str | None = None,
    extraction_version: str | None = None,
    confidence: float | None = None,
    fact_identity: str | int | None = None,
) -> None:
    if value is None:
        return
    if field_name in NUMERIC_FIELDS:
        value_type, value_numeric, value_text, value_date, value_boolean, value_json = "numeric", value, None, None, None, None
    elif field_name in DATE_FIELDS:
        value_type, value_numeric, value_text, value_date, value_boolean, value_json = "date", None, None, value, None, None
    elif field_name in BOOLEAN_FIELDS:
        value_type, value_numeric, value_text, value_date, value_boolean, value_json = "boolean", None, None, None, value, None
    elif isinstance(value, (dict, list)):
        value_type, value_numeric, value_text, value_date, value_boolean, value_json = "json", None, None, None, None, json.dumps(value, ensure_ascii=False, default=str)
    else:
        value_type, value_numeric, value_text, value_date, value_boolean, value_json = "text", None, str(value), None, None, None
    db.conn.execute(
        """INSERT INTO project_fact
           (fact_id, application_id, field_name, value_type, value_text,
            value_numeric, value_date, value_boolean, value_json, unit,
            source_kind, source_field, source_attachment_id, source_artifact_id,
            source_document_id, source_paragraph_id, source_page, source_char_start,
            source_char_end, source_excerpt, extraction_method, extraction_model,
            extraction_version, confidence, fact_status, is_current, review_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, 'pending')""",
        (
            stable_id("project_fact", application_id, field_name, source_attachment_id or "form", source_page or "", fact_identity or ""),
            application_id,
            field_name,
            value_type,
            value_text,
            value_numeric,
            value_date,
            value_boolean,
            value_json,
            FIELD_UNITS.get(field_name),
            source_kind,
            source_field or field_name,
            source_attachment_id,
            source_artifact_id,
            source_document_id,
            source_paragraph_id,
            source_page,
            source_char_start,
            source_char_end,
            source_excerpt,
            extraction_method,
            extraction_model,
            extraction_version,
            confidence,
        ),
    )


def _stage_state(values: dict[str, Any], stage_code: str) -> tuple[str, str | None, str | None, int | None, str]:
    if stage_code == "application":
        actual = values["permit_application_date"]
        return ("completed", None, actual, 1, "사용자 입력의 인허가 신청일") if actual else ("unknown", None, None, 1, "신청일 미입력")
    if stage_code == "power_generation_permit":
        actual = values["permit_date"]
        return ("completed", None, actual, 1, "사용자 입력의 발전사업허가일") if actual else ("required", None, None, 1, "발전사업허가 확인 필요")
    if stage_code == "development_environment_review":
        required = any(values[field] == 1 for field in ("development_permit_required", "urban_management_plan_required", "environmental_assessment_required"))
        notes = "개발행위·도시관리계획·환경 검토 대상" if required else "관련 검토 여부 미확인"
        return ("required" if required else "unknown", None, None, int(required), notes)
    if stage_code == "resident_consultation_complaint":
        if values["complaint_occurred"] == 1:
            return "reported", None, None, 1, values["complaint_type"] or "민원 발생 입력"
        if values["resident_consent_required"] == 1 and values["construction_consent"] == 1:
            return "completed", None, None, 1, "주민동의·공사동의 입력 확인"
        if values["resident_consent_required"] == 1:
            return "required", None, None, 1, "주민동의 확인 필요"
        return "unknown", None, None, None, "주민협의 정보 미확인"
    if stage_code == "construction_completion":
        start = values["construction_start_date"]
        complete = values["expected_completion_date"]
        business = values["business_start_date"]
        if business:
            return "completed", complete, business, 1, "사업개시일 입력 확인"
        if start:
            return "in_progress", complete, start, 1, "공사 시작일 입력 확인"
        return "planned" if complete else "unknown", complete, None, 1, "공사 일정 미확인"
    grid = values["grid_connection_point"]
    business = values["business_start_date"]
    if business:
        return "completed", business, business, 1, "사업개시일 입력 확인"
    if grid:
        return "planned", None, None, 1, "계통연계 지점 입력 확인"
    return "unknown", None, None, 1, "계통연계 정보 미확인"


def _schedule_status(values: dict[str, Any], warnings: list[str]) -> str:
    if any("보다 늦습니다" in warning for warning in warnings):
        return "inconsistent"
    if values["business_start_date"]:
        return "completed"
    if values["construction_start_date"]:
        return "in_progress"
    if values["expected_completion_date"]:
        return "planned"
    return "unknown"


def _checklist_status(values: dict[str, Any]) -> str:
    fields = ("development_permit_required", "urban_management_plan_required", "construction_plan_report", "environmental_assessment_required", "structural_safety_review")
    provided = [values[field] for field in fields if values[field] is not None]
    if not provided:
        return "not_started"
    return "completed" if len(provided) == len(fields) else "in_progress"


def _risk_status(values: dict[str, Any]) -> str:
    if values["complaint_occurred"] == 1:
        return "reported"
    if values["complaint_occurred"] == 0:
        return "none_reported"
    if values["resident_consent_required"] == 1:
        return "under_consultation"
    return "unknown"


def _connection_status(values: dict[str, Any]) -> str:
    if values["business_start_date"]:
        return "connected"
    if values["grid_connection_point"]:
        return "planned"
    return "unknown"


def _stage_source_field(values: dict[str, Any], stage_code: str) -> str | None:
    candidates = {
        "application": ("permit_application_date",),
        "power_generation_permit": ("permit_date", "permit_type"),
        "development_environment_review": ("development_permit_required", "urban_management_plan_required", "environmental_assessment_required"),
        "resident_consultation_complaint": ("complaint_occurred", "complaint_type", "resident_consent_required", "construction_consent"),
        "construction_completion": ("construction_start_date", "expected_completion_date", "business_start_date"),
        "grid_connection_operation": ("grid_connection_point", "business_start_date", "power_purchase_method"),
    }.get(stage_code, ())
    return next((field for field in candidates if values.get(field) is not None), None)


def _user_fact_id(application_id: str, field_name: str) -> str:
    return stable_id("project_fact", application_id, field_name, "form", "", "")


def _insert_stage_event(
    db: LuceraDB,
    application_id: str,
    stage_code: str,
    event_type: str,
    *,
    event_status: str = "reported",
    event_date: str | None = None,
    title: str | None = None,
    description: str | None = None,
    source_field: str | None = None,
    source_fact_id: str | None = None,
    confidence: float | None = 1.0,
) -> None:
    stage_id = stable_id("project_stage", application_id, stage_code)
    event_id = stable_id("project_stage_event", application_id, stage_code, event_type, event_date or "")
    db.conn.execute(
        """INSERT INTO project_stage_event
           (stage_event_id, stage_id, application_id, stage_code, event_type,
            event_status, event_date, title, description, source_kind,
            source_field, source_fact_id, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_input', ?, ?, ?)""",
        (event_id, stage_id, application_id, stage_code, event_type, event_status, event_date, title, description, source_field, source_fact_id, confidence),
    )


def _case_match_score(values: dict[str, Any], location: Any, case: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    components: dict[str, float] = {}
    raw_village = case.get("village") or ""
    case_village = case.get("place_ri") or ("" if str(raw_village).startswith("place:") else raw_village)
    case_eup = case.get("place_eup_myeon") or ""
    if location.ri and case_village and (location.ri == case_village or location.ri in case_village or case_village in location.ri):
        components["same_ri"] = 0.35
    elif location.eup_myeon and case_eup and location.eup_myeon == case_eup:
        components["same_eup_myeon"] = 0.28
    elif location.eup_myeon and case.get("village") and (location.eup_myeon in case["village"] or case["village"] in location.eup_myeon):
        components["same_eup_myeon"] = 0.28
    case_city = case.get("place_city_county") or case.get("municipality")
    if location.city_county and case_city == location.city_county:
        components["same_municipality"] = 0.20
    case_address = case.get("place_normalized_name") or case.get("address")
    if case_address and location.normalized_address == case_address:
        components["same_address"] = 0.45
    project_name = values.get("project_name") or ""
    historical_project = case.get("project_name") or ""
    if (
        project_name not in GENERIC_PROJECT_NAMES
        and historical_project not in GENERIC_PROJECT_NAMES
        and project_name
        and historical_project
        and (project_name == historical_project or project_name in historical_project or historical_project in project_name)
    ):
        components["same_project_name"] = 0.45
    facility = values.get("business_type") or values.get("permit_type") or ""
    if case.get("facility_type") and facility and (case["facility_type"] in facility or facility in case["facility_type"]):
        components["facility_type"] = 0.08
    return min(1.0, sum(components.values())), components


def _match_historical_cases(db: LuceraDB, project_id: str, application_id: str, values: dict[str, Any]) -> int:
    location = normalize_address(values["site_address"])
    rows = db.conn.execute(
        """SELECT case_id, case_key, canonical_title, municipality, village,
                  address, project_name, facility_type, confidence,
                  p.province AS place_province, p.city_county AS place_city_county,
                  p.eup_myeon AS place_eup_myeon, p.ri AS place_ri,
                  p.normalized_name AS place_normalized_name
             FROM conflict_case c
             LEFT JOIN canonical_place p ON p.place_id=c.representative_place_id
            WHERE (c.municipality=? OR p.city_county=?)
              AND (? IS NULL OR p.province IS NULL OR p.province=?)""",
        (location.city_county, location.city_county, location.province, location.province),
    ).fetchall()
    stage_code = "resident_consultation_complaint" if values["complaint_occurred"] == 1 or values["complaint_type"] else "development_environment_review"
    inserted = 0
    for row in rows:
        case = dict(row)
        score, features = _case_match_score(values, location, case)
        if score < 0.35:
            continue
        location_match_type = next(
            (key for key in ("same_address", "same_ri", "same_eup_myeon", "same_municipality") if key in features),
            "none",
        )
        db.conn.execute(
            """INSERT INTO project_case_link
               (project_case_link_id, project_id, application_id, case_id,
                stage_code, relation_type, match_score, matching_features_json,
                match_method, location_match_type, review_reason, review_status)
               VALUES (?, ?, ?, ?, ?, 'historical_comparable', ?, ?, 'deterministic_v1', ?, ?, 'pending')
               ON CONFLICT(application_id, case_id, stage_code, relation_type) DO UPDATE SET
                 match_score=excluded.match_score,
                 matching_features_json=excluded.matching_features_json,
                 match_method=excluded.match_method,
                 location_match_type=excluded.location_match_type,
                 review_reason=excluded.review_reason,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                stable_id("project_case_link", application_id, case["case_id"], stage_code),
                project_id,
                application_id,
                case["case_id"],
                stage_code,
                score,
                json.dumps(features, ensure_ascii=False),
                location_match_type,
                "자동 비교 후보이며 동일 민원 확정 전 검수가 필요함",
            ),
        )
        db.conn.execute(
            """UPDATE project_stage
                  SET case_id=?
                WHERE application_id=? AND stage_code=?
                  AND (case_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM project_case_link current_link
                         WHERE current_link.application_id=project_stage.application_id
                           AND current_link.stage_code=project_stage.stage_code
                           AND current_link.case_id=project_stage.case_id
                           AND current_link.match_score >= ?))""",
            (case["case_id"], application_id, stage_code, score),
        )
        inserted += 1
    return inserted


def create_project(db: LuceraDB, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a revision atomically; no partial project survives a failure."""
    try:
        return _create_project(db, payload)
    except Exception:
        db.conn.rollback()
        raise


def _create_project(db: LuceraDB, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one versioned project intake and its full workflow scaffold."""
    values, errors, warnings = validate_project_payload(payload)
    if errors:
        raise ValueError("; ".join(errors))
    for document_type in ATTACHMENT_TYPES:
        for raw_item in _attachment_items(payload, document_type):
            if not isinstance(raw_item, dict):
                continue
            source_document_id = raw_item.get("source_document_id")
            if source_document_id and not db.conn.execute(
                "SELECT 1 FROM source_document WHERE document_id=?", (source_document_id,)
            ).fetchone():
                raise ValueError(f"{document_type}.source_document_id does not exist")
    project_key = _text(_value(payload, "project_key")) or f"{values['project_name']}|{values['site_address']}"
    project_id = stable_id("project_intake", project_key)
    existing = db.conn.execute("SELECT current_revision_no FROM project_intake WHERE project_id=?", (project_id,)).fetchone()
    revision_no = int(existing["current_revision_no"] if existing else 0) + 1
    application_id = stable_id("project_application", project_id, revision_no)
    submission_id = stable_id("project_submission", project_id, revision_no)
    validation_status = "valid_with_warnings" if warnings else "valid"
    raw_payload = json.dumps(payload, ensure_ascii=False, default=str)
    payload_sha256 = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    submitted_by = _text(_value(payload, "submitted_by"))
    created_by = _text(_value(payload, "created_by")) or submitted_by
    intake_timestamp = now_utc()

    db.conn.execute(
        """INSERT INTO project_intake
           (project_id, project_key, project_status, current_revision_no,
            input_schema_version, intake_channel, created_by, updated_by,
            last_submitted_at, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET
             project_status=excluded.project_status,
             current_revision_no=excluded.current_revision_no,
             input_schema_version=excluded.input_schema_version,
             intake_channel=excluded.intake_channel,
             updated_by=excluded.updated_by,
             last_submitted_at=excluded.last_submitted_at,
             metadata_json=excluded.metadata_json,
             updated_at=CURRENT_TIMESTAMP""",
        (project_id, project_key, values["project_status"], revision_no, PROJECT_SCHEMA_VERSION, values["intake_channel"], created_by, submitted_by, intake_timestamp, json.dumps({"schema_version": PROJECT_SCHEMA_VERSION}, ensure_ascii=False)),
    )
    if existing:
        db.conn.execute(
            "UPDATE project_application SET status='superseded', updated_at=CURRENT_TIMESTAMP WHERE project_id=? AND status<>'superseded'",
            (project_id,),
        )
    application_status = {
        "draft": "draft",
        "submitted": "submitted",
        "under_review": "under_review",
        "completed": "approved",
        "archived": "superseded",
    }[values["project_status"]]
    db.conn.execute(
        """INSERT INTO project_intake_submission
           (submission_id, project_id, revision_no, schema_version,
            submission_channel, submitted_by, raw_payload_json, payload_sha256,
            validation_status, validation_errors_json, warnings_json, validated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?)""",
        (submission_id, project_id, revision_no, PROJECT_SCHEMA_VERSION, values["intake_channel"], submitted_by, raw_payload, payload_sha256, validation_status, json.dumps(warnings, ensure_ascii=False), intake_timestamp),
    )
    db.conn.execute(
        """INSERT INTO project_application
           (application_id, project_id, revision_no, project_name, business_type,
            permit_type, applicant_name, applicant_type, corporate_name,
            contractor_name, source_submission_id, status, validation_status,
            validation_errors_json, warnings_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)""",
        (application_id, project_id, revision_no, values["project_name"], values["business_type"], values["permit_type"], values["applicant_name"], values["applicant_type"], values["corporate_name"], values["contractor_name"], submission_id, application_status, validation_status, json.dumps(warnings, ensure_ascii=False)),
    )

    site_place_id = None
    site_place: dict[str, Any] | None = None
    building_place_id = None
    building_place: dict[str, Any] | None = None
    site_overrides = _location_overrides(payload, "subject_site")
    building_overrides = _location_overrides(payload, "building_site")
    if values["site_address"]:
        place_id, place = _place_payload(project_id, "subject_site", values["site_address"], site_overrides)
        site_place = place
        site_place_id = _insert_project_place(db, project_id, application_id, place)
        if values["lot_number"]:
            _, lot_place = _place_payload(
                project_id,
                "lot",
                f"{values['site_address']} {values['lot_number']}",
                {**site_overrides, "geo_precision": "parcel"},
            )
            _insert_project_place(db, project_id, application_id, lot_place)
    if values["building_address"]:
        place_id, place = _place_payload(project_id, "building_site", values["building_address"], building_overrides)
        building_place = place
        building_place_id = _insert_project_place(db, project_id, application_id, place)
    if values["grid_connection_point"]:
        grid_location = normalize_address(values["grid_connection_point"])
        if grid_location.status != "unresolved":
            place_id, place = _place_payload(project_id, "grid_connection_point", values["grid_connection_point"], _location_overrides(payload, "grid_connection_point"))
            _insert_project_place(db, project_id, application_id, place)
    site_location = normalize_address(values["site_address"])
    building_location = normalize_address(values["building_address"]) if values["building_address"] else None
    site_place = site_place or {
        "geocode_confidence": site_location.confidence,
        "latitude": None,
        "longitude": None,
        "resolution_method": "project_input_rule",
    }
    building_place = building_place or {
        "geocode_confidence": building_location.confidence if building_location else None,
        "latitude": None,
        "longitude": None,
        "resolution_method": "project_input_rule",
    }
    site_status = _address_resolution_status(site_location, site_place)
    building_status = _address_resolution_status(building_location, building_place) if building_location else "unresolved"
    db.conn.execute(
        """INSERT INTO project_site
           (site_id, application_id, site_address, site_address_normalized,
            lot_number, land_category, building_address,
            building_address_normalized, building_use, site_place_id,
            building_place_id, site_address_type, site_resolution_status,
            site_resolution_method, site_resolution_confidence,
            building_address_type, building_resolution_status,
            building_resolution_method, building_resolution_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            stable_id("project_site", application_id),
            application_id,
            values["site_address"],
            site_location.normalized_address,
            values["lot_number"],
            values["land_category"],
            values["building_address"],
            building_location.normalized_address if building_location else None,
            values["building_use"],
            site_place_id,
            building_place_id,
            _address_type(site_location, has_lot_number=bool(values["lot_number"])),
            site_status,
            site_overrides.get("resolution_method") or site_place.get("resolution_method"),
            site_place.get("geocode_confidence"),
            _address_type(building_location) if building_location else "unknown",
            building_status,
            building_overrides.get("resolution_method") or building_place.get("resolution_method"),
            building_place.get("geocode_confidence"),
        ),
    )
    db.conn.execute(
        """INSERT INTO project_equipment
           (equipment_id, application_id, installed_capacity_kw, module_count,
            module_capacity_w, inverter_count, inverter_capacity_kva,
            installation_height_m, installation_area_sqm)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_equipment", application_id), application_id, values["installed_capacity_kw"], values["module_count"], values["module_capacity_w"], values["inverter_count"], values["inverter_capacity_kva"], values["installation_height_m"], values["installation_area_sqm"]),
    )
    db.conn.execute(
        """INSERT INTO project_finance
           (finance_id, application_id, total_project_cost_krw,
            construction_cost_per_kw, annual_generation_mwh,
            annual_transmission_mwh, lease_fee_krw, resident_revenue_share)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_finance", application_id), application_id, values["total_project_cost_krw"], values["construction_cost_per_kw"], values["annual_generation_mwh"], values["annual_transmission_mwh"], values["lease_fee_krw"], values["resident_revenue_share"]),
    )
    db.conn.execute(
        """INSERT INTO project_schedule
           (schedule_id, application_id, permit_application_date,
            permit_date, construction_start_date, expected_completion_date,
            business_start_date, operation_period_years, schedule_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_schedule", application_id), application_id, values["permit_application_date"], values["permit_date"], values["construction_start_date"], values["expected_completion_date"], values["business_start_date"], values["operation_period_years"], _schedule_status(values, warnings)),
    )
    db.conn.execute(
        """INSERT INTO project_grid
           (grid_id, application_id, grid_connection_point,
            connection_voltage_v, transformer_info, power_purchase_method,
            connection_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_grid", application_id), application_id, values["grid_connection_point"], values["connection_voltage_v"], values["transformer_info"], values["power_purchase_method"], _connection_status(values)),
    )
    db.conn.execute(
        """INSERT INTO project_permit_checklist
           (permit_checklist_id, application_id, development_permit_required,
            urban_management_plan_required, construction_plan_report,
            environmental_assessment_required, structural_safety_review,
            checklist_status, checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_permit_checklist", application_id), application_id, values["development_permit_required"], values["urban_management_plan_required"], values["construction_plan_report"], values["environmental_assessment_required"], values["structural_safety_review"], _checklist_status(values), now_utc() if _checklist_status(values) == "completed" else None),
    )
    db.conn.execute(
        """INSERT INTO project_resident_risk
           (resident_risk_id, application_id, resident_consent_required,
            construction_consent, complaint_occurred, complaint_stop_commitment,
            removal_commitment, complaint_type, risk_status, complaint_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (stable_id("project_resident_risk", application_id), application_id, values["resident_consent_required"], values["construction_consent"], values["complaint_occurred"], values["complaint_stop_commitment"], values["removal_commitment"], values["complaint_type"], _risk_status(values), _text(_value(payload, "complaint_source"))),
    )

    attachment_ids: dict[str, list[str]] = {}
    for document_type in ATTACHMENT_TYPES:
        for index, raw_item in enumerate(_attachment_items(payload, document_type), 1):
            item = raw_item if isinstance(raw_item, dict) else {"storage_uri": str(raw_item)}
            sha256 = _text(item.get("sha256"))
            file_name = _text(item.get("file_name") or item.get("filename") or item.get("name"))
            attachment_id = stable_id("project_attachment", application_id, document_type, sha256 or file_name or index)
            attachment_ids.setdefault(document_type, []).append(attachment_id)
            file_size_bytes = _safe_int(item.get("file_size_bytes")) if item.get("file_size_bytes") is not None else None
            extraction_status = _text(item.get("extraction_status")) or "not_started"
            document_date = _iso_date(item.get("document_date")) if item.get("document_date") else None
            page_count = _safe_int(item.get("page_count")) if item.get("page_count") is not None else None
            if page_count is not None and page_count < 0:
                raise ValueError(f"{document_type}.page_count cannot be negative")
            ocr_used = _boolean(item.get("ocr_used")) if item.get("ocr_used") is not None else None
            required_flag = _boolean(item.get("is_required")) if item.get("is_required") is not None else int(document_type in ATTACHMENT_REQUIRED_TYPES)
            db.conn.execute(
                """INSERT INTO project_attachment
                   (attachment_id, application_id, document_type, file_name,
                    storage_uri, mime_type, sha256, file_size_bytes,
                    extraction_status, source_document_id, source_url, document_date,
                    page_count, is_required, extraction_started_at, extracted_at,
                    extractor_name, extractor_version, extraction_error,
                    content_text_uri, text_sha256, ocr_used, uploaded_by,
                    metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attachment_id,
                    application_id,
                    document_type,
                    file_name,
                    _text(item.get("storage_uri") or item.get("path") or item.get("url")),
                    _text(item.get("mime_type")),
                    sha256,
                    file_size_bytes,
                    extraction_status,
                    item.get("source_document_id"),
                    _text(item.get("source_url") or item.get("url")),
                    document_date,
                    page_count,
                    required_flag,
                    _text(item.get("extraction_started_at")),
                    _text(item.get("extracted_at")),
                    _text(item.get("extractor_name")),
                    _text(item.get("extractor_version")),
                    _text(item.get("extraction_error")),
                    _text(item.get("content_text_uri")),
                    _text(item.get("text_sha256")),
                    ocr_used,
                    _text(item.get("uploaded_by")),
                    json.dumps({"input_index": index}, ensure_ascii=False),
                ),
            )
            extracted_facts = item.get("extracted_facts") or []
            if isinstance(extracted_facts, dict):
                extracted_facts = [{"field_name": key, "value": value} for key, value in extracted_facts.items()]
            for fact_index, fact in enumerate(extracted_facts, 1):
                if not isinstance(fact, dict) or not fact.get("field_name"):
                    continue
                source_page = _safe_int(fact.get("source_page")) if fact.get("source_page") is not None else None
                source_char_start = _safe_int(fact.get("source_char_start")) if fact.get("source_char_start") is not None else None
                source_char_end = _safe_int(fact.get("source_char_end")) if fact.get("source_char_end") is not None else None
                _insert_fact(
                    db,
                    application_id,
                    str(fact["field_name"]),
                    fact.get("value"),
                    source_kind="attachment",
                    source_field=str(fact["field_name"]),
                    source_attachment_id=attachment_id,
                    source_artifact_id=_text(fact.get("source_artifact_id")),
                    source_document_id=_text(fact.get("source_document_id")) or item.get("source_document_id"),
                    source_paragraph_id=_text(fact.get("source_paragraph_id")),
                    source_page=source_page,
                    source_char_start=source_char_start,
                    source_char_end=source_char_end,
                    source_excerpt=_text(fact.get("source_excerpt")),
                    extraction_method=_text(fact.get("extraction_method")) or "attachment_extraction",
                    extraction_model=_text(fact.get("extraction_model")) or _text(item.get("extractor_name")),
                    extraction_version=_text(fact.get("extraction_version")) or _text(item.get("extractor_version")),
                    confidence=_safe_confidence(fact.get("confidence")),
                    fact_identity=fact_index,
                )

    for field_name, value in values.items():
        if field_name not in TEXT_FIELDS | NUMERIC_FIELDS.keys() | BOOLEAN_FIELDS | DATE_FIELDS:
            continue
        _insert_fact(db, application_id, field_name, value, source_field=field_name, extraction_version=PROJECT_SCHEMA_VERSION, confidence=1.0)

    for stage_code, stage_order in STAGES:
        status, planned_date, actual_date, required_flag, notes = _stage_state(values, stage_code)
        source_field = _stage_source_field(values, stage_code)
        source_fact_id = _user_fact_id(application_id, source_field) if source_field else None
        db.conn.execute(
            """INSERT INTO project_stage
               (stage_id, application_id, stage_code, stage_order,
                stage_status, planned_date, actual_date, required_flag,
                notes, source_kind, source_field, source_fact_id, confidence,
                last_evaluated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_input', ?, ?, ?, ?)""",
            (stable_id("project_stage", application_id, stage_code), application_id, stage_code, stage_order, status, planned_date, actual_date, required_flag, notes, source_field, source_fact_id, 1.0 if source_field else None, now_utc()),
        )

    # Keep explicit milestones in addition to the current stage snapshot.
    if values["permit_application_date"]:
        _insert_stage_event(db, application_id, "application", "permit_application_submitted", event_status="completed", event_date=values["permit_application_date"], title="발전사업 신청", source_field="permit_application_date", source_fact_id=_user_fact_id(application_id, "permit_application_date"))
    if values["permit_date"]:
        _insert_stage_event(db, application_id, "power_generation_permit", "power_generation_permit_issued", event_status="completed", event_date=values["permit_date"], title="발전사업허가", source_field="permit_date", source_fact_id=_user_fact_id(application_id, "permit_date"))
    if any(values[field] == 1 for field in ("development_permit_required", "urban_management_plan_required", "environmental_assessment_required")):
        _insert_stage_event(db, application_id, "development_environment_review", "development_environment_review_required", title="개발행위·환경 검토 대상", source_field="permit_checklist")
    if values["resident_consent_required"] == 1 and values["construction_consent"] == 1:
        _insert_stage_event(db, application_id, "resident_consultation_complaint", "resident_consent_confirmed", event_status="completed", title="주민·공사 동의 확인", source_field="construction_consent", source_fact_id=_user_fact_id(application_id, "construction_consent"))
    if values["complaint_occurred"] == 1:
        _insert_stage_event(db, application_id, "resident_consultation_complaint", "complaint_reported", event_status="reported", title="주민 민원 발생", description=values["complaint_type"], source_field="complaint_occurred", source_fact_id=_user_fact_id(application_id, "complaint_occurred"))
    if values["construction_start_date"]:
        _insert_stage_event(db, application_id, "construction_completion", "construction_started", event_status="completed", event_date=values["construction_start_date"], title="공사 시작", source_field="construction_start_date", source_fact_id=_user_fact_id(application_id, "construction_start_date"))
    if values["expected_completion_date"]:
        _insert_stage_event(db, application_id, "construction_completion", "construction_completion_expected", event_status="planned", event_date=values["expected_completion_date"], title="준공 예정", source_field="expected_completion_date", source_fact_id=_user_fact_id(application_id, "expected_completion_date"))
    if values["business_start_date"]:
        _insert_stage_event(db, application_id, "construction_completion", "business_start_reported", event_status="completed", event_date=values["business_start_date"], title="사업개시 확인", source_field="business_start_date", source_fact_id=_user_fact_id(application_id, "business_start_date"))
        _insert_stage_event(db, application_id, "grid_connection_operation", "business_start_reported", event_status="completed", event_date=values["business_start_date"], title="계통연계·사업개시", source_field="business_start_date", source_fact_id=_user_fact_id(application_id, "business_start_date"))
    elif values["grid_connection_point"]:
        _insert_stage_event(db, application_id, "grid_connection_operation", "grid_connection_point_identified", event_status="planned", title="계통연계 지점 확인", source_field="grid_connection_point", source_fact_id=_user_fact_id(application_id, "grid_connection_point"))

    match_count = _match_historical_cases(db, project_id, application_id, values)
    db.conn.commit()
    return {
        "project_id": project_id,
        "application_id": application_id,
        "revision_no": revision_no,
        "validation_status": validation_status,
        "warnings": warnings,
        "attachment_count": sum(len(items) for items in attachment_ids.values()),
        "historical_case_match_count": match_count,
        "workflow_stage_count": len(STAGES),
        "stage_event_count": int(db.conn.execute("SELECT COUNT(*) FROM project_stage_event WHERE application_id=?", (application_id,)).fetchone()[0]),
    }


def _json_value(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def get_project(db: LuceraDB, project_id: str) -> dict[str, Any] | None:
    root = db.conn.execute("SELECT * FROM project_intake WHERE project_id=?", (project_id,)).fetchone()
    if not root:
        return None
    application = db.conn.execute(
        "SELECT * FROM project_application WHERE project_id=? ORDER BY revision_no DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if not application:
        return {"project": dict(root), "application": None}
    app = dict(application)
    app["warnings"] = _json_value(app.get("warnings_json"), [])
    app["validation_errors"] = _json_value(app.get("validation_errors_json"), [])
    application_id = app["application_id"]

    def one(table: str) -> dict[str, Any] | None:
        row = db.conn.execute(f"SELECT * FROM {table} WHERE application_id=?", (application_id,)).fetchone()
        return dict(row) if row else None

    attachments = [dict(row) for row in db.conn.execute("SELECT * FROM project_attachment WHERE application_id=? ORDER BY document_type, attachment_id", (application_id,)).fetchall()]
    facts = [dict(row) for row in db.conn.execute("SELECT * FROM project_fact WHERE application_id=? ORDER BY fact_id", (application_id,)).fetchall()]
    for fact in facts:
        fact["value"] = fact.get("value_text")
        if fact["value_type"] == "numeric":
            fact["value"] = fact.get("value_numeric")
        elif fact["value_type"] == "date":
            fact["value"] = fact.get("value_date")
        elif fact["value_type"] == "boolean":
            fact["value"] = bool(fact.get("value_boolean")) if fact.get("value_boolean") is not None else None
        elif fact["value_type"] == "json":
            fact["value"] = _json_value(fact.get("value_json"), None)
    stages = [dict(row) for row in db.conn.execute("SELECT * FROM project_stage WHERE application_id=? ORDER BY stage_order", (application_id,)).fetchall()]
    stage_events = [dict(row) for row in db.conn.execute("SELECT * FROM project_stage_event WHERE application_id=? ORDER BY COALESCE(event_date, ''), created_at, stage_code", (application_id,)).fetchall()]
    locations = [dict(row) for row in db.conn.execute("""SELECT l.*, p.normalized_name, p.province, p.city_county, p.eup_myeon, p.ri, p.geo_precision, p.latitude, p.longitude, p.location_status FROM project_location_link l join canonical_place p on p.place_id=l.place_id WHERE l.application_id=?""", (application_id,)).fetchall()]
    matches = [dict(row) for row in db.conn.execute("""SELECT l.*, c.case_key, coalesce(c.canonical_title,c.case_name) canonical_title, c.municipality, c.village, c.address, c.project_name, c.facility_type, c.confidence case_confidence, c.review_status case_review_status FROM project_case_link l join conflict_case c on c.case_id=l.case_id WHERE l.application_id=? ORDER BY l.match_score DESC""", (application_id,)).fetchall()]
    for match in matches:
        match["matching_features"] = _json_value(match.pop("matching_features_json", None), {})
    return {
        "project": dict(root),
        "application": app,
        "site": one("project_site"),
        "equipment": one("project_equipment"),
        "finance": one("project_finance"),
        "schedule": one("project_schedule"),
        "grid": one("project_grid"),
        "permit_checklist": one("project_permit_checklist"),
        "resident_risk": one("project_resident_risk"),
        "attachments": attachments,
        "facts": facts,
        "workflow": stages,
        "stage_events": stage_events,
        "locations": locations,
        "historical_case_matches": matches,
    }


def get_precheck(db: LuceraDB, project_id: str) -> dict[str, Any] | None:
    project = get_project(db, project_id)
    if not project:
        return None
    app = project.get("application") or {}
    checklist = project.get("permit_checklist") or {}
    resident = project.get("resident_risk") or {}
    attachments = {attachment["document_type"] for attachment in project.get("attachments", [])}
    pending_extraction = sorted(
        {
            attachment["document_type"]
            for attachment in project.get("attachments", [])
            if attachment.get("extraction_status") not in {"extracted", "reviewed"}
        }
    )
    missing_documents: list[str] = []
    for document_type in ("business_plan", "funding_plan", "land_use_consent", "land_register", "cadastral_map"):
        if document_type not in attachments:
            missing_documents.append(document_type)
    if checklist.get("structural_safety_review") == 1 and "structural_safety_certificate" not in attachments:
        missing_documents.append("structural_safety_certificate")
    if checklist.get("environmental_assessment_required") == 1 and "land_use_plan" not in attachments:
        missing_documents.append("land_use_plan")
    risk_flags: list[dict[str, Any]] = []
    if resident.get("complaint_occurred") == 1:
        risk_flags.append({"code": "historical_complaint_reported", "severity": "high", "message": "입력된 주민 민원 발생 이력이 있습니다."})
    if resident.get("resident_consent_required") == 1 and resident.get("construction_consent") != 1:
        risk_flags.append({"code": "resident_consent_unconfirmed", "severity": "high", "message": "주민동의가 필요하지만 공사동의가 확인되지 않았습니다."})
    if checklist.get("environmental_assessment_required") == 1:
        risk_flags.append({"code": "environmental_review_required", "severity": "medium", "message": "환경영향 검토 대상 여부가 입력되었습니다."})
    if checklist.get("structural_safety_review") == 1:
        risk_flags.append({"code": "structural_safety_review_required", "severity": "medium", "message": "구조안전 검토 대상 여부가 입력되었습니다."})
    return {
        "project_id": project_id,
        "application_id": app.get("application_id"),
        "workflow": project.get("workflow", []),
        "risk_flags": risk_flags,
        "missing_recommended_documents": sorted(set(missing_documents)),
        "attachments_pending_extraction": pending_extraction,
        "historical_case_matches": project.get("historical_case_matches", []),
        "notice": "이 결과는 입력정보와 공개 근거를 연결한 사전점검 자료이며 인허가 결과를 확정하지 않습니다.",
    }
