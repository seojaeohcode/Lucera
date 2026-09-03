"""Canonical filesystem layout for the Lucera project.

Keeping paths in one module prevents collection, ingestion, validation, and
the local API from silently writing to different copies of the dataset.
"""

from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"

# Canonical application database. Historical databases live below snapshots.
DB_DIR = DATA_DIR / "db"
DATABASE_PATH = DB_DIR / "lucera_minutes.sqlite3"
DB_SNAPSHOT_DIR = DB_DIR / "snapshots"
DB_BACKUP_DIR = DB_DIR / "backups"

# Source and derived meeting-record artifacts.
DATASET_DIR = DATA_DIR / "dataset"
MINUTES_DIR = DATASET_DIR / "minutes"
MINUTES_ORIGINAL_DIR = MINUTES_DIR / "original"
MINUTES_NORMALIZED_DIR = MINUTES_DIR / "normalized"
MINUTES_EXTRACTED_DIR = MINUTES_DIR / "extracted"
MINUTES_REJECTED_DIR = MINUTES_DIR / "rejected"
MINUTES_MANIFEST_DIR = MINUTES_DIR / "manifests"
MINUTES_REPORT_DIR = MINUTES_DIR / "reports"
MINUTES_QA_DIR = MINUTES_DIR / "qa"

API_JSON_DIR = MINUTES_ORIGINAL_DIR / "api_json"
API_LISTINGS_DIR = MINUTES_ORIGINAL_DIR / "api_listings"
HTML_DIR = MINUTES_ORIGINAL_DIR / "html"
HWP_DIR = MINUTES_ORIGINAL_DIR / "hwp"
PDF_ORIGINAL_DIR = MINUTES_ORIGINAL_DIR / "pdf"
HWP_PDF_DIR = MINUTES_NORMALIZED_DIR / "pdf_from_hwp"
HTML_PDF_DIR = MINUTES_NORMALIZED_DIR / "pdf_from_html"
OPENDATALOADER_DIR = MINUTES_EXTRACTED_DIR / "opendataloader"
OPENDATALOADER_EXPERIMENT_DIR = MINUTES_EXTRACTED_DIR / "opendataloader_experiment"

# Supporting material and generated deliverables.
REFERENCE_DIR = DATA_DIR / "reference"
GAZETTEER_DIR = REFERENCE_DIR / "gazetteer"
SOURCE_MATERIALS_DIR = REFERENCE_DIR / "source_materials"
PUBLIC_API_ARCHIVE_DIR = REFERENCE_DIR / "public_api"
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
TEMP_DIR = DATA_DIR / "work" / "temporary"


def ensure_layout() -> None:
    """Create the non-database directories used by the application."""

    for path in (
        DB_DIR,
        DB_SNAPSHOT_DIR,
        DB_BACKUP_DIR,
        API_JSON_DIR,
        API_LISTINGS_DIR,
        HTML_DIR,
        HWP_DIR,
        PDF_ORIGINAL_DIR,
        HWP_PDF_DIR,
        HTML_PDF_DIR,
        OPENDATALOADER_DIR,
        OPENDATALOADER_EXPERIMENT_DIR,
        MINUTES_REJECTED_DIR,
        MINUTES_MANIFEST_DIR,
        MINUTES_REPORT_DIR,
        MINUTES_QA_DIR,
        GAZETTEER_DIR,
        SOURCE_MATERIALS_DIR,
        PUBLIC_API_ARCHIVE_DIR,
        ARTIFACTS_DIR,
        TEMP_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
