"""Official siting rules and the 2026 national setback cap.

`siting_rule` rows are the only place a distance threshold may come from: the
rule engine reads them, and nothing else is allowed to invent a number.  Two
kinds of row live here.

* `site_check` — a local ordinance distance the site must satisfy.  These are
  only seeded from a verified ordinance article; an unverified figure is worse
  than no figure, because a passing check would look official.
* `ordinance_cap` / `prohibited_criterion` — the national ceiling introduced by
  the 2026 시행령 amendment.  A cap does not itself decide a site; it bounds what
  a local ordinance may demand, so it is reported as a constraint on the check
  rather than as a pass/fail of the site.

Nothing here is region-specific yet.  When a 조례 article is confirmed, add it
through `upsert_rule` with `data_origin='official'` and the article reference.
"""

from __future__ import annotations

import json
from typing import Any

# Enforced from 2026-09-18 by the amended 재생에너지 개발·이용·보급 촉진법 시행령
# (promulgated 2026-08-11).  A local ordinance may still set a shorter distance,
# or none at all; it may no longer set a longer one.
NATIONAL_CAP_EFFECTIVE_DATE = "2026-09-18"
NATIONAL_CAP_SOURCE = "재생에너지 개발·이용·보급 촉진법 시행령(2026-08-11 공포)"
SOLAR_RESIDENCE_CAP_M = 200.0
RESIDENCE_HOUSEHOLD_THRESHOLD = 5
CAP_EXEMPT_TYPES = ("주민참여형", "건물지붕형", "자가소비용")


OFFICIAL_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "national-cap-2026-solar-residence",
        "region_code": None,
        "reference_object": "residence",
        "operator": "gte",
        "threshold_value": SOLAR_RESIDENCE_CAP_M,
        "unit": "m",
        "rule_name": "주거지 이격거리 국가 상한(200m)",
        "rule_description": (
            "조례가 요구할 수 있는 주거지 이격거리의 최대값입니다. "
            "주거지는 주택 5호 이상 밀집 지역을 뜻하며, 실제 적용 거리는 "
            "해당 시군 조례가 상한 이내에서 정합니다."
        ),
        "source_title": NATIONAL_CAP_SOURCE,
        "source_article": "이격거리 상한 기준",
        "valid_from": NATIONAL_CAP_EFFECTIVE_DATE,
        "valid_to": None,
        "severity": "high",
        "data_origin": "official",
        "metadata": {
            "rule_kind": "ordinance_cap",
            "cap_max_m": SOLAR_RESIDENCE_CAP_M,
            "household_threshold": RESIDENCE_HOUSEHOLD_THRESHOLD,
            "exempt_types": list(CAP_EXEMPT_TYPES),
            "promulgated": "2026-08-11",
        },
    },
    {
        "rule_id": "national-cap-2026-solar-road",
        "region_code": None,
        "reference_object": "road",
        "operator": "exists",
        "threshold_value": None,
        "unit": "m",
        "rule_name": "도로 이격거리 조례 설정 금지",
        "rule_description": (
            "2026-09-18부터 태양광 발전설비에 대해 도로를 기준으로 한 "
            "이격거리는 조례로 정할 수 없습니다. 기존 도로 이격거리 조항은 "
            "개정 대상입니다."
        ),
        "source_title": NATIONAL_CAP_SOURCE,
        "source_article": "이격거리 상한 기준",
        "valid_from": NATIONAL_CAP_EFFECTIVE_DATE,
        "valid_to": None,
        "severity": "medium",
        "data_origin": "official",
        "metadata": {
            "rule_kind": "prohibited_criterion",
            "promulgated": "2026-08-11",
        },
    },
)


def upsert_rule(db: Any, rule: dict[str, Any]) -> None:
    db.conn.execute(
        """INSERT INTO siting_rule
             (rule_id, region_code, reference_object, operator, threshold_value,
              unit, rule_name, rule_description, source_title, source_article,
              valid_from, valid_to, severity, data_origin, active, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
           ON CONFLICT(rule_id) DO UPDATE SET
             region_code=excluded.region_code,
             reference_object=excluded.reference_object,
             operator=excluded.operator,
             threshold_value=excluded.threshold_value,
             unit=excluded.unit,
             rule_name=excluded.rule_name,
             rule_description=excluded.rule_description,
             source_title=excluded.source_title,
             source_article=excluded.source_article,
             valid_from=excluded.valid_from,
             valid_to=excluded.valid_to,
             severity=excluded.severity,
             data_origin=excluded.data_origin,
             metadata_json=excluded.metadata_json,
             updated_at=CURRENT_TIMESTAMP""",
        (
            rule["rule_id"],
            rule["region_code"],
            rule["reference_object"],
            rule["operator"],
            rule["threshold_value"],
            rule["unit"],
            rule["rule_name"],
            rule["rule_description"],
            rule["source_title"],
            rule["source_article"],
            rule["valid_from"],
            rule["valid_to"],
            rule["severity"],
            rule["data_origin"],
            json.dumps(rule.get("metadata", {}), ensure_ascii=False),
        ),
    )


def seed_official_rules(db: Any) -> int:
    for rule in OFFICIAL_RULES:
        upsert_rule(db, rule)
    db.commit()
    return len(OFFICIAL_RULES)
