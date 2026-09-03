"""Deterministic, Yeongam-only fixtures for the offline product demo.

The real service can ingest public records later. Until then these fixtures
keep one coherent story across the database, map pins, RAG evidence, and chat.
Every row is explicitly marked synthetic so it cannot be mistaken for a real
permit or complaint.
"""

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


def _place(
    surface: str,
    normalized: str,
    *,
    eup_myeon: str,
    ri: str,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
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


# These records are shown on the Yeongam map. Coordinates are deliberately
# distributed across the named townships so area buttons have useful details.
# They are not real permit locations.
YEONGAM_SITES: tuple[dict[str, Any], ...] = (
    {
        "key": "synthetic-permit-001", "area": "삼호읍", "ri": "가상리", "parcel": "45-2",
        "facility": "삼호 배수 대응 태양광 A", "company": "합성에너지A", "capacity": 900,
        "date": "2022-05-20", "status": "허가", "lat": 34.800, "lon": 126.420,
        "site_area": 13000, "install_min": 11520, "install_max": 12780, "verdict": "review",
        "issues": ["safety_environment", "communication_procedure"],
        "evidence": "집중호우 때 배수와 토사 유출 우려가 제기되어 현장 점검·주민 설명회를 검토한 합성 사례",
    },
    {
        "key": "synthetic-permit-002", "area": "삼호읍", "ri": "가상리", "parcel": "51-2",
        "facility": "삼호 배수 대응 태양광 B", "company": "합성에너지B", "capacity": 650,
        "date": "2021-10-04", "status": "사업개시", "lat": 34.807, "lon": 126.414,
        "site_area": 9800, "install_min": 8320, "install_max": 9230, "verdict": "clear",
        "issues": ["safety_environment"],
        "evidence": "배수계획 보완 후 사업개시를 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-003", "area": "삼호읍", "ri": "가상리", "parcel": "66-7",
        "facility": "삼호 주민협의 태양광 C", "company": "합성마을전력", "capacity": 480,
        "date": "2023-03-15", "status": "검토중", "lat": 34.793, "lon": 126.429,
        "site_area": 7200, "install_min": 6144, "install_max": 6816, "verdict": "review",
        "issues": ["communication_procedure", "landscape_damage"],
        "evidence": "주민 설명회와 차폐 계획을 함께 검토하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-004", "area": "시종면", "ri": "가상리", "parcel": "122-1",
        "facility": "시종 계통연계 태양광 A", "company": "합성전력A", "capacity": 450,
        "date": "2020-06-11", "status": "허가", "lat": 34.860, "lon": 126.454,
        "site_area": 7580, "install_min": 5760, "install_max": 6390, "verdict": "review",
        "issues": ["grid_connection", "communication_procedure"],
        "evidence": "허가 이후 계통연계 여유와 미이행 여부를 별도로 확인하는 합성 사례",
    },
    {
        "key": "synthetic-permit-005", "area": "시종면", "ri": "가상리", "parcel": "108-2",
        "facility": "시종 누적개발 태양광 B", "company": "합성전력B", "capacity": 200,
        "date": "2024-01-19", "status": "허가", "lat": 34.868, "lon": 126.461,
        "site_area": 3720, "install_min": 2560, "install_max": 2840, "verdict": "review",
        "issues": ["grid_connection", "siting_permit_regulatory"],
        "evidence": "읍면 단위 누적 허가와 계통 병목을 함께 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-006", "area": "도포면", "ri": "가상리", "parcel": "155-5",
        "facility": "도포 농지검토 태양광 A", "company": "합성농촌전력", "capacity": 300,
        "date": "2022-11-02", "status": "허가", "lat": 34.835, "lon": 126.474,
        "site_area": 5410, "install_min": 3840, "install_max": 4260, "verdict": "review",
        "issues": ["agricultural_land_damage", "siting_permit_regulatory"],
        "evidence": "농지 전용 조건과 개발행위 검토를 함께 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-007", "area": "도포면", "ri": "가상리", "parcel": "산 97-1",
        "facility": "도포 경관검토 태양광 B", "company": "합성농촌전력B", "capacity": 480,
        "date": "2023-09-28", "status": "검토중", "lat": 34.842, "lon": 126.481,
        "site_area": 8180, "install_min": 6144, "install_max": 6816, "verdict": "review",
        "issues": ["landscape_damage", "safety_environment"],
        "evidence": "주요 조망점과 배수 경로를 현장에서 확인해야 하는 합성 사례",
    },
    {
        "key": "synthetic-permit-008", "area": "미암면", "ri": "가상리", "parcel": "산 383-1",
        "facility": "미암 소규모 태양광 A", "company": "합성마을전력C", "capacity": 90,
        "date": "2021-04-08", "status": "사업개시", "lat": 34.755, "lon": 126.399,
        "site_area": 1430, "install_min": 1152, "install_max": 1278, "verdict": "clear",
        "issues": ["communication_procedure"],
        "evidence": "소규모 주민참여와 사업개시 이력을 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-009", "area": "미암면", "ri": "가상리", "parcel": "354-3",
        "facility": "미암 주민참여 태양광 B", "company": "합성마을전력D", "capacity": 300,
        "date": "2024-04-22", "status": "허가", "lat": 34.765, "lon": 126.407,
        "site_area": 5110, "install_min": 3840, "install_max": 4260, "verdict": "clear",
        "issues": ["communication_procedure", "external_benefit_distribution"],
        "evidence": "주민참여·이익공유 협의 항목을 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-010", "area": "학산면", "ri": "가상리", "parcel": "산 429-5",
        "facility": "학산 반사광 점검 태양광 A", "company": "합성시야전력", "capacity": 180,
        "date": "2022-08-17", "status": "허가", "lat": 34.776, "lon": 126.487,
        "site_area": 2960, "install_min": 2304, "install_max": 2556, "verdict": "review",
        "issues": ["glare_reflection", "landscape_damage"],
        "evidence": "인근 도로와 주거지의 시간대별 반사 시야를 확인하는 합성 사례",
    },
    {
        "key": "synthetic-permit-011", "area": "학산면", "ri": "가상리", "parcel": "722-3",
        "facility": "학산 소음·경관 태양광 B", "company": "합성시야전력B", "capacity": 750,
        "date": "2023-12-06", "status": "검토중", "lat": 34.785, "lon": 126.496,
        "site_area": 12450, "install_min": 9600, "install_max": 10650, "verdict": "review",
        "issues": ["noise_living_discomfort", "landscape_damage"],
        "evidence": "운영 설비 소음과 주요 조망점 차폐를 함께 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-012", "area": "서호면", "ri": "가상리", "parcel": "186-2",
        "facility": "서호 배수·도로 태양광", "company": "합성서호전력", "capacity": 60,
        "date": "2020-03-12", "status": "사업개시", "lat": 34.860, "lon": 126.377,
        "site_area": 1020, "install_min": 768, "install_max": 852, "verdict": "clear",
        "issues": ["safety_environment"],
        "evidence": "소규모 배수로 정비와 사업개시를 함께 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-013", "area": "신북면", "ri": "가상리", "parcel": "산 332-5",
        "facility": "신북 농지·계통 태양광", "company": "합성신북전력", "capacity": 450,
        "date": "2024-06-13", "status": "허가", "lat": 34.883, "lon": 126.442,
        "site_area": 7680, "install_min": 5760, "install_max": 6390, "verdict": "review",
        "issues": ["agricultural_land_damage", "grid_connection"],
        "evidence": "농지 조건과 계통연계 가능 여부를 분리해 확인하는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-014", "area": "덕진면", "ri": "가상리", "parcel": "214-4",
        "facility": "덕진 주민협의 태양광", "company": "합성덕진전력", "capacity": 240,
        "date": "2022-02-25", "status": "허가", "lat": 34.842, "lon": 126.413,
        "site_area": 4080, "install_min": 3072, "install_max": 3408, "verdict": "review",
        "issues": ["communication_procedure", "siting_permit_regulatory"],
        "evidence": "주민 설명회와 개발행위 절차 확인을 함께 보여주는 합성 참고 사업",
    },
    {
        "key": "synthetic-permit-015", "area": "군서면", "ri": "가상리", "parcel": "88-6",
        "facility": "군서 경관·반사광 태양광", "company": "합성군서전력", "capacity": 520,
        "date": "2023-06-30", "status": "검토중", "lat": 34.793, "lon": 126.453,
        "site_area": 8840, "install_min": 6656, "install_max": 7384, "verdict": "review",
        "issues": ["glare_reflection", "landscape_damage"],
        "evidence": "마을회관과 도로에서 반사광·경관 영향을 살피는 합성 사례",
    },
    {
        "key": "synthetic-permit-016", "area": "영암읍", "ri": "가상리", "parcel": "31-8",
        "facility": "영암읍 생활권 태양광", "company": "합성영암전력", "capacity": 120,
        "date": "2024-02-16", "status": "검토중", "lat": 34.800, "lon": 126.492,
        "site_area": 2040, "install_min": 1536, "install_max": 1704, "verdict": "review",
        "issues": ["communication_procedure", "glare_reflection"],
        "evidence": "생활권 주민 설명과 반사 시야 확인을 함께 검토하는 합성 사례",
    },
)


def _site_place(site: dict[str, Any]) -> dict[str, Any]:
    address = f"전라남도 영암군 {site['area']} {site['ri']} {site['parcel']}"
    return _place(
        f"{site['area']} {site['ri']} {site['parcel']}",
        address,
        eup_myeon=site["area"],
        ri=site["ri"],
        latitude=site["lat"],
        longitude=site["lon"],
    )


def _segment(site: dict[str, Any], text: str, issues: list[tuple[str, str, float]], speaker: str, role: str) -> dict[str, Any]:
    return {
        "text_original": text,
        "segment_type": "speech",
        "speaker_name": speaker,
        "speaker_role": role,
        "issues": [
            {"issue_code": code, "polarity": polarity, "confidence": confidence,
             "evidence_span": text[:80], "metadata": {"data_origin": "synthetic"}}
            for code, polarity, confidence in issues
        ],
        "places": [_site_place(site)],
        "relevant": True,
    }


def _bundle(key: str, title: str, date: str, site: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": {
            "system_code": "demo_fixture",
            "source_record_key": key,
            "title": f"[합성] {title}",
            "source_url": f"synthetic://minutes/{key}",
            "document_type": "meeting_minutes",
            "mime_type": "text/plain",
            "access_policy": "demo",
            "metadata": _source_metadata(area=site["area"], scenario="yeongam_showcase"),
        },
        "meeting": {
            "assembly_name": "[합성] 전라남도 영암군의회",
            "province": "전라남도",
            "city_county": "영암군",
            "meeting_title": f"[합성] {title}",
            "meeting_type": "민원·현안 보고",
            "meeting_date": date,
        },
        "page": {"text_original": f"[합성] {title}"},
        "segments": segments,
    }


def synthetic_bundles() -> list[dict[str, Any]]:
    """Return several connected Yeongam stories used by map, RAG, and chat."""

    by_key = {site["key"]: site for site in YEONGAM_SITES}
    samho = by_key["synthetic-permit-001"]
    sijong = by_key["synthetic-permit-004"]
    dopo = by_key["synthetic-permit-006"]
    haksan = by_key["synthetic-permit-010"]
    miam = by_key["synthetic-permit-009"]
    sinbuk = by_key["synthetic-permit-013"]

    return [
        _bundle(
            "synthetic-yeongam-samho-drainage",
            "삼호읍 태양광 배수·주민협의 과정",
            "2024-08-21",
            samho,
            [
                _segment(samho, "삼호읍 가상리 45-2 발전사업에 대해 주민들이 집중호우 때 배수와 토사 유출을 우려하는 민원을 제기했다.", [("safety_environment", "opposition", .96), ("communication_procedure", "opposition", .82)], "최주민", "주민"),
                _segment(samho, "행정기관은 현장 점검과 배수계획 검토를 요청했고, 사업자는 주민 설명회를 진행하기로 했다.", [("safety_environment", "opposition", .91), ("communication_procedure", "support", .88)], "김국장", "국장"),
                _segment(samho, "주민 설명회 후 차폐와 배수 보완안을 검토했지만, 조치 완료 여부는 후속 확인이 필요한 상태로 남았다.", [("communication_procedure", "mixed", .84)], "이담당", "과장"),
            ],
        ),
        _bundle(
            "synthetic-yeongam-sijong-grid",
            "시종면 허가 누적·계통연계 점검",
            "2025-02-14",
            sijong,
            [
                _segment(sijong, "시종면 일대 태양광 허가가 누적되면서 주민들은 개별 사업이 아니라 마을 전체의 누적 영향을 함께 검토해 달라고 요청했다.", [("siting_permit_regulatory", "opposition", .94), ("communication_procedure", "opposition", .87)], "박의원", "의원"),
                _segment(sijong, "허가 건수와 실제 사업개시 건수는 다를 수 있으므로, 계통연계 가능 여부와 미이행 현황을 별도로 확인하기로 했다.", [("grid_connection", "neutral", .93)], "정과장", "과장"),
                _segment(sijong, "다음 회의에서는 읍면 단위 공급 가능 정보와 주민 설명회 일정을 함께 보고할 예정이다.", [("grid_connection", "support", .86), ("communication_procedure", "support", .81)], "김국장", "국장"),
            ],
        ),
        _bundle(
            "synthetic-yeongam-dopo-farmland",
            "도포면 농지·경관 영향 검토",
            "2023-06-09",
            dopo,
            [
                _segment(dopo, "도포면 가상리 태양광 예정지와 관련해 농지 전용 조건과 배수로 연결 상태를 먼저 확인해야 한다는 의견이 나왔다.", [("agricultural_land_damage", "opposition", .95), ("safety_environment", "opposition", .82)], "한의원", "의원"),
                _segment(dopo, "주요 도로에서 보이는 경관과 차폐 계획은 현장 사진과 함께 검토하고, 공식 기준은 조례 조문으로 확인하기로 했다.", [("landscape_damage", "opposition", .92), ("siting_permit_regulatory", "neutral", .88)], "서담당", "팀장"),
                _segment(dopo, "자료가 갖춰진 뒤 주민 설명회에서 농지·경관·배수 검토 결과를 설명하기로 했다.", [("communication_procedure", "support", .9)], "김국장", "국장"),
            ],
        ),
        _bundle(
            "synthetic-yeongam-haksan-glare",
            "학산면 반사광·생활불편 조사",
            "2024-03-18",
            haksan,
            [
                _segment(haksan, "학산면 산 429-5 인근 도로와 주거지에서 시간대에 따라 반사광과 눈부심이 느껴질 수 있다는 현장 의견이 접수되었다.", [("glare_reflection", "opposition", .97), ("landscape_damage", "opposition", .78)], "최주민", "주민"),
                _segment(haksan, "모듈 방향과 주변 조망점을 대조한 뒤, 필요한 경우 차폐와 배치 변경을 검토하기로 했다.", [("glare_reflection", "neutral", .94), ("landscape_damage", "neutral", .83)], "윤담당", "주무관"),
                _segment(haksan, "운영 설비의 소음 여부는 측정 시점과 설비 상태를 함께 기록해야 하며, 현재는 판단 보류로 남겼다.", [("noise_living_discomfort", "mixed", .89)], "이담당", "과장"),
            ],
        ),
        _bundle(
            "synthetic-yeongam-miam-benefit",
            "미암면 주민참여·이익공유 협의",
            "2022-10-27",
            miam,
            [
                _segment(miam, "미암면 주민들은 소규모 사업이라도 사업 수익과 관리 책임을 사전에 설명하고 협의해야 한다고 요청했다.", [("communication_procedure", "opposition", .91), ("external_benefit_distribution", "opposition", .93)], "박주민", "주민"),
                _segment(miam, "사업자는 주민참여 방식과 협의 결과를 문서로 남기고 사업개시 이후에도 정기적으로 공유하기로 했다.", [("external_benefit_distribution", "support", .9)], "김국장", "국장"),
            ],
        ),
        _bundle(
            "synthetic-yeongam-sinbuk-grid",
            "신북면 농지·계통 확인 회의",
            "2025-05-30",
            sinbuk,
            [
                _segment(sinbuk, "신북면 산 332-5 주변은 농지 이용 현황과 개발행위 조건을 확인한 뒤 계통연계 신청 순서를 정리해야 한다.", [("agricultural_land_damage", "neutral", .9), ("siting_permit_regulatory", "neutral", .88)], "서담당", "팀장"),
                _segment(sinbuk, "공급 가능 정보는 읍면 단위 참고자료이므로 최종 접속 가능 여부는 한전 심의로 확인해야 한다.", [("grid_connection", "neutral", .98)], "정과장", "과장"),
            ],
        ),
    ]


def _region_code(db: LuceraDB) -> str | None:
    row = db.conn.execute("SELECT region_code FROM administrative_region WHERE region_name='영암군' LIMIT 1").fetchone()
    return str(row[0]) if row else None


def seed_siting_rules(db: LuceraDB) -> int:
    # Demo inputs based on the Yeongam ordinance shape. They remain synthetic
    # until an official ordinance row is ingested and cited.
    rules = [
        ("synthetic-yeongam-residence-300", "residence", "gte", 300, "m", "주거지 이격거리(합성)", "주거지까지 최소 300m 이상인지 확인하는 시연용 규칙", "제20조의3", "high"),
        ("synthetic-yeongam-road-300", "road", "gte", 300, "m", "주요도로 이격거리(합성)", "주요도로까지 최소 300m 이상인지 확인하는 시연용 규칙", "제20조의3", "high"),
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
            (rule_id, _region_code(db), reference_object, operator, threshold, unit, name, description, article, severity,
             json.dumps({"data_origin": "synthetic", "region_name": "영암군"}, ensure_ascii=False)),
        )
        inserted += 1
    return inserted


def seed_permit_projects(db: LuceraDB) -> int:
    source_id = db._source_id("demo_fixture")
    for site in YEONGAM_SITES:
        project_id = stable_id("synthetic_permit_project", site["key"])
        metadata = {
            "data_origin": "synthetic",
            "warning": "실제 인허가 신청이 아닌 지도·RAG 시연용 합성 사업",
            "eup_myeon": site["area"],
            "ri": site["ri"],
            "issues": site["issues"],
            "evidence": site["evidence"],
            "site_area_sqm": site["site_area"],
            "installation_area_min_sqm": site["install_min"],
            "installation_area_max_sqm": site["install_max"],
            "verdict": site["verdict"],
            "geo_precision": "jibun_address",
        }
        address = f"전라남도 영암군 {site['area']} {site['ri']} {site['parcel']}"
        db.conn.execute(
            """INSERT INTO permit_project
               (project_id, source_system_id, source_record_key, facility_name,
                company_name, capacity_kw, permit_date, operation_status,
                province, city_county, eup_myeon, ri, latitude, longitude,
                road_address, jibun_address, location_status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '전라남도', '영암군', ?, ?, ?, ?, ?, ?, 'candidate', ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 facility_name=excluded.facility_name, company_name=excluded.company_name,
                 capacity_kw=excluded.capacity_kw, permit_date=excluded.permit_date,
                 operation_status=excluded.operation_status, eup_myeon=excluded.eup_myeon,
                 ri=excluded.ri, latitude=excluded.latitude, longitude=excluded.longitude,
                 jibun_address=excluded.jibun_address, location_status=excluded.location_status,
                 metadata_json=excluded.metadata_json""",
            (project_id, source_id, site["key"], site["facility"], site["company"], site["capacity"],
             site["date"], site["status"], site["area"], site["ri"], site["lat"], site["lon"],
             address, address, json.dumps(metadata, ensure_ascii=False)),
        )
    return len(YEONGAM_SITES)


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
