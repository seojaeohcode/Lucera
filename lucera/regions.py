from __future__ import annotations

import re
from typing import Any


UNIFIED_PROVINCE_NAME = "전남광주통합특별시"

# The product's actual local search/collection units are exactly:
# 5 autonomous cities + 17 autonomous counties + 5 autonomous districts.
# Province/metropolitan names are kept separately as parent scope metadata;
# they are not extra local targets and therefore do not inflate the 27 count.
REGION_CATALOG: tuple[dict[str, Any], ...] = (
    {"region_code": "061009", "name": "목포시", "province": "전라남도", "kind": "city", "region_group": "자치시", "parent_region_code": "JEONNAM", "assembly_id": "061009", "aliases": ()},
    {"region_code": "061014", "name": "여수시", "province": "전라남도", "kind": "city", "region_group": "자치시", "parent_region_code": "JEONNAM", "assembly_id": "061014", "aliases": ()},
    {"region_code": "061012", "name": "순천시", "province": "전라남도", "kind": "city", "region_group": "자치시", "parent_region_code": "JEONNAM", "assembly_id": "061012", "aliases": ()},
    {"region_code": "061005", "name": "광양시", "province": "전라남도", "kind": "city", "region_group": "자치시", "parent_region_code": "JEONNAM", "assembly_id": "061005", "aliases": ()},
    {"region_code": "061007", "name": "나주시", "province": "전라남도", "kind": "city", "region_group": "자치시", "parent_region_code": "JEONNAM", "assembly_id": "061007", "aliases": ()},
    {"region_code": "061008", "name": "담양군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061008", "aliases": ()},
    {"region_code": "061004", "name": "곡성군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061004", "aliases": ()},
    {"region_code": "061006", "name": "구례군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061006", "aliases": ()},
    {"region_code": "061003", "name": "고흥군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061003", "aliases": ()},
    {"region_code": "061011", "name": "보성군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061011", "aliases": ()},
    {"region_code": "061023", "name": "화순군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061023", "aliases": ()},
    {"region_code": "061019", "name": "장흥군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061019", "aliases": ()},
    {"region_code": "061002", "name": "강진군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061002", "aliases": ()},
    {"region_code": "061022", "name": "해남군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061022", "aliases": ()},
    {"region_code": "061016", "name": "영암군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061016", "aliases": ()},
    {"region_code": "061010", "name": "무안군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061010", "aliases": ()},
    {"region_code": "061021", "name": "함평군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061021", "aliases": ()},
    {"region_code": "061015", "name": "영광군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061015", "aliases": ()},
    {"region_code": "061018", "name": "장성군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061018", "aliases": ()},
    {"region_code": "061017", "name": "완도군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061017", "aliases": ()},
    {"region_code": "061020", "name": "진도군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061020", "aliases": ()},
    {"region_code": "061013", "name": "신안군", "province": "전라남도", "kind": "county", "region_group": "자치군", "parent_region_code": "JEONNAM", "assembly_id": "061013", "aliases": ()},
    {"region_code": "062004", "name": "동구", "province": "광주광역시", "kind": "autonomous_district", "region_group": "자치구", "parent_region_code": "GWANGJU", "assembly_id": "062004", "aliases": ("광주 동구",)},
    {"region_code": "062006", "name": "서구", "province": "광주광역시", "kind": "autonomous_district", "region_group": "자치구", "parent_region_code": "GWANGJU", "assembly_id": "062006", "aliases": ("광주 서구",)},
    {"region_code": "062003", "name": "남구", "province": "광주광역시", "kind": "autonomous_district", "region_group": "자치구", "parent_region_code": "GWANGJU", "assembly_id": "062003", "aliases": ("광주 남구",)},
    {"region_code": "062005", "name": "북구", "province": "광주광역시", "kind": "autonomous_district", "region_group": "자치구", "parent_region_code": "GWANGJU", "assembly_id": "062005", "aliases": ("광주 북구",)},
    # CLiK returned no linked council ID for Gwangsan-gu during discovery.
    {"region_code": "062007", "name": "광산구", "province": "광주광역시", "kind": "autonomous_district", "region_group": "자치구", "parent_region_code": "GWANGJU", "assembly_id": None, "aliases": ("광주 광산구",)},
)

PARENT_REGIONS: tuple[dict[str, Any], ...] = (
    {"region_code": "GWANGJU", "name": "광주광역시", "province": "광주광역시", "kind": "parent_scope", "region_group": "상위권역", "aliases": ("광주", "광주시")},
    {"region_code": "JEONNAM", "name": "전라남도", "province": "전라남도", "kind": "parent_scope", "region_group": "상위권역", "aliases": ("전남", "전남도의회", "전라남도의회")},
)

_BY_NAME = {item["name"]: item for item in (*REGION_CATALOG, *PARENT_REGIONS)}
_BY_ALIAS = {
    alias: item
    for item in (*REGION_CATALOG, *PARENT_REGIONS)
    for alias in (item["name"], *item.get("aliases", ()))
}


def region_catalog() -> list[dict[str, Any]]:
    """Return the 27 local target units, not their two parent scopes."""
    return [
        dict(item, aliases=list(item.get("aliases", ())), available=bool(item.get("assembly_id")))
        for item in REGION_CATALOG
    ]


def parent_region_catalog() -> list[dict[str, Any]]:
    return [dict(item, aliases=list(item.get("aliases", ())), available=True) for item in PARENT_REGIONS]


def region_for_name(value: str | None) -> dict[str, Any] | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for alias, item in _BY_ALIAS.items():
        if text == alias or re.search(rf"(?<![가-힣]){re.escape(alias)}(?:의회)?(?![가-힣])", text):
            specificity = 0 if item["kind"] == "parent_scope" else 1
            matches.append((specificity, len(alias), item))
    return max(matches, key=lambda match: (match[0], match[1]))[2] if matches else None


def province_for_city(city_county: str | None, fallback: str | None = None) -> str | None:
    city = " ".join(str(city_county or "").split())
    if city in _BY_NAME:
        return _BY_NAME[city]["province"]
    return fallback


def target_regions() -> list[dict[str, Any]]:
    return region_catalog()
