from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import PUBLIC_API_ARCHIVE_DIR


OUT = PUBLIC_API_ARCHIVE_DIR / "solar_permit_20260817"


def main() -> None:
    clean = OUT / "clean"
    solar = pd.read_csv(clean / "solar_permit_clean.csv", encoding="utf-8-sig", low_memory=False)
    kepco = pd.read_csv(clean / "kepco_connection_clean.csv", encoding="utf-8-sig", low_memory=False)

    exact_dedup = kepco.drop_duplicates().copy()
    line_groups = kepco.groupby("line_key", dropna=False)
    line_unique = line_groups.first().reset_index()
    line_unique["line_key_row_count"] = line_groups.size().reindex(line_unique["line_key"]).to_numpy()
    varying = line_groups[["substNm", "dlNm", "substPwr_kw", "mtrPwr_kw", "dlPwr_kw"]].nunique(dropna=False).max(axis=1)
    line_unique["line_key_conflict"] = line_unique["line_key"].map(varying).fillna(1).gt(1)

    exact_dedup.to_csv(clean / "kepco_connection_exact_dedup.csv", index=False, encoding="utf-8-sig")
    line_unique.to_csv(clean / "kepco_connection_line_unique.csv", index=False, encoding="utf-8-sig")

    line_name_ambiguity = (
        line_unique.groupby("dlNm", dropna=False)
        .agg(
            distinct_line_keys=("line_key", "nunique"),
            distinct_substations=("substCd", "nunique"),
            min_available_kw=("available_capacity_min_kw", "min"),
            max_available_kw=("available_capacity_min_kw", "max"),
        )
        .reset_index()
    )
    line_name_ambiguity = line_name_ambiguity[line_name_ambiguity["distinct_line_keys"] > 1].sort_values(
        ["distinct_substations", "distinct_line_keys"], ascending=False
    )
    line_name_ambiguity.to_csv(clean / "kepco_line_name_ambiguity.csv", index=False, encoding="utf-8-sig")

    target = solar[
        solar["province_std"].eq("전라남도")
        & solar["city_std"].eq("영암군")
        & solar["emd_std"].eq("삼호읍")
    ]
    target_summary = pd.DataFrame(
        [{
            "province_std": "전라남도",
            "city_std": "영암군",
            "emd_std": "삼호읍",
            "permit_rows": len(target),
            "capacity_clean_kw": target["capa_kw_clean"].sum(),
            "valid_coordinates": int(target["coord_valid"].sum()),
            "masked_substation_candidates": "|".join(sorted(set("|".join(target["candidate_substations"].dropna().astype(str)).split("|")))),
            "mapping_status": "|".join(sorted(target["mapping_status"].dropna().unique())),
            "warning": "No exact address-to-line mapping; all coordinates missing in this target subset.",
        }]
    )
    target_summary.to_csv(clean / "target_yeongam_samho_summary.csv", index=False, encoding="utf-8-sig")

    named_samho = line_unique[line_unique["dlNm"].astype(str).eq("삼호")].sort_values(["substNm", "mtrNo", "dlCd"])
    named_samho[["substCd", "substNm", "mtrNo", "dlCd", "dlNm", "available_capacity_min_kw", "rule_judgement_v1"]].to_csv(
        clean / "kepco_dl_name_samho_matches.csv", index=False, encoding="utf-8-sig"
    )

    summary_path = OUT / "build_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["kepco"]["exact_dedup_rows"] = int(len(exact_dedup))
    summary["kepco"]["line_unique_rows"] = int(len(line_unique))
    summary["kepco"]["line_key_duplicate_groups"] = int((line_groups.size() > 1).sum())
    summary["kepco"]["line_key_conflict_rows"] = int(line_unique["line_key_conflict"].sum())
    summary["kepco"]["ambiguous_line_name_count"] = int(len(line_name_ambiguity))
    summary["target_yeongam_samho"] = target_summary.iloc[0].to_dict()
    summary["new_outputs"] = [
        "clean/kepco_connection_exact_dedup.csv",
        "clean/kepco_connection_line_unique.csv",
        "clean/kepco_line_name_ambiguity.csv",
        "clean/kepco_dl_name_samho_matches.csv",
        "clean/target_yeongam_samho_summary.csv",
    ]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with (OUT / "README.md").open("a", encoding="utf-8") as f:
        f.write(
            "\nAdditional QA outputs:\n"
            "- `clean/kepco_connection_exact_dedup.csv`: exact duplicate rows removed.\n"
            "- `clean/kepco_connection_line_unique.csv`: one row per line key; conflicts are flagged.\n"
            "- `clean/kepco_line_name_ambiguity.csv`: feeder names repeated across multiple line keys.\n"
            "- `clean/kepco_dl_name_samho_matches.csv`: all current API rows whose feeder name is `삼호`.\n"
            "- `clean/target_yeongam_samho_summary.csv`: target subset used for feasibility review.\n"
        )
    print("FINALIZED", OUT)
    print("exact_dedup_rows", len(exact_dedup))
    print("line_unique_rows", len(line_unique))
    print("line_name_ambiguity_rows", len(line_name_ambiguity))
    print("target_rows", len(target), "target_coord_valid", int(target["coord_valid"].sum()))


if __name__ == "__main__":
    main()
