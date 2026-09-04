from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lucera.complaints import continue_conversation, create_complaint, get_conversation, yeongam_area_detail, yeongam_pins
from lucera.db import LuceraDB
from lucera.server import runtime_status
from lucera.synthetic import seed_synthetic


class YeongamFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = LuceraDB(Path(self.temp.name) / "flow.sqlite3")
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")
        seed_synthetic(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_synthetic_database_is_yeongam_only(self) -> None:
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0], 6)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM permit_project WHERE city_county <> '영암군'").fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM siting_rule WHERE rule_id LIKE 'synthetic-%' AND region_code NOT IN (SELECT region_code FROM administrative_region WHERE region_name='영암군')").fetchone()[0], 0)
        map_data = yeongam_pins(self.db)
        self.assertEqual(map_data["count"], 16)
        self.assertGreaterEqual(len(map_data["areas"]), 8)

    def test_area_detail_returns_only_the_selected_yeongam_area(self) -> None:
        detail = yeongam_area_detail(self.db, "삼호읍")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["summary"]["permit_count"], 3)
        self.assertTrue(all(pin["eup_myeon"] == "삼호읍" for pin in detail["pins"]))
        self.assertIsNone(yeongam_area_detail(self.db, "함평군"))

    def test_complaint_saves_coordinates_evidence_and_initial_messages(self) -> None:
        result = create_complaint(
            self.db,
            {
                "address": "전라남도 영암군 삼호읍 가상리 45-2",
                "text": "집중호우 때 배수로와 토사 유출이 걱정됩니다.",
                "latitude": 34.8,
                "longitude": 126.42,
                "resolve_address": False,
                "include_map_context": False,
            },
        )
        self.assertEqual((result["complaint"]["latitude"], result["complaint"]["longitude"]), (34.8, 126.42))
        self.assertEqual(result["complaint"]["city_county"], "영암군")
        self.assertTrue(result["analysis"]["input"]["include_map_context"])
        self.assertGreaterEqual(result["evidence_links"], 1)
        conversation = get_conversation(self.db, result["conversation_id"])
        self.assertEqual([item["role"] for item in conversation["messages"]], ["user", "assistant"])
        self.assertIn("slide", conversation["messages"][-1]["metadata"])
        self.assertEqual(result["answer_slide"]["title"], "영암군 민원 사전점검")
        complaint_pin = next(pin for pin in yeongam_pins(self.db)["pins"] if pin["kind"] == "complaint")
        self.assertEqual(complaint_pin["id"], result["complaint_id"])

    def test_follow_up_keeps_the_same_conversation_and_accepts_image_metadata(self) -> None:
        initial = create_complaint(
            self.db,
            {
                "address": "전라남도 영암군 삼호읍 가상리 45-2",
                "text": "주민 설명회에서 어떤 자료를 준비해야 하나요?",
                "latitude": 34.8,
                "longitude": 126.42,
                "resolve_address": False,
                "include_map_context": False,
            },
        )
        follow_up = continue_conversation(
            self.db,
            initial["conversation_id"],
            {"message": "현장 사진도 같이 볼 수 있나요?", "image": {"media_type": "image/png", "data": "aGVsbG8="}},
        )
        self.assertEqual(follow_up["conversation_id"], initial["conversation_id"])
        self.assertEqual(len(follow_up["messages"]), 4)
        self.assertTrue(follow_up["messages"][-2]["metadata"]["image_received"])
        self.assertIn("slide", follow_up["messages"][-1]["metadata"])

    def test_non_yeongam_complaints_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "영암군"):
            create_complaint(
                self.db,
                {"address": "전라남도 함평군 손불면 가상리 1-1", "text": "다른 지역 민원입니다.", "latitude": 35.1, "longitude": 126.52},
            )

    def test_runtime_status_reports_provider_state_without_credentials(self) -> None:
        status = runtime_status()
        self.assertEqual(status["scope"], "yeongam")
        self.assertEqual(status["answer"]["provider"], "Claude API")
        self.assertTrue(status["map"]["required"])
        self.assertNotIn("api_key", str(status).lower())
