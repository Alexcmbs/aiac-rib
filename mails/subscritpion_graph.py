# create_subscription.py
from datetime import datetime, timedelta, timezone
import requests
from graph_client import get_token, MAILBOX
from main import EXPECTED_CLIENT_STATE  # ou duplique la valeur
import os
try:
    # Chargement automatique du fichier .env s'il existe
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore
    find_dotenv = None  # type: ignore

load_dotenv()
NOTIFICATION_URL = os.getenv("NOTIFICATION_URL")

def create_subscription():
    token = get_token()
    expiration = datetime.now(timezone.utc) + timedelta(hours=1)  # ou max autorisé

    body = {
        "changeType": "created",
        "notificationUrl": NOTIFICATION_URL,
        "resource": f"/users/{MAILBOX}/mailFolders('inbox')/messages",
        "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
        "clientState": EXPECTED_CLIENT_STATE,
    }

    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    print(resp.status_code, resp.json())

if __name__ == "__main__":
    create_subscription()
