"""Deterministic extraction of the administrative process around a case.

The meeting hierarchy already captures issue paragraphs and cases.  This
module adds a second, deliberately smaller layer: complaint, inquiry,
inspection, consultation, decision, action, and recurrence events.  It is
designed as the pre-LLM fallback; an eventual model can propose additional
events, but every event still needs an evidence paragraph and review status.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from .db import stable_id


EVENT_PATTERNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("complaint_received", (r"민원\s*(?:이|을|은|접수|제기|발생|들어)", r"진정서", r"건의서", r"민원인"), "민원 접수·제기"),
    ("inquiry_or_request", (r"질의", r"질문", r"요청", r"요구", r"설명해", r"답변"), "질의·요청"),
    ("investigation_or_review", (r"현장\s*(?:확인|점검|방문)", r"조사", r"점검", r"측정", r"검토", r"확인하"), "조사·검토"),
    ("resident_consultation", (r"설명회", r"공청회", r"협의", r"간담회", r"의견수렴", r"주민\s*(?:의견|동의)"), "주민 설명·협의"),
    ("administrative_response", (r"회신", r"답변", r"대책", r"방안을\s*(?:마련|검토)", r"조치하", r"검토하겠"), "행정 답변·대응"),
    ("permit_or_authorization", (r"(?:개발행위|발전사업|건축|산지|농지)?허가", r"인허가", r"심의", r"허가\s*(?:신청|절차)"), "인허가·심의"),
    ("decision_or_disposition", (r"가결", r"부결", r"의결", r"반려", r"불허", r"보류", r"유보", r"승인", r"취소", r"결정"), "결정·처분"),
    ("mitigation_or_action", (r"차폐", r"보완", r"시정", r"개선", r"배수", r"이전", r"철거", r"조정", r"대책"), "보완·조치"),
    ("follow_up_or_recurrence", (r"재민원", r"재발", r"이후에도", r"다시\s*(?:민원|제기|발생)", r"후속"), "후속·재발"),
)

ACTOR_TERMS = (
    ("resident", "주민"),
    ("complainant", "민원인"),
    ("council_member", "의원"),
    ("administration", "행정기관"),
    ("public_officer", "공무원"),
    ("business_operator", "사업자"),
)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?다요])\s+|\n+", text or "") if part.strip()]


def _event_date(text: str, fallback: str | None) -> str | None:
    patterns = (
        r"(20\d{2})\s*[년./-]\s*(\d{1,2})\s*[월./-]\s*(\d{1,2})\s*일?",
        r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if not match:
            continue
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return fallback
    return fallback


def _actor(text: str, speaker_name: str | None = None, speaker_role: str | None = None) -> str | None:
    for code, label in ACTOR_TERMS:
        if label in text:
            return code
    if speaker_role:
        return f"speaker:{speaker_role}"
    if speaker_name:
        return f"speaker:{speaker_name}"
    return None


def _outcome(event_type: str, text: str) -> tuple[str | None, str]:
    if re.search(r"반려|불허|부결|취소", text):
        return "rejected", "confirmed"
    if re.search(r"가결|승인|허가(?:를|가)?\s*(?:받|함|완료)|의결", text):
        return "approved", "confirmed"
    if re.search(r"보류|유보", text):
        return "pending", "confirmed"
    if re.search(r"재발|재민원|이후에도|다시", text):
        return "recurred", "confirmed"
    if event_type in {"administrative_response", "investigation_or_review", "resident_consultation", "mitigation_or_action"} and re.search(r"완료|마쳤|시행했|진행했|실시했|확인했", text):
        return "completed", "confirmed"
    if re.search(r"하기로|예정|검토하겠|추진하겠|요청했|요구했", text):
        return "planned", "inferred"
    if event_type in {"complaint_received", "inquiry_or_request"}:
        return "reported", "confirmed"
    return "observed", "confirmed"


def extract_process_events(
    text: str,
    meeting_date: str | None = None,
    *,
    speaker_name: str | None = None,
    speaker_role: str | None = None,
) -> list[dict[str, Any]]:
    """Extract one or more process events from a paragraph.

    A sentence can legitimately contain several events, e.g. a complaint was
    received and a site inspection was promised.  The returned evidence is
    always the sentence containing the matched process expression.
    """

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for sentence in _sentences(text):
        for event_type, patterns, stage_label in EVENT_PATTERNS:
            matched = next((pattern for pattern in patterns if re.search(pattern, sentence)), None)
            if not matched:
                continue
            key = (event_type, sentence)
            if key in seen:
                continue
            seen.add(key)
            outcome, certainty = _outcome(event_type, sentence)
            events.append(
                {
                    "event_type": event_type,
                    "stage_label": stage_label,
                    "event_date": _event_date(sentence, meeting_date),
                    "actor": _actor(sentence, speaker_name, speaker_role),
                    "action_text": sentence,
                    "outcome": outcome,
                    "certainty": certainty,
                    "confidence": 0.86 if matched and outcome != "planned" else 0.72,
                    "evidence_text": sentence,
                    "extraction_method": "deterministic_process_v1",
                    "metadata": {"matched_pattern": matched, "process_stage": stage_label},
                }
            )
    return events


def _document_is_synthetic(metadata_json: str | None) -> bool:
    try:
        metadata = json.loads(metadata_json or "{}")
    except (TypeError, ValueError):
        return False
    return bool(metadata.get("fixture") or metadata.get("data_origin") == "synthetic")


def rebuild_case_process_events(db: Any, case_id: str, episode_id: str) -> int:
    """Replace deterministic process events for one case/episode."""

    db.conn.execute("DELETE FROM case_process_event WHERE case_id=? AND episode_id=?", (case_id, episode_id))
    rows = db.conn.execute(
        """SELECT DISTINCT ee.paragraph_id, s.text_original, m.meeting_date,
                  sp.name AS speaker_name, sp.role AS speaker_role,
                  d.metadata_json
             FROM episode_evidence ee
             JOIN meeting_segment s ON s.segment_id=ee.paragraph_id
             LEFT JOIN meeting m ON m.meeting_id=s.meeting_id
             LEFT JOIN speaker sp ON sp.speaker_id=s.speaker_id
             JOIN source_document d ON d.document_id=s.document_id
            WHERE ee.episode_id=?
            ORDER BY s.ordinal, ee.sentence_id""",
        (episode_id,),
    ).fetchall()
    inserted = 0
    for row in rows:
        for event in extract_process_events(
            row["text_original"] or "",
            row["meeting_date"],
            speaker_name=row["speaker_name"],
            speaker_role=row["speaker_role"],
        ):
            process_event_id = stable_id("case_process_event", case_id, episode_id, row["paragraph_id"], event["event_type"], event["action_text"])
            db.conn.execute(
                """INSERT INTO case_process_event
                   (process_event_id, case_id, episode_id, paragraph_id,
                    event_type, event_date, actor, action_text, outcome,
                    certainty, confidence, evidence_text, extraction_method,
                    review_status, data_origin, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'deterministic_process_v1', 'pending', ?, ?)""",
                (
                    process_event_id,
                    case_id,
                    episode_id,
                    row["paragraph_id"],
                    event["event_type"],
                    event["event_date"],
                    event["actor"],
                    event["action_text"],
                    event["outcome"],
                    event["certainty"],
                    event["confidence"],
                    event["evidence_text"],
                    "synthetic" if _document_is_synthetic(row["metadata_json"]) else "meeting_record",
                    json.dumps(event["metadata"], ensure_ascii=False),
                ),
            )
            inserted += 1
    return inserted
