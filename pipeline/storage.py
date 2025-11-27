import json
import shutil
import uuid
from pathlib import Path
from typing import Dict, Tuple

from .types import ProcessPaths


def _safe_dir_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("_", "-", ".") else "_" for c in name)


def ensure_process_dir(out_root: Path, base_name: str) -> Path:
    base = _safe_dir_name(base_name)
    candidate = out_root / base
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    # fallback unique
    unique = out_root / f"{base}_{uuid.uuid4().hex[:8]}"
    unique.mkdir(parents=True, exist_ok=True)
    return unique


def prepare_paths(pdf_path: str, out_root: Path) -> ProcessPaths:
    pdf = Path(pdf_path).expanduser().resolve()
    base_name = pdf.stem
    process_dir = ensure_process_dir(out_root, base_name)
    original_pdf_path = process_dir / f"original_{pdf.name}"
    try:
        shutil.copy2(str(pdf), str(original_pdf_path))
    except Exception:
        pass
    return ProcessPaths(run_root=out_root, process_dir=process_dir, base_name=base_name, original_pdf_path=original_pdf_path)


def write_json(path: Path, data: Dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_status(process_dir: Path, status: Dict) -> Path:
    p = process_dir / "status.json"
    write_json(p, status)
    return p


def write_errors(process_dir: Path, errors: Dict) -> Path:
    p = process_dir / "errors.json"
    write_json(p, errors)
    return p


