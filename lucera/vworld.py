"""VWorld (공간정보 오픈플랫폼) client for site imagery and surrounding features.

The pre-check already knows what the records say about a place. What it cannot
see is the place itself: whether the parcel sits against houses or behind a
ridge, whether a road runs along its edge, how the settlement is arranged. This
module fetches that view — aerial and base map tiles plus the feature layers
around the point — so the answer generator can describe the surroundings.

Two rules shape the design.

* **Every call is optional.** A missing key, a rate limit, a retired layer code
  or a slow network must degrade the answer, never break the pre-check. Each
  failure is recorded in `errors` and the pipeline continues.
* **Images are observation, not measurement.** Nothing here produces a number
  the report may quote. Distances and counts come from the rule engine and the
  permit register; the imagery only supports qualitative description.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config

from .location import normalize_address


USER_AGENT = "Lucera/0.1"


def _cache_dir() -> Path:
    path = Path(config.VWORLD_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _meters_per_pixel(latitude: float, zoom: int) -> float:
    """Web-Mercator ground resolution, used only to state the view's extent."""

    return 156543.03392 * math.cos(math.radians(latitude)) / (2 ** zoom)


class VWorldClient:
    provider = "vworld"

    def __init__(
        self,
        api_key: str | None = None,
        domain: str | None = None,
        timeout: float | None = None,
    ):
        self.api_key = api_key if api_key is not None else config.PUBLIC_DATA_KEYS.get("vworld")
        self.domain = domain if domain is not None else config.VWORLD_DOMAIN
        self.timeout = timeout if timeout is not None else config.VWORLD_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------ http

    def _get(self, endpoint: str, params: dict[str, Any]) -> tuple[bytes, str]:
        query = {**params, "key": self.api_key}
        if self.domain:
            query["domain"] = self.domain
        url = f"{endpoint}?{urlencode(query)}"
        request = Request(url, headers={"User-Agent": USER_AGENT, "Referer": self.domain or ""})
        with urlopen(request, timeout=self.timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")

    def _get_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        body, content_type = self._get(endpoint, params)
        text = body.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # VWorld answers an authentication or quota problem with an HTML
            # page, so the body is more useful than the parse error.
            raise RuntimeError(f"non-JSON response ({content_type}): {text[:200]}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("unexpected JSON shape")
        return payload

    # -------------------------------------------------------------- geocoder

    def geocode(self, address: str, address_type: str = "parcel") -> dict[str, Any]:
        """Resolve an address to a point.

        `type=parcel` matches 지번 addresses and `type=road` matches 도로명; the
        API does not fall back between them, so the caller tries both.
        """

        payload = self._get_json(
            config.VWORLD_ADDRESS_ENDPOINT,
            {
                "service": "address",
                "request": "getcoord",
                "version": "2.0",
                "crs": "epsg:4326",
                "address": address,
                "refine": "true",
                "simple": "false",
                "format": "json",
                "type": address_type,
            },
        )
        response = payload.get("response", {})
        status = str(response.get("status") or "unknown")
        if status != "OK":
            return {
                "status": status,
                "address_type": address_type,
                "error": (response.get("error") or {}).get("text"),
            }
        point = (response.get("result") or {}).get("point") or {}
        refined = response.get("refined") or {}
        structure = refined.get("structure") or {}
        refined_text = refined.get("text") or ""

        # The structure levels do not map cleanly onto 시도/시군구/읍면동/리.
        # A real response for 영암군 삼호읍 산호리 comes back with level3 empty
        # and level4L holding "삼호읍", so reading 리 out of level4L would put a
        # township name in the 리 field and break every downstream match.
        # `refined.text` is the canonical address string, and the project's own
        # parser already understands it — including the 2026-07-01
        # 전남광주통합특별시 merge, which it maps back to the legacy province
        # names the database is keyed on.
        parsed = normalize_address(refined_text) if refined_text else None
        return {
            "status": "OK",
            "address_type": address_type,
            "longitude": float(point["x"]),
            "latitude": float(point["y"]),
            "refined_address": refined_text or None,
            "province": parsed.province if parsed else None,
            "city_county": parsed.city_county if parsed else None,
            "eup_myeon": parsed.eup_myeon if parsed else None,
            "ri": parsed.ri if parsed else None,
            # 19-digit parcel identifier; the cadastral layer can be queried by
            # it directly (attrFilter=pnu:=:...) once the parcel is known.
            "pnu": structure.get("level4LC") or None,
            "administrative_name": structure.get("level1") or None,
            "structure": structure,
        }

    def geocode_any(self, address: str) -> dict[str, Any]:
        attempts = []
        for address_type in ("parcel", "road"):
            try:
                result = self.geocode(address, address_type)
            except Exception as exc:  # noqa: BLE001 - reported, never raised on
                attempts.append({"address_type": address_type, "status": "request_failed", "error": str(exc)[:200]})
                continue
            if result.get("status") == "OK":
                result["attempts"] = attempts
                return result
            attempts.append(result)
        return {"status": "NOT_FOUND", "attempts": attempts}

    # ------------------------------------------------------------- static map

    def static_map(
        self,
        latitude: float,
        longitude: float,
        *,
        zoom: int,
        basemap: str = "PHOTO",
        size: tuple[int, int] | None = None,
        marker: bool = True,
    ) -> dict[str, Any]:
        """Fetch one map view as PNG bytes, cached on disk by its parameters."""

        width, height = size or config.VWORLD_MAP_SIZE
        params: dict[str, Any] = {
            "service": "image",
            "request": "getmap",
            "format": "png",
            "basemap": basemap,
            "center": f"{longitude},{latitude}",
            "crs": "EPSG:4326",
            "zoom": zoom,
            "size": f"{width},{height}",
        }
        if marker:
            params["marker"] = f"point:{longitude},{latitude}"
        cache_key = hashlib.sha256(
            json.dumps({**params, "domain": self.domain}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:32]
        path = _cache_dir() / f"{cache_key}.png"
        if not path.exists():
            body, content_type = self._get(config.VWORLD_IMAGE_ENDPOINT, params)
            if "image" not in content_type:
                raise RuntimeError(f"expected an image, got {content_type}: {body[:200]!r}")
            path.write_bytes(body)
        ground = _meters_per_pixel(latitude, zoom)
        return {
            "cache_key": cache_key,
            "path": str(path),
            "bytes": path.stat().st_size,
            "media_type": "image/png",
            "basemap": basemap,
            "zoom": zoom,
            "size": [width, height],
            # Stated so the model can say "이 화면은 대략 몇 백 미터 범위" without
            # inventing a distance; it is derived from the projection, not measured.
            "approx_extent_m": round(ground * width),
        }

    # ---------------------------------------------------------------- features

    def features(
        self,
        latitude: float,
        longitude: float,
        *,
        data: str,
        buffer_m: int = 0,
        limit: int | None = None,
        attr_filter: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if attr_filter:
            params["attrFilter"] = attr_filter
        payload = self._get_json(
            config.VWORLD_DATA_ENDPOINT,
            {
                **params,
                "service": "data",
                "request": "GetFeature",
                "version": "2.0",
                "data": data,
                "geomFilter": f"POINT({longitude} {latitude})",
                "buffer": buffer_m,
                "size": limit or config.VWORLD_FEATURE_LIMIT,
                "page": 1,
                "format": "json",
                "crs": "EPSG:4326",
                "geometry": "false",
                "attribute": "true",
            },
        )
        response = payload.get("response", {})
        status = str(response.get("status") or "unknown")
        if status not in {"OK", "NOT_FOUND"}:
            raise RuntimeError(f"status={status} {(response.get('error') or {}).get('text')}")
        collection = ((response.get("result") or {}).get("featureCollection") or {})
        rows = []
        for feature in collection.get("features") or []:
            properties = feature.get("properties") or {}
            rows.append({key: value for key, value in properties.items() if value not in (None, "")})
        return {"data": data, "status": status, "count": len(rows), "features": rows[: limit or config.VWORLD_FEATURE_LIMIT]}

    # ----------------------------------------------------------------- bundle

    def parcel(self, pnu: str) -> dict[str, Any] | None:
        """The one cadastral parcel named by a PNU.

        The layer carries no area column, so nothing here can confirm a stated
        site area. What it does carry is the 지목 and the posted land value,
        both of which are official facts about the parcel.
        """

        payload = self._get_json(
            config.VWORLD_DATA_ENDPOINT,
            {
                "service": "data",
                "request": "GetFeature",
                "version": "2.0",
                "data": config.VWORLD_CADASTRAL_LAYER,
                "attrFilter": f"pnu:=:{pnu}",
                "size": 1,
                "page": 1,
                "format": "json",
                "crs": "EPSG:4326",
                "geometry": "false",
                "attribute": "true",
            },
        )
        response = payload.get("response", {})
        if str(response.get("status")) != "OK":
            raise RuntimeError(f"status={response.get('status')}")
        features = (((response.get("result") or {}).get("featureCollection") or {}).get("features")) or []
        if not features:
            return None
        properties = features[0].get("properties") or {}
        return {key: value for key, value in properties.items() if value not in (None, "")}

    def site_context(self, latitude: float, longitude: float, pnu: str | None = None) -> dict[str, Any]:
        """Everything VWorld can say about one point, with failures recorded."""

        context: dict[str, Any] = {
            "provider": self.provider,
            "latitude": latitude,
            "longitude": longitude,
            "images": [],
            "layers": [],
            "parcel": None,
            "errors": [],
        }
        if not self.enabled:
            context["errors"].append({"stage": "config", "detail": "vworld key is not configured"})
            return context

        for view in config.VWORLD_MAP_VIEWS:
            try:
                image = self.static_map(
                    latitude,
                    longitude,
                    zoom=int(view["zoom"]),
                    basemap=str(view["basemap"]),
                )
            except Exception as exc:  # noqa: BLE001 - imagery is optional
                context["errors"].append({"stage": f"image:{view['kind']}", "detail": str(exc)[:200]})
                continue
            context["images"].append({**image, "kind": view["kind"], "label": view["label"]})

        for layer in config.VWORLD_FEATURE_LAYERS:
            try:
                result = self.features(
                    latitude,
                    longitude,
                    data=str(layer["data"]),
                    buffer_m=int(layer.get("buffer_m") or 0),
                )
            except Exception as exc:  # noqa: BLE001 - a retired layer code is not fatal
                context["errors"].append({"stage": f"layer:{layer['data']}", "detail": str(exc)[:200]})
                continue
            context["layers"].append(
                {**result, "label": layer["label"], "buffer_m": layer.get("buffer_m"), "scope": layer.get("scope")}
            )

        if pnu:
            try:
                context["parcel"] = self.parcel(pnu)
            except Exception as exc:  # noqa: BLE001 - the parcel record is optional
                context["errors"].append({"stage": "parcel", "detail": str(exc)[:200]})

        return context


def check_vworld(address: str) -> dict[str, Any]:
    """Probe the key, the geocoder, each map view and each feature layer.

    Layer codes and key registrations both fail quietly at request time, so this
    reports each piece separately: which one is broken is the whole question.
    """

    client = VWorldClient()
    report: dict[str, Any] = {
        "key_configured": client.enabled,
        "domain": client.domain,
        "address": address,
    }
    if not client.enabled:
        report["verdict"] = "VWORLD_API_KEY가 없습니다."
        return report

    geocode = client.geocode_any(address)
    report["geocode"] = geocode
    if geocode.get("status") != "OK":
        report["verdict"] = (
            "지오코딩이 실패했습니다. 키가 등록된 서비스 URL과 config.VWORLD_DOMAIN이 "
            "같은지, 키 승인 상태가 '승인'인지 확인하세요."
        )
        return report

    latitude, longitude = geocode["latitude"], geocode["longitude"]
    images, layers, errors = [], [], []
    for view in config.VWORLD_MAP_VIEWS:
        try:
            image = client.static_map(latitude, longitude, zoom=int(view["zoom"]), basemap=str(view["basemap"]))
            images.append({"kind": view["kind"], "bytes": image["bytes"], "approx_extent_m": image["approx_extent_m"]})
        except Exception as exc:  # noqa: BLE001 - this command exists to report failures
            errors.append({"stage": f"image:{view['kind']}", "detail": str(exc)[:200]})
    for layer in config.VWORLD_FEATURE_LAYERS:
        try:
            result = client.features(latitude, longitude, data=str(layer["data"]), buffer_m=int(layer.get("buffer_m") or 0))
            layers.append({"data": layer["data"], "label": layer["label"], "status": result["status"], "count": result["count"],
                           "sample_keys": sorted(result["features"][0].keys())[:12] if result["features"] else []})
        except Exception as exc:  # noqa: BLE001
            errors.append({"stage": f"layer:{layer['data']}", "detail": str(exc)[:200]})

    report["images"] = images
    report["layers"] = layers
    report["errors"] = errors
    working = len(images) + len(layers)
    report["verdict"] = (
        f"지오코딩 성공 · 지도 {len(images)}/{len(config.VWORLD_MAP_VIEWS)} · "
        f"레이어 {len(layers)}/{len(config.VWORLD_FEATURE_LAYERS)}"
        + (" · 실패한 항목은 errors를 보고 config.VWORLD_* 에서 제거하거나 코드를 고치세요." if errors else " · 전부 정상")
    ) if working else "지오코딩은 되지만 지도·레이어 요청이 전부 실패했습니다."
    return report
