# Pipeline (production, autonome)

Implémentation modulaire et autonome du pipeline PDF → TXT → JSON → CSV.
Toutes les dépendances "app" ont été supprimées: les services Azure (OCR et texte→JSON) et le mapping sont intégrés ici.

## Fichiers

- `__init__.py`
  Expose les modules principaux du pipeline.

- `config.py`
  Chargement de la configuration (env/CLI) et valeurs par défaut (out_root, backends, DPI, timeouts).

- `types.py`
  Dataclasses: `ProcessConfig`, `ProcessPaths`, `StepResult`, `ProcessReport`.

- `storage.py`
  Dossiers de process, copie de l’original, helpers `status.json` / `errors.json`.

- `ocr_service.py`
  OCR Azure GPT‑5‑mini autonome (PDF→images via pdf2image+Poppler, puis Responses API). Fournit:
  - `AzureOCRService.extract_pages_text(pdf_path)`
  - `AzureOCRService.extract_name_columns(pdf_path)`

  Texte→JSON Azure autonome (Responses API), prompt condensé + nettoyage. Fournit:
  - `AzureTextToJsonService.text_pages_to_json(page_texts)`

- `writer.py`
  Écrit les artefacts: TXT par page, JSON par page + merged, CSV “extracted” depuis le JSON.

  Normalisation indépendante: détection d’entêtes, renommage/formatage des colonnes, sauvegarde CSV.



- `orchestrator.py`
  Orchestrateur end‑to‑end: OCR → JSON → `extracted.csv` → normalize → mapping → `final.csv`, avec `status.json` / `errors.json`.

- `cli.py`
  CLI de traitement par dossier (input, out_root, dpi, backends, skip‑existing).

## Sorties d’un process PDF

- `original_<fichier>.pdf`
- `*_ocr_page_*.txt`
- `*_json_page_*.json`
- `<prefix>_merged_all_pages.json`
- `extracted_<base>.csv`
- `intermediate_<base>.csv`
- `normalized_<base>.csv`
- `status.json` / `errors.json`

## Prérequis

- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_KEY`
- `pdf2image` + Poppler (conversion PDF→PNG)


## Exemple de commande pour tester 

- python -m pipeline.cli \
  --input "/Users/alexandrecombes/Desktop/Foaster/Suisscourtage/suisscourtage_bordereau_prod/pipeline" \
  --out-root "/Users/alexandrecombes/Desktop/Foaster/Suisscourtage/suisscourtage_bordereau_prod/outputs" \
  --dpi 200 \
  --mapping-backend azure