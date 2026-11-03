"""Sauvegarde quotidienne de la base (API backup de sqlite3).

Fichiers : DATA_DIR/backups/jobs-AAAA-MM-JJ.db ; on garde BACKUP_KEEP_DAYS jours (le jour
courant compris), les plus anciens sont supprimés. ``run_daily_backup`` est appelable
directement (tests) ; le lifespan l'appelle au démarrage puis
``backup_loop`` toutes les 24 h.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .db import db_path

log = logging.getLogger(__name__)

BACKUP_DIRNAME = "backups"
_BACKUP_RE = re.compile(r"^jobs-(\d{4}-\d{2}-\d{2})\.db$")
INTERVAL_SECONDS = 24 * 60 * 60


def backup_dir(data_dir: Path) -> Path:
    return Path(data_dir) / BACKUP_DIRNAME


def backup_file(data_dir: Path, day: date) -> Path:
    return backup_dir(data_dir) / f"jobs-{day.isoformat()}.db"


def create_backup(source: Path, target: Path) -> Path:
    """Copie cohérente de ``source`` vers ``target`` (fichier temporaire puis renommage)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    tmp.replace(target)
    return target


def prune_backups(data_dir: Path, keep_days: int, today: date) -> list[Path]:
    """Supprime les sauvegardes datées d'avant les ``keep_days`` derniers jours. Renvoie les supprimées."""
    limit = today - timedelta(days=max(keep_days, 1) - 1)
    removed = []
    directory = backup_dir(data_dir)
    if not directory.is_dir():
        return removed
    for file in directory.iterdir():
        match = _BACKUP_RE.match(file.name)
        if not match:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day < limit:
            file.unlink()
            removed.append(file)
    return removed


def run_daily_backup(data_dir: Path, keep_days: int, today: date | None = None) -> Path | None:
    """Crée la sauvegarde du jour si elle manque, puis élague. Renvoie le fichier créé ou None."""
    today = today or date.today()
    source = db_path(data_dir)
    created = None
    target = backup_file(data_dir, today)
    if source.is_file() and not target.exists():
        created = create_backup(source, target)
        log.info("Sauvegarde créée : %s", created)
    prune_backups(data_dir, keep_days, today)
    return created


def safe_daily_backup(data_dir: Path, keep_days: int) -> Path | None:
    """Comme run_daily_backup, mais une erreur est journalisée au lieu d'arrêter le service."""
    try:
        return run_daily_backup(data_dir, keep_days)
    except Exception:
        log.exception("Échec de la sauvegarde quotidienne")
        return None


async def backup_loop(data_dir: Path, keep_days: int, interval: float = INTERVAL_SECONDS) -> None:
    """Toutes les ``interval`` secondes, sauvegarde du jour si elle manque (tâche du lifespan ;
    la sauvegarde de démarrage est faite avant, par le lifespan lui-même)."""
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(safe_daily_backup, data_dir, keep_days)
