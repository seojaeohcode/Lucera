from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lucera.answer import (
    ClaudeAnswerGenerator,
    _collect_allowed_numbers,
    _extract_json,
    _unverified_numbers,
    build_prompt_pack,
)
from lucera.db import LuceraDB
from lucera.ordinance import seed_official_rules
from lucera.vworld import VWorldClient
from lucera.process import extract_process_events
from lucera.rag import (
    LocalAnswerGenerator,
    RAGService,
    _is_addressable,
    _map_context_checks,
    normalize_chat_input,
)
from lucera.synthetic import seed_synthetic


class _OfflineVWorld:
    """Stands in for VWorldClient so no test reaches the network."""

    enabled = False

    def site_context(self, latitude: float, longitude: float, pnu: str | None = None) -> dict:
        raise AssertionError("site_context must not be called when disabled")


def _service(db) -> RAGService:
    return RAGService(db, LocalAnswerGenerator(), vworld_client=_OfflineVWorld())


class RagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = LuceraDB(Path(self.temp.name) / "rag.sqlite3")
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_chat_input_extracts_address_area_and_capacity(self) -> None:
        data = normalize_chat_input(
            {
                "message": "전라남도 함평군 손불면 부지 6,200㎡, 설치면적 4,500㎡, 설비용량 480kW",
            }
        )
        self.assertEqual(data["address"], "전라남도 함평군 손불면")
        self.assertEqual(data["site_area_sqm"], 6200)
        self.assertEqual(data["installation_area_sqm"], 4500)
        self.assertEqual(data["capacity_kw"], 480)

    def test_process_extraction_keeps_action_and_uncertainty(self) -> None:
        events = extract_process_events(
            "민원이 접수되었다. 담당 부서는 현장 조사를 진행하기로 했다. 주민 설명회 이후 차폐시설 조치를 완료했다.",
            meeting_date="2025-04-12",
            speaker_name="이담당",
            speaker_role="과장",
        )
        self.assertEqual([event["event_type"] for event in events], [
            "complaint_received",
            "investigation_or_review",
            "resident_consultation",
            "mitigation_or_action",
        ])
        self.assertEqual(events[1]["outcome"], "planned")
        self.assertEqual(events[3]["outcome"], "completed")
        self.assertTrue(all(event["extraction_method"] == "deterministic_process_v1" for event in events))

    def test_synthetic_rag_returns_rules_evidence_process_and_permits(self) -> None:
        seeded = seed_synthetic(self.db)
        self.db.commit()
        self.assertEqual(seeded["documents"], 3)
        result = _service(self.db).analyze(
            {
                "address": "전라남도 함평군 손불면",
                "latitude": 35.10,
                "longitude": 126.52,
                "site_area_sqm": 6200,
                "installation_area_sqm": 4500,
                "capacity_kw": 480,
                "nearest_residence_m": 150,
                "nearest_road_m": 120,
                "resolve_address": False,
                "review_mode": "all",
            }
        )
        self.assertEqual(result["analysis"]["conclusion"], "review_required")
        self.assertGreaterEqual(len(result["retrieval"]["evidence"]), 1)
        self.assertGreaterEqual(len(result["analysis"]["timeline"]), 1)
        self.assertEqual(result["analysis"]["permit_analysis"]["count"], 2)
        self.assertTrue(all(item["source"]["data_origin"] == "synthetic" for item in result["analysis"]["rule_analysis"]["checks"] if item["rule_id"].startswith("synthetic-")))
        self.assertTrue(result["grounding"]["citation_required"])
        self.assertIn("빛반사", result["answer"])


class SitingRuleTests(unittest.TestCase):
    """The national cap bounds an ordinance; it is not itself a site test."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = LuceraDB(Path(self.temp.name) / "rules.sqlite3")
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")
        seed_official_rules(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def _checks(self, residence_m: float | None, as_of: str = "2026-10-01") -> dict[str, dict]:
        result = _service(self.db).analyze(
            {
                "address": "전라남도 영암군 삼호읍",
                "site_area_sqm": 13000,
                "installation_area_sqm": 9000,
                "capacity_kw": 900,
                "nearest_residence_m": residence_m,
                "as_of": as_of,
                "review_mode": "all",
            }
        )
        return {check["rule_id"]: check for check in result["analysis"]["rule_analysis"]["checks"]}

    def test_distance_beyond_the_cap_cannot_be_refused_on_setback(self) -> None:
        check = self._checks(250)["national-cap-2026-solar-residence"]
        self.assertEqual(check["status"], "pass")

    def test_distance_inside_the_cap_needs_the_local_article(self) -> None:
        check = self._checks(150)["national-cap-2026-solar-residence"]
        # Not "fail": the ordinance may require less than the ceiling.
        self.assertEqual(check["status"], "check_required")

    def test_cap_before_its_effective_date_says_so(self) -> None:
        check = self._checks(250, as_of="2026-09-01")["national-cap-2026-solar-residence"]
        self.assertIn("2026-09-18", check["reason"])

    def test_missing_local_ordinance_is_reported_not_assumed(self) -> None:
        checks = self._checks(150)
        self.assertIn("ordinance-not-loaded", checks)
        self.assertEqual(checks["ordinance-not-loaded"]["status"], "check_required")

    def test_area_inputs_produce_separate_site_and_installation_checks(self) -> None:
        checks = self._checks(150)
        self.assertEqual(checks["site-coverage-ratio"]["observed_value"], 0.692)
        self.assertEqual(checks["site-area-per-kw-advisory"]["observed_value"], 14.44)


class AnswerGuardTests(unittest.TestCase):
    """The model may only re-state the pack; both guards fail closed."""

    class _Fallback:
        def generate(self, pack: dict) -> str:
            return "FALLBACK"

    def _pack(self) -> dict:
        return {
            "input": {"address": "전라남도 영암군 삼호읍", "nearest_residence_m": 150.0},
            "location": {"city_county": "영암군"},
            "analysis": {
                "conclusion_label": "조건부 검토",
                "rule_analysis": {"checks": [{"rule_id": "r1", "rule_name": "규칙", "status": "pass", "reason": "충족"}]},
                "reason_cards": [],
                "timeline": [],
                "permit_analysis": {"count": 466, "projects": []},
                "limitations": [],
            },
            "grounding": {"evidence_ids": ["ev-1"], "rule_ids": ["r1"], "process_event_ids": [], "permit_project_ids": []},
        }

    def _generator(self) -> ClaudeAnswerGenerator:
        return ClaudeAnswerGenerator(self._Fallback(), api_key="test-key")

    def test_missing_key_falls_back_without_calling_the_api(self) -> None:
        generator = ClaudeAnswerGenerator(self._Fallback(), api_key="")
        self.assertFalse(generator.enabled)
        self.assertEqual(generator.generate(self._pack()), "FALLBACK")

    def test_unknown_citation_is_rejected(self) -> None:
        pack = self._pack()
        problems = self._generator()._validate(
            {
                "conclusion_sentence": "조건부 검토",
                "reasons": [{"title": "t", "body": "b", "evidence_ids": ["communication_procedure"]}],
            },
            build_prompt_pack(pack),
            pack,
        )
        self.assertTrue(any(problem.startswith("unknown_evidence_id") for problem in problems))

    def test_number_absent_from_the_pack_is_rejected(self) -> None:
        pack = self._pack()
        problems = self._generator()._validate(
            {
                "conclusion_sentence": "조건부 검토",
                "reasons": [{"title": "t", "body": "주거지까지 87m입니다.", "evidence_ids": ["r1"]}],
            },
            build_prompt_pack(pack),
            pack,
        )
        self.assertTrue(any(problem.startswith("unverified_number") for problem in problems))

    def test_number_present_in_the_pack_is_accepted(self) -> None:
        pack = self._pack()
        problems = self._generator()._validate(
            {
                "conclusion_sentence": "조건부 검토",
                "reasons": [{"title": "t", "body": "주거지까지 150m입니다.", "evidence_ids": ["r1"]}],
            },
            build_prompt_pack(pack),
            pack,
        )
        self.assertEqual(problems, [])

    def test_identifier_digits_do_not_widen_the_number_whitelist(self) -> None:
        allowed: set[str] = set()
        _collect_allowed_numbers({"evidence_id": "ev-9a3f2210", "quote": "주거지 150m"}, allowed)
        self.assertEqual(_unverified_numbers("150m", allowed), [])
        self.assertEqual(_unverified_numbers("2210건", allowed), ["2210"])

    def test_json_survives_fences_and_a_preamble(self) -> None:
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(_extract_json('설명입니다.\n{"a": {"b": "}"}}\n끝'), {"a": {"b": "}"}})
        self.assertIsNone(_extract_json("json이 없습니다"))


class MapContextTests(unittest.TestCase):
    """Imagery is optional, and never invents a coordinate for a vague address."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = LuceraDB(Path(self.temp.name) / "map.sqlite3")
        self.db.initialize(Path(__file__).parents[1] / "db" / "schema.sql")

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_only_a_parcel_level_address_is_geocoded(self) -> None:
        self.assertFalse(_is_addressable("전라남도 영암군 삼호읍"))
        self.assertFalse(_is_addressable("손불면 가상리"))
        self.assertTrue(_is_addressable("전라남도 영암군 삼호읍 산호리 45-2"))
        self.assertTrue(_is_addressable("전남 영암군 삼호읍 대불로 100"))

    def test_missing_key_disables_imagery_without_failing(self) -> None:
        result = _service(self.db).analyze({"address": "전라남도 영암군 삼호읍", "review_mode": "all"})
        self.assertFalse(result["map_context"]["requested"])
        self.assertEqual(result["map_context"]["reason"], "vworld_key_missing")

    def test_request_can_switch_imagery_off(self) -> None:
        result = _service(self.db).analyze(
            {"address": "전라남도 영암군 삼호읍 산호리 1", "include_map_context": False, "review_mode": "all"}
        )
        self.assertEqual(result["map_context"]["reason"], "disabled_by_request")

    def test_vague_address_is_not_geocoded_even_with_a_key(self) -> None:
        class _Enabled(_OfflineVWorld):
            enabled = True

            def geocode_any(self, address: str) -> dict:
                raise AssertionError("a township-level address must not be geocoded")

        result = RAGService(self.db, LocalAnswerGenerator(), vworld_client=_Enabled()).analyze(
            {"address": "전라남도 영암군 삼호읍", "review_mode": "all"}
        )
        self.assertEqual(result["map_context"]["reason"], "address_not_specific_enough")

    def test_successful_geocode_upgrades_the_location(self) -> None:
        class _Fake(_OfflineVWorld):
            enabled = True

            def geocode_any(self, address: str) -> dict:
                return {"status": "OK", "latitude": 34.80, "longitude": 126.42, "address_type": "parcel"}

            def site_context(self, latitude: float, longitude: float, pnu: str | None = None) -> dict:
                return {
                    "images": [{"cache_key": "abc123", "path": "/tmp/abc123.png", "kind": "aerial_close",
                                "label": "항공영상 (근접)", "approx_extent_m": 706, "media_type": "image/png"}],
                    "layers": [{"data": "LT_C_UQ111", "label": "용도지역(부지)", "scope": "site", "status": "OK",
                                "count": 1, "features": [{"uname": "계획관리지역"}]}],
                    "parcel": None,
                    "errors": [],
                }

        result = RAGService(self.db, LocalAnswerGenerator(), vworld_client=_Fake()).analyze(
            {"address": "전라남도 영암군 삼호읍 산호리 45-2", "review_mode": "all"}
        )
        self.assertEqual(result["location"]["provider"], "vworld")
        self.assertEqual(result["location"]["precision"], "jibun_address")
        self.assertEqual(result["input"]["latitude"], 34.80)
        self.assertEqual(result["map_context"]["images"][0]["url"], "/v1/map-image/abc123")
        # The server path is an implementation detail and must not leave the process.
        self.assertNotIn("path", result["map_context"]["images"][0])


class MapObservationGuardTests(unittest.TestCase):
    """An aerial image supports description, never measurement."""

    class _Fallback:
        def generate(self, pack: dict) -> str:
            return "FALLBACK"

    def _pack(self) -> dict:
        return {
            "input": {"address": "전라남도 영암군 삼호읍 산호리 45-2"},
            "location": {"city_county": "영암군"},
            "map_context": {"requested": True, "images": [], "layers": [], "errors": []},
            "analysis": {
                "conclusion_label": "조건부 검토",
                "rule_analysis": {"checks": [{"rule_id": "r1", "rule_name": "규칙", "status": "pass", "reason": "충족"}]},
                "reason_cards": [], "timeline": [], "permit_analysis": {"count": 0, "projects": []}, "limitations": [],
            },
            "grounding": {"evidence_ids": [], "rule_ids": ["r1"], "process_event_ids": [], "permit_project_ids": []},
        }

    def _validate(self, structured: dict) -> list[str]:
        pack = self._pack()
        generator = ClaudeAnswerGenerator(self._Fallback(), api_key="test-key")
        return generator._validate(structured, build_prompt_pack(pack), pack)

    def _structured(self, observations: list) -> dict:
        return {
            "conclusion_sentence": "조건부 검토",
            "reasons": [{"title": "t", "body": "충족", "evidence_ids": ["r1"]}],
            "map_observations": observations,
        }

    def test_qualitative_observation_is_accepted(self) -> None:
        problems = self._validate(self._structured([
            {"observation": "북측에 주택이 모여 있는 것으로 보입니다.", "relevance": "빛반사 영향 검토 대상"}
        ]))
        self.assertEqual(problems, [])

    def test_distance_read_off_the_image_is_rejected(self) -> None:
        problems = self._validate(self._structured([
            {"observation": "북측 약 100m 지점에 주택이 있습니다.", "relevance": "이격거리 검토"}
        ]))
        self.assertTrue(any(problem.startswith("measurement_in_map_observation") for problem in problems))

    def test_counting_from_the_image_is_rejected(self) -> None:
        problems = self._validate(self._structured([
            {"observation": "주변에 주택이 모여 있습니다.", "relevance": "3가구가 인접"}
        ]))
        self.assertTrue(any(problem.startswith("measurement_in_map_observation") for problem in problems))


class VWorldGeocodeTests(unittest.TestCase):
    """Parsing is pinned to a real response, recorded 2026-09-04.

    Two things in that response would silently corrupt the location if the
    structure levels were trusted: level3 is empty while level4L holds the
    township, and level1 is the post-merge 전남광주통합특별시 rather than a name
    the database is keyed on.
    """

    RECORDED = {
        "response": {
            "service": {"name": "address", "version": "2.0", "operation": "getcoord", "time": "13(ms)"},
            "status": "OK",
            "input": {"type": "parcel", "address": "전라남도 영암군 삼호읍 산호리 1"},
            "refined": {
                "text": "전남광주통합특별시 영암군 삼호읍 산호리 1",
                "structure": {
                    "level0": "대한민국",
                    "level1": "전남광주통합특별시",
                    "level2": "영암군",
                    "level3": "",
                    "level4L": "삼호읍",
                    "level4LC": "1280025325100010000",
                    "level4A": "",
                    "level4AC": "",
                    "level5": "1",
                    "detail": "",
                },
            },
            "result": {"crs": "EPSG:4326", "point": {"x": "126.49529323315639", "y": "34.746763729040026"}},
        }
    }

    def _client(self) -> VWorldClient:
        client = VWorldClient(api_key="test-key")
        client._get_json = lambda endpoint, params: self.RECORDED  # type: ignore[method-assign]
        return client

    def test_coordinates_are_read_from_the_result(self) -> None:
        result = self._client().geocode("전라남도 영암군 삼호읍 산호리 1")
        self.assertEqual(result["status"], "OK")
        self.assertAlmostEqual(result["longitude"], 126.49529323315639)
        self.assertAlmostEqual(result["latitude"], 34.746763729040026)

    def test_township_is_not_mistaken_for_a_ri(self) -> None:
        result = self._client().geocode("전라남도 영암군 삼호읍 산호리 1")
        self.assertEqual(result["eup_myeon"], "삼호읍")
        self.assertEqual(result["ri"], "산호리")

    def test_merged_province_maps_back_to_the_stored_name(self) -> None:
        result = self._client().geocode("전라남도 영암군 삼호읍 산호리 1")
        # The database and every meeting record predate the 2026-07-01 merge.
        self.assertEqual(result["province"], "전라남도")
        self.assertEqual(result["city_county"], "영암군")
        self.assertEqual(result["administrative_name"], "전남광주통합특별시")

    def test_pnu_is_kept_for_a_cadastral_lookup(self) -> None:
        self.assertEqual(self._client().geocode("전라남도 영암군 삼호읍 산호리 1")["pnu"], "1280025325100010000")


class CadastreAndZoningRuleTests(unittest.TestCase):
    """Checks built from live VWorld responses recorded 2026-09-04."""

    # 영암군 삼호읍 산호리 1 — LP_PA_CBND_BUBUN, one parcel by PNU.
    PARCEL = {
        "gosi_year": "2025", "pnu": "1280025325100010000", "jibun": "1 답", "bonbun": "1답",
        "addr": "전남광주통합특별시 영암군 삼호읍 산호리 1", "gosi_month": "01", "jiga": "79600",
    }
    # LT_C_UQ111 within 300m of the same point.
    NEARBY = [
        {"uname": ""}, {"uname": "생산녹지지역"}, {"uname": "자연녹지지역"},
        {"uname": "보전녹지지역"}, {"uname": "제1종일반주거지역"},
    ]

    def _checks(self, **context) -> dict[str, dict]:
        base = {"requested": True, "images": [], "layers": [], "parcel": None, "errors": []}
        return {check["rule_id"]: check for check in _map_context_checks({**base, **context})}

    def test_farmland_category_triggers_a_conversion_check(self) -> None:
        check = self._checks(parcel=self.PARCEL)["cadastre-land-category"]
        self.assertEqual(check["observed_value"], "답")
        self.assertEqual(check["status"], "check_required")
        self.assertIn("농지법", check["reason"])

    def test_non_farmland_category_passes(self) -> None:
        check = self._checks(parcel={**self.PARCEL, "jibun": "1 대", "bonbun": "1대"})["cadastre-land-category"]
        self.assertEqual(check["observed_value"], "대")
        self.assertEqual(check["status"], "pass")

    def test_forest_category_points_at_the_other_statute(self) -> None:
        check = self._checks(parcel={**self.PARCEL, "jibun": "산 1 임야", "bonbun": ""})["cadastre-land-category"]
        self.assertEqual(check["observed_value"], "임야")
        self.assertIn("산지관리법", check["reason"])

    def test_nearby_residential_zone_is_flagged(self) -> None:
        check = self._checks(
            layers=[{"scope": "nearby", "features": self.NEARBY}]
        )["zoning-nearby-residential"]
        self.assertEqual(check["status"], "check_required")
        self.assertIn("제1종일반주거지역", check["observed_value"])

    def test_absence_of_a_residential_zone_is_not_a_clearance(self) -> None:
        check = self._checks(
            layers=[{"scope": "nearby", "features": [{"uname": "생산녹지지역"}]}]
        )["zoning-nearby-residential"]
        self.assertEqual(check["status"], "pass")
        # A single house outside a zoned residential area is invisible here.
        self.assertIn("개별 주택은 이 도면에 나타나지 않으므로", check["reason"])

    def test_restrictive_site_zone_needs_review(self) -> None:
        check = self._checks(layers=[{"scope": "site", "features": [{"uname": "보전녹지지역"}]}])["zoning-site"]
        self.assertEqual(check["status"], "check_required")

    def test_site_and_nearby_zoning_are_not_conflated(self) -> None:
        checks = self._checks(layers=[
            {"scope": "site", "features": [{"uname": "생산녹지지역"}]},
            {"scope": "nearby", "features": self.NEARBY},
        ])
        self.assertEqual(checks["zoning-site"]["observed_value"], "생산녹지지역")
        self.assertEqual(checks["zoning-site"]["status"], "pass")
        self.assertEqual(checks["zoning-nearby-residential"]["status"], "check_required")

    def test_empty_zone_names_are_dropped(self) -> None:
        check = self._checks(layers=[{"scope": "nearby", "features": self.NEARBY}])["zoning-nearby-residential"]
        self.assertNotIn(", ,", check["observed_value"])


if __name__ == "__main__":
    unittest.main()
