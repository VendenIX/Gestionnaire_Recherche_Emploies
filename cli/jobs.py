#!/usr/bin/env python3
"""jobs.py : client en ligne de commande de l'API de suivi des candidatures.

Un seul fichier, bibliothèque standard uniquement (argparse + urllib), Python 3.10 ou plus,
Windows et Linux. Configuration par variables d'environnement :

    JOBS_API_URL    adresse du service (défaut : http://localhost:8000)
    JOBS_API_TOKEN  jeton d'accès (en-tête Authorization: Bearer)

Les options --url et --token les remplacent. Lancez ``python jobs.py --help`` pour l'aide.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0
STATUS_UNSET = "À qualifier"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

# Champs texte modifiables : (nom API, aide).
TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "intitulé du poste"),
    ("company", "entreprise"),
    ("contract_type", "type de contrat (CDI, CDD, stage…)"),
    ("location", "lieu (ville)"),
    ("department", "code de département, tel que saisi (ex. 75, 01, 2A)"),
    ("country", "pays, code ISO à 2 lettres (défaut FR)"),
    ("status", "statut (voir la sous-commande meta)"),
    ("main_issue", "enjeu principal du poste"),
    ("rejection_reason", "motif de refus"),
    ("source", "source de l'offre (site, contact…)"),
    ("applied_at", "date de candidature, AAAA-MM-JJ ou AAAA-MM-JJTHH:MM:SS"),
    ("deadline", "date limite, AAAA-MM-JJ"),
    ("deadline_note", "remarque sur la date limite"),
    ("description", "description de l'offre"),
)
BOOL_FIELDS: tuple[tuple[str, str], ...] = (
    ("application_sent", "candidature envoyée"),
    ("follow_up_done", "relance faite"),
)
METAVARS = {
    "department": "CODE",
    "country": "PAYS",
    "status": "STATUT",
    "applied_at": "DATE",
    "deadline": "DATE",
}
CLEARABLE = tuple(name for name, _ in TEXT_FIELDS) + ("links",)

LABELS: dict[str, str] = {
    "id": "Numéro",
    "title": "Intitulé",
    "company": "Entreprise",
    "contract_type": "Contrat",
    "location": "Lieu",
    "department": "Département",
    "country": "Pays",
    "region": "Région",
    "status": "Statut",
    "main_issue": "Enjeu principal",
    "rejection_reason": "Motif de refus",
    "source": "Source",
    "applied_at": "Candidature le",
    "application_sent": "Candidature envoyée",
    "follow_up_done": "Relance faite",
    "follow_up_suggested": "Relance suggérée",
    "deadline": "Date limite",
    "days_left": "Jours restants",
    "deadline_note": "Note date limite",
    "description": "Description",
    "links": "Liens",
    "created_at": "Créée le",
    "updated_at": "Modifiée le",
}

EPILOG_MAIN = """\
Exemples :
  jobs.py add --title "Ingénieur données" --company ACME --department 2A --status "À postuler"
  jobs.py add --id 42 --title "Analyste" --link https://exemple.fr/offre/42
  jobs.py list --status "En cours" --region "Occitanie"
  jobs.py list --department 01 --text python --json
  jobs.py show 42
  jobs.py update 42 --status Entretien --follow-up-done
  jobs.py update 42 --clear deadline --clear rejection-reason
  jobs.py delete 42 --yes
  jobs.py export -o suivi.xlsx --force
  jobs.py folder 42            (dossier Nextcloud : lien et contenu ; --create pour le créer)
  jobs.py --json list --status "En cours"
  jobs.py meta

Les options --url, --token, --timeout et --json se placent avant ou après la sous-commande.
Variables d'environnement : JOBS_API_URL (défaut http://localhost:8000), JOBS_API_TOKEN.
Codes de sortie : 0 succès, 1 erreur de l'API ou du réseau, 2 commande invalide ou refusée.
Encodage : redirigée vers un fichier ou un tube, la sortie est en UTF-8. Sous PowerShell,
  faites $OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8 avant le tube,
  ou écrivez avec Out-File -Encoding utf8.
"""


class CliError(Exception):
    """Erreur à afficher telle quelle, avec un code de sortie."""

    def __init__(
        self,
        message: str,
        code: int = EXIT_ERROR,
        http_status: int | None = None,
        api_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        # Réponse d'erreur de l'API : statut HTTP et code stable éventuel ({"detail", "code"}).
        self.http_status = http_status
        self.api_code = api_code


# --- Sortie --------------------------------------------------------------------------------


def configure_output() -> None:
    """Sortie sûre en UTF-8 : jamais d'UnicodeEncodeError, même sur une console Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                # Console : on garde son encodage (Windows : UTF-16 natif), sans planter.
                reconfigure(errors="replace")
            else:
                # Redirection vers un fichier ou un tube : UTF-8 partout.
                reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def out(text: str = "") -> None:
    print(text)


def out_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def err(text: str) -> None:
    print(text, file=sys.stderr)


# --- HTTP ----------------------------------------------------------------------------------


class Api:
    def __init__(self, url: str, token: str | None, timeout: float) -> None:
        base = url.strip().rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        _check_url(url, base)
        if not token or not token.strip():
            raise CliError(
                "Jeton absent : définissez la variable JOBS_API_TOKEN ou passez --token.", EXIT_USAGE
            )
        self.base = base
        self.token = token.strip()
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: dict[str, str] | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        url = f"{self.base}/api{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            detail, api_code = _error_payload(exc)
            raise CliError(
                f"Erreur de l'API (HTTP {exc.code}) : {detail}", http_status=exc.code, api_code=api_code
            ) from exc
        except urllib.error.URLError as exc:
            raise CliError(f"Impossible de joindre l'API à {self.base} : {exc.reason}") from exc
        except TimeoutError as exc:
            raise CliError(
                f"Délai dépassé ({self.timeout:g} s) en attendant l'API à {self.base}."
            ) from exc
        except OSError as exc:
            raise CliError(f"Erreur réseau en contactant l'API à {self.base} : {exc}") from exc
        except http.client.HTTPException as exc:
            raise CliError(
                f"Réponse HTTP invalide de {self.base} ({type(exc).__name__}) : "
                "le service à cette adresse est-il bien l'API de suivi ?"
            ) from exc

    def json(self, method: str, path: str, body: Any = None, query: dict[str, str] | None = None) -> Any:
        raw, _ = self.request(method, path, body, query)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CliError("Réponse inattendue de l'API (JSON illisible).") from exc


def _check_url(url: str, base: str) -> None:
    """Adresse http(s)://hôte[:port] bien formée, sinon erreur d'utilisation (code 2)."""
    invalid = CliError(f"Adresse de l'API invalide : {url!r} (ex. http://localhost:8000).", EXIT_USAGE)
    try:
        parts = urllib.parse.urlsplit(base)
        parts.port  # noqa: B018 - lève ValueError si le port n'est pas un entier valide
    except ValueError as exc:
        raise invalid from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise invalid
    if any(ch.isspace() for ch in base):
        raise invalid


def _unexpected(what: str) -> CliError:
    return CliError(f"Réponse inattendue de l'API ({what}).")


def expect_offer(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise _unexpected("offre attendue")
    return data


def expect_offers(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise _unexpected("liste d'offres attendue")
    for item in data:
        expect_offer(item)
    return data


def _error_payload(exc: urllib.error.HTTPError) -> tuple[str, str | None]:
    """(message d'erreur, code stable éventuel) d'une réponse d'erreur de l'API."""
    try:
        raw = exc.read()
    except (OSError, http.client.HTTPException):
        raw = b""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = raw.decode("utf-8", errors="replace").strip()
        return text[:500] or str(exc.reason), None
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
        code = payload.get("code")
        code = code if isinstance(code, str) else None
        if isinstance(detail, str):
            return detail, code
        return json.dumps(detail, ensure_ascii=False), code
    return json.dumps(payload, ensure_ascii=False), None


# --- Affichage humain ----------------------------------------------------------------------


def _deadline_text(offer: dict[str, Any]) -> str:
    deadline = offer.get("deadline")
    if not deadline:
        return "sans date limite"
    days = offer.get("days_left")
    if days is None:
        try:
            days = (date.fromisoformat(deadline) - date.today()).days
        except ValueError:
            return str(deadline)
    if days > 0:
        rest = f"J-{days}"
    elif days == 0:
        rest = "aujourd'hui"
    else:
        rest = f"dépassée de {-days} j"
    return f"{deadline} ({rest})"


def summary_line(offer: dict[str, Any]) -> str:
    status = offer.get("status") or STATUS_UNSET
    title = offer.get("title") or "(sans intitulé)"
    company = offer.get("company") or "(entreprise inconnue)"
    return f"#{offer['id']:<5} {status:<12} {_deadline_text(offer):<30} {title} — {company}"


def _value_text(name: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, bool):
        return "oui" if value else "non"
    if name == "links":
        return "\n" + "\n".join(f"    - {link}" for link in value)
    text = str(value)
    if "\n" in text:
        return "\n" + "\n".join(f"    {line}" for line in text.splitlines())
    return text


def print_offer(offer: dict[str, Any]) -> None:
    for name, label in LABELS.items():
        if name not in offer:
            continue
        value = offer[name]
        if name == "status" and value is None:
            value = STATUS_UNSET
        if name == "deadline" and value:
            value = _deadline_text(offer)
        if name == "days_left":
            continue
        out(f"{label:<20} : {_value_text(name, value)}")


def _size_text(size: Any) -> str:
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return ""
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            text = f"{value:.0f}" if unit == "o" else f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
        value /= 1024
    return ""


def expect_folder(data: Any) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("name"), str)
        or not isinstance(data.get("url"), str)
        or not isinstance(data.get("files"), list)
        or not all(isinstance(f, dict) and isinstance(f.get("name"), str) for f in data["files"])
    ):
        raise _unexpected("dossier attendu")
    return data


def print_folder(folder: dict[str, Any]) -> None:
    out(f"Dossier : {folder['name']}")
    out(f"Lien    : {folder['url']}")
    files = folder["files"]
    if not files:
        out("Contenu : (vide)")
        return
    out("Contenu :")
    for item in files:
        if item.get("is_dir"):
            out(f"  {item['name']}/")
            continue
        details = [t for t in (_size_text(item.get("size")), str(item.get("modified") or "")[:10]) if t]
        out(f"  {item['name']}" + (f"  ({', '.join(details)})" if details else ""))


# Issue de la création du dossier renvoyée par POST /offers (champ « folder »).
FOLDER_OUTCOMES = {
    "created": "Dossier Nextcloud créé : {name}",
    "existing": "Dossier Nextcloud existant, réutilisé : {name}",
    "error": "Dossier Nextcloud non créé (l'offre est bien enregistrée) : {detail}",
}


# --- Construction des données --------------------------------------------------------------


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def _field_values(args: argparse.Namespace) -> dict[str, Any]:
    """Champs effectivement fournis sur la ligne de commande (rien d'inventé)."""
    data: dict[str, Any] = {}
    for name, _ in TEXT_FIELDS:
        value = getattr(args, name)
        if value is not None:
            data[name] = value
    for name, _ in BOOL_FIELDS:
        value = getattr(args, name)
        if value is not None:
            data[name] = value
    if args.links is not None:
        data["links"] = args.links
    return data


def _clear_name(raw: str) -> str:
    name = raw.strip().lstrip("-").replace("-", "_")
    if name == "link":
        name = "links"
    if name in {n for n, _ in BOOL_FIELDS}:
        raise CliError(
            f"--clear {raw} : un booléen ne peut pas être vidé, utilisez --{_kebab(name)} "
            f"ou --no-{_kebab(name)}.",
            EXIT_USAGE,
        )
    if name not in CLEARABLE:
        raise CliError(
            f"--clear {raw} : champ inconnu. Champs effaçables : "
            f"{', '.join(_kebab(n) for n in CLEARABLE)}.",
            EXIT_USAGE,
        )
    return name


# --- Sous-commandes ------------------------------------------------------------------------


def cmd_add(api: Api, args: argparse.Namespace) -> int:
    body = _field_values(args)
    if args.id is not None:
        body["id"] = args.id
    offer = expect_offer(api.json("POST", "/offers", body))
    if args.json:
        out_json(offer)
    else:
        out(f"Offre n° {offer['id']} créée.")
        out(summary_line(offer))
        folder = offer.get("folder")
        if isinstance(folder, dict) and folder.get("status") in FOLDER_OUTCOMES:
            fields = {k: folder.get(k) or "" for k in ("name", "detail")}
            out(FOLDER_OUTCOMES[folder["status"]].format(**fields))
            if folder.get("url"):
                out(f"Lien : {folder['url']}")
    return EXIT_OK


def cmd_update(api: Api, args: argparse.Namespace) -> int:
    body = _field_values(args)
    for raw in args.clear or []:
        name = _clear_name(raw)
        if name in body:
            raise CliError(
                f"--clear {raw} contredit --{_kebab(name if name != 'links' else 'link')} : "
                "choisissez l'un ou l'autre.",
                EXIT_USAGE,
            )
        body[name] = None
    if not body:
        raise CliError(
            "Rien à modifier : indiquez au moins un champ (ex. --status Entretien) ou --clear <champ>.",
            EXIT_USAGE,
        )
    offer = expect_offer(api.json("PATCH", f"/offers/{args.offer_id}", body))
    if args.json:
        out_json(offer)
    else:
        changed = ", ".join(_kebab(n) for n in body)
        out(f"Offre n° {offer['id']} mise à jour ({changed}).")
        out(summary_line(offer))
    return EXIT_OK


def cmd_show(api: Api, args: argparse.Namespace) -> int:
    offer = expect_offer(api.json("GET", f"/offers/{args.offer_id}"))
    if args.json:
        out_json(offer)
    else:
        print_offer(offer)
        line = _folder_line(api, offer["id"])
        if line:
            out(line)
    return EXIT_OK


def _folder_line(api: Api, offer_id: int) -> str | None:
    """Ligne « Dossier » de show ; None si Nextcloud n'est pas activé (ou API sans dossiers)."""
    label = f"{'Dossier Nextcloud':<20} : "
    try:
        folder = expect_folder(api.json("GET", f"/offers/{offer_id}/folder"))
    except CliError as exc:
        if exc.api_code == "folder_not_found":
            return f"{label}aucun (pour le créer : jobs.py folder {offer_id} --create)"
        if exc.api_code and (exc.api_code.startswith("nextcloud_") or exc.api_code == "folder_ambiguous"):
            if exc.api_code == "nextcloud_disabled":
                return None
            return f"{label}indisponible ({exc.message})"
        return None
    return f"{label}{folder['url']}"


def cmd_folder(api: Api, args: argparse.Namespace) -> int:
    path = f"/offers/{args.offer_id}/folder"
    try:
        folder = expect_folder(api.json("POST" if args.create else "GET", path))
    except CliError as exc:
        if exc.api_code == "folder_not_found":
            exc.message += f"\nPour le créer : jobs.py folder {args.offer_id} --create"
        raise
    if args.json:
        out_json(folder)
        return EXIT_OK
    if args.create:
        out("Dossier créé." if folder.get("created") else "Le dossier existait déjà : rien n'a été modifié.")
    print_folder(folder)
    return EXIT_OK


def cmd_list(api: Api, args: argparse.Namespace) -> int:
    query: dict[str, str] = {}
    if args.status is not None:
        query["status"] = args.status
    if args.department is not None:
        query["department"] = args.department
    if args.region is not None:
        query["region"] = args.region
    if args.text is not None:
        query["q"] = args.text
    if args.desc:
        query["order"] = "desc"
    offers = expect_offers(api.json("GET", "/offers", query=query))
    if args.json:
        out_json(offers)
        return EXIT_OK
    for offer in offers:
        out(summary_line(offer))
    count = len(offers)
    out(f"{count} offre{'s' if count > 1 else ''}.")
    return EXIT_OK


def cmd_delete(api: Api, args: argparse.Namespace) -> int:
    if not args.yes:
        raise CliError(
            f"Suppression refusée : ajoutez --yes pour confirmer la suppression définitive "
            f"de l'offre n° {args.offer_id}.",
            EXIT_USAGE,
        )
    api.request("DELETE", f"/offers/{args.offer_id}")
    if args.json:
        out_json({"deleted": args.offer_id})
    else:
        out(f"Offre n° {args.offer_id} supprimée.")
    return EXIT_OK


def _filename_from(headers: dict[str, str]) -> str:
    disposition = next((v for k, v in headers.items() if k.lower() == "content-disposition"), "")
    match = re.search(r'filename="?([^";]+)"?', disposition)
    name = Path(match.group(1)).name if match else ""
    return name or f"suivi-offres-{date.today().isoformat()}.xlsx"


def _refuse_overwrite(target: Path, force: bool) -> None:
    if target.exists() and not force:
        raise CliError(
            f"{target} existe déjà : ajoutez --force pour l'écraser ou choisissez un autre nom (-o).",
            EXIT_USAGE,
        )


def cmd_export(api: Api, args: argparse.Namespace) -> int:
    if args.output:
        _refuse_overwrite(Path(args.output), args.force)
    content, headers = api.request("GET", "/export.xlsx")
    if not content.startswith(b"PK\x03\x04"):
        raise CliError(
            "Réponse inattendue de l'API : le contenu reçu n'est pas un classeur Excel "
            f"(vérifiez l'adresse {api.base}). Aucun fichier écrit."
        )
    target = Path(args.output) if args.output else Path(_filename_from(headers))
    _refuse_overwrite(target, args.force)
    try:
        target.write_bytes(content)
    except OSError as exc:
        raise CliError(f"Impossible d'écrire {target} : {exc}") from exc
    if args.json:
        out_json({"file": str(target), "bytes": len(content)})
    else:
        out(f"Classeur exporté : {target} ({len(content)} octets).")
    return EXIT_OK


def cmd_meta(api: Api, args: argparse.Namespace) -> int:
    meta = api.json("GET", "/meta")
    keys = ("statuses", "status_unset_label", "follow_up_days", "regions", "no_department_label",
            "departments")
    if not isinstance(meta, dict) or any(key not in meta for key in keys):
        raise _unexpected("référentiels attendus")
    if args.json:
        out_json(meta)
        return EXIT_OK
    out("Statuts : " + ", ".join(meta["statuses"]) + f" (absent : {meta['status_unset_label']})")
    out(f"Relance suggérée après {meta['follow_up_days']} jours sans réponse.")
    if meta.get("nextcloud_error"):
        out(f"Dossiers Nextcloud : désactivés ({meta['nextcloud_error']})")
    elif "nextcloud_enabled" in meta:
        out("Dossiers Nextcloud : " + ("activés (WebDAV)" if meta["nextcloud_enabled"] else "désactivés"))
    out("Régions :")
    for region in meta["regions"]:
        out(f"  - {region}")
    out(f"  - {meta['no_department_label']}")
    out("Départements :")
    for dep in meta["departments"]:
        out(f"  {dep['code']:<4} {dep['name']} ({dep['region']})")
    return EXIT_OK


# --- Analyse des arguments -----------------------------------------------------------------

# Messages d'argparse (anglais) traduits : (motif, remplacement).
_ARGPARSE_FR: tuple[tuple[str, str], ...] = (
    (r"the following arguments are required: ", "arguments obligatoires manquants : "),
    (r"unrecognized arguments: ", "arguments non reconnus : "),
    (r"expected one argument", "une valeur est attendue"),
    (r"ignored explicit argument (.*)", r"valeur inattendue \1"),
    (r"not allowed with argument ", "incompatible avec l'argument "),
    (r"ambiguous option: (\S+) could match (.*)", r"option ambiguë : \1 peut désigner \2"),
    (r"invalid choice: (.*) \(choose from (.*)\)", r"choix invalide : \1 (possibles : \2)"),
    (r"invalid (\w+) value: (.*)", r"valeur invalide : \2"),
    (r"^argument (\S+): ", r"argument \1 : "),
)


def _translate(message: str) -> str:
    for pattern, replacement in _ARGPARSE_FR:
        message = re.sub(pattern, replacement, message)
    return message


class FrenchFormatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, "utilisation : " if prefix is None else prefix)


class FrenchParser(argparse.ArgumentParser):
    """ArgumentParser aux titres, à l'aide et aux erreurs en français."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", FrenchFormatter)
        kwargs.setdefault("allow_abbrev", False)
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        self._positionals.title = "arguments positionnels"
        self._optionals.title = "options"
        if add_help:
            self.add_argument(
                "-h", "--help", action="help", default=argparse.SUPPRESS,
                help="afficher cette aide et quitter",
            )

    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog} : erreur : {_translate(message)}\n")


def _offer_id(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"numéro d'offre invalide : {raw!r} (entier attendu)") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"numéro d'offre invalide : {raw!r} (entier ≥ 1 attendu)")
    return value


def _timeout(raw: str) -> float:
    """Délai réseau : nombre fini strictement positif (refuse 0, négatifs, nan, inf, 1e309)."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"délai invalide : {raw!r} (nombre de secondes attendu)") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"délai invalide : {raw!r} (nombre fini > 0 attendu)")
    return value


def _common_options() -> argparse.ArgumentParser:
    """Options acceptées avant comme après la sous-commande.

    Défaut SUPPRESS : une option absente de la sous-commande n'écrase pas celle donnée avant
    elle ; les vrais défauts sont appliqués par _apply_defaults après l'analyse.
    """
    common = FrenchParser(add_help=False)
    group = common.add_argument_group("connexion et sortie")
    group.add_argument(
        "--url",
        default=argparse.SUPPRESS,
        help=f"adresse du service (défaut : $JOBS_API_URL ou {DEFAULT_URL})",
    )
    group.add_argument(
        "--token",
        default=argparse.SUPPRESS,
        help="jeton d'accès (défaut : $JOBS_API_TOKEN)",
    )
    group.add_argument(
        "--timeout", type=_timeout, default=argparse.SUPPRESS,
        help=f"délai réseau en secondes (défaut : {DEFAULT_TIMEOUT:g})",
    )
    group.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="sortie JSON lisible par une machine",
    )
    return common


def _apply_defaults(args: argparse.Namespace) -> None:
    if getattr(args, "url", None) is None:
        args.url = os.environ.get("JOBS_API_URL", "").strip() or DEFAULT_URL
    if getattr(args, "token", None) is None:
        args.token = os.environ.get("JOBS_API_TOKEN")
    if getattr(args, "timeout", None) is None:
        args.timeout = DEFAULT_TIMEOUT
    if not hasattr(args, "json"):
        args.json = False


def _add_field_options(parser: argparse.ArgumentParser, update: bool) -> None:
    group = parser.add_argument_group(
        "champs de l'offre",
        "Seuls les champs fournis sont envoyés"
        + (" et modifiés ; les autres restent intacts." if update else " ; les autres restent vides."),
    )
    for name, help_text in TEXT_FIELDS:
        metavar = METAVARS.get(name, "TEXTE")
        group.add_argument(f"--{_kebab(name)}", dest=name, metavar=metavar, help=help_text)
    for name, help_text in BOOL_FIELDS:
        group.add_argument(
            f"--{_kebab(name)}",
            dest=name,
            action=argparse.BooleanOptionalAction,
            default=None,
            help=help_text + " (oui / non)",
        )
    group.add_argument(
        "--link",
        dest="links",
        action="append",
        metavar="URL",
        help="lien http(s) ; répétable"
        + (" ; remplace toute la liste existante" if update else ""),
    )


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    formatter = FrenchFormatter
    parser = FrenchParser(
        prog="jobs.py",
        description="Client en ligne de commande du suivi des candidatures (API REST).",
        epilog=EPILOG_MAIN,
        formatter_class=formatter,
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMANDE", title="sous-commandes")
    sub.required = True

    p = sub.add_parser(
        "add",
        parents=[common],
        help="créer une offre",
        description="Crée une offre. Un champ non fourni reste vide (rien n'est inventé).",
        epilog=(
            "Exemples :\n"
            '  jobs.py add --title "Chargé d\'études" --company ACME --department 2A\n'
            "  jobs.py add --id 42 --title Analyste --status \"À postuler\" --deadline 2025-03-31 \\\n"
            "      --link https://exemple.fr/a --link https://exemple.fr/b --application-sent\n"
        ),
        formatter_class=formatter,
    )
    p.add_argument("--id", type=_offer_id, help="numéro imposé (sinon attribué par le service)")
    _add_field_options(p, update=False)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser(
        "update",
        parents=[common],
        help="modifier une offre (seuls les champs fournis)",
        description=(
            "Modifie une offre : seuls les champs fournis changent, les autres restent intacts.\n"
            "--clear <champ> vide un champ (répétable)."
        ),
        epilog=(
            "Exemples :\n"
            "  jobs.py update 42 --status Entretien\n"
            "  jobs.py update 42 --application-sent --applied-at 2025-02-03\n"
            "  jobs.py update 42 --clear deadline --clear deadline-note\n"
            "  jobs.py update 42 --no-follow-up-done --json\n"
            f"\nChamps effaçables : {', '.join(_kebab(n) for n in CLEARABLE)}.\n"
        ),
        formatter_class=formatter,
    )
    p.add_argument("offer_id", type=_offer_id, metavar="ID", help="numéro de l'offre")
    _add_field_options(p, update=True)
    p.add_argument(
        "--clear",
        action="append",
        metavar="CHAMP",
        help="vider ce champ (nom en kebab-case, ex. deadline-note) ; répétable",
    )
    p.set_defaults(func=cmd_update)

    p = sub.add_parser(
        "show",
        parents=[common],
        help="afficher une offre",
        description="Affiche le détail d'une offre.",
        epilog="Exemples :\n  jobs.py show 42\n  jobs.py show 42 --json\n",
        formatter_class=formatter,
    )
    p.add_argument("offer_id", type=_offer_id, metavar="ID", help="numéro de l'offre")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "list",
        parents=[common],
        help="lister les offres (filtres facultatifs)",
        description=(
            "Liste les offres, une par ligne : numéro, statut, date limite (jours restants), "
            "intitulé, entreprise."
        ),
        epilog=(
            "Exemples :\n"
            "  jobs.py list\n"
            '  jobs.py list --status "En cours"\n'
            "  jobs.py list --department 2A\n"
            '  jobs.py list --region "Hors France" --text data --json\n'
            '  jobs.py list --status "À qualifier"   (offres sans statut)\n'
        ),
        formatter_class=formatter,
    )
    p.add_argument("--status", metavar="STATUT", help="statut exact (voir meta)")
    p.add_argument("--department", metavar="CODE", help="code de département tel que saisi (ex. 01, 2A)")
    p.add_argument("--region", metavar="RÉGION", help="région (voir meta)")
    p.add_argument("--text", metavar="TEXTE", help="recherche dans l'intitulé, l'entreprise, le lieu…")
    p.add_argument("--desc", action="store_true", help="numéros décroissants")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "delete",
        parents=[common],
        help="supprimer une offre (exige --yes)",
        description="Supprime définitivement une offre. Sans --yes, la commande refuse.",
        epilog="Exemple :\n  jobs.py delete 42 --yes\n",
        formatter_class=formatter,
    )
    p.add_argument("offer_id", type=_offer_id, metavar="ID", help="numéro de l'offre")
    p.add_argument("--yes", action="store_true", help="confirmer la suppression")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser(
        "export",
        parents=[common],
        help="exporter toutes les offres en classeur Excel",
        description="Télécharge le classeur Excel (format v2) de toutes les offres.",
        epilog=(
            "Exemples :\n  jobs.py export -o suivi.xlsx\n"
            "  jobs.py export -o suivi.xlsx --force   (remplace un fichier existant)\n"
            "  jobs.py export            (nom proposé par le service : suivi-offres-AAAA-MM-JJ.xlsx)\n"
            "\nUn fichier existant n'est jamais écrasé sans --force.\n"
        ),
        formatter_class=formatter,
    )
    p.add_argument("-o", "--output", metavar="FICHIER", help="fichier .xlsx à écrire")
    p.add_argument("--force", action="store_true", help="écraser le fichier s'il existe déjà")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "folder",
        parents=[common],
        help="dossier Nextcloud d'une offre : lien et contenu (--create pour le créer)",
        description=(
            "Affiche le dossier Nextcloud de l'offre (trouvé par son numéro) : lien vers\n"
            "l'interface web et liste des fichiers. Avec --create, crée « NN - <intitulé>\n"
            "<entreprise> » (avec CV/ et Lettre/) s'il n'existe pas ; un dossier existant n'est\n"
            "jamais renommé ni modifié. Exige NEXTCLOUD_WEBDAV côté service."
        ),
        epilog=(
            "Exemples :\n  jobs.py folder 42\n  jobs.py folder 42 --create\n  jobs.py folder 42 --json\n"
        ),
        formatter_class=formatter,
    )
    p.add_argument("offer_id", type=_offer_id, metavar="ID", help="numéro de l'offre")
    p.add_argument("--create", action="store_true", help="créer le dossier s'il n'existe pas")
    p.set_defaults(func=cmd_folder)

    p = sub.add_parser(
        "meta",
        parents=[common],
        help="référentiels : statuts, régions, départements",
        description="Affiche les valeurs admises : statuts, régions, départements.",
        epilog="Exemples :\n  jobs.py meta\n  jobs.py meta --json\n",
        formatter_class=formatter,
    )
    p.set_defaults(func=cmd_meta)
    return parser


def _fix_argument(arg: str) -> str:
    """Argument décodé avec surrogateescape (locale C/ASCII sous Linux) : relu en UTF-8."""
    try:
        arg.encode("utf-8")
        return arg
    except UnicodeEncodeError:
        return os.fsencode(arg).decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_output()
    parser = build_parser()
    args = parser.parse_args([_fix_argument(a) for a in (sys.argv[1:] if argv is None else argv)])
    _apply_defaults(args)
    try:
        api = Api(args.url, args.token, args.timeout)
        return args.func(api, args)
    except CliError as exc:
        err(exc.message)
        return exc.code
    except KeyboardInterrupt:
        err("Interrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
