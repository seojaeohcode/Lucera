from __future__ import annotations

import re
from typing import Any


# The six lists supplied by the team are deliberately not treated as six
# independent OR buckets.  The first list establishes solar context, lists
# 2-5 describe the four user-facing problem categories, and list 6 is
# supporting evidence about an administrative response.
PROBLEM_CATEGORIES: dict[str, dict[str, Any]] = {
    "resident_conflict": {
        "name": "주민·민원·갈등",
        "description": "주민 반대·민원·소통·협의·의견수렴",
    },
    "siting_permit": {
        "name": "입지·토지·인허가",
        "description": "개발행위·이격거리·농지·산지·주거지·허가",
    },
    "impact_environment_safety": {
        "name": "피해·환경·안전",
        "description": "경관·빛반사·소음·환경·생태·재해·안전",
    },
    "benefit_compensation": {
        "name": "수익·보상·지역경제",
        "description": "주민참여·소득·수익·보상·기금·지역경제",
    },
}


# Full vocabulary supplied by the team.  This catalog is for traceable
# keyword_mentions, not for deciding that every occurrence is a dispute.
KEYWORD_GROUPS: dict[str, tuple[str, ...]] = {
    "business_equipment": (
        "태양광", "태양광발전", "태양광발전시설", "태양광발전소", "태양광시설",
        "발전시설", "발전사업", "발전용 태양광", "재생에너지", "신재생에너지",
        "수상태양광", "영농형태양광", "주민참여형 태양광", "학교 태양광", "영구시설물",
        "지붕", "옥상", "댐", "저수지", "간척지", "유휴지",
    ),
    "resident_conflict": (
        "민원", "주민민원", "집단민원", "주민", "마을주민", "주민불편", "주민피해",
        "주민요구", "반대", "설치반대", "결사반대", "반발", "갈등", "분쟁", "피해",
        "보상", "협의", "소통", "주민설명회", "설명회", "공청회", "주민의견",
        "의견수렴", "주민동의", "대책위",
    ),
    "siting_permit": (
        "개발행위", "개발행위허가", "인허가", "허가", "허가처분", "허가취소", "준공",
        "행정행위", "인허가절차", "이격거리", "거리제한", "이격거리완화", "도로",
        "주거지역", "주거밀집지역", "민가", "인가", "마을", "농지", "염해농지",
        "농업진흥구역", "산지", "임야", "토지", "용도지역", "군관리계획", "군계획시설",
    ),
    "impact_environment_safety": (
        "경관", "경관훼손", "미관", "조망권", "빛반사", "반사광", "소음", "전자파",
        "환경", "환경영향", "생태", "생물다양성", "산림훼손", "농업피해", "난개발",
        "토사유출", "토사", "산사태", "침수", "재해", "안전",
    ),
    "benefit_compensation": (
        "주민참여", "주민참여형", "영농형", "소득", "농가소득", "주민소득", "수익",
        "발전수익", "배당", "주민발전기금", "지역발전기금", "보상", "임대", "임대료",
        "세수", "세외수입", "기부채납", "지역경제", "일자리", "햇빛연금", "햇빛소득마을",
        "에너지자립마을",
    ),
    "administrative_support": (
        "조례", "군계획조례", "농지법", "국토계획법", "전기사업법", "특별법", "제도",
        "규제", "규제완화", "행정사무감사", "행정조사", "현장조사", "현장점검", "자료요구",
        "증인", "소송", "법원", "판결", "행정심판", "취소", "시정", "개선", "유보",
        "보류", "촉구", "정보공개",
    ),
}


# Strong anchors identify the subject as solar power.  Generic words such as
# 발전시설 or 재생에너지는 wind, biomass, hydro, and other projects too, so
# they are only contextual anchors when an installation/business word is also
# present in the same segment.
SOLAR_ANCHORS: tuple[str, ...] = (
    "태양광발전시설",
    "태양광발전소",
    "주민참여형 태양광",
    "발전용 태양광",
    "영농형태양광",
    "수상태양광",
    "학교 태양광",
    "태양광발전",
    "태양광시설",
    "태양광",
)
SOLAR_CONTEXT_ANCHORS: tuple[str, ...] = (
    "발전시설",
    "발전사업",
    "재생에너지",
    "신재생에너지",
)
SOLAR_ACTIVATORS: tuple[str, ...] = (
    "설치",
    "사업",
    "발전",
    "허가",
    "인허가",
    "시설",
    "개발",
    "조성",
    "단지",
    "패널",
    "모듈",
)


# These are intentionally small.  They can be used as a high-precision first
# pass even when a meeting paragraph omits the word 태양광.
HIGH_PRECISION_ISSUE_STANDALONE: tuple[str, ...] = (
    "빛반사",
    "반사광",
    "눈부심",
)
HIGH_PRECISION_SOLAR_TOPICS: tuple[str, ...] = (
    "햇빛연금",
    "햇빛소득마을",
)


# Detailed issue labels remain compatible with the existing database
# taxonomy.  Each one is mapped to one of the four problem categories.
ISSUE_RULES: dict[str, dict[str, Any]] = {
    "landscape_damage": {
        "category": "impact_environment_safety",
        "high": ("경관훼손", "산림훼손", "조망권"),
        "context": ("경관", "미관", "조망"),
        "single_context": True,
    },
    "noise_living_discomfort": {
        "category": "impact_environment_safety",
        "high": ("생활불편", "주민불편", "주거환경", "전자파"),
        "context": ("소음", "건강", "생활피해", "주민피해"),
        "single_context": True,
    },
    "agricultural_land_damage": {
        "category": "siting_permit",
        "high": ("염해농지", "농지훼손", "농업피해", "농업진흥구역"),
        "context": ("농지", "농업", "농민", "산지", "임야", "토지", "간척지", "유휴지", "영농"),
        "single_context": True,
        "single_context_terms": ("농지", "농업", "산지", "임야", "영농"),
    },
    "siting_permit_regulatory": {
        "category": "siting_permit",
        "high": (
            "개발행위허가",
            "인허가절차",
            "허가처분",
            "허가취소",
            "이격거리",
            "거리제한",
            "이격거리완화",
            "용도지역",
            "군관리계획",
            "군계획시설",
            "주거밀집지역",
            "주거지역",
            "농지법",
            "국토계획법",
            "전기사업법",
        ),
        "context": ("개발행위", "인허가", "허가", "준공", "행정행위", "도로", "민가", "인가", "마을"),
        "single_context": True,
        "single_context_terms": ("개발행위", "인허가", "허가", "준공", "행정행위"),
    },
    "communication_procedure": {
        "category": "resident_conflict",
        "high": (
            "집단민원",
            "주민민원",
            "주민설명회",
            "주민동의",
            "의견수렴",
            "설치반대",
            "결사반대",
            "주민대책위",
            "대책위",
        ),
        "context": ("민원", "주민", "마을주민", "주민불편", "주민피해", "주민요구", "반대", "반발", "갈등", "분쟁", "협의", "소통", "설명회", "공청회"),
        # 주민 alone is too broad because 주민참여 is often positive.  The
        # other single terms are direct dispute/process signals when solar
        # context is present; 주민/협의/소통 require a companion term.
        "single_context_terms": ("민원", "반대", "반발", "갈등", "분쟁", "주민불편", "주민피해", "주민요구", "설명회", "공청회", "대책위"),
    },
    "glare_reflection": {
        "category": "impact_environment_safety",
        "high": ("빛반사", "반사광", "눈부심"),
        "context": ("반사",),
        "standalone": True,
        "single_context": True,
    },
    "external_benefit_distribution": {
        "category": "benefit_compensation",
        "high": (
            "주민발전기금",
            "지역발전기금",
            "발전수익",
            "햇빛연금",
            "햇빛소득마을",
            "농가소득",
            "주민소득",
        ),
        "context": ("보상", "수익", "소득", "수익배분", "이익공유", "배당", "주민참여", "임대", "임대료", "세수", "세외수입", "기부채납", "지역경제", "일자리"),
        "single_context": True,
        "single_context_terms": ("보상", "수익배분", "이익공유", "배당", "주민참여", "임대료", "세수", "세외수입", "기부채납", "지역경제", "일자리"),
    },
    "safety_environment": {
        "category": "impact_environment_safety",
        "high": ("환경영향", "생물다양성", "산림훼손", "토사유출", "산사태", "침수", "재해"),
        "context": ("환경", "생태", "산림", "토사", "배수", "안전"),
        "single_context": True,
    },
    "grid_connection": {
        # Kept as an operational/system label from the original plan; it is
        # not one of the four resident complaint categories.
        "category": "siting_permit",
        "high": ("계통연계", "출력제어", "변전소", "전력망"),
        "context": ("계통", "접속"),
        "single_context": True,
    },
}


# Generic legal/administrative terms never create a complaint by themselves.
# They become useful evidence only when paired with a solar anchor and one of
# the issue rules above.
ADMIN_SUPPORT_TERMS: tuple[str, ...] = (
    "개발행위허가",
    "인허가절차",
    "허가처분",
    "허가취소",
    "농지법",
    "국토계획법",
    "전기사업법",
    "군계획조례",
    "행정사무감사",
    "행정조사",
    "현장조사",
    "현장점검",
    "자료요구",
    "행정심판",
    "정보공개",
    "소송",
    "판결",
    "시정",
    "보류",
    "촉구",
    "조례",
    "제도",
    "법령",
)


# API collection is performed by query families.  Broad solar queries find
# recall, while the combinations and high-specificity singles find likely
# dispute evidence without making generic words such as 주민 or 환경 stand
# alone queries.
COLLECTION_QUERY_PLAN: tuple[dict[str, str], ...] = (
    {"query": "태양광", "family": "solar_anchor", "precision": "broad"},
    {"query": "태양광발전", "family": "solar_anchor", "precision": "broad"},
    {"query": "수상태양광", "family": "solar_anchor", "precision": "high"},
    {"query": "영농형태양광", "family": "solar_anchor", "precision": "high"},
    {"query": "빛반사", "family": "high_specific_issue", "precision": "high"},
    {"query": "반사광", "family": "high_specific_issue", "precision": "high"},
    {"query": "눈부심", "family": "high_specific_issue", "precision": "high"},
    {"query": "염해농지", "family": "high_specific_issue", "precision": "high"},
    {"query": "이격거리", "family": "high_specific_issue", "precision": "high"},
    {"query": "거리제한", "family": "high_specific_issue", "precision": "high"},
    {"query": "햇빛연금", "family": "high_specific_issue", "precision": "high"},
    {"query": "햇빛소득마을", "family": "high_specific_issue", "precision": "high"},
    {"query": "태양광 민원", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 주민반대", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 집단민원", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 주민설명회", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 주민동의", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 갈등", "family": "resident_conflict", "precision": "high"},
    {"query": "태양광 이격거리", "family": "siting_permit", "precision": "high"},
    {"query": "태양광 개발행위허가", "family": "siting_permit", "precision": "high"},
    {"query": "태양광 염해농지", "family": "siting_permit", "precision": "high"},
    {"query": "태양광 농지", "family": "siting_permit", "precision": "medium"},
    {"query": "태양광 산지", "family": "siting_permit", "precision": "medium"},
    {"query": "태양광 경관", "family": "impact_environment_safety", "precision": "high"},
    {"query": "태양광 빛반사", "family": "impact_environment_safety", "precision": "high"},
    {"query": "태양광 소음", "family": "impact_environment_safety", "precision": "high"},
    {"query": "태양광 환경영향", "family": "impact_environment_safety", "precision": "high"},
    {"query": "태양광 안전", "family": "impact_environment_safety", "precision": "high"},
    {"query": "태양광 보상", "family": "benefit_compensation", "precision": "high"},
    {"query": "태양광 주민소득", "family": "benefit_compensation", "precision": "high"},
    {"query": "태양광 발전수익", "family": "benefit_compensation", "precision": "high"},
    {"query": "태양광 주민발전기금", "family": "benefit_compensation", "precision": "high"},
    {"query": "태양광 조례", "family": "administrative_support", "precision": "medium"},
    {"query": "태양광 행정사무감사", "family": "administrative_support", "precision": "high"},
    {"query": "태양광 허가취소", "family": "administrative_support", "precision": "high"},
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def find_terms(text: str, terms: tuple[str, ...] | list[str]) -> list[str]:
    """Find unique terms, accepting both spaced and compound Korean forms."""
    value = text or ""
    compact = _compact(value)
    hits: list[str] = []
    for term in terms:
        if term in value or _compact(term) in compact:
            hits.append(term)
    return hits


def _term_is_negated(text: str, term: str) -> bool:
    """Reject polarity/issue hits negated by the nearby Korean predicate.

    Keyword matching remains intentionally substring-based for recall, but a
    polarity classifier must not treat ``반대의견이 없으므로`` as opposition.
    This small scope-aware guard handles the common minute-writing forms while
    leaving the original text and keyword offsets untouched.
    """
    value = text or ""
    compact = _compact(value)
    normalized = _compact(term)
    if not normalized:
        return False
    position = compact.find(normalized)
    while position >= 0:
        before = compact[max(0, position - 10):position]
        after = compact[position + len(normalized):position + len(normalized) + 14]
        if re.search(r"(?:없|아니|않|못|무관|제외|불필요)$", before):
            return True
        if re.match(r"(?:의견|것|부분|점|내용)?(?:이|가|은|는)?없", after):
            return True
        if re.match(r"하지않|하지않음|아니(?:다|라)", after):
            return True
        position = compact.find(normalized, position + max(1, len(normalized)))
    return False


def active_term_hits(text: str, terms: tuple[str, ...] | list[str]) -> list[str]:
    """Find terms that are not locally negated in the source sentence."""
    return [term for term in find_terms(text, terms) if not _term_is_negated(text, term)]


def solar_anchor_hits(text: str) -> list[str]:
    strong = active_term_hits(text, SOLAR_ANCHORS)
    if strong:
        return strong
    topic_hits = active_term_hits(text, HIGH_PRECISION_SOLAR_TOPICS)
    if topic_hits:
        return topic_hits
    context_hits = active_term_hits(text, SOLAR_CONTEXT_ANCHORS)
    if context_hits and active_term_hits(text, SOLAR_ACTIVATORS):
        return context_hits
    return []


def _sentence_list(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?다요])\s+|\n+", text or "") if part.strip()]


def split_sentence_spans(text: str) -> list[dict[str, Any]]:
    """Split one stored paragraph while retaining exact source offsets."""
    value = text or ""
    if not value.strip():
        return []
    boundaries = re.compile(r"[.!?]+(?=\s|$)|다(?=\s|$)|요(?=\s|$)|\n+")
    spans: list[dict[str, Any]] = []
    start = 0
    for match in boundaries.finditer(value):
        end = match.end()
        raw = value[start:end]
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        if right > left:
            sentence = re.sub(r"[ \t\r\f\v]+", " ", raw[left:right]).strip()
            if sentence:
                spans.append({"text": sentence, "char_start": start + left, "char_end": start + right})
        start = end
    raw = value[start:]
    left = len(raw) - len(raw.lstrip())
    right = len(raw.rstrip())
    if right > left:
        sentence = re.sub(r"[ \t\r\f\v]+", " ", raw[left:right]).strip()
        if sentence:
            spans.append({"text": sentence, "char_start": start + left, "char_end": start + right})
    return spans


def classify_segment(text: str, context_text: str = "") -> dict[str, Any]:
    """Aggregate sentence classifications without losing sentence evidence.

    Paragraphs are the storage unit, but a whole paragraph is too broad for a
    dispute label.  This helper is used when materializing ``segment_issue``:
    only issue rules reproduced by at least one sentence are retained, and
    the selected evidence sentence is stored in the issue metadata.
    """
    spans = split_sentence_spans(text or "")
    if not spans and (text or "").strip():
        spans = [{"text": (text or "").strip(), "char_start": 0, "char_end": len(text or "")}]
    classifications = [classify_text(span["text"], context_text) for span in spans]
    issues_by_code: dict[str, dict[str, Any]] = {}
    evidence_by_code: dict[str, list[str]] = {}
    for span, classification in zip(spans, classifications):
        for issue in classification.get("issues", []):
            code = str(issue.get("issue_code") or "")
            if not code:
                continue
            evidence_by_code.setdefault(code, []).append(span["text"])
            candidate = dict(issue)
            metadata = dict(candidate.get("metadata") or {})
            metadata["evidence_sentences"] = evidence_by_code[code]
            candidate["metadata"] = metadata
            previous = issues_by_code.get(code)
            if previous is None or float(candidate.get("confidence") or 0) > float(previous.get("confidence") or 0):
                issues_by_code[code] = candidate
            else:
                previous_metadata = dict(previous.get("metadata") or {})
                previous_metadata["evidence_sentences"] = evidence_by_code[code]
                previous["metadata"] = previous_metadata
    return {
        "relevant": any(item.get("relevant") for item in classifications),
        "solar_related": any(item.get("solar_related") for item in classifications),
        "solar_anchor_hits": sorted({hit for item in classifications for hit in item.get("solar_anchor_hits", [])}),
        "standalone_high_precision_hits": sorted({hit for item in classifications for hit in item.get("standalone_high_precision_hits", [])}),
        "matched_issue_terms": sorted({hit for item in classifications for hit in item.get("matched_issue_terms", [])}, key=len, reverse=True),
        "admin_support_hits": sorted({hit for item in classifications for hit in item.get("admin_support_hits", [])}, key=len, reverse=True),
        "problem_categories": sorted({category for item in classifications for category in item.get("problem_categories", [])}),
        "issues": list(issues_by_code.values()),
        "sentence_count": len(spans),
    }


def _compact_with_offsets(text: str) -> tuple[str, list[int]]:
    compact_chars: list[str] = []
    original_offsets: list[int] = []
    for index, char in enumerate(text or ""):
        if char.isspace():
            continue
        compact_chars.append(char)
        original_offsets.append(index)
    return "".join(compact_chars), original_offsets


def keyword_occurrences(text: str, base_offset: int = 0) -> list[dict[str, Any]]:
    """Return traceable vocabulary occurrences with original-text offsets.

    Matching is longest-term aware through metadata, but overlapping terms are
    retained because `태양광발전시설` legitimately contains multiple catalog
    concepts.  Exact and whitespace-normalized matches remain distinguishable.
    """
    value = text or ""
    compact, offsets = _compact_with_offsets(value)
    occurrences: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for group, terms in KEYWORD_GROUPS.items():
        for term in terms:
            normalized = _compact(term)
            if not normalized:
                continue
            cursor = 0
            while True:
                position = compact.find(normalized, cursor)
                if position < 0:
                    break
                compact_end = position + len(normalized)
                if compact_end <= len(offsets):
                    start = offsets[position]
                    end = offsets[compact_end - 1] + 1
                    key = (term, start, end)
                    if key not in seen:
                        if group == "business_equipment":
                            match_type = "solar_anchor" if term in SOLAR_ANCHORS else "solar_context"
                        elif group == "administrative_support":
                            match_type = "admin_support"
                        elif term in HIGH_PRECISION_ISSUE_STANDALONE:
                            match_type = "high_precision_issue"
                        else:
                            match_type = "issue_keyword"
                        occurrences.append(
                            {
                                "keyword": term,
                                "normalized_keyword": normalized,
                                "start_offset": base_offset + start,
                                "end_offset": base_offset + end,
                                "match_type": match_type,
                                "keyword_group": group,
                                "problem_category": group if group in PROBLEM_CATEGORIES else None,
                                "metadata": {"matching": "exact" if term in value else "whitespace_normalized"},
                            }
                        )
                        seen.add(key)
                cursor = position + max(1, len(normalized))
    return sorted(occurrences, key=lambda item: (item["start_offset"], -(item["end_offset"] - item["start_offset"]), item["keyword"]))


def _context_has_solar(text: str, context_text: str) -> tuple[bool, list[str]]:
    direct = solar_anchor_hits(text)
    if direct:
        return True, direct
    context_hits = solar_anchor_hits(context_text)
    return bool(context_hits), context_hits


def _issue_hits(text: str, rule: dict[str, Any]) -> tuple[list[str], list[str]]:
    high = active_term_hits(text, tuple(rule.get("high", ())))
    context = active_term_hits(text, tuple(rule.get("context", ())))
    return high, context


def _should_emit_issue(code: str, text: str, has_solar: bool) -> tuple[bool, list[str], str]:
    rule = ISSUE_RULES[code]
    high, context = _issue_hits(text, rule)

    if code == "glare_reflection" and high:
        # Generic road/building reflection is excluded, but the supplied
        # domain term 빛반사/반사광/눈부심 is accepted as a high-precision
        # solar complaint candidate even when the paragraph omits 태양광.
        if not any(excluded in text for excluded in ("반사경", "후사경", "도로반사", "차량반사")):
            return True, high, "standalone_high_precision"

    if not has_solar:
        return False, [], "no_solar_context"

    if code == "safety_environment":
        # ``안전한 보행 환경 조성`` and ``환경 개선`` are usually public
        # programs, not evidence of a solar dispute.  Keep this category only
        # when a high-specificity impact or an explicit harm/risk expression
        # occurs in the same sentence.
        harm_hits = active_term_hits(
            text,
            ("훼손", "오염", "피해", "우려", "위험", "사고", "문제", "토사", "산림", "전자파", "침수", "산사태"),
        )
        if not high and not harm_hits:
            return False, [], "benign_environment_or_safety_context"
        return True, high or harm_hits, "solar_plus_environmental_harm"

    if high:
        return True, high, "solar_plus_high_issue"
    if not context:
        return False, [], "no_issue_term"
    if rule.get("single_context"):
        allowed = set(rule.get("single_context_terms", context))
        if any(hit in allowed for hit in context):
            return True, context, "solar_plus_issue"
    if len(set(context)) >= 2:
        return True, context, "solar_plus_compound_issue"
    return False, [], "generic_term_alone"


def classify_text(text: str, context_text: str = "") -> dict[str, Any]:
    """Return a high-precision solar/dispute classification for one segment.

    `context_text` should normally be the meeting title and agenda, not the
    entire document.  This permits a paragraph such as “주민들은 반대했다”
    under an agenda titled “태양광 주민 설명회” to be connected without
    allowing an unrelated solar paragraph elsewhere in a long document to
    contaminate every segment.
    """
    value = text or ""
    standalone_hits = active_term_hits(value, HIGH_PRECISION_ISSUE_STANDALONE)
    direct_solar_hits = solar_anchor_hits(value)
    context_solar_hits = [] if direct_solar_hits else solar_anchor_hits(context_text)
    solar_topic_hits = find_terms(value, HIGH_PRECISION_SOLAR_TOPICS)
    has_solar = bool(direct_solar_hits or context_solar_hits or solar_topic_hits)
    negative = active_term_hits(value, ("결사반대", "설치반대", "반대", "반발", "피해", "우려", "갈등", "분쟁", "훼손", "문제", "불편", "소송", "취소", "요구"))
    positive = active_term_hits(value, ("찬성", "동의", "수용", "협력", "지원", "환영", "상생", "주민참여", "소득", "수익"))
    sentences = _sentence_list(value)
    issues: list[dict[str, Any]] = []
    category_hits: set[str] = set()
    matched_issue_terms: list[str] = []

    for code in ISSUE_RULES:
        emit, hits, reason = _should_emit_issue(code, value, has_solar)
        if not emit:
            continue
        rule = ISSUE_RULES[code]
        polarity = "mixed" if negative and positive else "opposition" if negative else "support" if positive else "neutral"
        category = rule["category"]
        category_hits.add(category)
        matched_issue_terms.extend(hits)
        issues.append(
            {
                "issue_code": code,
                "problem_category": category,
                "polarity": polarity,
                "target_type": "project" if direct_solar_hits or context_solar_hits or solar_topic_hits or standalone_hits else "unknown",
                "confidence": min(0.98, 0.82 + 0.03 * min(len(set(hits)), 4)) if reason != "solar_plus_issue" else 0.79,
                "evidence_span": next((sentence.strip() for sentence in sentences if any(hit in sentence or _compact(hit) in _compact(sentence) for hit in hits)), value[:300]),
                "metadata": {
                    "matched_keywords": hits,
                    "solar_anchor_hits": direct_solar_hits or context_solar_hits or solar_topic_hits,
                    "rule_id": f"keyword-precision-v2:{reason}",
                    "problem_category": category,
                },
            }
        )

    admin_hits = find_terms(value, ADMIN_SUPPORT_TERMS)
    # Admin terms are evidence, not an independent complaint signal.
    has_solar_context = bool(direct_solar_hits or context_solar_hits or solar_topic_hits)
    complaint_context = active_term_hits(value, ("민원", "반대", "반발", "갈등", "분쟁", "피해", "보상", "주민요구", "대책위", "설명회", "협의"))
    relevant = bool(issues or standalone_hits or solar_topic_hits or direct_solar_hits or (has_solar_context and complaint_context))
    return {
        "relevant": relevant,
        "solar_related": bool(direct_solar_hits or context_solar_hits or solar_topic_hits or standalone_hits),
        "solar_anchor_hits": direct_solar_hits or context_solar_hits or solar_topic_hits,
        "standalone_high_precision_hits": standalone_hits,
        "matched_issue_terms": sorted(set(matched_issue_terms), key=len, reverse=True),
        "admin_support_hits": admin_hits if has_solar_context and (issues or complaint_context) else [],
        "problem_categories": sorted(category_hits),
        "issues": issues,
    }


def collection_query_plan() -> list[dict[str, str]]:
    return [dict(item) for item in COLLECTION_QUERY_PLAN]
