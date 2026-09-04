from lucera.case_library import case_catalog, infer_case_type, resolve_case_type
from lucera.rag import normalize_chat_input


def test_case_catalog_has_five_yeongam_cases() -> None:
    cases = case_catalog()
    assert len(cases) == 5
    assert {item["id"] for item in cases} == {"regulatory", "environment", "community", "trust", "grid"}
    assert all("영암군" in item["address"] and item["text"] for item in cases)


def test_case_classifier_maps_the_five_categories() -> None:
    assert infer_case_type("이격거리와 조례상 허가 기준을 확인해 주세요") == "regulatory"
    assert infer_case_type("배수로 침수와 경관 훼손이 걱정됩니다") == "environment"
    assert infer_case_type("주민 설명회와 의견 수렴 절차가 있었나요") == "community"
    assert infer_case_type("주민참여 수익 배분과 사업자 약속을 확인하고 싶습니다") == "trust"
    assert infer_case_type("변전소 여유 용량과 계통 접속을 확인해 주세요") == "grid"
    assert resolve_case_type("grid", "다른 내용") == "grid"


def test_chat_input_keeps_selected_case_type() -> None:
    data = normalize_chat_input(
        {
            "address": "전라남도 영암군 신북면 예향로 2346",
            "message": "계통 접속 가능 여부를 확인해 주세요.",
            "case_type": "grid",
            "resolve_address": False,
        }
    )
    assert data["case_type"] == "grid"
