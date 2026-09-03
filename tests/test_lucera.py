from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lucera.cli import demo_bundles
from lucera.db import LuceraDB, stable_id
from lucera.extract import extract_places
from lucera.extract import parse_minutes_html
from lucera.extract import redact_sensitive
from lucera.ingest import make_clik_bundle, materialize_clik_bundle
from lucera.keywords import classify_text, collection_query_plan
from lucera.location import normalize_address
from lucera.projects import create_project, get_precheck, get_project
from lucera.regions import parent_region_catalog, region_catalog, region_for_name
from lucera.review import rebuild_case_reviews
from lucera.search import SearchService


class LuceraTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.db = LuceraDB(self.db_path)
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")
        for bundle in demo_bundles():
            self.db.insert_document_bundle(bundle)
        rebuild_case_reviews(self.db)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_seed_is_idempotent(self) -> None:
        before = self.db.stats()
        for bundle in demo_bundles():
            self.db.insert_document_bundle(bundle)
        self.db.commit()
        self.assertEqual(before, self.db.stats())

    def test_admin_search_does_not_invent_distance(self) -> None:
        result = SearchService(self.db).search(
            {"address": "전라남도 영암군 삼호읍", "resolve_address": False, "limit": 10, "review_mode": "all"}
        )
        self.assertGreaterEqual(result["summary"]["total"], 2)
        self.assertFalse(result["summary"]["coordinate_search_used"])
        for item in result["results"]:
            if item["location_match"]["group"] != "comparative_case":
                self.assertIsNone(item["location_match"]["distance_m"])
                self.assertEqual(item["location_match"]["distance_status"], "unknown")

    def test_issue_filter(self) -> None:
        result = SearchService(self.db).search(
            {
                "address": "전라남도 영암군 삼호읍",
                "resolve_address": False,
                "issue_codes": ["communication_procedure"],
                "limit": 10,
            }
        )
        self.assertEqual(result["summary"]["total"], 1)
        self.assertEqual(result["results"][0]["issues"][0]["issue_code"], "communication_procedure")

    def test_search_exposes_case_and_episode_identity(self) -> None:
        result = SearchService(self.db).search(
            {"address": "전라남도 영암군 삼호읍", "resolve_address": False, "limit": 10}
        )
        self.assertGreater(result["summary"]["case_count"], 0)
        linked = next(item for item in result["results"] if item["case"] and item["episode"])
        self.assertIn("case_id", linked["case"])
        self.assertIn("episode_id", linked["episode"])
        self.assertIn("inferred_location", linked["case"])
        self.assertGreaterEqual(len(result["case_groups"]), 1)
        grouped = next(group for group in result["case_groups"] if group["paragraphs"])
        self.assertGreaterEqual(grouped["paragraph_count"], 1)
        self.assertIn("text_original", grouped["paragraphs"][0])
        detail = SearchService(self.db).get_case_paragraphs(grouped["case"]["case_id"])
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["paragraph_count"], grouped["paragraph_count"])

    def test_primary_document_case_paragraph_views(self) -> None:
        pair_count = self.db.conn.execute("SELECT COUNT(*) FROM document_cases").fetchone()[0]
        paragraph_count = self.db.conn.execute("SELECT COUNT(*) FROM case_paragraphs").fetchone()[0]
        self.assertGreater(pair_count, 0)
        self.assertGreater(paragraph_count, 0)
        self.assertEqual(
            self.db.conn.execute(
                """SELECT COUNT(*) FROM document_cases dc
                   WHERE dc.paragraph_count <> (
                       SELECT COUNT(*) FROM case_paragraphs cp
                        WHERE cp.case_id=dc.case_id AND cp.document_id=dc.document_id
                   )"""
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.db.conn.execute(
                """SELECT COUNT(*) FROM document_cases dc
                   WHERE NOT EXISTS (
                       SELECT 1 FROM case_paragraphs cp
                        WHERE cp.case_id=dc.case_id AND cp.document_id=dc.document_id
                   )"""
            ).fetchone()[0],
            0,
        )

    def test_gwangju_jeonnam_region_catalog_and_unified_name(self) -> None:
        self.assertEqual(len(region_catalog()), 27)
        self.assertEqual({row["region_group"] for row in region_catalog()}, {"자치시", "자치군", "자치구"})
        self.assertEqual(len(parent_region_catalog()), 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM administrative_region WHERE region_type <> 'parent_scope'").fetchone()[0], 27)
        self.assertEqual(sorted(tuple(row) for row in self.db.conn.execute("SELECT region_group, COUNT(*) FROM administrative_region WHERE region_type <> 'parent_scope' GROUP BY region_group").fetchall()), sorted([("자치구", 5), ("자치시", 5), ("자치군", 17)]))
        self.assertEqual(region_for_name("전라남도 영암군 삼호읍")["name"], "영암군")
        location = normalize_address("전남광주통합특별시 광양시")
        self.assertEqual(location.province, "전라남도")
        self.assertEqual(location.city_county, "광양시")
        gwangju_district = normalize_address("광주광역시 동구").to_dict()
        self.assertEqual(gwangju_district["city_county"], "동구")
        self.assertEqual(region_for_name("광주광역시 동구")['region_group'], "자치구")
        self.assertEqual(normalize_address("광산구").province, "광주광역시")

    def test_coordinate_search_uses_distance_only_when_available(self) -> None:
        self.db.insert_document_bundle(
            {
                "source": {
                    "system_code": "demo_fixture",
                    "source_record_key": "demo-coordinate",
                    "title": "[데모] 좌표가 확인된 발전시설 근거",
                    "document_type": "meeting_minutes",
                    "access_policy": "demo",
                },
                "meeting": {
                    "assembly_name": "전라남도 영암군의회",
                    "province": "전라남도",
                    "city_county": "영암군",
                    "meeting_title": "[데모] 좌표 근거 확인",
                    "meeting_date": "2024-06-01",
                },
                "page": {"text_original": "좌표가 확인된 사업지 설명회"},
                "segments": [
                    {
                        "text_original": "사업지 설명회에서 주민 협의 절차를 확인했다.",
                        "issues": [
                            {"issue_code": "communication_procedure", "polarity": "neutral", "confidence": 0.8}
                        ],
                        "places": [
                            {
                                "surface_form": "사업지",
                                "place_type": "road_address",
                                "province": "전라남도",
                                "city_county": "영암군",
                                "eup_myeon": "삼호읍",
                                "latitude": 34.8,
                                "longitude": 126.4,
                                "geo_precision": "road_address",
                                "location_status": "confirmed",
                                "relation_type": "subject_site",
                                "distance_status": "exact",
                                "confidence": 0.99,
                            }
                        ],
                        "relevant": True,
                    }
                ],
            }
        )
        self.db.commit()
        rebuild_case_reviews(self.db)
        result = SearchService(self.db).search(
            {
                "address": "전라남도 영암군 삼호읍",
                "latitude": 34.8,
                "longitude": 126.4,
                "resolve_address": False,
                "keywords": ["설명회"],
                "review_mode": "all",
                "limit": 10,
            }
        )
        target = next(item for item in result["results"] if item["location_match"]["group"] == "exact_site")
        self.assertEqual(target["location_match"]["group"], "exact_site")
        self.assertEqual(target["location_match"]["distance_status"], "exact")
        self.assertEqual(target["location_match"]["distance_m"], 0.0)

    def test_sensitive_text_is_redacted(self) -> None:
        redacted = redact_sensitive("연락처 010-1234-5678, mail test@example.com")
        self.assertNotIn("010-1234-5678", redacted)
        self.assertNotIn("test@example.com", redacted)

    def test_place_extraction_does_not_turn_common_words_into_ri(self) -> None:
        places = extract_places("검토 결과를 말씀드리며 일자리와 관리 방안을 논의했다.")
        self.assertEqual(places, [])
        self.assertEqual(extract_places("관리 및 처리 방안을 논의했다."), [])
        places = extract_places("해당 사업은 삼호읍 주민과 협의한다.", "전라남도 영암군")
        self.assertTrue(any(place["normalized_name"].endswith("삼호읍") for place in places))
        lot_places = extract_places("우산동 1576-2번지 태양광 발전설비 설치공사", "광주광역시 광산구")
        lot = next(place for place in lot_places if place["place_type"] == "jibun_address")
        self.assertEqual(lot["jibun_address"], "광주광역시 광산구 우산동 1576-2번지")
        self.assertEqual(lot["relation_type"], "subject_site")
        self.assertIsNone(lot["latitude"])

    def test_precision_keyword_rules(self) -> None:
        glare = classify_text("주민들이 빛반사를 호소했다.")
        self.assertEqual([item["issue_code"] for item in glare["issues"]], ["glare_reflection"])
        self.assertIn("standalone_high_precision", glare["issues"][0]["metadata"]["rule_id"])

        generic = classify_text("주민과 환경 문제를 점검했다.")
        self.assertEqual(generic["issues"], [])

        siting = classify_text("태양광발전시설의 이격거리와 개발행위허가 기준을 검토했다.")
        self.assertIn("siting_permit_regulatory", {item["issue_code"] for item in siting["issues"]})

        resident = classify_text("태양광 사업에 대한 주민 민원이 접수되었고 설명회를 요구했다.")
        self.assertIn("communication_procedure", {item["issue_code"] for item in resident["issues"]})

        negated = classify_text("태양광 사업에 반대의견이 없으므로 조례안을 원안대로 의결했다.")
        self.assertNotIn("communication_procedure", {item["issue_code"] for item in negated["issues"]})
        self.assertNotIn("opposition", {item["polarity"] for item in negated["issues"]})

        public_program = classify_text("태양광 조명을 설치하여 안전한 보행 환경을 조성한다.")
        self.assertNotIn("safety_environment", {item["issue_code"] for item in public_program["issues"]})

    def test_gazetteer_blocks_suffix_false_positive_and_comparative_reference(self) -> None:
        self.assertEqual(extract_places("보면 하면 측면 관리 일자리를 검토했다."), [])
        places = extract_places("북하면 태양광 사업을 비교 검토했다.", "전라남도 완도군")
        self.assertTrue(any(place["relation_type"] == "comparative" for place in places))
        self.assertTrue(all(place["city_county"] == "장성군" for place in places))

    def test_case_review_persists_decision_and_reasons(self) -> None:
        counts = rebuild_case_reviews(self.db)
        self.assertEqual(counts["cases"], self.db.conn.execute("SELECT COUNT(*) FROM conflict_case").fetchone()[0])
        self.assertEqual(counts["cases"], self.db.conn.execute("SELECT COUNT(*) FROM case_review").fetchone()[0])
        self.assertGreater(self.db.conn.execute("SELECT COUNT(*) FROM case_review WHERE decision IN ('eligible', 'needs_review', 'rejected')").fetchone()[0], 0)
        self.assertGreater(self.db.conn.execute("SELECT COUNT(*) FROM review_task WHERE target_type='case'").fetchone()[0], 0)

    def test_hierarchy_stores_offsets_and_merges_adjacent_paragraphs(self) -> None:
        text_one = "삼호읍 태양광발전시설 주민반대와 이격거리 민원이 제기됐다."
        text_two = "주민들은 빛반사와 경관훼손 피해에 대한 설명회를 요구했다."
        self.db.insert_document_bundle(
            {
                "source": {
                    "system_code": "demo_fixture",
                    "source_record_key": "hierarchy-adjacent",
                    "title": "[테스트] 태양광 주민민원 연속 발언",
                    "document_type": "meeting_minutes",
                    "access_policy": "demo",
                },
                "meeting": {
                    "assembly_name": "전라남도 영암군의회",
                    "province": "전라남도",
                    "city_county": "영암군",
                    "meeting_title": "태양광발전시설 주민민원 안건",
                    "agenda_text": "1. 태양광발전시설 주민민원",
                    "meeting_date": "2024-06-01",
                },
                "page": {"text_original": f"{text_one} {text_two}"},
                "segments": [
                    {"text_original": text_one, "agenda_no": "1", "segment_type": "speech"},
                    {"text_original": text_two, "agenda_no": "1", "segment_type": "speech"},
                    {"text_original": "회의를 마치고 다음 안건으로 넘어갔다.", "agenda_no": "2"},
                ],
            }
        )
        self.db.commit()

        stats = self.db.stats()
        self.assertGreater(stats["sentences"], 3)
        self.assertGreater(stats["keyword_mentions"], 0)
        document_id = self.db.conn.execute(
            "SELECT document_id FROM source_document WHERE source_record_key='hierarchy-adjacent'"
        ).fetchone()[0]
        self.assertEqual(
            self.db.conn.execute("SELECT administrative_region_code FROM meeting WHERE document_id=?", (document_id,)).fetchone()[0],
            "061016",
        )
        episodes = self.db.conn.execute(
            "SELECT episode_id, paragraph_start, paragraph_end FROM episodes WHERE document_id=?",
            (document_id,),
        ).fetchall()
        self.assertEqual(len(episodes), 1)
        self.assertEqual((episodes[0]["paragraph_start"], episodes[0]["paragraph_end"]), (1, 2))
        self.assertGreaterEqual(self.db.conn.execute("SELECT COUNT(*) FROM case_location_candidate WHERE case_id IN (SELECT case_id FROM case_evidence WHERE episode_id=?)", (episodes[0]["episode_id"],)).fetchone()[0], 1)

        sentence = self.db.conn.execute(
            """SELECT s.text, s.char_start, s.char_end, k.keyword
                 FROM sentences s
                 JOIN keyword_mentions k ON k.sentence_id=s.sentence_id
                 JOIN meeting_segment p ON p.segment_id=s.paragraph_id
                WHERE p.document_id=? AND s.text LIKE '%빛반사%'
                  AND k.normalized_keyword='빛반사'
                LIMIT 1"""
            , (document_id,)
        ).fetchone()
        self.assertIsNotNone(sentence)
        self.assertEqual(sentence["char_start"], 0)
        self.assertEqual(sentence["char_end"], len(text_two))

    def test_hierarchy_hard_break_creates_separate_episodes(self) -> None:
        self.db.insert_document_bundle(
            {
                "source": {
                    "system_code": "demo_fixture",
                    "source_record_key": "hierarchy-hard-break",
                    "title": "[테스트] 서로 다른 민원",
                    "document_type": "meeting_minutes",
                    "access_policy": "demo",
                },
                "meeting": {
                    "assembly_name": "전라남도 영암군의회",
                    "province": "전라남도",
                    "city_county": "영암군",
                    "meeting_title": "태양광 민원 보고",
                    "meeting_date": "2024-06-02",
                },
                "page": {"text_original": "서로 다른 민원"},
                "segments": [
                    {"text_original": "태양광 민원이 접수되었고 주민 반대가 있었다. 또 다른 민원으로 수상태양광 허가를 검토했다."},
                ],
            }
        )
        self.db.commit()
        document_id = self.db.conn.execute(
            "SELECT document_id FROM source_document WHERE source_record_key='hierarchy-hard-break'"
        ).fetchone()[0]
        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE document_id=?", (document_id,)
        ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_specialized_facility_alone_does_not_merge_cases_across_documents(self) -> None:
        for key, county in (("facility-only-a", "영암군"), ("facility-only-b", "고흥군")):
            self.db.insert_document_bundle(
                {
                    "source": {
                        "system_code": "demo_fixture",
                        "source_record_key": key,
                        "title": "[테스트] 시설 유형만 같은 별도 민원",
                        "document_type": "meeting_minutes",
                        "access_policy": "demo",
                    },
                    "meeting": {
                        "assembly_name": f"전라남도 {county}의회",
                        "province": "전라남도",
                        "city_county": county,
                        "meeting_title": "수상태양광 민원",
                        "meeting_date": "2024-06-03",
                    },
                    "page": {"text_original": "수상태양광 민원"},
                    "segments": [{"text_original": "수상태양광 사업에 주민 반대 민원이 있었다."}],
                }
            )
        self.db.commit()
        rows = self.db.conn.execute(
            """SELECT DISTINCT c.case_key, c.municipality
                 FROM conflict_case c
                 JOIN case_evidence ce ON ce.case_id=c.case_id
                 JOIN episodes e ON e.episode_id=ce.episode_id
                 JOIN source_document d ON d.document_id=e.document_id
                WHERE d.source_record_key IN ('facility-only-a', 'facility-only-b')"""
        ).fetchall()
        self.assertEqual({row["municipality"] for row in rows}, {"영암군", "고흥군"})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["case_key"].startswith("episode:") for row in rows))

    def test_collection_plan_does_not_use_generic_singletons(self) -> None:
        queries = {item["query"] for item in collection_query_plan()}
        self.assertIn("태양광 민원", queries)
        self.assertIn("빛반사", queries)
        self.assertNotIn("주민", queries)
        self.assertNotIn("환경", queries)

    def test_html_speaker_and_issue_extraction_input(self) -> None:
        page, segments = parse_minutes_html("<p>의사일정</p><spk><b>위원장 홍길동</b> 태양광 민원과 주민 설명회가 필요합니다.</spk>")
        self.assertIn("의사일정", page)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["speaker_name"], "홍길동")

    def test_clik_page_text_is_redacted_before_storage(self) -> None:
        bundle = make_clik_bundle(
            {
                "DOCID": "TEST-PII",
                "RASMBLY_NM": "전라남도 영암군의회",
                "MTG_DE": "20240601",
                "MTGNM": "본회의",
                "MINTS_HTML": "<spk>위원장 홍길동 연락처 010-1234-5678의 태양광 민원입니다.</spk>",
            }
        )
        self.assertNotIn("010-1234-5678", bundle["page"]["text_redacted"])
        self.assertNotIn("010-1234-5678", bundle["segments"][0]["text_redacted"])
        self.assertEqual(bundle["source"]["mime_type"], "application/json")
        self.assertEqual(bundle["source"]["metadata"]["embedded_content_mime_type"], "text/html")
        self.assertEqual(bundle["artifacts"][0]["acquisition_method"], "api_detail_response")

    def test_clik_detail_materialization_keeps_db_artifact_checksum(self) -> None:
        bundle = make_clik_bundle(
            {
                "DOCID": "TEST-MATERIALIZED",
                "RASMBLY_NM": "전라남도 영암군의회",
                "MTG_DE": "20240601",
                "MTGNM": "본회의",
                "MINTS_HTML": "<spk>태양광 민원과 빛반사 우려를 확인합니다.</spk>",
            }
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(materialize_clik_bundle(bundle, root, region_code="061016"))
            self.assertTrue(path.exists())
            self.assertEqual(bundle["source"]["storage_uri"], str(path.resolve()))
            self.assertEqual(bundle["source"]["sha256"], bundle["artifacts"][0]["sha256"])
            self.assertEqual(bundle["artifacts"][0]["storage_uri"], str(path.resolve()))

    def test_project_intake_persists_revisioned_flow_and_provenance(self) -> None:
        result = create_project(
            self.db,
            {
                "project_key": "project-flow-1",
                "business": {"project_name": "삼호 태양광", "business_type": "태양광발전시설", "permit_type": "발전사업허가"},
                "applicant": {"applicant_name": "신청자", "applicant_type": "법인", "corporate_name": "에너지법인"},
                "site": {"site_address": "전라남도 영암군 삼호읍 삼호리", "lot_number": "100-1", "land_category": "전"},
                "equipment": {"installed_capacity_kw": 100, "module_count": 200, "module_capacity_w": 500, "installation_height_m": 2.5},
                "schedule": {"permit_application_date": "2025-01-01", "permit_date": "2025-02-01", "construction_start_date": "2025-03-01"},
                "permits": {"development_permit_required": True, "environmental_assessment_required": True, "structural_safety_review": True},
                "resident": {"resident_consent_required": True, "complaint_occurred": True, "complaint_type": "빛반사"},
                "locations": {"site": {"latitude": 34.8, "longitude": 126.4, "geo_precision": "parcel"}},
                "business_plan": {"file_name": "business-plan.pdf", "file_size_bytes": 10, "extraction_status": "queued", "extracted_facts": [{"field_name": "total_project_cost_krw", "value": 1000000, "source_page": 3, "confidence": 0.92}]},
            },
        )
        project = get_project(self.db, result["project_id"])
        self.assertIsNotNone(project)
        assert project is not None
        self.assertEqual(project["application"]["revision_no"], 1)
        self.assertEqual(len(project["workflow"]), 6)
        self.assertGreaterEqual(len(project["stage_events"]), 4)
        self.assertEqual(len(project["attachments"]), 1)
        self.assertTrue(any(fact["source_kind"] == "attachment" and fact["source_page"] == 3 for fact in project["facts"]))
        self.assertEqual(project["locations"][0]["location_status"], "confirmed")
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_application").fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_stage WHERE application_id=?", (result["application_id"],)).fetchone()[0], 6)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_stage_event WHERE application_id=?", (result["application_id"],)).fetchone()[0], result["stage_event_count"])
        precheck = get_precheck(self.db, result["project_id"])
        self.assertTrue(any(flag["code"] == "historical_complaint_reported" for flag in precheck["risk_flags"]))

    def test_project_intake_v2_catalog_and_audit_columns_are_populated(self) -> None:
        result = create_project(
            self.db,
            {
                "project_key": "project-v2-audit",
                "submitted_by": "tester",
                "intake_channel": "api",
                "business": {"project_name": "감사 추적 태양광", "business_type": "영농형태양광", "permit_type": "발전사업허가"},
                "applicant": {"applicant_name": "신청자", "applicant_type": "법인", "corporate_name": "루체라에너지", "contractor_name": "시공사"},
                "site": {"site_address": "전라남도 고흥군 동일면 동일리", "lot_number": "12-3", "land_category": "전", "building_address": "전라남도 고흥군 동일면 동일리 12-3", "building_use": "창고"},
                "equipment": {"installed_capacity_kw": 120, "module_count": 240, "module_capacity_w": 500, "inverter_count": 4, "inverter_capacity_kva": 35, "installation_height_m": 2, "installation_area_sqm": 900},
                "finance": {"total_project_cost_krw": 150000000, "construction_cost_per_kw": 1250000, "annual_generation_mwh": 150, "annual_transmission_mwh": 145, "lease_fee_krw": 3000000, "resident_revenue_share": 12.5},
                "schedule": {"permit_application_date": "2025-01-01", "permit_date": "2025-02-01", "construction_start_date": "2025-03-01", "expected_completion_date": "2025-06-01", "business_start_date": "2025-07-01", "operation_period_years": 20},
                "grid": {"grid_connection_point": "고흥 변전소", "connection_voltage_v": 22000, "transformer_info": "100kVA", "power_purchase_method": "전력시장"},
                "permits": {"development_permit_required": True, "urban_management_plan_required": False, "construction_plan_report": True, "environmental_assessment_required": True, "structural_safety_review": True},
                "resident": {"resident_consent_required": True, "construction_consent": False, "complaint_occurred": True, "complaint_stop_commitment": False, "removal_commitment": False, "complaint_type": "경관·빛반사"},
                "locations": {"site": {"latitude": 34.62, "longitude": 127.28, "geo_precision": "parcel", "geo_provider": "juso", "resolution_method": "address_api"}},
                "business_plan": {"file_name": "plan.pdf", "mime_type": "application/pdf", "sha256": "a" * 64, "file_size_bytes": 100, "page_count": 8, "extraction_status": "extracted", "extractor_name": "OpenDataLoader", "extractor_version": "1.0", "text_sha256": "b" * 64, "ocr_used": False, "extracted_facts": [{"field_name": "total_project_cost_krw", "value": 150000000, "source_page": 4, "source_char_start": 10, "source_char_end": 20, "source_excerpt": "총사업비 1억5천만원", "confidence": 0.94}]},
            },
        )
        project = get_project(self.db, result["project_id"])
        assert project is not None
        app = project["application"]
        self.assertEqual(app["source_submission_id"], self.db.conn.execute("SELECT submission_id FROM project_intake_submission WHERE project_id=?", (result["project_id"],)).fetchone()[0])
        self.assertEqual(self.db.conn.execute("SELECT input_schema_version, intake_channel, updated_by FROM project_intake WHERE project_id=?", (result["project_id"],)).fetchone()[0:2], ("project-intake-v2", "api"))
        submission = self.db.conn.execute("SELECT schema_version, payload_sha256, validated_at FROM project_intake_submission WHERE submission_id=?", (app["source_submission_id"],)).fetchone()
        self.assertEqual(submission["schema_version"], "project-intake-v2")
        self.assertEqual(len(submission["payload_sha256"]), 64)
        self.assertIsNotNone(submission["validated_at"])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_field_definition WHERE active=1").fetchone()[0], 46)
        self.assertEqual(project["site"]["site_resolution_status"], "resolved")
        self.assertEqual(project["site"]["site_address_type"], "jibun")
        self.assertEqual(project["schedule"]["schedule_status"], "completed")
        self.assertEqual(project["grid"]["connection_status"], "connected")
        self.assertEqual(project["permit_checklist"]["checklist_status"], "completed")
        self.assertEqual(project["resident_risk"]["risk_status"], "reported")
        attachment = project["attachments"][0]
        self.assertEqual(attachment["extractor_name"], "OpenDataLoader")
        self.assertEqual(attachment["page_count"], 8)
        extracted = [fact for fact in project["facts"] if fact["source_kind"] == "attachment"]
        self.assertEqual(extracted[0]["source_page"], 4)
        self.assertEqual(extracted[0]["source_char_start"], 10)
        self.assertEqual(extracted[0]["fact_status"], "active")
        self.assertTrue(all(stage["source_fact_id"] or stage["source_field"] is None for stage in project["workflow"]))
        self.assertTrue(any(event["source_fact_id"] for event in project["stage_events"]))

    def test_project_revision_supersedes_previous_application(self) -> None:
        first = create_project(self.db, {"project_key": "revision-1", "project_name": "초기 사업", "site_address": "전라남도 영암군 삼호읍"})
        second = create_project(self.db, {"project_key": "revision-1", "project_name": "수정 사업", "site_address": "전라남도 영암군 삼호읍", "project_status": "under_review"})
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertEqual(second["revision_no"], 2)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_application WHERE project_id=?", (first["project_id"],)).fetchone()[0], 2)
        self.assertEqual(self.db.conn.execute("SELECT status FROM project_application WHERE application_id=?", (first["application_id"],)).fetchone()[0], "superseded")
        current = get_project(self.db, first["project_id"])
        self.assertEqual(current["application"]["application_id"], second["application_id"])

    def test_project_links_historical_case_only_with_location_evidence(self) -> None:
        place_id = stable_id("test-historical-place")
        case_id = stable_id("test-historical-case")
        self.db.conn.execute(
            """INSERT INTO canonical_place
               (place_id, place_type, raw_name, normalized_name, province,
                city_county, eup_myeon, ri, geo_precision, location_status)
               VALUES (?, 'ri', ?, ?, ?, ?, ?, ?, 'ri', 'confirmed')""",
            (place_id, "동일리", "전라남도 고흥군 동일면 동일리", "전라남도", "고흥군", "동일면", "동일리"),
        )
        self.db.conn.execute(
            """INSERT INTO conflict_case
               (case_id, case_key, case_name, canonical_title, municipality,
                village, facility_type, representative_place_id, confidence,
                review_status, metadata_json)
               VALUES (?, ?, '삼호리 과거 민원', '삼호리 과거 민원', ?, ?, '태양광', ?, 0.8, 'pending', '{}')""",
            (case_id, "test-historical-case", "고흥군", "동일리", place_id),
        )
        self.db.commit()
        result = create_project(self.db, {"project_name": "동일리 신규 태양광", "site_address": "전라남도 고흥군 동일면 동일리", "complaint_occurred": True, "complaint_type": "주민 반대"})
        self.assertGreaterEqual(result["historical_case_match_count"], 1)
        linked = self.db.conn.execute("SELECT case_id, stage_code, match_score FROM project_case_link WHERE application_id=? AND case_id=?", (result["application_id"], case_id)).fetchone()
        self.assertEqual(linked["case_id"], case_id)
        self.assertEqual(linked["stage_code"], "resident_consultation_complaint")
        self.assertGreaterEqual(linked["match_score"], 0.35)
        stage_case = self.db.conn.execute("SELECT case_id FROM project_stage WHERE application_id=? AND stage_code=?", (result["application_id"], "resident_consultation_complaint")).fetchone()[0]
        self.assertEqual(stage_case, case_id)

    def test_project_validation_rejects_bad_input_without_partial_rows(self) -> None:
        with self.assertRaises(ValueError):
            create_project(self.db, {"project_name": "잘못된 사업", "site_address": "전라남도 영암군", "module_count": 2.5, "business_plan": {"extraction_status": "bad"}})
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM project_intake").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
