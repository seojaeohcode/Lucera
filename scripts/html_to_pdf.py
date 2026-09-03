from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
from xml.sax.saxutils import escape

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import HTML_DIR, HTML_PDF_DIR, MINUTES_MANIFEST_DIR


HTML_ROOT = HTML_DIR
PDF_ROOT = HTML_PDF_DIR
MANIFEST = MINUTES_MANIFEST_DIR / "html_pdf_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def font_path() -> Path | None:
    candidates = [
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\malgunbd.ttf"),
        Path(r"C:\Windows\Fonts\NanumGothic.ttf"),
    ]
    return next((path for path in candidates if path.exists()), None)


def write_pdf(text: str, output: Path, font: Path | None) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    if font:
        if "LuceraMalgun" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("LuceraMalgun", str(font), subfontIndex=0))
        font_name = "LuceraMalgun"
    else:
        font_name = "Helvetica"
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "LuceraBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8.8,
        leading=12.5,
        alignment=TA_LEFT,
        spaceAfter=0,
        wordWrap="CJK",
    )
    story = []
    for raw_line in text.replace("\r\n", "\n").splitlines():
        line = raw_line.rstrip()
        if line:
            story.append(Paragraph(escape(line), body))
        else:
            story.append(Spacer(1, 2.8 * mm))
    if not story:
        story.append(Paragraph("(내용 없음)", body))
    document = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title="Lucera browser HTML print",
    )
    document.build(story)
    # reportlab does not expose the page count directly; the file is reopened
    # by the caller only for validation, while this estimate is for the audit.
    return 0


def main() -> None:
    import fitz

    font = font_path()
    results: list[dict[str, object]] = []
    for html_path in sorted(HTML_ROOT.glob("region_*/*.html")):
        region = html_path.parent.name.removeprefix("region_")
        output = PDF_ROOT / f"region_{region}" / f"{html_path.stem}.pdf"
        text_path = html_path.with_suffix(".txt")
        text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.exists() else ""
        row: dict[str, object] = {
            "region_code": region,
            "source_html_path": str(html_path),
            "source_html_sha256": sha256(html_path),
            "output_path": str(output),
            "parser_name": "browser-html-print",
            "parser_version": "1.0",
            "font": str(font) if font else None,
        }
        try:
            if not text:
                raise RuntimeError("browser HTML snapshot has no text sidecar")
            if not output.exists() or output.stat().st_size < 1000:
                write_pdf(text, output, font)
            with fitz.open(output) as pdf:
                row["page_count"] = len(pdf)
            row["status"] = "converted"
            row["output_sha256"] = sha256(output)
            row["output_size_bytes"] = output.stat().st_size
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = str(exc)[:500]
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    MANIFEST.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"inputs": len(results), "converted": sum(r.get("status") == "converted" for r in results), "failed": sum(r.get("status") == "failed" for r in results), "manifest": str(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
