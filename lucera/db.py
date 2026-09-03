from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .keywords import classify_segment
from .regions import parent_region_catalog, region_catalog


TAXONOMY = [
    ("landscape_damage", "경관 훼손", "경관·조망·농촌 경관 변화"),
    ("noise_living_discomfort", "소음·생활 불편", "소음, 생활환경, 건강·생활 불편"),
    ("agricultural_land_damage", "농지·토지 피해", "농지 훼손, 토지 이용, 수익·재산 피해"),
    ("siting_permit_regulatory", "입지·인허가·규제", "개발행위, 이격거리, 용도지역, 농지·국토·전기사업 인허가"),
    ("communication_procedure", "소통·절차", "주민 설명, 고지, 협의, 절차적 정당성"),
    ("glare_reflection", "빛반사·눈부심", "빛반사, 눈부심, 패널 반사"),
    ("external_benefit_distribution", "편익 배분", "수익 배분, 보상, 주민 참여·공유"),
    ("safety_environment", "환경·안전", "산사태, 침수, 산림, 환경·재난·안전"),
    ("grid_connection", "계통·접속", "계통연계, 변전소, 출력제어, 접속 절차"),
]

# Database-backed contract for the project intake form.  The same names are
# used by project_application/project_site/... and project_fact so a value can
# be read quickly from its structured table and still be audited through its
# source fact.  This is deliberately data, not a second Python validation
# implementation: the UI and future importers can inspect it from SQLite or
# PostgreSQL.
PROJECT_FIELD_DEFINITIONS = [
    ("project_name", "business", "사업명", "text", None, "application", 1, "user_or_attachment", {}, 1, "발전사업 또는 설비의 공식 명칭"),
    ("business_type", "business", "사업 유형", "text", None, "application", 0, "user_or_attachment", {}, 2, "태양광·수상·영농형·학교·지붕형 등"),
    ("permit_type", "business", "허가 유형", "text", None, "power_generation_permit", 0, "user_or_attachment", {}, 3, "발전사업허가 등 신청·허가 유형"),
    ("applicant_name", "applicant", "신청자명", "text", None, "application", 0, "user_or_attachment", {}, 4, "신청자 개인 또는 대표자명"),
    ("applicant_type", "applicant", "신청자 유형", "text", None, "application", 0, "user_or_attachment", {}, 5, "개인·법인·조합·공공기관 등"),
    ("corporate_name", "applicant", "법인명", "text", None, "application", 0, "user_or_attachment", {}, 6, "사업자 또는 법인의 등록 명칭"),
    ("contractor_name", "applicant", "시공사명", "text", None, "construction_completion", 0, "user_or_attachment", {}, 7, "시공·도급 업체명"),
    ("site_address", "site", "사업지 주소", "text", None, "application", 1, "user_or_attachment", {}, 8, "사업지 원문 주소. 좌표가 없어도 입력 가능"),
    ("lot_number", "site", "필지번호", "text", None, "application", 0, "user_or_attachment", {}, 9, "지번 또는 복수 필지 표기"),
    ("land_category", "site", "지목", "text", None, "development_environment_review", 0, "user_or_attachment", {}, 10, "전·답·임야·염해농지 등"),
    ("building_address", "site", "건축물 주소", "text", None, "application", 0, "user_or_attachment", {}, 11, "옥상·지붕형 설치 시 건축물 주소"),
    ("building_use", "site", "건축물 용도", "text", None, "development_environment_review", 0, "user_or_attachment", {}, 12, "공장·창고·학교 등 건축물 용도"),
    ("installed_capacity_kw", "equipment", "설치용량", "numeric", "kW", "application", 0, "user_or_attachment", {"min": 0}, 13, "총 설비용량"),
    ("module_count", "equipment", "모듈 수", "numeric", "count", "application", 0, "user_or_attachment", {"min": 0, "integer": True}, 14, "태양광 모듈 개수"),
    ("module_capacity_w", "equipment", "모듈 정격용량", "numeric", "W", "application", 0, "user_or_attachment", {"min": 0}, 15, "모듈 1장의 정격용량"),
    ("inverter_count", "equipment", "인버터 수", "numeric", "count", "grid_connection_operation", 0, "user_or_attachment", {"min": 0, "integer": True}, 16, "인버터 개수"),
    ("inverter_capacity_kva", "equipment", "인버터 용량", "numeric", "kVA", "grid_connection_operation", 0, "user_or_attachment", {"min": 0}, 17, "인버터 정격용량"),
    ("installation_height_m", "equipment", "설치 높이", "numeric", "m", "development_environment_review", 0, "user_or_attachment", {"min": 0}, 18, "지면 또는 건축물 기준 설치 높이"),
    ("installation_area_sqm", "equipment", "설치 면적", "numeric", "㎡", "development_environment_review", 0, "user_or_attachment", {"min": 0}, 19, "설비 또는 사업 대상 면적"),
    ("total_project_cost_krw", "finance", "총사업비", "numeric", "KRW", "application", 0, "user_or_attachment", {"min": 0}, 20, "총 사업비"),
    ("construction_cost_per_kw", "finance", "kW당 공사비", "numeric", "KRW/kW", "construction_completion", 0, "user_or_attachment", {"min": 0}, 21, "설치용량 기준 공사비"),
    ("annual_generation_mwh", "finance", "연간 발전량", "numeric", "MWh/년", "grid_connection_operation", 0, "user_or_attachment", {"min": 0}, 22, "예상 연간 발전량"),
    ("annual_transmission_mwh", "finance", "연간 송전량", "numeric", "MWh/년", "grid_connection_operation", 0, "user_or_attachment", {"min": 0}, 23, "예상 연간 송전량"),
    ("lease_fee_krw", "finance", "임대료", "numeric", "KRW/년", "resident_consultation_complaint", 0, "user_or_attachment", {"min": 0}, 24, "토지·건축물 임대료"),
    ("resident_revenue_share", "finance", "주민 수익 배분율", "numeric", "%", "resident_consultation_complaint", 0, "user_or_attachment", {"min": 0, "max": 100}, 25, "주민에게 배분하는 수익 비율"),
    ("permit_application_date", "schedule", "허가 신청일", "date", None, "application", 0, "user_or_attachment", {"format": "YYYY-MM-DD"}, 26, "발전사업 또는 관련 허가 신청일"),
    ("permit_date", "schedule", "발전사업허가일", "date", None, "power_generation_permit", 0, "user_or_attachment", {"format": "YYYY-MM-DD"}, 27, "발전사업허가 처분일"),
    ("construction_start_date", "schedule", "공사 시작일", "date", None, "construction_completion", 0, "user_or_attachment", {"format": "YYYY-MM-DD"}, 28, "공사 착수일"),
    ("expected_completion_date", "schedule", "준공 예정일", "date", None, "construction_completion", 0, "user_or_attachment", {"format": "YYYY-MM-DD"}, 29, "준공 예정일"),
    ("business_start_date", "schedule", "사업개시일", "date", None, "grid_connection_operation", 0, "user_or_attachment", {"format": "YYYY-MM-DD"}, 30, "사업개시 신고·확인일"),
    ("operation_period_years", "schedule", "운영 기간", "numeric", "년", "grid_connection_operation", 0, "user_or_attachment", {"minExclusive": 0}, 31, "예정 운영 기간"),
    ("grid_connection_point", "grid", "계통연계 지점", "text", None, "grid_connection_operation", 0, "user_or_attachment", {}, 32, "변전소·배전선로 등 연계 지점"),
    ("connection_voltage_v", "grid", "연계 전압", "numeric", "V", "grid_connection_operation", 0, "user_or_attachment", {"min": 0}, 33, "계통연계 전압"),
    ("transformer_info", "grid", "변압기 정보", "text", None, "grid_connection_operation", 0, "user_or_attachment", {}, 34, "변압기 용량·형식 등"),
    ("power_purchase_method", "grid", "전력 판매 방식", "text", None, "grid_connection_operation", 0, "user_or_attachment", {}, 35, "전력 구매·판매 계약 방식"),
    ("development_permit_required", "permit", "개발행위허가 필요 여부", "boolean", None, "development_environment_review", 0, "user_or_attachment", {}, 36, "개발행위허가 대상 여부"),
    ("urban_management_plan_required", "permit", "도시관리계획 필요 여부", "boolean", None, "development_environment_review", 0, "user_or_attachment", {}, 37, "도시·군관리계획 반영 필요 여부"),
    ("construction_plan_report", "permit", "공사계획 신고 여부", "boolean", None, "power_generation_permit", 0, "user_or_attachment", {}, 38, "공사계획 신고 또는 관련 절차 여부"),
    ("environmental_assessment_required", "permit", "환경영향 검토 필요 여부", "boolean", None, "development_environment_review", 0, "user_or_attachment", {}, 39, "환경영향평가·소규모환경영향평가 등 검토 대상 여부"),
    ("structural_safety_review", "permit", "구조안전 검토 여부", "boolean", None, "development_environment_review", 0, "user_or_attachment", {}, 40, "구조안전 검토 대상 여부"),
    ("resident_consent_required", "resident", "주민동의 필요 여부", "boolean", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 41, "주민동의 또는 주민협의 필요 여부"),
    ("construction_consent", "resident", "공사 동의 여부", "boolean", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 42, "공사 착수에 대한 동의 확인 여부"),
    ("complaint_occurred", "resident", "민원 발생 여부", "boolean", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 43, "현재 입력 사업에서 확인된 민원 여부"),
    ("complaint_stop_commitment", "resident", "민원 중단 약속 여부", "boolean", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 44, "민원 중단·해소 약속 여부"),
    ("removal_commitment", "resident", "철거 약속 여부", "boolean", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 45, "시설 철거 약속 여부"),
    ("complaint_type", "resident", "민원 유형", "text", None, "resident_consultation_complaint", 0, "user_or_attachment", {}, 46, "주민 반대·빛반사·소음·경관 등 원문 유형"),
]


def stable_id(*parts: object) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class LuceraDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The HTTP server uses one repository instance. Allow its worker
        # threads to enter the connection, while the handler-level RLock
        # serializes each read/write transaction for SQLite correctness.
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        self.conn.close()

    def initialize(self, schema_path: str | Path) -> None:
        self.conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
        self._apply_schema_migrations()
        self._create_compatibility_views()
        self.seed_reference_data()
        self.conn.commit()

    def _apply_schema_migrations(self) -> None:
        migrations = {
            "meeting_segment": {"agenda_no": "TEXT"},
            "meeting": {"administrative_region_code": "TEXT"},
            "conflict_case": {
                "canonical_title": "TEXT",
                "municipality": "TEXT",
                "village": "TEXT",
                "address": "TEXT",
                "project_name": "TEXT",
                "facility_type": "TEXT",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            },
            "case_review": {
                "reviewer_id": "TEXT",
                "reviewed_at": "TEXT",
                "review_note": "TEXT",
                "decision_source": "TEXT NOT NULL DEFAULT 'deterministic_gate'",
            },
            "project_intake": {
                "input_schema_version": "TEXT NOT NULL DEFAULT 'project-intake-v2'",
                "intake_channel": "TEXT NOT NULL DEFAULT 'web_form'",
                "created_by": "TEXT",
                "updated_by": "TEXT",
                "last_submitted_at": "TEXT",
                "completed_at": "TEXT",
            },
            "project_intake_submission": {
                "submission_channel": "TEXT NOT NULL DEFAULT 'web_form'",
                "submitted_by": "TEXT",
                "payload_sha256": "TEXT",
                "validated_at": "TEXT",
            },
            "project_application": {
                "source_submission_id": "TEXT REFERENCES project_intake_submission(submission_id) ON DELETE SET NULL",
            },
            "project_site": {
                "site_address_type": "TEXT NOT NULL DEFAULT 'unknown'",
                "site_resolution_status": "TEXT NOT NULL DEFAULT 'unresolved'",
                "site_resolution_method": "TEXT",
                "site_resolution_confidence": "REAL",
                "building_address_type": "TEXT NOT NULL DEFAULT 'unknown'",
                "building_resolution_status": "TEXT NOT NULL DEFAULT 'unresolved'",
                "building_resolution_method": "TEXT",
                "building_resolution_confidence": "REAL",
            },
            "project_schedule": {"schedule_status": "TEXT NOT NULL DEFAULT 'unknown'"},
            "project_grid": {"connection_status": "TEXT NOT NULL DEFAULT 'unknown'"},
            "project_permit_checklist": {
                "checklist_status": "TEXT NOT NULL DEFAULT 'not_started'",
                "checked_at": "TEXT",
            },
            "project_resident_risk": {
                "risk_status": "TEXT NOT NULL DEFAULT 'unknown'",
                "complaint_source": "TEXT",
            },
            "project_attachment": {
                "source_url": "TEXT",
                "document_date": "TEXT",
                "page_count": "INTEGER",
                "is_required": "INTEGER NOT NULL DEFAULT 0",
                "extraction_started_at": "TEXT",
                "extracted_at": "TEXT",
                "extractor_name": "TEXT",
                "extractor_version": "TEXT",
                "extraction_error": "TEXT",
                "content_text_uri": "TEXT",
                "text_sha256": "TEXT",
                "ocr_used": "INTEGER",
                "uploaded_by": "TEXT",
            },
            "project_fact": {
                "source_artifact_id": "TEXT REFERENCES document_artifact(artifact_id) ON DELETE SET NULL",
                "source_paragraph_id": "TEXT REFERENCES meeting_segment(segment_id) ON DELETE SET NULL",
                "source_char_start": "INTEGER",
                "source_char_end": "INTEGER",
                "extraction_model": "TEXT",
                "extraction_version": "TEXT",
                "fact_status": "TEXT NOT NULL DEFAULT 'active'",
                "is_current": "INTEGER NOT NULL DEFAULT 1",
                "reviewed_by": "TEXT",
                "reviewed_at": "TEXT",
                "review_note": "TEXT",
            },
            "project_stage": {
                "source_field": "TEXT",
                "source_fact_id": "TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL",
                "confidence": "REAL",
                "last_evaluated_at": "TEXT",
            },
            "project_stage_event": {
                "source_fact_id": "TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL",
                "review_status": "TEXT NOT NULL DEFAULT 'pending'",
                "reviewed_by": "TEXT",
                "reviewed_at": "TEXT",
                "review_note": "TEXT",
            },
            "project_location_link": {
                "raw_query": "TEXT",
                "candidate_rank": "INTEGER",
                "resolution_method": "TEXT",
                "geo_provider": "TEXT",
                "resolved_at": "TEXT",
            },
            "project_case_link": {
                "match_method": "TEXT NOT NULL DEFAULT 'deterministic_v1'",
                "location_match_type": "TEXT",
                "review_reason": "TEXT",
                "reviewed_by": "TEXT",
                "reviewed_at": "TEXT",
                "review_note": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # Attach old application revisions to their immutable submission row.
        # This is safe for databases created before source_submission_id was
        # introduced and makes the revision -> structured data trace complete.
        self.conn.execute(
            """UPDATE project_application
                  SET source_submission_id=(
                      SELECT submission_id
                        FROM project_intake_submission s
                       WHERE s.project_id=project_application.project_id
                         AND s.revision_no=project_application.revision_no
                  )
                WHERE source_submission_id IS NULL"""
        )
        self.conn.executescript(
            """CREATE INDEX IF NOT EXISTS idx_project_submission_project
                   ON project_intake_submission(project_id, revision_no);
               CREATE INDEX IF NOT EXISTS idx_project_fact_field_current
                   ON project_fact(application_id, field_name, is_current, fact_status);
               CREATE INDEX IF NOT EXISTS idx_project_attachment_extraction
                   ON project_attachment(application_id, extraction_status, is_required);
               CREATE INDEX IF NOT EXISTS idx_project_location_resolution
                   ON project_location_link(application_id, relation_type, review_status, candidate_rank);
               CREATE INDEX IF NOT EXISTS idx_project_case_link_stage
                   ON project_case_link(application_id, stage_code, review_status, match_score);
               CREATE INDEX IF NOT EXISTS idx_project_stage_event_source
                   ON project_stage_event(application_id, source_fact_id, event_date);
               CREATE INDEX IF NOT EXISTS idx_project_field_definition_section
                   ON project_field_definition(section_code, display_order, active);
               CREATE INDEX IF NOT EXISTS idx_case_review_reviewer
                   ON case_review(decision, reviewed_at);"""
        )

    def _create_compatibility_views(self) -> None:
        """Expose the hierarchy names without duplicating canonical tables."""
        self.conn.executescript(
            """CREATE VIEW IF NOT EXISTS documents AS
               SELECT document_id, source_system_id, source_record_key, title,
                      document_type, source_url, original_file_url, published_at,
                      access_policy, processing_status, raw_payload_json,
                      metadata_json, retrieved_at, created_at, updated_at
                 FROM source_document;

               CREATE VIEW IF NOT EXISTS paragraphs AS
               SELECT segment_id AS paragraph_id, document_id, page_from, page_to,
                      meeting_id, speaker_id, section_title, agenda_no,
                      ordinal AS paragraph_order, segment_type,
                      text_original AS text, text_redacted, relevance_status,
                      review_status, metadata_json
                 FROM meeting_segment;

               CREATE VIEW IF NOT EXISTS cases AS
               SELECT case_id, case_key,
                      COALESCE(canonical_title, case_name) AS canonical_title,
                      municipality, village, address, project_name, facility_type,
                      summary, case_status, started_on, ended_on,
                      representative_place_id, confidence, review_status,
                      metadata_json
                 FROM conflict_case;

               CREATE VIEW IF NOT EXISTS document_cases AS
               SELECT e.document_id,
                      ce.case_id,
                      COALESCE(c.canonical_title, c.case_name) AS canonical_title,
                      c.municipality, c.village, c.address, c.project_name,
                      c.facility_type,
                      COUNT(DISTINCT ce.paragraph_id) AS evidence_paragraph_count,
                      COUNT(DISTINCT cp.segment_id) AS paragraph_count,
                      COUNT(DISTINCT ce.episode_id) AS episode_count,
                      MIN(ms.ordinal) AS first_paragraph_order,
                      MAX(ms.ordinal) AS last_paragraph_order
                 FROM episodes e
                 JOIN case_evidence ce ON ce.episode_id = e.episode_id
                 JOIN conflict_case c ON c.case_id = ce.case_id
                 LEFT JOIN case_segment cs ON cs.case_id = ce.case_id
                 LEFT JOIN meeting_segment cp
                        ON cp.segment_id = cs.segment_id
                       AND cp.document_id = e.document_id
                 LEFT JOIN meeting_segment ms ON ms.segment_id = ce.paragraph_id
                GROUP BY e.document_id, ce.case_id, c.canonical_title, c.case_name,
                         c.municipality, c.village, c.address, c.project_name,
                         c.facility_type;

               CREATE VIEW IF NOT EXISTS case_paragraphs AS
               SELECT cs.case_id,
                      s.document_id,
                      cs.segment_id AS paragraph_id,
                      s.ordinal AS paragraph_order,
                      s.page_from, s.page_to, s.segment_type, s.speaker_id,
                      s.text_original, s.text_redacted,
                      cs.relation_type,
                      cs.confidence AS paragraph_link_confidence,
                      cs.review_status
                 FROM case_segment cs
                 JOIN meeting_segment s ON s.segment_id = cs.segment_id;"""
        )

    def seed_reference_data(self) -> None:
        systems = [
            (
                stable_id("source", "clik_minutes"),
                "clik_minutes",
                "국회도서관 지방의정포털 지방의회 회의록",
                "국회도서관",
                "https://clik.nanet.go.kr/openapi/minutes.do",
                "https://clik.nanet.go.kr/potal/guide/openApi.do",
            ),
            (
                stable_id("source", "juso"),
                "juso",
                "도로명주소 안내시스템",
                "행정안전부",
                "https://business.juso.go.kr/addrlink/addrLinkApi.do",
                "https://business.juso.go.kr/addrlink/devAddrLinkRequestGuide.do",
            ),
            (
                stable_id("source", "demo_fixture"),
                "demo_fixture",
                "Lucera 데모 고정 사례",
                "Lucera",
                None,
                None,
            ),
            (
                stable_id("source", "browser_minutes"),
                "browser_minutes",
                "브라우저로 확보한 지방의회 회의록 원문",
                "국회도서관 지방의정포털·각 의회 공식 사이트",
                "https://clik.nanet.go.kr",
                None,
            ),
        ]
        self.conn.executemany(
            """INSERT INTO source_system
            (source_system_id, code, name, provider, base_url, terms_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET name=excluded.name, provider=excluded.provider,
              base_url=excluded.base_url, terms_url=excluded.terms_url""",
            systems,
        )
        all_regions = [*region_catalog(), *parent_region_catalog()]
        self.conn.executemany(
            """INSERT INTO administrative_region
               (region_code, region_name, province, region_group, region_type,
                parent_region_code, assembly_id, available, aliases_json,
                source_kind, source_reference, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'catalog', ?, ?)
               ON CONFLICT(region_code) DO UPDATE SET
                 region_name=excluded.region_name, province=excluded.province,
                 region_group=excluded.region_group, region_type=excluded.region_type,
                 parent_region_code=excluded.parent_region_code,
                 assembly_id=excluded.assembly_id, available=excluded.available,
                 aliases_json=excluded.aliases_json,
                 source_reference=excluded.source_reference,
                 metadata_json=excluded.metadata_json,
                 updated_at=CURRENT_TIMESTAMP""",
            [
                (
                    region["region_code"],
                    region["name"],
                    region["province"],
                    region["region_group"],
                    region["kind"],
                    region.get("parent_region_code"),
                    region.get("assembly_id"),
                    1 if region.get("assembly_id") else 0,
                    json.dumps(region.get("aliases", []), ensure_ascii=False),
                    "parent scope catalog" if region["kind"] == "parent_scope" else "CLiK assembly discovery 2026-09-03" if region.get("assembly_id") else "CLiK assembly ID unavailable at discovery",
                    json.dumps({"scope": "광주·전남", "is_collection_target": region["kind"] != "parent_scope"}, ensure_ascii=False),
                )
                for region in all_regions
            ],
        )
        self.conn.executemany(
            """INSERT INTO project_field_definition
               (field_name, section_code, display_name, field_type, unit,
                stage_code, required_flag, source_policy, validation_rules_json,
                display_order, active, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(field_name) DO UPDATE SET
                 section_code=excluded.section_code,
                 display_name=excluded.display_name,
                 field_type=excluded.field_type,
                 unit=excluded.unit,
                 stage_code=excluded.stage_code,
                 required_flag=excluded.required_flag,
                 source_policy=excluded.source_policy,
                 validation_rules_json=excluded.validation_rules_json,
                 display_order=excluded.display_order,
                 active=excluded.active,
                 description=excluded.description""",
            [
                (
                    field_name,
                    section_code,
                    display_name,
                    field_type,
                    unit,
                    stage_code,
                    required_flag,
                    source_policy,
                    json.dumps(rules, ensure_ascii=False),
                    display_order,
                    description,
                )
                for field_name, section_code, display_name, field_type, unit,
                stage_code, required_flag, source_policy, rules, display_order,
                description in PROJECT_FIELD_DEFINITIONS
            ],
        )
        for code, name, description in TAXONOMY:
            taxonomy_id = stable_id("taxonomy", "v1", code)
            self.conn.execute(
                """INSERT INTO issue_taxonomy
                (taxonomy_id, taxonomy_version, issue_code, issue_name, description)
                VALUES (?, 'v1', ?, ?, ?)
                ON CONFLICT(taxonomy_version, issue_code) DO UPDATE SET
                  issue_name=excluded.issue_name, description=excluded.description""",
                (taxonomy_id, code, name, description),
            )

    def _source_id(self, code: str) -> str:
        row = self.conn.execute(
            "SELECT source_system_id FROM source_system WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            raise ValueError(f"unknown source system: {code}")
        return str(row[0])

    def insert_document_bundle(self, bundle: dict[str, Any]) -> str:
        """Upsert one source document and replace only its derived content.

        The original payload/text remains in source_document/document_page. Derived
        segments, mentions, links and issue labels can be regenerated safely.
        """
        source = bundle["source"]
        source_system_id = self._source_id(source["system_code"])
        document_id = source.get("document_id") or stable_id(
            "document", source["system_code"], source.get("source_record_key")
        )
        metadata = json.dumps(source.get("metadata", {}), ensure_ascii=False)
        raw_payload = json.dumps(source.get("raw_payload", {}), ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO source_document
            (document_id, source_system_id, source_record_key, title, document_type,
             source_url, original_file_url, storage_uri, mime_type, sha256,
             file_size_bytes, published_at, access_policy, processing_status,
             raw_payload_json, metadata_json, retrieved_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
              title=excluded.title, document_type=excluded.document_type,
              source_url=excluded.source_url, original_file_url=excluded.original_file_url,
              storage_uri=excluded.storage_uri, mime_type=excluded.mime_type,
              sha256=excluded.sha256, file_size_bytes=excluded.file_size_bytes,
              published_at=excluded.published_at, access_policy=excluded.access_policy,
              processing_status=excluded.processing_status,
              raw_payload_json=excluded.raw_payload_json, metadata_json=excluded.metadata_json,
              retrieved_at=excluded.retrieved_at, updated_at=excluded.updated_at""",
            (
                document_id,
                source_system_id,
                source.get("source_record_key"),
                source["title"],
                source.get("document_type", "meeting_minutes"),
                source.get("source_url"),
                source.get("original_file_url"),
                source.get("storage_uri"),
                source.get("mime_type", "text/html"),
                source.get("sha256"),
                source.get("file_size_bytes"),
                source.get("published_at"),
                source.get("access_policy", "public"),
                "parsed",
                raw_payload,
                metadata,
                now_utc(),
                now_utc(),
            ),
        )

        # Preserve the complete derivation chain (download -> conversion ->
        # parser output) as first-class rows instead of hiding it only in a
        # document metadata blob.
        for artifact in bundle.get("artifacts", []):
            artifact_sha = artifact.get("sha256")
            artifact_id = artifact.get("artifact_id") or stable_id(
                "artifact", document_id, artifact.get("artifact_role"), artifact_sha, artifact.get("storage_uri")
            )
            self.conn.execute(
                """INSERT INTO document_artifact
                   (artifact_id, document_id, artifact_role, storage_uri, source_url,
                    mime_type, file_name, sha256, file_size_bytes, acquisition_method,
                    derived_from_artifact_id, parser_name, parser_version, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(artifact_id) DO UPDATE SET
                     storage_uri=excluded.storage_uri, source_url=excluded.source_url,
                     mime_type=excluded.mime_type, file_name=excluded.file_name,
                     sha256=excluded.sha256, file_size_bytes=excluded.file_size_bytes,
                     acquisition_method=excluded.acquisition_method,
                     derived_from_artifact_id=excluded.derived_from_artifact_id,
                     parser_name=excluded.parser_name, parser_version=excluded.parser_version,
                     metadata_json=excluded.metadata_json""",
                (
                    artifact_id,
                    document_id,
                    artifact.get("artifact_role", "rendered_pdf"),
                    artifact.get("storage_uri"),
                    artifact.get("source_url"),
                    artifact.get("mime_type"),
                    artifact.get("file_name"),
                    artifact_sha,
                    artifact.get("file_size_bytes"),
                    artifact.get("acquisition_method"),
                    artifact.get("derived_from_artifact_id"),
                    artifact.get("parser_name"),
                    artifact.get("parser_version"),
                    json.dumps(artifact.get("metadata", {}), ensure_ascii=False),
                ),
            )

        old_segments = [
            row[0]
            for row in self.conn.execute(
                "SELECT segment_id FROM meeting_segment WHERE document_id = ?", (document_id,)
            ).fetchall()
        ]
        # Episodes/evidence belong to the parsed document and are regenerated
        # together with its paragraph-derived content.
        self.conn.execute("DELETE FROM episodes WHERE document_id = ?", (document_id,))
        if old_segments:
            self.conn.executemany(
                "DELETE FROM meeting_segment_fts WHERE segment_id = ?",
                [(segment_id,) for segment_id in old_segments],
            )
        self.conn.execute("DELETE FROM meeting_segment WHERE document_id = ?", (document_id,))
        self.conn.execute("DELETE FROM document_page WHERE document_id = ?", (document_id,))

        meeting_id = stable_id("meeting", document_id)
        meeting = bundle.get("meeting", {})
        region_code = meeting.get("administrative_region_code")
        if not region_code and meeting.get("assembly_id"):
            region_row = self.conn.execute(
                "SELECT region_code FROM administrative_region WHERE assembly_id=? LIMIT 1",
                (meeting.get("assembly_id"),),
            ).fetchone()
            region_code = region_row[0] if region_row else None
        if not region_code and meeting.get("city_county"):
            region_row = self.conn.execute(
                "SELECT region_code FROM administrative_region WHERE region_name=? LIMIT 1",
                (meeting.get("city_county"),),
            ).fetchone()
            region_code = region_row[0] if region_row else None
        if not region_code and meeting.get("province"):
            region_row = self.conn.execute(
                "SELECT region_code FROM administrative_region WHERE province=? AND region_type='parent_scope' LIMIT 1",
                (meeting.get("province"),),
            ).fetchone()
            region_code = region_row[0] if region_row else None
        self.conn.execute(
            """INSERT INTO meeting
            (meeting_id, document_id, council_level, administrative_region_code, assembly_id, assembly_name,
             province, city_county, session_number, assembly_number, meeting_order,
             meeting_type, meeting_title, meeting_date, agenda_text, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id) DO UPDATE SET
              council_level=excluded.council_level, administrative_region_code=excluded.administrative_region_code,
              assembly_id=excluded.assembly_id,
              assembly_name=excluded.assembly_name, province=excluded.province,
              city_county=excluded.city_county, session_number=excluded.session_number,
              assembly_number=excluded.assembly_number, meeting_order=excluded.meeting_order,
              meeting_type=excluded.meeting_type, meeting_title=excluded.meeting_title,
              meeting_date=excluded.meeting_date, agenda_text=excluded.agenda_text,
              metadata_json=excluded.metadata_json""",
            (
                meeting_id,
                document_id,
                meeting.get("council_level"),
                region_code,
                meeting.get("assembly_id"),
                meeting.get("assembly_name"),
                meeting.get("province"),
                meeting.get("city_county"),
                meeting.get("session_number"),
                meeting.get("assembly_number"),
                meeting.get("meeting_order"),
                meeting.get("meeting_type"),
                meeting.get("meeting_title") or source["title"],
                meeting.get("meeting_date"),
                meeting.get("agenda_text", ""),
                json.dumps(meeting.get("metadata", {}), ensure_ascii=False),
            ),
        )

        page_rows = bundle.get("pages") or [bundle.get("page", {})]
        for page_no, page in enumerate(page_rows, 1):
            page_id = stable_id("page", document_id, page_no)
            self.conn.execute(
                """INSERT INTO document_page
                (page_id, document_id, page_no, text_original, text_redacted,
                 raw_text_uri, image_uri, ocr_used, ocr_confidence, parser_name, parser_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    page_id,
                    document_id,
                    page_no,
                    page.get("text_original", ""),
                    page.get("text_redacted", page.get("text_original", "")),
                    page.get("raw_text_uri"),
                    page.get("image_uri"),
                    1 if page.get("ocr_used") else 0,
                    page.get("ocr_confidence"),
                    page.get("parser_name", "clik-html"),
                    page.get("parser_version", "1"),
                ),
            )

        for ordinal, segment in enumerate(bundle.get("segments", []), 1):
            segment_id = segment.get("segment_id") or stable_id("segment", document_id, ordinal)
            speaker_id = None
            if segment.get("speaker_name"):
                speaker_id = stable_id(
                    "speaker",
                    segment.get("speaker_name"),
                    segment.get("speaker_role"),
                    meeting.get("assembly_name"),
                )
                self.conn.execute(
                    """INSERT INTO speaker(speaker_id, name, role, affiliation)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(speaker_id) DO UPDATE SET role=excluded.role,
                      affiliation=excluded.affiliation""",
                    (
                        speaker_id,
                        segment["speaker_name"],
                        segment.get("speaker_role"),
                        meeting.get("assembly_name"),
                    ),
                )
            original_text = segment.get("text_original", "")
            redacted_text = segment.get("text_redacted", original_text)
            issue_rows = segment.get("issues", [])
            self.conn.execute(
                """INSERT INTO meeting_segment
                (segment_id, document_id, page_from, page_to, meeting_id, speaker_id,
                 section_title, agenda_no, ordinal, segment_type, text_original, text_redacted,
                 char_start, char_end, parse_confidence, relevance_status, review_status,
                 metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    segment_id,
                    document_id,
                    segment.get("page_from", 1),
                    segment.get("page_to", segment.get("page_from", 1)),
                    meeting_id,
                    speaker_id,
                    segment.get("section_title"),
                    segment.get("agenda_no"),
                    ordinal,
                    segment.get("segment_type", "paragraph"),
                    original_text,
                    redacted_text,
                    segment.get("char_start"),
                    segment.get("char_end"),
                    segment.get("parse_confidence", 1.0),
                    "relevant" if issue_rows or segment.get("relevant") else "unreviewed",
                    segment.get("review_status", "pending"),
                    json.dumps(segment.get("metadata", {}), ensure_ascii=False),
                ),
            )
            issue_text = " ".join(i.get("issue_code", "") for i in issue_rows)
            self.conn.execute(
                "INSERT INTO meeting_segment_fts(segment_id, title, text_redacted, issue_text) VALUES (?, ?, ?, ?)",
                (segment_id, meeting.get("meeting_title") or source["title"], redacted_text, issue_text),
            )
            for issue in issue_rows:
                code = issue["issue_code"]
                taxonomy_id = stable_id("taxonomy", "v1", code)
                self.conn.execute(
                    """INSERT INTO segment_issue
                    (segment_issue_id, segment_id, taxonomy_id, issue_code, polarity,
                     target_type, confidence, evidence_span, review_status, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        stable_id("segment_issue", segment_id, code),
                        segment_id,
                        taxonomy_id,
                        code,
                        issue.get("polarity", "unknown"),
                        issue.get("target_type", "unknown"),
                        issue.get("confidence", 0.7),
                        issue.get("evidence_span"),
                        issue.get("review_status", "pending"),
                        json.dumps(issue.get("metadata", {}), ensure_ascii=False),
                    ),
                )
            for place in segment.get("places", []):
                self._insert_place_link(document_id, segment_id, original_text, place)
        self.conn.execute(
            "UPDATE source_document SET processing_status = 'ready', updated_at = ? WHERE document_id = ?",
            (now_utc(), document_id),
        )
        from .hierarchy import rebuild_document_hierarchy

        rebuild_document_hierarchy(self, document_id)
        return document_id

    def _insert_place_link(
        self, document_id: str, segment_id: str, segment_text: str, place: dict[str, Any]
    ) -> None:
        place_id = place.get("place_id") or stable_id(
            "place",
            place.get("place_type", "unknown"),
            place.get("normalized_name"),
            place.get("road_address"),
            place.get("jibun_address"),
            place.get("province"),
            place.get("city_county"),
            place.get("eup_myeon"),
            place.get("ri"),
        )
        self.conn.execute(
            """INSERT INTO canonical_place
            (place_id, place_type, raw_name, normalized_name, road_address, jibun_address,
             province, city_county, eup_myeon, ri, admin_code, latitude, longitude,
             geom_wkt, geo_provider, geo_precision, geocode_confidence, location_status,
             resolution_method, source_document_id, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(place_id) DO UPDATE SET
              raw_name=excluded.raw_name, normalized_name=excluded.normalized_name,
              road_address=excluded.road_address, jibun_address=excluded.jibun_address,
              province=excluded.province, city_county=excluded.city_county,
              eup_myeon=excluded.eup_myeon, ri=excluded.ri, admin_code=excluded.admin_code,
              latitude=excluded.latitude, longitude=excluded.longitude,
              geo_provider=excluded.geo_provider, geo_precision=excluded.geo_precision,
              geocode_confidence=excluded.geocode_confidence,
              location_status=excluded.location_status, resolution_method=excluded.resolution_method,
              source_document_id=excluded.source_document_id, metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at""",
            (
                place_id,
                place.get("place_type", "unknown"),
                place.get("raw_name"),
                place.get("normalized_name") or place.get("raw_name"),
                place.get("road_address"),
                place.get("jibun_address"),
                place.get("province"),
                place.get("city_county"),
                place.get("eup_myeon"),
                place.get("ri"),
                place.get("admin_code"),
                place.get("latitude"),
                place.get("longitude"),
                place.get("geom_wkt"),
                place.get("geo_provider"),
                place.get("geo_precision", "unknown"),
                place.get("geocode_confidence"),
                place.get("location_status", "candidate"),
                place.get("resolution_method"),
                document_id,
                json.dumps(place.get("metadata", {}), ensure_ascii=False),
                now_utc(),
            ),
        )
        mention_id = stable_id("mention", segment_id, place.get("surface_form"), place_id)
        self.conn.execute(
            """INSERT INTO place_mention
            (mention_id, segment_id, surface_form, normalized_form, mention_type,
             context_text, confidence, review_status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mention_id,
                segment_id,
                place.get("surface_form") or place.get("raw_name") or place.get("normalized_name") or "",
                place.get("normalized_name"),
                place.get("mention_type", place.get("place_type", "unknown")),
                segment_text[:500],
                place.get("confidence", 0.7),
                place.get("review_status", "pending"),
                json.dumps(place.get("mention_metadata", {}), ensure_ascii=False),
            ),
        )
        self.conn.execute(
            """INSERT INTO place_resolution_candidate
            (mention_id, place_id, rank, match_method, confidence, resolution_reason,
             is_selected, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mention_id,
                place_id,
                1,
                place.get("resolution_method", "rule"),
                place.get("confidence", 0.7),
                place.get("resolution_reason"),
                1 if place.get("selected", True) else 0,
                place.get("review_status", "pending"),
            ),
        )
        link_id = stable_id("segment_place_link", segment_id, place_id, place.get("relation_type", "unknown"))
        self.conn.execute(
            """INSERT INTO segment_place_link
            (segment_place_link_id, segment_id, place_id, mention_id, relation_type,
             distance_m, distance_status, confidence, evidence_text, resolution_method,
             review_status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                link_id,
                segment_id,
                place_id,
                mention_id,
                place.get("relation_type", "unknown"),
                place.get("distance_m"),
                place.get("distance_status", "unknown"),
                place.get("confidence", 0.7),
                place.get("evidence_text") or place.get("surface_form"),
                place.get("resolution_method", "rule"),
                place.get("review_status", "pending"),
                json.dumps(place.get("metadata", {}), ensure_ascii=False),
            ),
        )

    def record_address_lookup(self, data: dict[str, Any]) -> str:
        lookup_id = stable_id("address_lookup", data["raw_query"], data["provider"])
        self.conn.execute(
            """INSERT INTO address_lookup
            (address_lookup_id, raw_query, normalized_query, provider, response_status,
             response_json, candidate_count, selected_place_id, resolution_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(address_lookup_id) DO UPDATE SET
              normalized_query=excluded.normalized_query, response_status=excluded.response_status,
              response_json=excluded.response_json, candidate_count=excluded.candidate_count,
              selected_place_id=excluded.selected_place_id, resolution_status=excluded.resolution_status""",
            (
                lookup_id,
                data["raw_query"],
                data.get("normalized_query"),
                data["provider"],
                data.get("response_status"),
                json.dumps(data.get("response_json", {}), ensure_ascii=False),
                data.get("candidate_count", 0),
                data.get("selected_place_id"),
                data.get("resolution_status", "unresolved"),
            ),
        )
        self.conn.commit()
        return lookup_id

    def upsert_canonical_place(self, place: dict[str, Any]) -> str:
        """Persist a resolved address even when it is not linked to a document."""
        place_id = place.get("place_id") or stable_id(
            "lookup_place",
            place.get("geo_provider"),
            place.get("road_address"),
            place.get("jibun_address"),
            place.get("admin_code"),
        )
        self.conn.execute(
            """INSERT INTO canonical_place
            (place_id, place_type, raw_name, normalized_name, road_address, jibun_address,
             province, city_county, eup_myeon, ri, admin_code, latitude, longitude,
             geo_provider, geo_precision, geocode_confidence, location_status,
             resolution_method, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(place_id) DO UPDATE SET
              normalized_name=excluded.normalized_name, road_address=excluded.road_address,
              jibun_address=excluded.jibun_address, province=excluded.province,
              city_county=excluded.city_county, eup_myeon=excluded.eup_myeon,
              ri=excluded.ri, admin_code=excluded.admin_code, latitude=excluded.latitude,
              longitude=excluded.longitude, geo_provider=excluded.geo_provider,
              geo_precision=excluded.geo_precision, geocode_confidence=excluded.geocode_confidence,
              location_status=excluded.location_status, resolution_method=excluded.resolution_method,
              metadata_json=excluded.metadata_json, updated_at=excluded.updated_at""",
            (
                place_id,
                place.get("place_type", "road_address"),
                place.get("raw_name"),
                place.get("normalized_name"),
                place.get("road_address"),
                place.get("jibun_address"),
                place.get("province"),
                place.get("city_county"),
                place.get("eup_myeon"),
                place.get("ri"),
                place.get("admin_code"),
                place.get("latitude"),
                place.get("longitude"),
                place.get("geo_provider"),
                place.get("geo_precision", "road_address"),
                place.get("geocode_confidence"),
                place.get("location_status", "reviewed"),
                place.get("resolution_method", "juso"),
                json.dumps(place.get("metadata", {}), ensure_ascii=False),
                now_utc(),
            ),
        )
        self.conn.commit()
        return place_id

    def record_search_request(self, data: dict[str, Any]) -> str:
        request_id = stable_id("search", now_utc(), data.get("raw_address"))
        self.conn.execute(
            """INSERT INTO search_request
            (search_request_id, raw_address, normalized_address, province, city_county,
             eup_myeon, ri, latitude, longitude, geocode_status, radius_m, keywords_json,
             issue_codes_json, from_date, limit_count, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                data["raw_address"],
                data.get("normalized_address"),
                data.get("province"),
                data.get("city_county"),
                data.get("eup_myeon"),
                data.get("ri"),
                data.get("latitude"),
                data.get("longitude"),
                data.get("geocode_status", "not_requested"),
                data.get("radius_m", 5000),
                json.dumps(data.get("keywords", []), ensure_ascii=False),
                json.dumps(data.get("issue_codes", []), ensure_ascii=False),
                data.get("from_date"),
                data.get("limit", 20),
                json.dumps(data.get("metadata", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return request_id

    def reclassify_keyword_labels(self, source_code: str = "clik_minutes") -> dict[str, int | str]:
        """Rebuild only keyword-derived labels for already stored segments.

        This avoids downloading source documents again when the precision rules
        change.  Original payloads, pages, place links, and source provenance
        remain untouched.  The new labels point to an extraction run so the
        change is auditable.
        """
        source_id = self._source_id(source_code)
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO extraction_run
            (extraction_run_id, job_type, model_name, model_version,
             validator_version, status, parameters_json)
            VALUES (?, 'keyword_classification', 'rules', 'precision-v2',
                    'keyword-precision-v2', 'running', ?)""",
            (run_id, json.dumps({"source_code": source_code}, ensure_ascii=False)),
        )
        rows = self.conn.execute(
            """SELECT s.segment_id, s.document_id, s.text_original, s.metadata_json,
                      m.meeting_title, m.agenda_text
               FROM meeting_segment s
               JOIN source_document d ON d.document_id = s.document_id
               JOIN meeting m ON m.meeting_id = s.meeting_id
               WHERE d.source_system_id = ?
               ORDER BY s.segment_id""",
            (source_id,),
        ).fetchall()
        place_link_count = self._rebuild_rule_place_links(source_id)
        segment_ids = [row["segment_id"] for row in rows]
        document_ids = sorted({row["document_id"] for row in rows})
        self.conn.executemany(
            "DELETE FROM segment_issue WHERE segment_id = ?",
            [(segment_id,) for segment_id in segment_ids],
        )
        self.conn.executemany(
            "DELETE FROM meeting_segment_fts WHERE segment_id = ?",
            [(segment_id,) for segment_id in segment_ids],
        )

        issue_count = 0
        relevant_count = 0
        for row in rows:
            context = f"{row['meeting_title'] or ''} {row['agenda_text'] or ''}".strip()
            classification = classify_segment(row["text_original"] or "", context)
            metadata = {}
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["keyword_classifier"] = {
                "version": "precision-v2",
                "solar_related": classification["solar_related"],
                "solar_anchor_hits": classification["solar_anchor_hits"],
                "standalone_high_precision_hits": classification["standalone_high_precision_hits"],
                "matched_issue_terms": classification["matched_issue_terms"],
                "admin_support_hits": classification["admin_support_hits"],
                "problem_categories": classification["problem_categories"],
            }
            self.conn.execute(
                """UPDATE meeting_segment
                   SET relevance_status = ?, metadata_json = ?
                 WHERE segment_id = ?""",
                (
                    "relevant" if classification["relevant"] else "unreviewed",
                    json.dumps(metadata, ensure_ascii=False),
                    row["segment_id"],
                ),
            )
            if classification["relevant"]:
                relevant_count += 1
            issue_rows = classification["issues"]
            self.conn.execute(
                """INSERT INTO meeting_segment_fts
                   (segment_id, title, text_redacted, issue_text)
                   SELECT s.segment_id, m.meeting_title, s.text_redacted, ?
                     FROM meeting_segment s
                     JOIN meeting m ON m.meeting_id = s.meeting_id
                    WHERE s.segment_id = ?""",
                (" ".join(issue["issue_code"] for issue in issue_rows), row["segment_id"]),
            )
            for issue in issue_rows:
                code = issue["issue_code"]
                self.conn.execute(
                    """INSERT INTO segment_issue
                       (segment_issue_id, segment_id, taxonomy_id, issue_code,
                        polarity, target_type, confidence, evidence_span,
                        extraction_run_id, review_status, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (
                        stable_id("segment_issue", row["segment_id"], code),
                        row["segment_id"],
                        stable_id("taxonomy", "v1", code),
                        code,
                        issue.get("polarity", "unknown"),
                        issue.get("target_type", "unknown"),
                        issue.get("confidence", 0.7),
                        issue.get("evidence_span"),
                        run_id,
                        json.dumps(issue.get("metadata", {}), ensure_ascii=False),
                    ),
                )
                issue_count += 1
        self.conn.execute(
            """UPDATE extraction_run SET status='completed', finished_at=CURRENT_TIMESTAMP,
               parameters_json=? WHERE extraction_run_id=?""",
            (
                json.dumps({"source_code": source_code, "segment_count": len(rows), "issue_count": issue_count}, ensure_ascii=False),
                run_id,
            ),
        )
        from .hierarchy import rebuild_document_hierarchy

        hierarchy_counts = {
            "sentences": 0,
            "mentions": 0,
            "episodes": 0,
            "cases": 0,
            "case_evidence": 0,
            "process_events": 0,
            "case_link_candidates": 0,
        }
        for document_id in document_ids:
            counts = rebuild_document_hierarchy(self, document_id)
            for key, value in counts.items():
                hierarchy_counts[key] += value
        return {
            "extraction_run_id": run_id,
            "segments": len(rows),
            "relevant_segments": relevant_count,
            "issues": issue_count,
            "place_links": place_link_count,
            **hierarchy_counts,
        }

    def _rebuild_rule_place_links(self, source_id: str) -> int:
        """Re-extract only unreviewed place links for one source.

        Place extraction rules are part of the evidence pipeline. Re-running
        keyword classification should therefore also remove stale rule-based
        links, while preserving any link that a reviewer has promoted out of
        ``pending``. Orphaned candidate places remain as historical audit data;
        only active segment links are replaced.
        """
        from .extract import extract_places

        rows = self.conn.execute(
            """SELECT s.segment_id, s.document_id, s.text_original,
                      m.province, m.city_county
                 FROM meeting_segment s
                 JOIN source_document d ON d.document_id=s.document_id
                 LEFT JOIN meeting m ON m.meeting_id=s.meeting_id
                WHERE d.source_system_id=?
                ORDER BY s.segment_id""",
            (source_id,),
        ).fetchall()
        inserted = 0
        for row in rows:
            segment_id = row["segment_id"]
            pending_mentions = [
                mention["mention_id"]
                for mention in self.conn.execute(
                    "SELECT mention_id FROM place_mention WHERE segment_id=? AND review_status='pending'",
                    (segment_id,),
                ).fetchall()
            ]
            self.conn.execute(
                "DELETE FROM segment_place_link WHERE segment_id=? AND review_status='pending'",
                (segment_id,),
            )
            if pending_mentions:
                self.conn.executemany(
                    "DELETE FROM place_mention WHERE mention_id=?",
                    [(mention_id,) for mention_id in pending_mentions],
                )
            context = " ".join(value for value in (row["province"], row["city_county"]) if value)
            for place in extract_places(row["text_original"] or "", context):
                self._insert_place_link(row["document_id"], segment_id, row["text_original"] or "", place)
                inserted += 1
        return inserted

    def stats(self) -> dict[str, int]:
        tables = [
            "source_document",
            "document_artifact",
            "administrative_region",
            "document_page",
            "meeting_segment",
            "sentences",
            "keyword_mentions",
            "episodes",
            "episode_evidence",
            "conflict_case",
            "case_review",
            "case_evidence",
            "case_process_event",
            "case_location_candidate",
            "case_link_candidate",
            "canonical_place",
            "segment_issue",
            "siting_rule",
            "permit_project",
            "project_intake",
            "project_field_definition",
            "project_intake_submission",
            "project_application",
            "project_site",
            "project_equipment",
            "project_finance",
            "project_schedule",
            "project_grid",
            "project_permit_checklist",
            "project_resident_risk",
            "project_attachment",
            "project_fact",
            "project_stage",
            "project_stage_event",
            "project_location_link",
            "project_case_link",
            "complaint_submission",
            "chat_conversation",
            "chat_message",
            "complaint_evidence",
        ]
        return {table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}

    def commit(self) -> None:
        self.conn.commit()
