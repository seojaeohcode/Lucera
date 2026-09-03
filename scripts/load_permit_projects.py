"""Load the public solar permit register into `permit_project`.

Source: `data/reference/public_api/solar_permit_20260817/clean/solar_permit_clean.csv`
(공공데이터포털 `tn_pubr_public_solar_gen_flct_api`, 전국 125,229건).  The chat
pre-check needs these rows for two things the meeting records cannot supply:
the existing permits around a proposed site, and a real distribution to compare
the user's own area/capacity against.

Only the service scope (광주·전남 by default) is loaded.  `instlArea` is carried
into metadata as `installation_area_sqm` because it is the register's own
installed-area figure, not an estimate; rows without it stay `null` rather than
receiving a derived value.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lucera.db import LuceraDB, stable_id  # noqa: E402
from lucera.paths import DATABASE_PATH, PUBLIC_API_ARCHIVE_DIR  # noqa: E402

DEFAULT_CSV = PUBLIC_API_ARCHIVE_DIR / "solar_permit_20260817" / "clean" / "solar_permit_clean.csv"
SOURCE_CODE = "public_solar_permit"
SOURCE_NAME = "공공데이터포털 전국 태양광발전시설 현황"
SOURCE_PROVIDER = "행정안전부·지방자치단체"
SOURCE_URL = "https://api.data.go.kr/openapi/tn_pubr_public_solar_gen_flct_api"
DEFAULT_PROVINCES = ("전라남도", "광주광역시")


def _float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if result == result else None


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def ensure_source_system(db: LuceraDB) -> str:
    source_system_id = stable_id("source_system", SOURCE_CODE)
    db.conn.execute(
        """INSERT INTO source_system (source_system_id, code, name, provider, base_url)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, provider=excluded.provider,
                                           base_url=excluded.base_url""",
        (source_system_id, SOURCE_CODE, SOURCE_NAME, SOURCE_PROVIDER, SOURCE_URL),
    )
    return str(
        db.conn.execute(
            "SELECT source_system_id FROM source_system WHERE code=?", (SOURCE_CODE,)
        ).fetchone()[0]
    )


def load(
    db: LuceraDB,
    csv_path: Path,
    provinces: tuple[str, ...] | None,
    *,
    skip: int = 0,
    time_budget: float | None = None,
) -> dict[str, int]:
    """Load matching rows, optionally resuming.

    The shell this runs in has a short wall-clock limit, so the loader is
    restartable: `skip` passes over the first N matching rows (row order in the
    CSV is stable) and `time_budget` stops cleanly before the shell is killed.
    Writes are upserts keyed on a stable project id, so an overlapping restart
    rewrites rows rather than duplicating them.
    """

    source_system_id = ensure_source_system(db)
    counts = {
        "read": 0,
        "matched": 0,
        "resumed_from": skip,
        "loaded": 0,
        "with_coordinates": 0,
        "with_install_area": 0,
        "skipped_no_key": 0,
        "stopped_early": 0,
    }
    started = time.monotonic()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            counts["read"] += 1
            province = _text(row.get("province_std"))
            if provinces and province not in provinces:
                continue
            counts["matched"] += 1
            if counts["matched"] <= skip:
                continue
            if time_budget is not None and time.monotonic() - started > time_budget:
                counts["stopped_early"] = 1
                break
            record_key = _text(row.get("record_id"))
            if not record_key:
                counts["skipped_no_key"] += 1
                continue

            coord_valid = (row.get("coord_valid") or "").strip().lower() == "true"
            latitude = _float(row.get("latitude")) if coord_valid else None
            longitude = _float(row.get("longitude")) if coord_valid else None
            install_area = _float(row.get("instlArea_num"))
            capacity = _float(row.get("capa_kw_clean"))

            metadata = {
                "data_origin": "public_api",
                "source_dataset": "tn_pubr_public_solar_gen_flct_api",
                "snapshot_date": _text(row.get("snapshot_date")),
                "criteria_date": _text(row.get("crtrYmd")),
                "installation_area_sqm": install_area,
                "installation_area_origin": "permit_register" if install_area else None,
                "install_year": _text(row.get("instlYr")),
                "detail_usage": _text(row.get("detlsUsg")),
                "install_position": _text(row.get("instlDtlPstnSeNm")),
                "capacity_qc_flag": _text(row.get("capa_qc_flag")),
                "permit_institution": _text(row.get("prmsnInst")),
                "region_source": _text(row.get("region_source")),
            }
            db.conn.execute(
                """INSERT INTO permit_project
                     (project_id, source_system_id, source_record_key, facility_name,
                      company_name, capacity_kw, permit_date, operation_status,
                      road_address, jibun_address, province, city_county, eup_myeon, ri,
                      latitude, longitude, location_status, metadata_json)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                     facility_name=excluded.facility_name,
                     capacity_kw=excluded.capacity_kw,
                     permit_date=excluded.permit_date,
                     operation_status=excluded.operation_status,
                     road_address=excluded.road_address,
                     jibun_address=excluded.jibun_address,
                     province=excluded.province,
                     city_county=excluded.city_county,
                     eup_myeon=excluded.eup_myeon,
                     latitude=excluded.latitude,
                     longitude=excluded.longitude,
                     location_status=excluded.location_status,
                     metadata_json=excluded.metadata_json""",
                (
                    stable_id("permit_project", SOURCE_CODE, record_key),
                    source_system_id,
                    record_key,
                    _text(row.get("solarGenFcltNm")),
                    capacity,
                    _text(row.get("prmsn_date")),
                    _text(row.get("oprtngSttsSeNm")),
                    _text(row.get("lctnRoadNmAddr")),
                    _text(row.get("lctnLotnoAddr")),
                    province,
                    _text(row.get("city_std")),
                    _text(row.get("emd_std")),
                    latitude,
                    longitude,
                    "confirmed" if coord_valid else "unknown",
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            counts["loaded"] += 1
            counts["with_coordinates"] += int(coord_valid)
            counts["with_install_area"] += int(install_area is not None)
            if counts["loaded"] % 2000 == 0:
                db.commit()
    db.commit()
    counts["next_skip"] = skip + counts["loaded"]
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument(
        "--provinces",
        nargs="*",
        default=list(DEFAULT_PROVINCES),
        help="적재할 시도. 비우면 전국을 적재합니다.",
    )
    parser.add_argument("--skip", type=int, default=0, help="이미 적재한 매칭 행 수(재시작용)")
    parser.add_argument("--time-budget-seconds", type=float, default=None)
    args = parser.parse_args()
    provinces = tuple(args.provinces) if args.provinces else None
    db = LuceraDB(args.db)
    try:
        result = load(db, args.csv, provinces, skip=args.skip, time_budget=args.time_budget_seconds)
        for key, value in result.items():
            print(f"{key}: {value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
