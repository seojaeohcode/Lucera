from __future__ import annotations

import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import config

from .answer import resolve_api_key
from .db import LuceraDB
from .complaints import (
    continue_conversation,
    create_complaint,
    get_conversation,
    yeongam_area_detail,
    yeongam_permit_context,
    yeongam_pins,
)
from .ingest import ingest_clik
from .projects import create_project, get_precheck, get_project
from .regions import parent_region_catalog, region_catalog
from .regional_collect import collect_regional
from .rag import RAGService
from .search import SearchService
from .solverton_features import feature_overview, yeongam_f1, yeongam_f3, yeongam_f4
from .vworld import VWorldClient


WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def runtime_status() -> dict[str, Any]:
    """Expose provider configuration without exposing any credential value."""

    claude_configured = bool(resolve_api_key())
    force_local = os.getenv("LUCERA_ANSWER_MODE", "").lower() == "local"
    vworld_configured = VWorldClient().enabled
    return {
        "scope": "yeongam",
        "answer": {
            "provider": "Claude API",
            "configured": claude_configured,
            "mode": "local" if force_local else ("claude_api" if claude_configured else "local_fallback"),
            "notice": (
                "Claude API 키가 설정되어 호출 시 사용됩니다."
                if claude_configured and not force_local
                else "Claude API 키가 없어 결정론적 로컬 분석으로 동작합니다."
            ),
        },
        "map": {
            "provider": "VWorld",
            "required": True,
            "configured": vworld_configured,
            "mode": "aerial_imagery" if vworld_configured else "imagery_pending_key",
            "notice": (
                "민원 분석과 챗봇 요청에 지도 영상을 자동 포함합니다."
                if vworld_configured
                else "지도 영상 자동 포함을 요청하지만 VWorld 키가 없어 영상 연결 대기 상태입니다."
            ),
        },
    }


class LuceraHandler(BaseHTTPRequestHandler):
    server_version = "Lucera/0.1"

    @property
    def db(self) -> LuceraDB:
        return self.server.db  # type: ignore[attr-defined]

    def _headers(self, status: int = 200, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._headers(204)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            with self.db.lock:
                self._json({"status": "ok", "service": "lucera", **self.db.stats()})
            return
        if path == "/v1/runtime/status":
            self._json(runtime_status())
            return
        if path == "/v1/stats":
            with self.db.lock:
                self._json({"database": str(self.db.path), **self.db.stats()})
            return
        if path == "/v1/regions":
            regions = [item for item in region_catalog() if item.get("name") == "영암군"]
            self._json({"scope": "영암군", "parent_regions": [], "regions": regions})
            return
        if path == "/v1/map/pins":
            with self.db.lock:
                self._json(yeongam_pins(self.db))
            return
        if path == "/v1/features":
            with self.db.lock:
                self._json(feature_overview(self.db))
            return
        if path == "/v1/features/f1":
            self._json(yeongam_f1())
            return
        if path == "/v1/features/f3":
            with self.db.lock:
                self._json(yeongam_f3(self.db))
            return
        if path == "/v1/features/f4":
            with self.db.lock:
                self._json(yeongam_f4(self.db))
            return
        if path.startswith("/assets/"):
            self._static_asset(path.removeprefix("/assets/"))
            return
        if path in {"/", "/index.html"}:
            body = (WEB_DIR / "index.html").read_bytes()
            self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(body)
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["v1", "conversations"]:
            with self.db.lock:
                conversation = get_conversation(self.db, parts[2])
            self._json(conversation or {"error": "conversation_not_found"}, 200 if conversation else 404)
            return
        if len(parts) == 4 and parts[:3] == ["v1", "map", "areas"]:
            area = unquote(parts[3])
            with self.db.lock:
                detail = yeongam_area_detail(self.db, area)
            self._json(detail or {"error": "area_not_found"}, 200 if detail else 404)
            return
        if len(parts) == 3 and parts[:2] == ["v1", "map-image"]:
            self._map_image(parts[2])
            return
        if len(parts) == 3 and parts[:2] == ["v1", "projects"]:
            with self.db.lock:
                project = get_project(self.db, parts[2])
            self._json(project or {"error": "project_not_found"}, 200 if project else 404)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "projects"] and parts[3] == "precheck":
            with self.db.lock:
                precheck = get_precheck(self.db, parts[2])
            self._json(precheck or {"error": "project_not_found"}, 200 if precheck else 404)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "cases"] and parts[3] == "paragraphs":
            with self.db.lock:
                case_paragraphs = SearchService(self.db).get_case_paragraphs(parts[2])
            self._json(case_paragraphs or {"error": "case_not_found"}, 200 if case_paragraphs else 404)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "cases"] and parts[3] == "timeline":
            with self.db.lock:
                timeline = RAGService(self.db).case_timeline(parts[2])
            self._json(timeline or {"error": "case_not_found"}, 200 if timeline else 404)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "permits"] and parts[3] == "context":
            with self.db.lock:
                context = yeongam_permit_context(self.db, parts[2])
                if context and context.get("latitude") is not None and context.get("longitude") is not None:
                    fetched = VWorldClient().site_context(float(context["latitude"]), float(context["longitude"]))
                    context["map_context"] = {
                        **fetched,
                        "images": [
                            {**image, "url": f"/v1/map-image/{image['cache_key']}"}
                            for image in fetched.get("images", [])
                        ],
                    }
                    for image in context["map_context"].get("images", []):
                        image.pop("path", None)
            self._json(context or {"error": "permit_not_found"}, 200 if context else 404)
            return
        self._json({"error": "not_found"}, 404)

    def _static_asset(self, requested: str) -> None:
        """Serve only files below web/assets; never treat the URL as a path directly."""

        asset_root = (WEB_DIR / "assets").resolve()
        candidate = (asset_root / unquote(requested)).resolve()
        try:
            candidate.relative_to(asset_root)
        except ValueError:
            self._json({"error": "not_found"}, 404)
            return
        if not candidate.is_file():
            self._json({"error": "not_found"}, 404)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _map_image(self, cache_key: str) -> None:
        """Serve a cached VWorld tile by its content key.

        The key is the hash the client generated, so anything else is a probe;
        rejecting non-hex before touching the filesystem keeps the cache
        directory from being used as a path-traversal handle.
        """

        if not re.fullmatch(r"[0-9a-f]{8,64}", cache_key):
            self._json({"error": "invalid_key"}, 400)
            return
        path = Path(config.VWORLD_CACHE_DIR) / f"{cache_key}.png"
        if not path.is_file():
            self._json({"error": "not_found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            max_length = 8_000_000 if path.startswith("/v1/conversations/") else 2_000_000
            if length > max_length:
                self._json({"error": "request_too_large"}, 413)
                return
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            if path == "/v1/complaints":
                with self.db.lock:
                    result = create_complaint(self.db, payload)
                self._json(result, 201)
                return
            match = re.fullmatch(r"/v1/conversations/([^/]+)/messages", path)
            if match:
                with self.db.lock:
                    result = continue_conversation(self.db, match.group(1), payload)
                self._json(result)
                return
            if path == "/v1/meeting-evidence/search":
                with self.db.lock:
                    self._json(SearchService(self.db).search(payload))
                return
            if path == "/v1/chat":
                with self.db.lock:
                    self._json(RAGService(self.db).analyze(payload))
                return
            if path == "/v1/ingest/clik":
                keyword = str(payload.get("keyword") or "태양광")
                with self.db.lock:
                    result = ingest_clik(
                        self.db,
                        keyword,
                        int(payload.get("list_count", 10)),
                        int(payload.get("start_count", 0)),
                        payload.get("detail_limit"),
                        payload.get("assembly_id"),
                    )
                self._json(result)
                return
            if path == "/v1/projects":
                with self.db.lock:
                    result = create_project(self.db, payload)
                self._json(result, 201)
                return
            if path == "/v1/collect/regional":
                kinds = payload.get("kinds")
                kind_set = {str(kind) for kind in kinds} if isinstance(kinds, list) else None
                result = collect_regional(
                    self.db,
                    target_count=int(payload.get("target_count", 10)),
                    region_names=[str(name) for name in payload.get("regions", [])] if isinstance(payload.get("regions"), list) else None,
                    kinds=kind_set,
                    max_api_calls=int(payload.get("max_api_calls", 880)),
                    sleep_seconds=float(payload.get("sleep_seconds", 0.05)),
                    detail_workers=int(payload.get("detail_workers", 6)),
                )
                self._json(result, 200)
                return
            self._json({"error": "not_found"}, 404)
        except json.JSONDecodeError:
            self._json({"error": "invalid_json"}, 400)
        except (ValueError, RuntimeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # Keep internal details out of the HTTP response.
            self._json({"error": "internal_error", "detail": str(exc)[:200]}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[lucera] {self.address_string()} - {format % args}")


def run_server(db: LuceraDB, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), LuceraHandler)
    server.db = db  # type: ignore[attr-defined]
    print("Warming the search cache…")
    warmed = SearchService(db).warm_cache()
    print(f"Warmed: {warmed}")
    print(f"Lucera running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping Lucera")
    finally:
        server.server_close()
        db.close()
