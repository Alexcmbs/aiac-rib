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



TARGET_FIELDS: List[str] = [
    "id"
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
    parts: List[str] = []
    parts.append(
        "Tu es un expert en analyse et structuration de mails en français. "
        "À partir du texte complet d'un mail (par exemple un mail de demande d'enregistrement de RIB), "
        "tu dois extraire l'identifiant du client ou de la souscription mentionné dans le message."
    )
    parts.append(f"\n\n🎯 CHAMP CIBLE UNIQUE À MAPPER : [{cols}]\n")

    parts.append(
        "## MISSION\n"
        "- Analyse le texte du mail et identifie l'identifiant du client / de contrat, "
        "souvent une chaîne alphanumérique comme \"TRS59861\".\n"
        "- La sortie DOIT être un tableau JSON d'objets, même s'il n'y a qu'un seul identifiant.\n"
        "- Chaque objet JSON ne doit contenir QUE la clé \"id\".\n"
        "- Ne renvoie AUCUN texte avant ou après le JSON (pas de commentaire, pas de prose).\n"
        "- Si aucun identifiant explicite ne peut être trouvé, renvoie un tableau avec un seul objet "
        "où \"id\" vaut null.\n"
    )

    parts.append(
        "\n## DÉFINITION DU CHAMP\n"
        "- id : identifiant du client, du contrat ou de la souscription indiqué de manière explicite dans le mail. "
        "Exemples possibles : \"TRS59861\", \"CLT12345\".\n"
        "Ne renvoie jamais de montants, d'IBAN, de dates ou de noms de personnes dans ce champ.\n"
    )

    parts.append(
        "\n## EXEMPLE\n"
        "Texte du mail :\n"
        "\"Bonjour Marie Pierre, Je te remercie d’enregistrer le RIB de Mr et Mme CROIBIER pour un montant de 180 € "
        "pour Augustin TRS59861 Cordialement.\"\n\n"
        "Sortie JSON attendue :\n"
        "[\n"
        "  {\n"
        '    "id": "TRS59861"\n'
        "  }\n"
        "]\n"
    )

    return "\n".join(parts)


def _azure_text_to_json(client: OpenAI, full_text: str) -> List[Dict[str, Any]]:
    """
    Appelle Azure Responses API pour transformer le texte d'un mail en tableau JSON normalisé
    contenant uniquement le champ `id`.
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
        # Si le modèle renvoie un objet unique, on l'encapsule dans un tableau.
        data = [data]

    normalized: List[Dict[str, Any]] = []

    for item in data:
        if not isinstance(item, dict):
            # On ignore les entrées non-objet au lieu de tout faire échouer.
            continue

        base: Dict[str, Any] = {}
        for field in TARGET_FIELDS:
            base[field] = item.get(field)

        final_obj: Dict[str, Any] = {}
        # On ne garde que la clé "id"
        final_obj["id"] = base.get("id")

        normalized.append(final_obj)

    return normalized



