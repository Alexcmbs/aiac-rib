import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI


API_TIMEOUT = int(os.getenv("API_TIMEOUT", "300"))
MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "3"))
RETRY_DELAY = int(os.getenv("API_RETRY_DELAY", "5"))


def _get_azure_client() -> OpenAI:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    if not (endpoint and deployment and api_key):
        raise RuntimeError("Variables Azure manquantes: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_KEY")
    base_url = endpoint.rstrip('/') + "/openai/v1/"
    return OpenAI(api_key=api_key, base_url=base_url)


TARGET_FIELDS_final: List[str] = [
    "id",
    "iban",
    "bic",
    "titulaire",
    "cdBanque",
    "nomBanque",
    "sens"
]

TARGET_FIELDS: List[str] = [
    "iban",
    "bic",
    "titulaire",
    "cdBanque",
    "nomBanque", 
]

def _strip_fences_and_think(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"<think>[\s\S]*?</think>", "", s)
    s = s.strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _extract_json_array(s: str) -> Optional[str]:
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    return s[start : end + 1]


def _build_system_prompt() -> str:
    cols = ", ".join(TARGET_FIELDS)
    # Prompt long, consolidé, avec raisonnement global et règles robustes
    parts: List[str] = []
    parts.append(
        "Tu es un expert en analyse et structuration de RIB de banque. "
        "Transforme un texte brut issu d'un OCR en JSON propre, strictement mappé aux colonnes cibles."
    )
    parts.append(f"\n\n🎯 COLONNES CIBLES À MAPPER (noms exacts et uniques) : [{cols}]\n")

    # Règles cœur: mapping, filtrage
    parts.append(
        "## MISSION\n"
        "- Identifier UNIQUEMENT les éléments mappables à ces colonnes cibles.\n"
        "- Les objets JSON doivent n'exposer QUE ces clés cibles (valeurs null si absentes).\n"
        "- La sortie DOIT être un tableau JSON (même s'il n'y a qu'un seul RIB).\n"
        "- Ne renvoie AUCUN texte avant ou après le JSON (pas de commentaire, pas de prose).\n"
        "- Si le même RIB apparaît plusieurs fois (mêmes valeurs pour iban, bic, titulaire, cdBanque, nomBanque), "
        "ne renvoie QU'UN SEUL objet JSON pour ce RIB (dédupliquer les doublons).\n"
    )

    # Définition détaillée des champs attendus
    parts.append(
        "\n## DÉFINITION DES CHAMPS\n"
        "- iban : IBAN complet du compte, sans espaces ni sauts de ligne, en majuscules. "
        "Exemple de format : FR7630006000011234567890189.\n"
        "- bic : Code BIC / SWIFT de la banque, 8 ou 11 caractères, en majuscules, sans espaces. "
        "Exemple : AGRIFRPPXXX.\n"
        "- titulaire : Nom complet du titulaire du compte tel qu'il apparaît sur le RIB "
        '(par exemple "DUPONT JEAN" ou "SAS MON ENTREPRISE").\n'
        "- cdBanque : Code banque exact présent sur le RIB (généralement 5 chiffres), "
        "sans espace ni caractère supplémentaire. Exemple : 30006.\n"
        "- nomBanque : Dénomination de la banque (par exemple \"CREDIT AGRICOLE ILE DE FRANCE\", "
        '"BANQUE POPULAIRE RIVES DE PARIS").\n'
        "\n"
        "Si un champ est introuvable ou trop ambigu dans le texte OCR, mets sa valeur à null.\n"
    )

    # Exemple de sortie
    parts.append(
        "\n## EXEMPLE DE SORTIE JSON\n"
        "Exemple pour un document contenant un seul RIB :\n"
        "[\n"
        "  {\n"
        '    "iban": "FR7630006000011234567890189",\n'
        '    "bic": "AGRIFRPPXXX",\n'
        '    "titulaire": "DUPONT JEAN",\n'
        '    "cdBanque": "30006",\n'
        '    "nomBanque": "CREDIT AGRICOLE ILE DE FRANCE"\n'
        "  }\n"
        "]\n"
        "\n"
        "Pour un document contenant plusieurs RIB, renvoie plusieurs objets dans le même tableau.\n"
    )

    return "\n".join(parts)


def _azure_text_to_json(client: OpenAI, full_text: str) -> List[Dict[str, Any]]:
    """
    Appelle Azure Responses API pour transformer un texte OCRisé de RIB en tableau JSON normalisé.
    """
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_DEPLOYMENT non défini (nom du déploiement Azure)")

    system_prompt = _build_system_prompt()

    resp = client.responses.create(
        model=deployment,
        instructions=system_prompt,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": full_text,
                    }
                ],
            }
        ],
    )

    raw = resp.output_text or ""
    cleaned = _strip_fences_and_think(raw)
    json_str = _extract_json_array(cleaned) or cleaned

    data = json.loads(json_str)
    if not isinstance(data, list):
        raise ValueError("La sortie JSON doit être un tableau d'objets (un objet par RIB détecté).")

    normalized: List[Dict[str, Any]] = []
    seen_keys: set[tuple] = set()

    for item in data:
        if not isinstance(item, dict):
            # On ignore les entrées non-objet au lieu de tout faire échouer.
            continue

        # 1) Normalisation des champs extraits par le modèle (TARGET_FIELDS)
        base: Dict[str, Any] = {}
        for field in TARGET_FIELDS:
            base[field] = item.get(field)

        # Clé de déduplication basée sur les champs principaux du RIB
        dedup_key = (
            base.get("iban"),
            base.get("bic"),
            base.get("titulaire"),
            base.get("cdBanque"),
            base.get("nomBanque"),
        )
        if dedup_key in seen_keys:
            # Doublon exact : on ignore cette entrée
            continue
        seen_keys.add(dedup_key)

        # 2) Construction de l'objet final strictement aligné sur TARGET_FIELDS_final
        final_obj: Dict[str, Any] = {}
        for field in TARGET_FIELDS_final:
            if field == "id":
                # Identifiant simple auto-incrémenté par ligne de RIB
                final_obj[field] = len(normalized) + 1
            elif field == "sens":
                # Pour ce pipeline RIB, le sens est fixé à "virement"
                final_obj[field] = "virement"
            else:
                final_obj[field] = base.get(field)

        normalized.append(final_obj)

    return normalized


class AzureTextToJsonService:
    """
    Service Azure autonome pour transformer une ou plusieurs pages texte OCR d'un RIB en JSON structuré.
    """

    async def text_pages_to_json(self, page_texts: List[str]) -> List[Dict[str, Any]]:
        """
        Agrège les pages OCR en un seul texte puis appelle Azure pour produire un tableau JSON.
        """
        full_text = "\n\n".join(page_texts).strip()
        if not full_text:
            return []

        last_error: Optional[BaseException] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                client = _get_azure_client()
                return _azure_text_to_json(client, full_text)
            except Exception as exc:  # pragma: no cover - robust API layer
                last_error = exc
                if attempt >= MAX_RETRIES:
                    break
                await asyncio.sleep(RETRY_DELAY)

        raise RuntimeError(f"Échec texte→JSON après {MAX_RETRIES} tentatives: {last_error}")

