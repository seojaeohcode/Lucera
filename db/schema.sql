PRAGMA foreign_keys = ON;

-- SQLite is the runnable MVP database. The logical model is mirrored in
-- db/schema.postgres.sql for the production PostGIS/pgvector deployment.

CREATE TABLE IF NOT EXISTS source_system (
    source_system_id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    base_url TEXT,
    terms_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The 27 local target units: 5 autonomous cities, 17 autonomous counties,
-- and 5 autonomous districts. Parent scopes are stored as metadata rows.
CREATE TABLE IF NOT EXISTS administrative_region (
    region_code TEXT PRIMARY KEY,
    region_name TEXT NOT NULL UNIQUE,
    province TEXT NOT NULL,
    region_group TEXT NOT NULL,
    region_type TEXT NOT NULL,
    parent_region_code TEXT,
    assembly_id TEXT,
    available INTEGER NOT NULL DEFAULT 0 CHECK (available IN (0, 1)),
    aliases_json TEXT NOT NULL DEFAULT '[]',
    source_kind TEXT NOT NULL DEFAULT 'catalog',
    source_reference TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_document (
    document_id TEXT PRIMARY KEY,
    source_system_id TEXT NOT NULL REFERENCES source_system(source_system_id),
    source_record_key TEXT,
    title TEXT NOT NULL,
    document_type TEXT NOT NULL,
    source_url TEXT,
    original_file_url TEXT,
    storage_uri TEXT,
    mime_type TEXT,
    sha256 TEXT,
    file_size_bytes INTEGER,
    published_at TEXT,
    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_policy TEXT NOT NULL DEFAULT 'public',
    processing_status TEXT NOT NULL DEFAULT 'raw',
    raw_payload_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_system_id, source_record_key),
    UNIQUE(sha256)
);

-- A single meeting may have an HWP original, a converted PDF, a browser HTML
-- snapshot, and an OpenDataLoader JSON/text result.  Keep those artifacts
-- normalized so every derived paragraph can be traced back to the exact file
-- and parser run that produced it.
CREATE TABLE IF NOT EXISTS document_artifact (
    artifact_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    artifact_role TEXT NOT NULL CHECK (artifact_role IN ('original_download', 'official_source', 'html_snapshot', 'rendered_pdf', 'opendataloader_json', 'extracted_text')),
    storage_uri TEXT,
    source_url TEXT,
    mime_type TEXT,
    file_name TEXT,
    sha256 TEXT,
    file_size_bytes INTEGER,
    acquisition_method TEXT,
    derived_from_artifact_id TEXT REFERENCES document_artifact(artifact_id) ON DELETE SET NULL,
    parser_name TEXT,
    parser_version TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, artifact_role, sha256)
);

CREATE INDEX IF NOT EXISTS idx_document_artifact_document ON document_artifact(document_id, artifact_role);

CREATE TABLE IF NOT EXISTS ingestion_job (
    ingestion_job_id TEXT PRIMARY KEY,
    source_system_id TEXT REFERENCES source_system(source_system_id),
    job_type TEXT NOT NULL,
    requested_keyword TEXT,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    requested_count INTEGER,
    processed_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meeting (
    meeting_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE REFERENCES source_document(document_id) ON DELETE CASCADE,
    council_level TEXT,
    administrative_region_code TEXT REFERENCES administrative_region(region_code),
    assembly_id TEXT,
    assembly_name TEXT,
    province TEXT,
    city_county TEXT,
    session_number TEXT,
    assembly_number TEXT,
    meeting_order TEXT,
    meeting_type TEXT,
    meeting_title TEXT,
    meeting_date TEXT,
    agenda_text TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS speaker (
    speaker_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT,
    affiliation TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(name, role, affiliation)
);

CREATE TABLE IF NOT EXISTS document_page (
    page_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    text_original TEXT NOT NULL DEFAULT '',
    text_redacted TEXT NOT NULL DEFAULT '',
    raw_text_uri TEXT,
    image_uri TEXT,
    ocr_used INTEGER NOT NULL DEFAULT 0 CHECK (ocr_used IN (0, 1)),
    ocr_confidence REAL,
    parser_name TEXT,
    parser_version TEXT,
    UNIQUE(document_id, page_no)
);

CREATE TABLE IF NOT EXISTS meeting_segment (
    segment_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    page_from INTEGER NOT NULL,
    page_to INTEGER NOT NULL,
    meeting_id TEXT REFERENCES meeting(meeting_id) ON DELETE SET NULL,
    speaker_id TEXT REFERENCES speaker(speaker_id) ON DELETE SET NULL,
    section_title TEXT,
    agenda_no TEXT,
    ordinal INTEGER NOT NULL,
    segment_type TEXT NOT NULL DEFAULT 'paragraph',
    text_original TEXT NOT NULL DEFAULT '',
    text_redacted TEXT NOT NULL DEFAULT '',
    char_start INTEGER,
    char_end INTEGER,
    parse_confidence REAL,
    relevance_status TEXT NOT NULL DEFAULT 'unreviewed',
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(document_id, ordinal)
);

-- Hierarchical evidence layer: meeting_segment is the canonical paragraph /
-- speech-block table; these tables add sentence and keyword granularity.
CREATE TABLE IF NOT EXISTS sentences (
    sentence_id TEXT PRIMARY KEY,
    paragraph_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_order INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    text_redacted TEXT NOT NULL DEFAULT '',
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(paragraph_id, sentence_order)
);

CREATE TABLE IF NOT EXISTS keyword_mentions (
    mention_id TEXT PRIMARY KEY,
    sentence_id TEXT NOT NULL REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    normalized_keyword TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    match_type TEXT NOT NULL,
    keyword_group TEXT,
    problem_category TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(sentence_id, keyword, start_offset, end_offset)
);

CREATE TABLE IF NOT EXISTS administrative_area (
    administrative_area_id TEXT PRIMARY KEY,
    level TEXT NOT NULL CHECK (level IN ('country', 'province', 'city_county', 'eup_myeon', 'ri', 'dong')),
    admin_code TEXT,
    canonical_name TEXT NOT NULL,
    province TEXT,
    city_county TEXT,
    eup_myeon TEXT,
    ri TEXT,
    parent_id TEXT REFERENCES administrative_area(administrative_area_id),
    centroid_lat REAL,
    centroid_lon REAL,
    boundary_source_document_id TEXT REFERENCES source_document(document_id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(level, admin_code, canonical_name)
);

CREATE TABLE IF NOT EXISTS canonical_place (
    place_id TEXT PRIMARY KEY,
    place_type TEXT NOT NULL CHECK (place_type IN ('parcel', 'road_address', 'jibun_address', 'building', 'village', 'ri', 'eup_myeon', 'city_county', 'province', 'unknown')),
    raw_name TEXT,
    normalized_name TEXT,
    road_address TEXT,
    jibun_address TEXT,
    province TEXT,
    city_county TEXT,
    eup_myeon TEXT,
    ri TEXT,
    admin_code TEXT,
    latitude REAL,
    longitude REAL,
    geom_wkt TEXT,
    geo_provider TEXT,
    geo_precision TEXT NOT NULL DEFAULT 'unknown' CHECK (geo_precision IN ('parcel', 'building', 'road_address', 'jibun_address', 'village', 'ri', 'eup_myeon', 'city_county', 'province', 'unknown')),
    geocode_confidence REAL,
    location_status TEXT NOT NULL DEFAULT 'candidate' CHECK (location_status IN ('candidate', 'reviewed', 'confirmed', 'rejected')),
    resolution_method TEXT,
    source_document_id TEXT REFERENCES source_document(document_id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS place_mention (
    mention_id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    surface_form TEXT NOT NULL,
    normalized_form TEXT,
    mention_type TEXT NOT NULL DEFAULT 'unknown',
    context_text TEXT,
    char_start INTEGER,
    char_end INTEGER,
    extraction_run_id TEXT,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS place_resolution_candidate (
    mention_id TEXT NOT NULL REFERENCES place_mention(mention_id) ON DELETE CASCADE,
    place_id TEXT NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    match_method TEXT NOT NULL,
    confidence REAL,
    resolution_reason TEXT,
    is_selected INTEGER NOT NULL DEFAULT 0 CHECK (is_selected IN (0, 1)),
    review_status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (mention_id, place_id)
);

CREATE TABLE IF NOT EXISTS segment_place_link (
    segment_place_link_id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    place_id TEXT NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    mention_id TEXT REFERENCES place_mention(mention_id) ON DELETE SET NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('subject_site', 'nearby', 'same_village', 'same_ri', 'same_eup_myeon', 'same_city_county', 'meeting_institution', 'comparative', 'unknown')),
    distance_m REAL,
    distance_status TEXT NOT NULL DEFAULT 'unknown' CHECK (distance_status IN ('exact', 'approximate', 'unknown')),
    confidence REAL,
    evidence_text TEXT,
    resolution_method TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(segment_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS extraction_run (
    extraction_run_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    prompt_version TEXT,
    validator_version TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    parameters_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS issue_taxonomy (
    taxonomy_id TEXT PRIMARY KEY,
    taxonomy_version TEXT NOT NULL,
    issue_code TEXT NOT NULL,
    parent_code TEXT,
    issue_name TEXT NOT NULL,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE(taxonomy_version, issue_code)
);

CREATE TABLE IF NOT EXISTS segment_issue (
    segment_issue_id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    taxonomy_id TEXT NOT NULL REFERENCES issue_taxonomy(taxonomy_id),
    issue_code TEXT NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'neutral' CHECK (polarity IN ('opposition', 'support', 'neutral', 'mixed', 'unknown')),
    target_type TEXT NOT NULL DEFAULT 'unknown' CHECK (target_type IN ('project', 'policy', 'process', 'company', 'facility', 'unknown')),
    confidence REAL,
    evidence_span TEXT,
    extraction_run_id TEXT REFERENCES extraction_run(extraction_run_id),
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(segment_id, taxonomy_id, issue_code)
);

CREATE TABLE IF NOT EXISTS conflict_case (
    case_id TEXT PRIMARY KEY,
    case_key TEXT NOT NULL UNIQUE,
    case_name TEXT,
    canonical_title TEXT,
    municipality TEXT,
    village TEXT,
    address TEXT,
    project_name TEXT,
    facility_type TEXT,
    summary TEXT,
    case_status TEXT NOT NULL DEFAULT 'unknown',
    started_on TEXT,
    ended_on TEXT,
    representative_place_id TEXT REFERENCES canonical_place(place_id),
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Deterministic quality gate for the inferred case.  This is separate from
-- conflict_case.review_status so every rebuild preserves the score, evidence
-- counts, reason codes, and classifier/gazetteer version used for the decision.
CREATE TABLE IF NOT EXISTS case_review (
    case_id TEXT PRIMARY KEY REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN ('eligible', 'needs_review', 'rejected')),
    quality_score REAL NOT NULL,
    subject_score REAL NOT NULL,
    dispute_score REAL NOT NULL,
    identity_score REAL NOT NULL,
    separation_score REAL NOT NULL,
    evidence_paragraph_count INTEGER NOT NULL DEFAULT 0,
    trigger_sentence_count INTEGER NOT NULL DEFAULT 0,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    review_version TEXT NOT NULL,
    reviewer_id TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    decision_source TEXT NOT NULL DEFAULT 'deterministic_gate' CHECK (decision_source IN ('deterministic_gate', 'human', 'llm_assist')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    episode_key TEXT NOT NULL UNIQUE,
    document_id TEXT NOT NULL REFERENCES source_document(document_id) ON DELETE CASCADE,
    paragraph_start INTEGER NOT NULL,
    paragraph_end INTEGER NOT NULL,
    issue_type TEXT,
    issue_types_json TEXT NOT NULL DEFAULT '[]',
    stance TEXT NOT NULL DEFAULT 'unknown' CHECK (stance IN ('opposition', 'support', 'neutral', 'mixed', 'unknown')),
    procedure_stage TEXT,
    confidence REAL,
    grouping_score REAL,
    grouping_method TEXT NOT NULL DEFAULT 'deterministic_v1',
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS episode_evidence (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_id TEXT REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('trigger_sentence', 'context_before', 'context_after', 'episode_context')),
    link_confidence REAL,
    PRIMARY KEY(episode_id, paragraph_id, sentence_id, evidence_role)
);

CREATE TABLE IF NOT EXISTS case_evidence (
    case_evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    sentence_id TEXT REFERENCES sentences(sentence_id) ON DELETE CASCADE,
    evidence_role TEXT NOT NULL,
    link_confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(case_id, episode_id, paragraph_id, sentence_id, evidence_role)
);

-- A meeting record can expose not only the issue but also the administrative
-- process around it: complaint, inquiry, inspection, consultation, decision,
-- and follow-up. Keep these events tied to their exact evidence paragraph.
CREATE TABLE IF NOT EXISTS case_process_event (
    process_event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    episode_id TEXT REFERENCES episodes(episode_id) ON DELETE CASCADE,
    paragraph_id TEXT REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_date TEXT,
    actor TEXT,
    action_text TEXT NOT NULL,
    outcome TEXT,
    certainty TEXT NOT NULL DEFAULT 'unconfirmed' CHECK (certainty IN ('confirmed', 'inferred', 'unconfirmed')),
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    evidence_text TEXT,
    extraction_method TEXT NOT NULL DEFAULT 'deterministic_process_v1',
    review_status TEXT NOT NULL DEFAULT 'pending',
    data_origin TEXT NOT NULL DEFAULT 'meeting_record' CHECK (data_origin IN ('meeting_record', 'synthetic', 'user_input', 'inferred')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(case_id, paragraph_id, event_type, action_text)
);

CREATE INDEX IF NOT EXISTS idx_case_process_event_case ON case_process_event(case_id, event_date, event_type);
CREATE INDEX IF NOT EXISTS idx_case_process_event_type ON case_process_event(event_type, certainty, review_status);

-- Location inference is a ranked candidate set, not an invented exact site.
-- A case may mention several villages/addresses; retain all of them and mark
-- the best current candidate separately from human confirmation.
CREATE TABLE IF NOT EXISTS case_location_candidate (
    case_location_candidate_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    place_id TEXT NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    inference_method TEXT NOT NULL,
    confidence REAL,
    evidence_episode_id TEXT REFERENCES episodes(episode_id) ON DELETE CASCADE,
    evidence_paragraph_id TEXT REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    evidence_text TEXT,
    is_selected INTEGER NOT NULL DEFAULT 0 CHECK (is_selected IN (0, 1)),
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(case_id, place_id)
);

-- A candidate link is deliberately separate from an accepted case merge.
-- It preserves low-confidence cross-document matches for human/LLM review
-- without contaminating conflict_case with an unverified identity.
CREATE TABLE IF NOT EXISTS case_link_candidate (
    candidate_id TEXT PRIMARY KEY,
    left_case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    right_case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    match_score REAL NOT NULL CHECK (match_score >= 0 AND match_score <= 1),
    matching_features_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (left_case_id < right_case_id),
    UNIQUE(left_case_id, right_case_id)
);

CREATE TABLE IF NOT EXISTS case_segment (
    case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'evidence',
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY(case_id, segment_id)
);

CREATE TABLE IF NOT EXISTS evidence_claim (
    claim_id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES meeting_segment(segment_id) ON DELETE CASCADE,
    case_id TEXT REFERENCES conflict_case(case_id) ON DELETE SET NULL,
    subject_text TEXT,
    predicate TEXT NOT NULL,
    object_text TEXT,
    event_date TEXT,
    certainty TEXT NOT NULL DEFAULT 'unconfirmed' CHECK (certainty IN ('confirmed', 'inferred', 'unconfirmed')),
    evidence_span TEXT,
    extraction_run_id TEXT REFERENCES extraction_run(extraction_run_id),
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS permit_project (
    project_id TEXT PRIMARY KEY,
    source_system_id TEXT REFERENCES source_system(source_system_id),
    source_record_key TEXT,
    facility_name TEXT,
    company_name TEXT,
    capacity_kw REAL,
    permit_date TEXT,
    operation_status TEXT,
    road_address TEXT,
    jibun_address TEXT,
    province TEXT,
    city_county TEXT,
    eup_myeon TEXT,
    ri TEXT,
    latitude REAL,
    longitude REAL,
    location_status TEXT NOT NULL DEFAULT 'unknown',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_system_id, source_record_key)
);

-- Objective siting checks are kept separate from generated explanations.
-- Rules may be synthetic fixtures today and official, versioned rules later.
CREATE TABLE IF NOT EXISTS siting_rule (
    rule_id TEXT PRIMARY KEY,
    region_code TEXT,
    reference_object TEXT NOT NULL,
    operator TEXT NOT NULL DEFAULT 'gte' CHECK (operator IN ('gte', 'gt', 'lte', 'lt', 'eq', 'exists')),
    threshold_value REAL,
    unit TEXT,
    rule_name TEXT NOT NULL,
    rule_description TEXT,
    source_title TEXT,
    source_article TEXT,
    valid_from TEXT,
    valid_to TEXT,
    severity TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    data_origin TEXT NOT NULL DEFAULT 'official' CHECK (data_origin IN ('official', 'synthetic', 'user_input')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_siting_rule_region ON siting_rule(region_code, reference_object, active, valid_from);

-- User-entered project intake is intentionally separate from permit_project,
-- which stores external/public permit records.
CREATE TABLE IF NOT EXISTS project_intake (
    project_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL UNIQUE,
    project_status TEXT NOT NULL DEFAULT 'draft' CHECK (project_status IN ('draft', 'submitted', 'under_review', 'completed', 'archived')),
    current_revision_no INTEGER NOT NULL DEFAULT 0,
    input_schema_version TEXT NOT NULL DEFAULT 'project-intake-v2',
    intake_channel TEXT NOT NULL DEFAULT 'web_form' CHECK (intake_channel IN ('web_form', 'api', 'import', 'draft_save')),
    created_by TEXT,
    updated_by TEXT,
    last_submitted_at TEXT,
    completed_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The field catalog is the contract between the web form, attachment
-- extraction, project_fact provenance, and the six-stage workflow.  Keeping
-- it in the database prevents the input schema from drifting away from the
-- screen and makes required/optional fields inspectable at runtime.
CREATE TABLE IF NOT EXISTS project_field_definition (
    field_name TEXT PRIMARY KEY,
    section_code TEXT NOT NULL CHECK (section_code IN ('business', 'applicant', 'site', 'equipment', 'finance', 'schedule', 'grid', 'permit', 'resident')),
    display_name TEXT NOT NULL,
    field_type TEXT NOT NULL CHECK (field_type IN ('text', 'numeric', 'date', 'boolean')),
    unit TEXT,
    stage_code TEXT,
    required_flag INTEGER NOT NULL DEFAULT 0 CHECK (required_flag IN (0, 1)),
    source_policy TEXT NOT NULL DEFAULT 'user_or_attachment' CHECK (source_policy IN ('user_input', 'attachment', 'user_or_attachment', 'derived')),
    validation_rules_json TEXT NOT NULL DEFAULT '{}',
    display_order INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    description TEXT,
    UNIQUE(section_code, display_order)
);

CREATE TABLE IF NOT EXISTS project_intake_submission (
    submission_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'project-intake-v2',
    submission_channel TEXT NOT NULL DEFAULT 'web_form' CHECK (submission_channel IN ('web_form', 'api', 'import', 'draft_save')),
    submitted_by TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    payload_sha256 TEXT,
    validation_status TEXT NOT NULL DEFAULT 'valid' CHECK (validation_status IN ('valid', 'valid_with_warnings', 'invalid')),
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    validated_at TEXT,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, revision_no)
);

CREATE TABLE IF NOT EXISTS project_application (
    application_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    project_name TEXT NOT NULL,
    business_type TEXT,
    permit_type TEXT,
    applicant_name TEXT,
    applicant_type TEXT,
    corporate_name TEXT,
    contractor_name TEXT,
    source_submission_id TEXT REFERENCES project_intake_submission(submission_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('draft', 'submitted', 'under_review', 'approved', 'rejected', 'superseded')),
    validation_status TEXT NOT NULL DEFAULT 'valid',
    validation_errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, revision_no)
);

CREATE TABLE IF NOT EXISTS project_site (
    site_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    site_address TEXT NOT NULL,
    site_address_normalized TEXT,
    lot_number TEXT,
    land_category TEXT,
    building_address TEXT,
    building_address_normalized TEXT,
    building_use TEXT,
    site_address_type TEXT NOT NULL DEFAULT 'unknown' CHECK (site_address_type IN ('road', 'jibun', 'mixed', 'administrative_only', 'unknown')),
    site_resolution_status TEXT NOT NULL DEFAULT 'unresolved' CHECK (site_resolution_status IN ('unresolved', 'parsed', 'candidate', 'resolved', 'reviewed')),
    site_resolution_method TEXT,
    site_resolution_confidence REAL CHECK (site_resolution_confidence IS NULL OR (site_resolution_confidence >= 0 AND site_resolution_confidence <= 1)),
    building_address_type TEXT NOT NULL DEFAULT 'unknown' CHECK (building_address_type IN ('road', 'jibun', 'mixed', 'administrative_only', 'unknown')),
    building_resolution_status TEXT NOT NULL DEFAULT 'unresolved' CHECK (building_resolution_status IN ('unresolved', 'parsed', 'candidate', 'resolved', 'reviewed')),
    building_resolution_method TEXT,
    building_resolution_confidence REAL CHECK (building_resolution_confidence IS NULL OR (building_resolution_confidence >= 0 AND building_resolution_confidence <= 1)),
    site_place_id TEXT REFERENCES canonical_place(place_id),
    building_place_id TEXT REFERENCES canonical_place(place_id),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_equipment (
    equipment_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    installed_capacity_kw REAL,
    module_count INTEGER,
    module_capacity_w REAL,
    inverter_count INTEGER,
    inverter_capacity_kva REAL,
    installation_height_m REAL,
    installation_area_sqm REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (installed_capacity_kw IS NULL OR installed_capacity_kw >= 0),
    CHECK (module_count IS NULL OR module_count >= 0),
    CHECK (module_capacity_w IS NULL OR module_capacity_w >= 0),
    CHECK (inverter_count IS NULL OR inverter_count >= 0),
    CHECK (inverter_capacity_kva IS NULL OR inverter_capacity_kva >= 0),
    CHECK (installation_height_m IS NULL OR installation_height_m >= 0),
    CHECK (installation_area_sqm IS NULL OR installation_area_sqm >= 0)
);

CREATE TABLE IF NOT EXISTS project_finance (
    finance_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    total_project_cost_krw REAL,
    construction_cost_per_kw REAL,
    annual_generation_mwh REAL,
    annual_transmission_mwh REAL,
    lease_fee_krw REAL,
    resident_revenue_share REAL CHECK (resident_revenue_share IS NULL OR (resident_revenue_share >= 0 AND resident_revenue_share <= 100)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK (total_project_cost_krw IS NULL OR total_project_cost_krw >= 0),
    CHECK (construction_cost_per_kw IS NULL OR construction_cost_per_kw >= 0),
    CHECK (annual_generation_mwh IS NULL OR annual_generation_mwh >= 0),
    CHECK (annual_transmission_mwh IS NULL OR annual_transmission_mwh >= 0),
    CHECK (lease_fee_krw IS NULL OR lease_fee_krw >= 0)
);

CREATE TABLE IF NOT EXISTS project_schedule (
    schedule_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    permit_application_date TEXT,
    permit_date TEXT,
    construction_start_date TEXT,
    expected_completion_date TEXT,
    business_start_date TEXT,
    operation_period_years REAL,
    schedule_status TEXT NOT NULL DEFAULT 'unknown' CHECK (schedule_status IN ('unknown', 'planned', 'in_progress', 'completed', 'inconsistent')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_grid (
    grid_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    grid_connection_point TEXT,
    connection_voltage_v REAL,
    transformer_info TEXT,
    power_purchase_method TEXT,
    connection_status TEXT NOT NULL DEFAULT 'unknown' CHECK (connection_status IN ('unknown', 'planned', 'requested', 'approved', 'connected', 'rejected')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_permit_checklist (
    permit_checklist_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    development_permit_required INTEGER CHECK (development_permit_required IS NULL OR development_permit_required IN (0, 1)),
    urban_management_plan_required INTEGER CHECK (urban_management_plan_required IS NULL OR urban_management_plan_required IN (0, 1)),
    construction_plan_report INTEGER CHECK (construction_plan_report IS NULL OR construction_plan_report IN (0, 1)),
    environmental_assessment_required INTEGER CHECK (environmental_assessment_required IS NULL OR environmental_assessment_required IN (0, 1)),
    structural_safety_review INTEGER CHECK (structural_safety_review IS NULL OR structural_safety_review IN (0, 1)),
    checklist_status TEXT NOT NULL DEFAULT 'not_started' CHECK (checklist_status IN ('not_started', 'in_progress', 'completed', 'needs_review')),
    checked_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_resident_risk (
    resident_risk_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL UNIQUE REFERENCES project_application(application_id) ON DELETE CASCADE,
    resident_consent_required INTEGER CHECK (resident_consent_required IS NULL OR resident_consent_required IN (0, 1)),
    construction_consent INTEGER CHECK (construction_consent IS NULL OR construction_consent IN (0, 1)),
    complaint_occurred INTEGER CHECK (complaint_occurred IS NULL OR complaint_occurred IN (0, 1)),
    complaint_stop_commitment INTEGER CHECK (complaint_stop_commitment IS NULL OR complaint_stop_commitment IN (0, 1)),
    removal_commitment INTEGER CHECK (removal_commitment IS NULL OR removal_commitment IN (0, 1)),
    complaint_type TEXT,
    risk_status TEXT NOT NULL DEFAULT 'unknown' CHECK (risk_status IN ('unknown', 'none_reported', 'reported', 'under_consultation', 'resolved', 'needs_review')),
    complaint_source TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_attachment (
    attachment_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    document_type TEXT NOT NULL CHECK (document_type IN ('business_plan', 'profit_loss_plan', 'funding_plan', 'land_use_consent', 'building_register', 'land_register', 'cadastral_map', 'land_use_plan', 'structural_safety_certificate')),
    file_name TEXT,
    storage_uri TEXT,
    mime_type TEXT,
    sha256 TEXT,
    file_size_bytes INTEGER,
    extraction_status TEXT NOT NULL DEFAULT 'not_started' CHECK (extraction_status IN ('not_started', 'queued', 'extracted', 'failed', 'reviewed')),
    source_document_id TEXT REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_url TEXT,
    document_date TEXT,
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    is_required INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),
    extraction_started_at TEXT,
    extracted_at TEXT,
    extractor_name TEXT,
    extractor_version TEXT,
    extraction_error TEXT,
    content_text_uri TEXT,
    text_sha256 TEXT,
    ocr_used INTEGER CHECK (ocr_used IS NULL OR ocr_used IN (0, 1)),
    uploaded_by TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(application_id, document_type, sha256)
);

CREATE TABLE IF NOT EXISTS project_fact (
    fact_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('text', 'numeric', 'date', 'boolean', 'json')),
    value_text TEXT,
    value_numeric REAL,
    value_date TEXT,
    value_boolean INTEGER CHECK (value_boolean IS NULL OR value_boolean IN (0, 1)),
    value_json TEXT,
    unit TEXT,
    source_kind TEXT NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field TEXT,
    source_attachment_id TEXT REFERENCES project_attachment(attachment_id) ON DELETE SET NULL,
    source_artifact_id TEXT REFERENCES document_artifact(artifact_id) ON DELETE SET NULL,
    source_document_id TEXT REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_paragraph_id TEXT REFERENCES meeting_segment(segment_id) ON DELETE SET NULL,
    source_page INTEGER,
    source_char_start INTEGER,
    source_char_end INTEGER,
    source_excerpt TEXT,
    extraction_method TEXT NOT NULL DEFAULT 'form_input',
    extraction_model TEXT,
    extraction_version TEXT,
    confidence REAL,
    fact_status TEXT NOT NULL DEFAULT 'active' CHECK (fact_status IN ('active', 'superseded', 'rejected')),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'accepted', 'rejected', 'needs_review')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS project_stage (
    stage_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    stage_code TEXT NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    stage_order INTEGER NOT NULL,
    stage_status TEXT NOT NULL DEFAULT 'unknown' CHECK (stage_status IN ('unknown', 'required', 'planned', 'in_progress', 'completed', 'reported', 'not_applicable')),
    planned_date TEXT,
    actual_date TEXT,
    required_flag INTEGER CHECK (required_flag IS NULL OR required_flag IN (0, 1)),
    case_id TEXT REFERENCES conflict_case(case_id) ON DELETE SET NULL,
    notes TEXT,
    source_kind TEXT NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field TEXT,
    source_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    last_evaluated_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(application_id, stage_code)
);

-- A stage row is the current workflow snapshot.  Events preserve the
-- chronological audit trail behind that snapshot and keep provenance at
-- the same granularity as project facts.
CREATE TABLE IF NOT EXISTS project_stage_event (
    stage_event_id TEXT PRIMARY KEY,
    stage_id TEXT NOT NULL REFERENCES project_stage(stage_id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    stage_code TEXT NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    event_type TEXT NOT NULL,
    event_status TEXT NOT NULL DEFAULT 'reported' CHECK (event_status IN ('reported', 'planned', 'completed', 'cancelled', 'unknown')),
    event_date TEXT,
    title TEXT,
    description TEXT,
    source_kind TEXT NOT NULL DEFAULT 'user_input' CHECK (source_kind IN ('user_input', 'attachment', 'source_document', 'inferred')),
    source_field TEXT,
    source_fact_id TEXT REFERENCES project_fact(fact_id) ON DELETE SET NULL,
    source_attachment_id TEXT REFERENCES project_attachment(attachment_id) ON DELETE SET NULL,
    source_document_id TEXT REFERENCES source_document(document_id) ON DELETE SET NULL,
    source_page INTEGER,
    source_excerpt TEXT,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'accepted', 'rejected', 'needs_review')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_location_link (
    project_location_link_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    place_id TEXT NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('subject_site', 'building_site', 'lot', 'grid_connection_point')),
    raw_query TEXT,
    candidate_rank INTEGER,
    resolution_method TEXT,
    geo_provider TEXT,
    resolved_at TEXT,
    confidence REAL,
    distance_status TEXT NOT NULL DEFAULT 'unknown',
    review_status TEXT NOT NULL DEFAULT 'pending',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(application_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS project_case_link (
    project_case_link_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_intake(project_id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES project_application(application_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES conflict_case(case_id) ON DELETE CASCADE,
    stage_code TEXT NOT NULL CHECK (stage_code IN ('application', 'power_generation_permit', 'development_environment_review', 'resident_consultation_complaint', 'construction_completion', 'grid_connection_operation')),
    relation_type TEXT NOT NULL DEFAULT 'historical_comparable',
    match_score REAL NOT NULL CHECK (match_score >= 0 AND match_score <= 1),
    matching_features_json TEXT NOT NULL DEFAULT '{}',
    match_method TEXT NOT NULL DEFAULT 'deterministic_v1',
    location_match_type TEXT,
    review_reason TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(application_id, case_id, stage_code, relation_type)
);

CREATE TABLE IF NOT EXISTS project_place_link (
    project_id TEXT NOT NULL REFERENCES permit_project(project_id) ON DELETE CASCADE,
    place_id TEXT NOT NULL REFERENCES canonical_place(place_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'located_at',
    confidence REAL,
    evidence TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY(project_id, place_id, relation_type)
);

CREATE TABLE IF NOT EXISTS address_lookup (
    address_lookup_id TEXT PRIMARY KEY,
    raw_query TEXT NOT NULL,
    normalized_query TEXT,
    provider TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    response_status TEXT,
    response_json TEXT NOT NULL DEFAULT '{}',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    selected_place_id TEXT REFERENCES canonical_place(place_id),
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    UNIQUE(raw_query, provider)
);

CREATE TABLE IF NOT EXISTS search_request (
    search_request_id TEXT PRIMARY KEY,
    raw_address TEXT NOT NULL,
    normalized_address TEXT,
    province TEXT,
    city_county TEXT,
    eup_myeon TEXT,
    ri TEXT,
    latitude REAL,
    longitude REAL,
    geocode_status TEXT NOT NULL DEFAULT 'not_requested',
    radius_m REAL NOT NULL DEFAULT 5000,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    issue_codes_json TEXT NOT NULL DEFAULT '[]',
    from_date TEXT,
    limit_count INTEGER NOT NULL DEFAULT 20,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS review_task (
    review_task_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'open',
    assigned_to TEXT,
    reviewed_at TEXT,
    decision TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(target_type, target_id, reason_code)
);

CREATE TABLE IF NOT EXISTS embedding (
    embedding_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(target_type, target_id, embedding_model, embedding_version)
);

CREATE INDEX IF NOT EXISTS idx_document_source_key ON source_document(source_system_id, source_record_key);
CREATE INDEX IF NOT EXISTS idx_document_published_at ON source_document(published_at);
CREATE INDEX IF NOT EXISTS idx_segment_document_page ON meeting_segment(document_id, page_from, page_to);
CREATE INDEX IF NOT EXISTS idx_sentence_paragraph ON sentences(paragraph_id, sentence_order);
CREATE INDEX IF NOT EXISTS idx_keyword_mention ON keyword_mentions(normalized_keyword, match_type);
CREATE INDEX IF NOT EXISTS idx_segment_review ON meeting_segment(review_status, relevance_status);
CREATE INDEX IF NOT EXISTS idx_place_admin ON canonical_place(province, city_county, eup_myeon, ri);
CREATE INDEX IF NOT EXISTS idx_place_coordinates ON canonical_place(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_segment_place ON segment_place_link(place_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_segment_issue ON segment_issue(issue_code, polarity, review_status);
CREATE INDEX IF NOT EXISTS idx_meeting_region_date ON meeting(province, city_county, meeting_date);
CREATE INDEX IF NOT EXISTS idx_claim_predicate ON evidence_claim(predicate, certainty);
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

-- FTS5 provides a fast local replacement for pg_trgm during the MVP. It stores
-- only the redacted search text and joins back to the evidence tables by ID.
CREATE VIRTUAL TABLE IF NOT EXISTS meeting_segment_fts USING fts5(
    segment_id UNINDEXED,
    title,
    text_redacted,
    issue_text,
    tokenize='unicode61'
);

-- Read models for the primary product hierarchy:
-- document -> conflict case -> the case's paragraphs.
-- These are views, not duplicated tables, so regenerated evidence cannot leave
-- a stale document/case relationship behind.
CREATE VIEW IF NOT EXISTS document_cases AS
SELECT e.document_id,
       ce.case_id,
       COALESCE(c.canonical_title, c.case_name) AS canonical_title,
       c.municipality,
       c.village,
       c.address,
       c.project_name,
       c.facility_type,
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

CREATE VIEW IF NOT EXISTS case_paragraphs AS
SELECT cs.case_id,
       s.document_id,
       cs.segment_id AS paragraph_id,
       s.ordinal AS paragraph_order,
       s.page_from,
       s.page_to,
       s.segment_type,
       s.speaker_id,
       s.text_original,
       s.text_redacted,
       cs.relation_type,
       cs.confidence AS paragraph_link_confidence,
       cs.review_status
  FROM case_segment cs
  JOIN meeting_segment s ON s.segment_id = cs.segment_id;
