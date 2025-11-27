from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ProcessConfig:
    """Configuration de haut niveau pour exécuter le pipeline."""
    out_root: Path
    ocr_backend: str = "azure"           # pour extension future
    mapping_backend: str = "hyperbolic"   # "hyperbolic" | "azure"
    dpi: int = 200
    skip_existing: bool = False


@dataclass
class ProcessPaths:
    """Regroupe les chemins utilisés pendant le process d'un PDF."""
    run_root: Path
    process_dir: Path
    base_name: str
    original_pdf_path: Path


@dataclass
class StepResult:
    name: str
    ok: bool
    duration_sec: float
    output_paths: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class ProcessReport:
    pdf: str
    process_dir: str
    steps: List[StepResult]
    final_csv: Optional[str] = None


