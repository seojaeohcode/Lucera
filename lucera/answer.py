"""Claude-backed answer generation over an already-computed evidence pack.

The rule engine, the distance maths, the permit aggregation and the retrieval
all run before this module is reached.  The model is therefore never asked to
decide anything: it receives the finished pack and writes the Korean prose for
it.  Two guards enforce that boundary, and both fall back to the deterministic
template rather than shipping an unverified answer.

* **Citation guard** - every reason must cite ids that exist in the pack.
* **Numeric guard** - every number in the prose must already appear in the
  pack.  A model that computes its own distance or count is rejected outright,
  because a plausible wrong number is the failure mode that would actually
  mislead a 담당자.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_TIMEOUT_SECONDS = 40.0
# Korean prose costs roughly twice the tokens of the equivalent English, and a
# truncated reply is unparseable JSON rather than a short answer.
DEFAULT_MAX_TOKENS = 8000

# How much of the pack the model is allowed to see. Trimming is not only about
# cost: a smaller pack makes the numeric guard's whitelist tight, so a fabricated
# figure has nowhere to hide.
MAX_REASONS = 5
MAX_QUOTES_PER_REASON = 1
MAX_QUOTE_CHARS = 220
MAX_TIMELINE = 6
MAX_PERMIT_SAMPLES = 3
MAX_MAP_OBSERVATIONS = 2
MAX_CHECKLIST = 4
MAX_LIMITATIONS = 1
MAX_TITLE_CHARS = 52
MAX_BODY_CHARS = 180
MAX_LIST_ITEM_CHARS = 100

SYSTEM_PROMPT = """당신은 태양광 발전시설 설치 예정지 사전점검 보고서를 쓰는 행정 실무 보조자입니다.

당신에게 주어지는 evidence pack은 이미 계산과 검색이 끝난 결과입니다.
당신의 역할은 판단이 아니라 서술입니다.

반드시 지킬 것:
1. evidence pack 안에 있는 값만 사용합니다. 거리, 면적, 용량, 건수, 비율, 날짜를
   새로 계산하거나 반올림하거나 추정하지 않습니다. 필요한 숫자는 pack에 적힌
   형태 그대로 옮겨 씁니다.
2. pack에 없는 법령·조례·판례·지명·사업명을 언급하지 않습니다.
3. 각 사유의 evidence_ids에는 pack의 allowed_evidence_ids 목록에 있는 문자열만
   그대로 넣습니다. 쟁점 코드나 지역명 같은 다른 값은 넣지 않습니다.
   넣을 수 있는 근거가 없으면 그 사유를 쓰지 않습니다.
4. "설치 가능" 또는 "설치 불가"를 단정하지 않습니다. 조례 기준 충족 여부는
   rule_analysis가 이미 판정한 상태값을 그대로 전달하고, 종합 판단은
   담당자가 확인할 재료로 제시합니다.
5. 허가·불허를 권고하지 않습니다. "허가하지 마십시오" 같은 문장을 쓰지 않습니다.
6. 위험도 점수처럼 근거 없는 정량화를 만들지 않습니다.
7. 주민 반대 기록은 반려 근거가 아니라 협의가 필요하다는 신호로 서술합니다.
8. status가 check_required인 항목은 미충족이 아니라 확인이 필요한 상태로 씁니다.

문체와 분량:
9. 결론은 한 문장, 핵심 근거는 최대 5개, 영상 관찰은 최대 2개, 다음 확인은 최대 4개,
   한계는 최대 1개만 씁니다.
   연속 대화에서는 conversation_context를 참고하되, 가장 최근 질문에만 답하고
   이전 답변을 통째로 반복하지 않습니다.
10. 각 근거와 관찰은 한두 문장, 가능하면 120자 이내로 씁니다. 같은 뜻을 반복하지 않습니다.
11. "이 항목은", "상태로 남아 있습니다", "담당자가 재확인할 필요가 있습니다"를 반복하지
   말고, "150m로 기준 300m 미충족", "현장 확인 필요"처럼 직접적으로 씁니다.
12. 보고서 머리말, 인사말, 자기 설명, "주요 판단 재료", "확인된 처리 과정" 같은 메타 문구를
   만들지 않습니다. evidence_ids는 JSON 필드에만 넣고 본문에는 근거 ID를 쓰지 않습니다.

지도 이미지가 함께 주어지는 경우:
13. 이미지는 설치 예정지와 그 주변의 항공영상·배경지도입니다. 붉은 표식이 예정지입니다.
14. 이미지에서 본 것은 map_observations에만 씁니다. reasons 본문에는 넣지 않습니다.
15. 이미지에서 거리·개수·면적·높이를 재거나 세지 않습니다. map_observations에는
    숫자를 쓰지 않습니다. "주거지까지 약 100m"가 아니라 "북측에 주택이 모여 있음"처럼
    방향과 배치만 서술합니다.
16. 이미지에서 본 것은 사실이 아니라 관찰입니다. 확정 표현 대신 "보입니다",
    "것으로 보입니다"를 쓰고, 현장 확인이 필요하다는 점을 함께 적습니다.
17. 이미지에 보이지 않는 것을 추측해서 쓰지 않습니다. 영상이 흐리거나 판독이
    어려우면 그렇게 적습니다.

사용자가 올린 현장 이미지가 함께 주어지는 경우:
18. 현장 이미지에서 관찰한 내용도 관찰로만 서술하고, 사진만으로 거리·면적·허가
    상태를 확정하지 않습니다. 이미지를 읽지 못하면 그 사실을 한계로 적습니다.

출력은 아래 JSON 스키마만 반환합니다. 설명이나 코드블록 표시 없이 JSON만 씁니다.

{
  "conclusion_sentence": "결론 상태를 한 문장으로. pack의 conclusion_label을 그대로 사용.",
  "reasons": [
    {
      "title": "짧은 제목",
      "body": "무엇이 확인됐고 어떤 확인이 필요한지 한두 문장으로.",
      "evidence_ids": ["allowed_evidence_ids 목록에 있는 값만"]
    }
  ],
  "map_observations": [
    {
      "observation": "지도에서 관찰한 주변 상황. 숫자 없이 방향·배치·지형만.",
      "relevance": "입지 검토에서 의미가 있는지 짧게"
    }
  ],
  "checklist": ["담당자가 다음에 확인할 항목", "..."],
  "limitations": ["이 답변으로 확정할 수 없는 것"]
}

reasons는 가장 중요한 3~5개만 씁니다. 이미지가 없으면 map_observations는 빈 배열로 둡니다."""


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_setting(names: tuple[str, ...], env_path: Path | None = None) -> str | None:
    """Environment first, then the project `.env`, so a deployment can use a
    secret manager without editing the repository."""

    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    path = env_path or Path(__file__).resolve().parents[1] / ".env"
    values = _load_env_file(path)
    for name in names:
        if values.get(name):
            return values[name]
    return None


def resolve_api_key(env_path: Path | None = None) -> str | None:
    return _resolve_setting(("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "claude_key"), env_path)


def resolve_workspace_id(env_path: Path | None = None) -> str | None:
    """Identity-linked API keys must name the workspace the call acts in.

    Console keys do not need this, so the header is only sent when a value is
    configured; sending an empty one would fail a request that would otherwise
    succeed.
    """

    return _resolve_setting(
        ("ANTHROPIC_WORKSPACE_ID", "CLAUDE_WORKSPACE_ID", "claude_workspace_id"), env_path
    )


NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number_forms(value: Any) -> set[str]:
    """Every textual form a pack number may legitimately take in the prose."""

    if isinstance(value, bool) or value is None:
        return set()
    if not isinstance(value, (int, float)):
        return set()
    forms = {str(value)}
    number = float(value)
    if number.is_integer():
        integer = int(number)
        forms.add(str(integer))
        forms.add(f"{integer:,}")
    for digits in (0, 1, 2):
        rendered = f"{number:,.{digits}f}"
        forms.add(rendered)
        forms.add(rendered.replace(",", ""))
    percent = number * 100
    if abs(percent) < 1e9:
        for digits in (0, 1):
            forms.add(f"{percent:,.{digits}f}")
            forms.add(f"{percent:,.{digits}f}".replace(",", ""))
    return forms


# Digits inside an id or a URL are not facts the prose may quote. Letting them
# into the whitelist would authorise almost any number, so both the field names
# that carry ids and anything shaped like one are skipped.
ID_FIELDS = frozenset(
    {
        "evidence_id",
        "case_id",
        "episode_id",
        "process_event_id",
        "paragraph_id",
        "project_id",
        "rule_id",
        "record_id",
        "source_url",
        "allowed_evidence_ids",
        "evidence_ids",
    }
)
ID_LIKE_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_.:\-]{8,}$")


def _collect_allowed_numbers(node: Any, allowed: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ID_FIELDS:
                continue
            _collect_allowed_numbers(value, allowed)
    elif isinstance(node, list):
        for value in node:
            _collect_allowed_numbers(value, allowed)
    elif isinstance(node, str):
        if ID_LIKE_RE.match(node.strip()) or "://" in node:
            return
        for match in NUMBER_RE.findall(node):
            allowed.add(match)
            allowed.add(match.replace(",", ""))
    else:
        allowed.update(_number_forms(node))


def _unverified_numbers(text: str, allowed: set[str]) -> list[str]:
    unverified = []
    for match in NUMBER_RE.findall(text or ""):
        if match in allowed or match.replace(",", "") in allowed:
            continue
        unverified.append(match)
    return unverified


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Parse the reply's JSON object, tolerating fences or a stray preamble.

    Not every model can be prefilled with an opening brace, so the reply may
    arrive wrapped in ```json or preceded by a sentence. Scan for the first
    balanced object rather than failing the whole answer on formatting.
    """

    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _clip_text(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.!?;:·")
    return f"{clipped}…"


def _short_body(value: Any, max_chars: int = MAX_BODY_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for before, after in (
        ("이 항목은 미충족이 아니라 확인이 필요한 상태로 남아 있습니다.", "미충족이 아니라 확인 필요."),
        ("이 항목은 fail로 판정되어 있어 담당자의 추가 확인이 필요합니다.", "기준 미충족으로 현장 확인 필요."),
        ("담당자가 재확인할 필요가 있습니다.", "현장 확인 필요."),
        ("후속 확인이 필요한 상태로 남아 있습니다.", "후속 확인 필요."),
    ):
        text = text.replace(before, after)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return _clip_text(" ".join(sentences[:2] or [text]), max_chars)


def _compact_structured(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize Claude's prose shape before validation and rendering.

    The model still supplies the grounded facts, but the application owns the
    amount and shape of text that reaches a 담당자. This keeps a verbose model
    response from turning the result card into a long report.
    """

    compact: dict[str, Any] = {
        "conclusion_sentence": _clip_text(value.get("conclusion_sentence"), 90),
        "reasons": [],
        "map_observations": [],
        "checklist": [],
        "limitations": [],
    }
    seen_reasons: set[tuple[str, str]] = set()
    for item in value.get("reasons") or []:
        if not isinstance(item, dict):
            continue
        title = _clip_text(item.get("title"), MAX_TITLE_CHARS)
        body = _short_body(item.get("body"))
        evidence_ids = list(dict.fromkeys(str(entry) for entry in (item.get("evidence_ids") or []) if entry))[:2]
        identity = (title, body)
        if not title or not body or not evidence_ids or identity in seen_reasons:
            continue
        seen_reasons.add(identity)
        compact["reasons"].append({"title": title, "body": body, "evidence_ids": evidence_ids})
        if len(compact["reasons"]) >= MAX_REASONS:
            break

    for item in value.get("map_observations") or []:
        if not isinstance(item, dict):
            continue
        observation = _short_body(item.get("observation"), 130)
        relevance = _short_body(item.get("relevance"), 100)
        if observation:
            compact["map_observations"].append({"observation": observation, "relevance": relevance})
        if len(compact["map_observations"]) >= MAX_MAP_OBSERVATIONS:
            break

    for key, limit in (("checklist", MAX_CHECKLIST), ("limitations", MAX_LIMITATIONS)):
        seen: set[str] = set()
        for item in value.get(key) or []:
            text = _short_body(item, MAX_LIST_ITEM_CHARS)
            if text and text not in seen:
                compact[key].append(text)
                seen.add(text)
            if len(compact[key]) >= limit:
                break
    return compact


MAX_IMAGES = 3
MAX_LAYER_FEATURES = 5
# The real column names, read off live responses on 2026-09-04: the zoning
# layer names the zone in `uname`, and the cadastral layer carries `jibun`
# ("1 답"), `addr` and the posted land value `jiga` (원/㎡).
LAYER_ATTRIBUTE_HINTS = ("uname", "jibun", "addr", "jiga", "pnu", "sigg_name", "gosi_year")


def _map_context_summary(map_context: dict[str, Any]) -> dict[str, Any]:
    """What the model is told about the imagery, alongside the images."""

    if not map_context.get("requested"):
        return {"available": False, "reason": map_context.get("reason")}
    images = map_context.get("images") or []
    layers = []
    for layer in map_context.get("layers") or []:
        features = []
        for row in (layer.get("features") or [])[:MAX_LAYER_FEATURES]:
            keep = {key: value for key, value in row.items() if key.lower() in LAYER_ATTRIBUTE_HINTS}
            features.append(keep or dict(list(row.items())[:4]))
        layers.append(
            {
                "label": layer.get("label"),
                "data": layer.get("data"),
                "buffer_m": layer.get("buffer_m"),
                "count": layer.get("count"),
                "features": features,
            }
        )
    parcel = map_context.get("parcel") or {}
    return {
        "available": bool(images or layers or parcel),
        "parcel": {key: value for key, value in parcel.items() if key in LAYER_ATTRIBUTE_HINTS} or None,
        "images": [
            {"kind": image.get("kind"), "label": image.get("label"), "approx_extent_m": image.get("approx_extent_m")}
            for image in images[:MAX_IMAGES]
        ],
        "layers": layers,
        "note": "이미지는 관찰용입니다. 거리·개수는 rule_checks와 permit_summary의 값만 사용하십시오.",
        "failed_requests": len(map_context.get("errors") or []),
    }


def _image_blocks(map_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Load cached PNGs as Anthropic image blocks, skipping anything unreadable."""

    blocks: list[dict[str, Any]] = []
    for image in (map_context.get("images") or [])[:MAX_IMAGES]:
        path = Path(str(image.get("path") or ""))
        if not path.exists():
            continue
        try:
            payload = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        blocks.append(
            {
                "type": "text",
                "text": f"[{image.get('label') or image.get('kind')}] 가로 약 {image.get('approx_extent_m')}m 범위, 중앙 표식이 설치 예정지",
            }
        )
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": str(image.get("media_type") or "image/png"),
                    "data": payload,
                },
            }
        )
    return blocks


def _user_image_blocks(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one browser-uploaded image into an Anthropic image block."""

    image = pack.get("user_image")
    if not isinstance(image, dict):
        return []
    data = str(image.get("data") or "")
    media_type = str(image.get("media_type") or "").lower()
    if not data or media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return []
    return [
        {"type": "text", "text": "[사용자 첨부 현장 이미지] 사진은 관찰 보조 자료이며 실측·허가 판정 근거가 아닙니다."},
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
    ]


def build_prompt_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Trim the full pack down to what the prose actually needs."""

    analysis = pack.get("analysis", {})
    rule_analysis = analysis.get("rule_analysis", {})
    permit = analysis.get("permit_analysis", {})

    reasons = []
    for card in (analysis.get("reason_cards") or [])[:MAX_REASONS]:
        evidence = []
        for item in (card.get("evidence") or [])[:MAX_QUOTES_PER_REASON]:
            evidence.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "case_id": item.get("case_id"),
                    "meeting_date": item.get("meeting_date"),
                    "region": item.get("region") or item.get("city_county"),
                    "quote": (item.get("quote") or item.get("text_original") or "")[:MAX_QUOTE_CHARS],
                    "source_url": item.get("source_url"),
                }
            )
        reasons.append(
            {
                "reason": card.get("reason"),
                "category": card.get("category"),
                "confidence": card.get("confidence"),
                "next_check": card.get("next_check"),
                "evidence": evidence,
            }
        )

    grounding = pack.get("grounding", {})
    allowed_evidence_ids = list(
        dict.fromkeys(
            [str(value) for value in (grounding.get("evidence_ids") or [])]
            + [str(value) for value in (grounding.get("rule_ids") or [])]
            + [str(value) for value in (grounding.get("process_event_ids") or [])]
            + [str(value) for value in (grounding.get("permit_project_ids") or [])]
        )
    )

    return {
        "allowed_evidence_ids": allowed_evidence_ids,
        "input": {
            key: pack.get("input", {}).get(key)
            for key in (
                "address",
                "site_area_sqm",
                "installation_area_sqm",
                "capacity_kw",
                "nearest_residence_m",
                "nearest_road_m",
                "radius_m",
                "as_of",
                "message",
            )
        },
        "conversation_context": [
            {
                "role": item.get("role"),
                "content": str(item.get("content") or "")[:500],
            }
            for item in (pack.get("input", {}).get("conversation_context") or [])[-6:]
            if isinstance(item, dict)
        ],
        "location": {
            key: pack.get("location", {}).get(key)
            for key in ("normalized_address", "city_county", "eup_myeon", "ri", "precision", "provider")
        },
        "conclusion": analysis.get("conclusion"),
        "conclusion_label": analysis.get("conclusion_label"),
        "rule_checks": [
            {
                "rule_id": check.get("rule_id"),
                "rule_name": check.get("rule_name"),
                "status": check.get("status"),
                "observed_value": check.get("observed_value"),
                "threshold_value": check.get("threshold_value"),
                "unit": check.get("unit"),
                "reason": check.get("reason"),
                "effective_from": check.get("effective_from"),
                "source": check.get("source"),
            }
            for check in rule_analysis.get("checks", [])
        ],
        "issue_counts": analysis.get("issue_counts"),
        "reasons": reasons,
        "timeline": (analysis.get("timeline") or [])[:MAX_TIMELINE],
        "permit_summary": {
            "count": permit.get("count"),
            "total_capacity_kw": permit.get("total_capacity_kw"),
            "operating_count": permit.get("operating_count"),
            "operation_rate": permit.get("operation_rate"),
            "distance_search_used": permit.get("distance_search_used"),
            "samples": [
                {
                    "facility_name": item.get("facility_name"),
                    "capacity_kw": item.get("capacity_kw"),
                    "permit_date": item.get("permit_date"),
                    "operation_status": item.get("operation_status"),
                    "distance_m": item.get("distance_m"),
                }
                for item in (permit.get("projects") or [])[:MAX_PERMIT_SAMPLES]
            ],
        },
        "solverton_context": pack.get("analysis", {}).get("solverton_context") or {},
        "limitations": analysis.get("limitations"),
        "map_context": _map_context_summary(pack.get("map_context") or {}),
    }


def _render(structured: dict[str, Any], pack: dict[str, Any]) -> str:
    analysis = pack.get("analysis", {})
    lines = ["결론", structured["conclusion_sentence"].strip()]
    reasons = structured.get("reasons") or []
    if reasons:
        lines.extend(["", "핵심 근거"])
        for reason in reasons:
            lines.append(f"- {str(reason.get('title') or '').strip()}: {str(reason.get('body') or '').strip()}")
    observations = [item for item in (structured.get("map_observations") or []) if isinstance(item, dict)]
    if observations:
        lines.extend(["", "영상 관찰 (실측 아님)"])
        for observation in observations:
            line = f"- {str(observation.get('observation') or '').strip()}"
            relevance = str(observation.get("relevance") or "").strip()
            if relevance:
                line += f" · {relevance}"
            lines.append(line)
    checklist = structured.get("checklist") or []
    if checklist:
        lines.extend(["", "다음 확인"])
        lines.extend(f"- {str(item).strip()}" for item in checklist)
    limitations = structured.get("limitations") or analysis.get("limitations") or []
    if limitations:
        lines.extend(["", f"참고: {str(limitations[0]).strip()}"])
    lines.extend(["", "※ 사전점검 참고자료이며 허가·불허를 확정하지 않습니다."])
    return "\n".join(lines)


class ClaudeAnswerGenerator:
    """Renders the pack with Claude, falling back to `fallback` on any doubt."""

    def __init__(
        self,
        fallback: Any,
        *,
        api_key: str | None = None,
        workspace_id: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.fallback = fallback
        self.api_key = api_key if api_key is not None else resolve_api_key()
        self.workspace_id = workspace_id if workspace_id is not None else resolve_workspace_id()
        self.model = model or os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)
        self.timeout = timeout if timeout is not None else float(os.getenv("CLAUDE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        self.max_tokens = max_tokens
        self.last_stop_reason: str | None = None
        self.last_status: dict[str, Any] = {"mode": "not_run"}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _call(self, prompt_pack: dict[str, Any], image_blocks: list[dict[str, Any]] | None = None) -> str:
        # Images first, then the pack: the model reads the site before it reads
        # the record, which is the order the observations are meant to follow.
        content: list[dict[str, Any]] = list(image_blocks or [])
        content.append(
            {
                "type": "text",
                "text": (
                    "다음 evidence pack만 사용해 사전점검 보고서 JSON을 작성하세요.\n"
                    "JSON 객체 하나만 출력하고 다른 문장은 쓰지 마세요.\n\n"
                    + json.dumps(prompt_pack, ensure_ascii=False, indent=2)
                ),
            }
        )
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self.workspace_id:
            headers["anthropic-workspace-id"] = self.workspace_id
        request = urllib.request.Request(ANTHROPIC_ENDPOINT, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.last_stop_reason = payload.get("stop_reason")
        parts = [block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"]
        return "".join(parts)

    def _validate(self, structured: dict[str, Any], prompt_pack: dict[str, Any], pack: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        if not isinstance(structured.get("conclusion_sentence"), str) or not structured["conclusion_sentence"].strip():
            problems.append("conclusion_sentence_missing")
        reasons = structured.get("reasons")
        if not isinstance(reasons, list) or not reasons:
            problems.append("reasons_missing")
            reasons = []

        known_ids = set(pack.get("grounding", {}).get("evidence_ids") or [])
        known_ids |= set(pack.get("grounding", {}).get("rule_ids") or [])
        known_ids |= set(pack.get("grounding", {}).get("process_event_ids") or [])
        known_ids |= set(pack.get("grounding", {}).get("permit_project_ids") or [])
        for reason in reasons:
            if not isinstance(reason, dict):
                problems.append("reason_not_object")
                continue
            ids = [str(value) for value in (reason.get("evidence_ids") or [])]
            if not ids:
                problems.append("reason_without_citation")
            unknown = [value for value in ids if value not in known_ids]
            if unknown:
                problems.append(f"unknown_evidence_id:{unknown[0]}")

        allowed: set[str] = set()
        _collect_allowed_numbers(prompt_pack, allowed)
        prose = " ".join(
            [str(structured.get("conclusion_sentence") or "")]
            + [f"{reason.get('title','')} {reason.get('body','')}" for reason in reasons if isinstance(reason, dict)]
            + [str(item) for item in (structured.get("checklist") or [])]
            + [str(item) for item in (structured.get("limitations") or [])]
        )
        unverified = _unverified_numbers(prose, allowed)
        if unverified:
            problems.append(f"unverified_number:{unverified[0]}")

        # An observation drawn from an aerial image cannot carry a measurement.
        # Rejecting every digit here is blunt on purpose: "약 100m 떨어진 주택"
        # reads as a survey result, and there is no way to check it.
        observations = structured.get("map_observations")
        if observations is not None and not isinstance(observations, list):
            problems.append("map_observations_not_a_list")
            observations = []
        for observation in observations or []:
            if not isinstance(observation, dict):
                problems.append("map_observation_not_object")
                continue
            text = f"{observation.get('observation', '')} {observation.get('relevance', '')}"
            found = NUMBER_RE.findall(text)
            if found:
                problems.append(f"measurement_in_map_observation:{found[0]}")
        return problems

    def generate(self, pack: dict[str, Any]) -> str:
        if not self.enabled:
            self.last_status = {"mode": "deterministic", "detail": "claude_key_missing"}
            return self.fallback.generate(pack)
        prompt_pack = build_prompt_pack(pack)
        image_blocks = _image_blocks(pack.get("map_context") or {}) + _user_image_blocks(pack)
        image_count = sum(1 for block in image_blocks if block.get("type") == "image")
        try:
            raw = self._call(prompt_pack, image_blocks)
        except urllib.error.HTTPError as exc:
            # Keep the API's own message: "invalid model", "missing workspace id"
            # and "over quota" all arrive as HTTPError and need different fixes.
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - the body is best-effort context only
                detail = ""
            self.last_status = {
                "mode": "deterministic",
                "detail": f"api_error_{exc.code}",
                "response": detail,
            }
            return self.fallback.generate(pack)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.last_status = {"mode": "deterministic", "detail": f"api_unavailable: {type(exc).__name__}"}
            return self.fallback.generate(pack)
        structured = _extract_json(raw)
        if structured is None:
            # A reply cut off at max_tokens is truncated JSON, not malformed
            # JSON; the two need different fixes, so name which one happened.
            detail = "response_truncated" if self.last_stop_reason == "max_tokens" else "invalid_json"
            self.last_status = {"mode": "deterministic", "detail": detail, "response": (raw or "")[-200:]}
            return self.fallback.generate(pack)
        structured = _compact_structured(structured)
        conclusion_label = (pack.get("analysis") or {}).get("conclusion_label")
        if conclusion_label:
            structured["conclusion_sentence"] = _clip_text(conclusion_label, 90)
        problems = self._validate(structured, prompt_pack, pack)
        if problems:
            self.last_status = {"mode": "deterministic", "detail": "guard_rejected", "problems": problems}
            return self.fallback.generate(pack)
        self.last_status = {
            "mode": "claude",
            "model": self.model,
            "images_sent": image_count,
            "structured": structured,
        }
        return _render(structured, pack)
