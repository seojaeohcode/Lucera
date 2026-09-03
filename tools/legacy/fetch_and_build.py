from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import PUBLIC_API_ARCHIVE_DIR, SOURCE_MATERIALS_DIR


OUT = PUBLIC_API_ARCHIVE_DIR / "solar_permit_20260817"
SOLAR_URL = "https://api.data.go.kr/openapi/tn_pubr_public_solar_gen_flct_api"
KEPCO_URL = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"
SOLAR_PAGE_SIZE = 1000
TARGET_DISTRICTS = {"광산구", "북구", "서구", "동구", "남구"}


def die(message: str) -> None:
    raise RuntimeError(message)


def request_json(url: str, params: dict[str, str]) -> tuple[Any, bytes]:
    query = urlencode(params)
    req = Request(url + "?" + query, headers={"User-Agent": "solacheck-data-builder/1.0"})
    with urlopen(req, timeout=90) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        text = body.decode(charset, errors="replace").lstrip("\ufeff")
        try:
            return json.loads(text), body
        except json.JSONDecodeError as exc:
            die(f"Non-JSON response from {url}: {text[:500]!r}; {exc}")


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def fetch_solar(raw_key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = unquote(raw_key)
    first, _ = request_json(
        SOLAR_URL,
        {"serviceKey": key, "pageNo": "1", "numOfRows": str(SOLAR_PAGE_SIZE), "type": "json"},
    )
    body = first.get("body", {}) if isinstance(first, dict) else {}
    header = first.get("header", {}) if isinstance(first, dict) else {}
    if str(header.get("resultCode")) != "00":
        die(f"Solar API error: {header}")
    total = int(body.get("totalCount", 0))
    first_items = parse_items(body.get("items", {}).get("item", []))
    if total <= 0 or not first_items:
        die(f"Solar API returned no data: total={total}, first_items={len(first_items)}")
    records = list(first_items)
    total_pages = (total + SOLAR_PAGE_SIZE - 1) // SOLAR_PAGE_SIZE
    for page in range(2, total_pages + 1):
        payload, _ = request_json(
            SOLAR_URL,
            {"serviceKey": key, "pageNo": str(page), "numOfRows": str(SOLAR_PAGE_SIZE), "type": "json"},
        )
        pbody = payload.get("body", {}) if isinstance(payload, dict) else {}
        pheader = payload.get("header", {}) if isinstance(payload, dict) else {}
        if str(pheader.get("resultCode")) != "00":
            die(f"Solar API error on page {page}: {pheader}")
        page_items = parse_items(pbody.get("items", {}).get("item", []))
        if not page_items:
            die(f"Solar API returned empty page {page}/{total_pages}")
        records.extend(page_items)
        if page == 2 or page % 10 == 0 or page == total_pages:
            print(f"solar_page={page}/{total_pages} records={len(records)}", flush=True)
        time.sleep(0.12)
    if len(records) != total:
        die(f"Solar count mismatch: api_total={total}, fetched={len(records)}")
    return records, {
        "endpoint": SOLAR_URL,
        "api_total_count": total,
        "page_size": SOLAR_PAGE_SIZE,
        "pages": total_pages,
    }


def fetch_kepco(key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload, raw = request_json(KEPCO_URL, {"apiKey": key, "returnType": "json"})
    records = parse_items(payload.get("data", []) if isinstance(payload, dict) else [])
    if not records:
        die(f"KEPCO API returned no data. keys={list(payload) if isinstance(payload, dict) else type(payload)}")
    return records, {
        "endpoint": KEPCO_URL,
        "api_response_bytes": len(raw),
        "records": len(records),
        "top_keys": list(payload.keys()) if isinstance(payload, dict) else [],
    }


def read_csv_any(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=encoding, low_memory=False)
        except Exception:
            continue
    die(f"Unable to read CSV: {path}")


def stable_id(row: pd.Series, columns: list[str]) -> str:
    text = "|".join("" if pd.isna(row[c]) else str(row[c]) for c in columns)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def normalize_province(province: Any, city: Any) -> str:
    p = "" if pd.isna(province) else str(province).strip()
    c = "" if pd.isna(city) else str(city).strip()
    if p == "전남광주통합특별시":
        return "광주광역시" if c in TARGET_DISTRICTS else "전라남도"
    if p in {"전남", "전라남도"}:
        return "전라남도"
    if p in {"광주", "광주광역시"}:
        return "광주광역시"
    return p


def parse_region(address: Any, fallback: Any) -> tuple[str, str, str, str]:
    text = "" if pd.isna(address) else str(address).strip()
    source = "address" if text else "insttNm"
    if not text:
        text = "" if pd.isna(fallback) else str(fallback).strip()
    tokens = text.split()
    if len(tokens) < 2:
        return "", "", "", source
    province, city = tokens[0], tokens[1]
    province = normalize_province(province, city)
    emd = ""
    match = re.search(r"([가-힣]+(?:읍|면|동))", text)
    if match:
        emd = match.group(1)
    return province, city, emd, source


def clean_solar(records: list[dict[str, Any]], mapping_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    solar = pd.DataFrame(records)
    expected = [
        "solarGenFcltNm", "lctnRoadNmAddr", "lctnLotnoAddr", "latitude", "longitude",
        "instlDtlPstnSeNm", "oprtngSttsSeNm", "capa", "splyVolt", "freq", "instlYr",
        "detlsUsg", "prmsnYmd", "prmsnInst", "instlArea", "crtrYmd", "insttCode", "insttNm",
    ]
    for col in expected:
        if col not in solar.columns:
            solar[col] = np.nan
    solar = solar[expected].copy()
    solar["capa_kw_raw"] = solar["capa"]
    solar["capa_kw"] = pd.to_numeric(solar["capa"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    solar["latitude"] = pd.to_numeric(solar["latitude"], errors="coerce")
    solar["longitude"] = pd.to_numeric(solar["longitude"], errors="coerce")
    solar["freq_hz"] = pd.to_numeric(solar["freq"], errors="coerce")
    solar["instlYr_num"] = pd.to_numeric(solar["instlYr"], errors="coerce")
    solar["instlArea_num"] = pd.to_numeric(solar["instlArea"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    solar["prmsn_date"] = pd.to_datetime(solar["prmsnYmd"], errors="coerce")
    solar["snapshot_date"] = pd.to_datetime(solar["crtrYmd"], errors="coerce")
    solar["coord_valid"] = solar["latitude"].between(33, 39, inclusive="both") & solar["longitude"].between(124, 132, inclusive="both")
    solar["capa_qc_flag"] = np.select(
        [solar["capa_kw"].isna(), solar["capa_kw"].le(0), solar["capa_kw"].gt(200_000)],
        ["parse_error", "zero_or_negative", "outlier_gt_200MW"],
        default="ok",
    )
    solar["capa_kw_clean"] = solar["capa_kw"].where(solar["capa_qc_flag"].eq("ok"))
    region_values = solar.apply(
        lambda row: parse_region(row["lctnLotnoAddr"] if pd.notna(row["lctnLotnoAddr"]) else row["lctnRoadNmAddr"], row["insttNm"]),
        axis=1,
        result_type="expand",
    )
    region_values.columns = ["province_std", "city_std", "emd_std", "region_source"]
    solar = pd.concat([solar, region_values], axis=1)
    solar["record_id"] = solar.apply(lambda row: stable_id(row, ["solarGenFcltNm", "lctnLotnoAddr", "capa", "prmsnYmd", "insttCode"]), axis=1)
    solar["queue_status"] = "unknown_no_grid_application_field"

    mapping = read_csv_any(mapping_path)
    mapping["province_std"] = mapping.apply(lambda r: normalize_province(r.iloc[0], r.iloc[1]), axis=1)
    mapping["city_std"] = mapping.iloc[:, 1].astype(str).str.strip()
    mapping["emd_std"] = mapping.iloc[:, 2].astype(str).str.strip()
    mapping["candidate_substations"] = mapping.iloc[:, 3].astype(str).str.strip()
    grouped = (
        mapping.groupby(["province_std", "city_std", "emd_std"], dropna=False)["candidate_substations"]
        .agg(lambda values: "|".join(sorted(set(v for v in values if v and v.lower() != "nan"))))
        .reset_index()
    )
    grouped["candidate_count"] = grouped["candidate_substations"].map(lambda x: 0 if not x else len(x.split("|")))
    solar = solar.merge(grouped, on=["province_std", "city_std", "emd_std"], how="left")
    solar["candidate_count"] = solar["candidate_count"].fillna(0).astype(int)
    solar["mapping_status"] = np.select(
        [solar["candidate_count"].eq(0), solar["candidate_count"].eq(1), solar["candidate_count"].gt(1)],
        ["no_candidate", "single_candidate_masked", "multiple_candidates_masked"],
        default="unknown",
    )
    summary = {
        "rows": int(len(solar)),
        "coord_valid": int(solar["coord_valid"].sum()),
        "coord_missing_or_invalid": int((~solar["coord_valid"]).sum()),
        "permit_date_missing": int(solar["prmsn_date"].isna().sum()),
        "capacity_qc": solar["capa_qc_flag"].value_counts(dropna=False).to_dict(),
        "capacity_clean_sum_kw": float(solar["capa_kw_clean"].sum()),
        "snapshot_min": solar["snapshot_date"].min().strftime("%Y-%m-%d") if solar["snapshot_date"].notna().any() else None,
        "snapshot_max": solar["snapshot_date"].max().strftime("%Y-%m-%d") if solar["snapshot_date"].notna().any() else None,
        "snapshot_unique": int(solar["snapshot_date"].nunique()),
        "mapping_status": solar["mapping_status"].value_counts(dropna=False).to_dict(),
        "queue_status": "not_inferred",
    }
    return solar, summary


def clean_kepco(records: list[dict[str, Any]]) -> tuple[pd.DataFrame, dict[str, Any]]:
    kepco = pd.DataFrame(records)
    expected = ["substCd", "substNm", "jsSubstPwr", "substPwr", "mtrNo", "jsMtrPwr", "mtrPwr", "dlCd", "dlNm", "jsDlPwr", "dlPwr", "vol1", "vol2", "vol3"]
    for col in expected:
        if col not in kepco.columns:
            kepco[col] = np.nan
    kepco = kepco[expected].copy()
    for col in ["jsSubstPwr", "substPwr", "jsMtrPwr", "mtrPwr", "jsDlPwr", "dlPwr", "vol1", "vol2", "vol3"]:
        kepco[f"{col}_kw"] = pd.to_numeric(kepco[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    capacity_cols = ["substPwr_kw", "mtrPwr_kw", "dlPwr_kw"]
    kepco["capacity_fields_complete"] = kepco[capacity_cols].notna().all(axis=1)
    kepco["available_capacity_min_kw"] = kepco[capacity_cols].min(axis=1, skipna=False)
    kepco["rule_judgement_v1"] = np.select(
        [kepco["available_capacity_min_kw"].ge(1000), kepco["available_capacity_min_kw"].gt(0)],
        ["가능_heuristic", "위험_heuristic"],
        default="불가_or_missing_heuristic",
    )
    kepco["line_key"] = kepco[["substCd", "mtrNo", "dlCd"]].astype(str).agg("|".join, axis=1)
    kepco["record_id"] = kepco.apply(lambda row: stable_id(row, ["substCd", "mtrNo", "dlCd", "substNm", "dlNm"]), axis=1)
    summary = {
        "rows": int(len(kepco)),
        "unique_line_keys": int(kepco["line_key"].nunique()),
        "duplicate_full_rows": int(kepco.duplicated().sum()),
        "duplicate_line_keys": int(kepco.duplicated("line_key").sum()),
        "unique_substations": int(kepco["substCd"].nunique()),
        "unique_lines": int(kepco["dlCd"].nunique()),
        "capacity_fields_complete": int(kepco["capacity_fields_complete"].sum()),
        "rule_judgement_v1": kepco["rule_judgement_v1"].value_counts(dropna=False).to_dict(),
        "js_capacity_zero_counts": {col: int((kepco[f"{col}_kw"] == 0).sum()) for col in ["jsSubstPwr", "jsMtrPwr", "jsDlPwr"]},
    }
    return kepco, summary


def main() -> None:
    solar_key = os.environ.get("SOLAR_KEY_RAW")
    kepco_key = os.environ.get("KEPCO_KEY")
    if not solar_key or not kepco_key:
        die("Missing task-specific API key environment variables")
    if OUT.exists():
        die(f"Output exists; refusing to overwrite: {OUT}")
    OUT.mkdir(parents=True)
    (OUT / "raw").mkdir()
    (OUT / "clean").mkdir()

    mapping_candidates = [
        p for p in SOURCE_MATERIALS_DIR.rglob("*.csv")
        if "__MACOSX" not in p.parts and len(read_csv_any(p).columns) == 4
    ]
    if not mapping_candidates:
        die("4-column administrative substation mapping CSV not found")
    mapping_path = mapping_candidates[0]

    fetched_at = datetime.now(timezone.utc).isoformat()
    print("fetching_solar", flush=True)
    solar_records, solar_meta = fetch_solar(solar_key)
    print("fetching_kepco", flush=True)
    kepco_records, kepco_meta = fetch_kepco(kepco_key)

    save_json(OUT / "raw" / "solar_api_records.json", {"metadata": {"fetched_at": fetched_at, **solar_meta}, "records": solar_records})
    save_json(OUT / "raw" / "kepco_api_records.json", {"metadata": {"fetched_at": fetched_at, **kepco_meta}, "records": kepco_records})
    (OUT / "raw" / "source_mapping_path.txt").write_text(str(mapping_path), encoding="utf-8")

    print("cleaning_solar", flush=True)
    solar, solar_summary = clean_solar(solar_records, mapping_path)
    print("cleaning_kepco", flush=True)
    kepco, kepco_summary = clean_kepco(kepco_records)

    solar.to_csv(OUT / "clean" / "solar_permit_clean.csv", index=False, encoding="utf-8-sig")
    kepco.to_csv(OUT / "clean" / "kepco_connection_clean.csv", index=False, encoding="utf-8-sig")

    summary = {
        "built_at_utc": fetched_at,
        "inputs": {"solar_endpoint": SOLAR_URL, "kepco_endpoint": KEPCO_URL, "mapping_file": str(mapping_path)},
        "solar": solar_summary,
        "kepco": kepco_summary,
        "development_limits": [
            "Permit data does not contain grid application date or business commencement date nationwide.",
            "KEPCO API rows provide line capacity fields but no address-to-line geometry in this response.",
            "rule_judgement_v1 is a demo heuristic, not a technical connection decision.",
            "Historical snapshots must be collected over time before training a forecast model.",
        ],
    }
    save_json(OUT / "build_summary.json", summary)
    (OUT / "README.md").write_text(
        "# API build output\n\n"
        "This directory was built from the two user-supplied APIs. API keys are not stored here.\n\n"
        "- `raw/solar_api_records.json`: full national solar permit API records and metadata.\n"
        "- `raw/kepco_api_records.json`: full current KEPCO distributed-generation response and metadata.\n"
        "- `clean/solar_permit_clean.csv`: typed fields, capacity/coordinate QC, normalized region fields, and masked administrative candidates.\n"
        "- `clean/kepco_connection_clean.csv`: typed capacity fields, duplicate keys, and a clearly labeled heuristic judgement.\n"
        "- `build_summary.json`: counts, missingness, QC, duplicate statistics, and limitations.\n\n"
        "Do not interpret `rule_judgement_v1` as a guarantee of technical connection.\n",
        encoding="utf-8",
    )
    print(f"DONE out={OUT}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
