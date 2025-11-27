"""Pipeline package: modular services and orchestrator for PDF → TXT → JSON → CSV.

This package provides:
- Configuration loading utilities
- Typed structures for process configuration and reporting
- Storage helpers for organizing process outputs
- Service wrappers around existing OCR/LLM/mapping logic
- A clean orchestrator to run the end-to-end pipeline
- A CLI to process folders in batch mode
"""

__all__ = [
    "config",
    "types",
    "storage",
    "ocr_service",
    "writer",
    "orchestrator",
]


