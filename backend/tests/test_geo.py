"""Tests du référentiel géographique (départements -> régions)."""

import ast
import json
from pathlib import Path

import pytest

from backend.app import geo

ROOT = Path(__file__).resolve().parents[2]
REFERENTIAL = ROOT / "data" / "departements.json"
BUILD_V2 = ROOT / "reference" / "build_excel_v2.py"


def _v2_constant(name: str):
    """Lit une constante littérale de build_excel_v2.py sans exécuter le script."""
    tree = ast.parse(BUILD_V2.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} introuvable dans build_excel_v2.py")


def test_exactly_96_metropolitan_codes():
    assert len(geo.DEPARTMENT_CODES) == 96
    assert len(set(geo.DEPARTMENT_CODES)) == 96


def test_codes_are_json_strings():
    raw = json.loads(REFERENTIAL.read_text(encoding="utf-8"))
    assert len(raw) == 96
    assert all(isinstance(d["code"], str) for d in raw)
    assert all(d["overseas"] is False for d in raw)
    assert all(isinstance(c, str) for c in geo.DEPARTMENT_CODES)


def test_official_order_corsica_between_19_and_21():
    codes = list(geo.DEPARTMENT_CODES)
    i = codes.index("19")
    assert codes[i : i + 4] == ["19", "2A", "2B", "21"]
    assert "20" not in codes
    assert codes[0] == "01" and codes[-1] == "95"
    expected = [f"{i:02d}" for i in range(1, 20)] + ["2A", "2B"] + [str(i) for i in range(21, 96)]
    assert codes == expected


def test_region_for_empty_or_lowercase_country_is_france():
    assert geo.region_for("75", "") == "Île-de-France"
    assert geo.region_for("75", "fr") == "Île-de-France"
    assert geo.region_for("75", None) == "Île-de-France"
    assert geo.region_for(None, "DE") == geo.HORS_FRANCE


def test_regions_13_plus_hors_france_in_v2_order():
    assert len(geo.REGIONS) == 14
    assert geo.REGIONS[-1] == "Hors France"
    assert list(geo.REGIONS) == _v2_constant("REGIONS")


@pytest.mark.parametrize(
    ("dep", "region"),
    [
        ("78", "Île-de-France"),
        ("2A", "Corse"),
        ("2B", "Corse"),
        ("54", "Grand Est"),
        ("01", "Auvergne-Rhône-Alpes"),
    ],
)
def test_sample_mapping(dep, region):
    assert geo.region_for(dep, "FR") == region


def test_department_lists_match_v2_depts():
    depts_v2 = {reg: ds.split() for reg, ds in _v2_constant("DEPTS").items()}
    ours: dict[str, list[str]] = {}
    for d in geo.DEPARTMENTS:
        ours.setdefault(d["region_name"], []).append(d["code"])
    assert {r: sorted(v) for r, v in ours.items()} == {r: sorted(v) for r, v in depts_v2.items()}


def test_region_codes_are_insee():
    expected = {
        "Île-de-France": "11",
        "Centre-Val de Loire": "24",
        "Bourgogne-Franche-Comté": "27",
        "Normandie": "28",
        "Hauts-de-France": "32",
        "Grand Est": "44",
        "Pays de la Loire": "52",
        "Bretagne": "53",
        "Nouvelle-Aquitaine": "75",
        "Occitanie": "76",
        "Auvergne-Rhône-Alpes": "84",
        "Provence-Alpes-Côte d'Azur": "93",
        "Corse": "94",
    }
    got = {d["region_name"]: d["region_code"] for d in geo.DEPARTMENTS}
    assert got == expected


def test_region_for_hors_france_and_missing():
    assert geo.region_for("75", "DE") == "Hors France"
    assert geo.region_for(None, "CH") == "Hors France"
    assert geo.region_for(None, "FR") is None
    assert geo.region_for("", "FR") is None


@pytest.mark.parametrize("bad", ["20", "1", "2a", "00", "96", "971", " 75", 75, 1, None])
def test_validate_department_rejects(bad):
    with pytest.raises(ValueError):
        geo.validate_department(bad)


@pytest.mark.parametrize("good", ["01", "2A", "2B", "75", "95"])
def test_validate_department_accepts_unchanged(good):
    assert geo.validate_department(good) == good


def test_region_for_rejects_invalid_department():
    with pytest.raises(ValueError):
        geo.region_for("20", "FR")
