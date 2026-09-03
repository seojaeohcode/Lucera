from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lucera.paths import HWP_DIR, HWP_PDF_DIR, MINUTES_MANIFEST_DIR


RAW_ROOT = HWP_DIR
PDF_ROOT = HWP_PDF_DIR
# Keep the automation staging path ASCII-only; the project directory itself
# contains Korean characters and older Hancom builds can hang on such paths.
WORK_ROOT = Path(os.environ.get("TEMP", "C:/Temp")) / "lucera_hwp_convert"
MANIFEST = MINUTES_MANIFEST_DIR / "conversion_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_one(source_text: str, output_text: str) -> dict[str, object]:
    # Import and create Hancom COM inside the worker.  Separate processes avoid
    # COM apartment sharing and reduce the time required for a regional batch.
    from pyhwpx import Hwp

    source = Path(source_text)
    output = Path(output_text)
    region = source.parent.name.removeprefix("region_")
    row: dict[str, object] = {
        "region_code": region,
        "source_path": str(source),
        "source_sha256": sha256(source),
        "output_path": str(output),
        "output_format": "PDF",
    }
    if output.exists() and output.stat().st_size >= 1000:
        row["status"] = "already_converted"
        row["output_sha256"] = sha256(output)
        row["output_size_bytes"] = output.stat().st_size
        return row
    work_root = Path(os.environ.get("TEMP", "C:/Temp")) / f"lucera_hwp_convert_{os.getpid()}"
    work_root.mkdir(parents=True, exist_ok=True)
    staged_input = work_root / f"input{source.suffix.lower()}"
    staged_output = work_root / "output.pdf"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        staged_input.unlink(missing_ok=True)
        staged_output.unlink(missing_ok=True)
        shutil.copy2(source, staged_input)
        hwp = Hwp(visible=False)
        try:
            opened = hwp.open(str(staged_input.resolve()), "", "lock:false;forceopen:true;suspendpassword:true;")
            if not opened:
                raise RuntimeError("Hancom automation returned open=false")
            saved = hwp.save_as(str(staged_output.resolve()), "PDF", "")
            if not saved or not staged_output.exists() or staged_output.stat().st_size < 1000:
                raise RuntimeError("Hancom automation did not create a valid PDF")
        finally:
            try:
                hwp.quit()
            except Exception:
                pass
        shutil.copy2(staged_output, output)
        row["status"] = "converted"
        row["output_sha256"] = sha256(output)
        row["output_size_bytes"] = output.stat().st_size
    except Exception as exc:
        row["status"] = "failed"
        row["error"] = str(exc)[:500]
    return row


def run_child(source: Path, output: Path) -> dict[str, object]:
    """Run one Hancom automation job in an isolated process.

    Hancom's COM server can outlive a Python worker when a document is
    malformed or locked.  A per-file subprocess gives the batch a hard
    timeout and keeps one bad HWP from preventing the remaining documents from
    being converted.
    """
    command = [sys.executable, str(Path(__file__).resolve()), "--one", str(source), str(output)]
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, _ = process.communicate(timeout=150)
    except subprocess.TimeoutExpired:
        # Terminate the isolated Python process and its descendants.  The
        # target is the subprocess created immediately above, never a broad
        # system process collection.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "region_code": source.parent.name.removeprefix("region_"),
            "source_path": str(source),
            "source_sha256": sha256(source),
            "output_path": str(output),
            "output_format": "PDF",
            "status": "failed",
            "error": "Hancom automation timeout after 150 seconds",
        }
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    if lines:
        try:
            row = json.loads(lines[-1])
            if isinstance(row, dict):
                return row
        except json.JSONDecodeError:
            pass
    return {
        "region_code": source.parent.name.removeprefix("region_"),
        "source_path": str(source),
        "source_sha256": sha256(source),
        "output_path": str(output),
        "output_format": "PDF",
        "status": "failed",
        "error": f"conversion subprocess exit={process.returncode}: {(stdout or '')[-400:]}",
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) == 4 and sys.argv[1] == "--one":
        row = convert_one(sys.argv[2], sys.argv[3])
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return

    inputs = sorted(RAW_ROOT.glob("region_*/*.hwp")) + sorted(RAW_ROOT.glob("region_*/*.hwpx"))
    jobs: list[tuple[Path, Path]] = []
    for source in inputs:
        region = source.parent.name.removeprefix("region_")
        jobs.append((source, PDF_ROOT / f"region_{region}" / f"{source.stem}.pdf"))
    results: list[dict[str, object]] = []
    for source, output in jobs:
        row = run_child(source, output)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    results.sort(key=lambda row: str(row["source_path"]))
    MANIFEST.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"inputs": len(inputs), "converted": sum(r.get("status") in {"converted", "already_converted"} for r in results), "failed": sum(r.get("status") == "failed" for r in results), "workers": 1, "manifest": str(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
