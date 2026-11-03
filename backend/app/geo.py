"""Référentiel géographique : département (code INSEE, toujours une chaîne) -> région.

La région n'est jamais stockée : elle est dérivée du département, ou vaut "Hors France"
si le pays n'est pas la France. L'ordre officiel du référentiel (2A et 2B entre 19 et 21)
est le seul ordre de tri ; aucun tri numérique.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HORS_FRANCE = "Hors France"
FRANCE = "FR"

# Ordre des régions identique à build_excel_v2.py (REGIONS), "Hors France" en dernier.
_REGION_ORDER = (
    "Auvergne-Rhône-Alpes",
    "Bourgogne-Franche-Comté",
    "Bretagne",
    "Centre-Val de Loire",
    "Corse",
    "Grand Est",
    "Hauts-de-France",
    "Île-de-France",
    "Normandie",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Pays de la Loire",
    "Provence-Alpes-Côte d'Azur",
)


def _referential_path() -> Path:
    """Trouve data/departements.json depuis le dépôt ou dans le conteneur.

    Ordre : variable GEO_REFERENTIAL, puis <racine du dépôt>/data, puis <dossier backend>/data
    (copie possible dans l'image), puis ./data.
    """
    env = os.environ.get("GEO_REFERENTIAL")
    here = Path(__file__).resolve()
    candidates = [Path(env)] if env else []
    candidates += [
        here.parents[2] / "data" / "departements.json",
        here.parents[1] / "data" / "departements.json",
        Path.cwd() / "data" / "departements.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Référentiel des départements introuvable ; chemins essayés : " + ", ".join(map(str, candidates))
    )


def _load() -> list[dict[str, Any]]:
    with _referential_path().open(encoding="utf-8") as f:
        data = json.load(f)
    for entry in data:
        if not isinstance(entry.get("code"), str):
            raise ValueError(f"Code de département non textuel dans le référentiel : {entry.get('code')!r}")
    return data


DEPARTMENTS: tuple[dict[str, Any], ...] = tuple(_load())
# Métropole seulement pour l'instant ; les 971-976 viendront avec "overseas": true.
DEPARTMENT_CODES: tuple[str, ...] = tuple(d["code"] for d in DEPARTMENTS)
DEPARTMENT_NAMES: dict[str, str] = {d["code"]: d["name"] for d in DEPARTMENTS}
DEPARTMENT_REGION: dict[str, str] = {d["code"]: d["region_name"] for d in DEPARTMENTS}
REGIONS: tuple[str, ...] = _REGION_ORDER + (HORS_FRANCE,)

_INDEX = {code: i for i, code in enumerate(DEPARTMENT_CODES)}

if set(DEPARTMENT_REGION.values()) != set(_REGION_ORDER):
    raise ValueError(
        "Les régions du référentiel ne correspondent pas aux 13 régions métropolitaines attendues."
    )


def validate_department(code: object) -> str:
    """Renvoie le code tel quel s'il est valide ; sinon ValueError (message en français).

    Aucune normalisation : "1" n'est pas converti en "01", un entier est refusé.
    """
    if not isinstance(code, str):
        raise ValueError(
            f"Le code de département doit être une chaîne de caractères (reçu : {code!r} de type "
            f"{type(code).__name__})."
        )
    if code not in _INDEX:
        raise ValueError(
            f'Code de département inconnu : {code!r}. Codes attendus : "01" à "95", "2A", "2B" '
            '("20" n\'existe plus).'
        )
    return code


def region_for(department: str | None, country: str | None = FRANCE) -> str | None:
    """Région dérivée : "Hors France" si le pays n'est pas FR, None sans département.

    Un pays absent ou vide vaut FR ; la casse est ignorée ("fr" == "FR").
    """
    if country is not None and country.strip() and country.strip().upper() != FRANCE:
        return HORS_FRANCE
    if department is None or department == "":
        return None
    return DEPARTMENT_REGION[validate_department(department)]


def department_sort_key(code: str | None) -> int:
    """Clé de tri selon l'ordre officiel du référentiel ; codes absents ou inconnus en dernier."""
    if code is None:
        return len(DEPARTMENT_CODES) + 1
    return _INDEX.get(code, len(DEPARTMENT_CODES))
