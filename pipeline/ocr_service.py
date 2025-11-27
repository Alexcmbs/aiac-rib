import base64
import io
import json
import os
from pathlib import Path
from typing import List, Optional

from openai import OpenAI
from pdf2image import convert_from_path
from PIL import Image


class OCRService:
    async def extract_pages_text(self, pdf_path: str) -> List[str]:
        raise NotImplementedError

    async def extract_name_columns(self, pdf_path: str) -> Optional[List[str]]:
        raise NotImplementedError


def _get_azure_client() -> OpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")

    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT non défini (ex: https://<resource>.openai.azure.com)")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT non défini (nom du déploiement dans Azure)")
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY non défini")

    base_url = endpoint.rstrip('/') + "/openai/v1/"
    client = OpenAI(api_key=api_key, base_url=base_url)
    return client


def _azure_image_to_text(client: OpenAI, image_bytes: bytes, instructions: str) -> str:
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64}"

    resp = client.responses.create(
        model=deployment,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Traiter cette page selon les instructions."},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    )
    return resp.output_text or ""


def _ocr_instructions() -> str:
    return ("""
       <role>
Tu es un expert en transcription fidèle de documents comptables/administratifs à partir d’images.
</role>

<mission>
Reproduire TOUT le texte visible avec la plus grande fidélité possible, en préservant la structure visuelle, et en structurant les tableaux au format requis.
</mission>

<exhaustivite>
- Extraire absolument tout le texte (en-têtes, titres, labels, valeurs, totaux, notes, mentions légales, petites lignes).
- Ne jamais omettre un mot, chiffre, caractère ou symbole.
- Ne pas interpréter ni déduire : transcrire uniquement ce qui est visible.
</exhaustivite>

<tables_spec>
- Identifier et structurer TOUS les tableaux visuels.
- Avant chaque tableau, écrire exactement : `=== TABLEAU ===`
- Une ligne d’en-têtes unique par tableau (même s’ils sont visuellement multi-lignes).
- Séparateur de colonnes : `|` (avec un espace de part et d’autre : ` ... | ... | ... `).
- Ordre des colonnes : strictement de gauche à droite.
- Chaque ligne de données suit les en-têtes, avec le même nombre de colonnes.
- Cellule vide = un espace entre deux `|` : `| |`
- Conserver le nombre EXACT de lignes du tableau original.
</tables_spec>

<headers_multilignes>
- Si des en-têtes sont répartis verticalement sur plusieurs lignes dans une même colonne, les fusionner en un seul libellé.
- Combiner les segments avec un espace ou `/` selon le contexte visuel (ex.: `Nom sociétaire / N° sociétaire / N° contrat`).
- La ligne d’en-têtes finale doit être unique et complète.
</headers_multilignes>

<cellules_fusionnees>
- Si une cellule s’étend verticalement sur plusieurs lignes : inscrire sa valeur UNIQUEMENT sur la première ligne concernée.
- Pour toutes les lignes suivantes partageant cette cellule : laisser la cellule **vide** (`| |`) à cet emplacement.
- Ne jamais remonter/copier des valeurs d’une ligne inférieure pour combler une cellule vide.
</cellules_fusionnees>

<contracts>
- CAPTURER INTÉGRALEMENT les numéros de contrat alphanumériques avec préfixes/codes et séparateurs (ex.: `ERP/3495U539`, `HA CPX 00123`).
- Conserver lettres, préfixes (ERP/HA/CPX…) et séparateurs (`/`, `-`, espaces contrôlés) tels qu’imprimés.
- Ne PAS supprimer les lettres qui précèdent/suivent les chiffres, même si l’en‑tête est simplement `N° contrat`.
- SEULE exception: si l’en‑tête combine plusieurs labels avec `/` (ex.: `CODE_ID / N° contrat`), alors la valeur peut être scindée visuellement (le texte restera fidèle; la scission exacte sera faite plus tard dans l’étape texte→JSON).
</contracts>

<format_sortie>
- Renvoyer du texte brut (pas de commentaires, pas d’explications, pas de Markdown autour).
- Utiliser des lignes vides pour séparer les sections hors tableaux si nécessaire.
- Pour les nombres, dates, montants : conserver exactement le format visible (espaces, séparateurs, zéros initiaux).
</format_sortie>

<filtrage_pages_synthese>
- Si la page ne présente PAS de tableau de lignes contractuelles (ex.: seulement des totaux/sous‑totaux/KPI/synthèses), NE PAS créer de bloc `=== TABLEAU ===`.
- Indice: absence d’identifiants (n° contrat/client), dates et montants alignés en colonnes; présence majoritaire de libellés `TOTAL`, `SOUS‑TOTAL`, `CUMUL`, `SYNTHÈSE`, etc.
- Dans ce cas, laisse la section tabulaire vide (ne pas fabriquer de tableau artificiel).
</filtrage_pages_synthese>

<ocr_corrections_min>
- Corriger uniquement les erreurs OCR évidentes de confusions visuelles courantes (ex.: `I`→`1`, `O`→`0` dans un contexte numérique).
- Ne modifier aucun autre contenu ni la casse, ni la ponctuation.
</ocr_corrections_min>

<pii_policy>
- NE JAMAIS anonymiser ni masquer les informations (noms, numéros, adresses, etc.).
- NE JAMAIS écrire `[REDACTED]`, `[XXXX]`, ou toute autre forme de censure. Transcrire exactement ce qui est visible.
- Ne jamais répondre "I don't know", "Je ne sais pas" ou toute phrase de non-réponse: si un champ est illisible/absent, laisse-le vide dans la transcription de la cellule correspondante, sans ajouter de texte hors-tableau.
- Interdiction absolue d'OMETTRE ou de SUPPRIMER des caractères lisibles: toute cellule lisible doit être retranscrite intégralement (y compris lettres, préfixes de contrat, tirets, slashs, zéros initiaux).
- Interdit de substituer des valeurs génériques: pas de '-', '—', 'N/A', 'ND', 'Unknown', '?', '0', ou tout placeholder si non visibles dans la cellule d'origine.
- Ne pas résumer, ne pas reformuler, ne pas normaliser sémantiquement: la sortie doit être une TRANSCRIPTION fidèle, pas une interprétation.
- Si un caractère est ambigu, conserver le glyphe OCR le plus probable sans marquer '[illisible]'; ne jamais inventer.
- NON-RESPECT = ERREUR CRITIQUE: ces règles priment sur toute autre consigne.
</pii_policy>

<table_scope>
- Insérer explicitement la balise `=== TABLEAU ===` juste avant la première rangée d'en‑têtes détectée, puis transcrire les lignes du tableau.
- Transcrire également l'intégralité des contenus HORS TABLEAU (en‑têtes, titres, labels, valeurs, totaux, notes, mentions légales, petites lignes) en dehors des blocs `=== TABLEAU ===`, dans l'ordre de lecture.
- Ne JAMAIS effectuer de calculs/déductions (totaux, TVA, inversions) pour remplir des cellules: écrire uniquement ce qui est VISIBILE dans la cellule d'origine, sinon laisser vide.
- Pages ne contenant que totaux/synthèses: laisser la section tabulaire vide (aucune ligne transcrite), mais transcrire quand même TOUT le texte hors tableau présent sur la page.
- CAPTURE INTÉGRALE DES NUMÉROS DE CONTRAT: conserver les préfixes/codes alphanumériques (ex.: ERP/, HA, CPX) s'ils figurent dans la cellule.
</table_scope>

<regles_absolues>
- NE JAMAIS fusionner des lignes, déplacer des valeurs entre lignes/colonnes, ni supprimer des lignes partiellement vides.
- TOUJOURS respecter l’alignement vertical implicite des colonnes et la hiérarchie visuelle.
</regles_absolues>

<comportement>
- Ne pas poser de questions, ne pas demander de confirmation.
- Produire directement la transcription finale conforme aux règles ci-dessus.
</comportement>

<sortie_uniquement>
La réponse doit contenir UNIQUEMENT la transcription (texte et tableaux). Aucune autre phrase.
</sortie_uniquement>
"""
    )


async def _azure_ocr_full_pdf_text(pdf_path: str) -> List[str]:
    """
    OCR complet d'un document PDF ou d'une image (JPG/PNG) en texte par page.

    - PDF : conversion via pdf2image (chaque page → image).
    - JPG/PNG : considéré comme un document 1 page.
    """
    client = _get_azure_client()
    dpi = int(os.getenv("VLM_DPI", "200"))
    instructions = _ocr_instructions()

    path = Path(pdf_path).expanduser().resolve()
    suffix = path.suffix.lower()

    page_texts: List[str] = []

    # 1) Chargement des pages sous forme d'images
    if suffix == ".pdf":
        pages = convert_from_path(str(path), dpi=dpi)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        # Image unique → une seule "page"
        with Image.open(str(path)) as img:
            pages = [img.copy()]
    else:
        raise RuntimeError(f"Type de fichier non supporté pour l'OCR: {suffix}")

    # 2) Envoi de chaque page au backend Vision
    for page_img in pages:
        with io.BytesIO() as buf:
            page_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
        text = _azure_image_to_text(client, img_bytes, instructions=instructions)
        page_texts.append(text)
    return page_texts


async def _azure_ocr_name_column_pdf(pdf_path: str) -> Optional[List[str]]:
    client = _get_azure_client()
    dpi = int(os.getenv("VLM_DPI", "200"))

    pages = convert_from_path(pdf_path, dpi=dpi)
    for page_img in pages:
        with io.BytesIO() as buf:
            page_img.save(buf, format="PNG")
            img_bytes = buf.getvalue()

        instructions = (
            "Retourne UNIQUEMENT une liste JSON des noms de colonnes du tableau principal s'il existe, sinon []."
        )
        out = _azure_image_to_text(client, img_bytes, instructions=instructions).strip()

        if out.startswith("```json"):
            out = out[7:]
        if out.startswith("```"):
            out = out[3:]
        if out.endswith("```"):
            out = out[:-3]
        out = out.strip()

        try:
            data = json.loads(out)
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                return data
        except Exception:
            continue

    return None


class AzureOCRService(OCRService):
    async def extract_pages_text(self, pdf_path: str) -> List[str]:
        return await _azure_ocr_full_pdf_text(pdf_path)

    async def extract_name_columns(self, pdf_path: str) -> Optional[List[str]]:
        return await _azure_ocr_name_column_pdf(pdf_path)


