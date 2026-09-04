"""Yeongam-scoped F1/F3/F4 feature adapters.

The upstream solverton repository contains several presentation add-ons and
some non-Yeongam demo data.  This module consumes only the three structured
source snapshots copied into ``data/reference/solverton`` and filters every
response to Yeongam before it reaches the API or UI.
"""

from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .yeongam import YEONGAM_COUNTY


ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "data" / "reference" / "solverton"
ORDINANCE_PATH = REFERENCE_DIR / "jeonnam_ordinance_rules.json"
ENCIRCLEMENT_PATH = REFERENCE_DIR / "jeonnam_ri_solar_accumulation.csv"
GRID_PATH = REFERENCE_DIR / "jeonnam_kepco_supply.csv"
LAW_SOURCE_URL = "https://www.law.go.kr/DRF/lawSearch.do?target=ordin"


def _number(value: Any) -> float | None:
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number or 0)


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def _ordinance_rows() -> tuple[dict[str, Any], ...]:
    if not ORDINANCE_PATH.is_file():
        return ()
    rows = json.loads(ORDINANCE_PATH.read_text(encoding="utf-8"))
    return tuple(row for row in rows if row.get("시군") == YEONGAM_COUNTY)


@lru_cache(maxsize=1)
def _encirclement_rows() -> tuple[dict[str, str], ...]:
    return tuple(row for row in _load_csv(ENCIRCLEMENT_PATH) if row.get("시군") == YEONGAM_COUNTY)


@lru_cache(maxsize=1)
def _grid_rows() -> tuple[dict[str, str], ...]:
    return tuple(row for row in _load_csv(GRID_PATH) if YEONGAM_COUNTY in str(row.get("시군구") or ""))


def yeongam_f1() -> dict[str, Any]:
    """Return the source-backed Yeongam ordinance layer.

    F1's national border visualization is intentionally not exposed here:
    Lucera's product scope is Yeongam only.  The exact Yeongam article and
    comparison values remain available as the local layer used by F2 checks.
    """

    row = dict(_ordinance_rows()[0]) if _ordinance_rows() else {}
    return {
        "feature": "F1",
        "name": "영암군 조례 기준 레이어",
        "scope": YEONGAM_COUNTY,
        "status": "ready" if row else "source_missing",
        "verification": "solverton_reference_snapshot",
        "source": {
            "title": "법제처 자치법규 이격거리 조례 스냅샷",
            "url": LAW_SOURCE_URL,
            "data_origin": "reference_snapshot",
            "snapshot_date": "2026-09-03",
        },
        "rule": {
            "county": row.get("시군"),
            "ordinance_name": row.get("조례명"),
            "article": row.get("조문"),
            "effective_date": row.get("시행일"),
            "department": row.get("담당부서"),
            "road_m": _number(row.get("도로_m")),
            "road_scope": row.get("도로_적용범위"),
            "residence_5plus_m": _number(row.get("주거10호이상_m")),
            "residence_small_m": _number(row.get("주거소규모_m")),
            "tourist_m": _number(row.get("관광지_m")),
            "cultural_asset_m": _number(row.get("문화재_m")),
            "note": row.get("비고"),
        },
        "notice": "영암군 조례 원문 스냅샷 기준입니다. 최종 허가 판단 전 현행 조문을 다시 확인해야 합니다.",
    }


def _map_pins_by_ri(db: Any) -> dict[tuple[str, str], list[dict[str, Any]]]:
    from .complaints import yeongam_pins

    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pin in yeongam_pins(db).get("pins", []):
        key = (str(pin.get("eup_myeon") or ""), str(pin.get("ri") or ""))
        if key[0] and key[1] and pin.get("latitude") is not None and pin.get("longitude") is not None:
            result.setdefault(key, []).append(
                {
                    "id": pin.get("id"),
                    "latitude": pin.get("latitude"),
                    "longitude": pin.get("longitude"),
                    "title": pin.get("title"),
                }
            )
    return result


def yeongam_f3(db: Any) -> dict[str, Any]:
    """Return the Yeongam subset of the reference encirclement dataset."""

    pins_by_ri = _map_pins_by_ri(db)
    rows = []
    for row in sorted(_encirclement_rows(), key=lambda item: (-_integer(item.get("건수")), str(item.get("리") or ""))):
        key = (str(row.get("읍면") or ""), str(row.get("리") or ""))
        rows.append(
            {
                "eup_myeon": key[0],
                "ri": key[1],
                "permit_count": _integer(row.get("건수")),
                "area_sqm": _number(row.get("면적합")),
                "area_ha": _number(row.get("면적_ha")),
                "first_year": _number(row.get("최초")),
                "latest_year": _number(row.get("최근")),
                "map_sample_count": len(pins_by_ri.get(key, [])),
                "map_sample_pins": pins_by_ri.get(key, [])[:4],
            }
        )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "feature": "F3",
        "name": "영암군 리별 누적 포위도",
        "scope": YEONGAM_COUNTY,
        "status": "ready" if rows else "source_missing",
        "radius_m": 1000,
        "ranking_basis": "reference_snapshot_permit_count",
        "data_origin": "solverton_reference_snapshot",
        "snapshot_date": "2026-09-03",
        "items": rows,
        "notice": "리별 누적 허가 건수와 면적을 보여줍니다. 좌표가 있는 대표 표본만 지도에 표시하며, 면적 비율은 근거가 없어 산출하지 않습니다.",
    }


def yeongam_f4(db: Any) -> dict[str, Any]:
    """Return the Yeongam subset of the masked KEPCO supply dataset."""

    permit_counts = {
        str(row["eup_myeon"]): int(row["count"])
        for row in db.conn.execute(
            """SELECT eup_myeon, COUNT(*) AS count
                 FROM permit_project
                WHERE city_county=?
                GROUP BY eup_myeon""",
            (YEONGAM_COUNTY,),
        ).fetchall()
        if row["eup_myeon"]
    }
    items = []
    for row in sorted(_grid_rows(), key=lambda item: str(item.get("읍면동") or "")):
        area = str(row.get("읍면동") or "")
        supply = str(row.get("공급변전소(비식별)") or "").strip()
        items.append(
            {
                "eup_myeon": area,
                "masked_substations": supply,
                "supply_data_present": bool(supply),
                "permit_register_count": permit_counts.get(area, 0),
                "notice": "변전소 위치는 비식별 처리된 읍면 단위 자료입니다.",
            }
        )
    return {
        "feature": "F4",
        "name": "영암군 계통 병목 신호",
        "scope": YEONGAM_COUNTY,
        "status": "ready" if items else "source_missing",
        "granularity": "eup_myeon",
        "data_origin": "solverton_reference_snapshot",
        "snapshot_date": "2026-09-03",
        "items": items,
        "notice": "공급 가능 변전소 자료는 읍면 단위·비식별 값만 제공하므로 선로 여유용량이나 변전소 정확 위치를 의미하지 않습니다.",
    }


def feature_overview(db: Any) -> dict[str, Any]:
    return {"scope": YEONGAM_COUNTY, "features": [yeongam_f1(), yeongam_f3(db), yeongam_f4(db)]}


def feature_context_for_location(db: Any, eup_myeon: str | None, ri: str | None) -> dict[str, Any]:
    """Build the compact F-context automatically used by every RAG turn."""

    f3_items = list(yeongam_f3(db).get("items") or [])
    f4_items = list(yeongam_f4(db).get("items") or [])
    matching_ri = [
        item for item in f3_items
        if (not eup_myeon or item.get("eup_myeon") == eup_myeon)
        and (not ri or item.get("ri") == ri)
    ]
    top_ri = f3_items[:5] if not matching_ri else matching_ri + [item for item in f3_items if item not in matching_ri][:3]
    matching_grid = [item for item in f4_items if not eup_myeon or item.get("eup_myeon") == eup_myeon]
    return {
        "f1_ordinance": yeongam_f1().get("rule"),
        "f3_ri_accumulation": [
            {key: item.get(key) for key in ("eup_myeon", "ri", "permit_count", "area_ha", "first_year", "latest_year")}
            for item in top_ri[:8]
        ],
        "f4_grid_signal": [
            {key: item.get(key) for key in ("eup_myeon", "masked_substations", "permit_register_count")}
            for item in matching_grid
        ],
        "scope": YEONGAM_COUNTY,
        "source": "solverton_reference_snapshot",
    }
