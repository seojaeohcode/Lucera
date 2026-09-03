# Lucera DB v2 컬럼 명세

기획서의 핵심 흐름인 `주소·사업 입력 → 위치 해석 → 과거 분쟁 검색 → 쟁점·근거 표시 → 사람 검토`를 기준으로 한 컬럼 계약이다. 기존 회의록 데이터의 `case_id`와 사용자가 입력하는 사업의 `project_id`는 서로 다른 식별자 공간으로 유지한다.

## 1. 사업 입력의 정본 관계

```text
project_intake(project_id)
  └─ project_intake_submission(revision_no, raw_payload_json, payload_sha256)
       └─ project_application(application_id, source_submission_id)
            ├─ project_site / project_equipment / project_finance
            ├─ project_schedule / project_grid
            ├─ project_permit_checklist / project_resident_risk
            ├─ project_attachment
            │    └─ project_fact(source_attachment_id, source_page, source_excerpt)
            ├─ project_fact(source_kind='user_input')
            ├─ project_stage → project_stage_event
            ├─ project_location_link → canonical_place
            └─ project_case_link → conflict_case → case_evidence → episode → meeting_segment
```

`project_intake`는 사업의 루트이고, `project_application`은 제출 revision별 정본이다. 구조화 테이블은 화면 조회를 위한 현재 값, `project_fact`는 값의 출처·추출·검수 이력이다. 원본 payload는 절대 버리지 않고 revision마다 해시와 함께 보존한다.

## 2. 사용자 입력 필드와 저장 테이블

`project_field_definition`에 아래 필드를 등록해 화면·API·첨부 추출기가 같은 이름과 타입을 사용한다. `required_flag=1`은 현재 MVP 최소 입력인 `project_name`, `site_address`에만 적용한다. 나머지는 미입력(`NULL`)과 “확인 필요”를 구분한다.

| 구분 | 필드 | 저장 테이블 | 타입/단위 |
|---|---|---|---|
| 사업 기본정보 | `project_name`, `business_type`, `permit_type` | `project_application` | text |
| 신청자 | `applicant_name`, `applicant_type`, `corporate_name`, `contractor_name` | `project_application` | text |
| 입지 | `site_address`, `lot_number`, `land_category`, `building_address`, `building_use` | `project_site` | text |
| 설비 | `installed_capacity_kw`, `module_count`, `module_capacity_w`, `inverter_count`, `inverter_capacity_kva`, `installation_height_m`, `installation_area_sqm` | `project_equipment` | numeric / kW·count·W·kVA·m·㎡ |
| 사업비·수익 | `total_project_cost_krw`, `construction_cost_per_kw`, `annual_generation_mwh`, `annual_transmission_mwh`, `lease_fee_krw`, `resident_revenue_share` | `project_finance` | numeric / KRW·KRW/kW·MWh·% |
| 일정 | `permit_application_date`, `permit_date`, `construction_start_date`, `expected_completion_date`, `business_start_date`, `operation_period_years` | `project_schedule` | date(ISO) / years |
| 전력계통 | `grid_connection_point`, `connection_voltage_v`, `transformer_info`, `power_purchase_method` | `project_grid` | text / V |
| 인허가 | `development_permit_required`, `urban_management_plan_required`, `construction_plan_report`, `environmental_assessment_required`, `structural_safety_review` | `project_permit_checklist` | nullable boolean |
| 주민·민원 | `resident_consent_required`, `construction_consent`, `complaint_occurred`, `complaint_stop_commitment`, `removal_commitment`, `complaint_type` | `project_resident_risk` | nullable boolean / text |

수치에는 음수 금지, 수량에는 정수, 주민 수익 배분율에는 0~100 범위를 적용한다. 날짜는 `YYYY-MM-DD`로 정규화하고 순서가 어긋나면 저장을 막지 않고 `schedule_status='inconsistent'`와 경고로 남긴다.

## 3. 출처·첨부·추출 컬럼

### 제출과 신청

- `project_intake.input_schema_version`, `intake_channel`, `created_by`, `updated_by`, `last_submitted_at`: 어떤 입력 계약과 채널로 들어왔는지 기록한다.
- `project_intake_submission.schema_version`, `submission_channel`, `submitted_by`, `raw_payload_json`, `payload_sha256`, `validation_status`, `validation_errors_json`, `warnings_json`, `validated_at`: revision 단위의 불변 제출 기록이다.
- `project_application.source_submission_id`: 구조화된 신청이 어느 raw submission에서 만들어졌는지 연결한다.

### 첨부파일

`project_attachment`는 파일을 읽지 못해도 먼저 메타데이터를 저장한다.

- 파일 식별: `document_type`, `file_name`, `storage_uri`, `source_url`, `mime_type`, `sha256`, `file_size_bytes`, `document_date`
- 분석 상태: `extraction_status`, `extraction_started_at`, `extracted_at`, `extraction_error`
- 분석 재현: `extractor_name`, `extractor_version`, `content_text_uri`, `text_sha256`, `page_count`, `ocr_used`
- 운영: `is_required`, `uploaded_by`, `source_document_id`

HWP·PDF·변환 PDF·OpenDataLoader 결과는 파일 형식이 달라도 이 메타데이터와 `project_fact`로 연결한다. 파일 자체의 경로와 해시는 `storage_uri`·`sha256`으로 보존하고, 추출 텍스트는 `content_text_uri` 또는 별도 artifact로 보존한다.

### 사실(fact) 출처

`project_fact`는 입력 필드 하나의 값과 출처를 한 행에 저장한다.

- 값: `field_name`, `value_type`, `value_text`, `value_numeric`, `value_date`, `value_boolean`, `value_json`, `unit`
- 출처: `source_kind`, `source_field`, `source_attachment_id`, `source_artifact_id`, `source_document_id`, `source_paragraph_id`, `source_page`, `source_char_start`, `source_char_end`, `source_excerpt`
- 분석·검수: `extraction_method`, `extraction_model`, `extraction_version`, `confidence`, `fact_status`, `is_current`, `review_status`, `reviewed_by`, `reviewed_at`, `review_note`

따라서 “현재 값은 얼마인가”는 구조화 테이블에서, “어느 첨부파일 몇 페이지의 어떤 문장에서 나왔나”는 `project_fact`에서 조회한다. 첨부 추출값은 `source_kind='attachment'`, 사용자가 직접 입력한 값은 `source_kind='user_input'`, 회의록 근거를 재사용한 값은 `source_kind='source_document'`로 구분한다.

## 4. 위치 해석 컬럼

정확 좌표를 임의로 만들지 않는다. 주소 API나 사용자가 실제로 제공한 좌표가 있을 때만 `canonical_place.latitude/longitude`를 채운다.

- `project_site.site_address_normalized`, `site_address_type`, `site_resolution_status`, `site_resolution_method`, `site_resolution_confidence`: 사업지 주소가 도·시군·읍면·리 수준인지, 도로명/지번으로 해석됐는지 기록한다.
- `project_site.building_*`: 옥상·지붕형 사업의 건축물 주소 해석을 사업지와 분리한다.
- `project_location_link.relation_type`: `subject_site`, `building_site`, `lot`, `grid_connection_point`를 구분한다.
- `project_location_link.raw_query`, `candidate_rank`, `resolution_method`, `geo_provider`, `resolved_at`, `distance_status`: 입력 원문과 후보 순위를 보존한다.
- `canonical_place.geo_precision`: `parcel`, `building`, `road_address`, `jibun_address`, `village`, `ri`, `eup_myeon`, `city_county`, `province`, `unknown` 중 하나다.

행정구역만 확인된 사건은 `same_ri`·`same_eup_myeon`·`same_city_county`로 검색한다. 입력 좌표와 사건 후보 좌표가 모두 있을 때만 거리값을 계산한다. 읍면·리의 중심점을 사업지 좌표로 넣지 않으므로, 좌표가 없는 결과의 거리는 `NULL`이고 `unknown`이다.

## 5. 6단계 절차와 민원 연결

`project_stage`는 현재 상태 snapshot, `project_stage_event`는 날짜와 근거를 포함한 이력이다.

| 단계 | `stage_code` | 주요 연결 컬럼 |
|---:|---|---|
| 1 | `application` | `permit_application_date` |
| 2 | `power_generation_permit` | `permit_date`, `permit_type` |
| 3 | `development_environment_review` | 개발행위·도시관리계획·환경·구조안전 여부 |
| 4 | `resident_consultation_complaint` | 주민동의·공사동의·민원 여부·`complaint_type` |
| 5 | `construction_completion` | 공사 시작·준공 예정·사업개시 일정 |
| 6 | `grid_connection_operation` | 계통연계 지점·전압·변압기·판매 방식 |

각 단계의 `source_field`, `source_fact_id`, `confidence`, `last_evaluated_at`을 통해 상태가 어떤 입력값에서 계산됐는지 되짚는다. 이벤트도 `source_fact_id`, `source_attachment_id`, `source_document_id`, 페이지·발췌·검수 컬럼을 가진다.

`project_case_link`는 과거 `conflict_case`와의 비교 후보다. `match_score`만 저장하지 않고 `matching_features_json`, `match_method`, `location_match_type`, `review_reason`, `review_status`, `reviewed_by`, `reviewed_at`, `review_note`를 함께 저장한다. 같은 시군이라는 이유만으로 동일 민원으로 확정하지 않으며, 자동 비교 후보는 `pending`으로 남긴다.

## 6. 기획서 안전장치와 조회 원칙

- 원문은 `source_document`·`document_page`·`meeting_segment`에 보존하고, 사건 근거는 `case_evidence`까지 연결한다.
- 쟁점 요약은 `segment_issue`의 `evidence_span`, `confidence`, `review_status` 없이 확정하지 않는다.
- `case_review`는 실제 분쟁성·태양광 관련성·식별 가능성·분리 필요성을 점수와 사유 코드로 저장하며, `reviewer_id`, `reviewed_at`, `review_note`, `decision_source`로 사람 검토와 규칙 판정을 구분한다.
- 시스템은 `eligible`, `needs_review`, `rejected`를 반환할 수 있지만 인허가 가능·불가를 결정하지 않는다.
- 모든 화면 결과는 문서일·페이지·원문 링크와 함께 보여주고, 위치 정밀도와 거리 상태를 함께 표시한다.
