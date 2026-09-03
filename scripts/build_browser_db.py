from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.db import LuceraDB, stable_id
from lucera.extract import extract_places, parse_speaker, redact_sensitive
from lucera.keywords import classify_segment
from lucera.paths import (
    DATABASE_PATH,
    HWP_DIR,
    HWP_PDF_DIR,
    HTML_DIR,
    HTML_PDF_DIR,
    MINUTES_MANIFEST_DIR,
    MINUTES_REPORT_DIR,
    MINUTES_DIR,
    OPENDATALOADER_DIR,
    PDF_ORIGINAL_DIR,
)
from lucera.regions import region_catalog
from lucera.review import rebuild_case_reviews


DATA_ROOT = MINUTES_DIR
RAW_ROOT = HWP_DIR
HWP_PDF_ROOT = HWP_PDF_DIR
HTML_PDF_ROOT = HTML_PDF_DIR
ORIGINAL_PDF_ROOT = PDF_ORIGINAL_DIR
ORIGINAL_MANIFEST_PATH = MINUTES_MANIFEST_DIR / "pdf_original_manifest.json"
OD_ROOT = OPENDATALOADER_DIR
DB_PATH = DATABASE_PATH
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_candidates() -> dict[str, dict[str, Any]]:
    payload = json.loads((MINUTES_MANIFEST_DIR / "browser_candidates.json").read_text(encoding="utf-8"))
    by_docid: dict[str, dict[str, Any]] = {}
    for code, region in payload.items():
        for item in region.get("items", []):
            by_docid[item["docid"]] = {**item, "region_code": code, "region_name": region.get("region_name")}
    return by_docid


def load_browser_manifests() -> dict[str, dict[str, Any]]:
    by_docid: dict[str, dict[str, Any]] = {}
    for manifest in list(MINUTES_MANIFEST_DIR.glob("manifest_*.json")) + list(MINUTES_MANIFEST_DIR.glob("html_manifest_*.json")) + list(MINUTES_MANIFEST_DIR.glob("html_missing_manifest_*.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("items", []):
            if item.get("docid"):
                by_docid[item["docid"]] = item
    return by_docid


def load_original_pdf_manifest() -> dict[str, dict[str, Any]]:
    if not ORIGINAL_MANIFEST_PATH.exists():
        return {}
    try:
        payload = json.loads(ORIGINAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    by_docid: dict[str, dict[str, Any]] = {}
    for item in payload.get("items", []):
        filename = item.get("file")
        if filename:
            by_docid[Path(filename).stem] = item
    return by_docid


def region_info(code: str | None) -> dict[str, Any]:
    if not code:
        return {"region_code": None, "name": "", "province": "", "kind": "unknown"}
    for row in region_catalog():
        if row["region_code"] == code:
            return row
    return {"region_code": code, "name": code, "province": "전라남도", "kind": "unknown"}


def java_env() -> dict[str, str]:
    env = os.environ.copy()
    java_dir = Path(r"C:\Users\seoco\AppData\Local\Programs\Microsoft\jdk-17.0.10.7-hotspot\bin")
    if (java_dir / "java.exe").exists():
        env["PATH"] = str(java_dir) + os.pathsep + env.get("PATH", "")
    return env


def ensure_open_data_loader(pdf_path: Path, output_dir: Path) -> tuple[Path | None, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{pdf_path.stem}.json"
    source_hash_path = output_dir / f"{pdf_path.stem}.source.sha256"
    pdf_hash = sha256(pdf_path)
    if (
        json_path.exists()
        and json_path.stat().st_size > 100
        and source_hash_path.exists()
        and source_hash_path.read_text(encoding="utf-8").strip() == pdf_hash
    ):
        return json_path, "opendataloader-2.5.7"
    executable = shutil.which("opendataloader-pdf")
    if not executable:
        candidate = Path(r"C:\Users\seoco\AppData\Roaming\Python\Python313\Scripts\opendataloader-pdf.exe")
        executable = str(candidate) if candidate.exists() else None
    if not executable:
        return None, "fallback-no-opendataloader"
    command = [executable, str(pdf_path), "-o", str(output_dir), "-f", "json,text", "--image-output", "off", "--keep-line-breaks", "--quiet"]
    completed = subprocess.run(command, env=java_env(), capture_output=True, text=True, timeout=180)
    if completed.returncode != 0 or not json_path.exists():
        return None, "fallback-opendataloader-error"
    source_hash_path.write_text(pdf_hash, encoding="utf-8")
    return json_path, "opendataloader-2.5.7"


def fallback_elements(pdf_path: Path) -> tuple[list[dict[str, Any]], int]:
    import fitz

    doc = fitz.open(pdf_path)
    elements: list[dict[str, Any]] = []
    for page_no, page in enumerate(doc, 1):
        elements.append({"page number": page_no, "content": page.get_text("text"), "type": "paragraph"})
    page_count = len(doc)
    doc.close()
    return elements, page_count


def load_elements(pdf_path: Path, json_path: Path | None) -> tuple[list[dict[str, Any]], int, str]:
    if json_path:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        flattened: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            # OpenDataLoader may wrap a page in a `text block` and put the
            # actual paragraphs several levels below it.  Reading only the
            # top-level kids silently produced page rows with zero paragraphs.
            if node.get("content") not in (None, ""):
                flattened.append(node)
            for key in ("kids", "list items"):
                children = node.get(key)
                if isinstance(children, list):
                    for child in children:
                        walk(child)

        for item in payload.get("kids", []):
            walk(item)
        page_count = int(payload.get("number of pages") or 0)
        if flattened:
            return flattened, page_count, "opendataloader-2.5.7"
        # Keep a usable text layer when a valid OpenDataLoader JSON has no
        # structural content (for example, an image-only or unusual PDF).
        elements, fallback_page_count = fallback_elements(pdf_path)
        return elements, max(page_count, fallback_page_count), "pymupdf-fallback"
    elements, page_count = fallback_elements(pdf_path)
    return elements, page_count, "pymupdf-fallback"


def clean_element_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    text = re.sub(r"[\t\r\f\v ]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def build_pages(elements: list[dict[str, Any]], page_count: int, parser_name: str, raw_uri: str) -> list[dict[str, Any]]:
    by_page: dict[int, list[str]] = {}
    for element in elements:
        page_no = int(element.get("page number") or 1)
        content = clean_element_text(element.get("content"))
        if content:
            by_page.setdefault(page_no, []).append(content)
    count = max(page_count, max(by_page, default=1))
    return [{"text_original": "\n".join(by_page.get(page_no, [])), "text_redacted": redact_sensitive("\n".join(by_page.get(page_no, []))), "raw_text_uri": raw_uri, "ocr_used": False, "parser_name": parser_name, "parser_version": "1"} for page_no in range(1, count + 1)]


def make_bundle(
    pdf_path: Path,
    json_path: Path | None,
    loader: str,
    candidates: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
    originals: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    docid = pdf_path.stem
    candidate = candidates.get(docid, {})
    original = originals.get(docid, {})
    is_original_pdf = bool(original) or pdf_path.parent == ORIGINAL_PDF_ROOT
    region_code = original.get("region_code") or candidate.get("region_code")
    if not region_code and pdf_path.parent.name.startswith("region_"):
        region_code = pdf_path.parent.name.removeprefix("region_")
    region = region_info(region_code)
    title = original.get("title") or candidate.get("title")
    if not title:
        title = f"{region.get('name', '')} 지방의회 회의록 {docid}".strip()
    meeting_date = original.get("meeting_date") or candidate.get("meeting_date")
    acquisition = "browser_official_pdf" if is_original_pdf else ("browser_portal_hwp" if pdf_path.parent.parent.name == "converted" else "browser_portal_html_print")
    raw_path: Path | None = None
    html_path: Path | None = None
    if not is_original_pdf and region_code:
        raw_candidates = [
            RAW_ROOT / f"region_{region_code}" / f"{docid}.hwp",
            RAW_ROOT / f"region_{region_code}" / f"{docid}.hwpx",
        ]
        raw_path = next((path for path in raw_candidates if path.exists()), raw_candidates[0])
        html_path = HTML_DIR / f"region_{region_code}" / f"{docid}.html"
    manifest = {**manifests.get(docid, {}), **original}
    source_url = original.get("source_page_url") or original.get("source_url") or manifest.get("viewer_url") or f"https://clik.nanet.go.kr/minutes/viewer.do?collection=minutes&DOCID={docid}"
    elements, page_count, loader_name = load_elements(pdf_path, json_path)
    pages = build_pages(elements, page_count, loader_name, str(json_path or pdf_path))
    context = " ".join(
        part
        for part in (
            title,
            original.get("assembly_name"),
            original.get("province"),
            region.get("province"),
            region.get("name"),
        )
        if part
    ).strip()
    segments: list[dict[str, Any]] = []
    running = 0
    for element in elements:
        text = clean_element_text(element.get("content"))
        if len(text) < 2:
            continue
        page_no = int(element.get("page number") or 1)
        # Keep issue labels sentence-grounded even though the DB stores the
        # surrounding paragraph.  This prevents a policy/support sentence in
        # the same paragraph from inheriting a conflict label from another
        # sentence.
        classification = classify_segment(text, context)
        speaker_name, speaker_role = parse_speaker(text)
        segments.append({
            "text_original": text,
            "text_redacted": redact_sensitive(text),
            "page_from": page_no,
            "page_to": page_no,
            "segment_type": "speech" if speaker_name else ("heading" if element.get("type") == "heading" else "paragraph"),
            "speaker_name": speaker_name,
            "speaker_role": speaker_role,
            "char_start": running,
            "char_end": running + len(text),
            "parse_confidence": 0.95 if loader_name.startswith("opendataloader") else 0.8,
            "issues": classification["issues"],
            "places": extract_places(text, context),
            "relevant": bool(classification["relevant"]),
            "metadata": {
                "source_element": {"id": element.get("id"), "type": element.get("type"), "pdfua_tag": element.get("pdfua_tag"), "bounding_box": element.get("bounding box"), "heading_level": element.get("heading level")},
                "acquisition_method": acquisition,
                "source_file": str(pdf_path),
                "open_data_loader": loader_name,
                "source_page_url": original.get("source_page_url"),
                "download_url": original.get("download_url"),
                "keyword_classifier": {"version": "precision-v2", "solar_related": classification["solar_related"], "solar_anchor_hits": classification["solar_anchor_hits"], "standalone_high_precision_hits": classification["standalone_high_precision_hits"], "matched_issue_terms": classification["matched_issue_terms"], "admin_support_hits": classification["admin_support_hits"], "problem_categories": classification["problem_categories"]},
            },
        })
        running += len(text) + 1
    source_metadata = {
        "provider": original.get("provider") or "국회도서관 지방의정포털 브라우저 수집",
        "docid": docid,
        "retrieval_mode": acquisition,
        "acquisition_method": acquisition,
        "source_pdf_path": str(pdf_path),
        "source_hwp_path": str(raw_path) if raw_path and raw_path.exists() else None,
        "source_html_path": str(html_path) if html_path and html_path.exists() else None,
        "open_data_loader": loader_name,
        "open_data_loader_json": str(json_path) if json_path else None,
        "browser_manifest": manifest,
        "official_pdf_manifest": original or None,
        "source_page_url": original.get("source_page_url"),
        "download_url": original.get("download_url"),
        "region_code": region_code,
        "council_level": original.get("council_level"),
        "page_count": page_count,
    }
    document_id = stable_id("document", "browser_minutes", docid)
    artifacts: list[dict[str, Any]] = []

    def add_file_artifact(
        role: str,
        path: Path,
        method: str,
        parser: str | None = None,
        version: str | None = None,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        artifact_sha = sha256(path)
        artifact_id = stable_id("artifact", document_id, role, artifact_sha, str(path))
        artifact = {"artifact_id": artifact_id, "artifact_role": role, "storage_uri": str(path), "mime_type": "application/pdf" if path.suffix.lower() == ".pdf" else "text/html" if path.suffix.lower() == ".html" else "text/plain" if path.suffix.lower() == ".txt" else "application/octet-stream", "file_name": path.name, "sha256": artifact_sha, "file_size_bytes": path.stat().st_size, "acquisition_method": method, "parser_name": parser, "parser_version": version}
        if source_url:
            artifact["source_url"] = source_url
        if metadata:
            artifact["metadata"] = metadata
        artifacts.append(artifact)
        return artifact_id

    if is_original_pdf:
        raw_artifact_id = None
        html_artifact_id = None
        pdf_artifact_id = add_file_artifact(
            "original_download",
            pdf_path,
            acquisition,
            "browser-download",
            "1.0",
            source_url=original.get("download_url") or source_url,
            metadata={"source_page_url": original.get("source_page_url"), "download_url": original.get("download_url"), "official_pdf": True},
        )
    else:
        raw_artifact_id = add_file_artifact("original_download", raw_path, "browser_portal_hwp", "browser-download") if raw_path and raw_path.exists() else None
        html_artifact_id = add_file_artifact("html_snapshot", html_path, "browser_portal_html", "browser-dom-snapshot", "1.0") if html_path and html_path.exists() else None
        pdf_artifact_id = add_file_artifact("rendered_pdf", pdf_path, acquisition, "hancom-office" if acquisition == "browser_portal_hwp" else "browser-html-print", "1.0")
        if raw_artifact_id:
            artifacts[-1]["derived_from_artifact_id"] = raw_artifact_id
        elif html_artifact_id:
            artifacts[-1]["derived_from_artifact_id"] = html_artifact_id
    if json_path and json_path.exists():
        json_artifact_id = add_file_artifact("opendataloader_json", json_path, "local-analysis", "opendataloader-pdf", "2.5.7")
        artifacts[-1]["derived_from_artifact_id"] = pdf_artifact_id
        txt_path = json_path.with_suffix(".txt")
        if txt_path.exists():
            add_file_artifact("extracted_text", txt_path, "local-analysis", "opendataloader-pdf", "2.5.7")
            artifacts[-1]["derived_from_artifact_id"] = json_artifact_id
    if candidate.get("original_file_url") and not is_original_pdf:
        artifacts.append({"artifact_id": stable_id("artifact", document_id, "official_source", candidate["original_file_url"]), "artifact_role": "official_source", "source_url": candidate["original_file_url"], "mime_type": "application/octet-stream", "acquisition_method": "api_provenance", "metadata": {"note": "API가 제공한 의회 공식 원문 주소"}})
    assembly_name = original.get("assembly_name") or (f"{region.get('name', '')}의회" if region_code else "농어업·농어촌특별위원회")
    province = original.get("province") or region.get("province") or None
    city_county = original.get("city_county") or (region.get("name") if region_code else None)
    assembly_id = original.get("assembly_id") if is_original_pdf else region.get("assembly_id")
    document_type = original.get("document_type") or candidate.get("document_type") or "meeting_minutes"
    return {
        "source": {"document_id": document_id, "system_code": "browser_minutes", "source_record_key": docid, "title": title, "document_type": document_type, "source_url": source_url, "original_file_url": original.get("download_url") or candidate.get("original_file_url") or None, "storage_uri": str(pdf_path), "mime_type": "application/pdf", "sha256": sha256(pdf_path), "file_size_bytes": pdf_path.stat().st_size, "published_at": meeting_date, "access_policy": "public", "raw_payload": {"candidate": candidate, "browser_manifest": manifest, "official_pdf": original or None}, "metadata": source_metadata},
        "meeting": {"council_level": original.get("council_level") or "local_council", "administrative_region_code": region_code, "assembly_id": assembly_id, "assembly_name": assembly_name, "province": province, "city_county": city_county, "meeting_title": title, "meeting_date": meeting_date, "agenda_text": "", "metadata": {"docid": docid, "acquisition_method": acquisition, "official_pdf": bool(is_original_pdf), "source_page_url": original.get("source_page_url"), "download_url": original.get("download_url")}},
        "pages": pages,
        "artifacts": artifacts,
        "segments": segments,
    }


def pdf_inputs(*, all_inputs: bool = False) -> list[Path]:
    # `converted/` also contains an older, aborted HTML-print attempt.  A PDF
    # is considered a Hancom result only when its document ID has the matching
    # raw HWP artifact; this prevents the two pipelines from being confused.
    hwp_paths = [
        path for path in HWP_PDF_ROOT.glob("region_*/*.pdf")
        if any(
            (RAW_ROOT / path.parent.name / f"{path.stem}{suffix}").exists()
            for suffix in (".hwp", ".hwpx")
        )
    ]
    hwp_docids = {path.stem for path in hwp_paths}
    html_paths = [path for path in HTML_PDF_ROOT.glob("region_*/*.pdf") if path.stem not in hwp_docids]

    # The browser collection intentionally keeps both acquisition routes.  A
    # region can therefore have HWP files plus HTML snapshots. The default
    # selection preserves the original ten-per-region test target; a rebuild
    # can explicitly opt into every collected PDF with --all-inputs.
    by_region: dict[str, dict[str, list[Path]]] = {}
    for path in hwp_paths:
        by_region.setdefault(path.parent.name, {"hwp": [], "html": []})["hwp"].append(path)
    for path in html_paths:
        by_region.setdefault(path.parent.name, {"hwp": [], "html": []})["html"].append(path)
    selected: list[Path] = []
    if all_inputs:
        for region_name in sorted(by_region):
            selected.extend(sorted(by_region[region_name]["hwp"]))
            selected.extend(sorted(by_region[region_name]["html"]))
    else:
        for region_name in sorted(by_region):
            hwp_region = sorted(by_region[region_name]["hwp"])[:10]
            html_region = sorted(by_region[region_name]["html"])[: max(0, 10 - len(hwp_region))]
            selected.extend(hwp_region + html_region)
    original_paths: list[Path] = []
    if ORIGINAL_MANIFEST_PATH.exists():
        try:
            payload = json.loads(ORIGINAL_MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        for item in payload.get("items", []):
            filename = item.get("file")
            if not filename:
                continue
            path = ORIGINAL_PDF_ROOT / filename
            if path.exists() and path.read_bytes()[:5] == b"%PDF-":
                original_paths.append(path)
    # Directly downloaded official PDFs are supplemental inputs and are not
    # counted against the 10-per-local-region target above.
    return sorted(selected) + sorted(original_paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--official-only",
        action="store_true",
        help="적재된 기존 문서는 건드리지 않고 pdf_original manifest의 공식 PDF만 증분 처리",
    )
    parser.add_argument(
        "--all-inputs",
        action="store_true",
        help="지역당 10건 제한을 풀고 현재 데이터셋의 모든 고유 PDF를 처음부터 적재",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="재생성할 SQLite 경로. 새 경로를 지정하면 기존 DB를 건드리지 않음",
    )
    args = parser.parse_args()
    candidates, manifests, originals = load_candidates(), load_browser_manifests(), load_original_pdf_manifest()
    db_path = args.db if args.db.is_absolute() else ROOT / args.db
    db = LuceraDB(db_path)
    db.initialize(SCHEMA_PATH)
    processed, errors = 0, []
    inputs = pdf_inputs(all_inputs=args.all_inputs)
    if args.official_only:
        inputs = [path for path in inputs if path.parent == ORIGINAL_PDF_ROOT]
    selected_inputs = inputs[: args.limit or None]
    prepared: list[tuple[Path, Path | None, str]] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected_inputs)))) as executor:
        futures = {
            executor.submit(
                ensure_open_data_loader,
                pdf_path,
                OD_ROOT / ("official_pdf" if pdf_path.parent == ORIGINAL_PDF_ROOT else f"region_{pdf_path.parent.name.removeprefix('region_')}"),
            ): pdf_path
            for pdf_path in selected_inputs
        }
        for future in as_completed(futures):
            pdf_path = futures[future]
            try:
                json_path, loader = future.result()
                prepared.append((pdf_path, json_path, loader))
            except Exception as exc:
                errors.append({"docid": pdf_path.stem, "error": f"analysis_prepare: {str(exc)[:500]}"})
    prepared.sort(key=lambda row: str(row[0]))
    for pdf_path, json_path, loader in prepared:
        docid = pdf_path.stem
        try:
            bundle = make_bundle(pdf_path, json_path, loader, candidates, manifests, originals)
            region_code = bundle["meeting"].get("administrative_region_code")
            db.insert_document_bundle(bundle)
            db.commit()
            processed += 1
            print(json.dumps({"docid": docid, "region_code": region_code, "loader": loader, "segments": len(bundle["segments"])}, ensure_ascii=False), flush=True)
        except Exception as exc:
            errors.append({"docid": docid, "error": str(exc)[:500]})
            print(json.dumps({"docid": docid, "error": str(exc)[:500]}, ensure_ascii=False), flush=True)
    review_counts = rebuild_case_reviews(db)
    db.commit()
    report = {"db_path": str(db_path), "pdf_candidates": len(inputs), "processed": processed, "errors": errors, "review": review_counts, "stats": db.stats()}
    (MINUTES_REPORT_DIR / "browser_db_build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    db.close()


if __name__ == "__main__":
    main()
