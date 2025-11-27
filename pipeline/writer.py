from pathlib import Path
from typing import Any, List

import json


def write_txt_pages(out_dir: Path, prefix: str, page_texts: List[str]) -> None:
    """Écrit une série de pages texte OCR dans des fichiers .txt individuels."""
    for idx, txt in enumerate(page_texts, start=1):
        (out_dir / f"{prefix}_ocr_page_{idx}.txt").write_text(txt, encoding="utf-8")


def write_merged_txt(out_dir: Path, prefix: str, page_texts: List[str]) -> Path:
    """
    Écrit un fichier texte unique contenant toutes les pages OCR concaténées,
    afin que le modèle texte→JSON reçoive le document complet en une seule fois.
    """
    full_text = "\n\n".join(page_texts)
    path = out_dir / f"{prefix}_ocr_all_pages.txt"
    path.write_text(full_text, encoding="utf-8")
    return path


def write_merged_json(out_dir: Path, prefix: str, data: Any) -> None:
    """
    Écrit le JSON fusionné de toutes les pages dans `<prefix>_merged_all_pages.json`.
    Le contenu peut être un tableau ou un objet Python sérialisable en JSON.
    """
    path = out_dir / f"{prefix}_merged_all_pages.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


