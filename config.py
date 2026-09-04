"""Safe, cloneable runtime configuration.

Secrets are read only from environment variables (or the deployment
EnvironmentFile). The repository deliberately contains no working API key.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from lucera.paths import DATA_DIR, DATABASE_PATH as DEFAULT_DATABASE_PATH


@lru_cache(maxsize=1)
def _local_env() -> dict[str, str]:
    """Read the ignored project .env without putting credentials in source."""

    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _setting(name: str, default: str = "") -> str:
    return str(os.getenv(name) or _local_env().get(name) or default).strip()


DATABASE_PATH = Path(_setting("LUCERA_DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))

PUBLIC_DATA_KEYS = {
    "road_address": _setting("ROAD_ADDRESS_API_KEY"),
    "clik": _setting("CLIK_API_KEY"),
    "vworld": _setting("VWORLD_API_KEY"),
}

DATA_GO_KR_API_KEY = _setting("DATA_GO_KR_API_KEY")

JUSO_ENDPOINT = _setting("JUSO_ENDPOINT", "https://business.juso.go.kr/addrlink/addrLinkApi.do")
VWORLD_DOMAIN = _setting("VWORLD_DOMAIN", "http://localhost:3000")
VWORLD_ADDRESS_ENDPOINT = _setting("VWORLD_ADDRESS_ENDPOINT", "https://api.vworld.kr/req/address")
VWORLD_IMAGE_ENDPOINT = _setting("VWORLD_IMAGE_ENDPOINT", "https://api.vworld.kr/req/image")
VWORLD_DATA_ENDPOINT = _setting("VWORLD_DATA_ENDPOINT", "https://api.vworld.kr/req/data")
VWORLD_SEARCH_ENDPOINT = _setting("VWORLD_SEARCH_ENDPOINT", "https://api.vworld.kr/req/search")
VWORLD_TIMEOUT_SECONDS = float(_setting("VWORLD_TIMEOUT_SECONDS", "20"))

VWORLD_MAP_VIEWS = (
    {"kind": "aerial_close", "basemap": "PHOTO", "zoom": 17, "label": "항공영상 (근접)"},
    {"kind": "aerial_wide", "basemap": "PHOTO", "zoom": 15, "label": "항공영상 (광역)"},
    {"kind": "base_wide", "basemap": "GRAPHIC", "zoom": 15, "label": "배경지도 (광역)"},
)
VWORLD_MAP_SIZE = (720, 720)
VWORLD_ZONING_LAYER = "LT_C_UQ111"
VWORLD_NEARBY_BUFFER_M = 300
VWORLD_FEATURE_LAYERS = (
    {"data": VWORLD_ZONING_LAYER, "label": "용도지역(부지)", "buffer_m": 0, "scope": "site"},
    {"data": VWORLD_ZONING_LAYER, "label": "용도지역(반경 300m)", "buffer_m": VWORLD_NEARBY_BUFFER_M, "scope": "nearby"},
)
VWORLD_CADASTRAL_LAYER = "LP_PA_CBND_BUBUN"
VWORLD_FEATURE_LIMIT = 30
VWORLD_CACHE_DIR = DATA_DIR / "work" / "vworld_cache"

CLIK_MINUTES_ENDPOINT = _setting("CLIK_MINUTES_ENDPOINT", "https://clik.nanet.go.kr/openapi/minutes.do")
CLIK_PORTAL_URL = _setting("CLIK_PORTAL_URL", "https://clik.nanet.go.kr")
DEFAULT_RADIUS_M = 5_000
DEFAULT_RESULT_LIMIT = 20
MAX_RADIUS_M = 50_000
MAX_RESULT_LIMIT = 100
