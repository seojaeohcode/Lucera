from __future__ import annotations

import json
import re
from typing import Any

from .db import stable_id
from .extract import extract_places, redact_sensitive
from .keywords import classify_text, keyword_occurrences, split_sentence_spans


EPISODE_BREAK_PATTERN = re.compile(
    r"또\s*다른\s*민원|별도로|한편|다음\s*건|다음\s*안건|다른\s*민원|이와\s*별개|다른\s*사업"
)
FACILITY_TYPES = (
    ("수상태양광", "수상태양광"),
    ("영농형태양광", "영농형태양광"),
    ("학교 태양광", "학교 태양광"),
    ("주민참여형 태양광", "주민참여형 태양광"),
    ("지붕", "지붕형 태양광"),
    ("옥상", "옥상형 태양광"),
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _issue_codes(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("issue_code")) for item in items if item.get("issue_code")}


def _place_entities(text: str, meeting_context: str) -> tuple[set[str], list[dict[str, Any]]]:
    entities: set[str] = set()
    places = extract_places(text, meeting_context)
    for place in places:
        place_type = place.get("place_type")
        # A municipality alone is not strong enough to merge cases across
        # documents.  Eup/myeon/ri or a resolved road/jibun address is.
        if (
            place_type in {"ri", "eup_myeon", "road_address", "jibun_address"}
            and place.get("relation_type") not in {"comparative", "comparative_reference", "unverified_reference"}
        ):
            normalized = place.get("normalized_name") or place.get("raw_name")
            if normalized:
                entities.add(f"place:{normalized}")
    return entities, places


def _facility_type(text: str) -> str:
    for term, label in FACILITY_TYPES:
        if term in text:
            return label
    return "태양광"


def _project_name(text: str) -> str | None:
    patterns = (
        r"([가-힣0-9]{2,20}(?:산|마을|단지|저수지|댐))\s*(?:태양광|발전사업|발전시설|발전소)",
        r"(?:태양광|발전사업|발전시설|발전소)\s*([가-힣0-9]{2,20}(?:산|마을|단지|저수지|댐))",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _entity_keys(text: str, meeting_context: str) -> tuple[set[str], str, str | None, list[dict[str, Any]]]:
    entities, places = _place_entities(text, meeting_context)
    facility_type = _facility_type(text)
    project_name = _project_name(text)
    if facility_type != "태양광":
        entities.add(f"facility:{facility_type}")
    if project_name:
        entities.add(f"project:{project_name}")
    return entities, facility_type, project_name, places


def _agenda_key(row: Any, meeting_title: str, agenda_text: str) -> str:
    return str(row["agenda_no"] or row["section_title"] or meeting_title or agenda_text or "__meeting__")


def _existing_issue_rows(db: Any, segment_id: str) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """SELECT issue_code, polarity, confidence, evidence_span, metadata_json
           FROM segment_issue WHERE segment_id = ? ORDER BY confidence DESC""",
        (segment_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _paragraph_break_between(rows: list[dict[str, Any]], left: int, right: int) -> bool:
    return any(EPISODE_BREAK_PATTERN.search(rows[index]["text"] or "") for index in range(left + 1, right + 1))


def _link_score(left: dict[str, Any], right: dict[str, Any], paragraph_rows: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    # A transition can occur inside one paragraph, so paragraph-only checks
    # are not sufficient when signals are sentence-level.
    if EPISODE_BREAK_PATTERN.search(right.get("sentence", {}).get("text", "") or ""):
        return 0.0, {"hard_break": 0.0}
    if _paragraph_break_between(paragraph_rows, left["paragraph_index"], right["paragraph_index"]):
        return 0.0, {"hard_break": 0.0}
    gap = right["paragraph_index"] - left["paragraph_index"]
    if gap > 5:
        return 0.0, {"gap": 0.0}
    components = {"same_document": 0.25}
    if left["agenda_key"] == right["agenda_key"]:
        components["same_agenda"] = 0.20
    issue_overlap = _issue_codes(left["classification"].get("issues", [])) & _issue_codes(right["classification"].get("issues", []))
    if issue_overlap:
        components["issue_overlap"] = 0.15
    entity_overlap = left["entity_keys"] & right["entity_keys"]
    if entity_overlap:
        components["entity_overlap"] = 0.30
    if gap <= 2:
        components["paragraph_proximity"] = 0.10
    elif gap <= 5:
        components["paragraph_proximity"] = 0.05
    return sum(components.values()), components


def _stance(signals: list[dict[str, Any]]) -> str:
    polarities: set[str] = set()
    for signal in signals:
        for issue in signal["classification"].get("issues", []):
            if issue.get("polarity") in {"opposition", "support"}:
                polarities.add(issue["polarity"])
            elif issue.get("polarity") == "mixed":
                polarities.update({"opposition", "support"})
    if polarities == {"opposition", "support"}:
        return "mixed"
    if "opposition" in polarities:
        return "opposition"
    if "support" in polarities:
        return "support"
    return "neutral" if signals else "unknown"


def _procedure_stage(text: str) -> str | None:
    if any(term in text for term in ("소송", "법원", "판결", "행정심판")):
        return "소송·판결·행정심판"
    if any(term in text for term in ("허가취소", "취소", "시정", "보류", "유보")):
        return "허가취소·행정조치"
    if any(term in text for term in ("개발행위허가", "인허가", "허가", "이격거리", "거리제한")):
        return "개발행위·인허가"
    if "민원" in text:
        return "민원 접수·처리"
    if any(term in text for term in ("설명회", "공청회", "협의", "소통", "의견수렴")):
        return "주민설명·협의"
    return None


def _insert_sentence_and_mentions(db: Any, paragraph: dict[str, Any], context_text: str) -> list[dict[str, Any]]:
    sentence_records: list[dict[str, Any]] = []
    spans = split_sentence_spans(paragraph["text"] or "")
    for order, span in enumerate(spans, 1):
        sentence_id = stable_id("sentence", paragraph["paragraph_id"], order)
        sentence_text = span["text"]
        db.conn.execute(
            """INSERT INTO sentences
               (sentence_id, paragraph_id, sentence_order, text, text_redacted,
                char_start, char_end, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sentence_id,
                paragraph["paragraph_id"],
                order,
                sentence_text,
                redact_sensitive(sentence_text),
                span["char_start"],
                span["char_end"],
                _json({"splitter": "sentence-boundary-v1"}),
            ),
        )
        mention_ids: list[str] = []
        for mention in keyword_occurrences(sentence_text, span["char_start"]):
            mention_id = stable_id(
                "keyword_mention",
                sentence_id,
                mention["keyword"],
                mention["start_offset"],
                mention["end_offset"],
            )
            mention_ids.append(mention_id)
            db.conn.execute(
                """INSERT INTO keyword_mentions
                   (mention_id, sentence_id, keyword, normalized_keyword,
                    start_offset, end_offset, match_type, keyword_group,
                    problem_category, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mention_id,
                    sentence_id,
                    mention["keyword"],
                    mention["normalized_keyword"],
                    mention["start_offset"],
                    mention["end_offset"],
                    mention["match_type"],
                    mention["keyword_group"],
                    mention["problem_category"],
                    _json(mention.get("metadata", {})),
                ),
            )
        classification = classify_text(sentence_text, context_text)
        sentence_records.append(
            {
                "sentence_id": sentence_id,
                "sentence_order": order,
                "text": sentence_text,
                "classification": classification,
                "mention_ids": mention_ids,
            }
        )
    return sentence_records


def _case_name(meeting: dict[str, Any], episode: dict[str, Any]) -> str:
    village = episode.get("village")
    if village:
        village_tokens = str(village).split()
        village = " ".join(
            token for token in village_tokens
            if token not in {meeting.get("province"), meeting.get("city_county")}
        )
    parts = [meeting.get("city_county"), village, episode.get("project_name")]
    if not episode.get("project_name"):
        parts.append(episode.get("facility_type") or "태양광")
    parts = [str(value) for value in parts if value]
    return " ".join(parts) + " 관련 민원·갈등" if parts else "태양광 관련 민원·갈등"


def _upsert_case(db: Any, identity: str, meeting: dict[str, Any], episode: dict[str, Any]) -> str:
    case_id = stable_id("case", identity)
    case_name = _case_name(meeting, episode)
    existing = db.conn.execute(
        "SELECT case_id, started_on, ended_on, metadata_json FROM conflict_case WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    started = meeting.get("meeting_date")
    ended = meeting.get("meeting_date")
    if existing:
        metadata: dict[str, Any]
        try:
            metadata = json.loads(existing["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        issue_types = set(metadata.get("issue_types", []))
        issue_types.update(episode.get("issue_types", []))
        metadata["issue_types"] = sorted(issue_types)
        metadata["identity"] = identity
        db.conn.execute(
            """UPDATE conflict_case
               SET canonical_title=?, case_name=?, municipality=?, village=?,
                   address=?, project_name=?, facility_type=?,
                   started_on=CASE WHEN started_on IS NULL OR ? < started_on THEN ? ELSE started_on END,
                   ended_on=CASE WHEN ended_on IS NULL OR ? > ended_on THEN ? ELSE ended_on END,
                   confidence=MAX(COALESCE(confidence, 0), ?), metadata_json=?, updated_at=CURRENT_TIMESTAMP
             WHERE case_id=?""",
            (
                case_name,
                case_name,
                meeting.get("city_county"),
                episode.get("village"),
                episode.get("address"),
                episode.get("project_name"),
                episode.get("facility_type"),
                started,
                started,
                ended,
                ended,
                episode.get("confidence", 0.55),
                _json(metadata),
                case_id,
            ),
        )
        return case_id
    db.conn.execute(
        """INSERT INTO conflict_case
           (case_id, case_key, case_name, canonical_title, municipality,
            village, address, project_name, facility_type, summary,
            case_status, started_on, ended_on, confidence, review_status,
            metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, 'pending', ?)""",
        (
            case_id,
            identity,
            case_name,
            case_name,
            meeting.get("city_county"),
            episode.get("village"),
            episode.get("address"),
            episode.get("project_name"),
            episode.get("facility_type"),
            _json({"issue_types": episode.get("issue_types", []), "identity": identity}),
            started,
            ended,
            episode.get("confidence", 0.55),
            _json({"grouping_method": "identity_v1", "identity_confidence": episode.get("confidence", 0.55)}),
        ),
    )
    return case_id


def _infer_case_locations(db: Any, case_id: str, episode_id: str) -> int:
    """Rank places explicitly mentioned in an episode as case-site candidates.

    This deliberately never creates a coordinate from an 읍/면/리 name.  A
    parcel/road/building place with coordinates ranks highest; an administrative
    place remains a candidate with its original precision and unknown distance.
    """
    rows = db.conn.execute(
        """SELECT ee.paragraph_id, ee.evidence_role, spl.place_id,
                  spl.relation_type, spl.confidence, spl.evidence_text,
                  p.place_type, p.geo_precision, p.latitude, p.longitude,
                  p.location_status
             FROM episode_evidence ee
             JOIN segment_place_link spl ON spl.segment_id=ee.paragraph_id
             JOIN canonical_place p ON p.place_id=spl.place_id
            WHERE ee.episode_id=?
              AND ee.evidence_role IN ('trigger_sentence', 'episode_context', 'context_after')
              AND spl.review_status <> 'rejected'
              AND spl.relation_type NOT IN ('comparative', 'comparative_reference', 'unverified_reference')
              AND p.location_status <> 'rejected'""",
        (episode_id,),
    ).fetchall()
    precision_weight = {
        "parcel": 0.95,
        "building": 0.92,
        "road_address": 0.90,
        "jibun_address": 0.88,
        "ri": 0.72,
        "village": 0.72,
        "eup_myeon": 0.60,
        "city_county": 0.42,
        "province": 0.25,
        "unknown": 0.20,
    }
    relation_weight = {
        "subject_site": 0.10,
        "nearby": 0.06,
        "same_village": 0.04,
        "same_ri": 0.04,
        "same_eup_myeon": 0.02,
        "same_city_county": 0.00,
        "meeting_institution": -0.10,
    }
    best_by_place: dict[str, dict[str, Any]] = {}
    for row in rows:
        precision = row["geo_precision"] or row["place_type"] or "unknown"
        score = min(0.98, precision_weight.get(precision, 0.20) + relation_weight.get(row["relation_type"], 0.0))
        if row["latitude"] is not None and row["longitude"] is not None:
            score = min(0.99, score + 0.08)
        candidate = {
            "place_id": row["place_id"],
            "confidence": score,
            "inference_method": "episode_place_cooccurrence",
            "paragraph_id": row["paragraph_id"],
            "evidence_text": row["evidence_text"],
            "evidence_role": row["evidence_role"],
            "relation_type": row["relation_type"],
            "geo_precision": precision,
        }
        previous = best_by_place.get(row["place_id"])
        if not previous or candidate["confidence"] > previous["confidence"]:
            best_by_place[row["place_id"]] = candidate
    candidates = sorted(best_by_place.values(), key=lambda item: (-item["confidence"], item["place_id"]))
    if not candidates:
        return 0
    for rank, candidate in enumerate(candidates, 1):
        db.conn.execute(
            """INSERT INTO case_location_candidate
               (case_location_candidate_id, case_id, place_id, rank,
                inference_method, confidence, evidence_episode_id,
                evidence_paragraph_id, evidence_text, is_selected,
                review_status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
               ON CONFLICT(case_id, place_id) DO UPDATE SET
                 rank=excluded.rank, inference_method=excluded.inference_method,
                 confidence=excluded.confidence,
                 evidence_episode_id=excluded.evidence_episode_id,
                 evidence_paragraph_id=excluded.evidence_paragraph_id,
                 evidence_text=excluded.evidence_text,
                 is_selected=excluded.is_selected,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                stable_id("case_location_candidate", case_id, candidate["place_id"]),
                case_id,
                candidate["place_id"],
                rank,
                candidate["inference_method"],
                candidate["confidence"],
                episode_id,
                candidate["paragraph_id"],
                candidate["evidence_text"],
                0,
                _json({"relation_type": candidate["relation_type"], "geo_precision": candidate["geo_precision"], "evidence_role": candidate["evidence_role"]}),
            ),
        )
    all_candidates = db.conn.execute(
        """SELECT case_location_candidate_id
             FROM case_location_candidate
            WHERE case_id=?
            ORDER BY confidence DESC, rank, place_id""",
        (case_id,),
    ).fetchall()
    db.conn.execute("UPDATE case_location_candidate SET is_selected=0 WHERE case_id=?", (case_id,))
    for rank, row in enumerate(all_candidates, 1):
        db.conn.execute(
            "UPDATE case_location_candidate SET rank=?, is_selected=? WHERE case_location_candidate_id=?",
            (rank, 1 if rank == 1 else 0, row["case_location_candidate_id"]),
        )
    db.conn.execute(
        """UPDATE conflict_case
              SET representative_place_id=(
                    SELECT place_id FROM case_location_candidate
                     WHERE case_id=? AND is_selected=1
                     ORDER BY confidence DESC, rank LIMIT 1
                  ), updated_at=CURRENT_TIMESTAMP
            WHERE case_id=? AND review_status='pending'""",
        (case_id, case_id),
    )
    return len(candidates)


def _case_link_score(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Score a possible cross-document case link without accepting it."""
    components: dict[str, float] = {}
    if left.get("municipality") and left.get("municipality") == right.get("municipality"):
        components["same_municipality"] = 0.20
    if left.get("village") and left.get("village") == right.get("village"):
        components["same_village"] = 0.30
    if left.get("address") and left.get("address") == right.get("address"):
        components["same_address"] = 0.40
    if left.get("project_name") and left.get("project_name") == right.get("project_name"):
        components["same_project"] = 0.45
    if (
        left.get("facility_type")
        and left.get("facility_type") == right.get("facility_type")
        and left.get("facility_type") != "태양광"
    ):
        components["same_specialized_facility"] = 0.10
    issue_overlap = set(left.get("issue_types", [])) & set(right.get("issue_types", []))
    if issue_overlap:
        components["issue_overlap"] = 0.10
    return min(1.0, sum(components.values())), components


def _create_case_link_candidates(db: Any, case_ids: set[str]) -> int:
    """Persist only reviewable, non-accepted cross-document link candidates.

    Accepted identity merges already share one case_id.  This table is for
    plausible but insufficiently certain links; it prevents the common error
    of silently merging every solar complaint in one municipality.
    """
    created = 0
    if case_ids:
        placeholders = ",".join("?" for _ in case_ids)
        values = list(case_ids) + list(case_ids)
        db.conn.execute(
            f"""DELETE FROM case_link_candidate
                 WHERE status='pending'
                   AND (left_case_id IN ({placeholders})
                        OR right_case_id IN ({placeholders}))""",
            values,
        )
    for case_id in case_ids:
        current_row = db.conn.execute(
            """SELECT case_id, case_key, municipality, village, address,
                      project_name, facility_type, metadata_json
                 FROM conflict_case WHERE case_id=?""",
            (case_id,),
        ).fetchone()
        if not current_row:
            continue
        current = dict(current_row)
        try:
            current["issue_types"] = json.loads(current.get("metadata_json") or "{}").get("issue_types", [])
        except (TypeError, ValueError):
            current["issue_types"] = []
        if not current.get("municipality"):
            continue
        candidates = db.conn.execute(
            """SELECT case_id, case_key, municipality, village, address,
                      project_name, facility_type, metadata_json
                 FROM conflict_case
                WHERE municipality=? AND case_id<>?
                  AND (village IS NOT NULL OR address IS NOT NULL OR project_name IS NOT NULL
                       OR facility_type IS NOT NULL)""",
            (current["municipality"], case_id),
        ).fetchall()
        for candidate_row in candidates:
            candidate = dict(candidate_row)
            if candidate["case_id"] == case_id:
                continue
            try:
                candidate["issue_types"] = json.loads(candidate.get("metadata_json") or "{}").get("issue_types", [])
            except (TypeError, ValueError):
                candidate["issue_types"] = []
            score, features = _case_link_score(current, candidate)
            # Same project/address/village can be auto-merged by identity.  If
            # it somehow exists as two cases, leave the link as a review item;
            # do not rewrite evidence in this candidate pass.
            if score < 0.35 or score >= 0.80:
                continue
            left, right = sorted((case_id, candidate["case_id"]))
            db.conn.execute(
                """INSERT INTO case_link_candidate
                   (candidate_id, left_case_id, right_case_id, match_score,
                    matching_features_json, status, review_status)
                   VALUES (?, ?, ?, ?, ?, 'pending', 'pending')
                   ON CONFLICT(left_case_id, right_case_id) DO UPDATE SET
                     match_score=excluded.match_score,
                     matching_features_json=excluded.matching_features_json,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    stable_id("case_link_candidate", left, right),
                    left,
                    right,
                    score,
                    _json(features),
                ),
            )
            created += 1
    return created


def _prune_orphan_auto_cases(db: Any) -> int:
    """Remove stale rule-generated cases that no longer have evidence."""
    rows = db.conn.execute(
        """SELECT case_id
             FROM conflict_case c
            WHERE c.review_status='pending'
              AND c.metadata_json LIKE '%identity_v1%'
              AND NOT EXISTS (
                    SELECT 1 FROM case_evidence ce WHERE ce.case_id=c.case_id
              )"""
    ).fetchall()
    if rows:
        db.conn.executemany(
            "DELETE FROM conflict_case WHERE case_id=?",
            [(row["case_id"],) for row in rows],
        )
    return len(rows)


def rebuild_document_hierarchy(db: Any, document_id: str) -> dict[str, int]:
    """Rebuild sentences, keyword mentions, episodes, and case evidence.

    This function is deterministic and intentionally conservative.  A generic
    keyword occurrence never creates a case.  Paragraphs become episode signals
    only when they contain a classified issue, a high-precision standalone
    issue, or an issue keyword in solar context.  Cross-document cases require
    a strong project/place/facility identity; otherwise the case remains
    episode-scoped and pending review.
    """
    meeting_row = db.conn.execute(
        """SELECT m.meeting_id, m.meeting_title, m.agenda_text, m.assembly_name,
                  m.province, m.city_county, m.meeting_date
           FROM meeting m WHERE m.document_id = ?""",
        (document_id,),
    ).fetchone()
    if not meeting_row:
        return {
            "sentences": 0,
            "mentions": 0,
            "episodes": 0,
            "cases": 0,
            "case_evidence": 0,
            "process_events": 0,
            "case_link_candidates": 0,
        }
    meeting = dict(meeting_row)
    paragraphs = [
        dict(row)
        for row in db.conn.execute(
            """SELECT segment_id AS paragraph_id, document_id, ordinal,
                      section_title, agenda_no, segment_type, text_original AS text
               FROM meeting_segment WHERE document_id=? ORDER BY ordinal""",
            (document_id,),
        ).fetchall()
    ]
    db.conn.execute("DELETE FROM episodes WHERE document_id = ?", (document_id,))
    if paragraphs:
        db.conn.execute(
            """DELETE FROM sentences
               WHERE paragraph_id IN (SELECT segment_id FROM meeting_segment WHERE document_id=?)""",
            (document_id,),
        )

    context_text = f"{meeting.get('meeting_title') or ''} {meeting.get('agenda_text') or ''}".strip()
    meeting_context = " ".join(value for value in (meeting.get("province"), meeting.get("city_county")) if value)
    all_sentences: dict[str, list[dict[str, Any]]] = {}
    signals: list[dict[str, Any]] = []
    sentence_count = 0
    mention_count = 0
    paragraph_records: list[dict[str, Any]] = []

    for index, paragraph in enumerate(paragraphs):
        records = _insert_sentence_and_mentions(db, paragraph, context_text)
        all_sentences[paragraph["paragraph_id"]] = records
        sentence_count += len(records)
        mention_count += sum(len(record["mention_ids"]) for record in records)
        existing_issues = _existing_issue_rows(db, paragraph["paragraph_id"])
        manual_issues = []
        for issue in existing_issues:
            try:
                issue_metadata = json.loads(issue.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                issue_metadata = {}
            # Rule-derived segment labels must never promote the first
            # paragraph sentence into a trigger.  Explicit/manual fixtures or
            # human-entered labels remain eligible for the hierarchy.
            if issue_metadata.get("source_kind") == "manual" or not issue_metadata.get("rule_id"):
                manual_issues.append({key: value for key, value in issue.items() if key != "metadata_json"})
        entities, facility_type, project_name, places = _entity_keys(paragraph["text"], meeting_context)
        paragraph_record = {
            **paragraph,
            "paragraph_index": index,
            "agenda_key": _agenda_key(paragraph, meeting.get("meeting_title") or "", meeting.get("agenda_text") or ""),
            "entity_keys": entities,
            "facility_type": facility_type,
            "project_name": project_name,
            "places": places,
        }
        paragraph_records.append(paragraph_record)
        for record in records:
            classification = record["classification"]
            is_signal = bool(
                classification.get("issues")
                or classification.get("standalone_high_precision_hits")
                or (classification.get("solar_related") and classification.get("matched_issue_terms"))
            )
            if is_signal:
                signals.append({**paragraph_record, "sentence": record, "classification": classification})
        if manual_issues and not any(signal["paragraph_id"] == paragraph["paragraph_id"] for signal in signals):
            fallback = records[0] if records else {"sentence_id": None, "mention_ids": [], "text": paragraph["text"]}
            signals.append(
                {
                    **paragraph_record,
                    "sentence": fallback,
                    "classification": {
                        "issues": manual_issues,
                        "matched_issue_terms": [],
                        "solar_related": True,
                        "problem_categories": [],
                        "standalone_high_precision_hits": [],
                    },
                }
            )

    groups: list[list[dict[str, Any]]] = []
    group_scores: list[list[float]] = []
    for signal in signals:
        if not groups:
            groups.append([signal])
            group_scores.append([])
            continue
        previous = groups[-1][-1]
        score, _components = _link_score(previous, signal, paragraph_records)
        if score >= 0.55:
            groups[-1].append(signal)
            group_scores[-1].append(score)
        else:
            groups.append([signal])
            group_scores.append([])

    episode_rows: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    case_evidence_count = 0
    process_event_count = 0
    for group, scores in zip(groups, group_scores):
        first = group[0]
        last = group[-1]
        issue_items = [issue for signal in group for issue in signal["classification"].get("issues", [])]
        issue_codes = sorted(_issue_codes(issue_items))
        categories = sorted(
            {
                issue.get("problem_category")
                or issue.get("metadata", {}).get("problem_category")
                for issue in issue_items
                if issue.get("problem_category") or issue.get("metadata", {}).get("problem_category")
            }
        )
        combined_text = " ".join(signal.get("sentence", {}).get("text", "") for signal in group)
        entity_keys = sorted({key for signal in group for key in signal["entity_keys"]})
        places = [place for signal in group for place in signal.get("places", [])]
        village = next((place.get("normalized_name") for place in places if place.get("place_type") in {"ri", "eup_myeon"}), None)
        address = next((place.get("road_address") or place.get("jibun_address") or place.get("normalized_name") for place in places if place.get("place_type") in {"road_address", "jibun_address"}), village)
        episode_key = f"{document_id}:{first['ordinal']}:{last['ordinal']}:{','.join(issue_codes)}"
        episode_id = stable_id("episode", episode_key)
        grouping_score = min(scores) if scores else 1.0
        confidence = min(0.98, 0.66 + (0.12 if issue_codes else 0) + (0.10 if entity_keys else 0) + (0.05 if len(group) > 1 else 0))
        episode = {
            "episode_id": episode_id,
            "episode_key": episode_key,
            "document_id": document_id,
            # Bounds describe trigger paragraphs only. The ±1 context window
            # is represented explicitly in episode_evidence instead of being
            # hidden inside the episode range.
            "paragraph_start": first["ordinal"],
            "paragraph_end": last["ordinal"],
            "issue_type": "|".join(issue_codes) if issue_codes else "keyword_candidate",
            "issue_types": issue_codes,
            "categories": categories,
            "stance": _stance(group),
            "procedure_stage": _procedure_stage(combined_text),
            "confidence": confidence,
            "grouping_score": grouping_score,
            "entity_keys": entity_keys,
            "facility_type": first["facility_type"],
            "project_name": next((signal.get("project_name") for signal in group if signal.get("project_name")), None),
            "village": village,
            "address": address,
        }
        db.conn.execute(
            """INSERT INTO episodes
               (episode_id, episode_key, document_id, paragraph_start, paragraph_end,
                issue_type, issue_types_json, stance, procedure_stage, confidence,
                grouping_score, grouping_method, review_status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic_v1', 'pending', ?)""",
            (
                episode_id,
                episode_key,
                document_id,
                episode["paragraph_start"],
                episode["paragraph_end"],
                episode["issue_type"],
                _json(issue_codes),
                episode["stance"],
                episode["procedure_stage"],
                confidence,
                grouping_score,
                _json(
                    {
                        "trigger_sentence_ids": [signal["sentence"].get("sentence_id") for signal in group if signal["sentence"].get("sentence_id")],
                        "keyword_mention_ids": [mention_id for signal in group for mention_id in signal["sentence"].get("mention_ids", [])],
                        "entity_keys": entity_keys,
                        "problem_categories": categories,
                        "grouping_scores": scores,
                    }
                ),
            ),
        )

        trigger_ids = {signal["sentence"].get("sentence_id") for signal in group}
        first_index = first["paragraph_index"]
        last_index = last["paragraph_index"]
        context_first = max(0, first_index - 1)
        context_last = min(len(paragraph_records) - 1, last_index + 1)
        for paragraph_index in range(context_first, context_last + 1):
            paragraph = paragraph_records[paragraph_index]
            role = "context_before" if paragraph_index < first_index else "context_after" if paragraph_index > last_index else "episode_context"
            for sentence in all_sentences.get(paragraph["paragraph_id"], []):
                sentence_id = sentence["sentence_id"]
                if sentence_id in trigger_ids:
                    evidence_role = "trigger_sentence"
                    link_confidence = 0.95
                else:
                    evidence_role = role
                    link_confidence = 0.65
                db.conn.execute(
                    """INSERT INTO episode_evidence
                       (episode_id, paragraph_id, sentence_id, evidence_role, link_confidence)
                       VALUES (?, ?, ?, ?, ?)""",
                    (episode_id, paragraph["paragraph_id"], sentence_id, evidence_role, link_confidence),
                )
        episode_rows.append(episode)

    # Cases are created from strong identity keys only.  A specialized
    # facility type by itself (for example, every "수상태양광" mention) is not
    # a unique complaint identity: several municipalities can have unrelated
    # projects of the same type.  Without a project/place identity, each
    # episode therefore gets an isolated pending case.
    for episode in episode_rows:
        place_project_keys = [
            key for key in episode["entity_keys"]
            if key.startswith("place:") or key.startswith("project:")
        ]
        facility_keys = [key for key in episode["entity_keys"] if key.startswith("facility:")]
        identity_keys = place_project_keys + facility_keys if place_project_keys else []
        identity = "|".join(str(key) for key in identity_keys) if identity_keys else f"episode:{episode['episode_id']}"
        case_id = _upsert_case(db, identity, meeting, episode)
        case_ids.add(case_id)
        evidence_rows = db.conn.execute(
            """SELECT paragraph_id, sentence_id, evidence_role, link_confidence
               FROM episode_evidence WHERE episode_id=?""",
            (episode["episode_id"],),
        ).fetchall()
        for evidence in evidence_rows:
            paragraph_id = evidence["paragraph_id"]
            sentence_id = evidence["sentence_id"]
            evidence_role = evidence["evidence_role"]
            db.conn.execute(
                """INSERT OR IGNORE INTO case_evidence
                   (case_evidence_id, case_id, episode_id, paragraph_id, sentence_id,
                    evidence_role, link_confidence, review_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    stable_id("case_evidence", case_id, episode["episode_id"], paragraph_id, sentence_id, evidence_role),
                    case_id,
                    episode["episode_id"],
                    paragraph_id,
                    sentence_id,
                    evidence_role,
                    evidence["link_confidence"],
                ),
            )
            db.conn.execute(
                """INSERT OR IGNORE INTO case_segment
                   (case_id, segment_id, relation_type, confidence, review_status)
                   VALUES (?, ?, 'episode_evidence', ?, 'pending')""",
                (case_id, paragraph_id, evidence["link_confidence"]),
            )
            case_evidence_count += 1
        _infer_case_locations(db, case_id, episode["episode_id"])
        from .process import rebuild_case_process_events

        process_event_count += rebuild_case_process_events(db, case_id, episode["episode_id"])

    _prune_orphan_auto_cases(db)
    candidate_count = _create_case_link_candidates(db, case_ids)
    return {
        "sentences": sentence_count,
        "mentions": mention_count,
        "episodes": len(episode_rows),
        "cases": len(case_ids),
        "case_evidence": case_evidence_count,
        "process_events": process_event_count,
        "case_link_candidates": candidate_count,
    }
