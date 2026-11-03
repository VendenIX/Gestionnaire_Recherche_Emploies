"""Import d'un classeur Excel dans la base (via la couche repository, jamais en SQL brut).

Deux formats sont lus :

- l'ORIGINAL (première feuille, ex. "Feuil1") : en-têtes de l'Excel d'origine ("Status",
  "DeadLine", "Entrerprise"…) ;
- le format v2 (feuille "Suivi"), y compris les classeurs produits par ``excel_export``.

Règles (reprises de reference/build_excel_v2.py et des décisions du propriétaire) :

- cellule vide (ou blanche) -> None ; les textes sont lus comme texte (un nombre devient "12") ;
- Statut "?" -> None (décision 2026-11-02) ; autre statut inconnu -> erreur ;
- Date Candidature : date Excel, ou texte "03/02/2026 à 09h30" (parse_date_str de v2) -> ISO ;
- Deadline : date Excel, numéro de série Excel (cellule au format Standard, ex. 46000) ou texte
  "jj/mm/aaaa" -> ``deadline`` ; "?" ou "/" -> ``deadline_note``. Tout AUTRE texte (ex. « fin
  juin », « dès que possible ») est conservé tel quel dans ``deadline_note`` : contrairement à
  Date Candidature (rejetée si illisible), une deadline approximative est une information utile ;
  elle n'a simplement pas de jours restants ;
- Candidature envoyée : original = présence d'une date de candidature (comme v2) ; v2 = case ☑ ;
- Relance faite : original = non ; v2 = case ☑ ;
- Link(s) : URL séparées par des blancs, plus la cible du lien hypertexte si elle n'y est pas ;
- département / pays : repris des propriétés ``geo.<id>`` = "dép|pays|localisation" d'un export
  seulement si les cellules Région ET Localisation de la ligne sont inchangées ; sinon déduits de
  la localisation (``infer_location``). En v2, la Région saisie par l'utilisateur prime : si le
  département déduit appartient à une autre région, département = None (pays France) ; Région
  « Hors France » avec une localisation française -> département None, pays par défaut (France,
  donc « Sans département ») sauf si un pays étranger est déduit. Région inchangée et localisation
  modifiée en un lieu non reconnu : le pays étranger (« Hors France ») ou le département (s'il est
  dans la Région) stockés dans ``geo.<id>`` sont gardés.

Les ids du fichier sont conservés. L'import est atomique (repository.import_offers) : tout le
fichier est validé, puis suppression (``replace``) et insertions se font en une transaction ;
en cas d'erreur la base est inchangée.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO

import openpyxl
from openpyxl.utils.datetime import from_excel
from pydantic import ValidationError

from . import geo, repository
from .excel_export import CK_ON, COLS, GEO_PROP_PREFIX
from .schemas import OfferCreate

# --------------------------------------------------------------------- géographie
# Mots-clés de build_excel_v2.py, étendus pour donner un département (None = région entière :
# département ambigu). Un mot-clé est cherché dans la localisation (casse ignorée, accents compris) ;
# si plusieurs correspondent, le dernier de la liste l'emporte : l'étranger est testé en premier,
# pour que « Belgique - Lille » soit rangé en France.
FOREIGN: dict[str, str] = {
    "Allemagne": "DE",
    "Germany": "DE",
    "Berlin": "DE",
    "Munich": "DE",
    "Heidelberg": "DE",
    "Belgique": "BE",
    "Bruxelles": "BE",
    "Suisse": "CH",
    "Genève": "CH",
    "Lausanne": "CH",
    "Zurich": "CH",
    "Luxembourg": "LU",
    "Royaume-Uni": "GB",
    "Londres": "GB",
    "London": "GB",
    "Irlande": "IE",
    "Dublin": "IE",
    "Pays-Bas": "NL",
    "Amsterdam": "NL",
    "Espagne": "ES",
    "Barcelone": "ES",
    "Madrid": "ES",
    "Italie": "IT",
    "Milan": "IT",
    "Danemark": "DK",
    "Copenhague": "DK",
    "Suède": "SE",
    "Stockholm": "SE",
    "Canada": "CA",
    "Montréal": "CA",
    "Québec": "CA",
    "États-Unis": "US",
    "Etats-Unis": "US",
    "Singapour": "SG",
    "Japon": "JP",
    "Tokyo": "JP",
    "Chine": "CN",
    "Maroc": "MA",
    "Tunisie": "TN",
    "Israël": "IL",
}

# Région -> mots-clés (ordre de v2) avec leur département.
PLACES: dict[str, dict[str, str | None]] = {
    "Île-de-France": {
        "Île-de-France": None,
        "Ile-de-France": None,
        "Paris": "75",
        "Vélizy": "78",
        "Velizy": "78",
        "Saclay": "91",
        "Palaiseau": "91",
        "Orsay": "91",
        "Gif-sur-Yvette": "91",
        "Bures-sur-Yvette": "91",
        "Massy": "91",
        "Boulogne-Billancourt": "92",
        "Issy-les-Moulineaux": "92",
        "Nanterre": "92",
        "La Défense": "92",
        "La Defense": "92",
        "Courbevoie": "92",
        "Puteaux": "92",
        "Montrouge": "92",
        "Évry": "91",
        "Evry": "91",
        "Villejuif": "94",
        "Créteil": "94",
        "Creteil": "94",
        "Saint-Denis": "93",
        "Montreuil": "93",
        "Versailles": "78",
        "Guyancourt": "78",
        "Rueil-Malmaison": "92",
        "Levallois": "92",
        "Clichy": "92",
        "Ivry-sur-Seine": "94",
        "Kremlin-Bicêtre": "94",
        "Le Plessis-Robinson": "92",
        "Châtillon": "92",
        "Malakoff": "92",
        "Vincennes": "94",
        "Cergy": "95",
        "Marne-la-Vallée": "77",
        "Champs-sur-Marne": "77",
        "Meudon": "92",
        "Suresnes": "92",
        "Neuilly-sur-Seine": "92",
        "Saint-Ouen": "93",
        "Aubervilliers": "93",
        "Pantin": "93",
        "Bagneux": "92",
        "Cachan": "94",
        "Fontenay-aux-Roses": "92",
        "Fontenay-sous-Bois": "94",
        "Colombes": "92",
        "Asnières": "92",
        "Gennevilliers": "92",
        "Argenteuil": "95",
        "Antony": "92",
        "Rungis": "94",
        "Saint-Quentin-en-Yvelines": "78",
    },
    "Hauts-de-France": {
        "Hauts-de-France": None,
        "Lille": "59",
        "Villeneuve-d'Ascq": "59",
        "Roubaix": "59",
        "Tourcoing": "59",
        "Amiens": "80",
        "Compiègne": "60",
        "Dunkerque": "59",
        "Valenciennes": "59",
        "Arras": "62",
        "Calais": "62",
        "Beauvais": "60",
        "Boulogne-sur-Mer": "62",
    },
    "Normandie": {
        "Normandie": None,
        "Rouen": "76",
        "Caen": "14",
        "Le Havre": "76",
        "Cherbourg": "50",
        "Évreux": "27",
        "Alençon": "61",
    },
    "Bretagne": {
        "Bretagne": None,
        "Rennes": "35",
        "Brest": "29",
        "Lorient": "56",
        "Vannes": "56",
        "Quimper": "29",
        "Saint-Brieuc": "22",
        "Lannion": "22",
        "Saint-Malo": "35",
    },
    "Pays de la Loire": {
        "Pays de la Loire": None,
        "Nantes": "44",
        "Angers": "49",
        "Le Mans": "72",
        "Saint-Nazaire": "44",
        "La Roche-sur-Yon": "85",
        "Cholet": "49",
    },
    "Centre-Val de Loire": {
        "Centre-Val de Loire": None,
        "Orléans": "45",
        "Orleans": "45",
        "Tours": "37",
        "Bourges": "18",
        "Blois": "41",
        "Chartres": "28",
        "Châteauroux": "36",
    },
    "Grand Est": {
        "Grand Est": None,
        "Strasbourg": "67",
        "Nancy": "54",
        "Metz": "57",
        "Reims": "51",
        "Mulhouse": "68",
        "Colmar": "68",
        "Illkirch": "67",
        "Troyes": "10",
        "Charleville-Mézières": "08",
        "Épinal": "88",
        "Vandoeuvre": "54",
        "Thionville": "57",
        "Châlons": "51",
    },
    "Bourgogne-Franche-Comté": {
        "Bourgogne-Franche-Comté": None,
        "Dijon": "21",
        "Besançon": "25",
        "Besancon": "25",
        "Belfort": "90",
        "Montbéliard": "25",
        "Auxerre": "89",
        "Chalon-sur-Saône": "71",
        "Mâcon": "71",
        "Nevers": "58",
    },
    "Nouvelle-Aquitaine": {
        "Nouvelle-Aquitaine": None,
        "Bordeaux": "33",
        "Poitiers": "86",
        "Limoges": "87",
        "La Rochelle": "17",
        "Pessac": "33",
        "Talence": "33",
        "Mérignac": "33",
        "Merignac": "33",
        "Bayonne": "64",
        "Biarritz": "64",
        "Anglet": "64",
        "Angoulême": "16",
        "Niort": "79",
        "Périgueux": "24",
    },
    "Auvergne-Rhône-Alpes": {
        "Auvergne-Rhône-Alpes": None,
        "Lyon": "69",
        "Villeurbanne": "69",
        "Grenoble": "38",
        "Saint-Étienne": "42",
        "Saint-Etienne": "42",
        "Clermont-Ferrand": "63",
        "Annecy": "74",
        "Chambéry": "73",
        "Chambery": "73",
        "Valence": "26",
        "Meylan": "38",
        "Échirolles": "38",
        "Echirolles": "38",
        "Vénissieux": "69",
        "Venissieux": "69",
        "Saint-Martin-d'Hères": "38",
        "Montbonnot": "38",
        "Aubière": "63",
    },
    "Occitanie": {
        "Occitanie": None,
        "Toulouse": "31",
        "Montpellier": "34",
        "Nîmes": "30",
        "Nimes": "30",
        "Perpignan": "66",
        "Béziers": "34",
        "Beziers": "34",
        "Tarbes": "65",
        "Carcassonne": "11",
        "Castres": "81",
        "Montauban": "82",
        "Labège": "31",
        "Labege": "31",
        "Blagnac": "31",
        "Colomiers": "31",
        "Narbonne": "11",
    },
    "Provence-Alpes-Côte d'Azur": {
        "Provence-Alpes-Côte d'Azur": None,
        "PACA": None,
        "Côte d'Azur": None,
        "Marseille": "13",
        "Nice": "06",
        "Aix-en-Provence": "13",
        "Aix-Marseille": "13",
        "Sophia Antipolis": "06",
        "Valbonne": "06",
        "Antibes": "06",
        "Cannes": "06",
        "Toulon": "83",
        "Avignon": "84",
    },
    "Corse": {"Corse": None, "Ajaccio": "2A", "Bastia": "2B"},
}


def _keyword_table() -> list[tuple[str, str | None, str]]:
    """(mot-clé, département ou None, pays), dans l'ordre de priorité croissante de v2."""
    table: list[tuple[str, str | None, str]] = [(k, None, c) for k, c in FOREIGN.items()]
    for region, places in PLACES.items():
        codes = [code for code, name in geo.DEPARTMENT_REGION.items() if name == region]
        table += [(f"({code})", code, geo.FRANCE) for code in codes]
        table += [(k, dep, geo.FRANCE) for k, dep in places.items()]
    for _keyword, dep, _ in table:
        if dep is not None:
            geo.validate_department(dep)
    return table


KEYWORDS = _keyword_table()


def infer_location(location: object) -> tuple[str | None, str]:
    """Localisation libre -> (département, pays). Le dernier mot-clé trouvé l'emporte (logique
    guess_region de v2) ; étranger seul -> (None, pays) ; région entière ou rien -> (None, "FR")."""
    if location is None or not str(location).strip():
        return None, geo.FRANCE
    low = str(location).lower()
    found: tuple[str | None, str] = (None, geo.FRANCE)
    for keyword, dep, country in KEYWORDS:
        if keyword.lower() in low:
            found = (dep, country)
    return found


# -------------------------------------------------------------------------- dates
def parse_date_str(text: str) -> datetime | None:
    """ "03/02/2026 à 09h30" ou "14/03/2026 ) 10h05" -> datetime ; "03/02/2026" -> minuit (v2)."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4}).*?(\d{1,2})h(\d{2})", text)
    if m:
        d, mo, y, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi)
    m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{4})\s*", text)
    if m:
        d, mo, y = map(int, m.groups())
        return datetime(y, mo, d)
    return None


def _as_text(value: Any) -> str | None:
    """Valeur de cellule -> texte (None si vide). Un nombre entier reste sans décimale."""
    if value is None:
        return None
    if isinstance(value, bool):
        text = "VRAI" if value else "FAUX"
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    elif isinstance(value, datetime):
        text = (
            value.strftime("%d/%m/%Y %H:%M")
            if (value.hour, value.minute) != (0, 0)
            else value.strftime("%d/%m/%Y")
        )
    elif isinstance(value, date):
        text = value.strftime("%d/%m/%Y")
    else:
        text = str(value)
    return text if text.strip() else None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = _as_text(value)
    if text is None:
        return None
    parsed = parse_date_str(text)
    if parsed is None:
        raise ValueError(f"date illisible : {text!r} (attendu : jj/mm/aaaa ou « jj/mm/aaaa à HHhMM »).")
    return parsed


# ----------------------------------------------------------------------- en-têtes
# Intitulé (original ou v2) -> clé interne.
HEADER_KEYS: dict[str, str] = {}
for _orig, _v2, _ in COLS:
    HEADER_KEYS[_v2] = _v2
    if _orig:
        HEADER_KEYS[_orig] = _v2

TEXT_FIELDS = {
    "Intitulé": "title",
    "Entreprise": "company",
    "Type de contrat": "contract_type",
    "Localisation": "location",
    "Problème majeur": "main_issue",
    "Motif rejet si rejet": "rejection_reason",
    "Offre trouvée sur": "source",
    "Description": "description",
}
URL_RE = re.compile(r"https?://\S+")
DEADLINE_NOTES = ("?", "/")
DERIVED_COLUMNS = ("Jours restants", "Région", "Candidature envoyée", "Relance faite")


class XlsxImportError(Exception):
    """Fichier illisible ou lignes invalides (messages en français, une ligne par problème)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class ParsedOffer:
    row: int
    offer_id: int | None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportResult:
    sheet: str
    format: str  # "original" ou "v2"
    imported: list[int]
    replaced: int = 0


def _cell(ws: Any, row: int, col: int | None) -> Any:
    return None if col is None else ws.cell(row, col)


def _hyperlink(cell: Any) -> str | None:
    if cell is None or cell.hyperlink is None:
        return None
    link = cell.hyperlink.target or ""
    if cell.hyperlink.location:
        link += "#" + cell.hyperlink.location
    return link or None


def _links(cell: Any) -> list[str]:
    text = _as_text(cell.value) if cell is not None else None
    links = URL_RE.findall(text) if text else []
    rest = URL_RE.sub("", text).strip() if text else ""
    target = _hyperlink(cell)
    if target and target not in links:
        links.append(target)
    if rest and not target:
        raise ValueError(f"Link(s) : texte qui n'est pas une URL : {rest!r}.")
    return links


def _geo_props(wb: Any) -> dict[int, tuple[str | None, str, str]]:
    """Propriétés ``geo.<id>`` = "dép|pays|localisation" -> {id: (dép ou None, pays, localisation)}."""
    found: dict[int, tuple[str | None, str, str]] = {}
    for prop in getattr(wb, "custom_doc_props", None) or []:
        name = getattr(prop, "name", "") or ""
        if not name.startswith(GEO_PROP_PREFIX) or not isinstance(prop.value, str):
            continue
        try:
            offer_id = int(name[len(GEO_PROP_PREFIX) :])
        except ValueError:
            continue
        parts = prop.value.split("|", 2)
        if len(parts) != 3:
            continue  # format inconnu : la localisation décidera
        department, country, location = parts
        found[offer_id] = (department or None, country or geo.FRANCE, location)
    return found


def _find_sheet(wb: Any) -> tuple[Any, str]:
    if "Suivi" in wb.sheetnames:
        return wb["Suivi"], "v2"
    return wb.worksheets[0], "original"


def read_workbook(source: str | Path | BinaryIO) -> tuple[list[ParsedOffer], str, str]:
    """Lit le classeur -> (offres validées, nom de la feuille, format). XlsxImportError si invalide."""
    try:
        wb = openpyxl.load_workbook(source)
    except Exception as exc:  # fichier absent, pas un .xlsx…
        raise XlsxImportError([f"Classeur illisible : {exc}"]) from exc
    ws, fmt = _find_sheet(wb)
    columns: dict[str, int] = {}
    for ci in range(1, ws.max_column + 1):
        header = ws.cell(1, ci).value
        key = HEADER_KEYS.get(str(header).strip()) if header is not None else None
        if key is not None and key not in columns:
            columns[key] = ci
    if "Intitulé" not in columns:
        raise XlsxImportError([f"Feuille {ws.title!r} : colonne « Intitulé » introuvable en ligne 1."])
    geo_props = _geo_props(wb) if fmt == "v2" else {}

    errors: list[str] = []
    parsed: list[ParsedOffer] = []
    seen: set[int] = set()
    for r in range(2, ws.max_row + 1):
        cells = {k: ws.cell(r, ci) for k, ci in columns.items()}
        if all(_as_text(c.value) is None for k, c in cells.items() if k not in DERIVED_COLUMNS):
            continue  # ligne vide (les colonnes calculées ou cases à cocher seules ne comptent pas)
        try:
            item = _parse_row(r, cells, fmt, geo_props)
        except ValueError as exc:
            errors.append(f"Ligne {r} : {exc}")
            continue
        if item.offer_id is not None:
            if item.offer_id in seen:
                errors.append(f"Ligne {r} : numéro de dossier {item.offer_id} en double dans le fichier.")
                continue
            seen.add(item.offer_id)
        parsed.append(item)
    if errors:
        raise XlsxImportError(errors)
    return parsed, ws.title, fmt


def _parse_row(
    r: int, cells: dict[str, Any], fmt: str, geo_props: dict[int, tuple[str | None, str, str]]
) -> ParsedOffer:
    def value(key: str) -> Any:
        c = cells.get(key)
        return None if c is None else c.value

    data: dict[str, Any] = {name: _as_text(value(h)) for h, name in TEXT_FIELDS.items()}

    raw_id = value("Id (Dossier associé)")
    offer_id: int | None = None
    if _as_text(raw_id) is not None:
        if isinstance(raw_id, int | float) and not isinstance(raw_id, bool) and float(raw_id).is_integer():
            offer_id = int(raw_id)
        elif isinstance(raw_id, str) and raw_id.strip().isdigit():
            offer_id = int(raw_id.strip())
        else:
            raise ValueError(f"numéro de dossier invalide : {raw_id!r}.")

    status = _as_text(value("Statut"))
    data["status"] = None if status is None or status.strip() == "?" else status.strip()

    applied = _as_datetime(value("Date Candidature"))
    data["applied_at"] = applied.isoformat() if applied else None

    deadline_raw = value("Deadline")
    data["deadline"] = None
    data["deadline_note"] = None
    if isinstance(deadline_raw, datetime | date):
        data["deadline"] = _as_datetime(deadline_raw).date().isoformat()
    elif isinstance(deadline_raw, int | float) and not isinstance(deadline_raw, bool):
        try:  # numéro de série Excel d'une date affichée au format Standard
            serial = from_excel(deadline_raw)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Deadline : nombre qui n'est pas une date Excel : {deadline_raw!r}.") from exc
        if not isinstance(serial, datetime) or not 1 <= deadline_raw < 2958466:
            raise ValueError(f"Deadline : nombre qui n'est pas une date Excel : {deadline_raw!r}.")
        data["deadline"] = serial.date().isoformat()
    else:
        text = _as_text(deadline_raw)
        parsed = parse_date_str(text) if text else None
        if parsed is not None:
            data["deadline"] = parsed.date().isoformat()
        elif text is not None:
            data["deadline_note"] = text.strip() if text.strip() in DEADLINE_NOTES else text

    if fmt == "v2":
        data["application_sent"] = _as_text(value("Candidature envoyée")) == CK_ON
        data["follow_up_done"] = _as_text(value("Relance faite")) == CK_ON
    else:  # v2 déduit « envoyée » de la présence d'une date de candidature ; relance : non
        data["application_sent"] = applied is not None
        data["follow_up_done"] = False

    data["links"] = _links(cells.get("Link(s)"))

    department, country = _geo(data["location"], offer_id, _as_text(value("Région")), fmt, geo_props)
    data["department"] = department
    data["country"] = country

    try:
        model = OfferCreate(id=offer_id, **data)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))} : {e['msg']}" for e in exc.errors())
        raise ValueError(details) from exc
    return ParsedOffer(row=r, offer_id=offer_id, data=model.model_dump(exclude={"id"}))


def _geo(
    location: str | None,
    offer_id: int | None,
    region_cell: str | None,
    fmt: str,
    geo_props: dict[int, tuple[str | None, str, str]],
) -> tuple[str | None, str]:
    """Département et pays d'une ligne (voir la docstring du module)."""
    region_cell = region_cell.strip() if region_cell else None
    stored = geo_props.get(offer_id) if offer_id is not None else None
    stored_region: str | None = None
    if stored is not None:
        stored_department, stored_country, stored_location = stored
        try:
            stored_region = geo.region_for(stored_department, stored_country) or None
        except ValueError:
            stored_region = "?"
        if stored_region == region_cell and (location or "") == stored_location:
            return stored_department, stored_country
    department, country = infer_location(location)
    if fmt != "v2" or region_cell is None:
        return department, country
    # Région inchangée et localisation non reconnue : la valeur stockée, cohérente avec la Région
    # (qui prime), est gardée plutôt que de perdre le pays étranger ou le département.
    unrecognised = department is None and country == geo.FRANCE
    keep_stored = stored is not None and unrecognised and stored_region == region_cell
    if region_cell == geo.HORS_FRANCE:
        if keep_stored:
            return None, stored[1]
        return None, country  # pays étranger déduit gardé ; sinon France sans département
    if region_cell in geo.REGIONS:
        if keep_stored:
            return stored[0], geo.FRANCE
        if country != geo.FRANCE:
            return None, geo.FRANCE  # région française saisie : l'étranger déduit est écarté
        if department is not None and geo.region_for(department, country) != region_cell:
            return None, geo.FRANCE
    return department, country


def import_workbook(
    conn: sqlite3.Connection, source: str | Path | BinaryIO, *, replace: bool = False
) -> ImportResult:
    """Valide tout le classeur puis importe les offres (ids conservés) en une transaction.

    Sans ``replace``, un id déjà présent en base est une erreur (rien n'est importé).
    Avec ``replace``, toutes les offres existantes sont supprimées dans la même transaction.
    Toute erreur lève XlsxImportError et laisse la base inchangée.
    """
    offers, sheet, fmt = read_workbook(source)
    return import_parsed(conn, offers, sheet, fmt, replace=replace)


def import_parsed(
    conn: sqlite3.Connection, offers: list[ParsedOffer], sheet: str, fmt: str, *, replace: bool = False
) -> ImportResult:
    """Importe des offres déjà lues par ``read_workbook`` (atomique, voir ``import_workbook``)."""
    if not replace:
        wanted = {o.offer_id for o in offers if o.offer_id is not None}
        taken = sorted(row["id"] for row in repository.list_rows(conn) if row["id"] in wanted)
        if taken:
            raise XlsxImportError(
                [f"Numéros de dossier déjà en base : {', '.join(map(str, taken))} (utiliser --replace)."]
            )
    try:
        imported, replaced = repository.import_offers(
            conn, [(o.offer_id, o.data) for o in offers], replace=replace
        )
    except repository.OfferExists as exc:
        raise XlsxImportError(
            [
                f"Numéro de dossier {exc.args[0]} déjà utilisé (en base ou en double dans le fichier) : "
                "rien n'a été importé."
            ]
        ) from exc
    except (KeyError, ValueError, sqlite3.Error) as exc:
        raise XlsxImportError([f"Écriture en base refusée, rien n'a été importé : {exc}"]) from exc
    return ImportResult(sheet=sheet, format=fmt, imported=imported, replaced=replaced)
