from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Iterable

from .gazetteer import identity_place_is_valid, suspicious_suffix_token, gazetteer_version
from .keywords import HIGH_PRECISION_ISSUE_STANDALONE, active_term_hits, classify_text


REVIEW_VERSION = "case-review-v1:nlp-negation+gazetteer-v1"
EXPLICIT_DISPUTE_TERMS = (
    "민원", "집단민원", "주민민원", "반대", "반발", "갈등", "분쟁", "주민피해",
    "주민불편", "주민요구", "대책위", "소송", "행정심판", "허가취소",
)
HARM_TERMS = (
    "피해", "우려", "훼손", "오염", "위험", "사고", "문제", "토사유출", "산사태",
    "침수", "농업피해", "산림훼손", "생활불편", "전자파",
)
SUPPORT_TERMS = ("찬성", "동의", "수용", "협력", "지원", "환영", "상생", "주민참여", "소득", "수익")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _case_trigger_rows(db: Any, case_id: str) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """SELECT ce.paragraph_id, ce.sentence_id, ce.evidence_role,
                  s.text AS sentence_text, e.stance, e.issue_types_json,
                  m.meeting_title, m.agenda_text, d.title AS document_title,
                  d.document_id
             FROM case_evidence ce
             JOIN episodes e ON e.episode_id=ce.episode_id
             JOIN sentences s ON s.sentence_id=ce.sentence_id
             JOIN source_document d ON d.document_id=e.document_id
             LEFT JOIN meeting m ON m.document_id=d.document_id
            WHERE ce.case_id=? AND ce.evidence_role='trigger_sentence'
            ORDER BY d.document_id, s.paragraph_id, s.sentence_order""",
        (case_id,),
    ).fetchall()
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        key = str(item.get("sentence_id") or f"{item.get('paragraph_id')}:{item.get('sentence_text')}")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _identity_quality(case: dict[str, Any]) -> tuple[float, list[str], bool]:
    key = str(case.get("case_key") or "")
    reasons: list[str] = []
    known_place = False
    place_keys = [part for part in key.split("|") if part.startswith("place:")]
    for place_key in place_keys:
        place_value = place_key.removeprefix("place:")
        if identity_place_is_valid(place_key, case.get("municipality")):
            known_place = True
        else:
            reasons.append("SUSPICIOUS_PLACE_TOKEN")
            if suspicious_suffix_token(place_value.split()[-1] if place_value.split() else place_value):
                reasons.append("UNVERIFIED_PLACE_NAME")
    if known_place:
        return 0.92, reasons, True
    if case.get("project_name"):
        return 0.82, reasons, True
    if key.startswith("episode:"):
        return 0.46, reasons, False
    return 0.35, reasons, False


def _shared_trigger_paragraphs(db: Any, case_id: str) -> int:
    row = db.conn.execute(
        """SELECT COUNT(*) FROM (
                 SELECT ce.paragraph_id
                   FROM case_evidence ce
                  WHERE ce.case_id=? AND ce.evidence_role='trigger_sentence'
                  GROUP BY ce.paragraph_id
                 HAVING (SELECT COUNT(DISTINCT ce2.case_id)
                           FROM case_evidence ce2
                          WHERE ce2.paragraph_id=ce.paragraph_id
                            AND ce2.evidence_role='trigger_sentence') > 1
             )""",
        (case_id,),
    ).fetchone()
    return int(row[0] or 0)


def _review_case(db: Any, case: dict[str, Any]) -> dict[str, Any]:
    triggers = _case_trigger_rows(db, case["case_id"])
    issue_rows: list[dict[str, Any]] = []
    solar_sentences = 0
    high_precision_sentences = 0
    explicit_hits: set[str] = set()
    harm_hits: set[str] = set()
    support_hits: set[str] = set()
    stances: set[str] = set()
    for row in triggers:
        text = str(row.get("sentence_text") or "")
        context = f"{row.get('meeting_title') or ''} {row.get('agenda_text') or ''}".strip()
        classification = classify_text(text, context)
        issue_rows.extend(classification.get("issues", []))
        if classification.get("solar_related"):
            solar_sentences += 1
        if active_term_hits(text, HIGH_PRECISION_ISSUE_STANDALONE):
            high_precision_sentences += 1
        explicit_hits.update(active_term_hits(text, EXPLICIT_DISPUTE_TERMS))
        harm_hits.update(active_term_hits(text, HARM_TERMS))
        support_hits.update(active_term_hits(text, SUPPORT_TERMS))
        stance = str(row.get("stance") or "")
        if stance in {"opposition", "support", "mixed"}:
            stances.add(stance)

    issue_codes = {str(issue.get("issue_code")) for issue in issue_rows if issue.get("issue_code")}
    subject_score = min(
        1.0,
        (0.62 if solar_sentences else 0.0)
        + (0.30 if high_precision_sentences else 0.0)
        + (0.08 if issue_codes else 0.0),
    )
    dispute_score = min(
        1.0,
        (0.48 if explicit_hits else 0.0)
        + (0.30 if harm_hits else 0.0)
        + (0.22 if "opposition" in stances or "mixed" in stances else 0.0),
    )
    identity_score, reasons, stable_identity = _identity_quality(case)
    shared_count = _shared_trigger_paragraphs(db, case["case_id"])
    doc_count = int(case.get("document_count") or 0)
    separation_score = 0.92
    if shared_count:
        separation_score = 0.38
        reasons.append("SHARED_TRIGGER_PARAGRAPH")
    elif not stable_identity and doc_count > 1:
        separation_score = 0.48
        reasons.append("MULTI_DOCUMENT_WEAK_IDENTITY")
    elif not stable_identity:
        separation_score = 0.68
        reasons.append("EPISODE_SCOPED_IDENTITY")

    quality_score = round(
        0.38 * subject_score + 0.34 * dispute_score + 0.16 * identity_score + 0.12 * separation_score,
        4,
    )
    standalone_glare = bool(active_term_hits(" ".join(row.get("sentence_text") or "" for row in triggers), ("빛반사", "반사광", "눈부심")))
    generic_support_only = bool(support_hits) and not explicit_hits and not harm_hits and not standalone_glare
    if not subject_score:
        decision = "rejected"
        reasons.insert(0, "NO_EXACT_SOLAR_TRIGGER")
    elif generic_support_only and not high_precision_sentences:
        decision = "rejected"
        reasons.insert(0, "POLICY_OR_SUPPORT_RECORD")
    elif reasons:
        decision = "needs_review"
    elif standalone_glare or high_precision_sentences or explicit_hits or harm_hits:
        decision = "eligible"
    else:
        decision = "needs_review"
        reasons.insert(0, "NO_EXPLICIT_DISPUTE_MARKER")
    if not explicit_hits and not high_precision_sentences and not harm_hits and decision != "rejected":
        reasons.append("NO_EXPLICIT_DISPUTE_MARKER")

    return {
        "case_id": case["case_id"],
        "decision": decision,
        "quality_score": quality_score,
        "subject_score": round(subject_score, 4),
        "dispute_score": round(dispute_score, 4),
        "identity_score": round(identity_score, 4),
        "separation_score": round(separation_score, 4),
        "evidence_paragraph_count": int(case.get("evidence_paragraph_count") or 0),
        "trigger_sentence_count": len(triggers),
        "reason_codes": sorted(set(reasons)),
        "metadata": {
            "solar_sentence_count": solar_sentences,
            "high_precision_sentence_count": high_precision_sentences,
            "explicit_dispute_hits": sorted(explicit_hits),
            "harm_hits": sorted(harm_hits),
            "support_hits": sorted(support_hits),
            "issue_codes": sorted(issue_codes),
            "document_count": doc_count,
            "shared_trigger_paragraph_count": shared_count,
            "gazetteer_version": gazetteer_version(),
        },
    }


def rebuild_case_reviews(db: Any, case_ids: Iterable[str] | None = None) -> dict[str, int]:
    """Re-score every inferred case and create auditable review tasks."""
    if case_ids is None:
        rows = db.conn.execute(
            """SELECT c.case_id, c.case_key, c.municipality, c.project_name,
                      COUNT(DISTINCT ce.paragraph_id) AS evidence_paragraph_count,
                      COUNT(DISTINCT e.document_id) AS document_count
                 FROM conflict_case c
                 LEFT JOIN case_evidence ce ON ce.case_id=c.case_id
                 LEFT JOIN episodes e ON e.episode_id=ce.episode_id
                GROUP BY c.case_id, c.case_key, c.municipality, c.project_name"""
        ).fetchall()
    else:
        ids = list(dict.fromkeys(str(value) for value in case_ids if value))
        if not ids:
            return {"cases": 0, "eligible": 0, "needs_review": 0, "rejected": 0}
        placeholders = ",".join("?" for _ in ids)
        rows = db.conn.execute(
            f"""SELECT c.case_id, c.case_key, c.municipality, c.project_name,
                       COUNT(DISTINCT ce.paragraph_id) AS evidence_paragraph_count,
                       COUNT(DISTINCT e.document_id) AS document_count
                  FROM conflict_case c
                  LEFT JOIN case_evidence ce ON ce.case_id=c.case_id
                  LEFT JOIN episodes e ON e.episode_id=ce.episode_id
                 WHERE c.case_id IN ({placeholders})
                 GROUP BY c.case_id, c.case_key, c.municipality, c.project_name""",
            ids,
        ).fetchall()

    counts = defaultdict(int)
    for row in rows:
        result = _review_case(db, dict(row))
        db.conn.execute(
            """INSERT INTO case_review
                  (case_id, decision, quality_score, subject_score, dispute_score,
                   identity_score, separation_score, evidence_paragraph_count,
                   trigger_sentence_count, reason_codes_json, review_version,
                   decision_source, metadata_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic_gate', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(case_id) DO UPDATE SET
                   decision=excluded.decision,
                   quality_score=excluded.quality_score,
                   subject_score=excluded.subject_score,
                   dispute_score=excluded.dispute_score,
                   identity_score=excluded.identity_score,
                   separation_score=excluded.separation_score,
                   evidence_paragraph_count=excluded.evidence_paragraph_count,
                   trigger_sentence_count=excluded.trigger_sentence_count,
                   reason_codes_json=excluded.reason_codes_json,
                   review_version=excluded.review_version,
                   decision_source=excluded.decision_source,
                   metadata_json=excluded.metadata_json,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                result["case_id"], result["decision"], result["quality_score"], result["subject_score"],
                result["dispute_score"], result["identity_score"], result["separation_score"],
                result["evidence_paragraph_count"], result["trigger_sentence_count"],
                _json(result["reason_codes"]), REVIEW_VERSION, _json(result["metadata"]),
            ),
        )
        case_review_status = "verified" if result["decision"] == "eligible" else "rejected" if result["decision"] == "rejected" else "pending"
        db.conn.execute(
            "UPDATE conflict_case SET review_status=?, updated_at=CURRENT_TIMESTAMP WHERE case_id=?",
            (case_review_status, result["case_id"]),
        )
        for reason in result["reason_codes"]:
            priority = 1 if reason in {"SUSPICIOUS_PLACE_TOKEN", "UNVERIFIED_PLACE_NAME", "SHARED_TRIGGER_PARAGRAPH"} else 2
            db.conn.execute(
                """INSERT INTO review_task
                      (review_task_id, target_type, target_id, reason_code, priority, status, decision, note)
                   VALUES (?, 'case', ?, ?, ?, 'open', ?, ?)
                   ON CONFLICT(target_type, target_id, reason_code) DO UPDATE SET
                      priority=excluded.priority, decision=excluded.decision, note=excluded.note""",
                (
                    f"case-review:{result['case_id']}:{reason}", result["case_id"], reason, priority,
                    result["decision"], "자동 검토: " + ", ".join(result["reason_codes"]),
                ),
            )
        counts[result["decision"]] += 1
    counts["cases"] = sum(counts[key] for key in ("eligible", "needs_review", "rejected"))
    return dict(counts)
