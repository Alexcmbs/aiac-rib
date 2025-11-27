import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

try:
    # Chargement automatique du fichier .env s'il existe
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:  # pragma: no cover - sécurité en environnement sans python-dotenv
    load_dotenv = None  # type: ignore
    find_dotenv = None  # type: ignore

try:
    import firebase_admin  # type: ignore
    from firebase_admin import firestore, storage  # type: ignore
except Exception:  # pragma: no cover - firebase optionnel mais requis pour cette fonctionnalité
    firebase_admin = None  # type: ignore
    firestore = None  # type: ignore
    storage = None  # type: ignore


if load_dotenv and find_dotenv:
    try:
        load_dotenv(find_dotenv(usecwd=True), override=False)
    except Exception:
        # On ignore silencieusement si le chargement échoue
        pass


TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_AIAC_MAIL_APP_ID")
CLIENT_SECRET = os.getenv("AZURE_AIAC_MAIL_APP_SECRET")

# Adresse de la boîte utilisée pour lire les mails, ex: "alexandre.combes@foaster.ai"
MAILBOX = os.getenv("AIAC_MAILBOX") or os.getenv("MAILBOX") or "alexandre.combes@foaster.ai"

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

SUPPORTED_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}

# Config Firebase (Firestore + Storage)
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET")
FIREBASE_COLLECTION = "agent_mail_rib"


@dataclass
class MailProcessContext:
    """
    Contexte de traitement mail:
    - chemin local de la pièce jointe
    - nom de processus (id du document Firestore)
    - texte complet du mail
    """

    attachment_path: Path
    process_name: str
    mail_text: str


class MailServiceError(RuntimeError):
    """Erreur dédiée au service de récupération d'e-mails."""


def _get_env_or_raise() -> Tuple[str, str, str]:
    if not TENANT_ID or not CLIENT_ID or not CLIENT_SECRET:
        raise MailServiceError(
            "Variables d'environnement mail manquantes : "
            "AZURE_TENANT_ID, AZURE_AIAC_MAIL_APP_ID, AZURE_AIAC_MAIL_APP_SECRET"
        )
    return TENANT_ID, CLIENT_ID, CLIENT_SECRET


def get_graph_token() -> str:
    """
    Récupère un token OAuth2 client_credentials pour Microsoft Graph.

    Inspiré de `mails/test_graph.py`.
    """
    tenant_id, client_id, client_secret = _get_env_or_raise()

    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise MailServiceError("Impossible de récupérer access_token depuis la réponse OAuth2.")
    return token


def _graph_get(url: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_last_message(token: str) -> Optional[Dict[str, Any]]:
    """
    Récupère le dernier message reçu dans la mailbox (tri par receivedDateTime desc),
    avec le corps complet pour pouvoir remplir `mail_text`.
    """
    url = f"{GRAPH_BASE_URL}/users/{MAILBOX}/messages"
    data = _graph_get(
        url,
        token,
        params={
            "$top": 1,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,receivedDateTime,body,bodyPreview",
        },
    )
    values: List[Dict[str, Any]] = data.get("value") or []
    if not values:
        return None
    return values[0]


def _get_attachments(message_id: str, token: str) -> List[Dict[str, Any]]:
    url = f"{GRAPH_BASE_URL}/users/{MAILBOX}/messages/{message_id}/attachments"
    data = _graph_get(url, token)
    return data.get("value") or []


def _safe_filename(name: str) -> str:
    name = name.strip() or "attachment"
    # remplace tous les caractères "sales" par underscore
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _choose_best_attachment(attachments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Sélectionne la meilleure pièce jointe :
    - Priorité aux attachments de type fichier non inline
    - Priorité aux extensions connues (PDF/JPG/PNG)
    """
    file_attachments = [
        att
        for att in attachments
        if att.get("@odata.type") == "#microsoft.graph.fileAttachment"
        and not att.get("isInline", False)
        and att.get("contentBytes")
    ]
    if not file_attachments:
        return None

    # D'abord, on cherche une extension "connue"
    prioritized: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []
    for att in file_attachments:
        name = att.get("name") or ""
        ext = Path(name).suffix.lower()
        if ext in SUPPORTED_EXTS:
            prioritized.append(att)
        else:
            others.append(att)

    if prioritized:
        return prioritized[0]
    return others[0]


def _extract_mail_text_from_message(message: Dict[str, Any]) -> str:
    """
    Récupère le texte du mail à partir de l'objet message Graph.
    Utilise de préférence body.content, sinon bodyPreview.
    """
    body = message.get("body") or {}
    content = body.get("content")
    if content:
        return str(content)
    preview = message.get("bodyPreview")
    if preview:
        return str(preview)
    return ""


def _init_firebase() -> Tuple[Any, Any]:
    """
    Initialise Firebase (Firestore + Storage) et retourne (db, bucket).
    """
    if firebase_admin is None or firestore is None or storage is None:
        raise MailServiceError(
            "Le module firebase_admin est requis pour la fonctionnalité Firebase "
            "(installe-le avec `pip install firebase-admin`)."
        )
    if not FIREBASE_PROJECT_ID:
        raise MailServiceError("FIREBASE_PROJECT_ID doit être défini dans l'environnement.")
    if not FIREBASE_STORAGE_BUCKET:
        raise MailServiceError("FIREBASE_STORAGE_BUCKET doit être défini dans l'environnement.")

    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            options={
                "projectId": FIREBASE_PROJECT_ID,
                "storageBucket": FIREBASE_STORAGE_BUCKET,
            }
        )

    db = firestore.client()
    bucket = storage.bucket(FIREBASE_STORAGE_BUCKET)
    return db, bucket


def _build_storage_object_name(process_name: str, filename: str) -> str:
    """
    Construit le chemin de l'objet dans le bucket, en incluant le nom du processus.
    """
    return f"{FIREBASE_COLLECTION}/{process_name}/{filename}"


def _build_public_download_url(object_name: str) -> str:
    """
    Construit une URL de téléchargement publique basée sur le nom d'objet Storage.
    (attention : les ACL / règles de sécurité Firebase peuvent restreindre l'accès réel)
    """
    encoded = quote(object_name, safe="")
    return f"https://firebasestorage.googleapis.com/v0/b/{FIREBASE_STORAGE_BUCKET}/o/{encoded}?alt=media"


def update_mail_rib_document_with_ids(process_name: str, ids: List[Dict[str, Any]]) -> None:
    """
    Met à jour le document Firestore `agent_mail_rib/<process_name>` avec l'identifiant
    client/contrat extrait à partir du texte du mail.

    - `ids` est la sortie brute de `_azure_text_to_json` (liste d'objets {"id": ...}).
    - On stocke la liste complète dans `parsed_ids` et le premier id non vide
      dans `client_id` pour un accès direct.
    """
    db, _bucket = _init_firebase()
    doc_ref = db.collection(FIREBASE_COLLECTION).document(process_name)

    extracted_id: Optional[str] = None
    for item in ids:
        if isinstance(item, dict) and item.get("id"):
            extracted_id = str(item["id"])
            break

    doc_ref.update(
        {
            "parsed_ids": ids,
            "client_id": extracted_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def update_mail_rib_document_with_agent_output(process_name: str, agent_json: Any) -> None:
    """
    Met à jour le document Firestore `agent_mail_rib/<process_name>` avec le JSON
    complet retourné par l'agent RIB (AzureTextToJsonService).

    Le JSON est stocké dans le champ `agent_rib_json`.
    """
    db, _bucket = _init_firebase()
    doc_ref = db.collection(FIREBASE_COLLECTION).document(process_name)
    doc_ref.update(
        {
            "agent_rib_json": agent_json,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def download_last_mail_attachment(out_dir: Path) -> MailProcessContext:
    """
    Télécharge la pièce jointe du dernier mail reçu pour MAILBOX **et** enregistre
    les informations associées dans Firebase (Firestore + Storage).

    Étapes :
    1. Initialisation Firebase et création d'un document dans la collection
       `agent_mail_rib` avec `status="created"`.
    2. Appel Microsoft Graph pour récupérer le dernier message + sa pièce jointe.
    3. Sauvegarde de la pièce jointe en local dans `out_dir`.
    4. Upload de la pièce jointe dans le bucket `FIREBASE_STORAGE_BUCKET` dans un
       dossier contenant le nom du processus (id du document Firestore).
    5. Mise à jour du document Firestore avec :
       - `attachment_url` : URL de la pièce jointe dans le Storage
       - `mail_text` : texte du mail (corps du message)

    Retourne un `MailProcessContext` contenant le chemin local de la pièce jointe,
    le nom de processus (id du doc Firestore) et le texte du mail.
    """
    # 1) Firebase : création du document avant de récupérer le mail
    db, bucket = _init_firebase()
    collection_ref = db.collection(FIREBASE_COLLECTION)
    doc_ref = collection_ref.document()  # id auto → utilisé comme nom de processus
    process_name = doc_ref.id

    created_at = datetime.now(timezone.utc).isoformat()
    doc_ref.set(
        {
            "status": "created",
            "process_name": process_name,
            "created_at": created_at,
        }
    )

    # 2) Récupération du dernier mail via Graph
    token = get_graph_token()
    message = _get_last_message(token)
    if not message:
        raise MailServiceError(f"Aucun message trouvé pour la mailbox {MAILBOX!r}.")

    msg_id = message.get("id")
    if not msg_id:
        raise MailServiceError("Le dernier message ne contient pas de champ 'id'.")

    mail_text = _extract_mail_text_from_message(message)

    attachments = _get_attachments(msg_id, token)
    if not attachments:
        raise MailServiceError("Le dernier message ne contient aucune pièce jointe.")

    chosen = _choose_best_attachment(attachments)
    if not chosen:
        raise MailServiceError("Aucune pièce jointe exploitable trouvée sur le dernier message.")

    raw_name = chosen.get("name") or "attachment"
    filename = _safe_filename(raw_name)
    content_b64 = chosen.get("contentBytes")
    if not content_b64:
        raise MailServiceError("La pièce jointe choisie ne contient pas de 'contentBytes'.")

    try:
        content = base64.b64decode(content_b64)
    except Exception as exc:  # pragma: no cover - robustesse décodage
        raise MailServiceError(f"Impossible de décoder le contenu base64 de la pièce jointe: {exc}") from exc

    # 3) Sauvegarde locale
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / filename
    target_path.write_bytes(content)

    # 4) Upload dans Firebase Storage (dossier contenant le nom du processus)
    object_name = _build_storage_object_name(process_name, filename)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(target_path))

    attachment_url = _build_public_download_url(object_name)

    # 5) Mise à jour du document Firestore avec l'URL et le texte du mail
    doc_ref.update(
        {
            "attachment_url": attachment_url,
            "mail_text": mail_text,
            "message_id": msg_id,
            "subject": message.get("subject"),
            "received_at": message.get("receivedDateTime"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    return MailProcessContext(
        attachment_path=target_path,
        process_name=process_name,
        mail_text=mail_text,
    )



