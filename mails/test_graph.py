import requests
import os
try:
    # Chargement automatique du fichier .env s'il existe
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore
    find_dotenv = None  # type: ignore

load_dotenv()
TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_AIAC_MAIL_APP_ID")
CLIENT_SECRET = os.getenv("AZURE_AIAC_MAIL_APP_SECRET")
MAILBOX = "alexandre.combes@foaster.ai"

def get_token():
    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

token = get_token()

resp = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages?$top=5",
    headers={"Authorization": f"Bearer {token}"},
)
print(resp.status_code, resp.json())
