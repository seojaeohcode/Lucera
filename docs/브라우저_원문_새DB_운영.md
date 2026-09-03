# 브라우저 원문 기반 새 DB 운영 설계

## 목적

브라우저에서 실제로 확인·저장한 지방의회 회의록과 현재 보관된 CLiK API 상세 원문을 하나의 운영 DB에 적재한다. 두 수집 경로를 무리하게 같은 파일 형식으로 취급하지 않고, 원문 형식·취득 경로·변환·분석 결과를 artifact와 metadata로 모두 추적한다.

## 입력 경로

1. 공식 홈페이지에서 실제 다운로드 가능한 PDF가 확인되면 `data/dataset/minutes/original/pdf/`에 원본 그대로 보존하고 `manifests/pdf_original_manifest.json`에 게시 페이지·다운로드 URL·문서일·지역 귀속을 기록한다.
2. PDF 원문이 없고 HWP 또는 HWPX만 제공되면 `data/dataset/minutes/original/hwp/region_<지역코드>/`에 보존한 뒤 한컴 자동화로 PDF를 `normalized/pdf_from_hwp/`에 만든다.
3. PDF/HWP 원문이 모두 없고 HTML viewer만 제공되는 경우에만 브라우저 DOM snapshot을 `original/html/`에 보존하고, 이를 `normalized/pdf_from_html/`의 fallback PDF로 만든다.
4. 모든 PDF는 OpenDataLoader로 JSON과 text를 `extracted/opendataloader/`에 생성한다. JSON의 페이지·bounding box·heading/paragraph 정보를 파싱 입력으로 사용한다.

## 실행 순서

```powershell
$py = 'C:\Python313\python.exe'
& $py scripts\convert_hwp.py
& $py scripts\html_to_pdf.py
& $py scripts\build_browser_db.py --all-inputs --db data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3
& $py scripts\validate_browser_db.py --db data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3
& $py scripts\promote_rebuilt_db.py --rebuilt data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3 --promote
& $py scripts\ingest_collected_api_json.py --db data\db\lucera_minutes.sqlite3
& $py scripts\validate_browser_db.py
```

공식 PDF만 새로 추가하는 경우에는 기존 문서를 다시 처리하지 않고 다음 증분 명령을 사용한다.

```powershell
& $py scripts\build_browser_db.py --official-only
```

실행 결과는 `data/db/lucera_minutes.sqlite3`에 저장한다. HWP 변환이 실패한 파일은 `manifests/conversion_manifest.json`에 `failed`로 남기며, 그 파일을 임의로 PDF로 채우지 않는다.

## 계층과 출처

```text
source_document
  └─ meeting
      └─ document_page
          └─ meeting_segment (문단/발언블록)
              └─ sentences
                  └─ keyword_mentions
              └─ episodes ── episode_evidence
                  └─ conflict_case ── case_evidence / case_segment
                                      └─ case_location_candidate
```

화면의 기본 조회 계층은 다음 두 read view로 고정한다.

```text
document_cases (문서별 민원 목록)
  └─ case_paragraphs (해당 민원의 여러 문단 목록)
```

`document_cases`는 문서-민원별 문단 수·episode 수를 요약하고, `case_paragraphs`는 민원에 연결된 모든 문단을 페이지·순서·원문과 함께 반환한다. 동일 민원이 여러 문서에 걸치면 문서별 행이 생기고, `case_id`로 전체 이력을 다시 묶을 수 있다.

`document_artifact`는 파일 단위 출처 사슬이다.

```text
official PDF download ──> opendataloader_json ──> extracted_text
HWP/HWPX download ──────> rendered_pdf ─────────> opendataloader_json ──> extracted_text
HTML snapshot ──────────> rendered_pdf ─────────> opendataloader_json ──> extracted_text
```

직접 내려받은 공식 PDF는 `original_download` artifact 자체가 분석 원문이며 `acquisition_method=browser_official_pdf`로 표시한다. HWP·HTML 경로의 변환 PDF에는 부모 artifact를 연결한다. 모든 경로에 SHA-256과 파일 크기를 저장하며, 공식 게시 페이지와 실제 다운로드 URL도 함께 보존한다.

이번 직접 PDF 10건 중 광산구 경관위원회 회의록 1건만 `062007(광산구)`에 귀속했다. 농어업·농어촌특별위원회 자료 9건은 전국 단위 공식 보조자료이므로 `administrative_region_code=NULL`로 두어 광주·전남 지역 민원으로 잘못 집계하지 않는다. API 상세 원문은 `api_detail_response`로 별도 식별하며, PDF가 없는 경우에도 `MINTS_HTML` 원문을 문단·문장으로 분석한다.

## 민원 분리 규칙

- 일반 키워드 발생만으로 case를 만들지 않는다.
- 빛반사·반사광·염해농지·이격거리처럼 고정밀인 쟁점은 단독 후보로 허용한다.
- `주민`, `환경`, `안전`, `협의` 등 일반어는 태양광 앵커 또는 다른 강한 설비/인허가 문맥과 결합될 때만 신호가 된다.
- 같은 문서에서 연속된 신호는 같은 안건·장소·설비·쟁점의 점수로 episode를 묶는다.
- `또 다른 민원`, `다음 안건`, `다른 사업` 등의 전환 표현, 안건·장소·사업 식별자 변경은 episode를 분리한다.
- 강한 읍면리/주소/사업명 식별자가 없으면 문서 간 자동 병합하지 않고 별도 case와 `case_link_candidate` 검수 항목으로 남긴다.
- case의 문단 리스트는 `case_evidence`와 `case_segment`를 통해 원문 문단 ID로 직접 조회할 수 있다.

## 위치 추정 규칙

자연어에서 도·시군·읍면·리·마을·도로명·지번·건물명을 추출한다. 행정구역명만 있는 경우에는 그 행정구역을 후보 장소로 저장하고, 중심점이나 임의 좌표를 생성하지 않는다. 도로명·지번·건물 수준의 실제 좌표가 양쪽 모두 확보된 경우에만 반경 거리를 계산한다. 따라서 운영 화면에서 다음을 구분한다.

- 확정/검토 좌표: 실제 지오코딩 결과와 출처가 있는 장소
- 추론 후보: 원문에 언급된 행정구역·마을·주소와 근거 문단
- 거리 미계산: 좌표가 없는 후보. `distance_m = NULL`, `distance_status = unknown`

## 검증 기준

`validate_browser_db.py`는 27개 지역 각각에 대해 HWP 원문, 변환 PDF, HTML snapshot, HTML PDF, DB 문서와 근거 case 수를 보고한다. 또한 직접 공식 PDF artifact 수, 모든 문서의 artifact 보유 여부, 변환 산물의 부모 artifact 존재 여부, 문서→페이지→문단→문장과 episode/case 근거 연결이 끊기지 않았는지 검사한다.

지역별 수집 건수는 공개 색인과 브라우저 접근 결과의 관측값이다. 10건 목표에 못 미치는 지역을 다른 지역 문서로 채우지 않으며, 빈 지역은 빈 상태 그대로 보고한다.
