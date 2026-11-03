"""Import en ligne de commande d'un classeur Excel (format d'origine ou v2) dans la base.

Usage (depuis backend/, ou ``python -m backend.app.import_xlsx`` depuis la racine) ::

    python -m app.import_xlsx <fichier.xlsx> [--replace]

La base est DATA_DIR/jobs.db (variable d'environnement DATA_DIR, /data par défaut).
Sans --replace, l'import échoue si un numéro de dossier du fichier existe déjà en base.
Avec --replace, la base est d'abord copiée dans DATA_DIR/backups/before-import-<horodatage>.db,
puis toutes les offres sont remplacées par celles du fichier.
Tout le fichier est validé avant la moindre écriture, et l'écriture se fait en une seule
transaction : en cas d'erreur, la base est inchangée (message en français, code de sortie 1).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .backup import backup_dir, create_backup
from .config import get_settings
from .db import connect, init_db
from .excel_import import XlsxImportError, import_parsed, read_workbook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.import_xlsx",
        description="Importe un classeur Excel de suivi d'offres (format d'origine ou v2) dans la base.",
    )
    parser.add_argument("file", type=Path, help="classeur .xlsx à importer")
    parser.add_argument(
        "--replace", action="store_true", help="remplace toutes les offres existantes (sauvegarde préalable)"
    )
    args = parser.parse_args(argv)

    if not args.file.is_file():
        print(f"Fichier introuvable : {args.file}", file=sys.stderr)
        return 2
    settings = get_settings()
    database = init_db(Path(settings.data_dir))
    try:
        offers, sheet, fmt = read_workbook(args.file)  # validation complète avant toute écriture
    except XlsxImportError as exc:
        print("Import refusé :\n" + "\n".join(f"  - {e}" for e in exc.errors), file=sys.stderr)
        return 1
    if args.replace:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        saved = create_backup(database, backup_dir(settings.data_dir) / f"before-import-{stamp}.db")
        print(f"Sauvegarde avant remplacement : {saved}")
    conn = connect(database)
    try:
        result = import_parsed(conn, offers, sheet, fmt, replace=args.replace)
    except XlsxImportError as exc:
        print("Import refusé :\n" + "\n".join(f"  - {e}" for e in exc.errors), file=sys.stderr)
        return 1
    finally:
        conn.close()
    if args.replace:
        print(f"{result.replaced} offre(s) supprimée(s).")
    print(
        f"{len(result.imported)} offre(s) importée(s) depuis la feuille « {result.sheet} » "
        f"(format {result.format}) dans {database}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
