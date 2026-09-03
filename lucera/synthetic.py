"""Coherent synthetic fixtures for an offline chatbot demonstration.

The fixtures are intentionally relational rather than random: a proposed site
has an area/capacity, nearby permit records, meeting evidence, and a process
timeline. Every synthetic value is marked in metadata so it cannot be
mistaken for an official record.
"""

from __future__ import annotations

import json
from typing import Any

from .db import LuceraDB, stable_id
from .review import rebuild_case_reviews


def _source_metadata(**extra: Any) -> dict[str, Any]:
    return {"fixture": True, "data_origin": "synthetic", "warning": "실제 민원·허가 원문이 아닌 기능 시연용 합성 데이터", **extra}


def _place(
    surface: str,
    normalized: str,
    *,
    city_county: str,
    eup_myeon: str,
    ri: str,
    latitude: float,
    longitude: float,
    place_type: str = "jibun_address",
    precision: str = "jibun_address",
    relation: str = "subject_site",
) -> dict[str, Any]:
    return {
        "surface_form": surface,
        "raw_name": surface,
        "normalized_name": normalized,
        "place_type": place_type,
        "province": "전라남도",
        "city_county": city_county,
        "eup_myeon": eup_myeon,
        "ri": ri,
        "geo_precision": precision,
        "latitude": latitude,
        "longitude": longitude,
        "relation_type": relation,
        "confidence": 0.98,
        "resolution_method": "synthetic_fixture",
        "resolution_reason": "합성 시나리오에 정의된 설치·영향 위치",
        "metadata": {"data_origin": "synthetic"},
    }


def synthetic_bundles() -> list[dict[str, Any]]:
    base = {
        "system_code": "demo_fixture",
        "document_type": "meeting_minutes",
        "mime_type": "text/plain",
        "access_policy": "demo",
    }
    return [
        {
            "source": {
                **base,
                "source_record_key": "synthetic-hampyeong-glare-process",
                "title": "[합성] 함평군 손불면 빛반사 민원 처리 과정",
                "source_url": "synthetic://minutes/hampyeong-glare-process",
                "metadata": _source_metadata(scenario="rule_fail_and_unresolved_process"),
            },
            "meeting": {
                "assembly_name": "[합성] 전라남도 함평군의회",
                "province": "전라남도",
                "city_county": "함평군",
                "meeting_title": "[합성] 손불면 태양광 주민 민원 처리 보고",
                "meeting_type": "민원 보고",
                "meeting_date": "2025-04-12",
            },
            "page": {"text_original": "[합성] 손불면 태양광 주민 민원 처리 보고"},
            "segments": [
                {
                    "text_original": "손불면 가상리 123-4 태양광 시설 인근 주거지에서 빛반사와 눈부심 민원이 접수되었다.",
                    "segment_type": "speech",
                    "speaker_name": "이담당",
                    "speaker_role": "과장",
                    "issues": [{"issue_code": "glare_reflection", "polarity": "opposition", "confidence": 0.98, "evidence_span": "빛반사와 눈부심 민원이 접수", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}}],
                    "places": [_place("손불면 가상리 123-4", "전라남도 함평군 손불면 가상리 123-4", city_county="함평군", eup_myeon="손불면", ri="가상리", latitude=35.10, longitude=126.52)],
                    "relevant": True,
                },
                {
                    "text_original": "담당 부서는 현장 조사를 진행하고 반사 방향과 발생 시간대를 확인하기로 했다.",
                    "segment_type": "speech",
                    "speaker_name": "이담당",
                    "speaker_role": "과장",
                    "issues": [{"issue_code": "glare_reflection", "polarity": "opposition", "confidence": 0.91, "evidence_span": "반사 방향과 발생 시간대", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}}],
                    "relevant": True,
                },
                {
                    "text_original": "주민 설명회를 열고 차폐시설을 검토했지만, 후속 회의록에는 조치 완료 여부가 확인되지 않았다.",
                    "segment_type": "speech",
                    "speaker_name": "박의원",
                    "speaker_role": "의원",
                    "issues": [
                        {"issue_code": "communication_procedure", "polarity": "mixed", "confidence": 0.93, "evidence_span": "주민 설명회를 열고", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                        {"issue_code": "glare_reflection", "polarity": "opposition", "confidence": 0.86, "evidence_span": "차폐시설", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                    ],
                    "relevant": True,
                },
            ],
        },
        {
            "source": {
                **base,
                "source_record_key": "synthetic-yeongam-drainage-process",
                "title": "[합성] 영암군 삼호읍 태양광 배수·주민협의 과정",
                "source_url": "synthetic://minutes/yeongam-drainage-process",
                "metadata": _source_metadata(scenario="conditional_environment_and_consultation"),
            },
            "meeting": {
                "assembly_name": "[합성] 전라남도 영암군의회",
                "province": "전라남도",
                "city_county": "영암군",
                "meeting_title": "[합성] 삼호읍 태양광 개발행위 주민협의 보고",
                "meeting_type": "현안 보고",
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
                        {"issue_code": "safety_environment", "polarity": "opposition", "confidence": 0.96, "evidence_span": "배수와 토사 유출", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                        {"issue_code": "communication_procedure", "polarity": "opposition", "confidence": 0.82, "evidence_span": "민원을 제기", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                    ],
                    "places": [_place("삼호읍 가상리 45-2", "전라남도 영암군 삼호읍 가상리 45-2", city_county="영암군", eup_myeon="삼호읍", ri="가상리", latitude=34.80, longitude=126.42)],
                    "relevant": True,
                },
                {
                    "text_original": "행정기관은 현장 점검과 배수계획 검토를 요청했고, 사업자는 주민 설명회를 진행하기로 했다.",
                    "segment_type": "speech",
                    "speaker_name": "김국장",
                    "speaker_role": "국장",
                    "issues": [
                        {"issue_code": "safety_environment", "polarity": "opposition", "confidence": 0.91, "evidence_span": "현장 점검과 배수계획 검토", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                        {"issue_code": "communication_procedure", "polarity": "support", "confidence": 0.88, "evidence_span": "주민 설명회를 진행하기로", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                    ],
                    "relevant": True,
                },
            ],
        },
        {
            "source": {
                **base,
                "source_record_key": "synthetic-muan-landscape-process",
                "title": "[합성] 무안군 해제면 경관 협의 완료 사례",
                "source_url": "synthetic://minutes/muan-landscape-process",
                "metadata": _source_metadata(scenario="caution_with_mitigation"),
            },
            "meeting": {
                "assembly_name": "[합성] 전라남도 무안군의회",
                "province": "전라남도",
                "city_county": "무안군",
                "meeting_title": "[합성] 해제면 태양광 경관 보완 협의",
                "meeting_type": "협의 보고",
                "meeting_date": "2023-11-03",
            },
            "page": {"text_original": "[합성] 해제면 태양광 경관 보완 협의"},
            "segments": [
                {
                    "text_original": "해제면 가상리 88-1 태양광 사업은 주요 조망점의 경관 우려가 제기되어 차폐 식재를 검토했다.",
                    "segment_type": "speech",
                    "speaker_name": "정의원",
                    "speaker_role": "의원",
                    "issues": [{"issue_code": "landscape_damage", "polarity": "opposition", "confidence": 0.95, "evidence_span": "경관 우려", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}}],
                    "places": [_place("해제면 가상리 88-1", "전라남도 무안군 해제면 가상리 88-1", city_county="무안군", eup_myeon="해제면", ri="가상리", latitude=35.06, longitude=126.29)],
                    "relevant": True,
                },
                {
                    "text_original": "주민 설명회 이후 차폐 식재 계획을 보완했고, 담당 부서는 조치 완료를 확인했다.",
                    "segment_type": "speech",
                    "speaker_name": "오과장",
                    "speaker_role": "과장",
                    "issues": [
                        {"issue_code": "communication_procedure", "polarity": "support", "confidence": 0.92, "evidence_span": "주민 설명회 이후", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                        {"issue_code": "landscape_damage", "polarity": "support", "confidence": 0.91, "evidence_span": "조치 완료를 확인", "metadata": {"source_kind": "manual", "data_origin": "synthetic"}},
                    ],
                    "relevant": True,
                },
            ],
        },
    ]


def _region_code(db: LuceraDB, name: str) -> str | None:
    row = db.conn.execute("SELECT region_code FROM administrative_region WHERE region_name=? LIMIT 1", (name,)).fetchone()
    return str(row[0]) if row else None


def seed_siting_rules(db: LuceraDB) -> int:
    rules = [
        ("synthetic-hampyeong-residence-200", "함평군", "residence", "gte", 200, "m", "주거지 이격거리(합성)", "주거지까지 최소 200m 이상인지 확인하는 시연용 규칙", "[합성] 함평군 도시계획 조례", "제19조의3", "high"),
        ("synthetic-hampyeong-road-100", "함평군", "road", "gte", 100, "m", "도로 이격거리(합성)", "도로까지 최소 100m 이상인지 확인하는 시연용 규칙", "[합성] 함평군 도시계획 조례", "제19조의3", "medium"),
        ("synthetic-yeongam-residence-200", "영암군", "residence", "gte", 200, "m", "주거지 이격거리(합성)", "주거지까지 최소 200m 이상인지 확인하는 시연용 규칙", "[합성] 영암군 도시계획 조례", "제19조의3", "high"),
        ("synthetic-yeongam-road-100", "영암군", "road", "gte", 100, "m", "도로 이격거리(합성)", "도로까지 최소 100m 이상인지 확인하는 시연용 규칙", "[합성] 영암군 도시계획 조례", "제19조의3", "medium"),
    ]
    inserted = 0
    for rule_id, region_name, reference_object, operator, threshold, unit, name, description, source_title, article, severity in rules:
        region_code = _region_code(db, region_name)
        db.conn.execute(
            """INSERT INTO siting_rule
               (rule_id, region_code, reference_object, operator, threshold_value,
                unit, rule_name, rule_description, source_title, source_article,
                severity, data_origin, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic', ?)
               ON CONFLICT(rule_id) DO UPDATE SET
                 region_code=excluded.region_code, reference_object=excluded.reference_object,
                 operator=excluded.operator, threshold_value=excluded.threshold_value,
                 unit=excluded.unit, rule_name=excluded.rule_name,
                 rule_description=excluded.rule_description, source_title=excluded.source_title,
                 source_article=excluded.source_article, severity=excluded.severity,
                 data_origin=excluded.data_origin, metadata_json=excluded.metadata_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (rule_id, region_code, reference_object, operator, threshold, unit, name, description, source_title, article, severity, json.dumps({"data_origin": "synthetic", "region_name": region_name}, ensure_ascii=False)),
        )
        inserted += 1
    return inserted


def seed_permit_projects(db: LuceraDB) -> int:
    source_id = db._source_id("demo_fixture")
    projects = [
        ("synthetic-permit-001", "함평 손불 태양광 A", "합성에너지A", 480, "2024-03-01", "허가", "전라남도 함평군 손불면 가상리 123-4", "35.1000", "126.5200", {"site_area_sqm": 6200, "installation_area_sqm": 4500, "data_origin": "synthetic"}),
        ("synthetic-permit-002", "함평 손불 태양광 B", "합성에너지B", 300, "2023-09-12", "사업개시", "전라남도 함평군 손불면 가상리 20-1", "35.0940", "126.5140", {"site_area_sqm": 4400, "installation_area_sqm": 2800, "data_origin": "synthetic"}),
        ("synthetic-permit-003", "영암 삼호 태양광 A", "합성에너지C", 900, "2022-05-20", "허가", "전라남도 영암군 삼호읍 가상리 45-2", "34.8000", "126.4200", {"site_area_sqm": 13000, "installation_area_sqm": 9000, "data_origin": "synthetic"}),
        ("synthetic-permit-004", "영암 삼호 태양광 B", "합성에너지D", 650, "2021-10-04", "사업개시", "전라남도 영암군 삼호읍 가상리 51-2", "34.8070", "126.4140", {"site_area_sqm": 9800, "installation_area_sqm": 6500, "data_origin": "synthetic"}),
        ("synthetic-permit-005", "무안 해제 태양광 A", "합성에너지E", 200, "2022-02-14", "사업개시", "전라남도 무안군 해제면 가상리 88-1", "35.0600", "126.2900", {"site_area_sqm": 3200, "installation_area_sqm": 2100, "data_origin": "synthetic"}),
    ]
    for key, facility, company, capacity, permit_date, status, address, lat, lon, metadata in projects:
        project_id = stable_id("synthetic_permit_project", key)
        db.conn.execute(
            """INSERT INTO permit_project
               (project_id, source_system_id, source_record_key, facility_name,
                company_name, capacity_kw, permit_date, operation_status,
                province, city_county, eup_myeon, ri, latitude, longitude,
                road_address, jibun_address, location_status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '전라남도', ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 facility_name=excluded.facility_name, company_name=excluded.company_name,
                 capacity_kw=excluded.capacity_kw, permit_date=excluded.permit_date,
                 operation_status=excluded.operation_status, province=excluded.province,
                 city_county=excluded.city_county, eup_myeon=excluded.eup_myeon,
                 ri=excluded.ri, latitude=excluded.latitude, longitude=excluded.longitude,
                 jibun_address=excluded.jibun_address, location_status=excluded.location_status,
                 metadata_json=excluded.metadata_json""",
            (project_id, source_id, key, facility, company, capacity, permit_date, status, "함평군" if "함평" in address else "영암군" if "영암" in address else "무안군", "손불면" if "손불" in address else "삼호읍" if "삼호" in address else "해제면", "가상리", float(lat), float(lon), address, address, json.dumps(metadata, ensure_ascii=False)),
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
    return {
        "documents": document_count,
        "siting_rules": rule_count,
        "permit_projects": permit_count,
        "review_cases": review_counts["cases"],
    }
