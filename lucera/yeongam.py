"""Product scope helpers for the Yeongam-only demo.

The first release intentionally has one clear geography. Keeping the scope in
one module prevents a new endpoint or UI control from accidentally widening a
query back to the old 광주·전남 catalogue.
"""

from __future__ import annotations

from typing import Any

YEONGAM_COUNTY = "영암군"
YEONGAM_PROVINCE = "전라남도"
YEONGAM_SCOPE = "yeongam"


def is_yeongam(value: str | None) -> bool:
    text = " ".join(str(value or "").split())
    return YEONGAM_COUNTY in text or text == "영암"


def require_yeongam(city_county: str | None, address: str | None = None) -> None:
    if city_county != YEONGAM_COUNTY and not is_yeongam(address):
        raise ValueError("Lucera 데모는 영암군 주소만 지원합니다.")


def scope_city_county(scope: Any) -> str | None:
    value = str(scope or YEONGAM_SCOPE).strip().lower()
    if value in {YEONGAM_SCOPE, YEONGAM_COUNTY, "영암"}:
        return YEONGAM_COUNTY
    raise ValueError("scope must be yeongam")
