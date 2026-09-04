-- Production migration target for Lucera.
-- Requires Cloud DB/PostgreSQL permissions that allow these extensions.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Install vector only when the selected embedding model/dimension is fixed.
-- CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS source_system (
    source_system_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    provider text NOT NULL,
    base_url text,
    terms_url text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The 27 local target units: 5 autonomous cities, 17 autonomous counties,
-- and 5 autonomous districts. Parent scopes are stored as metadata rows.
CREATE TABLE IF NOT EXISTS administrative_region (
    region_code text PRIMARY KEY,
    region_name text NOT NULL UNIQUE,
    province text NOT NULL,
    region_group text NOT NULL,
    region_type text NOT NULL,
    parent_region_code text,
    assembly_id text,
    available boolean NOT NULL DEFAULT false,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_kind text NOT NULL DEFAULT 'catalog',
    source_reference text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_document (
    document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id uuid NOT NULL REFERENCES source_system(source_system_id),
    source_record_key text,
    title text NOT NULL,
    document_type text NOT NULL,
    source_url text,
    original_file_url text,
    storage_uri text,
    mime_type text,
    sha256 char(64),
    file_size_bytes bigint,
    published_at date,
    retrieved_at timestamptz NOT NULL DEFAULT now(),
    access_policy text NOT NULL DEFAULT 'public',
    processing_status text NOT NULL DEFAULT 'raw',
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_system_id, source_record_key),
    UNIQUE(sha256)
);

CREATE TABLE IF NOT EXISTS document_artifact (
    artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    artifact_role text NOT NULL CHECK (artifact_role IN ('original_download', 'official_source', 'html_snapshot', 'rendered_pdf', 'opendataloader_json', 'extracted_text')),
    storage_uri text,
    source_url text,
    mime_type text,
    file_name text,
    sha256 char(64),
    file_size_bytes bigint,
    acquisition_method text,
    derived_from_artifact_id uuid REFERENCES document_artifact(artifact_id) ON DELETE SET NULL,
    parser_name text,
    parser_version text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(document_id, artifact_role, sha256)
);

CREATE INDEX IF NOT EXISTS idx_document_artifact_document ON document_artifact(document_id, artifact_role);

CREATE TABLE IF NOT EXISTS ingestion_job (
    ingestion_job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id uuid REFERENCES source_system(source_system_id),
    job_type text NOT NULL,
    requested_keyword text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    requested_count integer,
    processed_count integer NOT NULL DEFAULT 0,
    error_count integer NOT NULL DEFAULT 0,
    error_message text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS meeting (
    meeting_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL UNIQUE REFERENCES source_document(document_id) ON DELETE CASCADE,
    council_level text,
    administrative_region_code text REFERENCES administrative_region(region_code),
    assembly_id text,
    assembly_name text,
    province text,
    city_county text,
    session_number text,
    assembly_number text,
    meeting_order text,
    meeting_type text,
    meeting_title text,
    meeting_date date,
    agenda_text text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS speaker (
    speaker_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    role text,
    affiliation text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(name, role, affiliation)
);

CREATE TABLE IF NOT EXISTS document_page (
    page_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    page_no integer NOT NULL,
    text_original text NOT NULL DEFAULT '',
    text_redacted text NOT NULL DEFAULT '',
    raw_text_uri text,
    image_uri text,
    ocr_used boolean NOT NULL DEFAULT false,
    ocr_confidence numeric,
    parser_name text,
    parser_version text,
    UNIQUE(document_id, page_no)
);

CREATE TABLE IF NOT EXISTS meeting_segment (
    segment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    page_from integer NOT NULL,
    page_to integer NOT NULL,
    meeting_id uuid REFERENCES meeting(meeting_id) ON DELETE SET NULL,
    speaker_id uuid REFERENCES speaker(speaker_id) ON DELETE SET NULL,
    section_title text,
    agenda_no text,
    ordinal integer NOT NULL,
    segment_type text NOT NULL DEFAULT 'paragraph',
    text_original text NOT NULL DEFAULT '',
    text_redacted text NOT NULL DEFAULT '',
    char_start integer,
    char_end integer,
    parse_confidence numeric,
    relevance_status text NOT NULL DEFAULT 'unreviewed',
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(document_id, ordinal)
);

-- Hierarchical evidence layer. meeting_segment is the canonical paragraph /
-- speech-block table; these tables retain exact sentence and keyword offsets.
CREATE TABLE IF NOT EXISTS sentences (
    sentence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    paragraph_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_order integer NOT NULL,
    text text NOT NULL DEFAULT '',
    text_redacted text NOT NULL DEFAULT '',
    char_start integer NOT NULL,
    char_end integer NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(paragraph_id, sentence_order)
);

CREATE TABLE IF NOT EXISTS keyword_mentions (
    mention_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sentence_id uuid NOT NULL REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    keyword text NOT NULL,
    normalized_keyword text NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    match_type text NOT NULL,
    keyword_group text,
    problem_category text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(sentence_id, keyword, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS canonical_place (
    place_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    place_type text NOT NULL,
    raw_name text,
    normalized_name text,
    road_address text,
    jibun_address text,
    province text,
    city_county text,
    eup_myeon text,
    ri text,
    admin_code text,
    geom geography(Point, 4326),
    geom_wkt text,
    geo_provider text,
    geo_precision text NOT NULL DEFAULT 'unknown',
    geocode_confidence numeric,
    location_status text NOT NULL DEFAULT 'candidate',
    resolution_method text,
    source_document_id uuid REFERENCES source_document(document_id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS administrative_area (
    administrative_area_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    level text NOT NULL,
    admin_code text,
    canonical_name text NOT NULL,
    province text,
    city_county text,
    eup_myeon text,
    ri text,
    parent_id uuid REFERENCES administrative_area(administrative_area_id),
    centroid geography(Point, 4326),
    boundary_source_document_id uuid REFERENCES source_document(document_id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(level, admin_code, canonical_name)
);

CREATE TABLE IF NOT EXISTS place_mention (
    mention_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    segment_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    surface_form text NOT NULL,
    normalized_form text,
    mention_type text NOT NULL DEFAULT 'unknown',
    context_text text,
    char_start integer,
    char_end integer,
    extraction_run_id uuid,
    confidence numeric,
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS place_resolution_candidate (
    mention_id uuid NOT NULL REFERENCES place_mention(mention_id) ON DELETE CASCADE,
    place_id uuid NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    rank integer NOT NULL,
    match_method text NOT NULL,
    confidence numeric,
    resolution_reason text,
    is_selected boolean NOT NULL DEFAULT false,
    review_status text NOT NULL DEFAULT 'pending',
    PRIMARY KEY (mention_id, place_id)
);

CREATE TABLE IF NOT EXISTS segment_place_link (
    segment_place_link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    segment_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    place_id uuid NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    mention_id uuid REFERENCES place_mention(mention_id) ON DELETE SET NULL,
    relation_type text NOT NULL,
    distance_m numeric,
    distance_status text NOT NULL DEFAULT 'unknown',
    confidence numeric,
    evidence_text text,
    resolution_method text,
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(segment_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS extraction_run (
    extraction_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    model_name text,
    model_version text,
    prompt_version text,
    validator_version text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS issue_taxonomy (
    taxonomy_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taxonomy_version text NOT NULL,
    issue_code text NOT NULL,
    parent_code text,
    issue_name text NOT NULL,
    description text,
    active boolean NOT NULL DEFAULT true,
    UNIQUE(taxonomy_version, issue_code)
);

CREATE TABLE IF NOT EXISTS segment_issue (
    segment_issue_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    segment_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    taxonomy_id uuid NOT NULL REFERENCES issue_taxonomy(taxonomy_id),
    issue_code text NOT NULL,
    polarity text NOT NULL DEFAULT 'neutral',
    target_type text NOT NULL DEFAULT 'unknown',
    confidence numeric,
    evidence_span text,
    extraction_run_id uuid REFERENCES extraction_run(extraction_run_id),
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(segment_id, taxonomy_id, issue_code)
);

CREATE TABLE IF NOT EXISTS conflict_case (
    case_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_key text NOT NULL UNIQUE,
    case_name text,
    canonical_title text,
    municipality text,
    village text,
    address text,
    project_name text,
    facility_type text,
    summary text,
    case_status text NOT NULL DEFAULT 'unknown',
    started_on date,
    ended_on date,
    representative_place_id uuid REFERENCES canonical_place(place_id),
    confidence numeric,
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_review (
    case_id uuid PRIMARY KEY REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    decision text NOT NULL CHECK (decision IN ('eligible', 'needs_review', 'rejected')),
    quality_score numeric NOT NULL,
    subject_score numeric NOT NULL,
    dispute_score numeric NOT NULL,
    identity_score numeric NOT NULL,
    separation_score numeric NOT NULL,
    evidence_paragraph_count integer NOT NULL DEFAULT 0,
    trigger_sentence_count integer NOT NULL DEFAULT 0,
    reason_codes_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    review_version text NOT NULL,
    reviewer_id text,
    reviewed_at timestamptz,
    review_note text,
    decision_source text NOT NULL DEFAULT 'deterministic_gate' CHECK (decision_source IN ('deterministic_gate', 'human', 'llm_assist')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    episode_key text NOT NULL UNIQUE,
    document_id uuid NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    paragraph_start integer NOT NULL,
    paragraph_end integer NOT NULL,
    issue_type text,
    issue_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    stance text NOT NULL DEFAULT 'unknown',
    procedure_stage text,
    confidence numeric,
    grouping_score numeric,
    grouping_method text NOT NULL DEFAULT 'deterministic_v1',
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS episode_evidence (
    episode_id uuid NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_id uuid NOT NULL REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    evidence_role text NOT NULL,
    link_confidence numeric,
    PRIMARY KEY(episode_id, paragraph_id, sentence_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS case_evidence (
    case_evidence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    episode_id uuid NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_id uuid REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    evidence_role text NOT NULL,
    link_confidence numeric,
    review_status text NOT NULL DEFAULT 'pending',
    UNIQUE(case_id, episode_id, paragraph_id, sentence_id, evidence_role)
);

-- The issue and the administrative process are separate evidence layers.
-- Every extracted event remains tied to the paragraph that supports it.
CREATE TABLE IF NOT EXISTS case_process_event (
    process_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    episode_id uuid REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id uuid REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    event_date date,
    actor text,
    action_text text NOT NULL,
    outcome text,
    certainty text NOT NULL DEFAULT 'unconfirmed' CHECK (certainty IN ('confirmed', 'inferred', 'unconfirmed')),
    confidence numeric CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_text text,
    extraction_method text NOT NULL DEFAULT 'deterministic_process_v1',
    review_status text NOT NULL DEFAULT 'pending',
    data_origin text NOT NULL DEFAULT 'meeting_record' CHECK (data_origin IN ('meeting_record', 'synthetic', 'user_input', 'inferred')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(case_id, paragraph_id, event_type, action_text)
);

CREATE INDEX IF NOT EXISTS idx_case_process_event_case ON case_process_event(case_id, event_date, event_type);
CREATE INDEX IF NOT EXISTS idx_case_process_event_type ON case_process_event(event_type, certainty, review_status);

-- Ranked inferred places are kept separate from a confirmed exact site.
CREATE TABLE IF NOT EXISTS case_location_candidate (
    case_location_candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    place_id uuid NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    rank integer NOT NULL,
    inference_method text NOT NULL,
    confidence numeric,
    evidence_episode_id uuid REFERENCES episodes(episode_id) ON DELETE CASCADE,
    evidence_paragraph_id uuid REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    evidence_text text,
    is_selected boolean NOT NULL DEFAULT false,
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(case_id, place_id)
);

-- Keep uncertain cross-document links reviewable instead of silently merging
-- separate complaints into one case.
CREATE TABLE IF NOT EXISTS case_link_candidate (
    candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    left_case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    right_case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    match_score numeric NOT NULL CHECK (match_score >= 0 AND match_score <= 1),
    matching_features jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    review_status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (left_case_id < right_case_id),
    UNIQUE(left_case_id, right_case_id)
);

CREATE TABLE IF NOT EXISTS case_segment (
    case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    segment_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    relation_type text NOT NULL DEFAULT 'evidence',
    confidence numeric,
    review_status text NOT NULL DEFAULT 'pending',
    PRIMARY KEY(case_id, segment_id)
);

CREATE TABLE IF NOT EXISTS evidence_claim (
    claim_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    segment_id uuid NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    case_id uuid REFERENCES conflict_case(case_id) ON DELETE SET NULL,
    subject_text text,
    predicate text NOT NULL,
    object_text text,
    event_date date,
    certainty text NOT NULL DEFAULT 'unconfirmed',
    evidence_span text,
    extraction_run_id uuid REFERENCES extraction_run(extraction_run_id),
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS permit_project (
    project_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id uuid REFERENCES source_system(source_system_id),
    source_record_key text,
    facility_name text,
    company_name text,
    capacity_kw numeric,
    permit_date date,
    operation_status text,
    road_address text,
    jibun_address text,
    province text,
    city_county text,
    eup_myeon text,
    ri text,
    geom geography(Point, 4326),
    location_status text NOT NULL DEFAULT 'unknown',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(source_system_id, source_record_key)
);

-- Objective prechecks are versioned and kept separate from generated answers.
-- Synthetic rules can be replaced with reviewed official rules later.
CREATE TABLE IF NOT EXISTS siting_rule (
    rule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    region_code text REFERENCES administrative_region(region_code),
    reference_object text NOT NULL,
    operator text NOT NULL DEFAULT 'gte' CHECK (operator IN ('gte', 'gt', 'lte', 'lt', 'eq', 'exists')),
    threshold_value numeric,
    unit text,
    rule_name text NOT NULL,
    rule_description text,
    source_title text,
    source_article text,
    valid_from date,
    valid_to date,
    severity text NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    data_origin text NOT NULL DEFAULT 'official' CHECK (data_origin IN ('official', 'synthetic', 'user_input')),
    active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_siting_rule_region ON siting_rule(region_code, reference_object, active, valid_from);

-- User-entered project intake is separate from permit_project, which stores
-- external/public permit records.
CREATE TABLE IF NOT EXISTS project_intake (
    project_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_key text NOT NULL UNIQUE,
    project_status text NOT NULL DEFAULT 'draft' CHECK (project_status IN ('draft', 'submitted', 'under_review', 'completed', 'archived')),
    current_revision_no integer NOT NULL DEFAULT 0,
    input_schema_version text NOT NULL DEFAULT 'project-intake-v2',
    intake_channel text NOT NULL DEFAULT 'web_form' CHECK (intake_channel IN ('web_form', 'api', 'import', 'draft_save')),
    created_by text,
    updated_by text,
    last_submitted_at timestamptz,
    completed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Runtime contract for the web form, attachment extraction, provenance facts,
-- and the six-stage workflow.  It keeps the physical schema and UI field
-- definitions inspectable from the database itself.
CREATE TABLE IF NOT EXISTS project_field_definition (
    field_name text PRIMARY KEY,
    section_code text NOT NULL CHECK (section_code IN ('business', 'applicant', 'site', 'equipment', 'finance', 'schedule', 'grid', 'permit', 'resident')),
    display_name text NOT NULL,
    field_type text NOT NULL CHECK (field_type IN ('text', 'numeric', 'date', 'boolean')),
    unit text,
    stage_code text,
    required_flag boolean NOT NULL DEFAULT false,
    source_policy text NOT NULL DEFAULT 'user_or_attachment' CHECK (source_policy IN ('user_input', 'attachment', 'user_or_attachment', 'derived')),
    validation_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    display_order integer NOT NULL,
    active boolean NOT NULL DEFAULT true,
    description text,
    UNIQUE(section_code, display_order)
);

CREATE TABLE IF NOT EXISTS project_intake_submission (
    submission_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    revision_no integer NOT NULL,
    schema_version text NOT NULL DEFAULT 'project-intake-v2',
    submission_channel text NOT NULL DEFAULT 'web_form' CHECK (submission_channel IN ('web_form', 'api', 'import', 'draft_save')),
    submitted_by text,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload_sha256 char(64),
    validation_status text NOT NULL DEFAULT 'valid' CHECK (validation_status IN ('valid', 'valid_with_warnings', 'invalid')),
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    validated_at timestamptz,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(project_id, revision_no)
);

CREATE TABLE IF NOT EXISTS project_application (
    application_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    revision_no integer NOT NULL,
    project_name text NOT NULL,
    business_type text,
    permit_type text,
    applicant_name text,
    applicant_type text,
    corporate_name text,
    contractor_name text,
    source_submission_id uuid REFERENCES project_intake_submission(submission_id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'submitted' CHECK (status IN ('draft', 'submitted', 'under_review', 'approved', 'rejected', 'superseded')),
    validation_status text NOT NULL DEFAULT 'valid',
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(project_id, revision_no)
);

CREATE TABLE IF NOT EXISTS project_site (
    site_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    site_address text NOT NULL,
    site_address_normalized text,
    lot_number text,
    land_category text,
    building_address text,
    building_address_normalized text,
    building_use text,
    site_address_type text NOT NULL DEFAULT 'unknown' CHECK (site_address_type IN ('road', 'jibun', 'mixed', 'administrative_only', 'unknown')),
    site_resolution_status text NOT NULL DEFAULT 'unresolved' CHECK (site_resolution_status IN ('unresolved', 'parsed', 'candidate', 'resolved', 'reviewed')),
    site_resolution_method text,
    site_resolution_confidence numeric CHECK (site_resolution_confidence IS NULL OR (site_resolution_confidence >= 0 AND site_resolution_confidence <= 1)),
    building_address_type text NOT NULL DEFAULT 'unknown' CHECK (building_address_type IN ('road', 'jibun', 'mixed', 'administrative_only', 'unknown')),
    building_resolution_status text NOT NULL DEFAULT 'unresolved' CHECK (building_resolution_status IN ('unresolved', 'parsed', 'candidate', 'resolved', 'reviewed')),
    building_resolution_method text,
    building_resolution_confidence numeric CHECK (building_resolution_confidence IS NULL OR (building_resolution_confidence >= 0 AND building_resolution_confidence <= 1)),
    site_place_id uuid REFERENCES canonical_place(place_id),
    building_place_id uuid REFERENCES canonical_place(place_id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_equipment (
    equipment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    installed_capacity_kw numeric,
    module_count integer,
    module_capacity_w numeric,
    inverter_count integer,
    inverter_capacity_kva numeric,
    installation_height_m numeric,
    installation_area_sqm numeric,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (installed_capacity_kw IS NULL OR installed_capacity_kw >= 0),
    CHECK (module_count IS NULL OR module_count >= 0),
    CHECK (module_capacity_w IS NULL OR module_capacity_w >= 0),
    CHECK (inverter_count IS NULL OR inverter_count >= 0),
    CHECK (inverter_capacity_kva IS NULL OR inverter_capacity_kva >= 0),
    CHECK (installation_height_m IS NULL OR installation_height_m >= 0),
    CHECK (installation_area_sqm IS NULL OR installation_area_sqm >= 0)
);

CREATE TABLE IF NOT EXISTS project_finance (
    finance_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    total_project_cost_krw numeric,
    construction_cost_per_kw numeric,
    annual_generation_mwh numeric,
    annual_transmission_mwh numeric,
    lease_fee_krw numeric,
    resident_revenue_share numeric CHECK (resident_revenue_share IS NULL OR (resident_revenue_share >= 0 AND resident_revenue_share <= 100)),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (total_project_cost_krw IS NULL OR total_project_cost_krw >= 0),
    CHECK (construction_cost_per_kw IS NULL OR construction_cost_per_kw >= 0),
    CHECK (annual_generation_mwh IS NULL OR annual_generation_mwh >= 0),
    CHECK (annual_transmission_mwh IS NULL OR annual_transmission_mwh >= 0),
    CHECK (lease_fee_krw IS NULL OR lease_fee_krw >= 0)
);

CREATE TABLE IF NOT EXISTS project_schedule (
    schedule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    permit_application_date date,
    permit_date date,
    construction_start_date date,
    expected_completion_date date,
    business_start_date date,
    operation_period_years numeric,
    schedule_status text NOT NULL DEFAULT 'unknown' CHECK (schedule_status IN ('unknown', 'planned', 'in_progress', 'completed', 'inconsistent')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_grid (
    grid_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    grid_connection_point text,
    connection_voltage_v numeric,
    transformer_info text,
    power_purchase_method text,
    connection_status text NOT NULL DEFAULT 'unknown' CHECK (connection_status IN ('unknown', 'planned', 'requested', 'approved', 'connected', 'rejected')),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_permit_checklist (
    permit_checklist_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    development_permit_required boolean,
    urban_management_plan_required boolean,
    construction_plan_report boolean,
    environmental_assessment_required boolean,
    structural_safety_review boolean,
    checklist_status text NOT NULL DEFAULT 'not_started' CHECK (checklist_status IN ('not_started', 'in_progress', 'completed', 'needs_review')),
    checked_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_resident_risk (
    resident_risk_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    resident_consent_required boolean,
    construction_consent boolean,
    complaint_occurred boolean,
    complaint_stop_commitment boolean,
    removal_commitment boolean,
    complaint_type text,
    risk_status text NOT NULL DEFAULT 'unknown' CHECK (risk_status IN ('unknown', 'none_reported', 'reported', 'under_consultation', 'resolved', 'needs_review')),
    complaint_source text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_attachment (
    attachment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    document_type text NOT NULL CHECK (document_type IN ('business_plan', 'profit_loss_plan', 'funding_plan', 'land_use_consent', 'building_register', 'land_register', 'cadastral_map', 'land_use_plan', 'structural_safety_certificate')),
    file_name text,
    storage_uri text,
    mime_type text,
    sha256 char(64),
    file_size_bytes bigint,
    extraction_status text NOT NULL DEFAULT 'not_started' CHECK (extraction_status IN ('not_started', 'queued', 'extracted', 'failed', 'reviewed')),
    source_document_id uuid REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_url text,
    document_date date,
    page_count integer CHECK (page_count IS NULL OR page_count >= 0),
    is_required boolean NOT NULL DEFAULT false,
    extraction_started_at timestamptz,
    extracted_at timestamptz,
    extractor_name text,
    extractor_version text,
    extraction_error text,
    content_text_uri text,
    text_sha256 char(64),
    ocr_used boolean,
    uploaded_by text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(application_id, document_type, sha256)
);

CREATE TABLE IF NOT EXISTS project_fact (
    fact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    field_name text NOT NULL,
    value_type text NOT NULL CHECK (value_type IN ('text', 'numeric', 'date', 'boolean', 'json')),
    value_text text,
    value_numeric numeric,
    value_date date,
    value_boolean boolean,
    value_json jsonb,
    unit text,
    source_kind text NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field text,
    source_attachment_id uuid REFERENCES project_attachment(attachment_id) ON DELETE SET NULL,
    source_artifact_id uuid REFERENCES document_artifact(artifact_id) ON DELETE SET NULL,
    source_document_id uuid REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_paragraph_id uuid REFERENCES meeting_segment(segment_id) ON DELETE SET NULL,
    source_page integer,
    source_char_start integer,
    source_char_end integer,
    source_excerpt text,
    extraction_method text NOT NULL DEFAULT 'form_input',
    extraction_model text,
    extraction_version text,
    confidence numeric,
    fact_status text NOT NULL DEFAULT 'active' CHECK (fact_status IN ('active', 'superseded', 'rejected')),
    is_current boolean NOT NULL DEFAULT true,
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'accepted', 'rejected', 'needs_review')),
    reviewed_by text,
    reviewed_at timestamptz,
    review_note text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS project_stage (
    stage_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    stage_code text NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    stage_order integer NOT NULL,
    stage_status text NOT NULL DEFAULT 'unknown' CHECK (stage_status IN ('unknown', 'required', 'planned', 'in_progress', 'completed', 'reported', 'not_applicable')),
    planned_date date,
    actual_date date,
    required_flag boolean,
    case_id uuid REFERENCES conflict_case(case_id) ON DELETE SET NULL,
    notes text,
    source_kind text NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field text,
    source_fact_id uuid REFERENCES project_fact(fact_id) ON DELETE SET NULL,
    confidence numeric CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    last_evaluated_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(application_id, stage_code)
);

-- Current workflow snapshots are complemented by immutable stage events.
CREATE TABLE IF NOT EXISTS project_stage_event (
    stage_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stage_id uuid NOT NULL REFERENCES project_stage(stage_id) ON DELETE CASCADE,
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    stage_code text NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    event_type text NOT NULL,
    event_status text NOT NULL DEFAULT 'reported' CHECK (event_status IN ('reported', 'planned', 'completed', 'cancelled', 'unknown')),
    event_date date,
    title text,
    description text,
    source_kind text NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field text,
    source_fact_id uuid REFERENCES project_fact(fact_id) ON DELETE SET NULL,
    source_attachment_id uuid REFERENCES project_attachment(attachment_id) ON DELETE SET NULL,
    source_document_id uuid REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_page integer,
    source_excerpt text,
    confidence numeric,
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'accepted', 'rejected', 'needs_review')),
    reviewed_by text,
    reviewed_at timestamptz,
    review_note text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_location_link (
    project_location_link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    place_id uuid NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    relation_type text NOT NULL CHECK (relation_type IN ('subject_site', 'building_site', 'lot', 'grid_connection_point')),
    raw_query text,
    candidate_rank integer,
    resolution_method text,
    geo_provider text,
    resolved_at timestamptz,
    confidence numeric,
    distance_status text NOT NULL DEFAULT 'unknown',
    review_status text NOT NULL DEFAULT 'pending',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(application_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS project_case_link (
    project_case_link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    application_id uuid NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    case_id uuid NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    stage_code text NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    relation_type text NOT NULL DEFAULT 'historical_comparable',
    match_score numeric NOT NULL CHECK (match_score >= 0 AND match_score <= 1),
    matching_features jsonb NOT NULL DEFAULT '{}'::jsonb,
    match_method text NOT NULL DEFAULT 'deterministic_v1',
    location_match_type text,
    review_reason text,
    reviewed_by text,
    reviewed_at timestamptz,
    review_note text,
    review_status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(application_id, case_id, stage_code, relation_type)
);

CREATE TABLE IF NOT EXISTS project_place_link (
    project_id uuid NOT NULL REFERENCES permit_project(project_id) ON DELETE CASCADE,
    place_id uuid NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    relation_type text NOT NULL DEFAULT 'located_at',
    confidence numeric,
    evidence text,
    review_status text NOT NULL DEFAULT 'pending',
    PRIMARY KEY(project_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS permit_meeting_link (
    link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES permit_project(project_id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    meeting_id uuid REFERENCES meeting(meeting_id) ON DELETE CASCADE,
    segment_id uuid REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    relation_type text NOT NULL,
    match_score numeric NOT NULL,
    issue_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    link_reason text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(project_id, segment_id)
);

CREATE INDEX IF NOT EXISTS idx_permit_meeting_project
    ON permit_meeting_link(project_id, match_score DESC);
CREATE INDEX IF NOT EXISTS idx_permit_meeting_segment
    ON permit_meeting_link(segment_id);

CREATE TABLE IF NOT EXISTS address_lookup (
    address_lookup_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_query text NOT NULL,
    normalized_query text,
    provider text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    response_status text,
    response jsonb NOT NULL DEFAULT '{}'::jsonb,
    candidate_count integer NOT NULL DEFAULT 0,
    selected_place_id uuid REFERENCES canonical_place(place_id),
    resolution_status text NOT NULL DEFAULT 'unresolved',
    UNIQUE(raw_query, provider)
);

CREATE TABLE IF NOT EXISTS search_request (
    search_request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_address text NOT NULL,
    normalized_address text,
    province text,
    city_county text,
    eup_myeon text,
    ri text,
    geom geography(Point, 4326),
    geocode_status text NOT NULL DEFAULT 'not_requested',
    radius_m numeric NOT NULL DEFAULT 5000,
    keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    issue_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    from_date date,
    limit_count integer NOT NULL DEFAULT 20,
    created_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS complaint_submission (
    complaint_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL,
    raw_text text NOT NULL,
    title text NOT NULL DEFAULT '영암군 민원',
    address text NOT NULL,
    normalized_address text NOT NULL,
    province text,
    city_county text NOT NULL CHECK (city_county = '영암군'),
    eup_myeon text,
    ri text,
    geom geography(Point, 4326) NOT NULL,
    geocode_status text NOT NULL DEFAULT 'unresolved',
    ai_summary text NOT NULL DEFAULT '',
    issue_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'received',
    data_origin text NOT NULL DEFAULT 'user_input',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_conversation (
    conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    complaint_id uuid NOT NULL REFERENCES complaint_submission(complaint_id) ON DELETE CASCADE,
    address text NOT NULL,
    geom geography(Point, 4326) NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_message (
    message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES chat_conversation(conversation_id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS complaint_evidence (
    complaint_id uuid NOT NULL REFERENCES complaint_submission(complaint_id) ON DELETE CASCADE,
    evidence_id text NOT NULL,
    evidence_type text NOT NULL DEFAULT 'meeting_evidence',
    rank integer NOT NULL DEFAULT 1,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (complaint_id, evidence_id, evidence_type)
);

CREATE TABLE IF NOT EXISTS review_task (
    review_task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type text NOT NULL,
    target_id uuid NOT NULL,
    reason_code text NOT NULL,
    priority integer NOT NULL DEFAULT 3,
    status text NOT NULL DEFAULT 'open',
    assigned_to text,
    reviewed_at timestamptz,
    decision text,
    note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(target_type, target_id, reason_code)
);

CREATE TABLE IF NOT EXISTS embedding (
    embedding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type text NOT NULL,
    target_id uuid NOT NULL,
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    dimensions integer NOT NULL,
    vector_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(target_type, target_id, embedding_model, embedding_version)
);

CREATE INDEX IF NOT EXISTS idx_source_document_published ON source_document(published_at);
CREATE INDEX IF NOT EXISTS idx_segment_document_page ON meeting_segment(document_id, page_from, page_to);
CREATE INDEX IF NOT EXISTS idx_sentence_paragraph ON sentences(paragraph_id, sentence_order);
CREATE INDEX IF NOT EXISTS idx_keyword_mention ON keyword_mentions(normalized_keyword, match_type);
CREATE INDEX IF NOT EXISTS idx_segment_text_trgm ON meeting_segment USING gin(text_redacted gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_place_geom ON canonical_place USING gist(geom);
CREATE INDEX IF NOT EXISTS idx_place_admin ON canonical_place(province, city_county, eup_myeon, ri);
CREATE INDEX IF NOT EXISTS idx_segment_issue ON segment_issue(issue_code, polarity, review_status);
CREATE INDEX IF NOT EXISTS idx_meeting_region_date ON meeting(province, city_county, meeting_date);
CREATE INDEX IF NOT EXISTS idx_episode_document ON episodes(document_id, paragraph_start, paragraph_end);
CREATE INDEX IF NOT EXISTS idx_episode_review ON episodes(review_status, confidence);
CREATE INDEX IF NOT EXISTS idx_case_evidence_case ON case_evidence(case_id, evidence_role);
CREATE INDEX IF NOT EXISTS idx_case_review_decision ON case_review(decision, quality_score);
CREATE INDEX IF NOT EXISTS idx_case_location_candidate ON case_location_candidate(case_id, is_selected, confidence);
CREATE INDEX IF NOT EXISTS idx_case_link_candidate_review ON case_link_candidate(status, match_score);
CREATE INDEX IF NOT EXISTS idx_project_application_project ON project_application(project_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_project_stage_workflow ON project_stage(application_id, stage_order);
CREATE INDEX IF NOT EXISTS idx_project_stage_event_workflow ON project_stage_event(application_id, stage_code, event_date);
CREATE INDEX IF NOT EXISTS idx_project_case_link_review ON project_case_link(project_id, review_status, match_score);
CREATE INDEX IF NOT EXISTS idx_project_fact_source ON project_fact(source_attachment_id, source_document_id);
CREATE INDEX IF NOT EXISTS idx_project_attachment_type ON project_attachment(application_id, document_type);

-- Compatibility names used by the application and analytical queries.
CREATE OR REPLACE VIEW documents AS
SELECT document_id, source_system_id, source_record_key, title, document_type,
       source_url, original_file_url, published_at, access_policy,
       processing_status, raw_payload AS raw_payload_json, metadata AS metadata_json,
       retrieved_at, created_at, updated_at
  FROM source_document;

CREATE OR REPLACE VIEW paragraphs AS
SELECT segment_id AS paragraph_id, document_id, page_from, page_to, meeting_id,
       speaker_id, section_title, agenda_no, ordinal AS paragraph_order,
       segment_type, text_original AS text, text_redacted, relevance_status,
       review_status, metadata AS metadata_json
  FROM meeting_segment;

CREATE OR REPLACE VIEW cases AS
SELECT case_id, case_key, COALESCE(canonical_title, case_name) AS canonical_title,
       municipality, village, address, project_name, facility_type, summary,
       case_status, started_on, ended_on, representative_place_id, confidence,
       review_status, metadata AS metadata_json
  FROM conflict_case;

CREATE OR REPLACE VIEW document_cases AS
SELECT e.document_id,
       ce.case_id,
       COALESCE(c.canonical_title, c.case_name) AS canonical_title,
       c.municipality, c.village, c.address, c.project_name, c.facility_type,
       COUNT(DISTINCT ce.paragraph_id) AS evidence_paragraph_count,
       COUNT(DISTINCT cp.segment_id) AS paragraph_count,
       COUNT(DISTINCT ce.episode_id) AS episode_count,
       MIN(ms.ordinal) AS first_paragraph_order,
       MAX(ms.ordinal) AS last_paragraph_order
  FROM episodes e
  JOIN case_evidence ce ON ce.episode_id = e.episode_id
  JOIN conflict_case c ON c.case_id = ce.case_id
  LEFT JOIN case_segment cs ON cs.case_id = ce.case_id
  LEFT JOIN meeting_segment cp
         ON cp.segment_id = cs.segment_id
        AND cp.document_id = e.document_id
  LEFT JOIN meeting_segment ms ON ms.segment_id = ce.paragraph_id
 GROUP BY e.document_id, ce.case_id, c.canonical_title, c.case_name,
          c.municipality, c.village, c.address, c.project_name, c.facility_type;

CREATE OR REPLACE VIEW case_paragraphs AS
SELECT cs.case_id,
       s.document_id,
       cs.segment_id AS paragraph_id,
       s.ordinal AS paragraph_order,
       s.page_from, s.page_to, s.segment_type, s.speaker_id,
       s.text_original, s.text_redacted,
       cs.relation_type,
       cs.confidence AS paragraph_link_confidence,
       cs.review_status
  FROM case_segment cs
  JOIN meeting_segment s ON s.segment_id = cs.segment_id;

-- Once an embedding model is selected, add a fixed-dimension vector column/table
-- and an HNSW/IVFFlat index. Never mix model dimensions in one vector column.
-- CREATE EXTENSION IF NOT EXISTS vector;
-- ALTER TABLE meeting_segment ADD COLUMN embedding vector(1536);
-- CREATE INDEX idx_segment_embedding ON meeting_segment USING hnsw (embedding vector_cosine_ops);
