import os
from pathlib import Path
from typing import Optional

from .types import ProcessConfig


def load_config(
    out_root: Optional[str] = None,
    ocr_backend: Optional[str] = None,
    mapping_backend: Optional[str] = None,
    dpi: Optional[int] = None,
    skip_existing: bool = False,
) -> ProcessConfig:
    root = Path(out_root or os.getenv("PIPELINE_OUT_ROOT", "uploads")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    cfg = ProcessConfig(
        out_root=root,
        ocr_backend=(ocr_backend or os.getenv("OCR_BACKEND", "azure")).lower(),
        mapping_backend=(mapping_backend or os.getenv("MAPPING_BACKEND", "hyperbolic")).lower(),
        dpi=int(dpi or int(os.getenv("VLM_DPI", "200"))),
        skip_existing=skip_existing or os.getenv("SKIP_EXISTING", "0") == "1",
    )
    return cfg


