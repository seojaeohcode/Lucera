from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import config

from .db import LuceraDB
from .ingest import ingest_clik
from .projects import create_project, get_precheck, get_project
from .regions import parent_region_catalog, region_catalog
from .regional_collect import collect_regional
from .rag import RAGService
from .review import rebuild_case_reviews
from .search import SearchService
from .ordinance import seed_official_rules
from .vworld import check_vworld
from .synthetic import seed_synthetic


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def demo_bundles() -> list[dict[str, Any]]:
    """Small clearly-labelled fixtures for an offline first-run demo."""
    base = {
        "system_code": "demo_fixture",
        "document_type": "meeting_minutes",
        "source_url": None,
        "mime_type": "text/plain",
        "access_policy": "demo",
    }
    return [
        {
            "source": {
                **base,
                "source_record_key": "demo-yeongam-samho-2024",
                "title": "[데모] 영암군의회 삼호읍 태양광 주민 협의 기록",
                "metadata": {"fixture": True, "warning": "실제 회의록이 아닌 기능 검증용 데이터"},
            },
            "meeting": {
                "assembly_name": "전라남도 영암군의회",
                "province": "전라남도",
                "city_county": "영암군",
                "meeting_title": "[데모] 태양광 발전시설 주민 설명 및 협의",
                "meeting_type": "주민 의견 청취",
                "meeting_date": "2024-05-12",
            },
            "page": {"text_original": "삼호읍 태양광 발전시설 주민 설명 및 협의 기록"},
            "segments": [
                {
                    "text_original": "삼호읍 주민들은 사업 설명이 충분하지 않았고, 농지와 경관 변화에 대한 우려를 제기했다. 추가 설명회와 자료 공개를 요구했다.",
                    "segment_type": "speech",
                    "speaker_name": "김민원",
                    "speaker_role": "주민",
                    "issues": [
                        {"issue_code": "communication_procedure", "polarity": "opposition", "confidence": 0.96, "evidence_span": "사업 설명이 충분하지 않았고"},
                        {"issue_code": "agricultural_land_damage", "polarity": "opposition", "confidence": 0.88, "evidence_span": "농지"},
                        {"issue_code": "landscape_damage", "polarity": "opposition", "confidence": 0.88, "evidence_span": "경관 변화"},
                    ],
                    "places": [
                        {
                            "surface_form": "삼호읍",
                            "raw_name": "삼호읍",
                            "normalized_name": "전라남도 영암군 삼호읍",
                            "place_type": "eup_myeon",
                            "province": "전라남도",
                            "city_county": "영암군",
                            "eup_myeon": "삼호읍",
                            "geo_precision": "eup_myeon",
                            "relation_type": "same_eup_myeon",
                            "confidence": 0.95,
                            "resolution_reason": "데모 입력에서 읍 단위까지만 확인",
                        }
                    ],
                    "relevant": True,
                }
            ],
        },
        {
            "source": {
                **base,
                "source_record_key": "demo-yeongam-samho-2023",
                "title": "[데모] 영암군의회 재생에너지 사업과 주민 의견",
                "metadata": {"fixture": True, "warning": "실제 회의록이 아닌 기능 검증용 데이터"},
            },
            "meeting": {
                "assembly_name": "전라남도 영암군의회",
                "province": "전라남도",
                "city_county": "영암군",
                "meeting_title": "[데모] 재생에너지 사업 민원 보고",
                "meeting_type": "보고",
                "meeting_date": "2023-11-03",
            },
            "page": {"text_original": "영암군 재생에너지 사업 민원 보고"},
            "segments": [
                {
                    "text_original": "대불 인근 발전시설과 관련해 주민 민원이 접수되었다. 소음과 빛반사, 안전 문제를 확인하고 현장 설명을 진행하기로 했다.",
                    "segment_type": "speech",
                    "speaker_name": "이담당",
                    "speaker_role": "과장",
                    "issues": [
                        {"issue_code": "noise_living_discomfort", "polarity": "opposition", "confidence": 0.87, "evidence_span": "소음"},
                        {"issue_code": "glare_reflection", "polarity": "opposition", "confidence": 0.87, "evidence_span": "빛반사"},
                        {"issue_code": "safety_environment", "polarity": "opposition", "confidence": 0.82, "evidence_span": "안전 문제"},
                    ],
                    "places": [
                        {
                            "surface_form": "영암군",
                            "raw_name": "영암군",
                            "normalized_name": "전라남도 영암군",
                            "place_type": "city_county",
                            "province": "전라남도",
                            "city_county": "영암군",
                            "geo_precision": "city_county",
                            "relation_type": "same_city_county",
                            "confidence": 0.85,
                        }
                    ],
                    "relevant": True,
                }
            ],
        },
        {
            "source": {
                **base,
                "source_record_key": "demo-muan-2022",
                "title": "[데모] 무안군 재생에너지 주민 설명회 기록",
                "metadata": {"fixture": True, "warning": "실제 회의록이 아닌 기능 검증용 데이터"},
            },
            "meeting": {
                "assembly_name": "전라남도 무안군의회",
                "province": "전라남도",
                "city_county": "무안군",
                "meeting_title": "[데모] 농지 태양광 관련 주민 설명회",
                "meeting_type": "주민 의견 청취",
                "meeting_date": "2022-07-18",
            },
            "page": {"text_original": "무안군 농지 태양광 주민 설명회"},
            "segments": [
                {
                    "text_original": "농지 훼손과 수익 배분에 대한 주민 우려가 있었고, 사업자는 주민 참여 방안을 검토하기로 했다.",
                    "segment_type": "speech",
                    "issues": [
                        {"issue_code": "agricultural_land_damage", "polarity": "opposition", "confidence": 0.9, "evidence_span": "농지 훼손"},
                        {"issue_code": "external_benefit_distribution", "polarity": "mixed", "confidence": 0.83, "evidence_span": "수익 배분"},
                    ],
                    "places": [
                        {
                            "surface_form": "무안군",
                            "raw_name": "무안군",
                            "normalized_name": "전라남도 무안군",
                            "place_type": "city_county",
                            "province": "전라남도",
                            "city_county": "무안군",
                            "geo_precision": "city_county",
                            "relation_type": "same_city_county",
                            "confidence": 0.85,
                        }
                    ],
                    "relevant": True,
                }
            ],
        },
    ]


def open_db(path: str | Path) -> LuceraDB:
    db = LuceraDB(path)
    db.initialize(SCHEMA_PATH)
    return db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lucera 태양광 분쟁이력 조회 MVP")
    parser.add_argument("--db", default=str(config.DATABASE_PATH), help="SQLite 파일 경로")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="DB 스키마와 기준 데이터를 생성")
    sub.add_parser("seed-demo", help="오프라인 기능 검증용 데모 사례 적재")
    sub.add_parser("seed-synthetic", help="면적·용량·쟁점·처리 과정이 연결된 합성 시나리오 적재")
    sub.add_parser("seed-rules", help="공식 이격거리 규칙(국가 상한 포함) 적재")
    vworld_check = sub.add_parser("vworld-check", help="VWorld 키·지도·레이어가 실제로 응답하는지 점검")
    vworld_check.add_argument("--address", default="전라남도 영암군 삼호읍 산호리 1")
    sub.add_parser("stats", help="적재 건수 확인")
    sub.add_parser("reclassify-keywords", help="보관된 원문에서 고정밀 키워드 분류만 재생성")
    project_create = sub.add_parser("project-create", help="사업 입력정보와 절차·출처 구조를 DB에 저장")
    project_create.add_argument("--json-file", required=True, help="사업 입력 JSON 파일")
    project_show = sub.add_parser("project-show", help="저장된 사업과 단계·출처·과거 사건 연결 조회")
    project_show.add_argument("project_id")
    project_precheck = sub.add_parser("project-precheck", help="사업 입력정보 기반 사전점검 조회")
    project_precheck.add_argument("project_id")
    chat = sub.add_parser("chat", help="주소·면적·용량 기반 로컬 RAG 사전점검")
    chat.add_argument("--json-file", required=True, help="챗봇 입력 JSON 파일")
    sub.add_parser("region-list", help="광주·전남 검색/수집 대상 지역 목록")
    regional = sub.add_parser("collect-regional", help="지역별 고정밀 회의록을 중복 없이 수집")
    regional.add_argument("--target-count", type=int, default=10, help="지역별 목표 문서 수")
    regional.add_argument("--region", action="append", dest="regions", default=[], help="특정 지역만 수집(반복 가능)")
    regional.add_argument("--kind", action="append", dest="kinds", default=[], choices=["city", "county", "autonomous_district"], help="지역 유형 필터: 자치시·자치군·자치구")
    regional.add_argument("--max-api-calls", type=int, default=880, help="이번 실행에서 사용할 API 호출 상한")
    regional.add_argument("--sleep-seconds", type=float, default=0.05, help="호출 사이 대기 시간")
    regional.add_argument("--detail-workers", type=int, default=6, help="상세 원문 병렬 호출 수(1~12)")
    ingest = sub.add_parser("ingest-clik", help="국회도서관 지방의정포털 회의록 적재")
    ingest.add_argument("--keyword", default="태양광")
    ingest.add_argument("--list-count", type=int, default=10, help="목록 조회 건수(최대 100)")
    ingest.add_argument("--detail-limit", type=int, default=None, help="상세 원문 조회 건수")
    ingest.add_argument("--start-count", type=int, default=0)
    ingest.add_argument("--assembly-id", default=None)
    search = sub.add_parser("search", help="주소 기반 분쟁이력 검색")
    search.add_argument("--address", required=True)
    search.add_argument("--radius-m", type=float, default=5_000)
    search.add_argument("--issue-code", action="append", dest="issue_codes", default=[])
    search.add_argument("--keyword", action="append", dest="keywords", default=[])
    search.add_argument("--from-date", default=None)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--lat", type=float, default=None)
    search.add_argument("--lon", type=float, default=None)
    search.add_argument("--offline", action="store_true", help="주소 API 호출 없이 행정구역만 사용")
    serve = sub.add_parser("serve", help="웹 API와 데모 화면 실행")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = open_db(args.db)
    try:
        if args.command == "init":
            print(json.dumps({"database": str(Path(args.db).resolve()), "status": "initialized", **db.stats()}, ensure_ascii=False, indent=2))
        elif args.command == "seed-demo":
            for bundle in demo_bundles():
                db.insert_document_bundle(bundle)
            review_counts = rebuild_case_reviews(db)
            db.commit()
            print(json.dumps({"status": "seeded_demo", **review_counts, **db.stats()}, ensure_ascii=False, indent=2))
        elif args.command == "seed-synthetic":
            result = seed_synthetic(db)
            print(json.dumps({"status": "seeded_synthetic", **result, **db.stats()}, ensure_ascii=False, indent=2))
        elif args.command == "seed-rules":
            count = seed_official_rules(db)
            print(json.dumps({"status": "seeded_rules", "siting_rules": count}, ensure_ascii=False, indent=2))
        elif args.command == "vworld-check":
            print(json.dumps(check_vworld(args.address), ensure_ascii=False, indent=2))
        elif args.command == "stats":
            print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
        elif args.command == "reclassify-keywords":
            result = db.reclassify_keyword_labels("clik_minutes")
            db.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "project-create":
            payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
            result = create_project(db, payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "project-show":
            result = get_project(db, args.project_id)
            if result is None:
                raise ValueError("project not found")
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.command == "project-precheck":
            result = get_precheck(db, args.project_id)
            if result is None:
                raise ValueError("project not found")
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.command == "chat":
            payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
            result = RAGService(db).analyze(payload)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif args.command == "region-list":
            print(json.dumps({"scope": "광주광역시·전라남도", "parent_regions": parent_region_catalog(), "regions": region_catalog()}, ensure_ascii=False, indent=2))
        elif args.command == "collect-regional":
            result = collect_regional(
                db,
                target_count=args.target_count,
                region_names=args.regions or None,
                kinds=set(args.kinds) or None,
                max_api_calls=args.max_api_calls,
                sleep_seconds=args.sleep_seconds,
                detail_workers=args.detail_workers,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "ingest-clik":
            result = ingest_clik(db, args.keyword, args.list_count, args.start_count, args.detail_limit, args.assembly_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "search":
            payload = {
                "address": args.address,
                "radius_m": args.radius_m,
                "issue_codes": args.issue_codes,
                "keywords": args.keywords,
                "from_date": args.from_date,
                "limit": args.limit,
                "resolve_address": not args.offline,
            }
            if args.lat is not None and args.lon is not None:
                payload.update({"latitude": args.lat, "longitude": args.lon})
            result = SearchService(db).search(payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "serve":
            from .server import run_server

            run_server(db, args.host, args.port)
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if args.command != "serve":
            db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
