"""Schémas Pydantic v2 de l'API (validation stricte, messages d'erreur en français).

- champs inconnus refusés ; textes et booléens stricts (pas de conversion 1 -> True) ;
- department : chaîne du référentiel ("2A", "01"…), un nombre JSON est refusé ;
- country : 2 lettres, mis en majuscules, vide ou null = "FR" ;
- links : liste d'URL http(s).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from . import geo

STATUSES: tuple[str, ...] = ("À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon")
# Libellé d'affichage d'un statut absent (null) dans les statistiques et les filtres.
STATUS_UNSET = "À qualifier"
# Plus grand numéro de dossier accepté (explicite à la création, ou dans le chemin).
# Borne large mais très en dessous de 2**63 : un id proche du maximum SQLite rendrait
# AUTOINCREMENT inutilisable (SQLITE_FULL) pour toutes les créations suivantes.
MAX_OFFER_ID = 10**9
# Libellé d'une offre en France sans département (région inconnue).
NO_DEPARTMENT = "Sans département"

TEXT_FIELDS = (
    "title",
    "contract_type",
    "company",
    "location",
    "main_issue",
    "rejection_reason",
    "source",
    "deadline_note",
    "description",
)
BOOL_FIELDS = ("application_sent", "follow_up_done")


_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}([T ].*)?", re.DOTALL)


def check_date(value: object) -> object:
    """Date stricte : texte AAAA-MM-JJ (ou objet date) ; nombres et horodatages refusés."""
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"Date invalide : {value!r} (format AAAA-MM-JJ attendu).")
    return value


def check_datetime(value: object) -> object:
    """Date et heure ISO en texte (ou objet datetime) ; un nombre (horodatage Unix) est refusé."""
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value):
        raise ValueError(
            f"date et heure invalides : {value!r} (format ISO attendu, ex. 2025-01-31T10:00:00)."
        )
    return value


def check_status(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in STATUSES:
        raise ValueError(f"Statut inconnu : {value!r}. Valeurs possibles : {', '.join(STATUSES)} (ou null).")
    return value


def check_department(value: object) -> str | None:
    if value is None or value == "":
        return None
    return geo.validate_department(value)


def check_country(value: object) -> str:
    if value is None:
        return geo.FRANCE
    if not isinstance(value, str):
        raise ValueError(f"Le pays doit être un code ISO de 2 lettres (reçu : {value!r}).")
    code = value.strip().upper()
    if code == "":
        return geo.FRANCE
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        raise ValueError(f"Le pays doit être un code ISO de 2 lettres, ex. FR, CH (reçu : {value!r}).")
    return code


def check_links(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Les liens doivent être une liste d'URL.")
    links: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"Lien invalide : {item!r} (une URL texte est attendue).")
        url = item.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c.isspace() for c in url):
            raise ValueError(f"Lien invalide : {item!r} (URL http:// ou https:// attendue).")
        links.append(url)
    return links


class _OfferFields(BaseModel):
    """Champs modifiables d'une offre ; tous facultatifs."""

    model_config = ConfigDict(extra="forbid")

    title: StrictStr | None = None
    contract_type: StrictStr | None = None
    company: StrictStr | None = None
    location: StrictStr | None = None
    department: str | None = None
    country: str = geo.FRANCE
    status: str | None = None
    main_issue: StrictStr | None = None
    rejection_reason: StrictStr | None = None
    source: StrictStr | None = None
    applied_at: datetime | None = None
    application_sent: StrictBool = False
    follow_up_done: StrictBool = False
    deadline: date | None = None
    deadline_note: StrictStr | None = None
    description: StrictStr | None = None
    links: list[str] = Field(default_factory=list)

    _status = field_validator("status", mode="before")(check_status)
    _department = field_validator("department", mode="before")(check_department)
    _country = field_validator("country", mode="before")(check_country)
    _links = field_validator("links", mode="before")(check_links)

    # Pas de mode « lax » de Pydantic : 86400 ou "1700006400" seraient lus comme horodatages.
    _deadline = field_validator("deadline", mode="before")(check_date)
    _applied_at = field_validator("applied_at", mode="before")(check_datetime)


class OfferCreate(_OfferFields):
    """Création : id explicite permis (import, reprise d'un numéro de dossier)."""

    id: StrictInt | None = Field(default=None, ge=1, le=MAX_OFFER_ID)


class OfferReplace(_OfferFields):
    """Remplacement complet (PUT) : les champs absents reprennent leur valeur par défaut."""


class OfferUpdate(_OfferFields):
    """Mise à jour partielle (PATCH) : seuls les champs fournis changent ; null efface.

    Pour les booléens, null est refusé (un booléen n'est jamais vide).
    """

    def changes(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.model_fields_set}

    @field_validator(*BOOL_FIELDS, mode="before")
    @classmethod
    def _no_null_bool(cls, value: object) -> object:
        if value is None:
            raise ValueError("Ce champ booléen ne peut pas être null (true ou false attendu).")
        return value


class OfferOut(BaseModel):
    """Offre renvoyée par l'API, avec les champs calculés à la lecture (jamais stockés)."""

    id: int
    title: str | None
    contract_type: str | None
    company: str | None
    location: str | None
    department: str | None
    country: str
    region: str | None
    status: str | None
    main_issue: str | None
    rejection_reason: str | None
    source: str | None
    applied_at: datetime | None
    application_sent: bool
    follow_up_done: bool
    deadline: date | None
    deadline_note: str | None
    description: str | None
    links: list[str]
    created_at: datetime
    updated_at: datetime
    days_left: int | None
    follow_up_suggested: bool


class DepartmentOut(BaseModel):
    code: str
    name: str
    region: str


class MetaOut(BaseModel):
    statuses: list[str]
    status_unset_label: str
    regions: list[str]
    out_of_france_label: str
    no_department_label: str
    departments: list[DepartmentOut]
    follow_up_days: int
    # Phase 3 (D-003) : lien WebDAV avec Nextcloud actif et configuration valide.
    nextcloud_enabled: bool = False
    # Message en français si NEXTCLOUD_WEBDAV est activé mais la configuration est invalide.
    nextcloud_error: str | None = None


# --- Dossiers Nextcloud (phase 3, D-003) ------------------------------------------------------

# Issue de la création du dossier lors de POST /api/offers.
FOLDER_DISABLED = "disabled"  # NEXTCLOUD_WEBDAV désactivé ou incomplet : aucun appel réseau
FOLDER_CREATED = "created"  # dossier créé (avec CV/ et Lettre/)
FOLDER_EXISTING = "existing"  # un dossier portant ce numéro existait : réutilisé tel quel
FOLDER_ERROR = "error"  # échec (Nextcloud injoignable, configuration invalide…) ; offre créée


class FolderFileOut(BaseModel):
    name: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    url: str


class FolderOut(BaseModel):
    """Dossier Nextcloud d'une offre (GET/POST /api/offers/{id}/folder)."""

    name: str
    url: str
    files: list[FolderFileOut]
    created: bool = False


class FolderOutcome(BaseModel):
    """Résultat de la création automatique du dossier à la création d'une offre."""

    status: str  # disabled | created | existing | error
    name: str | None = None
    url: str | None = None
    detail: str | None = None


class OfferCreatedOut(OfferOut):
    """Réponse de POST /api/offers : l'offre, plus l'issue de la création de son dossier."""

    folder: FolderOutcome


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_region: dict[str, int]
    deadlines_soon: list[OfferOut]
