from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config

from .regions import UNIFIED_PROVINCE_NAME, province_for_city


PROVINCE_ALIASES = {
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
    UNIFIED_PROVINCE_NAME: UNIFIED_PROVINCE_NAME,
}


@dataclass
class Location:
    raw_address: str
    normalized_address: str
    province: str | None = None
    city_county: str | None = None
    eup_myeon: str | None = None
    ri: str | None = None
    road_address: str | None = None
    jibun_address: str | None = None
    admin_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    precision: str = "unknown"
    provider: str = "rule"
    confidence: float = 0.0
    status: str = "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def normalize_address(raw_address: str) -> Location:
    raw = " ".join(str(raw_address or "").split())
    normalized = raw
    for short, full in sorted(PROVINCE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = re.sub(rf"(?<![가-힣]){re.escape(short)}(?=\s|$)", full, normalized)
    province = _first(
        r"(서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|"
        rf"세종특별자치시|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|"
        rf"전라남도|경상북도|경상남도|제주특별자치도|제주도|{re.escape(UNIFIED_PROVINCE_NAME)})",
        normalized,
    )
    city_source = re.sub(rf"^{re.escape(province)}\s*", "", normalized) if province else normalized
    city_county = _first(r"([가-힣]+(?:시|군|구))(?:\s|$)", city_source)
    eup_myeon = _first(r"([가-힣0-9]+(?:읍|면|동))(?:\s|$)", normalized)
    ri = _first(r"([가-힣0-9]+리)(?:\s|$)", normalized)
    if province == UNIFIED_PROVINCE_NAME:
        province = province_for_city(city_county, "전라남도")
    elif not province and city_county:
        province = province_for_city(city_county)
    precision = "unknown"
    if ri:
        precision = "ri"
    elif eup_myeon:
        precision = "eup_myeon"
    elif city_county:
        precision = "city_county"
    elif province:
        precision = "province"
    if re.search(r"\d", normalized) and ("로" in normalized or "길" in normalized):
        precision = "road_address"
    return Location(
        raw_address=raw,
        normalized_address=normalized,
        province=province,
        city_county=city_county,
        eup_myeon=eup_myeon,
        ri=ri,
        road_address=normalized if precision == "road_address" else None,
        precision=precision,
        confidence=0.65 if province or city_county else 0.25,
        status="parsed" if province or city_county or eup_myeon or ri else "unresolved",
    )


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 32 <= number <= 39:
        return number
    return None


def _safe_lon(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 124 <= number <= 132:
        return number
    return None


class JusoClient:
    provider = "juso"

    def __init__(self, api_key: str | None = None, endpoint: str = config.JUSO_ENDPOINT):
        self.api_key = api_key or config.PUBLIC_DATA_KEYS["road_address"]
        self.endpoint = endpoint

    def search(self, address: str, count: int = 10) -> dict[str, Any]:
        params = {
            "confmKey": self.api_key,
            "currentPage": "1",
            "countPerPage": str(min(max(count, 1), 20)),
            "keyword": address,
            "hstryYn": "Y",
            "addInfoYn": "Y",
            "resultType": "json",
        }
        request = Request(
            self.endpoint + "?" + urlencode(params),
            headers={"User-Agent": "Lucera/0.1"},
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8"))
        results = payload.get("results", {})
        common = results.get("common", {})
        candidates = results.get("juso", []) or []
        parsed = [self._parse_candidate(item) for item in candidates]
        return {
            "provider": self.provider,
            "status": str(common.get("errorCode", "unknown")),
            "message": common.get("errorMessage"),
            "candidates": parsed,
            "raw": payload,
        }

    def resolve(self, address: str) -> tuple[Location, dict[str, Any]]:
        parsed = normalize_address(address)
        try:
            response = self.search(address)
        except Exception as exc:  # Network/provider failures must not block regional fallback.
            parsed.provider = self.provider
            parsed.status = "provider_error"
            parsed.confidence = min(parsed.confidence, 0.5)
            return parsed, {"provider": self.provider, "status": "provider_error", "error": str(exc)[:300], "candidates": []}
        candidates = response["candidates"]
        if not candidates:
            parsed.provider = self.provider
            parsed.status = "no_candidate"
            return parsed, response
        best = candidates[0]
        for field in ("province", "city_county", "eup_myeon", "ri", "road_address", "jibun_address", "admin_code"):
            if not getattr(parsed, field, None) and best.get(field):
                setattr(parsed, field, best[field])
        parsed.latitude = best.get("latitude")
        parsed.longitude = best.get("longitude")
        parsed.provider = self.provider
        parsed.precision = best.get("precision") or parsed.precision
        parsed.confidence = 0.9 if parsed.latitude and parsed.longitude else 0.82
        parsed.status = "resolved" if best.get("road_address") or best.get("jibun_address") else "parsed"
        return parsed, response

    @staticmethod
    def _parse_candidate(item: dict[str, Any]) -> dict[str, Any]:
        lat = _safe_float(item.get("entY"))
        lon = _safe_lon(item.get("entX"))
        road = item.get("roadAddr") or item.get("roadAddrPart1")
        jibun = item.get("jibunAddr")
        precision = "road_address" if road else "unknown"
        return {
            "road_address": road,
            "jibun_address": jibun,
            "province": item.get("siNm"),
            "city_county": item.get("sggNm"),
            "eup_myeon": item.get("emdNm"),
            "ri": item.get("liNm"),
            "admin_code": item.get("admCd"),
            "latitude": lat,
            "longitude": lon,
            "precision": precision,
            "raw": item,
        }
