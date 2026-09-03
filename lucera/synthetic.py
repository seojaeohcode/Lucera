"""Coherent Yeongam-only fixtures for the offline product demo."""

from __future__ import annotations

import json
from typing import Any

from .db import LuceraDB, stable_id
from .review import rebuild_case_reviews


def _source_metadata(**extra: Any) -> dict[str, Any]:
    return {
        "fixture": True,
        "data_origin": "synthetic",
        "warning": "실제 민원·허가 원문이 아닌 기능 시연용 합성 데이터",
        **extra,
    }


def _place(surface: str, normalized: str, *, eup_myeon: str, ri: str, latitude: float, longitude: float) -> dict[str, Any]:
    return {
        "surface_form": surface,
        "raw_name": surface,
        "normalized_name": normalized,
        "place_type": "jibun_address",
        "province": "전라남도",
        "city_county": "영암군",
        "eup_myeon": eup_myeon,
        "ri": ri,
        "geo_precision": "jibun_address",
        "latitude": latitude,
        "longitude": longitude,
        "relation_type": "subject_site",
        "confidence": 0.98,
        "resolution_method": "synthetic_fixture",
        "resolution_reason": "영암군 합성 시나리오에 정의된 설치·영향 위치",
        "metadata": {"data_origin": "synthetic"},
    }


def synthetic_bundles() -> list[dict[str, Any]]:
    """Return one relational story used by the map, RAG, and chat tests."""

    base = {"system_code": "demo_fixture", "document_type": "meeting_minutes", "mime_type": "text/plain", "access_policy": "demo"}
    return [
        {
            "source": {
                **base,
                "source_record_key": "synthetic-yeongam-samho-drainage",
                "title": "[합성] 영암군 삼호읍 태양광 배수·주민협의 과정",
                "source_url": "synthetic://minutes/yeongam-samho-drainage",
                "metadata": _source_metadata(scenario="environment_and_consultation"),
            },
            "meeting": {
                "assembly_name": "[합성] 전라남도 영암군의회",
                "province": "전라남도",
                "city_county": "영암군",
                "meeting_title": "[합성] 삼호읍 태양광 개발행위 주민협의 보고",
                "meeting_type": "민원·현안 보고",
                "meeting_date": "2024-08-21",
            },
            "page": {"text_original": "[합성] 삼호읍 태양광 개발행위 주민협의 보고"},
            "segments": [
                {
                    "text_original": "삼호읍 가상리 45-2 발전사업에 대해 주민들이 집중호우 때 배수와 토사 유출을 우려하는 민원을 제기했다.",
                    "segment_type": "speech",
                    "speaker_name": "최주민",
                    "speaker_role": "주민",
                    "issues": [
                        {"issue_code": "safety_environment", "polarity": "opposition", "confidence": 0.96, "evidence_span": "배수와 토사 유출", "metadata": {"data_origin": "synthetic"}},
                        {"issue_code": "communication_procedure", "polarity": "opposition", "confidence": 0.82, "evidence_span": "민원을 제기", "metadata": {"data_origin": "synthetic"}},
                    ],
                    "places": [_place("삼호읍 가상리 45-2", "전라남도 영암군 삼호읍 가상리 45-2", eup_myeon="삼호읍", ri="가상리", latitude=34.80, longitude=126.42)],
                    "relevant": True,
                },
                {
                    "text_original": "행정기관은 현장 점검과 배수계획 검토를 요청했고, 사업자는 주민 설명회를 진행하기로 했다.",
                    "segment_type": "speech",
                    "speaker_name": "김국장",
                    "speaker_role": "국장",
                    "issues": [
                        {"issue_code": "safety_environment", "polarity": "opposition", "confidence": 0.91, "evidence_span": "현장 점검과 배수계획 검토", "metadata": {"data_origin": "synthetic"}},
                        {"issue_code": "communication_procedure", "polarity": "support", "confidence": 0.88, "evidence_span": "주민 설명회를 진행하기로", "metadata": {"data_origin": "synthetic"}},
                    ],
                    "relevant": True,
                },
                {
                    "text_original": "주민 설명회 후 차폐와 배수 보완안을 검토했지만, 조치 완료 여부는 후속 확인이 필요한 상태로 남았다.",
                    "segment_type": "speech",
                    "speaker_name": "이담당",
                    "speaker_role": "과장",
                    "issues": [{"issue_code": "communication_procedure", "polarity": "mixed", "confidence": 0.84, "evidence_span": "후속 확인이 필요한 상태", "metadata": {"data_origin": "synthetic"}}],
                    "relevant": True,
                },
            ],
        }
    ]


def _region_code(db: LuceraDB) -> str | None:
    row = db.conn.execute("SELECT region_code FROM administrative_region WHERE region_name='영암군' LIMIT 1").fetchone()
    return str(row[0]) if row else None


def seed_siting_rules(db: LuceraDB) -> int:
    rules = [
        ("synthetic-yeongam-residence-200", "residence", "gte", 200, "m", "주거지 이격거리(합성)", "주거지까지 최소 200m 이상인지 확인하는 시연용 규칙", "제19조의3", "high"),
        ("synthetic-yeongam-road-100", "road", "gte", 100, "m", "도로 이격거리(합성)", "도로까지 최소 100m 이상인지 확인하는 시연용 규칙", "제19조의3", "medium"),
    ]
    inserted = 0
    for rule_id, reference_object, operator, threshold, unit, name, description, article, severity in rules:
        db.conn.execute(
            """INSERT INTO siting_rule
               (rule_id, region_code, reference_object, operator, threshold_value,
                unit, rule_name, rule_description, source_title, source_article,
                severity, data_origin, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[합성] 영암군 도시계획 조례', ?, ?, 'synthetic', ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                 region_code=excluded.region_code, reference_object=excluded.reference_object,
                 operator=excluded.operator, threshold_value=excluded.threshold_value,
                 unit=excluded.unit, rule_name=excluded.rule_name,
                 rule_description=excluded.rule_description, source_title=excluded.source_title,
                 source_article=excluded.source_article, severity=excluded.severity,
                 data_origin=excluded.data_origin, metadata_json=excluded.metadata_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (rule_id, _region_code(db), reference_object, operator, threshold, unit, name, description, article, severity, json.dumps({"data_origin": "synthetic", "region_name": "영암군"}, ensure_ascii=False)),
        )
        inserted += 1
    return inserted


def seed_permit_projects(db: LuceraDB) -> int:
    source_id = db._source_id("demo_fixture")
    projects = [
        ("synthetic-permit-001", "영암 삼호 태양광 A", "합성에너지A", 900, "2022-05-20", "허가", "전라남도 영암군 삼호읍 가상리 45-2", 34.80, 126.42, {"site_area_sqm": 13000, "installation_area_sqm": 9000}),
        ("synthetic-permit-002", "영암 삼호 태양광 B", "합성에너지B", 650, "2021-10-04", "사업개시", "전라남도 영암군 삼호읍 가상리 51-2", 34.807, 126.414, {"site_area_sqm": 9800, "installation_area_sqm": 6500}),
    ]
    for key, facility, company, capacity, permit_date, status, address, latitude, longitude, metadata in projects:
        project_id = stable_id("synthetic_permit_project", key)
        metadata = {**metadata, "data_origin": "synthetic"}
        db.conn.execute(
            """INSERT INTO permit_project
               (project_id, source_system_id, source_record_key, facility_name,
                company_name, capacity_kw, permit_date, operation_status,
                province, city_county, eup_myeon, ri, latitude, longitude,
                road_address, jibun_address, location_status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '전라남도', '영암군', '삼호읍', '가상리', ?, ?, ?, ?, 'confirmed', ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 facility_name=excluded.facility_name, company_name=excluded.company_name,
                 capacity_kw=excluded.capacity_kw, permit_date=excluded.permit_date,
                 operation_status=excluded.operation_status, latitude=excluded.latitude,
                 longitude=excluded.longitude, jibun_address=excluded.jibun_address,
                 location_status=excluded.location_status, metadata_json=excluded.metadata_json""",
            (project_id, source_id, key, facility, company, capacity, permit_date, status, latitude, longitude, address, address, json.dumps(metadata, ensure_ascii=False)),
        )
    return len(projects)


def seed_synthetic(db: LuceraDB) -> dict[str, int]:
    document_count = 0
    for bundle in synthetic_bundles():
        db.insert_document_bundle(bundle)
        document_count += 1
    rule_count = seed_siting_rules(db)
    permit_count = seed_permit_projects(db)
    review_counts = rebuild_case_reviews(db)
    db.commit()
    return {"documents": document_count, "siting_rules": rule_count, "permit_projects": permit_count, "review_cases": review_counts["cases"]}
