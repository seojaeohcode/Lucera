from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from .paths import GAZETTEER_DIR
from .regions import region_catalog


GAZETTEER_PATH = GAZETTEER_DIR / "gwangju_jeonnam_admin_units.json"
_MUNICIPALITIES = {row["name"] for row in region_catalog()}
_PROVINCE_BY_MUNICIPALITY = {row["name"]: row["province"] for row in region_catalog()}


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    try:
        return json.loads(GAZETTEER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": "missing", "units": []}


@lru_cache(maxsize=1)
def _units_by_name() -> dict[str, tuple[dict[str, Any], ...]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for unit in _payload().get("units", []):
        name = str(unit.get("name") or "").strip()
        if name:
            result.setdefault(name, []).append(dict(unit))
    return {name: tuple(items) for name, items in result.items()}


def gazetteer_version() -> str:
    return str(_payload().get("version") or "unknown")


def lookup_unit(surface: str | None) -> list[dict[str, Any]]:
    """Return only curated, municipality-backed local administrative units."""
    value = " ".join(str(surface or "").split())
    return [dict(item) for item in _units_by_name().get(value, ())]


def municipality_for_unit(surface: str | None) -> str | None:
    units = lookup_unit(surface)
    municipalities = {str(item.get("municipality")) for item in units if item.get("municipality")}
    return next(iter(municipalities)) if len(municipalities) == 1 else None


def is_known_municipality(value: str | None) -> bool:
    return " ".join(str(value or "").split()) in _MUNICIPALITIES


def is_known_unit(surface: str | None, municipality: str | None = None) -> bool:
    units = lookup_unit(surface)
    if municipality:
        return any(item.get("municipality") == municipality for item in units)
    return bool(units)


def unit_type(surface: str | None, municipality: str | None = None) -> str | None:
    units = lookup_unit(surface)
    for item in units:
        if not municipality or item.get("municipality") == municipality:
            return str(item.get("type") or "") or None
    return None


def known_unit_names() -> set[str]:
    return set(_units_by_name())


def identity_place_is_valid(identity: str | None, municipality: str | None = None) -> bool:
    """Validate a normalized `place:` identity before it can merge cases."""
    value = str(identity or "")
    if value.startswith("place:"):
        value = value.removeprefix("place:")
    tokens = value.split()
    if not tokens:
        return False
    found_municipality = next((token for token in tokens if token in _MUNICIPALITIES), None)
    if not found_municipality:
        return False
    if municipality and found_municipality != municipality:
        return False
    for token in tokens:
        if is_known_unit(token, found_municipality):
            return True
    return len(tokens) == 1 and found_municipality in tokens


def suspicious_suffix_token(value: str | None) -> bool:
    """Detect suffix-shaped text that is not a curated local place."""
    text = " ".join(str(value or "").split())
    if not text or text in known_unit_names() or is_known_municipality(text):
        return False
    return bool(re.fullmatch(r"[가-힣0-9]{1,12}(?:읍|면|동|리)", text))


def municipality_province(municipality: str | None) -> str | None:
    return _PROVINCE_BY_MUNICIPALITY.get(str(municipality or ""))
