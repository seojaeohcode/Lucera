from __future__ import annotations

import math
import json
import re
from collections import defaultdict
from datetime import date
from typing import Any

from .db import LuceraDB
from .location import JusoClient, Location, normalize_address
from .yeongam import scope_city_county


# Search defaults are intentionally precision-oriented.  Generic terms such as
# 주민, 환경, 협의, and 안전 are only useful after solar context is known and
# must not independently pull unrelated council records.
DEFAULT_KEYWORDS = (
    "태양광",
    "태양광발전",
    "태양광발전시설",
    "수상태양광",
    "영농형태양광",
    "빛반사",
    "반사광",
    "염해농지",
    "이격거리",
    "햇빛연금",
    "주민민원",
    "집단민원",
    "주민반대",
)
LOCAL_GROUPS = {"exact_site", "nearby", "same_village", "same_ri", "same_admin_area"}
GROUP_RANK = {
    "exact_site": 5,
    "nearby": 4,
    "same_village": 3,
    "same_ri": 3,
    "same_admin_area": 2,
    "comparative_case": 1,
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1, math.sqrt(a)))


def _date_recency(value: str | None) -> float:
    if not value:
        return 0.1
    try:
        days = max(0, (date.today() - date.fromisoformat(value)).days)
    except ValueError:
        return 0.1
    return max(0.1, 1.0 - min(days, 3650) / 3650)


def _same(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _snippet(text: str, keywords: list[str], max_chars: int = 420) -> str:
    value = " ".join((text or "").split())
    if len(value) <= max_chars:
        return value
    positions = [value.find(keyword) for keyword in keywords if value.find(keyword) >= 0]
    start = max(0, (min(positions) if positions else 0) - 100)
    snippet = value[start : start + max_chars]
    return ("..." if start else "") + snippet + ("..." if start + max_chars < len(value) else "")


GENERIC_EVIDENCE_TERMS = frozenset({"태양광", "민원", "허가", "설치", "검토", "사업"})
ISSUE_SPECIFIC_TERMS = {
    "siting_permit_regulatory": ("인허가", "개발행위", "이격거리", "규제"),
    "communication_procedure": ("주민", "설명회", "협의", "동의", "고지", "의견수렴", "공청회"),
    "safety_environment": ("배수", "침수", "토사", "사면", "산사태", "재해", "안전"),
    "landscape_damage": ("경관", "조망", "차폐", "훼손"),
    "agricultural_land_damage": ("농지", "간척지", "농업", "경작", "토지"),
    "glare_reflection": ("빛반사", "반사광", "눈부심"),
    "noise_living_discomfort": ("소음", "생활불편", "진동"),
    "external_benefit_distribution": ("보상", "수익배분", "주민참여", "마을기금", "협약"),
    "grid_connection": ("계통", "접속", "송전", "변전", "선로", "용량"),
}


def _issue_anchor_hits(issue_codes: list[str], text: str, row_issues: list[dict[str, Any]]) -> list[str]:
    """Return issue-specific words actually present in this paragraph."""

    haystack = " ".join(
        [text]
        + [str(item.get("evidence_span") or "") for item in row_issues if item.get("issue_code") in issue_codes]
    )
    anchors: list[str] = []
    for code in issue_codes:
        for term in ISSUE_SPECIFIC_TERMS.get(code, ()):
            if term in haystack:
                anchors.append(term)
    return list(dict.fromkeys(anchors))


def _contextual_snippet(text: str, keywords: list[str], max_chars: int = 360) -> str:
    """Extract a short, query-focused passage from one minutes segment.

    A minutes segment can contain several answers or a long OCR paragraph. The
    UI should cite the sentence that matches the current issue, with at most
    one adjacent sentence for context, rather than forwarding the whole
    segment and making an unrelated sentence look like evidence.
    """

    value = " ".join((text or "").split())
    if not value:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", value) if part.strip()]
    if len(sentences) <= 1:
        return _snippet(value, keywords, max_chars)

    terms = list(dict.fromkeys(term.strip() for term in keywords if term and term.strip()))
    specific_terms = [term for term in terms if term not in GENERIC_EVIDENCE_TERMS]
    scoring_terms = specific_terms or terms
    matches: list[tuple[int, int]] = []
    for index, sentence in enumerate(sentences):
        score = sum(3 if term in sentence else 0 for term in scoring_terms)
        score += sum(1 for term in terms if term in sentence)
        if score:
            matches.append((index, score))
    if not matches:
        return _snippet(value, terms, max_chars)

    best_index, _ = max(matches, key=lambda item: (item[1], -item[0]))
    candidates: list[tuple[int, int]] = [(best_index, best_index)]
    for neighbor in (best_index - 1, best_index + 1):
        if not 0 <= neighbor < len(sentences):
            continue
        start, end = sorted((best_index, neighbor))
        if len(" ".join(sentences[start : end + 1])) <= max_chars:
            candidates.append((start, end))
    start, end = max(
        candidates,
        key=lambda bounds: (
            sum(score for index, score in matches if bounds[0] <= index <= bounds[1]),
            -len(" ".join(sentences[bounds[0] : bounds[1] + 1])),
        ),
    )
    result = " ".join(sentences[start : end + 1])
    if start:
        result = "…" + result
    if end < len(sentences) - 1:
        result += "…"
    return result if len(result) <= max_chars else _snippet(result, scoring_terms, max_chars)


def _dedupe_and_diversify(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Remove mirrored source paragraphs and cap one meeting's dominance."""

    unique: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    meeting_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    deferred: list[dict[str, Any]] = []
    for item in results:
        text_key = " ".join(str(item.get("evidence_text") or "").split())
        if text_key and text_key in seen_text:
            continue
        if text_key:
            seen_text.add(text_key)
        meeting_key = (
            str(item.get("meeting_date") or ""),
            " ".join(str(item.get("meeting_title") or item.get("title") or "").split()),
        )
        if meeting_counts[meeting_key] >= 3:
            deferred.append(item)
            continue
        meeting_counts[meeting_key] += 1
        unique.append(item)
        if len(unique) >= limit:
            return unique

    # If the corpus has fewer than three distinct paragraphs per meeting, use
    # any remaining non-duplicate records to fill the requested limit.
    for item in deferred:
        if len(unique) >= limit:
            break
        unique.append(item)
    return unique


class SearchService:
    def __init__(self, db: LuceraDB, geocoder: JusoClient | None = None):
        self.db = db
        self.geocoder = geocoder or JusoClient()

    def resolve_location(self, raw_address: str, payload: dict[str, Any]) -> tuple[Location, str, dict[str, Any] | None]:
        if payload.get("latitude") is not None and payload.get("longitude") is not None:
            location = normalize_address(raw_address)
            location.latitude = float(payload["latitude"])
            location.longitude = float(payload["longitude"])
            location.precision = "road_address" if location.precision == "unknown" else location.precision
            location.provider = "user_input"
            location.confidence = max(location.confidence, 0.75)
            location.status = "resolved_by_user"
            return location, "user_input", None
        # An 읍/면/리-only input must not be geocoded to an arbitrary first building.
        has_parcel_number = bool(re.search(r"\d+(?:-\d+)?\s*(?:번지|번)?\s*$", raw_address))
        has_road_number = bool(re.search(r"(?:로|길)\s*\d+", raw_address))
        should_geocode = payload.get("resolve_address", True) and (has_parcel_number or has_road_number)
        if not should_geocode:
            return normalize_address(raw_address), "parsed_admin_area", None
        location, response = self.geocoder.resolve(raw_address)
        selected_place_id = None
        if response.get("candidates"):
            candidate = response["candidates"][0]
            selected_place_id = self.db.upsert_canonical_place(
                {
                    **candidate,
                    "raw_name": raw_address,
                    "normalized_name": location.normalized_address,
                    "place_type": candidate.get("precision", "road_address"),
                    "geo_provider": self.geocoder.provider,
                    "geo_precision": candidate.get("precision", "road_address"),
                    "geocode_confidence": location.confidence,
                    "location_status": "reviewed" if location.status == "resolved" else "candidate",
                    "resolution_method": self.geocoder.provider,
                }
            )
        self.db.record_address_lookup(
            {
                "raw_query": raw_address,
                "normalized_query": location.normalized_address,
                "provider": self.geocoder.provider,
                "response_status": response.get("status"),
                "response_json": response,
                "candidate_count": len(response.get("candidates", [])),
                "selected_place_id": selected_place_id,
                "resolution_status": location.status,
            }
        )
        return location, "juso" if location.status == "resolved" else location.status, response

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_address = " ".join(str(payload.get("address") or "").split())
        if not raw_address:
            raise ValueError("address is required")
        radius_m = float(payload.get("radius_m", 5_000))
        if radius_m <= 0 or radius_m > 50_000:
            raise ValueError("radius_m must be between 1 and 50000")
        limit = int(payload.get("limit", 20))
        if limit <= 0 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        case_paragraph_limit = int(payload.get("case_paragraph_limit", 100))
        if case_paragraph_limit < 0 or case_paragraph_limit > 1000:
            raise ValueError("case_paragraph_limit must be between 0 and 1000")
        review_mode = str(payload.get("review_mode", "eligible")).strip().lower()
        if review_mode not in {"eligible", "all", "needs_review"}:
            raise ValueError("review_mode must be eligible, needs_review, or all")
        keywords = [str(k).strip() for k in payload.get("keywords", []) if str(k).strip()]
        if not keywords:
            keywords = list(DEFAULT_KEYWORDS)
        issue_codes = [str(k).strip() for k in payload.get("issue_codes", []) if str(k).strip()]
        scope_county = scope_city_county(payload["scope"]) if "scope" in payload else None
        location, geocode_status, geocode_response = self.resolve_location(raw_address, payload)
        from_date = payload.get("from_date")
        if from_date:
            try:
                date.fromisoformat(from_date)
            except ValueError as exc:
                raise ValueError("from_date must be YYYY-MM-DD") from exc

        request_id = self.db.record_search_request(
            {
                "raw_address": raw_address,
                "normalized_address": location.normalized_address,
                "province": location.province,
                "city_county": location.city_county,
                "eup_myeon": location.eup_myeon,
                "ri": location.ri,
                "latitude": location.latitude,
                "longitude": location.longitude,
                "geocode_status": geocode_status,
                "radius_m": radius_m,
                "keywords": keywords,
                "issue_codes": issue_codes,
                "from_date": from_date,
                "limit": limit,
                "metadata": {"geocode_provider": location.provider},
            }
        )
        fts_query = self._build_fts_query(keywords + issue_codes)
        fts_used = bool(fts_query and self._fts_has_hits(fts_query))
        rows = self._load_segments(from_date, fts_query if fts_used else None, keywords)
        if scope_county:
            rows = [row for row in rows if row["city_county"] == scope_county]
        links = self._load_links()
        issues_by_segment = self._load_all_issues()
        hierarchy_by_segment = self._load_hierarchy_links()
        results = []
        for row in rows:
            row_issues = issues_by_segment.get(row["segment_id"], [])
            if issue_codes and not set(issue_codes).intersection({i["issue_code"] for i in row_issues}):
                continue
            text = f"{row['title']} {row['text_redacted']}"
            keyword_hits = [keyword for keyword in keywords if keyword in text]
            issue_anchor_hits = _issue_anchor_hits(issue_codes, text, row_issues)
            # An issue label can be broad (for example, a paragraph may be
            # tagged as regulatory because it says "허가"). For a typed case,
            # require the paragraph itself or its extracted evidence span to
            # contain a word specific to that issue. This prevents a grid or
            # general permit discussion from becoming distance evidence.
            if issue_codes and not issue_anchor_hits:
                continue
            keyword_score = min(1.0, len(keyword_hits) / max(1, min(3, len(keywords))))
            match = self._location_match(location, row, links.get(row["segment_id"], []), radius_m)
            if not match and keyword_score <= 0:
                continue
            if not match:
                match = {
                    "group": "comparative_case",
                    "precision": "unknown",
                    "distance_status": "unknown",
                    "distance_m": None,
                    "basis": "검색어 일치만 확인; 입력 지역과 위치 연결 없음",
                    "confidence": 0.2,
                }
            all_hierarchy = hierarchy_by_segment.get(row["segment_id"], [])
            if review_mode != "all" and not all_hierarchy:
                continue
            if review_mode == "all":
                hierarchy = all_hierarchy
            elif review_mode == "needs_review":
                hierarchy = [
                    link for link in all_hierarchy
                    if link.get("case", {}).get("review_decision") == "needs_review"
                ]
            else:
                hierarchy = [
                    link for link in all_hierarchy
                    if link.get("case", {}).get("review_decision") == "eligible"
                ]
            # A direct segment can remain searchable without a case, but a
            # rejected/ambiguous case must not silently re-enter strict search
            # through its trigger paragraph.
            if all_hierarchy and not hierarchy:
                continue
            primary_hierarchy = hierarchy[0] if hierarchy else None
            if match["group"] == "comparative_case" and not payload.get("include_comparative", False):
                continue
            spatial_score = {
                "exact_site": 1.0,
                "nearby": 0.82,
                "same_village": 0.72,
                "same_ri": 0.72,
                "same_admin_area": 0.5,
                "comparative_case": 0.12,
            }[match["group"]]
            source_score = 0.9 if row["source_url"] else 0.5
            score = 0.38 * keyword_score + 0.32 * spatial_score + 0.2 * _date_recency(row["meeting_date"]) + 0.1 * source_score
            result = {
                    "evidence_id": row["segment_id"],
                    "document_id": row["document_id"],
                    "meeting_id": row["meeting_id"],
                    "title": row["title"],
                    "meeting_title": row["meeting_title"] or row["title"],
                    "assembly_name": row["assembly_name"],
                    "meeting_date": row["meeting_date"],
                    "page_no": row["page_from"],
                    "segment_id": row["segment_id"],
                    "speaker": {"name": row["speaker_name"], "role": row["speaker_role"]},
                    "location_match": match,
                    "issues": row_issues,
                    "evidence_text": _contextual_snippet(
                        row["text_redacted"],
                        list(dict.fromkeys(issue_anchor_hits + keyword_hits)) or keywords,
                    ),
                    "evidence_text_original": row["text_original"],
                    "source": {
                        "url": row["original_file_url"] or row["source_url"],
                        "api_url": row["source_url"],
                        "original_file_url": row["original_file_url"],
                        "provider": row["provider"],
                        "source_record_key": row["source_record_key"],
                    },
                    "review_status": row["review_status"],
                    "case": primary_hierarchy["case"] if primary_hierarchy else None,
                    "episode": primary_hierarchy["episode"] if primary_hierarchy else None,
                    "evidence_role": primary_hierarchy["evidence_role"] if primary_hierarchy else None,
                    "hierarchy_links": hierarchy,
                    "retrieval": {
                        "score": round(score, 4),
                        "keyword_hits": keyword_hits,
                        "method": "admin_or_distance_plus_keyword",
                    },
                }
            results.append(result)
        results.sort(
            key=lambda item: (
                GROUP_RANK.get(item["location_match"]["group"], 0),
                item["retrieval"]["score"],
                item["meeting_date"] or "",
            ),
            reverse=True,
        )
        results = _dedupe_and_diversify(results, limit)
        groups = defaultdict(int)
        case_groups: dict[str, dict[str, Any]] = {}
        for item in results:
            groups[item["location_match"]["group"]] += 1
            linked_cases = []
            if item.get("case"):
                linked_cases.append({"case": item["case"], "episode": item.get("episode")})
            linked_cases.extend(
                {"case": link.get("case"), "episode": link.get("episode")}
                for link in item.get("hierarchy_links", [])
                if link.get("case")
            )
            seen_case_ids: set[str] = set()
            for linked in linked_cases:
                case = linked["case"]
                if not case or case["case_id"] in seen_case_ids:
                    continue
                seen_case_ids.add(case["case_id"])
                group = case_groups.setdefault(
                    case["case_id"],
                    {"case": case, "episode_ids": set(), "evidence_count": 0},
                )
                episode = linked.get("episode")
                if episode:
                    group["episode_ids"].add(episode["episode_id"])
                group["evidence_count"] += 1
        case_ids = set(case_groups)
        paragraph_groups = self._load_case_paragraphs(case_ids, case_paragraph_limit)
        case_groups_payload = []
        for case_id, group in case_groups.items():
            paragraph_payload = paragraph_groups.get(case_id, {"total": 0, "paragraphs": [], "truncated": False})
            case_groups_payload.append(
                {
                    "case": group["case"],
                    "episode_ids": sorted(group["episode_ids"]),
                    "evidence_count": group["evidence_count"],
                    "paragraph_count": paragraph_payload["total"],
                    "paragraphs_truncated": paragraph_payload["truncated"],
                    "paragraphs": paragraph_payload["paragraphs"],
                }
            )
        return {
            "search_request_id": request_id,
            "query": {
                "address": raw_address,
                "normalized_address": location.normalized_address,
                "location": location.to_dict(),
                "radius_m": radius_m,
                "keywords": keywords,
                "issue_codes": issue_codes,
                "from_date": from_date,
                "case_paragraph_limit": case_paragraph_limit,
                "review_mode": review_mode,
                "scope": scope_county,
            },
            "summary": {
                "total": len(results),
                "case_count": len(case_groups_payload),
                "groups": dict(groups),
                "geocode_status": geocode_status,
                "coordinate_search_used": bool(location.latitude and location.longitude),
                "fts_used": fts_used,
                "review_mode": review_mode,
                "scope": scope_county,
                "review_filtered_case_links": sum(max(0, len(hierarchy_by_segment.get(row["segment_id"], [])) - len([link for link in hierarchy_by_segment.get(row["segment_id"], []) if review_mode == "all" or link.get("case", {}).get("review_decision") == ("eligible" if review_mode == "eligible" else "needs_review")])) for row in rows),
            },
            "results": results,
            "case_groups": case_groups_payload,
            "notice": "영암군 범위의 합성·공개 기록을 연결한 참고 자료이며, 허가·계통 접속 여부를 자동 판정하지 않습니다.",
        }

    def get_case_paragraphs(self, case_id: str, limit: int | None = None) -> dict[str, Any] | None:
        case_row = self.db.conn.execute(
            """SELECT c.case_id, COALESCE(c.canonical_title, c.case_name) AS canonical_title,
                      municipality, village, address, project_name, facility_type,
                      confidence, review_status,
                      cr.decision AS review_decision, cr.quality_score,
                      cr.reason_codes_json
                 FROM conflict_case c
                 LEFT JOIN case_review cr ON cr.case_id=c.case_id
                WHERE c.case_id=?""",
            (case_id,),
        ).fetchone()
        if not case_row:
            return None
        paragraph_payload = self._load_case_paragraphs({case_id}, limit).get(
            case_id, {"total": 0, "paragraphs": [], "truncated": False}
        )
        case_payload = dict(case_row)
        try:
            case_payload["review_reason_codes"] = json.loads(case_payload.pop("reason_codes_json") or "[]")
        except (TypeError, ValueError):
            case_payload["review_reason_codes"] = []
        return {
            "case": case_payload,
            "paragraph_count": paragraph_payload["total"],
            "paragraphs_truncated": paragraph_payload["truncated"],
            "paragraphs": paragraph_payload["paragraphs"],
        }

    @staticmethod
    def _build_fts_query(terms: list[str]) -> str:
        safe_terms = [term.replace('"', " ").strip() for term in terms if term.strip()]
        return " OR ".join(f'"{term}"' for term in safe_terms)

    def _fts_has_hits(self, fts_query: str) -> bool:
        row = self.db.conn.execute(
            "SELECT 1 FROM meeting_segment_fts WHERE meeting_segment_fts MATCH ? LIMIT 1",
            (fts_query,),
        ).fetchone()
        return bool(row)

    def warm_cache(self) -> dict[str, int]:
        """Touch the pages a search reads, so the first user query is not slow.

        The segment table dominates the query cost and is only fast once the
        operating system has it cached; on a cold start the first search pays
        the whole read. Running it at boot moves that cost off the demo.
        """

        counts = {
            "segments": self.db.conn.execute(
                "SELECT count(*) FROM meeting_segment WHERE text_redacted <> ''"
            ).fetchone()[0],
            "issues": self.db.conn.execute("SELECT count(*) FROM segment_issue").fetchone()[0],
            "permits": self.db.conn.execute("SELECT count(*) FROM permit_project").fetchone()[0],
        }
        return counts

    def _load_segments(
        self, from_date: str | None, fts_query: str | None = None, keywords: list[str] | None = None
    ) -> list[Any]:
        sql = """SELECT s.segment_id, s.document_id, s.page_from, s.text_original, s.text_redacted,
                 s.review_status, s.speaker_id, d.title, d.source_url, d.original_file_url,
                 d.source_record_key, ss.provider, m.meeting_id, m.meeting_title,
                 m.assembly_name, m.province, m.city_county, m.meeting_date,
                 sp.name AS speaker_name, sp.role AS speaker_role
                 FROM meeting_segment s
                 JOIN source_document d ON d.document_id = s.document_id
                 JOIN source_system ss ON ss.source_system_id = d.source_system_id
                 LEFT JOIN meeting m ON m.meeting_id = s.meeting_id
                 LEFT JOIN speaker sp ON sp.speaker_id = s.speaker_id
                 WHERE (s.relevance_status = 'relevant' OR s.text_redacted <> '')"""
        params: list[Any] = []
        if from_date:
            sql += " AND (m.meeting_date IS NULL OR m.meeting_date >= ?)"
            params.append(from_date)
        if fts_query:
            # Korean minutes often use agglutinated forms such as 설명회에서.
            # Keep FTS as the indexed shortlist but retain a LIKE fallback so
            # substring recall is not lost when unicode61 cannot stem Korean.
            #
            # The two are gathered as a UNION of id queries rather than as one
            # OR predicate. An OR that mixes an FTS subquery with LIKE forces a
            # full scan of every segment plus its joins; on a 200k-segment
            # database that is ~25s per search, against ~2s for the union.
            # The candidate set is identical either way.
            like_terms = [term for term in (keywords or []) if term]
            branches = ["SELECT segment_id FROM meeting_segment_fts WHERE meeting_segment_fts MATCH ?"]
            candidate_params: list[Any] = [fts_query]
            for term in like_terms:
                branches.append("SELECT segment_id FROM meeting_segment WHERE text_redacted LIKE ?")
                candidate_params.append(f"%{term}%")
                # Match the title against source_document first (hundreds of
                # rows) and expand to segments by document id, instead of
                # joining every segment to its document to test the title.
                branches.append(
                    """SELECT segment_id FROM meeting_segment
                        WHERE document_id IN (
                              SELECT document_id FROM source_document WHERE title LIKE ?)"""
                )
                candidate_params.append(f"%{term}%")
            sql += " AND s.segment_id IN (" + " UNION ".join(branches) + ")"
            params.extend(candidate_params)
        return self.db.conn.execute(sql, params).fetchall()

    def _load_case_paragraphs(
        self, case_ids: set[str], limit: int | None = None
    ) -> dict[str, dict[str, Any]]:
        if not case_ids:
            return {}
        placeholders = ",".join("?" for _ in case_ids)
        rows = self.db.conn.execute(
            f"""SELECT cp.case_id, cp.document_id, cp.paragraph_id,
                      cp.paragraph_order, cp.page_from, cp.page_to,
                      cp.segment_type, cp.speaker_id, cp.text_original,
                      cp.text_redacted, cp.relation_type,
                      cp.paragraph_link_confidence, cp.review_status,
                      d.title AS document_title, d.source_url,
                      d.original_file_url, d.source_record_key,
                      ss.provider, m.meeting_title, m.meeting_date,
                      m.assembly_name, sp.name AS speaker_name,
                      sp.role AS speaker_role,
                      group_concat(DISTINCT ce.evidence_role) AS evidence_roles,
                      group_concat(DISTINCT ce.episode_id) AS episode_ids
                 FROM case_paragraphs cp
                 JOIN source_document d ON d.document_id=cp.document_id
                 JOIN source_system ss ON ss.source_system_id=d.source_system_id
                 LEFT JOIN meeting m ON m.meeting_id=(
                     SELECT meeting_id FROM meeting WHERE document_id=cp.document_id LIMIT 1
                 )
                 LEFT JOIN speaker sp ON sp.speaker_id=cp.speaker_id
                 LEFT JOIN case_evidence ce
                        ON ce.case_id=cp.case_id AND ce.paragraph_id=cp.paragraph_id
                WHERE cp.case_id IN ({placeholders})
                GROUP BY cp.case_id, cp.document_id, cp.paragraph_id,
                         cp.paragraph_order, cp.page_from, cp.page_to,
                         cp.segment_type, cp.speaker_id, cp.text_original,
                         cp.text_redacted, cp.relation_type,
                         cp.paragraph_link_confidence, cp.review_status,
                         d.title, d.source_url, d.original_file_url,
                         d.source_record_key, ss.provider, m.meeting_title,
                         m.meeting_date, m.assembly_name, sp.name, sp.role
                ORDER BY cp.case_id, COALESCE(m.meeting_date, '9999-12-31'),
                         cp.paragraph_order, cp.paragraph_id""",
            list(case_ids),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            roles = sorted({value for value in str(row["evidence_roles"] or "").split(",") if value})
            episode_ids = sorted({value for value in str(row["episode_ids"] or "").split(",") if value})
            grouped[row["case_id"]].append(
                {
                    "paragraph_id": row["paragraph_id"],
                    "document_id": row["document_id"],
                    "document_title": row["document_title"],
                    "meeting_title": row["meeting_title"] or row["document_title"],
                    "meeting_date": row["meeting_date"],
                    "assembly_name": row["assembly_name"],
                    "page_from": row["page_from"],
                    "page_to": row["page_to"],
                    "paragraph_order": row["paragraph_order"],
                    "segment_type": row["segment_type"],
                    "speaker": {"name": row["speaker_name"], "role": row["speaker_role"]},
                    "text_original": row["text_original"],
                    "text_redacted": row["text_redacted"],
                    "evidence_roles": roles,
                    "episode_ids": episode_ids,
                    "relation_type": row["relation_type"],
                    "link_confidence": row["paragraph_link_confidence"],
                    "source": {
                        "url": row["original_file_url"] or row["source_url"],
                        "official_url": row["source_url"],
                        "original_file_url": row["original_file_url"],
                        "provider": row["provider"],
                        "source_record_key": row["source_record_key"],
                    },
                    "review_status": row["review_status"],
                }
            )
        output: dict[str, dict[str, Any]] = {}
        for case_id, paragraphs in grouped.items():
            truncated = bool(limit and len(paragraphs) > limit)
            output[case_id] = {
                "total": len(paragraphs),
                "paragraphs": paragraphs[:limit] if limit else paragraphs,
                "truncated": truncated,
            }
        return output

    def _load_links(self) -> dict[str, list[Any]]:
        rows = self.db.conn.execute(
            """SELECT l.segment_id, l.relation_type, l.distance_m, l.distance_status,
               l.confidence, l.evidence_text, p.place_id, p.raw_name, p.place_type,
               p.province, p.city_county, p.eup_myeon, p.ri, p.latitude, p.longitude,
               p.geo_precision, p.location_status
               FROM segment_place_link l JOIN canonical_place p ON p.place_id = l.place_id
               WHERE l.review_status <> 'rejected' AND p.location_status <> 'rejected'"""
        ).fetchall()
        result: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            result[row["segment_id"]].append(row)
        return result

    def _issues_for(self, segment_id: str) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """SELECT issue_code, polarity, target_type, confidence, evidence_span, review_status
               FROM segment_issue WHERE segment_id = ? ORDER BY confidence DESC""",
            (segment_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _load_all_issues(self) -> dict[str, list[dict[str, Any]]]:
        rows = self.db.conn.execute(
            """SELECT segment_id, issue_code, polarity, target_type, confidence,
               evidence_span, review_status
               FROM segment_issue ORDER BY segment_id, confidence DESC"""
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result[row["segment_id"]].append(dict(row))
        return result

    def _load_hierarchy_links(self) -> dict[str, list[dict[str, Any]]]:
        """Load case/episode identity separately from paragraph evidence.

        A paragraph can be context for more than one episode, so the API keeps
        all links and chooses the strongest one only for the compact `case` and
        `episode` fields. This makes a result explainable without pretending
        that one paragraph can belong to only one inferred event.
        """
        rows = self.db.conn.execute(
            """SELECT ce.paragraph_id, ce.case_id, ce.episode_id, ce.evidence_role,
                      ce.link_confidence, c.case_key,
                      COALESCE(c.canonical_title, c.case_name) AS canonical_title,
                      c.municipality, c.village, c.address, c.project_name,
                      c.facility_type, c.confidence AS case_confidence,
                      c.review_status AS case_review_status,
                      cr.decision AS case_review_decision,
                      cr.quality_score AS case_quality_score,
                      cr.reason_codes_json AS case_review_reason_codes,
                      c.representative_place_id,
                      cp.normalized_name AS inferred_place_name,
                      cp.geo_precision AS inferred_geo_precision,
                      cp.latitude AS inferred_latitude,
                      cp.longitude AS inferred_longitude,
                      e.issue_type, e.issue_types_json, e.stance,
                      e.procedure_stage, e.paragraph_start, e.paragraph_end,
                      e.confidence AS episode_confidence,
                      e.grouping_score, e.review_status AS episode_review_status
                 FROM case_evidence ce
                 JOIN conflict_case c ON c.case_id=ce.case_id
                 JOIN episodes e ON e.episode_id=ce.episode_id
                LEFT JOIN canonical_place cp ON cp.place_id=c.representative_place_id
                LEFT JOIN case_review cr ON cr.case_id=c.case_id
                WHERE ce.review_status <> 'rejected'
                ORDER BY ce.paragraph_id,
                         CASE ce.evidence_role
                           WHEN 'trigger_sentence' THEN 0
                           WHEN 'episode_context' THEN 1
                           WHEN 'context_before' THEN 2
                           WHEN 'context_after' THEN 3
                           ELSE 4
                         END,
                         COALESCE(ce.link_confidence, 0) DESC"""
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            try:
                issue_types = json.loads(row["issue_types_json"] or "[]")
            except (TypeError, ValueError):
                issue_types = []
            try:
                review_reason_codes = json.loads(row["case_review_reason_codes"] or "[]")
            except (TypeError, ValueError):
                review_reason_codes = []
            result[row["paragraph_id"]].append(
                {
                    "case": {
                        "case_id": row["case_id"],
                        "case_key": row["case_key"],
                        "canonical_title": row["canonical_title"],
                        "municipality": row["municipality"],
                        "village": row["village"],
                        "address": row["address"],
                        "project_name": row["project_name"],
                        "facility_type": row["facility_type"],
                        "inferred_location": {
                            "place_id": row["representative_place_id"],
                            "place_name": row["inferred_place_name"],
                            "geo_precision": row["inferred_geo_precision"],
                            "latitude": row["inferred_latitude"],
                            "longitude": row["inferred_longitude"],
                            "distance_status": "exact" if row["inferred_latitude"] is not None and row["inferred_longitude"] is not None else "unknown",
                            "inference_note": "문서에 명시된 장소 후보이며, 좌표가 없으면 실제 설치 필지·거리로 확정하지 않음",
                        } if row["representative_place_id"] else None,
                        "confidence": row["case_confidence"],
                        "review_status": row["case_review_status"],
                        "review_decision": row["case_review_decision"],
                        "quality_score": row["case_quality_score"],
                        "review_reason_codes": review_reason_codes,
                    },
                    "episode": {
                        "episode_id": row["episode_id"],
                        "issue_type": row["issue_type"],
                        "issue_types": issue_types,
                        "stance": row["stance"],
                        "procedure_stage": row["procedure_stage"],
                        "paragraph_start": row["paragraph_start"],
                        "paragraph_end": row["paragraph_end"],
                        "confidence": row["episode_confidence"],
                        "grouping_score": row["grouping_score"],
                        "review_status": row["episode_review_status"],
                    },
                    "evidence_role": row["evidence_role"],
                    "link_confidence": row["link_confidence"],
                }
            )
        return result

    def _location_match(
        self, target: Location, row: Any, link_rows: list[Any], radius_m: float
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for link in link_rows:
            province_match = not target.province or not link["province"] or _same(target.province, link["province"])
            city_match = not target.city_county or not link["city_county"] or _same(target.city_county, link["city_county"])
            if not province_match or not city_match:
                continue
            if target.latitude is not None and target.longitude is not None and link["latitude"] is not None and link["longitude"] is not None:
                distance = haversine_m(target.latitude, target.longitude, link["latitude"], link["longitude"])
                if distance <= radius_m:
                    exact_precision = link["geo_precision"] in {"parcel", "building", "road_address", "jibun_address"}
                    group = "exact_site" if exact_precision and distance <= 150 else "nearby"
                    candidates.append(
                        {
                            "group": group,
                            "precision": link["geo_precision"],
                            "distance_status": "exact",
                            "distance_m": round(distance, 1),
                            "basis": "좌표 간 거리",
                            "confidence": min(0.98, float(link["confidence"] or 0.7)),
                            "place_name": link["raw_name"],
                        }
                    )
                    continue
            if target.ri and _same(target.ri, link["ri"]):
                candidates.append(
                    {
                        "group": "same_village" if link["ri"] else "same_ri",
                        "precision": link["geo_precision"],
                        "distance_status": "unknown",
                        "distance_m": None,
                        "basis": "동일 리(좌표 없음)",
                        "confidence": min(0.88, float(link["confidence"] or 0.6)),
                        "place_name": link["raw_name"],
                    }
                )
            elif target.eup_myeon and _same(target.eup_myeon, link["eup_myeon"]):
                candidates.append(
                    {
                        "group": "same_admin_area",
                        "precision": link["geo_precision"],
                        "distance_status": "unknown",
                        "distance_m": None,
                        "basis": "동일 읍·면·동(좌표 없음)",
                        "confidence": min(0.8, float(link["confidence"] or 0.55)),
                        "place_name": link["raw_name"],
                    }
                )
            elif target.city_county and _same(target.city_county, link["city_county"]):
                candidates.append(
                    {
                        # Keep the existing group for API compatibility, but
                        # state clearly in the basis that this is only a
                        # county-level comparison when an 읍·면 is supplied.
                        "group": "same_admin_area",
                        "precision": link["geo_precision"],
                        "distance_status": "unknown",
                        "distance_m": None,
                        "basis": "동일 시·군·구 비교 참고(입력 읍·면·리와 직접 연결되지 않음)" if target.eup_myeon else "동일 시·군·구(좌표 없음)",
                        "confidence": min(0.65, float(link["confidence"] or 0.5)),
                        "place_name": link["raw_name"],
                    }
                )
        if not candidates:
            meeting_province = row["province"]
            meeting_city = row["city_county"]
            if (not target.province or not meeting_province or _same(target.province, meeting_province)) and (
                not target.city_county or not meeting_city or _same(target.city_county, meeting_city)
            ):
                candidates.append(
                    {
                        "group": "same_admin_area",
                        "precision": "city_county" if meeting_city else "province",
                        "distance_status": "unknown",
                        "distance_m": None,
                        "basis": "회의 개최 지방의회 기준 비교 참고(사업지 좌표 아님)",
                        "confidence": 0.45 if meeting_city else 0.25,
                        "place_name": meeting_city or meeting_province,
                    }
                )
        if not candidates:
            return None
        candidates.sort(key=lambda item: (GROUP_RANK[item["group"]], item["confidence"]), reverse=True)
        return candidates[0]
