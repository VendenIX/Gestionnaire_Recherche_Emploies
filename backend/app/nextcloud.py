"""Client WebDAV Nextcloud minimal (bibliothèque standard uniquement) — D-003.

Chaque offre a un dossier sous ``NEXTCLOUD_ROOT`` nommé « NN - <intitulé> <entreprise> »
(noms du propriétaire conservés). Le dossier d'une offre est retrouvé par son numéro, quel que
soit le remplissage de zéros. Le module ne fait que lire (PROPFIND) et créer (MKCOL) : il ne
renomme, ne déplace, n'écrase ni ne supprime jamais rien.

Création (``create_offer_folder``) : un dossier neuf reçoit CV/ et Lettre/ ; un dossier existant
n'est jamais renommé, seuls CV/ ou Lettre/ manquants y sont ajoutés (PROPFIND du dossier d'abord,
nom comparé sans tenir compte de la casse ; un fichier ou dossier déjà présent n'est pas touché).

Délai : chaque requête WebDAV attend au plus ``NextcloudConfig.timeout`` (``DEFAULT_TIMEOUT``,
10 s par requête WebDAV, routes /folder). La création automatique à POST /api/offers utilise un
client copié par ``with_timeout(OFFER_CREATE_TIMEOUT)`` (5 s par requête WebDAV, pas au total :
jusqu'à 4-5 requêtes pour un dossier existant) pour qu'un Nextcloud lent ne bloque pas le
formulaire. Sur un dossier existant, l'ajout de CV/ ou Lettre/ est au mieux : un échec (hors
authentification) est journalisé et le dossier est renvoyé tel quel.

Formes de noms reconnues (numéro en chiffres ASCII 0-9, zéros de tête quelconques), suivi de la
fin du nom, d'un espace, d'un tiret ASCII « - » ou d'un souligné :
« 1 - x », « 01 - x », « 0017 - x », « 01-x », « 01_x », « 01 x », « 01 ».
Non reconnues : « 01. x », « 01–x » (tiret demi-cadratin), « 01x », chiffres non ASCII
(« ٠١ - x », « ０１ - x »). « 1 » ne correspond jamais à « 10 - x ».
"""

from __future__ import annotations

import base64
import http.client
import logging
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Seules méthodes HTTP que ce module a le droit d'envoyer.
ALLOWED_METHODS = frozenset({"PROPFIND", "MKCOL"})
SUBFOLDERS = ("CV", "Lettre")
# Délai par requête WebDAV (secondes) : routes /folder, et création automatique avec une offre.
DEFAULT_TIMEOUT = 10.0
OFFER_CREATE_TIMEOUT = 5.0
MAX_NAME_CHARS = 120
MAX_NAME_BYTES = 240

_DAV = "{DAV:}"
_OC = "{http://owncloud.org/ns}"
_PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop>'
    b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><oc:fileid/><oc:size/>"
    b"</d:prop></d:propfind>"
)

# Numéro en tête du nom : chiffres puis fin, espace, tiret ou souligné ("01 - …", "01-…", "01").
_NUMBER_RE = re.compile(r"^([0-9]+)(?=$|[\s\-_])")
_DAV_MARKER = "/remote.php/dav/files/"


class NextcloudError(Exception):
    """Erreur Nextcloud, message en français, sans identifiants."""


class NextcloudUnreachable(NextcloudError):
    pass


class NextcloudConfigError(NextcloudError):
    pass


class NextcloudAuthError(NextcloudError):
    pass


class NextcloudRootMissing(NextcloudError):
    pass


class AmbiguousFolderError(NextcloudError):
    def __init__(self, offer_id: int, names: list[str]):
        self.offer_id = offer_id
        self.names = names
        listed = ", ".join(f"« {n} »" for n in names)
        super().__init__(
            f"Plusieurs dossiers Nextcloud correspondent à l'offre n° {offer_id} : {listed}. "
            "Renommez ou déplacez l'un d'eux dans Nextcloud."
        )


@dataclass(frozen=True)
class DavEntry:
    name: str
    is_dir: bool
    size: int | None = None
    modified: datetime | None = None
    file_id: str | None = None


class FolderCreation(NamedTuple):
    """Issue de create_offer_folder : « created » ne concerne que le dossier de l'offre."""

    name: str
    created: bool
    subfolders_added: tuple[str, ...] = ()


@dataclass(frozen=True)
class NextcloudConfig:
    url: str
    user: str
    password: str = field(repr=False)
    root: str = "/Candidatures"
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        u = urllib.parse.urlsplit(self.url or "")
        if u.scheme not in ("http", "https") or not u.hostname:
            raise NextcloudConfigError(
                "NEXTCLOUD_URL doit être une adresse complète commençant par http:// ou https:// "
                "(ex. http://nextcloud.local:8080)."
            )
        try:
            _ = u.port
        except ValueError:
            raise NextcloudConfigError("NEXTCLOUD_URL contient un port invalide.") from None

    @property
    def public_url(self) -> str:
        """scheme://hôte[:port], sans identifiants ni chemin (pour les messages)."""
        u = urllib.parse.urlsplit(self.url)
        host = f"[{u.hostname}]" if ":" in (u.hostname or "") else u.hostname
        return f"{u.scheme}://{host}" + (f":{u.port}" if u.port else "")

    @classmethod
    def from_settings(cls, settings: Any) -> NextcloudConfig | None:
        """Configuration WebDAV, ou None si NEXTCLOUD_WEBDAV est désactivé ou incomplet."""
        if not getattr(settings, "nextcloud_webdav", False):
            return None
        url = settings.nextcloud_url
        user = settings.nextcloud_user
        password = settings.nextcloud_app_password
        if not (url and user and password):
            return None
        return cls(url=url, user=user, password=password, root=settings.nextcloud_root)


# --- fonctions pures -------------------------------------------------------------------------


def normalize_root(root: str) -> str:
    """« Documents/Candidatures/ » -> « /Documents/Candidatures » ; racine vide -> « »."""
    parts = [p for p in root.split("/") if p]
    return "/" + "/".join(parts) if parts else ""


def folder_number(name: str) -> int | None:
    """Numéro d'offre en tête d'un nom de dossier, ou None."""
    m = _NUMBER_RE.match(name)
    return int(m.group(1)) if m else None


def _field(offer: Any, key: str) -> Any:
    if isinstance(offer, Mapping):
        return offer.get(key)
    return getattr(offer, key, None)


def sanitize_name(name: str) -> str:
    """Retire ce que Nextcloud refuse dans un nom (/, \\, contrôles, points et espaces aux bords)."""
    cleaned = "".join(
        " " if unicodedata.category(c).startswith("C") or c in "/\\" else c for c in name
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:MAX_NAME_CHARS]
    while len(cleaned.encode("utf-8")) > MAX_NAME_BYTES:
        cleaned = cleaned[:-1]
    return cleaned.strip(" .")


def folder_name_for(offer: Any) -> str:
    """Nom du dossier d'une nouvelle offre : « NN - <intitulé> <entreprise> » (2 chiffres minimum)."""
    offer_id = int(_field(offer, "id"))
    if offer_id <= 0:
        raise ValueError(f"Numéro d'offre invalide pour un dossier Nextcloud : {offer_id}.")
    label = " ".join(
        str(v).strip() for v in (_field(offer, "title"), _field(offer, "company")) if v and str(v).strip()
    )
    prefix = f"{offer_id:02d}"
    name = sanitize_name(f"{prefix} - {label}") if label else prefix
    if name.endswith(" -"):
        name = name[:-2]
    return name or prefix


def web_url(base_url: str, root: str, folder: str, file_id: str | None = None) -> str:
    """Lien vers le dossier dans l'interface web : <url>/apps/files/files[/<id>]?dir=<root>/<nom>."""
    path = f"{normalize_root(root)}/{folder}"
    segment = f"/{urllib.parse.quote(str(file_id), safe='')}" if file_id else ""
    return f"{base_url.rstrip('/')}/apps/files/files{segment}?dir={urllib.parse.quote(path, safe='/')}"


def _dav_key(path: str) -> str:
    """Partie d'un chemin WebDAV après /remote.php/dav/files/ (insensible au préfixe du proxy)."""
    path = urllib.parse.unquote(path).rstrip("/")
    i = path.rfind(_DAV_MARKER)
    return path[i + len(_DAV_MARKER):] if i >= 0 else path.lstrip("/")


def parse_multistatus(body: bytes, request_path: str) -> list[DavEntry]:
    """Entrées d'une réponse PROPFIND Depth 1, sans le dossier interrogé lui-même."""
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NextcloudError(f"Réponse WebDAV illisible : {exc}.") from exc
    own = _dav_key(request_path)
    entries: list[DavEntry] = []
    for resp in tree.iter(f"{_DAV}response"):
        href = resp.findtext(f"{_DAV}href") or ""
        raw_path = urllib.parse.urlparse(href).path
        if _dav_key(raw_path) == own:  # un seul décodage : un « %41 » dans un nom reste tel quel
            continue
        path = urllib.parse.unquote(raw_path).rstrip("/")
        name = path.rsplit("/", 1)[-1]
        if not name:
            continue
        is_dir = False
        size: int | None = None
        modified: datetime | None = None
        file_id: str | None = None
        for propstat in resp.iter(f"{_DAV}propstat"):
            status = propstat.findtext(f"{_DAV}status") or ""
            if " 200 " not in f"{status} ":
                continue
            prop = propstat.find(f"{_DAV}prop")
            if prop is None:
                continue
            rtype = prop.find(f"{_DAV}resourcetype")
            if rtype is not None and rtype.find(f"{_DAV}collection") is not None:
                is_dir = True
            for tag in (f"{_DAV}getcontentlength", f"{_OC}size"):
                text = (prop.findtext(tag) or "").strip()
                if text.isdigit() and size is None:
                    size = int(text)
            lm = (prop.findtext(f"{_DAV}getlastmodified") or "").strip()
            if lm:
                try:
                    modified = parsedate_to_datetime(lm)
                except (TypeError, ValueError):
                    modified = None
            fid = (prop.findtext(f"{_OC}fileid") or "").strip()
            file_id = fid or file_id
        entries.append(DavEntry(name=name, is_dir=is_dir, size=size, modified=modified, file_id=file_id))
    return entries


# --- client ----------------------------------------------------------------------------------


class NextcloudClient:
    def __init__(self, config: NextcloudConfig):
        self.config = config
        self.root = normalize_root(config.root)

    def __repr__(self) -> str:
        cfg = self.config
        return f"NextcloudClient(url={cfg.public_url!r}, user={cfg.user!r}, root={self.root!r})"

    def with_timeout(self, timeout: float) -> NextcloudClient:
        """Copie du client dont chaque requête attend au plus ``timeout`` secondes."""
        return NextcloudClient(replace(self.config, timeout=timeout))

    # chemins
    def _dav_path(self, *segments: str) -> str:
        base = urllib.parse.urlparse(self.config.url).path.rstrip("/")
        parts = [p for p in self.root.split("/") if p] + [s for s in segments if s]
        quoted = "/".join(urllib.parse.quote(p, safe="") for p in [self.config.user, *parts])
        return f"{base}/remote.php/dav/files/{quoted}/"

    def _dav_url(self, path: str) -> str:
        u = urllib.parse.urlparse(self.config.url)
        return urllib.parse.urlunparse((u.scheme, u.netloc, path, "", "", ""))

    def _request(self, method: str, path: str, body: bytes | None = None, depth: str | None = None):
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Méthode WebDAV interdite : {method}.")
        token = base64.b64encode(f"{self.config.user}:{self.config.password}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        if body is not None:
            headers["Content-Type"] = "application/xml; charset=utf-8"
        if depth is not None:
            headers["Depth"] = depth
        req = urllib.request.Request(self._dav_url(path), data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            exc.read()
            exc.close()
            if exc.code == 401:
                raise NextcloudAuthError(
                    "Nextcloud refuse l'identification (401) : vérifiez NEXTCLOUD_USER et "
                    "NEXTCLOUD_APP_PASSWORD (mot de passe d'application)."
                ) from None
            return exc.code, b""
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            where = self.config.public_url
            raise NextcloudUnreachable(f"Nextcloud injoignable ({where}) : {reason}.") from None
        except http.client.HTTPException as exc:
            raise NextcloudUnreachable(
                f"Nextcloud a renvoyé une réponse HTTP invalide ({self.config.public_url}) : "
                f"{type(exc).__name__}."
            ) from None

    def _root_missing(self) -> NextcloudRootMissing:
        return NextcloudRootMissing(
            f"Le dossier racine « {self.root or '/'} » n'existe pas dans le Nextcloud de "
            f"{self.config.user} : vérifiez NEXTCLOUD_ROOT."
        )

    # lecture
    def list_dir(self, *segments: str) -> list[DavEntry]:
        """Contenu (profondeur 1) d'un dossier relatif à la racine."""
        path = self._dav_path(*segments)
        status, body = self._request("PROPFIND", path, body=_PROPFIND_BODY, depth="1")
        if status == 404:
            if not segments:
                raise self._root_missing()
            raise NextcloudError(f"Dossier Nextcloud introuvable : « {'/'.join(segments)} ».")
        if status != 207:
            raise NextcloudError(f"Réponse WebDAV inattendue ({status}) pour PROPFIND.")
        return sorted(parse_multistatus(body, path), key=lambda e: (not e.is_dir, e.name.lower()))

    def find_offer_folder(self, offer_id: int) -> DavEntry | None:
        """Dossier dont le numéro de tête vaut offer_id ; None si absent ; ambiguïté -> erreur."""
        matches = [e for e in self.list_dir() if e.is_dir and folder_number(e.name) == offer_id]
        if len(matches) > 1:
            raise AmbiguousFolderError(offer_id, sorted(e.name for e in matches))
        return matches[0] if matches else None

    def list_offer_files(self, offer_id: int) -> tuple[DavEntry, list[DavEntry]] | None:
        folder = self.find_offer_folder(offer_id)
        if folder is None:
            return None
        return folder, self.list_dir(folder.name)

    # création
    def _mkcol(self, *segments: str) -> bool:
        """Crée un dossier ; False s'il existait déjà (jamais écrasé)."""
        status, _ = self._request("MKCOL", self._dav_path(*segments))
        if status in (200, 201):
            return True
        if status == 405:
            parent, name = segments[:-1], segments[-1]
            entry = next((e for e in self.list_dir(*parent) if e.name == name), None)
            if entry is not None and entry.is_dir:
                return False
            raise NextcloudError(
                f"« {'/'.join(segments)} » existe déjà dans Nextcloud mais n'est pas un dossier : "
                "renommez ou déplacez ce fichier dans Nextcloud."
            )
        if status == 409:
            if len(segments) == 1:
                raise self._root_missing()
            raise NextcloudError(f"Dossier parent manquant pour « {'/'.join(segments)} » (409).")
        raise NextcloudError(f"Création du dossier « {'/'.join(segments)} » refusée ({status}).")

    def _ensure_subfolders(self, name: str) -> tuple[str, ...]:
        """Crée CV/ et Lettre/ s'ils manquent dans un dossier existant ; ne touche à rien d'autre."""
        present = {e.name.casefold() for e in self.list_dir(name)}
        added = []
        for sub in SUBFOLDERS:
            if sub.casefold() not in present and self._mkcol(name, sub):
                added.append(sub)
        return tuple(added)

    def _try_ensure_subfolders(self, name: str) -> tuple[str, ...]:
        """_ensure_subfolders sans échec : le dossier existe, un refus (partage en lecture seule,
        5xx, délai) n'est qu'un avertissement. Une erreur d'authentification reste levée."""
        try:
            return self._ensure_subfolders(name)
        except NextcloudAuthError:
            raise
        except NextcloudError as exc:
            logger.warning("Sous-dossiers CV/ et Lettre/ non vérifiés dans « %s » : %s", name, exc)
            return ()

    def create_offer_folder(self, offer: Any) -> FolderCreation:
        """Dossier de l'offre (+ CV/ et Lettre/). Un dossier existant est réutilisé tel quel, seuls
        ses CV/ ou Lettre/ manquants sont ajoutés. Renvoie (nom, créé, sous-dossiers ajoutés)."""
        name = folder_name_for(offer)  # refuse un id <= 0 avant toute requête
        existing = self.find_offer_folder(int(_field(offer, "id")))
        if existing is not None:
            return FolderCreation(existing.name, False, self._try_ensure_subfolders(existing.name))
        if not self._mkcol(name):  # apparu entre-temps : réutilisé
            return FolderCreation(name, False, self._try_ensure_subfolders(name))
        added = tuple(sub for sub in SUBFOLDERS if self._mkcol(name, sub))
        return FolderCreation(name, True, added)

    def web_url(self, folder: str, file_id: str | None = None) -> str:
        return web_url(self.config.url, self.root, folder, file_id)
