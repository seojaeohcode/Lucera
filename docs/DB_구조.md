# Lucera DB 구조

## 설계 목표

이 DB는 “검색 결과를 빨리 내는 테이블”보다 “왜 이 결과가 나왔는지 다시 확인할 수 있는 구조”를 우선합니다.

```text
원본 출처
  source_system → source_document → document_page
                                      ↓
                                 meeting_segment
                                  ↙      ↘
                       place_mention      segment_issue
                              ↓
                 place_resolution_candidate
                              ↓
                      canonical_place
                              ↓
                    segment_place_link
                              ↓
                    case_location_candidate
```

```text
문서 → 문단/발언블록 → 문장 → 키워드 발생지점 → episode → case
```

계층의 각 단계는 의미가 다릅니다.

- 문단/발언블록: `meeting_segment`의 하나의 발언 또는 구조화된 문단
- 문장: `sentences`의 문장 및 원문 오프셋
- 키워드 발생: `keyword_mentions`의 사전 단어 위치
- 회의 내 사건: `episodes`의 연속 논의 단위
- 동일 민원: `conflict_case`의 여러 회의 연결 단위

장소·쟁점의 원래 연결은 문단에서 계속 보존합니다.

```text
meeting_segment ── place_mention → canonical_place
       └───────── segment_issue
```

키워드가 많이 나온 문단을 사건으로 간주하지 않습니다. 일반 키워드 발생은 검색·감사 근거로만 남고, 보수적인 쟁점 분류를 통과한 문장만 episode trigger가 됩니다.

## 핵심 엔터티

### `source_document` / `document_page`

원본 식별과 근거 위치를 담당합니다.

- `source_record_key`: 공급자가 준 문서 ID. CLiK는 `DOCID`를 사용
- `sha256`: 파일 원본 중복 방지용 해시
- `source_url` / `original_file_url`: API 출처와 원본 파일 출처를 분리
- `raw_payload_json`: 공급자 응답 원문 보존
- `text_original` / `text_redacted`: 원문과 외부 처리용 마스킹 텍스트 분리
- `page_no`: PDF/페이지가 없는 HTML도 MVP에서는 논리 페이지 1로 보존

### `meeting` / `meeting_segment` / `speaker`

회의 전체를 하나의 벡터나 문자열로 저장하지 않습니다. 발언·안건·문단별로 쪼개서 저장하여 반대 발언과 행정기관 답변이 섞이지 않도록 합니다.

- 한 문서에 하나의 `meeting`
- 한 문서에 여러 `meeting_segment`
- `meeting.administrative_region_code`로 27개 자치시·자치군·자치구 카탈로그와 직접 연결
- 발언자 이름·직책은 `speaker`에 별도 저장
- `segment_type`, `ordinal`, `page_from/page_to`로 원문 위치 재현

### `sentences` / `keyword_mentions`

문단을 다시 원문 문장과 키워드 발생 위치로 내려가는 근거 계층입니다.

- `sentences.paragraph_id`로 원문 문단을 추적
- `sentence_order`와 `char_start/char_end`로 문단 안의 순서·원문 위치 재현
- `text`와 `text_redacted`를 분리하여 개인정보 마스킹 원칙 유지
- `keyword_mentions`에는 팀이 제공한 6개 그룹 전체의 발생을 저장
- `start_offset/end_offset`은 문장 내부가 아니라 문단 원문 기준 오프셋
- `match_type`, `keyword_group`, `problem_category`로 앵커·문제어·행정 보조어를 구분

따라서 `주민`, `환경`, `안전`, `협의`가 저장되어 있다는 사실만으로 민원 사건이 생기지 않습니다. `segment_issue`의 보수적 규칙을 통과하거나 `빛반사`·`반사광`·`눈부심` 같은 고정밀 예외가 있어야 episode trigger가 됩니다.

### `canonical_place` / `place_mention` / `segment_place_link`

장소는 세 단계로 나눕니다.

1. `place_mention`: 문서에서 발견한 표현. 예: `삼호읍`, `○○리`, `영암군`
2. `canonical_place`: 주소/행정코드/좌표를 붙인 표준 장소 후보
3. `segment_place_link`: 이 문단에서 이 장소가 어떤 관계인지 기록

`canonical_place.geo_precision`은 `parcel`, `building`, `road_address`, `jibun_address`, `village`, `ri`, `eup_myeon`, `city_county`, `province`, `unknown` 중 하나입니다.

좌표가 없으면 `latitude`, `longitude`를 비워둡니다. `distance_m`을 추정해 채우지 않고 `distance_status=unknown`으로 저장합니다. 같은 읍·면·리라는 사실과 실제 사업지 좌표를 동일한 것으로 취급하지 않습니다.

`place_resolution_candidate`는 동명이인 장소 후보를 모두 남길 수 있게 만든 검수용 테이블입니다. 자동 추출은 `candidate`/`pending`으로 시작하고, 사람이 확인한 경우에만 `reviewed` 또는 `confirmed`로 승격합니다.

### `case_location_candidate`

사건 위치는 `conflict_case.representative_place_id` 하나만으로 저장하지 않습니다. 같은 민원이 문서마다 읍면·리·도로명·지번을 다르게 드러낼 수 있으므로, 사건에 연결 가능한 모든 표준 장소를 `case_location_candidate`에 보존합니다.

| 컬럼 | 의미 |
|---|---|
| `case_id`, `place_id` | 사건과 표준 장소의 후보 연결 |
| `rank`, `confidence` | 정밀도·좌표 존재·문단 내 관계를 반영한 순위와 점수 |
| `inference_method` | 현재 `episode_place_cooccurrence` |
| `evidence_episode_id`, `evidence_paragraph_id`, `evidence_text` | 후보를 만든 원문 근거 |
| `is_selected` | 검색 편의를 위한 대표 후보 여부 |
| `review_status` | 자동 후보는 `pending`, 검수 후 상태 변경 |

필지·건물·도로명·지번 좌표가 있으면 읍면·리보다 우선한다. 좌표가 없는 행정구역은 후보로만 저장하며 중심점이나 임의 좌표를 생성하지 않는다. 대표 후보가 `conflict_case.representative_place_id`로 반영되더라도, 이는 “추론 위치”이고 설치지 확정 필드가 아니다.

### 지역 카탈로그와 수집 이력

현재 지역 카탈로그는 27개 광주·전남 지역 단위(자치시 5·자치군 17·자치구 5)와 2개 상위권역 메타데이터로 구성한다. DB의 `administrative_region`은 지역 그룹·상위권역·API에서 확인된 의회 ID·`available` 여부를 함께 보존한다. 지역별 상세 회의록 수집기는 문서의 지역 ID와 `DOCID`를 검증한 뒤 저장하고, 이미 저장된 DOCID를 건너뛴다. 따라서 `source_document` 건수, `episodes` 건수, `conflict_case` 건수, “실제 민원 사건 수”를 서로 같은 숫자로 해석하면 안 된다. 수집 목표 미달은 `shortfall`, API 한도 중단은 `paused_quota`, 의회 ID 미확인은 `unavailable`로 구분한다.

### `issue_taxonomy` / `segment_issue`

쟁점 사전은 버전을 가집니다. 현재 `v1`에는 기획서의 핵심 범주와 안전·계통 보완 범주를 포함했습니다. 별도로 키워드 분류기의 네 가지 상위 문제 카테고리를 파생 메타데이터에 기록합니다.

```text
resident_conflict              주민·민원·갈등
siting_permit                  입지·토지·인허가
impact_environment_safety      피해·환경·안전
benefit_compensation           수익·보상·지역경제
```

```text
landscape_damage
noise_living_discomfort
agricultural_land_damage
communication_procedure
glare_reflection
external_benefit_distribution
safety_environment
grid_connection
```

`segment_issue`는 문단마다 `polarity`, `target_type`, `confidence`, `evidence_span`, `review_status`를 함께 저장합니다. 한 문서 전체를 “반대”로 단정하지 않습니다.

키워드 메타데이터에는 `matched_keywords`, `solar_anchor_hits`, `rule_id`, `problem_category`, `admin_support_hits`를 저장합니다. `빛반사`, `반사광`, `눈부심`은 태양광 단어가 같은 문단에 없어도 고정밀 후보로 허용합니다. 반면 `주민`, `환경`, `안전`, `협의`, `조례` 같은 일반어는 태양광 문맥과 함께 문제어가 확인될 때만 쟁점으로 생성합니다. 행정·법제어는 민원 자체가 아니라 처리·근거를 설명하는 보조 신호입니다.

### `episodes` / `episode_evidence`

`episodes`는 한 회의 안에서 같은 안건·가까운 문단·공통 장소·공통 쟁점으로 이어지는 사건 묶음입니다.

- `paragraph_start/end`: trigger 문단의 실제 범위. 앞뒤 문맥은 범위에 포함하지 않고 evidence로 별도 표현
- `issue_types_json`: 하나의 episode에 함께 등장한 세부 쟁점 목록
- `stance`, `procedure_stage`: 원문에서 추출한 참고용 방향·절차 단계
- `grouping_score`, `grouping_method`: 어떤 규칙으로 묶였는지 재현
- `episode_evidence.evidence_role`: `trigger_sentence`, `context_before`, `context_after`, `episode_context`

자동 병합 점수는 같은 문서 `0.25`, 같은 안건 `0.20`, 쟁점 겹침 `0.15`, 강한 장소·사업·시설 식별자 겹침 `0.30`, 문단 근접성 `0.05~0.10`입니다. `또 다른 민원`, `다음 안건`, `다른 사업`, `별도로` 등 명시적 전환어와 5문단 초과 간격은 점수를 0으로 만들어 강제 분리합니다. 인접한 문단에서 이슈가 `허가 → 주민 반대 → 빛반사`로 바뀌는 것은 정상적인 하나의 episode일 수 있으므로 쟁점이 다르다는 이유만으로 분리하지 않습니다.

### `conflict_case` / `case_evidence` / `case_link_candidate`

`conflict_case`는 여러 회의·문서에 반복되는 동일 민원 후보입니다. 다음 강한 단서가 있을 때만 동일 `case_key`로 자동 연결합니다.

- 사업명·발전소명·프로젝트명
- 도로명/지번 주소 또는 해석된 주소
- 리·읍면 단위 장소 표현
- 수상·영농형·학교·지붕형 등 비일반 시설 유형

시·군만 같은 경우에는 자동 병합하지 않습니다. 강한 식별자가 없으면 `episode:<episode_id>` case로 격리하고 `pending`으로 둡니다. `case_evidence`는 case가 어느 episode·문단·문장을 근거로 하는지 보존합니다. `case_link_candidate`는 같은 시·군·시설·쟁점 등의 일부 단서가 겹치지만 자동 병합 기준에는 못 미친 연결을 검수용으로 저장합니다.

주장 단위 확장은 `evidence_claim`에서 담당하며, `confirmed`, `inferred`, `unconfirmed`를 구분해 AI 요약이 원문보다 강해지지 않도록 합니다.

## 사용자 사업 입력 영역

현재 사용자 입력 스키마는 기획서의 입력·첨부·위치·검토 요구사항을 반영한 `project-intake-v2` 계약으로 확장되어 있다. 전체 필드·타입·출처 컬럼은 [DB 컬럼 명세서](DB_컬럼명세서.md)에 정리했다. 특히 `project_field_definition`을 기준 테이블로 두어 웹 폼의 필드명과 `project_fact.field_name`이 달라지는 문제를 방지하고, 제출 raw payload의 해시와 구조화 신청의 `source_submission_id`를 연결한다.

공개 회의록에서 만들어진 과거 민원 영역과 사용자가 입력하는 사업 영역은 별도 namespace로 관리합니다.

| 목적 | 테이블 | 핵심 관계 |
|---|---|---|
| 사업 루트·개정 | `project_intake`, `project_intake_submission` | `project_id → revision_no → raw_payload` |
| 신청 정보 | `project_application` | 기본정보·신청자, revision별 1건 |
| 상세 입력 | `project_site`, `project_equipment`, `project_finance`, `project_schedule`, `project_grid` | 모두 `application_id` 1:1 |
| 검토·민원 입력 | `project_permit_checklist`, `project_resident_risk` | 허가 체크·주민 위험 정보 |
| 첨부·출처 | `project_attachment`, `project_fact` | 첨부 → 추출 fact → 페이지/발췌/신뢰도 |
| 절차 | `project_stage`, `project_stage_event` | 6단계 현재 snapshot + 변경/발생 event |
| 위치 | `project_location_link` | 사업·신청 revision·표준 장소·관계 |
| 과거 비교 | `project_case_link` | 사업·신청 revision·`conflict_case`·단계·매칭 근거 |

사용자 입력의 논리적 조회 경로는 다음과 같습니다.

```text
project_intake(project_id)
  └─ project_application(revision_no)
      ├─ project_site / equipment / finance / schedule / grid
      ├─ project_permit_checklist / project_resident_risk
      ├─ project_attachment
      │   └─ project_fact(source_attachment_id, source_page, source_excerpt)
      ├─ project_fact(source_kind='user_input')
      ├─ project_stage
      │   └─ project_stage_event
      ├─ project_location_link → canonical_place
      └─ project_case_link → conflict_case → case_evidence → episode → paragraph
```

이 설계로 “현재 사업의 주민 민원 단계에서 어떤 과거 민원을 참고했는가”를 조회할 수 있습니다. `project_stage.case_id`는 해당 단계의 대표 링크 1건을 빠르게 보여주기 위한 값이고, 여러 후보와 점수·매칭 단서는 `project_case_link`에 모두 남깁니다. 자동 링크는 `pending`으로 두며, 동일 시군만 겹치는 경우에는 임계값을 넘지 않도록 했습니다.

사업 입력 필드는 원본 입력 테이블에만 흩어지지 않고 `project_fact`에도 한 번씩 기록됩니다. 이 때문에 현재 화면용 구조화 값은 `project_equipment` 등에서 빠르게 읽고, 값의 출처·단위·페이지·추출 방식·검수 상태는 `project_fact`에서 감사할 수 있습니다. 첨부파일 자체는 `not_started/queued/extracted/failed/reviewed` 상태를 갖고, 문서 자동 추출기가 아직 돌지 않은 경우에도 파일과 출처 연결을 잃지 않습니다.

## 검색 지원

### SQLite MVP

- `meeting_segment_fts`: FTS5 전문 검색
- `idx_place_admin`: 도/시·군·읍·면·리 행정구역 검색
- `idx_place_coordinates`: 좌표 존재 여부 및 후보 조회
- `idx_meeting_region_date`: 기관 지역·회의일 필터
- `idx_segment_issue`: 쟁점·찬반·검수 상태 필터
- `idx_case_location_candidate`: 사건 대표 장소 후보와 신뢰도 정렬

MVP 검색은 행정구역과 키워드/쟁점 중심입니다. 좌표가 모두 있을 때만 하버사인 거리 계산을 적용합니다.

### PostgreSQL 운영 전환

`db/schema.postgres.sql`은 다음 확장을 전제로 합니다.

- PostGIS: `canonical_place.geom geography(Point,4326)` 공간 검색
- pg_trgm: 한국어 고유명사·마을명 보완 검색
- pgcrypto: UUID 생성
- pgvector: 임베딩 모델/차원이 확정된 뒤 고정 차원 컬럼과 HNSW/IVFFlat 인덱스 추가

벡터 차원을 정하지 않은 상태에서 임의 차원을 박아 넣지 않았습니다. 운영 모델을 선택한 후 `meeting_segment` 또는 별도 `embedding` 테이블에 모델명·버전·차원을 함께 고정해야 합니다.

## 무결성·재처리 규칙

- 같은 `(source_system_id, source_record_key)`는 하나의 문서
- 문서 재적재 시 원본 메타데이터는 upsert하고 파생 페이지/문단/장소 연결만 재생성
- 모든 파생 행은 `document_id` 또는 `segment_id`로 원본까지 역추적
- 쟁점과 장소는 `pending` 검수 상태로 시작
- 처리 실패는 `ingestion_job`과 `review_task`로 남김
- 주소 API 요청과 응답은 `address_lookup`에 저장하여 어떤 주소 해석 결과를 사용했는지 재현
- 검색 요청은 `search_request`에 저장하여 반경·키워드·주소 해석 상태를 추적
