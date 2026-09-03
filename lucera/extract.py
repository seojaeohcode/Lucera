from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from .gazetteer import is_known_municipality, lookup_unit, municipality_for_unit, municipality_province, unit_type
from .location import normalize_address
from .keywords import classify_text


def redact_sensitive(text: str) -> str:
    """Mask common personal identifiers before search/LLM use."""
    value = text or ""
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[이메일]", value)
    value = re.sub(r"(?<!\d)(?:01[016789])[- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[전화번호]", value)
    value = re.sub(r"(?<!\d)\d{6}[- ]?\d{7}(?!\d)", "[주민등록번호]", value)
    return value


def clean_text(text: str) -> str:
    value = html.unescape(text or "")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


class _MinutesHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.outside: list[str] = []
        self.speaker_blocks: list[tuple[str | None, str]] = []
        self._speaker_depth = 0
        self._speaker_class: str | None = None
        self._speaker_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "spk":
            self._speaker_depth = 1
            self._speaker_class = attributes.get("class")
            self._speaker_buffer = []
        elif self._speaker_depth:
            self._speaker_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._speaker_depth:
            self.outside.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._speaker_depth:
            return
        if tag.lower() == "spk" and self._speaker_depth == 1:
            text = clean_text("".join(self._speaker_buffer))
            if text:
                self.speaker_blocks.append((self._speaker_class, text))
            self._speaker_depth = 0
            self._speaker_class = None
            self._speaker_buffer = []
        else:
            self._speaker_depth = max(0, self._speaker_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._speaker_depth:
            self._speaker_buffer.append(data)
        else:
            self.outside.append(data)


def parse_minutes_html(raw_html: str) -> tuple[str, list[dict[str, Any]]]:
    parser = _MinutesHTMLParser()
    parser.feed(raw_html or "")
    outside = clean_text("".join(parser.outside))
    segments: list[dict[str, Any]] = []
    if parser.speaker_blocks:
        for speaker_class, text in parser.speaker_blocks:
            name, role = parse_speaker(text)
            segments.append(
                {
                    "text_original": text,
                    "text_redacted": redact_sensitive(text),
                    "segment_type": "speech",
                    "speaker_name": name,
                    "speaker_role": role,
                    "metadata": {"speaker_class": speaker_class} if speaker_class else {},
                }
            )
        if outside:
            for index, part in enumerate(split_paragraphs(outside)):
                if len(part) >= 12:
                    segments.insert(
                        index,
                        {
                            "text_original": part,
                            "text_redacted": redact_sensitive(part),
                            "segment_type": "agenda_or_header",
                        },
                    )
    else:
        for part in split_paragraphs(outside):
            if len(part) >= 12:
                segments.append(
                    {
                        "text_original": part,
                        "text_redacted": redact_sensitive(part),
                        "segment_type": "paragraph",
                    }
                )
    return outside, segments


def split_paragraphs(text: str, max_chars: int = 1400) -> list[str]:
    parts = [clean_text(p) for p in re.split(r"(?:\n\s*){2,}|(?<=다\.)\s+", text) if clean_text(p)]
    output: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            output.append(part)
            continue
        sentences = re.split(r"(?<=[.!?다요])\s+", part)
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > max_chars:
                output.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            output.append(current)
    return output


def parse_speaker(text: str) -> tuple[str | None, str | None]:
    value = re.sub(r"^[○◦·\s]+", "", text)
    match = re.match(
        r"(?:(위원장|의장|부의장|전문위원|의원|시장|군수|국장|과장|팀장)\s*)?([가-힣]{2,4})",
        value,
    )
    if not match:
        return None, None
    name = match.group(2)
    role = match.group(1)
    if name in {"그러면", "이상으로", "오늘은", "존경하는", "안녕하"}:
        return None, None
    return name, role


def extract_issues(text: str, context_text: str = "") -> list[dict[str, Any]]:
    """Extract only high-precision issue labels from one segment.

    `context_text` is limited to the meeting title/agenda by callers.  The
    previous implementation classified generic words such as 환경, 주민, and
    협의 on their own, which created too many false positives in council
    minutes.
    """
    return classify_text(text, context_text).get("issues", [])


def extract_places(text: str, meeting_context: str = "") -> list[dict[str, Any]]:
    """Extract high-precision administrative places with provenance.

    Suffix matching alone is unsafe in Korean minutes: ``보면`` and ``하면``
    look like 면 names, while ``관리`` and ``일자리`` look like 리 names.  A
    standalone token is therefore accepted only when it is in the curated
    광주·전남 gazetteer.  Unknown suffix-shaped text is intentionally omitted
    from identity matching so it can never merge unrelated cases.
    """
    value = text or ""
    context_location = normalize_address(meeting_context)
    candidates: list[str] = []
    full_pattern = (
        r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|"
        r"세종특별자치시|전남광주통합특별시|[가-힣]+(?:특별자치도|도))\s+[가-힣]+(?:시|군|구)"
        r"(?:\s+[가-힣0-9]+(?:읍|면|동))?(?:\s+[가-힣0-9]+리)?"
    )
    candidates.extend(re.findall(full_pattern, value))
    candidates.extend(re.findall(r"[가-힣0-9]{1,12}(?:읍|면|동)\s+\d+(?:-\d+)?(?:번지)?", value))
    # Candidate generation remains broad for recall, but only gazetteer-backed
    # standalone tokens survive below.
    candidates.extend(re.findall(r"[가-힣0-9]{1,12}(?:읍|면|동)", value))
    candidates.extend(re.findall(r"[가-힣0-9]{2,8}리", value))

    seen: set[str] = set()
    places: list[dict[str, Any]] = []
    for candidate in candidates:
        surface = clean_text(candidate)
        if not surface or surface in seen:
            continue
        seen.add(surface)
        lot_expression = re.fullmatch(
            r"(?P<admin>[가-힣0-9]{1,12}(?:읍|면|동))\s+(?P<lot>\d+(?:-\d+)?)(?:번지)?",
            surface,
        )
        # For a full address, validate the municipality/local unit pair.  For
        # a standalone local token, use the gazetteer to supply its parent.
        raw_location = normalize_address(surface)
        local_tokens = [token for token in surface.split() if re.fullmatch(r"[가-힣0-9]+(?:읍|면|동|리)", token)]
        local_token = local_tokens[-1] if local_tokens else None
        known_units = lookup_unit(local_token)
        known_municipality = None
        if local_token and known_units:
            known_unit = known_units[0]
            known_municipalities = {str(item.get("municipality")) for item in known_units}
            if raw_location.city_county in known_municipalities:
                known_municipality = raw_location.city_county
            elif len(known_municipalities) == 1:
                known_municipality = next(iter(known_municipalities))
                raw_location.city_county = known_municipality
                raw_location.province = municipality_province(known_municipality)
            else:
                known_municipality = raw_location.city_county
            if known_unit.get("type") == "ri" and known_unit.get("parent") and not raw_location.eup_myeon:
                raw_location.eup_myeon = str(known_unit["parent"])
        elif raw_location.city_county and is_known_municipality(raw_location.city_county):
            known_municipality = raw_location.city_county

        if not raw_location.province and known_municipality:
            raw_location.province = municipality_province(known_municipality)
        if not raw_location.city_county and context_location.city_county and not known_municipality:
            raw_location.city_county = context_location.city_county
            raw_location.province = raw_location.province or context_location.province
        if not raw_location.eup_myeon and context_location.eup_myeon and not local_token:
            raw_location.eup_myeon = context_location.eup_myeon
        if not raw_location.ri and context_location.ri and not local_token:
            raw_location.ri = context_location.ri

        if local_token and known_municipality:
            canonical_parts = [
                part for part in (raw_location.province, known_municipality, raw_location.eup_myeon, raw_location.ri)
                if part
            ]
            raw_location.normalized_address = " ".join(canonical_parts)

        # A standalone unknown suffix token is not a place record.  This is
        # the main guard against verb/common-noun false positives.
        if local_token and not known_units:
            continue
        if local_token and known_municipality and known_units and not any(item.get("municipality") == known_municipality for item in known_units):
            continue

        if lot_expression:
            prefix = " ".join(part for part in (raw_location.province, raw_location.city_county) if part)
            raw_location.jibun_address = " ".join(part for part in (prefix, surface) if part)
            place_type, precision = "jibun_address", "jibun_address"
        elif raw_location.ri or (local_token and unit_type(local_token, known_municipality) == "ri"):
            place_type, precision = "ri", "ri"
        elif raw_location.eup_myeon or (local_token and unit_type(local_token, known_municipality) == "eup_myeon"):
            place_type, precision = "eup_myeon", "eup_myeon"
        elif raw_location.city_county:
            place_type, precision = "city_county", "city_county"
        else:
            continue

        same_municipality = bool(
            known_municipality and context_location.city_county and known_municipality == context_location.city_county
        )
        comparative = bool(
            known_municipality and context_location.city_county and known_municipality != context_location.city_county
        )
        relation_type = (
            "comparative" if comparative else
            "subject_site" if lot_expression else
            "same_ri" if raw_location.ri else
            "same_eup_myeon" if raw_location.eup_myeon else
            "same_city_county"
        )
        places.append(
            {
                "surface_form": surface,
                "raw_name": surface,
                "normalized_name": raw_location.normalized_address,
                "place_type": place_type,
                "province": raw_location.province,
                "city_county": raw_location.city_county,
                "eup_myeon": raw_location.eup_myeon,
                "ri": raw_location.ri,
                "road_address": raw_location.road_address,
                "jibun_address": raw_location.jibun_address,
                "admin_code": raw_location.admin_code,
                "latitude": raw_location.latitude,
                "longitude": raw_location.longitude,
                "geo_precision": precision,
                "geocode_confidence": 0.68 if lot_expression else 0.55 if not comparative else 0.25,
                "location_status": "candidate",
                "resolution_method": "gazetteer+rule" if local_token and known_units else "rule",
                "relation_type": relation_type,
                "distance_status": "unknown",
                "confidence": 0.72 if lot_expression else 0.62 if same_municipality or not known_municipality else 0.25,
                "resolution_reason": (
                    "문서 본문에 지번 표현이 있으나 좌표는 확인되지 않음" if lot_expression else
                    "문서의 행정구역 표현을 광주·전남 지명사전과 대조함" if local_token and known_units else
                    "회의 개최 지역의 자치단체로만 해석되며 세부 지명은 확인되지 않음"
                ),
                "evidence_text": surface,
            }
        )
    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for place in places:
        key = (str(place.get("normalized_name") or ""), str(place.get("place_type") or ""), str(place.get("relation_type") or ""))
        previous = deduplicated.get(key)
        if previous is None or float(place.get("confidence") or 0) > float(previous.get("confidence") or 0):
            deduplicated[key] = place
    return list(deduplicated.values())
