# 루체라 (Lucera)

태양광 발전사업 예정지의 주소를 입력하면 공개된 지방의회 회의록을 지역·거리·쟁점별로 조회하고, 원문 근거와 출처를 함께 보여주는 MVP입니다.

## 기획서에서 구현한 핵심

- 주소 또는 읍·면·리 입력
- 국회도서관 지방의정포털 회의록 목록/상세 API 수집
- 원본 API 응답, 문서, 페이지, 발언/문단을 분리 보존
- 문단 → 문장 → 키워드 발생 → 회의 내 episode → 동일 민원 case 계층과 근거 연결
- 네 가지 문제 카테고리(주민·민원·갈등 / 입지·토지·인허가 / 피해·환경·안전 / 수익·보상·지역경제)와 세부 쟁점 분류
- 정확한 좌표가 있으면 반경 검색, 좌표가 없으면 읍·면·리·시·군 단위 연결
- 광주광역시·5개 자치구·전라남도·22개 시군의 지역 카탈로그와 의회 ID 관리
- 주소 입력 시 행정구역 우선 검색, 좌표가 양쪽에 있을 때만 실제 거리 검색
- 좌표가 없는 회의록은 문서에 언급된 장소를 `case_location_candidate`로 순위화하고 추론 위치와 확정 위치를 구분
- 모든 결과에 `evidence_id`, 문서 ID, 페이지, 출처 URL, 장소 정밀도, 근거 문장을 반환
- 허가·계통 접속 여부를 자동 판정하지 않고 참고 자료로 명시

키워드가 곧 민원 한 건은 아닙니다. 검색 결과에는 `case_id`와 `episode_id`가 함께 붙고, 같은 회의에서 이어진 문단·문장은 하나의 episode로 묶입니다. 같은 안건이라도 `또 다른 민원`, `다음 안건`, `다른 사업` 같은 명시적 전환어가 나오면 분리합니다. 강한 사업명·주소·리·읍면 식별자가 없으면 서로 다른 문서의 결과를 같은 case로 자동 병합하지 않고, `pending` 검수 상태와 `case_link_candidate`로 남깁니다. 자동 생성 case는 `case_review`에서 문장 단위 태양광 주제성·분쟁성·지명 식별성·문단 분리성을 재평가하며, 기본 검색은 `eligible`만 노출합니다.

## 빠른 실행

Windows PowerShell에서 프로젝트 루트에서 실행합니다.

```powershell
$py = 'C:\Users\seoco\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m lucera.cli init
& $py -m lucera.cli seed-demo
& $py -m lucera.cli search --address '전라남도 영암군 삼호읍' --offline
& $py -m lucera.cli serve --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. 데모 사례는 실제 회의록이 아니라 오프라인 기능 검증용임을 화면에 표시합니다.

`serve`는 기동 시 검색 캐시를 예열합니다. 회의록 문단이 21만 건이라 첫 검색은 디스크에서 읽는 비용을 전부 부담하는데(측정: 첫 질의 28초, 이후 2초), 예열이 그 비용을 부팅 쪽으로 옮깁니다. 시연 직전에 서버를 미리 띄워 두십시오.

## 설치 예정지 사전점검 파이프라인

현재 챗봇은 외부 AI API 없이도 다음 파이프라인을 실행합니다.

`입력 정규화 → 위치·행정구역 검색 → 회의록 근거 검색 → 민원 처리과정 추출 → 수치 룰 검산 → 근거 기반 답변`

면적·용량·거리 규칙은 `siting_rule`에서 읽어 결정론적으로 계산합니다. 회의록의 쟁점은 `segment_issue`, 처리 과정은 `case_process_event`에 원문 문단과 함께 저장합니다. 답변 문장은 Claude가 쓰지만, 판정과 숫자는 전부 이 단계에서 이미 확정된 값입니다.

### 사전점검 입력 4종

| 입력 | 필드 | 무엇에 쓰이는가 |
|---|---|---|
| 설치 예정지 주소 | `address` | 행정구역 정규화 → 같은 시군·읍면의 회의록 근거, 주변 허가 사업 검색 |
| 부지면적 | `site_area_sqm` | 설비용량 대비 부지 규모 검산, 부지 이용률 분모 |
| 설비용량 | `capacity_kw` | 위 두 비율의 분모, 주변 사업 규모 비교 |
| 실제 설치면적 | `installation_area_sqm` | 부지 이용률, 그리고 허가 원장 실측 분포와의 대조 |

선택 입력인 `nearest_residence_m`, `nearest_road_m`을 함께 보내면 이격거리 규칙이 실제로 판정됩니다. 넣지 않으면 해당 항목은 `check_required`로 남고 미충족으로 처리하지 않습니다.

### 사전점검 테이블 준비

브라우저 원문으로 재구축한 DB에는 `siting_rule`과 `case_process_event`가 없습니다. 없으면 이격거리 판정과 처리과정 타임라인이 조용히 비어 있는 채로 동작하므로, 재구축 후에는 아래를 실행합니다.

```powershell
& $py scripts\migrate_precheck_tables.py
& $py -m lucera.cli seed-rules
& $py scripts\backfill_process_events.py --time-budget-seconds 600
& $py scripts\load_permit_projects.py
```

- `seed-rules`는 2026-09-18 시행 재생에너지 시행령의 이격거리 상한(주거지 200m, 도로 기준 설정 금지)을 `data_origin='official'`로 넣습니다. 상한은 조례가 요구할 수 있는 최대값이지 부지 판정 기준이 아니므로, 상한보다 멀면 `pass`, 가까우면 조례 조문을 확인해야 하는 `check_required`가 됩니다.
- 시군 조례의 실제 이격거리는 조문을 확인한 뒤에만 `lucera.ordinance.upsert_rule`로 넣습니다. 넣기 전까지는 `ordinance-not-loaded` 항목이 미적재 상태를 그대로 표시합니다.
- `load_permit_projects.py`는 공공데이터포털 허가 원장에서 광주·전남 16,649건을 적재합니다. 이 원장의 `instlArea`가 설치면적 비교의 근거이며, 값이 없는 지역에서는 비교 카드를 만들지 않고 "비교 기준 없음"으로 표시합니다.

### VWorld 주변 상황 (멀티모달)

`config.py`의 `PUBLIC_DATA_KEYS["vworld"]`(환경변수 `VWORLD_API_KEY`)로 설치 예정지 주변의 항공영상·배경지도와 용도지역·지적·도로 레이어를 가져와 답변 생성기에 함께 넘깁니다.

먼저 키와 레이어가 실제로 응답하는지 점검합니다. 레이어 코드와 키 등록 상태는 둘 다 요청 시점에 조용히 실패하므로, 어느 쪽이 문제인지 따로 보고합니다.

```powershell
& $py -m lucera.cli vworld-check --address "전라남도 영암군 삼호읍 산호리 1"
```

- **지오코딩이 실패하면** 키가 등록된 서비스 URL과 `config.VWORLD_DOMAIN`이 같은지 확인합니다. VWorld는 `domain` 파라미터를 등록 URL과 대조하므로, 키가 유효해도 도메인이 다르면 인증 오류가 납니다. 현재 값은 키를 신청한 `http://localhost:3000`이며, Lucera가 실제로 서비스하는 포트(`serve --port 8000`)와는 무관합니다.
- **특정 레이어만 실패하면** `config.VWORLD_FEATURE_LAYERS`에서 그 항목을 빼거나 코드를 고칩니다. 레이어 하나가 실패해도 나머지는 그대로 진행합니다.
- 지도 이미지는 `data/work/vworld_cache/`에 캐시되고, 화면에는 `/v1/map-image/{cache_key}`로 제공됩니다.

가져오는 것은 세 가지입니다(2026-09-04 실제 응답 확인).

| 요청 | 레이어 | 쓰는 값 |
|---|---|---|
| 부지 용도지역 | `LT_C_UQ111` buffer 0 | `uname` (예: 생산녹지지역) |
| 반경 300m 용도지역 | `LT_C_UQ111` buffer 300 | `uname` 중 주거지역 포함 여부 |
| 이 필지 | `LP_PA_CBND_BUBUN` `attrFilter=pnu:=:...` | `jibun`("1 답")의 지목, `jiga`(개별공시지가 원/㎡) |

용도지역을 두 번 묻는 이유는, buffer 0은 **이 부지가 무엇인지**이고 buffer 300은 **무엇 옆에 있는지**이기 때문입니다. 둘을 합치면 옆 동네의 주거지역이 이 부지의 용도지역인 것처럼 보고됩니다.

지적 레이어에는 **면적 컬럼이 없습니다.** 입력한 부지면적을 지적도로 대조할 수는 없고, 대신 다음 두 가지가 규칙으로 들어갑니다.

- **지목** → `cadastre-land-category`. 전·답·과수원·목장용지면 「농지법」 농지전용허가, 임야면 「산지관리법」 산지전용허가 확인 대상으로 표시합니다. 지목은 지적공부에 기재된 사실이고 전용허가 대상 여부는 법이 정하므로, 판단이 아니라 조회입니다.
- **반경 내 주거지역** → `zoning-nearby-residential`. 사용자가 `nearest_residence_m`을 넣지 않아도 이격거리 검토가 필요한지 신호를 줍니다. 단 **거리는 아닙니다.** API는 버퍼와 교차하는 폴리곤을 돌려줄 뿐 거리를 주지 않으므로, "반경 안에 포함되어 있음"까지만 말합니다. 주거지역으로 지정되지 않은 개별 주택은 이 도면에 없으므로, 주거지역이 없다는 결과를 "주변에 집이 없다"로 읽으면 안 됩니다.

주소에 리·번지 또는 도로명+건물번호가 있어야 지오코딩합니다. `영암군 삼호읍`처럼 읍면까지만 입력하면 읍 중심점을 설치 위치로 만들지 않고 `address_not_specific_enough`로 건너뜁니다. 지오코딩이 성공하면 좌표가 확보되므로 반경 검색과 거리 계산도 함께 켜집니다.

**이미지에서 읽은 것은 숫자가 될 수 없습니다.** 모델의 관찰은 `map_observations`에만 들어가고, 여기에 숫자가 하나라도 있으면 답변 전체가 거부됩니다. "북측에 주택이 모여 있음"은 되지만 "북측 100m에 주택"은 안 됩니다. 거리·개수·면적은 룰 엔진과 허가 원장이 계산한 값만 씁니다. 화면에서도 이 절은 "AI 판독 · 실측 아님"으로 따로 표시됩니다.

### Claude 답변 생성기

`.env`의 `claude_key`(또는 `ANTHROPIC_API_KEY`)가 있으면 답변 문장을 Claude가 씁니다. 모델은 `CLAUDE_MODEL`로 바꿀 수 있고 기본값은 `claude-sonnet-5`입니다.

모델에게는 이미 계산이 끝난 evidence pack만 전달하고, 생성된 결과는 두 가지 검사를 통과해야 사용자에게 나갑니다.

1. **인용 검사** — 각 사유의 `evidence_ids`가 pack의 `allowed_evidence_ids` 안에 있어야 합니다.
2. **숫자 검사** — 답변에 나온 모든 숫자가 pack에 이미 있는 값이어야 합니다. 모델이 거리나 건수를 직접 계산하면 거부됩니다.

둘 중 하나라도 실패하거나 API 호출이 실패하면 규칙 기반 답변으로 자동 전환되고, 응답의 `answer_generation`과 화면 배지에 그 사유가 표시됩니다. `LUCERA_ANSWER_MODE=local`을 주면 호출 자체를 하지 않습니다.

오프라인에서 서로 연결된 합성 시나리오를 넣어 보려면 다음을 실행합니다. 합성 문서·규칙·주변 발전사업에는 모두 `data_origin=synthetic`이 표시됩니다.

```powershell
& $py -m lucera.cli seed-synthetic
& $py -m lucera.cli chat --json-file .\chat-input.synthetic.json
```

`chat-input.synthetic.json` 예시는 다음과 같습니다.

```json
{
  "address": "전라남도 함평군 손불면",
  "site_area_sqm": 6200,
  "installation_area_sqm": 4500,
  "capacity_kw": 480,
  "nearest_residence_m": 150,
  "nearest_road_m": 120,
  "latitude": 35.1,
  "longitude": 126.52,
  "review_mode": "all",
  "message": "빛반사와 주민 협의 이력을 중심으로 설치하면 안 되는 이유를 확인해줘"
}
```

`as_of`를 함께 보내면 그 날짜 기준으로 규칙의 유효기간을 평가합니다. 2026-09-18 이전 날짜를 넣으면 국가 상한이 "시행 예정"으로 표시되고, 이후 날짜를 넣으면 적용 상태로 바뀝니다.

HTTP에서는 같은 JSON을 `POST /v1/chat`으로 보냅니다. 응답의 `analysis`에는 결론, 규칙별 통과·미충족·확인 필요 상태, 쟁점별 reason card, 처리과정 timeline, 주변 사업 현황과 한계가 들어가며, `grounding`에는 사용된 `evidence_id`, `process_event_id`, `rule_id`, `permit_project_id`가 들어갑니다. `citation_required=true`이므로 이후 AI 답변기도 이 목록 밖의 사실을 단정하지 않도록 연결할 수 있습니다.

주소에 읍·면만 입력하면 좌표를 임의로 만들지 않습니다. 정확한 거리를 계산하려면 `latitude`, `longitude`를 함께 보내거나 도로명·번지 주소에 `resolve_address=true`를 사용해야 합니다.

## 실제 회의록 적재

`config.py`의 국회도서관 공개 API 키를 사용해 목록과 상세 원문을 적재합니다. 호출당 목록은 100건 이하로 제한되고, MVP 기본값은 상세 10건입니다. API 상세 응답은 `source_document.raw_payload_json`뿐 아니라 `data/dataset/minutes/original/api_json/`의 동일 checksum JSON 파일에도 보존됩니다.

```powershell
& $py -m lucera.cli ingest-clik --keyword '태양광' --list-count 20 --detail-limit 20
& $py -m lucera.cli search --address '전라남도 영암군 삼호읍' --offline --limit 20
```

수집 키워드는 무작정 단어를 OR로 합치지 않습니다. `태양광` 계열을 주제 앵커로 사용하고, `태양광 민원`, `태양광 주민반대`, `태양광 이격거리`, `태양광 빛반사`, `태양광 농지`처럼 문제어와 조합합니다. `빛반사`, `반사광`, `눈부심`, `염해농지`, `이격거리`, `햇빛연금`은 고정밀 보완 검색어로 별도 수집합니다. `주민`, `환경`, `안전`, `협의` 같은 일반어는 단독 수집어로 사용하지 않습니다. 동일 문서를 다시 적재하면 `source_system + source_record_key`로 upsert됩니다.

## 브라우저 확보 원문으로 별도 DB 만들기

브라우저에서 직접 내려받은 공식 PDF, HWP/HWPX, HTML viewer snapshot은 API DB와 분리해 `data/db/lucera_minutes.sqlite3`로 재구축할 수 있습니다. 공식 PDF는 `data/dataset/minutes/original/pdf/`에 원본 그대로 보존하고, HWP/HWPX는 `normalized/pdf_from_hwp/`의 한컴 PDF로 변환합니다. HTML은 PDF/HWP 원문이 없는 경우의 fallback으로 `normalized/pdf_from_html/`에 인쇄용 PDF를 만들며, 모든 PDF는 `extracted/opendataloader/`의 JSON/text로 분석합니다. 재구축 시 문장 단위 분류와 광주·전남 지명사전 대조를 다시 수행합니다.

```powershell
$py = 'C:\Python313\python.exe'
& $py scripts\convert_hwp.py
& $py scripts\html_to_pdf.py
& $py scripts\build_browser_db.py
& $py scripts\validate_browser_db.py
```

현재 보관된 회의록 PDF를 제한 없이 처음부터 재구축하려면 별도 DB로 만든 뒤 검증하고 승격합니다.

```powershell
& $py scripts\build_browser_db.py --all-inputs --db data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3
& $py scripts\validate_browser_db.py --db data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3
& $py scripts\promote_rebuilt_db.py --rebuilt data\db\lucera_minutes_rebuild_YYYYMMDD.sqlite3 --promote
& $py scripts\ingest_collected_api_json.py --db data\db\lucera_minutes.sqlite3
& $py scripts\validate_browser_db.py --db data\db\lucera_minutes.sqlite3
```

공식 PDF manifest에 새 파일을 추가한 뒤 기존 문서는 재처리하지 않고 증분 반영하려면 다음을 실행합니다.

```powershell
& $py scripts\build_browser_db.py --official-only
```

새 DB의 `document_artifact`에는 직접 다운로드 PDF 원본, HWP/HTML 변환 PDF, HTML snapshot, OpenDataLoader JSON/text와 각 산출물의 부모 artifact가 SHA-256과 함께 저장됩니다. API 상세 원문은 `original/api_json/`에, 목록 응답은 `original/api_listings/`에 보존합니다. 수집 manifest는 `manifests/`, 검증·분석 보고서는 `reports/`, 시각 QA 이미지는 `qa/`에 분리합니다. 직접 PDF 10건은 `data/dataset/minutes/original/pdf/`과 `manifests/pdf_original_manifest.json`에서 확인할 수 있습니다. 운영 문서와 지역별 검증표는 [브라우저 원문 새 DB 운영 문서](docs/브라우저_원문_새DB_운영.md)와 `data/dataset/minutes/reports/`에 남습니다.

## 광주·전남 지역별 수집

`region-list`는 검색·수집 대상 27개 지역 단위(자치시 5·자치군 17·자치구 5)를 반환합니다. 광주광역시와 전라남도는 상위권역 메타데이터로 별도 관리하며, 실제 지역별 목표에는 중복해서 세지 않습니다. 현재 공개 API에서 의회 ID가 확인되지 않은 광산구는 카탈로그에 `available=false`로 남겨 억지로 다른 의회 자료를 섞지 않습니다.

27개 지역의 고정밀 회의록을 지역당 우선 15건씩 적재하려면 다음처럼 실행합니다. 목록 결과는 후보이고, 상세 원문을 성공적으로 받은 문서만 DB에 저장합니다. 기존 문서는 DOCID로 건너뛰므로 중단 후 재실행할 수 있습니다. 이전 실행에서 15건을 초과해 저장된 문서는 삭제하지 않고 원문 보존용으로 유지합니다.

```powershell
& $py -m lucera.cli region-list
& $py -m lucera.cli collect-regional --target-count 15 --max-api-calls 450 --sleep-seconds 0.08 --detail-workers 6
```

수집어는 `태양광` 앵커, 태양광+민원/반대/허가/이격거리/빛반사 등의 조합, `빛반사·반사광·눈부심·염해농지·햇빛연금` 고정밀 단독어로 제한합니다. `주민`, `환경`, `안전`, `협의`만으로 문서를 태양광 민원으로 편입하지 않습니다. 공개 API의 지역별 실제 색인 건수가 15건보다 적거나 색인이 없으면 결과의 `shortfall`과 `reason`으로 보고합니다.

지역별 목표를 채울 때는 문서 수를 억지로 맞추지 않도록 먼저 감사표를 생성합니다. `candidate_pool`은 정밀 후보, `local_pdf_materialized`는 실제 PDF가 있는 후보, `raw_api_pool`은 로컬에 보존된 API 상세 원문 전체입니다. 후보가 충분한데 PDF·변환·적재가 빠진 경우는 `collection_gap`, 정밀 분쟁 후보 자체가 부족한 경우는 `insufficient_pool`, 의회 식별자가 없는 경우는 `no_source`로 구분합니다.

```powershell
& $py scripts\audit_regional_coverage.py --phase before
```

API 호출한도나 브라우저 다운로드 공백이 있을 때는 이미 보존된 상세 API 원문을 현재 문장 단위 분류기로 재검토해 보강할 수 있습니다. 이 경로는 원문 JSON과 페이지·문단을 DB에 남기지만, 로컬 PDF가 없는 문서를 PDF로 가장하지 않습니다. 분쟁 근거가 없는 지원·정책 문서는 15건을 채우기 위한 패딩으로 추가하지 않습니다.

```powershell
& $py scripts\enrich_browser_db_from_local_api.py --time-budget-seconds 420
& $py scripts\audit_regional_coverage.py --phase after_enrichment
& $py scripts\validate_browser_db.py --db data\db\lucera_minutes.sqlite3
```

전체 재구축 직후에는 `ingest_collected_api_json.py`도 실행합니다. 이 명령은 현재 `original/api_json/`에 실제 보관된 상세 원문만 전수 검사해 신규 고정밀 분쟁 후보를 통합합니다. 기존 DB에 이미 있는 DOCID는 건너뛰고, 분쟁 근거가 없는 문서는 적재하지 않습니다. API 문서는 `source_document.raw_payload_json`과 동일 checksum의 JSON artifact를 갖지만, 실제 PDF가 아니므로 PDF artifact로 표시하지 않습니다.

```powershell
& $py scripts\ingest_collected_api_json.py --db data\db\lucera_minutes.sqlite3
& $py scripts\validate_browser_db.py --db data\db\lucera_minutes.sqlite3
```

## HTTP API

### `POST /v1/meeting-evidence/search`

```json
{
  "address": "전라남도 영암군 삼호읍",
  "radius_m": 5000,
  "issue_codes": ["communication_procedure"],
  "from_date": "2020-01-01",
  "limit": 20,
  "resolve_address": true,
  "review_mode": "eligible",
  "include_comparative": false
}
```

주소에 도로명/번지와 숫자가 포함되면 도로명주소 API를 호출합니다. 읍·면·리만 입력한 경우에는 임의 건물로 지오코딩하지 않고 행정구역 파싱만 합니다. 좌표 검색을 직접 검증할 때는 `latitude`, `longitude`를 함께 보낼 수 있습니다.

위치 검색은 세 단계입니다. (1) 입력 주소에서 도·시군구·읍면동·리를 정규화해 같은 행정구역의 근거를 찾고, (2) 입력 좌표와 근거 장소 좌표가 모두 있을 때만 하버사인 거리로 반경을 계산하며, (3) 회의록에 장소는 있으나 좌표가 없으면 `case_location_candidate`에 후보·정밀도·근거 문단·신뢰도를 저장합니다. 이 후보는 “민원이 발생한 설치 필지”의 추론 근거이지 좌표 확정값이 아닙니다. 따라서 좌표가 없는 결과의 `distance_m`은 항상 `null`, `distance_status`는 `unknown`입니다.

검색 응답의 `case_groups`에는 민원 후보별 `paragraphs`가 포함되며, 각 항목의 `text_original`에 해당 민원에 연결된 원문 문단이 들어 있습니다. `case_paragraph_limit`은 민원별 반환 상한이며 `0`이면 제한이 없습니다. `review_mode=eligible`은 자동 검토 통과 사건만, `needs_review`는 보류 사건만, `all`은 원문 후보 전체를 반환합니다. 비교 지역 사례는 `include_comparative=true`일 때만 포함합니다.

### `GET /v1/cases/{case_id}/paragraphs`

민원 하나의 전체 원문 문단을 페이지·문단 순서·회의일·출처와 함께 반환합니다. 문서·민원·문단 연결은 `document_cases`와 `case_paragraphs` view로도 직접 조회할 수 있습니다.

### `GET /v1/cases/{case_id}/timeline`

민원 하나에 대해 접수·질의·조사·협의·조치·후속 과정과 각 과정을 뒷받침하는 문단 ID를 반환합니다. 과정 추출은 현재 결정론적 규칙 기반이며, 각 이벤트는 `pending` 검수 상태로 남습니다.

### `POST /v1/ingest/clik`

```json
{ "keyword": "태양광", "list_count": 10, "detail_limit": 10 }
```

### 사업 입력·사전점검 API

사업 입력은 공개 회의록의 `case_id`와 분리된 사용자 사업 `project_id`로 저장합니다. 입력을 다시 제출하면 같은 `project_key` 아래에 새 `revision_no`가 생기고 이전 신청은 `superseded`가 됩니다.

```http
POST /v1/projects
GET  /v1/projects/{project_id}
GET  /v1/projects/{project_id}/precheck
```

최소 입력은 `project_name`과 `site_address`입니다. 나머지는 평탄한 JSON 또는 분야별 중첩 JSON으로 보낼 수 있습니다.

```json
{
  "project_key": "lucera-demo-001",
  "business": {
    "project_name": "○○ 태양광 발전사업",
    "business_type": "태양광발전시설",
    "permit_type": "발전사업허가"
  },
  "site": {
    "site_address": "전라남도 ○○군 ○○면 ○○리",
    "lot_number": "123-4",
    "land_category": "전"
  },
  "equipment": {
    "installed_capacity_kw": 999,
    "module_count": 1998,
    "module_capacity_w": 500
  },
  "permits": {
    "development_permit_required": true,
    "environmental_assessment_required": true
  },
  "resident": {
    "complaint_occurred": true,
    "complaint_type": "빛반사·경관훼손"
  },
  "business_plan": {
    "file_name": "business-plan.pdf",
    "storage_uri": "local://uploads/business-plan.pdf",
    "extraction_status": "queued"
  }
}
```

저장 후에는 `사업 신청 → 발전사업허가 → 개발행위·환경검토 → 주민 협의·민원 → 공사·준공 → 계통연계·사업개시` 6단계가 모두 만들어집니다. 날짜가 있는 단계는 `project_stage_event`에 이력으로 남고, 주소는 좌표가 없어도 정규화 주소·시군·읍면·리·정밀도와 함께 후보 장소로 보존됩니다. 과거 민원과의 비교는 `project_case_link`에 `pending` 검수 링크로 저장하며, 사업 입력 `project_id`와 과거 민원 `case_id`를 혼동하지 않습니다.

입력 스키마는 `project-intake-v2`입니다. 46개 입력 필드의 표시명·타입·단위·필수 여부·단계 연결은 `project_field_definition`에서 관리하고, 모든 입력값은 구조화 테이블과 `project_fact`에 함께 저장됩니다. 첨부파일은 PDF/HWP/HWPX/변환파일을 구분하지 않고 파일 해시·추출기·추출 버전·페이지·오류·텍스트 위치를 보존하며, 상세 컬럼은 [DB 컬럼 명세서](docs/DB_컬럼명세서.md)를 참조하세요.

CLI로는 다음처럼 사용할 수 있습니다.

```powershell
& $py -m lucera.cli project-create --json-file .\project.json
& $py -m lucera.cli project-show <project_id>
& $py -m lucera.cli project-precheck <project_id>
```

### `GET /health`

서비스와 핵심 테이블 건수를 반환합니다.

## 파일 구조

```text
config.py                 # 제공받은 공공데이터 키, 환경변수 우선
lucera/                   # 주소 해석, 수집, 추출, 검색, HTTP 서버
lucera/gazetteer.py       # 광주·전남 읍면동·리 지명 검증
lucera/review.py          # 문장 기반 사건 검토·점수·review_task 생성
data/db/lucera_minutes.sqlite3 # 현재 운영 SQLite DB
data/db/snapshots/        # 과거 DB 스냅샷
data/db/backups/          # 복구용 SQLite 백업
data/dataset/minutes/     # 회의록 원문·변환본·추출본·manifest·검증물
data/reference/gazetteer/ # 검증된 행정구역·지명 사전
db/schema.sql             # 실행 가능한 SQLite MVP 스키마
db/schema.postgres.sql    # PostGIS 운영 전환용 스키마
administrative_region table # DB에 저장되는 광주·전남 27개 지역 카탈로그
web/index.html            # 주소 기반 조회 화면
docs/                     # 기획서 이해와 구현/DB 설계 문서
tools/legacy/             # 초기 공공데이터 일괄 구축 도구
```

## 중요한 데이터 원칙

1. 원문과 마스킹 검색 텍스트를 분리합니다. 외부 AI/검색에는 `text_redacted`를 사용하고, `text_original`은 근거 보존용으로 남깁니다.
2. 장소 언급, 표준 장소, 후보 해석, 문단-장소 연결을 분리합니다. 동일한 마을명이 여러 지역에 있을 때 후보를 잃지 않습니다.
3. 좌표 없는 자료에 거리값을 만들지 않습니다. `distance_status=unknown`과 `geo_precision=eup_myeon/ri/city_county`로 표시합니다.
4. 자동 추출 결과는 `pending` 검수 상태로 저장합니다. 원문에 없는 확정 정보를 만들어 운영 테이블에 넣지 않습니다.
5. 검색 결과는 허가·계통 접속 판정이 아닙니다. 기획서가 요구한 “먼저 확인할 근거”를 제공하는 기능입니다.
6. 키워드 분류는 `keyword-precision-v2`로 기록합니다. 빛반사 계열은 단독 고정밀 후보로 허용하지만, 주민·환경·안전 등 일반어는 태양광 문맥과 문제어 조합이 없으면 쟁점으로 저장하지 않습니다.
7. 지역당 15건 목표는 수집 목표입니다. 공개 색인에 없는 지역을 일반 회의록으로 채워 15건처럼 보이게 하지 않고, 지역·쿼리별 총건수와 미달 사유를 보존합니다.

상세한 기획서 해석과 테이블별 설계는 [docs/기획서_이해와_구현.md](docs/기획서_이해와_구현.md), [docs/DB_구조.md](docs/DB_구조.md), 기존 실행 설계서 [회의록_DB_구축_실행설계서.md](회의록_DB_구축_실행설계서.md)를 참고합니다.
