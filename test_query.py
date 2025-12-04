import numbers
import os
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

LOGIN = os.getenv("VEOS_LOGIN")
PASSWORD = os.getenv("VEOS_PASSWORD")
LANGUAGE = "fr"  # adapte si besoin

logger.debug("LOGIN (uid) lu depuis l'environnement : %s", LOGIN)
logger.debug("LANGUAGE utilisé : %s", LANGUAGE)

# On passe directement en HTTPS pour éviter la redirection HTTP -> HTTPS
BASE_URL = "https://aiac.rec-veos.iga.fr//rs/rsExtranet2"
BOBY_BASE = f"{BASE_URL}/boBy"

session = requests.Session()
# On essaie de récupérer un token déjà présent dans l'environnement (par ex. depuis .env)
TOKEN = os.getenv("VEOS_TOKEN")
if TOKEN:
    logger.debug(
        "Token initial VEOS_TOKEN trouvé dans l'environnement (tronqué) : %s...",
        TOKEN[:10],
    )
else:
    logger.debug("Aucun token initial VEOS_TOKEN trouvé, un login sera nécessaire.")


def _persist_token_to_env_file(token: str) -> None:
    """
    Persiste le token dans le fichier .env sous la clé VEOS_TOKEN
    pour pouvoir le réutiliser aux prochains lancements du script.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    try:
        if env_path.exists():
            content = env_path.read_text()
            lines = [
                line
                for line in content.splitlines()
                if not line.startswith("VEOS_TOKEN=")
            ]
        else:
            lines = []
        lines.append(f"VEOS_TOKEN={token}")
        env_path.write_text("\n".join(lines) + "\n")
        logger.debug("Token VEOS_TOKEN persisté dans le fichier .env.")
    except Exception as e:
        logger.warning("Impossible de persister le token dans .env : %s", e)


def login_and_get_token():
    """Appelle /login et stocke le token globalement."""
    global TOKEN

    url = f"{BASE_URL}/login"
    logger.debug("Appel à /login : url=%s", url)
    payload = {
        "uid": LOGIN,
        "passwd": PASSWORD,
        "language": LANGUAGE,
    }
    # On évite de logger le mot de passe tel quel
    logger.debug(
        "Payload login (sans mot de passe) : uid=%s, language=%s",
        payload["uid"],
        payload["language"],
    )

    logger.debug("Envoi requête POST de login…")
    resp = session.post(url, json=payload, timeout=10)
    logger.debug("Réponse brute login - status=%s, body=%s", resp.status_code, resp.text)
    resp.raise_for_status()

    data = resp.json()
    # Optionnel : vérif du statusCode renvoyé par l'API
    logger.debug("JSON login parsé : %s", data)
    if data.get("statusCode") != 0:
        raise RuntimeError(
            f"Login échoué : {data.get('statusMessage')} (statusCode={data.get('statusCode')})"
        )

    TOKEN = data["token"]
    # On log uniquement le début du token pour vérifier qu'on en a bien un
    logger.debug("Token récupéré (tronqué) : %s...", TOKEN[:10])

    # Mise à jour de l'environnement pour les prochains appels / prochains lancements
    os.environ["VEOS_TOKEN"] = TOKEN
    _persist_token_to_env_file(TOKEN)

    return TOKEN


def get_headers():
    """Construit les headers avec le token (en le récupérant si besoin)."""
    if TOKEN is None:
        logger.debug("Aucun token en mémoire, on appelle login_and_get_token()")
        login_and_get_token()
    else:
        logger.debug("Réutilisation du token existant")
    return {
        "Authorization": f"Bearer {TOKEN}"
    }


def get_id_per(numper: str):
    """Appelle le boBy WS_EXT_FOASTER_CHERID_PER pour un NUMPER donné."""
    url = f"{BOBY_BASE}/WS_EXT_FOASTER_CHERID_PER"
    params = {"NUMPER": numper}

    logger.debug(
        "Appel à WS_EXT_FOASTER_CHERID_PER : url=%s, params=%s",
        url,
        params,
    )

    headers = get_headers()
    logger.debug("Headers envoyés (Authorization tronqué) : %s...", headers["Authorization"][:20])

    logger.debug("Envoi requête GET WS_EXT_FOASTER_CHERID_PER…")
    resp = session.get(url, params=params, headers=headers, timeout=10)
    logger.debug(
        "Réponse initiale WS_EXT_FOASTER_CHERID_PER - status=%s, body=%s",
        resp.status_code,
        resp.text,
    )

    # Si le token a expiré, on réessaie une fois après relogin
    if resp.status_code == 401:
        logger.warning("Token expiré (401). On relance un login puis on retente l'appel.")
        login_and_get_token()
        headers = get_headers()
        logger.debug(
            "Nouvelle tentative WS_EXT_FOASTER_CHERID_PER avec nouveau token, headers (tronqués) : %s...",
            headers["Authorization"][:20],
        )
        resp = session.get(url, params=params, headers=headers, timeout=10)
        logger.debug(
            "Réponse après renouvellement de token - status=%s, body=%s",
            resp.status_code,
            resp.text,
        )

    resp.raise_for_status()

    logger.info("Status final WS_EXT_FOASTER_CHERID_PER : %s", resp.status_code)
    logger.info("Taille du corps de réponse : %d caractères", len(resp.text))
    logger.debug("Corps de réponse complet : %s", resp.text)

    try:
        data = resp.json()
        logger.debug("JSON WS_EXT_FOASTER_CHERID_PER parsé : %s", data)
        return data
    except ValueError:
        logger.warning(
            "Réponse WS_EXT_FOASTER_CHERID_PER non JSON, on renvoie le texte brut."
        )
        return {"raw": resp.text}


def create_person(person_data: dict):
    """
    Appelle POST /personne pour créer une personne.
    Retourne la réponse JSON parsée (ou lève une erreur si non JSON).
    """
    url = f"{BASE_URL}/personne"
    headers = get_headers()
    logger.debug("Appel à POST /personne : url=%s", url)
    logger.debug("Payload personne : %s", person_data)

    resp = session.post(url, json=person_data, headers=headers, timeout=10)
    logger.debug(
        "Réponse brute création personne - status=%s, body=%s",
        resp.status_code,
        resp.text,
    )

    if resp.status_code == 401:
        logger.warning(
            "Token expiré (401) lors de POST /personne. On relance un login puis on retente."
        )
        login_and_get_token()
        headers = get_headers()
        resp = session.post(url, json=person_data, headers=headers, timeout=10)
        logger.debug(
            "Réponse POST /personne après renouvellement de token - status=%s, body=%s",
            resp.status_code,
            resp.text,
        )

    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        logger.error("Réponse création personne non JSON, contenu : %s", resp.text)
        raise

    logger.info("Création personne réussie, réponse JSON : %s", data)
    return data


def get_person_by_id(person_id: str):
    """
    Appelle GET /personne/{id} pour récupérer les informations d'une personne.
    """
    url = f"{BASE_URL}/personne/{person_id}"
    headers = get_headers()
    logger.debug("Appel à GET /personne/{id} : url=%s", url)

    resp = session.get(url, headers=headers, timeout=10)
    logger.debug(
        "Réponse brute GET personne - status=%s, body=%s",
        resp.status_code,
        resp.text,
    )

    if resp.status_code == 401:
        logger.warning(
            "Token expiré (401) lors de GET /personne/%s. On relance un login puis on retente.",
            person_id,
        )
        login_and_get_token()
        headers = get_headers()
        resp = session.get(url, headers=headers, timeout=10)
        logger.debug(
            "Réponse GET /personne/%s après renouvellement de token - status=%s, body=%s",
            person_id,
            resp.status_code,
            resp.text,
        )

    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        logger.error("Réponse GET /personne/%s non JSON, contenu : %s", person_id, resp.text)
        raise

    logger.info("Récupération personne par id réussie, réponse JSON : %s", data)
    return data


def update_person(person_id: str, person_data: dict):
    """
    Appelle PUT /personne/{id} pour modifier une personne existante.
    On doit renvoyer l'intégralité du payload de la personne.
    """
    url = f"{BASE_URL}/personne/{person_id}"
    headers = get_headers()
    logger.debug("Appel à PUT /personne/{id} : url=%s", url)
    logger.debug("Payload personne (mise à jour) : %s", person_data)

    resp = session.put(url, json=person_data, headers=headers, timeout=10)
    logger.debug(
        "Réponse brute PUT personne - status=%s, body=%s",
        resp.status_code,
        resp.text,
    )

    if resp.status_code == 401:
        logger.warning(
            "Token expiré (401) lors de PUT /personne/%s. On relance un login puis on retente.",
            person_id,
        )
        login_and_get_token()
        headers = get_headers()
        resp = session.put(url, json=person_data, headers=headers, timeout=10)
        logger.debug(
            "Réponse PUT /personne/%s après renouvellement de token - status=%s, body=%s",
            person_id,
            resp.status_code,
            resp.text,
        )

    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        logger.error("Réponse PUT /personne/%s non JSON, contenu : %s", person_id, resp.text)
        raise

    logger.info("Mise à jour personne réussie, réponse JSON : %s", data)
    return data


# Exemple d'appels enchaînés :
# 1. Création d'une personne via POST /personne
# 2. Récupération de l'id via le boBy WS_EXT_FOASTER_CHERID_PER (si possible)
# 3. Récupération des infos de la personne via GET /personne/{id}
if __name__ == "__main__":
    person_payload = {
    "type": "P",
    "nom": "COMBES",
    "prenom": "Alexandre",
    "categorie": "CLIENT",
    "cdLangue": "FR",
    "cdMonnaie": "EUR",
    "cdTitre": "M",
    "mail": "alexandre.combes@foaster.ai",
    "tel": "0142000000",
    "mobile": "0601000203",
    "login": "alexandre.combes",
    "pass": "Foaster",
    "sms": "1",

    "persph": {
        "sexe": "M",
        "situationFamille": "CEL",
        "situationProfession": "SAL",
        "codeProfession": "DEVIA"
    },

    "compte": {
        "numCompte": "4000001",
        "numSousCompte": "000",
        "numSociete": "FOASTER",
        "libCompte": "Compte client Foaster",
        "cdCompte": "CLI_FOASTER"
    },


    # Tableau de RIBs (même structure, 8 champs)
    "rib": 
        {
            "bic": "SOGEFRPP",
            "iban": "FR7630003038580005025916860",
            "sens": "2",
            "cdBanque": "30003",
            "nomBanque": "Banque Foaster",
            "titulaire": "ALEXANDRE COMBES - FOASTER",
        }
    ,

    "adresses": [
        {
            "adresse1": "Foaster",
            "adresse2": "12 Rue des Startups",
            "cp": "75010",
            "ville": "PARIS",
            "cdPays": "FR",
            "categorie": "PRO",
            "principale": "O",
            "envoi": "O",
            "idAdr": None
        }
    ],

    "listInfos": [
        {"key": "origine_client", "value": "Foaster - démo API"},
        {"key": "societe", "value": "Foaster"}
    ]
}


    logger.info("=== Étape 1 : création de la personne via POST /personne ===")
    # creation_response = create_person(person_payload)

    # logger.info("Réponse création personne : %s", creation_response)

    # On essaie de récupérer un numéro de personne renvoyé par l'API
    # numper = (
    #     creation_response.get("numper")
    #     or creation_response.get("NUMPER")
    #     or creation_response.get("numPer")
    #     or creation_response.get("num")
    # )
    numper='ASS966'
    logger.info("Numéro de personne récupéré après création : %s", numper)

    person_id = None

    if numper:
        logger.info(
            "=== Étape 2 : appel du boBy WS_EXT_FOASTER_CHERID_PER pour récupérer l'id interne ==="
        )
        id_per_response = get_id_per(numper)
        logger.info("Réponse WS_EXT_FOASTER_CHERID_PER : %s", id_per_response)

        # Tentative générique d'extraction d'un identifiant depuis la réponse boBy
        try:
            beans = id_per_response.get("beans") or []
            if beans:
                first_bean = beans[0]
                person_id = (
                    first_bean.get("id")
                    or first_bean.get("idPer")
                    or first_bean.get("id_personne")
                    or first_bean.get("ID_PER")
                )
                logger.info("Identifiant de personne extrait depuis le boBy : %s", person_id)
            else:
                logger.warning(
                    "Aucun bean dans la réponse boBy, impossible d'en extraire un id."
                )
        except AttributeError:
            logger.warning(
                "Réponse boBy inattendue (non dict JSON ?), impossible d'en extraire un id."
            )
    else:
        logger.warning(
            "Aucun numéro de personne trouvé dans la réponse de création, on ne peut pas appeler le boBy."
        )

    if person_id:
        logger.info("=== Étape 3 : récupération des infos de la personne via GET /personne/{id} ===")
        person_details = get_person_by_id(str(person_id))
        logger.info("Détails de la personne récupérés : %s", person_details)

        # Étape 4a : test PUT avec le payload IDENTIQUE à celui du GET
        if not isinstance(person_details, dict):
            logger.warning(
                "Détails de la personne inattendus (non dict JSON), PUT /personne/{id} ignoré."
            )
        else:
            logger.info("=== Étape 4a : PUT /personne/{id} avec le payload EXACT du GET ===")
            put_identique_ok = True
            try:
                same_payload_response = update_person(str(person_id), person_details)
                logger.info(
                    "Réponse PUT (payload identique au GET) : %s", same_payload_response
                )
            except requests.exceptions.HTTPError as e:
                logger.error(
                    "PUT /personne/{id} avec payload identique au GET a échoué : %s",
                    e,
                )
                # On s'arrête là pour le diagnostic, on ne tente pas de modifier rib/ribs
                put_identique_ok = False

            if put_identique_ok:
                # Étape 4b : test de mise à jour de rib / ribs uniquement si le PUT identique passe
                # On repart du payload complet renvoyé par l'API
                updated_person = dict(person_details)

                # On part de la structure EXACTE renvoyée par l'API pour rib / ribs
                api_rib = person_details.get("rib") or {}
                api_ribs = person_details.get("ribs") or []

                logger.debug("rib renvoyé par l'API avant modification : %s", api_rib)
                logger.debug("ribs renvoyés par l'API avant modification : %s", api_ribs)

                # On clone le rib API et on ne modifie que les champs demandés,
                # pour garder exactement les mêmes "colonnes" côté serveur.
                new_rib = dict(api_rib)
                new_rib.update(
                    {
                        "id": "11006200",
                        "iban": "FR7630003038580005025916860",
                        "bic": "SOGEFRPP",
                        "sens": "2",
                        "cdBanque": None,
                        "nomBanque": "SG",
                        "titulaire": "ALEXANDRE COMBES",
                        "dtInactif": None,
                        "idPer": None,
                        "currencyCode": None,
                        "dtCreation": "04/12/2025",
                    }
                )

                # Mise à jour de rib
                updated_person["rib"] = new_rib

                # Mise à jour de ribs : on prend la même structure tableau que l'API,
                # en remplaçant la première entrée par new_rib (et en gardant les autres si elles existent)
                new_ribs = []
                if api_ribs:
                    # On remplace seulement le premier élément pour limiter les risques
                    first = dict(api_ribs[0])
                    first.update(new_rib)
                    new_ribs.append(first)
                    # On garde les autres tels quels
                    if len(api_ribs) > 1:
                        new_ribs.extend(api_ribs[1:])
                else:
                    # Pas de tableau à l'origine : on en crée un avec new_rib
                    new_ribs = [new_rib]

                updated_person["ribs"] = new_ribs

                logger.debug("rib après modification : %s", updated_person.get("rib"))
                logger.debug("ribs après modification : %s", updated_person.get("ribs"))

                logger.info("=== Étape 4b : mise à jour de la personne via PUT /personne/{id} avec rib/ribs modifiés ===")
                update_response = update_person(str(person_id), updated_person)
                logger.info("Réponse mise à jour personne : %s", update_response)
    else:
        logger.warning(
            "Aucun id de personne déterminé, les appels GET /personne/{id} et PUT /personne/{id} sont ignorés."
        )


