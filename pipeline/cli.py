import argparse
import asyncio
import sys
from pathlib import Path

from .config import load_config
from .orchestrator import run_latest_mail_attachment_pipeline, run_pdf_pipeline

try:
    # Chargement automatique du fichier .env s'il existe
    from dotenv import load_dotenv, find_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore
    find_dotenv = None  # type: ignore


def find_documents(input_dir: str):
    """
    Retourne tous les fichiers supportés dans le dossier d'entrée :
    - PDF
    - Images: JPG, JPEG, PNG
    """
    root = Path(input_dir).expanduser().resolve()
    exts = {".pdf", ".jpg", ".jpeg", ".png"}
    return [p for p in root.rglob("**/*") if p.is_file() and p.suffix.lower() in exts]


def main() -> None:
    # Charger .env avant toute lecture d'os.getenv (config/services)
    if load_dotenv and find_dotenv:
        try:
            load_dotenv(find_dotenv(usecwd=True), override=False)
        except Exception:
            # On ignore silencieusement si le chargement échoue
            pass

    parser = argparse.ArgumentParser(description="Pipeline: PDF/Images → TXT/JSON (OCR Azure) ou depuis le dernier mail.")
    parser.add_argument(
        "--input",
        required=False,
        help="Dossier d'entrée contenant des PDF ou des images (JPG/PNG). Ignoré si --from-mail est utilisé.",
    )
    parser.add_argument(
        "--from-mail",
        action="store_true",
        help="Traite la dernière pièce jointe RIB reçue par mail (pipeline mail + OCR + agent).",
    )
    parser.add_argument("--out-root", required=False, help="Dossier racine de sortie (défaut: uploads)")
    parser.add_argument("--ocr-backend", required=False, default="azure", help="Backend OCR (par défaut: azure)")
    parser.add_argument("--mapping-backend", required=False, default="hyperbolic", help="Backend mapping (hyperbolic|azure)")
    parser.add_argument("--dpi", required=False, type=int, default=None, help="DPI pour OCR (défaut via env VLM_DPI=200)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip si final existe")
    args = parser.parse_args()

    cfg = load_config(
        out_root=args.out_root,
        ocr_backend=args.ocr_backend,
        mapping_backend=args.mapping_backend,
        dpi=args.dpi,
        skip_existing=args.skip_existing,
    )
    # Mode 1 : traitement depuis le dernier mail (pipeline complète mail + RIB)
    if args.from_mail:
        try:
            print("▶️ Lancement de la pipeline depuis le dernier mail (création doc Firebase, lecture mail, OCR, agent RIB)...")
            report = asyncio.run(run_latest_mail_attachment_pipeline(cfg))
            print(f"✅ Pipeline mail+RIB terminée. Dossier de process: {report.process_dir}")
        except KeyboardInterrupt:
            print("Interrompu par l'utilisateur.")
            sys.exit(130)
        except Exception as e:
            print(f"❌ Échec pipeline mail+RIB → {e}")
            sys.exit(1)
        return

    # Mode 2 : traitement classique d'un dossier de PDF/Images
    if not args.input:
        print("Erreur: --input est obligatoire sauf si vous utilisez --from-mail.")
        sys.exit(1)

    docs = find_documents(args.input)
    if not docs:
        print("Aucun fichier PDF/JPG/PNG trouvé.")
        sys.exit(0)

    print(f"{len(docs)} fichier(s) (PDF/JPG/PNG) détecté(s) → sortie: {cfg.out_root}")
    for i, pdf in enumerate(docs, start=1):
        try:
            print(f"\n[{i}/{len(docs)}] {pdf}")
            asyncio.run(run_pdf_pipeline(str(pdf), cfg))
        except KeyboardInterrupt:
            print("Interrompu par l'utilisateur.")
            sys.exit(130)
        except Exception as e:
            print(f"❌ Échec: {pdf} → {e}")


if __name__ == "__main__":
    main()


