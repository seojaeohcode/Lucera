# SolarCheck 회의록 DB 구축 실행설계서

작성일: 2026-08-29

## 0. 결론

회의록을 단순히 잘라서 벡터 DB에 넣는 방식은 채택하지 않는다. SolarCheck가 실제로 필요한 것은 다음의 연결이다.

```text
원본 문서 → 페이지 → 안건/발언 문단 → 장소 언급 → 표준 장소 → 쟁점 → 근거 문장 → 검색/요약
```

최적안은 다음과 같다.

- 원본 PDF·HWP·HTML·페이지 이미지는 Ncloud Storage에 보존한다.
- PostgreSQL을 기준 DB로 사용한다.
- PostGIS로 주소·행정구역·반경 검색을 처리한다.
- pgvector로 문단 의미 검색을 처리한다.
- pg_trgm을 함께 사용해 한국어 고유명사·마을명·발전소명 검색을 보완한다.
- AI 결과는 원문을 대체하지 않고, 반드시 원본 페이지와 문단에 연결한다.
- 회의록은 `AI 전수 독해 + 로직 전수 검증`, CSV·JSON은 `로직 전수 처리 + AI 예외 해석`으로 분리한다.
- AI가 추출한 결과는 곧바로 운영 테이블에 쓰지 않고 `staging → validation → approved` 단계를 거친다.
- 주소가 정확히 확인되지 않으면 자동으로 확정하지 않고 `확인 필요` 상태로 남긴다.

현재 아키텍처 이미지의 `RAG/ETL Worker → Cloud DB for PostgreSQL/PostGIS/pgvector → FastAPI` 흐름은 적합하다. 다만 1박 2일 MVP에서는 MCP Adapter, 별도 CDN, 복잡한 마이크로서비스 분리, 자동 확장까지 구현하지 않는다.

## 0A. 2026-09-02 현재 구현 업데이트

이 설계서를 기준으로 주소 기반 분쟁이력 조회 MVP를 구현했다.

- 실행 DB: SQLite + FTS5 (`data/db/lucera_minutes.sqlite3`)
- 운영 전환 스키마: `db/schema.postgres.sql` (PostGIS, pg_trgm, pgcrypto, pgvector 확장 지점 포함)
- API: `POST /v1/meeting-evidence/search`, `POST /v1/ingest/clik`, `GET /health`
- 수집: 국회도서관 지방의정포털 회의록 목록/상세 API
- 주소 해석: 전체 도로명 주소는 도로명주소 API, 읍·면·리 입력은 임의 좌표를 만들지 않는 행정구역 fallback
- 근거 계층: 원본 응답 → 페이지 → 발언/문단 → 장소 언급/표준 장소 → 쟁점/근거 문장
- 전량 재구축 후 운영 DB에는 회의록 문서 283건, 논리 문단 212,069건, 문장 387,560건, 표준 장소 1,110건이 적재되어 있다. PDF/HWP/HTML 출력 원문 205건과 보존 API 상세 원문 중 고정밀 통과 78건을 합친 수치다. episode 747건과 민원 case 726건은 동일한 문장·문단 기반 검토 로직으로 다시 생성한다.

기존의 “아직 없음” 표현은 최초 조사 시점의 상태이고, 현재 실행 결과는 [docs/기획서_이해와_구현.md](docs/기획서_이해와_구현.md)와 [docs/DB_구조.md](docs/DB_구조.md)를 기준으로 본다.

## 1. 현재 자료 기준 현실성 판단

현재 확보된 파일에서 확인된 사실은 다음과 같다.

- 태양광 허가 API 원자료: 125,229건
- 한전 분산전원 API 원자료: 8,408건
- 행정구역-공급변전소 매핑: 4,557건
- 영암군 삼호읍 허가 자료: 466건, 총 108,658kW
- 삼호읍 대상 좌표 유효 건수: 0건
- 한전 API의 `js*Pwr` 필드: 전체 0
- 주소와 실제 한전 선로를 직접 연결하는 선로 geometry: 없음
- 최초 조사 당시에는 실제 회의록 원문·수집기·주소 지오코더 결과·웹 앱이 없었음. 현재는 국회도서관 회의록 수집기, 도로명주소 연동, SQLite/FTS5 API, 데모 화면을 구현했으며 행정경계 GeoJSON과 학습 모델은 아직 별도 구축하지 않음

따라서 현재 바로 만들 수 있는 것은 “공개 회의록을 주소 주변의 과거 사례와 쟁점으로 검색하고, 원문 근거와 함께 보여주는 서비스”다.

현재 데이터만으로 만들 수 없는 것은 다음이다.

- 특정 주택이 실제로 어느 저압 변압기와 연결되는지에 대한 확정 판정
- 한전 최종 접속 승인·거절 예측
- 과거 API snapshot만으로 학습한 신뢰도 높은 6개월 미래 예측
- 회의록이 말하는 장소를 모든 경우에 정확한 필지로 자동 매칭하는 기능

회의록 DB는 위의 불가능한 부분을 억지로 해결하려는 모델이 아니라, “해당 주소 주변에서 과거에 어떤 갈등·민원·협의가 있었는가”를 증거와 함께 제공하는 별도 지식 계층으로 설계한다.

## 2. 아키텍처 결정

### 2.1 MVP 구조

```text
Web Frontend
    ↓
FastAPI API
    ├─ 주소 정규화·지오코딩
    ├─ PostGIS 장소 필터
    ├─ pg_trgm 키워드 검색
    ├─ pgvector 의미 검색
    └─ 근거 묶음 생성
          ↓
      CLOVA 요약 API

RAG/ETL Worker
    ├─ 공개 포털 수집
    ├─ 원본 저장
    ├─ PDF/HWP/HTML 변환
    ├─ 페이지·발언 분할
    ├─ 장소·쟁점 추출
    ├─ VWorld 지오코딩
    └─ 임베딩 생성

Ncloud Object Storage       Cloud DB for PostgreSQL
원본·이미지·추출파일            문서·장소·쟁점·벡터
```

### 2.2 운영형 구조

운영 단계에서는 첨부 이미지와 같이 Ncloud VPC를 사용한다.

- Public Subnet: Load Balancer, 외부 진입점
- Private App Subnet: FastAPI, ETL Worker, Frontend
- Private Data Subnet: PostgreSQL, PostGIS, pgvector
- NAT Gateway: 공공 API·VWorld·CLOVA 호출
- Object Storage: 원본 문서, OCR 이미지, 처리 결과 백업
- Cloud Log Analytics/Insight: 요청·처리 실패·성능 모니터링
- Sub Account/Key 관리: API 키와 DB 권한 분리

Cloud DB for PostgreSQL은 PostgreSQL 14 이상 Rocky Linux 환경에서 PostGIS를 지원하며, pgvector와 pg_trgm도 확장으로 사용할 수 있다. 단, 실제 서버 생성 전 PostgreSQL 버전·OS·확장 설치 권한을 먼저 확인해야 한다.

## 3. 무엇을 “회의록”으로 저장할 것인가

회의록 하나를 하나의 검색 문서로만 저장하면 다음 문제를 해결할 수 없다.

- 어느 페이지에 근거가 있었는지 알 수 없다.
- 한 회의 안의 찬성·반대 발언이 섞인다.
- 같은 마을명이 여러 지역에 존재할 수 있다.
- AI가 추정한 주소가 실제 주소인지 검증할 수 없다.
- 쟁점별로 과거 사례를 비교할 수 없다.

따라서 저장 단위는 다음 네 층으로 나눈다.

1. Evidence layer: 원본·페이지·문단·발언
2. Entity layer: 사람·기관·마을·주소·발전소·회사
3. Domain layer: 민원·반대·협의·허가·분쟁 사건
4. Retrieval layer: 키워드·공간·벡터 검색용 인덱스

요약문은 Evidence layer가 아니라 조회 시 생성되는 화면 결과다.

## 4. 권장 데이터 모델

### 4.1 핵심 테이블

| 테이블 | 역할 | 반드시 보존할 값 |
|---|---|---|
| `council` | 지방의회·기관 | 기관명, 시도, 시군구, 출처 URL |
| `meeting` | 회의 메타데이터 | 회의번호, 회의일, 회의종류, 회의명 |
| `source_document` | 원본 파일 관리 | 원본 URL, 저장 URI, SHA-256, 수집일, 문서 유형 |
| `document_page` | 페이지 근거 관리 | 페이지 번호, 텍스트, OCR 여부, OCR 신뢰도, 이미지 URI |
| `meeting_segment` | 발언·안건·문단 단위 | 텍스트, 발언자, 페이지, 순서, 검색용 텍스트 |
| `speaker` | 발언자 | 이름, 소속, 직책 |
| `canonical_place` | 표준 장소 | 주소, 행정코드, 좌표, geometry, 지오코딩 정밀도 |
| `entity_mention` | 원문 속 표현 | 표면형, 유형, 시작·끝 위치, 추출 신뢰도 |
| `segment_place_link` | 문단-장소 연결 | 관계 유형, 거리, 신뢰도, 승인 상태 |
| `issue_taxonomy` | 쟁점 사전 | 버전, 코드, 명칭 |
| `segment_issue` | 문단별 쟁점 | 쟁점, 찬반 방향, 신뢰도, 근거 위치 |
| `evidence_claim` | 구조화된 주장 | 주체, 사건, 대상, 날짜, 근거 문단 |
| `conflict_case` | 같은 사례 묶음 | 사례명, 대표 장소, 상태, 시작·종료 시점 |
| `case_segment` | 사례-근거 연결 | 역할, 연결 신뢰도 |
| `embedding` | 벡터 검색 | 대상 문단, 모델명, 버전, 벡터 |
| `extraction_run` | AI 처리 이력 | 모델, 프롬프트 버전, 처리 시점, 상태 |
| `review_task` | 사람 검수 대기열 | 대상, 사유, 우선순위, 검수자, 결과 |

### 4.2 문서와 페이지

`source_document`는 원본의 불변 식별자다.

```text
source_system_id
source_record_key
title
document_type              -- meeting_minutes / petition / speech / decision
source_url
storage_uri
sha256
published_at
retrieved_at
access_policy
processing_status
```

중복 방지를 위해 `(source_system_id, source_record_key)`와 `sha256`를 모두 관리한다. URL이 바뀌어도 파일 해시가 같으면 동일 원본으로 판단한다.

`document_page`에는 원문과 AI용 텍스트를 분리한다.

```text
document_id
page_no
raw_text_uri
text_clean
text_redacted
ocr_used
ocr_confidence
image_uri
```

주민 청원 등에 개인정보가 포함될 수 있으므로 임베딩과 외부 LLM 호출에는 `text_redacted`만 사용한다. 원문은 권한이 있는 운영자만 볼 수 있게 한다.

### 4.3 발언·문단

`meeting_segment`는 검색과 근거 제시의 기본 단위다.

```text
segment_id
document_id
page_from
page_to
section_title
speaker_id
ordinal
text_original_uri
text_redacted
char_start
char_end
segment_type              -- agenda / speech / petition / decision / appendix
parse_confidence
review_status
```

문서 전체를 500토큰 단위로 무작정 자르지 않는다. 우선 안건 제목·발언자·문단 경계를 사용하고, 구조가 없는 문서에만 문장 경계를 보존한 토큰 분할을 적용한다.

### 4.4 장소와 장소 연결

`canonical_place`는 AI가 추정한 텍스트와 분리된 표준 장소다.

```text
place_id
place_type                 -- parcel / road_address / village / eup_myeon / city_county
raw_name
normalized_address
province
city_county
eup_myeon
ri
admin_code
geom geography(Point,4326)
geocode_provider
geocode_confidence
geo_precision
match_status               -- candidate / reviewed / confirmed / rejected
```

`segment_place_link`에는 다음을 저장한다.

```text
segment_id
place_id
relation_type              -- subject_site / nearby / same_village / comparative
distance_m
distance_status             -- exact / approximate / unknown
confidence
evidence_text
resolution_method           -- rule / geocoder / LLM / human
review_status
```

마을 중심점만 있는 경우에는 정확한 거리로 표시하지 않는다. `geo_precision=village`이면 “삼호읍 관련 사례” 또는 “마을 단위 참고 사례”로만 표시한다.

### 4.5 쟁점 분류

기획서의 6개 분류를 `issue_taxonomy_v1`로 만든다.

```text
landscape_damage
noise_living_discomfort
agricultural_land_damage
communication_procedure
glare_reflection
external_benefit_distribution
```

산사태·환경·안전 문제를 기타로 잃지 않도록 `issue_taxonomy_v2`에서 `safety_environment`를 추가한다. 분류 체계는 코드와 버전을 함께 저장하므로 나중에 라벨을 바꿔도 기존 결과를 추적할 수 있다.

`segment_issue`에는 문서 단위가 아니라 문단 단위로 다음을 저장한다.

```text
segment_id
taxonomy_version
issue_code
polarity                    -- opposition / support / neutral / mixed
target_type                 -- project / policy / process / company / unknown
confidence
evidence_span
extraction_run_id
review_status
```

한 회의 안에도 찬성 발언과 반대 발언이 같이 있을 수 있으므로 문서 전체를 반대 문서로 분류하지 않는다.

### 4.6 허가 데이터와 연결

기존 태양광 허가 데이터는 별도의 `permit_project`로 둔다.

```text
permit_project
  project_id
  source_record_id
  facility_name
  company_name
  capacity_kw
  permit_date
  operation_status
  address
  geom
  match_status

project_place_link
  project_id
  place_id
  relation_type
  confidence
  evidence
```

회의록이 발전소명을 언급했다고 해서 곧바로 허가 데이터와 확정 결합하지 않는다. 이름·회사·용량·주소 중 최소 두 개 이상이 일치하거나 사람이 승인한 경우에만 `confirmed`로 승격한다.

## 5. 최소 PostgreSQL 확장과 인덱스

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE INDEX idx_place_geom
  ON canonical_place USING GIST (geom);

CREATE INDEX idx_segment_text_trgm
  ON meeting_segment USING GIN (text_redacted gin_trgm_ops);

CREATE INDEX idx_segment_document_page
  ON meeting_segment (document_id, page_from, page_to);

CREATE INDEX idx_segment_issue
  ON segment_issue (issue_code, polarity, review_status);

CREATE INDEX idx_place_admin
  ON canonical_place (province, city_county, eup_myeon);
```

벡터 인덱스는 실제 선택한 임베딩 모델의 차원을 확인한 뒤 생성한다. 여러 모델을 한 컬럼에 섞지 말고 `embedding_model`과 `embedding_version`을 분리한다.

## 6. 실제 수집·처리 파이프라인

### 단계 1. 수집 대상 고정

처음부터 전남 22개 시군을 모두 처리하지 않는다. 우선 다음 범위로 시작한다.

- 무안군의회
- 나주시의회
- 신안군의회
- 해남군의회
- 전라남도의회

검색어는 `태양광`, `풍력`, `재생에너지`, `발전시설`, `주민`, `민원`, `반대`, `청원`, `협의`, `농지`, `경관`, `빛반사`로 시작한다.

문서 유형은 회의록, 5분 발언, 주민 청원, 의회 결의·성명서로 한정한다. 포털에서 공식 API가 없으면 먼저 공개 다운로드 파일을 seed로 저장하고, 이후 수집기를 만든다. API가 있다고 가정하고 전체 자동화를 선행하지 않는다.

### 단계 2. 원본 적재

```text
다운로드
  → 파일 해시 계산
  → source_document upsert
  → Ncloud Storage 원본 저장
  → ingestion_job 생성
```

원본 Object Storage 경로는 다음처럼 고정한다.

```text
raw/{source_system}/{yyyy}/{document_id}/original.ext
raw/{source_system}/{yyyy}/{document_id}/page-0001.png
processed/{document_id}/{run_id}/page-0001.json
```

### 단계 3. 문서 변환

- 텍스트가 있는 PDF: 페이지별 텍스트 추출
- 스캔 PDF: 페이지 이미지 생성 후 OCR
- HTML: 본문과 회의 메타데이터 추출
- HWP: MVP에서는 공식 다운로드 파일을 PDF 또는 HTML로 변환한 뒤 처리

HWP 변환을 클라우드 런타임의 필수 전제로 삼지 않는다. HWP 변환이 실패하면 원본을 보존하고 해당 문서를 `manual_conversion_required`로 검수 대기시킨다.

### 단계 4. 구조 분해

다음 정규식·규칙을 먼저 적용한다.

- `제\d+회` → 회의번호
- `20\d{2}[.년/-]\d{1,2}` → 날짜 후보
- `의장`, `위원장`, `의원`, `시장`, `군수`, `발언자` → 발언자 후보
- `의사일정`, `안건`, `보고`, `질의`, `답변`, `청원` → 문서 섹션 후보

규칙으로 확정하지 못한 값은 AI 추출로 넘기되, 원문 근거 위치를 같이 요구한다.

### 단계 5. 문단·발언 추출

각 문단은 `meeting_segment`에 저장한다. AI에는 다음 JSON만 요청한다.

```json
{
  "speaker": {"name": "", "role": ""},
  "places": [],
  "projects": [],
  "issues": [],
  "events": [],
  "evidence_required": true
}
```

AI가 JSON 형식을 지키지 않으면 재시도하고, 두 번 실패하면 `extraction_failed`로 검수 대기시킨다. 자연어 응답을 그대로 DB에 넣지 않는다.

### 단계 6. 장소 표준화

장소 연결 우선순위는 다음과 같다.

1. 완전한 도로명·지번주소
2. 읍면동·리·마을명 + 시군구
3. 발전소명 + 회사명 + 시군구
4. 문서의 회의기관·행정구역만 확인

각 후보를 모두 저장하고 다음 조건을 만족할 때만 확정한다.

- 지오코더가 반환한 주소가 원문 주소와 시군구 이상 일치
- 좌표가 대한민국 범위 안에 있음
- 동일 이름의 후보가 여러 개가 아님
- 사람이 검수했거나, 고정된 높은 신뢰도 기준을 통과함

### 단계 7. 쟁점·찬반 분류

초기에는 미세조정 모델을 만들지 않는다. 다음 순서로 진행한다.

1. 키워드 사전으로 후보 쟁점 생성
2. CLOVA JSON 추출로 쟁점·찬반·대상 판정
3. 200개 문단을 사람이 검수
4. 분류 F1을 측정
5. 오분류가 반복될 때만 프롬프트 또는 분류 모델 개선

### 단계 8. 임베딩

임베딩에는 `text_redacted`와 검색에 필요한 메타데이터를 함께 사용한다.

임베딩 단위는 문서 전체가 아니라 `meeting_segment`다. 문단이 너무 길면 문장 경계를 유지해 하위 chunk를 만들고, 원래 `segment_id`와 부모 관계를 저장한다.

### 단계 9. 검수

다음 문서는 자동 공개하지 않고 검수 큐로 보낸다.

- OCR 신뢰도 낮음
- 장소 후보가 2개 이상
- 좌표가 없음
- 쟁점 신뢰도 낮음
- 주민 개인정보 탐지
- 같은 사례 연결 신뢰도 낮음
- 원문 페이지를 찾지 못함

## 6A. AI 전수 독해와 로직 전수 검증의 결합

### 6A.1 처리 원칙

“모든 파일을 GPT에 한 번에 넣는다”가 아니라, 모든 파일을 코드로 빠짐없이 읽은 뒤 AI가 의미를 해석하게 한다.

```text
전체 파일 목록화
  → 코드로 해시·형식·텍스트·표 추출
  → 문서별 페이지·문단 생성
  → AI 1차 관련성 판정
  → AI 2차 구조화 추출
  → 로직 전수 검증
  → 승인 또는 재처리·사람 검수
  → DB 반영·임베딩
```

AI는 의미·맥락·유사 표현을 판단하고, 로직은 누락·오류·중복·근거 불일치를 검사한다. 어느 한쪽만 사용하지 않는다.

### 6A.2 파일 유형별 처리 방식

| 파일 유형 | 전수 처리 주체 | AI에 넘기는 내용 | 이유 |
|---|---|---|---|
| PDF·HTML 회의록 | 코드로 텍스트화 후 AI | 페이지·발언·문단 | 의미와 장소·쟁점 추출이 필요함 |
| 스캔 PDF | OCR 후 AI | OCR 텍스트와 OCR 신뢰도 | 이미지 자체보다 텍스트 단위 검수가 쉬움 |
| HWP | PDF·HTML 변환 후 AI | 변환된 문단 | 클라우드에서 HWP 변환 실패를 핵심 전제로 삼지 않음 |
| CSV·JSON 허가 데이터 | 코드 | 집계·이상치·예외 행만 | 125,229건 전체를 LLM에 보내는 것은 비용·오류 면에서 비효율적 |
| 원문 첨부파일 | 코드로 해시·보관 | 필요한 페이지의 안전한 텍스트 | 원본은 증거로 보존하고 외부 AI에는 마스킹 텍스트만 전달 |

### 6A.3 AI 1차 독해: 관련 문단 선별

문서 전체를 먼저 요약하지 않고, 문단별로 다음을 판정한다.

```json
{
  "relevant": true,
  "relevance_type": ["solar", "resident_opposition"],
  "confidence": 0.94,
  "reason": "태양광 시설과 주민 반대 성명서가 같은 안건에서 언급됨",
  "evidence_segment_id": "seg_000123"
}
```

처리 규칙은 다음과 같다.

- `relevant=true` 문단만 2차 추출 대상으로 보낸다.
- `confidence >= 0.85`: 자동 처리 후보
- `0.60 <= confidence < 0.85`: 검수 후보
- `confidence < 0.60`: 보류 또는 키워드 규칙 재검토
- 관련성이 없어도 원문과 판정 결과는 처리 이력에 남긴다.

### 6A.4 AI 2차 독해: 구조화 정보 추출

관련 문단에서 장소·사업·쟁점·사건을 JSON으로 추출한다. 자연어 요약 결과는 DB 입력값으로 사용하지 않는다.

```json
{
  "places": [
    {
      "surface": "나주시 세지면 대산리 계양마을",
      "type": "village",
      "confidence": 0.94
    }
  ],
  "projects": [
    {
      "surface": "태양광 발전시설",
      "capacity_kw": null,
      "confidence": 0.81
    }
  ],
  "issues": [
    {
      "code": "agricultural_land_damage",
      "polarity": "opposition",
      "confidence": 0.91
    }
  ],
  "events": [
    {
      "type": "resident_opposition",
      "event_date": null,
      "confidence": 0.88
    }
  ],
  "evidence_segment_id": "seg_000123"
}
```

AI 요청에는 항상 다음을 포함한다.

- 문서 ID
- 페이지 번호
- 문단 ID
- 앞뒤 문맥 일부
- 허용된 쟁점 코드 목록
- JSON 스키마
- “원문에 없는 정보는 null” 규칙

### 6A.5 로직 전수 검증

AI 추출 결과는 `llm_extraction_staging`에 먼저 저장한 뒤 다음 검증을 통과해야 한다.

```text
1. JSON Schema 통과 여부
2. evidence_segment_id가 실제 존재하는지
3. 페이지 번호가 실제 문서 범위인지
4. 장소·사업·회사명이 실제 원문에 포함되는지
5. 쟁점 코드가 issue_taxonomy에 존재하는지
6. 용량·날짜·좌표 형식이 올바른지
7. 좌표가 대한민국 범위 안인지
8. 중복 문서·중복 문단인지
9. 장소 후보가 여러 개인지
10. 개인정보가 마스킹되었는지
```

검증 결과는 다음처럼 저장한다.

```text
validation_status
  approved
  needs_review
  retry
  rejected

validation_errors: JSON 배열
validator_version
validated_at
```

검증에 실패한 결과를 삭제하지 않는다. 실패 이유를 남겨야 같은 문제가 반복되지 않는다.

### 6A.6 자동 승인과 사람 검수 기준

| 조건 | 처리 |
|---|---|
| 정확한 도로명·지번주소, 단일 지오코딩 후보 | 자동 승인 후보 |
| 읍면동·리·마을명만 존재 | 지역 단위 연결로 저장, 필지 확정 금지 |
| 동일 이름 장소가 여러 개 | 사람 검수 |
| OCR 신뢰도 낮음 | 원본 이미지 검수 |
| 쟁점 신뢰도 낮음 | 재질문 또는 사람 라벨링 |
| 원문에 없는 용량·날짜 생성 | 즉시 거부 |
| 페이지·문단 근거 누락 | 공개 금지 |

### 6A.7 대규모 구조화 데이터 처리

태양광 허가 CSV와 한전 API JSON은 다음 순서로 처리한다.

```text
코드로 전체 행 읽기
  → 타입 변환
  → 중복·결측·이상치 검사
  → 지역·날짜·용량 집계
  → 예외 행 추출
  → AI가 예외의 의미 해석
  → 사람이 필요한 행만 검수
```

예를 들어 `capa=3630562275`, `capa=89..6`, 좌표 범위 밖 값처럼 규칙으로 잡히는 행은 코드가 먼저 걸러낸다. AI는 “이 값이 오탈자인지, 원자료의 특수 표기인지”를 해석하는 보조 역할만 맡긴다.

### 6A.8 DB 반영 순서

```text
raw document
  → source_document / document_page
  → meeting_segment
  → llm_extraction_staging
  → validation_result
  → canonical_place / segment_issue / evidence_claim
  → embedding
```

`canonical_place`, `segment_issue`, `evidence_claim`에는 반드시 `extraction_run_id`와 `evidence_segment_id`를 저장한다. 나중에 모델이나 프롬프트를 바꿔도 어떤 결과가 어디서 나왔는지 재현할 수 있어야 한다.

### 6A.9 실제 예시

문장:

> “제270회 무안군의회에서 염해농지 태양광 설치 반대 성명서가 채택되었다.”

처리 결과:

1. 코드가 회의번호와 페이지를 인식한다.
2. AI가 `무안군`, `염해농지`, `태양광`, `반대 성명서 채택`을 추출한다.
3. 쟁점은 `agricultural_land_damage`, 방향은 `opposition`으로 분류한다.
4. “염해농지”가 정확한 필지 주소가 아니면 장소를 `city_county` 또는 `unknown` 정밀도로 저장한다.
5. VWorld 후보가 여러 개면 자동 확정하지 않는다.
6. 최종 검색 결과에는 문서명·회의일·페이지·원문 문단을 함께 표시한다.

이렇게 해야 AI가 “무안군의 특정 태양광 사업이 주민 반대를 받았다”고 과도하게 확정하는 오류를 막을 수 있다.

## 7. 검색과 답변 로직

### 7.1 주소 기반 검색

```text
사용자 주소
  → 주소 정규화
  → 좌표·행정구역 획득
  → 해당 지역 permit_project 조회
  → 1km / 5km 장소 후보 검색
  → 문단 키워드·벡터 검색
  → 사례별 중복 제거
  → 근거 묶음 생성
  → CLOVA 요약
```

검색 결과는 다음 그룹으로 나눈다.

- `exact_site`: 정확한 사업지·주소에 연결된 기록
- `same_village`: 같은 마을·리 단위 기록
- `nearby`: 좌표가 확인된 반경 기록
- `same_admin_area`: 읍면동·시군구 단위 기록
- `comparative_case`: 다른 지역의 유사 사례

이 그룹을 화면에서 섞지 않는다.

### 7.2 초기 점수

초기 점수는 설정값으로 관리하고, 최종 값으로 간주하지 않는다.

```text
검색점수 =
  0.35 × 의미 유사도
+ 0.25 × 키워드 유사도
+ 0.20 × 공간 적합도
+ 0.10 × 최신성
+ 0.10 × 출처 품질
```

공간 적합도는 정확한 좌표가 있는 경우에만 거리 기반으로 계산한다. 마을명만 있는 기록은 `same_village` 또는 `same_admin_area` 점수로 제한한다.

### 7.3 LLM 답변 제한

LLM에 전달하는 입력에는 각 근거의 다음 값이 포함되어야 한다.

```text
evidence_id
meeting_title
meeting_date
source_url
page_no
segment_text
place_precision
issue_code
```

LLM 출력에는 다음이 필수다.

- 주장별 `evidence_id`
- 회의명·일자·페이지
- 확정·추정·미확인 구분
- 주소 매칭 수준
- 한전 최종 확인이 필요한 항목

근거 ID가 없는 문장은 서비스 응답에서 제거한다.

## 8. API 설계

### `POST /v1/meeting-evidence/search`

```json
{
  "address": "전라남도 영암군 삼호읍 ...",
  "radius_m": 5000,
  "issue_codes": ["agricultural_land_damage", "communication_procedure"],
  "from_date": "2018-01-01",
  "limit": 20
}
```

응답에는 `document_id`, `meeting_id`, `page_no`, `segment_id`, `place_precision`, `distance_status`, `evidence_text`, `source_url`을 포함한다.

### `POST /v1/report`

```json
{
  "address": "전라남도 영암군 삼호읍 ...",
  "capacity_kw": 3,
  "monthly_usage_kwh": 350,
  "battery": false
}
```

이 API는 다음 세 결과를 분리해서 반환한다.

1. 가정 발전량·잉여전력 계산
2. 계통 데이터 기반 참고판정
3. 회의록 기반 지역 갈등·민원 사례

회의록 사례를 계통 접속 가능성의 직접 정답으로 합산하지 않는다. 둘은 서로 다른 근거다.

## 9. 1박 2일 MVP 범위

### 반드시 구현할 것

- 회의록 100~300건 수동 seed 적재
- PDF·HTML 문서의 페이지·문단 분할
- 문서·페이지·문단·장소·쟁점 DB
- 주소 1건 지오코딩
- 1km·5km 반경 검색
- 키워드 + 벡터 하이브리드 검색
- 원문 페이지·출처 URL 포함 결과
- 장소 불확실성 표시
- 개인정보 마스킹

### 구현하지 않을 것

- 22개 시군 전체 자동 수집
- 모든 HWP 자동 변환
- Graph DB 도입
- 별도 OpenSearch 클러스터 구축
- 회의록 분류 모델 fine-tuning
- 실제 한전 접속 승인 예측
- 사용자 입력 주소의 필지 단위 확정
- MCP를 통한 일반 자연어 데이터 조작

### 시간 배분

| 시간 | 산출물 |
|---|---|
| 0~2시간 | PostgreSQL 스키마, 확장, seed 문서 10건 |
| 2~6시간 | PDF/HTML 적재, 페이지·문단 분할 |
| 6~10시간 | 장소·쟁점 추출, 검수용 CSV 생성 |
| 10~14시간 | 지오코딩·PostGIS 검색 |
| 14~20시간 | 임베딩·하이브리드 검색 |
| 20~28시간 | FastAPI·대시보드·근거 표시 |
| 28~32시간 | 30개 질의 QA, 발표 시나리오 고정 |

## 10. 운영형으로 확장할 때의 우선순위

### 1단계: 데이터 품질

- 전남 5개 시군의 공식 회의록 수집기
- 행정경계 GeoJSON
- 주소·마을·발전소 표준명 사전
- 장소 후보 검수 화면

### 2단계: 검색 품질

- 200~500개 문단 사람 이중 라벨링
- 쟁점·찬반 F1 측정
- 검색 Top-3 근거 적합성 평가
- 한국어 형태소 검색엔진 도입 검토

### 3단계: 서비스 운영

- 수집 스케줄러
- 실패 재처리
- 모델·프롬프트 버전 관리
- 읽기 전용 서비스 DB 또는 replica
- Object Storage 보존 정책
- 개인정보 접근 권한·감사 로그

## 11. 현실성·위험 검토

| 위험 | 수준 | 현실적인 대응 |
|---|---:|---|
| 지방의회 포털에 API가 없거나 구조가 바뀜 | 높음 | 공식 다운로드 seed + source adapter 구조로 시작 |
| 스캔 PDF OCR 오류 | 중간 | 페이지별 OCR 신뢰도, 낮은 문서 검수 대기 |
| 마을명이 여러 곳에 존재 | 높음 | 후보 다중 저장, 자동 확정 금지 |
| 회의록과 발전소 허가자료 연결 오류 | 높음 | 이름 하나가 아니라 주소·회사·용량 복합 매칭 |
| 한국어 키워드 검색 약함 | 중간 | pg_trgm + pgvector, 이후 한국어 검색엔진 검토 |
| 개인정보가 임베딩으로 유출 | 높음 | 마스킹 텍스트만 AI 전달, 원문 권한 분리 |
| 학습 정답 데이터 부족 | 높음 | 처음에는 추출·검색 중심, 200건 검수 후 모델화 |
| Ncloud 인프라 설정 지연 | 중간 | 로컬 Docker 또는 준비된 단일 DB로 MVP 먼저 검증 |
| 한전 최종 승인을 예측한다고 오해 | 높음 | “지역 사례 참고”와 “한전 최종 확인”을 화면에서 분리 |

## 12. 완료 기준

다음 조건을 통과해야 회의록 DB MVP가 완성된 것으로 본다.

- 동일 문서를 두 번 넣어도 문서가 중복 생성되지 않는다.
- 모든 검색 결과가 회의명·일자·페이지·원문 URL을 가진다.
- 모든 장소 연결 결과에 정밀도와 신뢰도가 있다.
- 정확한 주소와 모호한 마을명이 화면에서 구분된다.
- 사람이 검수한 200개 문단에서 쟁점 분류 F1 0.80 이상을 목표로 한다.
- 30개 테스트 질의에서 근거 없는 문장을 생성하지 않는다.
- 좌표 없는 삼호읍 사례는 정확한 거리 또는 정확한 선로로 표시하지 않는다.
- 주민 개인정보가 외부 LLM 요청 payload에 포함되지 않는다.
- 원본 파일을 삭제하지 않고 처리 버전을 새로 만든다.

## 13. 최종 권고

첫 구현은 “회의록 전체를 AI가 읽고 요약하는 시스템”이 아니라 다음 데모 하나에 집중해야 한다.

> 주소를 입력하면 반경과 행정구역 기준으로 과거 회의록·청원·발언을 찾고, 쟁점별로 묶어 원문 페이지와 함께 보여준다.

이 기능이 먼저 작동하면 나중에 태양광 허가 데이터, 발전소 위치, 계통 위험도, 예상 잉여전력을 각각 연결할 수 있다. 반대로 주소 매칭과 근거 보존이 되지 않은 상태에서 모델부터 학습하면, 설명할 수 없는 위험도 숫자만 만들어지게 된다.

## 참고한 공식 문서

- [NAVER Cloud Cloud DB for PostgreSQL 확장 관리](https://guide.ncloud-docs.com/docs/en/clouddbforpostgresql-postgresqlextension)
- [NAVER Cloud Cloud DB for PostgreSQL 개요](https://guide.ncloud-docs.com/docs/en/clouddbforpostgresql-overview)
- [NAVER Cloud Ncloud Storage 개요](https://guide.ncloud-docs.com/docs/en/ncloudstorage-overview)
- [NAVER Cloud CLOVA Studio API 사용](https://guide.ncloud-docs.com/docs/en/clovastudio-explorer03)
