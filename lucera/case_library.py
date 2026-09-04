"""Curated Yeongam complaint cases used by the intake UI and classifier."""

from __future__ import annotations

from typing import Any


CASE_LIBRARY: tuple[dict[str, Any], ...] = (
    {
        "id": "regulatory",
        "label": "규제·인허가",
        "share": "48.1%",
        "summary": "이격거리·조례·허가기준을 확인하는 사례",
        "address": "전라남도 영암군 영암읍 군청로 1",
        "title": "주거지와 도로 이격거리 기준이 맞는지 확인해 주세요",
        "text": "영암읍 군청로 인근에 태양광 발전시설 설치를 검토하고 있습니다. 예정 부지가 주거지와 주요도로에서 각각 300m 기준을 충족하는지, 영암군 조례상 개발행위와 인허가 절차에 문제가 없는지 확인해 주세요.",
        "issues": ("siting_permit_regulatory",),
    },
    {
        "id": "environment",
        "label": "입지·환경 훼손",
        "share": "43.5%",
        "summary": "배수·경관·농지 영향을 살펴보는 사례",
        "address": "전라남도 영암군 군서면 왕인로 440",
        "title": "집중호우와 경관·농지 훼손이 걱정됩니다",
        "text": "군서면 왕인로 인근 농지에 태양광 시설이 들어오면 산비탈의 배수로가 막히거나 집중호우 때 토사가 마을 쪽으로 흘러내릴까 걱정됩니다. 월출산 조망과 농지 훼손 가능성까지 함께 검토하고 보완해야 할 사항을 알려주세요.",
        "issues": ("safety_environment", "landscape_damage", "agricultural_land_damage"),
    },
    {
        "id": "community",
        "label": "절차·주민수용성",
        "share": "33.3%",
        "summary": "주민 고지·설명회·협의 절차를 확인하는 사례",
        "address": "전라남도 영암군 삼호읍 대불주거1로 12",
        "title": "주민 설명회와 사전 고지가 제대로 이뤄졌나요?",
        "text": "삼호읍 대불주거1로 인근 주민입니다. 태양광 사업 계획을 뒤늦게 알게 되었고, 마을 주민 설명회나 의견 수렴이 있었는지 확인하기 어렵습니다. 사업자가 어떤 절차로 주민에게 알리고 의견을 반영해야 하는지, 회의록에 확인되는 유사 사례도 알려주세요.",
        "issues": ("communication_procedure",),
    },
    {
        "id": "trust",
        "label": "이익배분·사업 신뢰",
        "share": "27.8%",
        "summary": "주민참여·보상·사업자 약속을 확인하는 사례",
        "address": "전라남도 영암군 학산면 독천로 193",
        "title": "주민참여와 수익 배분 약속을 확인하고 싶습니다",
        "text": "학산면 독천로 인근 태양광 사업에서 주민에게 수익 일부를 나누거나 마을기금을 지원한다는 설명을 들었습니다. 실제 협약이나 주민참여 방식이 있었는지, 사업자가 약속한 차폐·배수 보완을 이행했는지 회의록과 허가 자료를 바탕으로 확인해 주세요.",
        "issues": ("external_benefit_distribution", "communication_procedure"),
    },
    {
        "id": "grid",
        "label": "계통·송전",
        "share": "17.6%",
        "summary": "접속 가능성·송전선로·변전 설비를 확인하는 사례",
        "address": "전라남도 영암군 신북면 예향로 2346",
        "title": "계통 접속과 송전선로 계획을 확인해 주세요",
        "text": "신북면 예향로 인근에 태양광 발전시설을 검토 중인데, 인근 변전소의 여유 용량과 계통 접속 가능 여부를 알기 어렵습니다. 송전선로가 추가로 설치되는지, 주변 사업의 접속 지연이나 보완 요청 사례가 있었는지 확인해 주세요.",
        "issues": ("grid_connection",),
    },
)

CASE_BY_ID = {str(case["id"]): case for case in CASE_LIBRARY}


def case_catalog() -> list[dict[str, Any]]:
    """Return UI-safe case data without exposing internal implementation details."""

    return [
        {
            "id": case["id"],
            "label": case["label"],
            "share": case["share"],
            "summary": case["summary"],
            "address": case["address"],
            "title": case["title"],
            "text": case["text"],
        }
        for case in CASE_LIBRARY
    ]


def get_case(case_id: str | None) -> dict[str, Any] | None:
    return CASE_BY_ID.get(str(case_id or "").strip())


def infer_case_type(text: str) -> str | None:
    """Pick one of the five presentation categories from free-form text."""

    normalized = " ".join(str(text or "").split())
    scores: dict[str, int] = {}
    terms = {
        "regulatory": ("이격거리", "조례", "허가", "인허가", "개발행위", "규제", "기준"),
        "environment": ("배수", "침수", "토사", "경관", "조망", "농지", "훼손", "산비탈"),
        "community": ("주민", "설명회", "고지", "의견", "협의", "동의", "수용성"),
        "trust": ("수익", "배분", "보상", "마을기금", "약속", "사업자", "주민참여"),
        "grid": ("계통", "송전", "변전", "접속", "전력", "여유 용량"),
    }
    for case_id, synonyms in terms.items():
        scores[case_id] = sum(normalized.count(term) for term in synonyms)
    best = max(scores, key=scores.get, default=None)
    return best if best and scores[best] else None


def resolve_case_type(case_id: str | None, text: str) -> str | None:
    if get_case(case_id):
        return str(case_id)
    return infer_case_type(text)
