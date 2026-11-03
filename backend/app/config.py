"""Configuration lue depuis les variables d'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"La variable {name} doit être un entier (reçu : {raw!r}).") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on", "oui"):
        return True
    if value in ("0", "false", "no", "off", "non"):
        return False
    raise ValueError(f"La variable {name} doit valoir true ou false (reçu : {raw!r}).")


def _str_env(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


@dataclass(frozen=True)
class Settings:
    api_token: str | None
    data_dir: Path
    follow_up_days: int
    backup_keep_days: int
    nextcloud_url: str | None
    nextcloud_root: str
    # Dossier du front compilé servi en statique ; None = backend/app/static s'il existe.
    frontend_dir: Path | None = None
    # WebDAV Nextcloud (phase 3, D-003) : désactivé par défaut. Le mot de passe d'application
    # n'apparaît jamais dans repr() (ni donc dans les journaux qui affichent les réglages).
    nextcloud_webdav: bool = False
    nextcloud_user: str | None = None
    nextcloud_app_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            api_token=_str_env("API_TOKEN"),
            data_dir=Path(_str_env("DATA_DIR", "/data")),
            follow_up_days=_int_env("FOLLOW_UP_DAYS", 14),
            backup_keep_days=_int_env("BACKUP_KEEP_DAYS", 14),
            nextcloud_url=_str_env("NEXTCLOUD_URL"),
            nextcloud_root=_str_env("NEXTCLOUD_ROOT", "/Candidatures"),
            frontend_dir=Path(p) if (p := _str_env("FRONTEND_DIR")) else None,
            nextcloud_webdav=_bool_env("NEXTCLOUD_WEBDAV", False),
            nextcloud_user=_str_env("NEXTCLOUD_USER"),
            nextcloud_app_password=_str_env("NEXTCLOUD_APP_PASSWORD"),
        )


def get_settings() -> Settings:
    """Relit l'environnement à chaque appel (pratique pour les tests)."""
    return Settings.from_env()
