"""Accès SQLite : fabrique de connexions (WAL, clés étrangères) et migrations versionnées.

Les migrations sont les fichiers ``migrations/NNNN_*.sql``, appliquées dans l'ordre ; la
version courante est suivie par ``PRAGMA user_version``. ``migrate`` est idempotent.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_FILENAME = "jobs.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[\w-]+\.sql$")


def db_path(data_dir: Path) -> Path:
    """Chemin du fichier de base : DATA_DIR/jobs.db."""
    return Path(data_dir) / DB_FILENAME


def _casefold(value: object) -> str | None:
    return None if value is None else str(value).casefold()


def connect(path: Path) -> sqlite3.Connection:
    """Ouvre une connexion configurée (WAL, foreign_keys, sqlite3.Row, py_casefold)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout=10 règle déjà busy_timeout à 10 000 ms : pas de PRAGMA en double.
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # LOWER() de SQLite ignore les lettres accentuées : recherche insensible à la casse en Python.
    conn.create_function("py_casefold", 1, _casefold, deterministic=True)
    return conn


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """Migrations disponibles, triées par numéro ; un numéro en double est une erreur."""
    found: dict[int, Path] = {}
    for file in sorted(directory.glob("*.sql")):
        match = _MIGRATION_RE.match(file.name)
        if not match:
            raise ValueError(f"Nom de migration invalide : {file.name} (attendu : NNNN_nom.sql)")
        number = int(match.group(1))
        if number in found:
            raise ValueError(f"Numéro de migration en double : {number:04d}")
        found[number] = file
    return sorted(found.items())


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> int:
    """Applique les migrations manquantes, chacune dans sa transaction. Renvoie la version finale."""
    current = schema_version(conn)
    for number, file in list_migrations(directory):
        if number <= current:
            continue
        script = file.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        current = number
    return current


def init_db(data_dir: Path) -> Path:
    """Crée DATA_DIR si besoin et met le schéma à jour. Renvoie le chemin de la base."""
    path = db_path(data_dir)
    conn = connect(path)
    try:
        migrate(conn)
    finally:
        conn.close()
    return path
