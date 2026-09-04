"""Local, evidence-grounded RAG and siting analysis.

This module intentionally works without an external LLM.  The retrieval layer
uses the existing meeting-segment search, the rule layer performs numeric and
versioned checks, and the local answer generator renders a conservative
Korean report.  A future AI provider can replace only the answer generator
after receiving the same evidence pack.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Iterable

from .answer import MAX_REASONS, ClaudeAnswerGenerator, _short_body
import config

from .location import Location, normalize_address
from .search import SearchService, haversine_m
from .vworld import VWorldClient
from .yeongam import require_yeongam, scope_city_county


ISSUE_LABELS = {
    "landscape_damage": "경관·조망",
    "noise_living_discomfort": "소음·생활 불편",
    "agricultural_land_damage": "농지·토지",
    "siting_permit_regulatory": "입지·인허가·규제",
    "communication_procedure": "소통·절차",
    "glare_reflection": "빛반사·눈부심",
    "external_benefit_distribution": "편익·보상",
    "safety_environment": "환경·안전",
    "grid_connection": "계통·접속",
}

ISSUE_QUERY_TERMS = {
    "landscape_damage": ("경관", "조망", "경관훼손"),
    "noise_living_discomfort": ("소음", "생활불편", "소음피해"),
    "agricultural_land_damage": ("농지", "농지훼손", "토지피해"),
    "siting_permit_regulatory": ("인허가", "개발행위", "이격거리", "규제"),
    "communication_procedure": ("주민협의", "주민설명회", "소통", "협의"),
    "glare_reflection": ("빛반사", "반사광", "눈부심"),
    "external_benefit_distribution": ("보상", "수익배분", "주민참여"),
    "safety_environment": ("환경", "안전", "배수", "침수", "산사태"),
    "grid_connection": ("계통", "접속", "변전소"),
}

PROCESS_LABELS = {
    "complaint_received": "민원 접수·제기",
    "inquiry_or_request": "질의·요청",
    "investigation_or_review": "조사·검토",
    "resident_consultation": "주민 설명·협의",
    "administrative_response": "행정 답변·대응",
    "permit_or_authorization": "인허가·심의",
    "decision_or_disposition": "결정·처분",
    "mitigation_or_action": "보완·조치",
    "follow_up_or_recurrence": "후속·재발",
}

CONCLUSION_LABELS = {
    "review_required": "설치 재검토 필요",
    "conditional_review": "조건부 검토",
    "caution": "주의해서 검토",
    "no_material_risk_found": "현재 자료에서 주요 위험 미확인",
    "insufficient_evidence": "판단 보류",
}


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(str(value).replace(",", "").replace("㎡", "").replace("m²", "").strip())
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _first_float(patterns: Iterable[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if not match:
            continue
        value = _float(match.group(1))
        if value is None:
            continue
        unit = (match.group(2) or "").lower()
        if unit in {"mw", "메가와트"}:
            value *= 1000
        if unit in {"평", "pyeong"}:
            value *= 3.305785
        return value
    return None


def _extract_address(message: str) -> str | None:
    # Prefer an explicitly supplied address. This fallback is only for a
    # Korean demo chat message and never claims to be a full address parser.
    match = re.search(
        r"((?:전라남도|전남|광주광역시|광주)\s*[가-힣]+(?:시|군|구)(?:\s*[가-힣0-9]+(?:읍|면|동))?(?:\s*[가-힣0-9]+리)?(?:\s*\d+(?:-\d+)?)?)",
        message or "",
    )
    return " ".join(match.group(1).split()) if match else None


def normalize_chat_input(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("chat payload must be an object")
    message = " ".join(str(payload.get("message") or "").split())
    address = " ".join(str(payload.get("address") or "").split()) or _extract_address(message)
    if not address:
        raise ValueError("address is required")

    site_area = _float(payload.get("site_area_sqm") or payload.get("site_area"))
    installation_area = _float(payload.get("installation_area_sqm") or payload.get("installation_area"))
    capacity = _float(payload.get("capacity_kw") or payload.get("installed_capacity_kw"))
    if site_area is None:
        site_area = _first_float(
            (r"(?:부지|사업부지|전체부지|부지면적)\s*[:：]?\s*([\d,.]+)\s*(㎡|m2|m²|평)?", r"(?:면적)\s*[:：]?\s*([\d,.]+)\s*(㎡|m2|m²|평)?"),
            message,
        )
    if installation_area is None:
        installation_area = _first_float(
            (r"(?:설치|패널|모듈)\s*면적\s*[:：]?\s*([\d,.]+)\s*(㎡|m2|m²|평)?",),
            message,
        )
    if capacity is None:
        capacity = _first_float(
            (r"(?:설비용량|설치용량|용량)\s*[:：]?\s*([\d,.]+)\s*(kW|MW|킬로와트|메가와트)?", r"([\d,.]+)\s*(kW|MW)"),
            message,
        )

    latitude = _float(payload.get("latitude"))
    longitude = _float(payload.get("longitude"))
    normalized = {
        "message": message,
        "address": address,
        "site_area_sqm": site_area,
        "installation_area_sqm": installation_area,
        "capacity_kw": capacity,
        "latitude": latitude,
        "longitude": longitude,
        "nearest_residence_m": _float(payload.get("nearest_residence_m")),
        "nearest_road_m": _float(payload.get("nearest_road_m")),
        "radius_m": _float(payload.get("radius_m")) or 5_000,
        "resolve_address": bool(payload.get("resolve_address", False)),
        "review_mode": str(payload.get("review_mode") or "all"),
        "include_comparative": bool(payload.get("include_comparative", True)),
        "include_map_context": bool(payload.get("include_map_context", True)),
        "scope": str(payload.get("scope") or "yeongam"),
        "image": payload.get("image"),
        # Rule validity is evaluated against this date so a demo can be pinned
        # to a fixed day and still show which rules are not yet in force.
        "as_of": str(payload.get("as_of") or date.today().isoformat()),
    }
    for key in ("site_area_sqm", "installation_area_sqm", "capacity_kw", "nearest_residence_m", "nearest_road_m", "radius_m"):
        value = normalized[key]
        if value is not None and value < 0:
            raise ValueError(f"{key} cannot be negative")
    if normalized["radius_m"] <= 0 or normalized["radius_m"] > 50_000:
        raise ValueError("radius_m must be between 1 and 50000")
    scope_city_county(normalized["scope"])
    return normalized


def _region_code(db: Any, location: Location) -> str | None:
    if not location.city_county:
        return None
    row = db.conn.execute(
        """SELECT region_code FROM administrative_region
           WHERE region_name=? OR region_name LIKE ?
           ORDER BY CASE WHEN region_name=? THEN 0 ELSE 1 END LIMIT 1""",
        (location.city_county, f"%{location.city_county}", location.city_county),
    ).fetchone()
    return str(row[0]) if row else None


def _apply_operator(operator: str, observed: float | None, threshold: float | None) -> bool | None:
    if operator == "exists":
        return observed is not None
    if observed is None or threshold is None:
        return None
    return {
        "gte": observed >= threshold,
        "gt": observed > threshold,
        "lte": observed <= threshold,
        "lt": observed < threshold,
        "eq": observed == threshold,
    }.get(operator)


def _rule_check(
    *,
    rule_id: str,
    rule_name: str,
    rule_description: str | None,
    operator: str,
    threshold: float | None,
    unit: str | None,
    observed: float | None,
    severity: str,
    data_origin: str,
    source_title: str | None,
    source_article: str | None,
) -> dict[str, Any]:
    passed = _apply_operator(operator, observed, threshold)
    if passed is None:
        status = "check_required"
        reason = f"{rule_name}을 계산할 입력값이 부족합니다."
    elif passed:
        status = "pass"
        reason = f"{rule_name} 기준을 충족합니다."
    else:
        status = "fail"
        reason = f"{rule_name} 기준을 충족하지 않습니다."
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "description": rule_description,
        "status": status,
        "observed_value": observed,
        "threshold_value": threshold,
        "unit": unit,
        "operator": operator,
        "severity": severity,
        "reason": reason,
        "source": {"title": source_title, "article": source_article, "data_origin": data_origin},
    }


OPERATING_STATUS_TERMS = ("정상가동", "개시", "운영", "완료")
NON_OPERATING_STATUS_TERMS = ("중단", "폐기", "중지", "취소")

# The permit register records 설치면적 (the installed array), not 부지면적.
# Keep the two comparisons apart: one is measured against real permits, the
# other against a published planning figure, and they differ by roughly a factor
# of two. Mixing them would flag ordinary projects as anomalies.
SITE_AREA_REFERENCE_MIN = 10.0
SITE_AREA_REFERENCE_MAX = 30.0
SITE_AREA_REFERENCE_SOURCE = "지상 태양광 부지 소요 통상 범위(기획서 4-3)"
# Guard the benchmark against register noise: rows with 1㎡/kW or 1,000㎡/kW are
# unit or entry errors, not real layouts.
BENCHMARK_RATIO_MIN = 2.0
BENCHMARK_RATIO_MAX = 60.0
BENCHMARK_MIN_SAMPLE = 30


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile of an empty sample")
    index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
    return values[index]


def _installation_area_benchmark(db: Any, location: Location) -> dict[str, Any] | None:
    """Installed-area per kW distribution from the permit register.

    Prefer the county the site is in; fall back to the province, then to every
    loaded row. Returns None when no scope has enough clean samples, so the
    caller reports "비교 기준 없음" instead of comparing against noise.
    """

    scopes: list[tuple[str, str, tuple[Any, ...]]] = []
    if location.city_county:
        scopes.append((location.city_county, "city_county=?", (location.city_county,)))
    if location.province:
        scopes.append((location.province, "province=?", (location.province,)))
    scopes.append(("적재된 전체 허가", "1=1", ()))

    for label, where, params in scopes:
        rows = db.conn.execute(
            f"""SELECT capacity_kw, metadata_json FROM permit_project
                 WHERE {where} AND capacity_kw IS NOT NULL AND capacity_kw > 0""",
            params,
        ).fetchall()
        ratios: list[float] = []
        for row in rows:
            try:
                area = json.loads(row["metadata_json"] or "{}").get("installation_area_sqm")
            except (TypeError, ValueError):
                continue
            area_value = _float(area)
            if not area_value:
                continue
            ratio = area_value / float(row["capacity_kw"])
            if BENCHMARK_RATIO_MIN <= ratio <= BENCHMARK_RATIO_MAX:
                ratios.append(ratio)
        if len(ratios) >= BENCHMARK_MIN_SAMPLE:
            ratios.sort()
            return {
                "scope": label,
                "sample_size": len(ratios),
                "p10": round(_percentile(ratios, 0.10), 2),
                "p25": round(_percentile(ratios, 0.25), 2),
                "median": round(_percentile(ratios, 0.50), 2),
                "p75": round(_percentile(ratios, 0.75), 2),
                "p90": round(_percentile(ratios, 0.90), 2),
                "filter": f"{BENCHMARK_RATIO_MIN:g}~{BENCHMARK_RATIO_MAX:g} ㎡/kW",
            }
    return None


# 「농지법」 and 「산지관리법」 attach a conversion permit to the 지목 recorded in
# the cadastre, so the category alone decides which one to check for. This is a
# lookup, not a judgement.
FARMLAND_CATEGORIES = ("전", "답", "과수원", "목장용지")
FOREST_CATEGORIES = ("임야",)
# Zones where a development permit meets an additional preservation test.
RESTRICTIVE_ZONES = ("보전녹지", "자연환경보전", "농림지역", "보전관리")
JIBUN_CATEGORY_RE = re.compile(r"([가-힣]+)\s*$")


def _land_category(parcel: dict[str, Any]) -> str | None:
    """Read the 지목 out of the cadastral record's `jibun` field ("1 답")."""

    for field in ("jibun", "bonbun"):
        match = JIBUN_CATEGORY_RE.search(str(parcel.get(field) or "").strip())
        if match:
            return match.group(1)
    return None


def _zone_names(layers: list[dict[str, Any]], scope: str) -> list[str]:
    names: list[str] = []
    for layer in layers:
        if layer.get("scope") != scope:
            continue
        for feature in layer.get("features") or []:
            name = str(feature.get("uname") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _map_context_checks(map_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the cadastral and zoning records into checks.

    These are facts held by an official register, so they may carry numbers and
    conclusions — unlike anything read off the aerial image, which stays
    qualitative.
    """

    checks: list[dict[str, Any]] = []
    source = {"title": "국토교통부 VWorld 공간정보 오픈플랫폼", "article": None, "data_origin": "public_api"}

    parcel = map_context.get("parcel") or {}
    category = _land_category(parcel)
    if category:
        if category in FARMLAND_CATEGORIES:
            status, severity = "check_required", "high"
            reason = (
                f"지적도상 지목이 '{category}'입니다. 「농지법」상 농지에 해당하므로 "
                "농지전용허가 대상인지 확인해야 합니다."
            )
        elif category in FOREST_CATEGORIES:
            status, severity = "check_required", "high"
            reason = (
                f"지적도상 지목이 '{category}'입니다. 「산지관리법」상 산지에 해당하므로 "
                "산지전용허가와 경사도·표고 기준을 확인해야 합니다."
            )
        else:
            status, severity = "pass", "low"
            reason = f"지적도상 지목이 '{category}'로, 농지·산지 전용허가 대상 지목은 아닙니다."
        checks.append(
            {
                "rule_id": "cadastre-land-category",
                "rule_name": "지적도상 지목",
                "description": f"연속지적도 {parcel.get('pnu') or ''}".strip(),
                "status": status,
                "observed_value": category,
                "threshold_value": None,
                "unit": None,
                "operator": "lookup",
                "severity": severity,
                "rule_kind": "site_check",
                "effective_from": None,
                "reason": reason,
                "source": source,
            }
        )

    layers = map_context.get("layers") or []
    site_zones = _zone_names(layers, "site")
    if site_zones:
        restrictive = [zone for zone in site_zones if any(term in zone for term in RESTRICTIVE_ZONES)]
        checks.append(
            {
                "rule_id": "zoning-site",
                "rule_name": "부지의 용도지역",
                "description": "국토계획 용도지역 도면에서 확인한 값",
                "status": "check_required" if restrictive else "pass",
                "observed_value": ", ".join(site_zones),
                "threshold_value": None,
                "unit": None,
                "operator": "lookup",
                "severity": "high" if restrictive else "medium",
                "rule_kind": "site_check",
                "effective_from": None,
                "reason": (
                    f"부지가 {', '.join(restrictive)}에 걸쳐 있습니다. 개발행위허가에서 "
                    "보전 목적 기준을 함께 검토해야 합니다."
                    if restrictive
                    else f"부지의 용도지역은 {', '.join(site_zones)}입니다. 해당 용도지역의 "
                         "행위제한과 지자체 조례를 함께 확인하십시오."
                ),
                "source": source,
            }
        )

    nearby_zones = _zone_names(layers, "nearby")
    residential = [zone for zone in nearby_zones if "주거" in zone]
    if nearby_zones:
        checks.append(
            {
                "rule_id": "zoning-nearby-residential",
                "rule_name": "반경 내 주거지역 포함 여부",
                "description": "이격거리 검토가 필요한지 판단하는 신호",
                "status": "check_required" if residential else "pass",
                # Presence within the queried radius, never a distance: the API
                # returns polygons intersecting the buffer, not their distance.
                "observed_value": ", ".join(residential) if residential else "없음",
                "threshold_value": None,
                "unit": None,
                "operator": "lookup",
                "severity": "high" if residential else "low",
                "rule_kind": "site_check",
                "effective_from": None,
                "reason": (
                    f"반경 {config.VWORLD_NEARBY_BUFFER_M}m 안의 용도지역에 "
                    f"{', '.join(residential)}이 포함되어 있습니다. 주거지 이격거리 기준 검토 대상이며, "
                    "정확한 거리는 현장 또는 지적 대조로 확인해야 합니다."
                    if residential
                    else f"반경 {config.VWORLD_NEARBY_BUFFER_M}m 안의 용도지역에서 주거지역은 확인되지 않았습니다. "
                         "주거지역 밖의 개별 주택은 이 도면에 나타나지 않으므로 별도 확인이 필요합니다."
                ),
                "source": source,
            }
        )
    return checks


def _rule_metadata(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError, IndexError, KeyError):
        return {}
    return value if isinstance(value, dict) else {}


def _rule_source(row: Any) -> dict[str, Any]:
    return {
        "title": row["source_title"],
        "article": row["source_article"],
        "data_origin": row["data_origin"],
    }


def _cap_check(row: Any, observed: float | None, as_of: str) -> dict[str, Any]:
    """A national cap bounds what an ordinance may demand; it is not a site test.

    Only one thing can be decided from the cap alone: a site already further away
    than the ceiling cannot be refused on distance grounds by any ordinance.
    Anything closer depends on the local article, so it stays `check_required`
    instead of being reported as a failure.
    """

    cap = _float(row["threshold_value"])
    effective = not row["valid_from"] or str(row["valid_from"]) <= as_of
    if observed is None:
        status = "check_required"
        reason = f"{row['rule_name']}: 가장 가까운 주거지까지의 거리를 입력하면 조례 기준과 대조할 수 있습니다."
    elif cap is not None and observed >= cap:
        status = "pass"
        reason = (
            f"가장 가까운 주거지까지 {observed:,.0f}m로, 조례가 요구할 수 있는 "
            f"최대 거리 {cap:,.0f}m 이상입니다. 이격거리만을 이유로 한 반려는 성립하기 어렵습니다."
        )
    else:
        status = "check_required"
        reason = (
            f"가장 가까운 주거지까지 {observed:,.0f}m입니다. 조례는 최대 {cap:,.0f}m까지 "
            "정할 수 있으므로, 해당 시군 조례의 실제 기준값을 확인해야 충족 여부가 결정됩니다."
        )
    if not effective:
        reason += f" (이 상한은 {row['valid_from']} 시행 예정입니다.)"
    return {
        "rule_id": row["rule_id"],
        "rule_name": row["rule_name"],
        "description": row["rule_description"],
        "status": status,
        "observed_value": observed,
        "threshold_value": cap,
        "unit": row["unit"],
        "operator": "cap",
        "severity": row["severity"],
        "reason": reason,
        "source": _rule_source(row),
    }


def _prohibited_criterion_check(row: Any, as_of: str) -> dict[str, Any]:
    effective = not row["valid_from"] or str(row["valid_from"]) <= as_of
    if effective:
        reason = f"{row['rule_name']}: 이 기준은 조례로 정할 수 없으므로 반려 사유가 되지 않습니다."
        status = "pass"
    else:
        reason = (
            f"{row['rule_name']}: {row['valid_from']} 시행 예정입니다. "
            "그 전까지는 기존 조례 조항이 유효하므로 현행 조문을 확인해야 합니다."
        )
        status = "check_required"
    return {
        "rule_id": row["rule_id"],
        "rule_name": row["rule_name"],
        "description": row["rule_description"],
        "status": status,
        "observed_value": None,
        "threshold_value": None,
        "unit": row["unit"],
        "operator": "prohibited",
        "severity": row["severity"],
        "reason": reason,
        "source": _rule_source(row),
    }


class SitingRuleEngine:
    """Calculate objective checks; it never emits a legal final decision."""

    def __init__(self, db: Any):
        self.db = db

    def evaluate(
        self, data: dict[str, Any], location: Location, map_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = _map_context_checks(map_context or {})
        site_area = data.get("site_area_sqm")
        installation_area = data.get("installation_area_sqm")
        capacity = data.get("capacity_kw")

        if site_area is not None and installation_area is not None:
            checks.append(
                _rule_check(
                    rule_id="input-area-relation",
                    rule_name="설치면적이 부지면적을 초과하지 않는지",
                    rule_description="사용자가 입력한 두 면적의 구조 검산",
                    operator="lte",
                    threshold=site_area,
                    unit="㎡",
                    observed=installation_area,
                    severity="high",
                    data_origin="user_input",
                    source_title="사용자 입력",
                    source_article=None,
                )
            )
        elif site_area is None:
            checks.append(
                _rule_check(
                    rule_id="input-site-area-required",
                    rule_name="부지면적 입력",
                    rule_description="면적 대비 규모 검토를 위한 입력",
                    operator="exists",
                    threshold=None,
                    unit="㎡",
                    observed=None,
                    severity="medium",
                    data_origin="user_input",
                    source_title="사용자 입력",
                    source_article=None,
                )
            )

        if site_area is not None and installation_area:
            coverage = installation_area / site_area
            checks.append(
                {
                    "rule_id": "site-coverage-ratio",
                    "rule_name": "부지 이용률(설치면적 / 부지면적)",
                    "description": "개발행위·배수·경관 검토 규모를 가늠하는 입력 검산",
                    "status": "pass" if coverage <= 1 else "fail",
                    "observed_value": round(coverage, 3),
                    "threshold_value": 1.0,
                    "unit": "비율",
                    "operator": "lte",
                    "severity": "medium",
                    "rule_kind": "site_check",
                    "effective_from": None,
                    "reason": (
                        f"부지 {site_area:,.0f}㎡ 중 설치면적이 {installation_area:,.0f}㎡로 "
                        f"이용률은 약 {coverage * 100:.0f}%입니다."
                    ),
                    "source": {"title": "사용자 입력", "article": None, "data_origin": "user_input"},
                }
            )

        if site_area is not None and capacity:
            ratio = site_area / capacity
            status = "pass" if SITE_AREA_REFERENCE_MIN <= ratio <= SITE_AREA_REFERENCE_MAX else "check_required"
            checks.append(
                {
                    "rule_id": "site-area-per-kw-advisory",
                    "rule_name": "설비용량 대비 부지면적 검산",
                    "description": "법적 허가 기준이 아닌 배치 규모 사전 점검",
                    "status": status,
                    "observed_value": round(ratio, 2),
                    "threshold_value": {"min": SITE_AREA_REFERENCE_MIN, "max": SITE_AREA_REFERENCE_MAX},
                    "unit": "㎡/kW",
                    "operator": "range",
                    "severity": "medium",
                    "rule_kind": "site_check",
                    "effective_from": None,
                    "reason": (
                        f"용량 대비 부지면적은 약 {ratio:.1f}㎡/kW로 통상 범위 "
                        f"({SITE_AREA_REFERENCE_MIN:g}~{SITE_AREA_REFERENCE_MAX:g}㎡/kW) 안입니다."
                        if status == "pass"
                        else f"용량 대비 부지면적이 약 {ratio:.1f}㎡/kW로 통상 범위 "
                             f"({SITE_AREA_REFERENCE_MIN:g}~{SITE_AREA_REFERENCE_MAX:g}㎡/kW)를 벗어나 배치·이격 검토가 필요합니다."
                    ),
                    "source": {"title": SITE_AREA_REFERENCE_SOURCE, "article": None, "data_origin": "reference"},
                }
            )

        if installation_area and capacity:
            ratio = installation_area / capacity
            benchmark = _installation_area_benchmark(self.db, location)
            if benchmark:
                low, high = benchmark["p10"], benchmark["p90"]
                inside = low <= ratio <= high
                checks.append(
                    {
                        "rule_id": "installation-area-per-kw-benchmark",
                        "rule_name": "설비용량 대비 설치면적 — 실제 허가 분포 대조",
                        "description": (
                            f"{benchmark['scope']} 허가 원장 {benchmark['sample_size']:,}건의 "
                            f"설치면적/용량 분포와 비교 (필터 {benchmark['filter']})"
                        ),
                        "status": "pass" if inside else "check_required",
                        "observed_value": round(ratio, 2),
                        "threshold_value": {"p10": low, "median": benchmark["median"], "p90": high},
                        "unit": "㎡/kW",
                        "operator": "range",
                        "severity": "medium",
                        "rule_kind": "site_check",
                        "effective_from": None,
                        "reason": (
                            f"입력값은 {ratio:.1f}㎡/kW입니다. {benchmark['scope']} 실제 허가의 "
                            f"중앙값은 {benchmark['median']}㎡/kW(하위 10% {low}, 상위 10% {high})로, "
                            + ("통상 분포 안에 들어갑니다." if inside else "분포를 벗어나 입력값 또는 배치 계획을 확인해야 합니다.")
                        ),
                        "source": {
                            "title": "공공데이터포털 태양광발전시설 현황(허가 원장)",
                            "article": None,
                            "data_origin": "public_api",
                        },
                    }
                )
            else:
                checks.append(
                    {
                        "rule_id": "installation-area-benchmark-unavailable",
                        "rule_name": "설비용량 대비 설치면적 — 비교 기준 없음",
                        "description": "허가 원장에 설치면적 값이 부족해 분포 비교를 하지 않습니다.",
                        "status": "check_required",
                        "observed_value": round(ratio, 2),
                        "threshold_value": None,
                        "unit": "㎡/kW",
                        "operator": "range",
                        "severity": "low",
                        "rule_kind": "site_check",
                        "effective_from": None,
                        "reason": (
                            f"입력값은 {ratio:.1f}㎡/kW이지만, 이 지역 허가 원장에 설치면적이 "
                            "기록된 건이 부족해 실제 분포와 대조하지 못했습니다."
                        ),
                        "source": {"title": None, "article": None, "data_origin": "not_loaded"},
                    }
                )
        elif capacity is None:
            checks.append(
                _rule_check(
                    rule_id="input-capacity-required",
                    rule_name="설비용량 입력",
                    rule_description="면적 대비 규모 검토를 위한 입력",
                    operator="exists",
                    threshold=None,
                    unit="kW",
                    observed=None,
                    severity="medium",
                    data_origin="user_input",
                    source_title="사용자 입력",
                    source_article=None,
                )
            )

        if location.latitude is None or location.longitude is None:
            checks.append(
                {
                    "rule_id": "location-precision",
                    "rule_name": "설치 예정지 좌표 확인",
                    "description": "좌표가 없으면 거리 계산 대신 행정구역·후보 위치로만 검토",
                    "status": "check_required",
                    "observed_value": location.precision,
                    "threshold_value": "coordinate",
                    "unit": None,
                    "operator": "exists",
                    "severity": "medium",
                    "reason": "정확한 좌표가 없어 반경 거리와 필지 단위 비교를 확정할 수 없습니다.",
                    "source": {"title": "주소 해석 결과", "article": None, "data_origin": location.provider},
                }
            )

        region_code = _region_code(self.db, location)
        as_of = str(data.get("as_of") or date.today().isoformat())
        rules = self.db.conn.execute(
            """SELECT * FROM siting_rule
                WHERE active=1 AND (region_code IS NULL OR region_code=?)
                  AND (valid_to IS NULL OR valid_to >= ?)
                ORDER BY CASE WHEN region_code IS NULL THEN 1 ELSE 0 END, rule_id""",
            (region_code, as_of),
        ).fetchall()
        observed_by_object = {
            "residence": data.get("nearest_residence_m"),
            "road": data.get("nearest_road_m"),
        }
        local_ordinance_loaded = False
        for row in rules:
            observed = observed_by_object.get(row["reference_object"])
            metadata = _rule_metadata(row)
            kind = str(metadata.get("rule_kind") or "site_check")
            if kind == "ordinance_cap":
                check = _cap_check(row, observed, as_of)
            elif kind == "prohibited_criterion":
                check = _prohibited_criterion_check(row, as_of)
            else:
                if row["region_code"]:
                    local_ordinance_loaded = True
                check = _rule_check(
                    rule_id=row["rule_id"],
                    rule_name=row["rule_name"],
                    rule_description=row["rule_description"],
                    operator=row["operator"],
                    threshold=row["threshold_value"],
                    unit=row["unit"],
                    observed=observed,
                    severity=row["severity"],
                    data_origin=row["data_origin"],
                    source_title=row["source_title"],
                    source_article=row["source_article"],
                )
            check["effective_from"] = row["valid_from"]
            check["rule_kind"] = kind
            checks.append(check)

        if not local_ordinance_loaded:
            region_label = location.city_county or "해당 시군"
            checks.append(
                {
                    "rule_id": "ordinance-not-loaded",
                    "rule_name": f"{region_label} 조례 이격거리 조문",
                    "description": "조례 원문에서 확인한 기준값만 판정에 사용합니다.",
                    "status": "check_required",
                    "observed_value": observed_by_object.get("residence"),
                    "threshold_value": None,
                    "unit": "m",
                    "operator": "exists",
                    "severity": "high",
                    "rule_kind": "site_check",
                    "effective_from": None,
                    "reason": (
                        f"{region_label} 도시계획 조례의 이격거리 조문이 아직 적재되지 않아 "
                        "충족 여부를 단정할 수 없습니다. 조문을 확인해 siting_rule에 등록하면 "
                        "이 항목이 확정 판정으로 바뀝니다."
                    ),
                    "source": {"title": None, "article": None, "data_origin": "not_loaded"},
                }
            )
        return {
            "checks": checks,
            "region_code": region_code,
            "as_of": as_of,
            "local_ordinance_loaded": local_ordinance_loaded,
            "failed_count": sum(check["status"] == "fail" for check in checks),
            "unknown_count": sum(check["status"] == "check_required" for check in checks),
        }


def _query_terms(message: str) -> tuple[list[str], list[str]]:
    text = message or ""
    terms = ["태양광", "민원"]
    issue_codes: list[str] = []
    for code, synonyms in ISSUE_QUERY_TERMS.items():
        if any(term in text for term in synonyms):
            issue_codes.append(code)
            terms.extend(synonyms[:2])
    if any(word in text for word in ("주민", "협의", "설명회", "동의")):
        terms.extend(("주민", "협의"))
    if any(word in text for word in ("허가", "설치", "가능", "불가", "검토")):
        terms.extend(("허가", "설치"))
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(terms)), issue_codes


def _issue_counts(results: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for result in results:
        for issue in result.get("issues", []):
            code = issue.get("issue_code")
            if code:
                counts[str(code)] += 1
    return counts


def _load_process_events(db: Any, case_ids: set[str]) -> list[dict[str, Any]]:
    if not case_ids:
        return []
    placeholders = ",".join("?" for _ in case_ids)
    rows = db.conn.execute(
        f"""SELECT e.*, COALESCE(c.canonical_title, c.case_name) AS case_title,
                   d.title AS document_title, s.page_from, s.ordinal AS paragraph_order
              FROM case_process_event e
              JOIN conflict_case c ON c.case_id=e.case_id
              LEFT JOIN meeting_segment s ON s.segment_id=e.paragraph_id
              LEFT JOIN source_document d ON d.document_id=s.document_id
             WHERE e.case_id IN ({placeholders})
             ORDER BY COALESCE(e.event_date, ''), e.case_id, e.process_event_id""",
        list(case_ids),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            item["metadata"] = {}
        item["label"] = PROCESS_LABELS.get(item["event_type"], item["event_type"])
        events.append(item)
    return events


def _load_permit_projects(db: Any, data: dict[str, Any], location: Location) -> dict[str, Any]:
    """Load structured permit facts separately from narrative evidence."""

    rows = db.conn.execute(
        """SELECT project_id, facility_name, company_name, capacity_kw,
                  permit_date, operation_status, jibun_address, road_address,
                  province, city_county, eup_myeon, ri, latitude, longitude,
                  location_status, metadata_json
             FROM permit_project
            WHERE city_county=?
            ORDER BY permit_date DESC, project_id""",
        (location.city_county,),
    ).fetchall() if location.city_county else []
    projects: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        distance = None
        distance_status = "unknown"
        if (
            location.latitude is not None
            and location.longitude is not None
            and item.get("latitude") is not None
            and item.get("longitude") is not None
        ):
            distance = round(haversine_m(location.latitude, location.longitude, item["latitude"], item["longitude"]), 1)
            if distance > data["radius_m"]:
                continue
            distance_status = "exact"
        elif location.eup_myeon and item.get("eup_myeon") != location.eup_myeon:
            continue
        item["distance_m"] = distance
        item["distance_status"] = distance_status
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            item["metadata"] = {}
        projects.append(item)
    projects.sort(key=lambda item: (item["distance_m"] is None, item["distance_m"] if item["distance_m"] is not None else 0, item.get("permit_date") or ""))
    # The public permit register reports 정상가동 / 가동중단 / 폐기; the demo
    # fixtures use 사업개시. Match both vocabularies, and never count a stopped
    # or scrapped plant as operating.
    operating = [
        item for item in projects
        if any(term in str(item.get("operation_status") or "") for term in OPERATING_STATUS_TERMS)
        and not any(term in str(item.get("operation_status") or "") for term in NON_OPERATING_STATUS_TERMS)
    ]
    total_capacity = sum(float(item.get("capacity_kw") or 0) for item in projects)
    origins = {str(item.get("metadata", {}).get("data_origin") or "permit_project") for item in projects}
    if origins == {"public_dataset"}:
        data_origin = "public_dataset"
    elif origins == {"synthetic"}:
        data_origin = "synthetic"
    else:
        data_origin = "mixed" if len(origins) > 1 else next(iter(origins), "permit_project")
    return {
        "count": len(projects),
        "total_capacity_kw": round(total_capacity, 2),
        "operating_count": len(operating),
        "operation_rate": round(len(operating) / len(projects), 4) if projects else None,
        "distance_search_used": any(item["distance_status"] == "exact" for item in projects),
        "data_origin": data_origin,
        "projects": projects[:30],
    }


def _evidence_from_result(result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("source") or {}
    match = result.get("location_match") or {}
    return {
        "evidence_id": result.get("evidence_id"),
        "case_id": (result.get("case") or {}).get("case_id"),
        "title": result.get("meeting_title") or result.get("title"),
        "date": result.get("meeting_date"),
        "text": result.get("evidence_text_original") or result.get("evidence_text"),
        "snippet": result.get("evidence_text"),
        "location_match": {
            "group": match.get("group"),
            "precision": match.get("precision"),
            "distance_m": match.get("distance_m"),
            "basis": match.get("basis"),
        },
        "issues": [issue.get("issue_code") for issue in result.get("issues", []) if issue.get("issue_code")],
        "source": source,
        "review_status": result.get("review_status"),
    }


def _reason_cards(
    results: list[dict[str, Any]],
    rule_analysis: dict[str, Any],
    process_events: list[dict[str, Any]],
    permit_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    rule_checks = sorted(
        rule_analysis["checks"],
        key=lambda check: 0 if check["status"] == "fail" else 1,
    )
    for check in rule_checks:
        if check["status"] not in {"fail", "check_required"}:
            continue
        cards.append(
            {
                "category": "입지·규칙",
                "title": check["rule_name"],
                "statement": check["reason"],
                "assertion_type": "fact" if check["status"] == "fail" else "unknown",
                "severity": check["severity"],
                "evidence_ids": [check["rule_id"]],
                "evidence": [{"type": "rule", **check}],
                "next_check": "공식 기준·현장 조건·입력값을 담당자가 확인",
            }
        )

    issue_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        for issue in result.get("issues", []):
            code = issue.get("issue_code")
            if code:
                issue_results[str(code)].append(result)
    issue_priority = {
        "safety_environment": 0,
        "communication_procedure": 1,
        "agricultural_land_damage": 2,
        "landscape_damage": 3,
        "glare_reflection": 4,
        "noise_living_discomfort": 5,
    }
    for code, items in sorted(
        issue_results.items(),
        key=lambda pair: (issue_priority.get(pair[0], 99), -len(pair[1]), pair[0]),
    ):
        unique_cases = {((item.get("case") or {}).get("case_id")) for item in items}
        unique_cases.discard(None)
        label = ISSUE_LABELS.get(code, code)
        repeated = len(unique_cases) >= 2 or len(items) >= 2
        evidence = [_evidence_from_result(item) for item in items[:3]]
        cards.append(
            {
                "category": "과거 쟁점",
                "title": f"{label} 관련 기록 {'반복 확인' if repeated else '확인'}",
                "statement": f"검색된 근거에서 {label} 관련 기록 {len(items)}건, 사건 {len(unique_cases)}건이 확인되었습니다.",
                "assertion_type": "fact",
                "severity": "high" if repeated else "medium",
                "evidence_ids": [item["evidence_id"] for item in evidence if item.get("evidence_id")],
                "evidence": evidence,
                "next_check": {
                    "glare_reflection": "모듈 방향·시간대별 반사 시야 확인",
                    "communication_procedure": "주민 협의 대상과 설명회 계획 확인",
                    "safety_environment": "배수·침수·사면·재해 현장 조사",
                    "landscape_damage": "주요 조망점과 차폐 계획 검토",
                    "noise_living_discomfort": "소음 발생 설비와 운영 시간 확인",
                }.get(code, "관련 원문과 현재 사업 조건을 대조"),
            }
        )

    if process_events:
        last_by_case: dict[str, dict[str, Any]] = {}
        for event in process_events:
            last_by_case[event["case_id"]] = event
        unresolved = [event for event in last_by_case.values() if event.get("outcome") in {"planned", "pending", "reported", "observed", "recurred"}]
        process_ids = [event["process_event_id"] for event in process_events[:8]]
        cards.append(
            {
                "category": "처리 과정",
                "title": "과거 민원의 행정 처리 과정 확인",
                "statement": f"민원·질의·조사·협의 등 처리 과정 {len(process_events)}건이 확인되었습니다.",
                "assertion_type": "fact",
                "severity": "high" if unresolved else "medium",
                "evidence_ids": process_ids,
                "evidence": [
                    {
                        "process_event_id": event["process_event_id"],
                        "event_type": event["event_type"],
                        "label": event["label"],
                        "event_date": event["event_date"],
                        "action_text": event["action_text"],
                        "outcome": event["outcome"],
                        "certainty": event["certainty"],
                        "case_id": event["case_id"],
                        "paragraph_id": event["paragraph_id"],
                    }
                    for event in process_events[:8]
                ],
                "next_check": "마지막 조치의 완료 여부와 재발 여부 확인",
                "unresolved_case_count": len(unresolved),
            }
        )
    if permit_analysis.get("count", 0) >= 3:
        cards.append(
            {
                "category": "주변 사업 현황",
                "title": "주변 발전사업이 여러 건 확인됨",
                "statement": f"검색 범위 안에 기존 발전사업 {permit_analysis['count']}건({permit_analysis['total_capacity_kw']:,.0f}kW)이 확인되었습니다.",
                "assertion_type": "fact",
                "severity": "medium",
                "evidence_ids": [item["project_id"] for item in permit_analysis.get("projects", [])[:10]],
                "evidence": permit_analysis.get("projects", [])[:10],
                "next_check": "누적 입지 영향과 계통연계 여유를 별도로 확인",
            }
        )
    return cards


def _timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "process_event_id": event["process_event_id"],
            "case_id": event["case_id"],
            "event_type": event["event_type"],
            "label": event["label"],
            "event_date": event["event_date"],
            "actor": event["actor"],
            "action_text": event["action_text"],
            "outcome": event["outcome"],
            "certainty": event["certainty"],
            "confidence": event["confidence"],
            "evidence_text": event["evidence_text"],
            "paragraph_id": event["paragraph_id"],
            "document_title": event.get("document_title"),
            "page_from": event.get("page_from"),
        }
        for event in events
    ]


def _format_local_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.0f}" if number.is_integer() else f"{number:g}"


def _local_reason_text(reason: dict[str, Any]) -> tuple[str, str]:
    title = str(reason.get("title") or reason.get("category") or "검토 항목").replace("(합성)", "").strip()
    evidence = (reason.get("evidence") or [])
    check = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
    if reason.get("category") == "입지·규칙" and check:
        observed = check.get("observed_value")
        threshold = check.get("threshold_value")
        unit = str(check.get("unit") or "")
        if check.get("status") == "fail" and observed is not None and threshold is not None:
            return title, f"{_format_local_number(observed)}{unit}로 기준 {_format_local_number(threshold)}{unit} 미충족."
        if "비교 기준" in title or "원장" in str(reason.get("statement") or ""):
            return title, "지역 허가 원장 비교자료가 부족해 확인 필요."
    issue_match = re.search(r"관련 기록\s*(\d+)건,\s*사건\s*(\d+)건", str(reason.get("statement") or ""))
    if issue_match:
        title = re.sub(r"\s*관련 기록\s*(?:반복 확인|확인)$", "", title).strip()
        return title, f"관련 기록 {issue_match.group(1)}건·사건 {issue_match.group(2)}건 확인."
    return title, _short_body(reason.get("statement"), 180)


class LocalAnswerGenerator:
    """Short deterministic fallback with the same shape as the Claude answer."""

    def generate(self, pack: dict[str, Any]) -> str:
        analysis = pack["analysis"]
        conclusion = analysis["conclusion_label"]
        lines = ["결론", conclusion]
        reasons = analysis.get("reason_cards", [])[:MAX_REASONS]
        if reasons:
            lines.extend(["", "핵심 근거"])
            for reason in reasons:
                title, statement = _local_reason_text(reason)
                if statement:
                    lines.append(f"- {title}: {statement}")
        else:
            lines.append("\n핵심 근거가 충분히 확인되지 않았습니다.")
        checks = []
        for reason in reasons:
            item = _short_body(reason.get("next_check"), 110)
            if item == "공식 기준·현장 조건·입력값을 담당자가 확인":
                item = "주거지·도로 이격거리와 현장 조건 확인"
            if item and item not in checks:
                checks.append(item)
        if checks:
            lines.extend(["", "다음 확인"])
            lines.extend(f"- {item}" for item in checks[:4])
        limitations = analysis.get("limitations", [])
        if limitations:
            lines.extend(["", f"참고: {_short_body(limitations[0], 140)}"])
        lines.extend(["", "※ 사전점검 참고자료이며 허가·불허를 확정하지 않습니다."])
        return "\n".join(lines)


# A VWorld geocode is only accepted for an address specific enough to name one
# parcel or building. "영암군 삼호읍" would resolve to the township centre, and
# turning that into a site coordinate is exactly the invented precision the
# design forbids.
ADDRESSABLE_RE = re.compile(r"\d+(?:-\d+)?\s*(?:번지|호)?\s*$|(?:로|길)\s*\d+")


def _is_addressable(address: str) -> bool:
    return bool(ADDRESSABLE_RE.search((address or "").strip()))


def _resolve_map_context(
    client: Any, data: dict[str, Any], location: Location
) -> dict[str, Any]:
    """Geocode when the address warrants it, then fetch the surrounding view."""

    context: dict[str, Any] = {"requested": True, "images": [], "layers": [], "errors": []}
    if not client.enabled:
        context.update({"requested": False, "reason": "vworld_key_missing"})
        return context

    if location.latitude is None or location.longitude is None:
        if not _is_addressable(data["address"]):
            context.update({"requested": False, "reason": "address_not_specific_enough"})
            return context
        geocode = client.geocode_any(data["address"])
        context["geocode"] = geocode
        if geocode.get("status") != "OK":
            context["errors"].append({"stage": "geocode", "detail": geocode.get("status")})
            return context
        location.latitude = geocode["latitude"]
        location.longitude = geocode["longitude"]
        location.provider = "vworld"
        location.precision = "jibun_address" if geocode.get("address_type") == "parcel" else "road_address"
        location.status = "resolved_by_vworld"
        location.confidence = max(location.confidence, 0.9)
        # Fill gaps only. What the user typed, and what the local parser already
        # made of it, stays authoritative; the refined address is used to
        # complete an abbreviated input, not to rewrite it.
        for field in ("province", "city_county", "eup_myeon", "ri"):
            if not getattr(location, field) and geocode.get(field):
                setattr(location, field, geocode[field])
        if geocode.get("refined_address"):
            location.jibun_address = location.jibun_address or geocode["refined_address"]
        data["latitude"] = location.latitude
        data["longitude"] = location.longitude
        context["pnu"] = geocode.get("pnu")
        context["refined_address"] = geocode.get("refined_address")
        context["administrative_name"] = geocode.get("administrative_name")

    fetched = client.site_context(location.latitude, location.longitude, pnu=context.get("pnu"))
    context["images"] = [
        {**image, "url": f"/v1/map-image/{image['cache_key']}"} for image in fetched["images"]
    ]
    context["layers"] = fetched["layers"]
    context["parcel"] = fetched.get("parcel")
    context["errors"].extend(fetched["errors"])
    return context


class RAGService:
    def __init__(self, db: Any, answer_generator: Any | None = None, vworld_client: Any | None = None):
        self.db = db
        self.search = SearchService(db)
        self.rules = SitingRuleEngine(db)
        self.vworld = vworld_client or VWorldClient()
        # Claude only renders the finished pack, and only when a key is present
        # and its guards pass; otherwise the deterministic template answers.
        # LUCERA_ANSWER_MODE=local forces the template for offline demos.
        if answer_generator is not None:
            self.answer_generator = answer_generator
        elif os.getenv("LUCERA_ANSWER_MODE", "").lower() == "local":
            self.answer_generator = LocalAnswerGenerator()
        else:
            self.answer_generator = ClaudeAnswerGenerator(LocalAnswerGenerator())

    def _retrieve(self, data: dict[str, Any], location: Location) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        terms, issue_codes = _query_terms(data["message"])
        search_payload = {
            "address": data["address"],
            "latitude": data["latitude"],
            "longitude": data["longitude"],
            "radius_m": data["radius_m"],
            "keywords": terms,
            "issue_codes": issue_codes,
            "limit": 20,
            "case_paragraph_limit": 6,
            "review_mode": data["review_mode"] if data["review_mode"] in {"eligible", "all", "needs_review"} else "all",
            "include_comparative": data["include_comparative"],
            "resolve_address": data["resolve_address"],
            "scope": data["scope"],
        }
        result = self.search.search(search_payload)
        evidence = [_evidence_from_result(item) for item in result.get("results", [])]
        return result, evidence

    def analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = normalize_chat_input(payload)
        location, geocode_status, geocode_response = self.search.resolve_location(
            data["address"],
            {
                "latitude": data["latitude"],
                "longitude": data["longitude"],
                "resolve_address": data["resolve_address"],
            },
        )
        # resolve_location may fill coordinates from an address provider. Keep
        # the resolved values in the canonical input shown to the caller.
        data["latitude"] = location.latitude
        data["longitude"] = location.longitude
        require_yeongam(location.city_county, data["address"])
        # Imagery is fetched before retrieval because a successful VWorld
        # geocode upgrades the location, which changes what retrieval and the
        # distance rules can do.
        map_context = (
            _resolve_map_context(self.vworld, data, location)
            if data["include_map_context"]
            else {"requested": False, "reason": "disabled_by_request", "images": [], "layers": [], "errors": []}
        )
        search_result, evidence = self._retrieve(data, location)
        results = search_result.get("results", [])
        case_ids = {
            (item.get("case") or {}).get("case_id")
            for item in results
            if (item.get("case") or {}).get("case_id")
        }
        process_events = _load_process_events(self.db, {str(case_id) for case_id in case_ids})
        permit_analysis = _load_permit_projects(self.db, data, location)
        rule_analysis = self.rules.evaluate(data, location, map_context)
        issue_counts = _issue_counts(results)
        repeated_codes = [code for code, count in issue_counts.items() if count >= 2]
        severe_repeated = any(code in {"glare_reflection", "safety_environment", "communication_procedure", "siting_permit_regulatory"} for code in repeated_codes)
        if rule_analysis["failed_count"]:
            conclusion = "review_required"
        elif severe_repeated or process_events:
            conclusion = "conditional_review"
        elif rule_analysis["unknown_count"] or not results:
            conclusion = "insufficient_evidence" if not results or rule_analysis["unknown_count"] else "caution"
        else:
            conclusion = "no_material_risk_found"

        limitations: list[str] = []
        if location.latitude is None or location.longitude is None:
            limitations.append(f"주소가 {location.precision} 수준으로 해석되어 정확한 거리 계산을 하지 않았습니다.")
        if not data.get("site_area_sqm"):
            limitations.append("부지면적이 없어 면적 대비 규모 검산을 완료하지 못했습니다.")
        if not data.get("capacity_kw"):
            limitations.append("설비용량이 없어 용량 대비 부지 규모를 계산하지 못했습니다.")
        if any(item.get("review_status") == "pending" for item in results):
            limitations.append("일부 사건은 담당자 검수 전 후보 기록입니다.")
        if not results:
            limitations.append("현재 검색 범위에서 연결된 회의록 근거가 없습니다.")

        analysis = {
            "conclusion": conclusion,
            "conclusion_label": CONCLUSION_LABELS[conclusion],
            "rule_analysis": rule_analysis,
            "issue_counts": {code: {"label": ISSUE_LABELS.get(code, code), "count": count} for code, count in issue_counts.items()},
            "reason_cards": _reason_cards(results, rule_analysis, process_events, permit_analysis),
            "timeline": _timeline(process_events),
            "permit_analysis": permit_analysis,
            "limitations": limitations,
            "method": {
                "retrieval": "existing_search_hybrid_fts_location",
                "rule_engine": "deterministic_siting_v1",
                "process_extraction": "deterministic_process_v1",
                "answer_generation": "local_template_v1",
            },
        }
        pack = {
            "input": data,
            "location": location.to_dict(),
            "geocode": {"status": geocode_status, "response": geocode_response if data["resolve_address"] else None},
            "map_context": map_context,
            "analysis": analysis,
            "retrieval": {
                "query": search_result.get("query"),
                "summary": search_result.get("summary"),
                "evidence": evidence,
                "case_groups": search_result.get("case_groups", []),
            },
            "grounding": {
                "evidence_ids": [item["evidence_id"] for item in evidence if item.get("evidence_id")],
                "process_event_ids": [event["process_event_id"] for event in process_events],
                "rule_ids": [check["rule_id"] for check in rule_analysis["checks"]],
                "permit_project_ids": [item["project_id"] for item in permit_analysis.get("projects", [])],
                "citation_required": True,
            },
            "user_image": data.get("image"),
        }
        pack["answer"] = self.answer_generator.generate(pack)
        status = getattr(self.answer_generator, "last_status", None)
        if isinstance(status, dict):
            # Surface which generator actually produced the prose, and why, so a
            # silent fallback is visible instead of looking like a Claude answer.
            analysis["method"]["answer_generation"] = (
                f"claude:{status.get('model')}" if status.get("mode") == "claude" else "local_template_v1"
            )
            pack["answer_generation"] = {
                key: value for key, value in status.items() if key != "structured"
            }
            if status.get("mode") == "claude":
                pack["answer_structured"] = status.get("structured")
        for image in pack["map_context"].get("images") or []:
            image.pop("path", None)
        pack["notice"] = "영암군 공개 허가 원장·실제 회의록 기반의 사전점검입니다. 법적 허가·불허 또는 설치 결정을 자동 확정하지 않습니다."
        return pack

    def case_timeline(self, case_id: str) -> dict[str, Any] | None:
        case = self.db.conn.execute(
            """SELECT case_id, COALESCE(canonical_title, case_name) AS title,
                      municipality, village, address, project_name, facility_type,
                      review_status, confidence
                 FROM conflict_case WHERE case_id=?""",
            (case_id,),
        ).fetchone()
        if not case:
            return None
        events = _load_process_events(self.db, {case_id})
        return {"case": dict(case), "events": _timeline(events), "event_count": len(events)}
