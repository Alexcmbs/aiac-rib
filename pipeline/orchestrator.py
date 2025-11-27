import asyncio
import time
from pathlib import Path
from typing import Optional

from .config import load_config
from .json_service import AzureTextToJsonService
from .ocr_service import AzureOCRService
from .storage import prepare_paths, write_errors, write_status
from .types import ProcessConfig, ProcessPaths, ProcessReport, StepResult
from .writer import write_merged_json, write_merged_txt, write_txt_pages


async def run_pdf_pipeline(pdf_path: str, cfg: Optional[ProcessConfig] = None) -> ProcessReport:
    """
    Orchestrateur principal: OCR → TXT → JSON (RIB).

    Étapes actuelles:
    1. OCR des pages PDF en texte brut.
    2. Agrégation du texte et appel Azure texte→JSON pour extraire les champs RIB.
    """
    cfg = cfg or load_config()

    paths: ProcessPaths = prepare_paths(pdf_path, cfg.out_root)
    steps: list[StepResult] = []
    errors: dict[str, str] = {}

    page_texts: list[str] = []

    # 1) OCR → TXT (page par page + fichier TXT global)
    try:
        t0 = time.time()
        ocr = AzureOCRService()
        page_texts = await ocr.extract_pages_text(str(paths.original_pdf_path))

        # Sauvegarde page par page (debug) + fichier TXT combiné pour le text→JSON
        write_txt_pages(paths.process_dir, paths.base_name, page_texts)
        merged_txt_path = write_merged_txt(paths.process_dir, paths.base_name, page_texts)
        steps.append(
            StepResult(
                name="ocr_pages_text",
                ok=True,
                duration_sec=time.time() - t0,
                output_paths={
                    "txt_dir": str(paths.process_dir),
                    "merged_txt": str(merged_txt_path),
                },
            )
        )
    except Exception as e:
        steps.append(
            StepResult(
                name="ocr_pages_text",
                ok=False,
                duration_sec=0.0,
                error=str(e),
            )
        )
        errors["ocr_pages_text"] = str(e)

    # 2) Texte OCR → JSON (RIB) uniquement si l'OCR a réussi
    if not errors:
        try:
            t0 = time.time()
            json_svc = AzureTextToJsonService()
            rib_rows = await json_svc.text_pages_to_json(page_texts)

            # Écriture du JSON fusionné (toutes pages) dans `<base>_merged_all_pages.json`
            write_merged_json(paths.process_dir, paths.base_name, rib_rows)
            merged_path = paths.process_dir / f"{paths.base_name}_merged_all_pages.json"

            steps.append(
                StepResult(
                    name="text_to_json_rib",
                    ok=True,
                    duration_sec=time.time() - t0,
                    output_paths={"merged_json": str(merged_path)},
                )
            )
        except Exception as e:
            steps.append(
                StepResult(
                    name="text_to_json_rib",
                    ok=False,
                    duration_sec=0.0,
                    error=str(e),
                )
            )
            errors["text_to_json_rib"] = str(e)

    # Écriture du status final (OCR + JSON, ou partiel si erreur)
    write_status(
        paths.process_dir,
        {
            "pdf": str(paths.original_pdf_path),
            "steps": [s.__dict__ for s in steps],
        },
    )

    if errors:
        write_errors(paths.process_dir, errors)

    return ProcessReport(
        pdf=str(paths.original_pdf_path),
        process_dir=str(paths.process_dir),
        steps=steps,
    )


