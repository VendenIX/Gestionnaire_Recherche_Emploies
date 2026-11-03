"""Import Excel (original et v2) et export fidèle au classeur v2."""

from __future__ import annotations

import io
import sqlite3
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from backend.app import db, excel_export, excel_import, geo, import_xlsx, repository
from backend.app.main import create_app
from backend.app.schemas import STATUSES
from backend.tests.conftest import TOKEN, make_settings

REFERENCE = Path(__file__).resolve().parents[2] / "reference"
ORIGINAL = REFERENCE / "Offres de contrats.xlsx"
V2 = REFERENCE / "Offres_de_contrats_v2.xlsx"
needs_reference = pytest.mark.skipif(
    not (ORIGINAL.is_file() and V2.is_file()),
    reason="classeurs de référence absents (reference/ est ignoré par git) : comparaison impossible",
)

# Seules différences admises entre l'export (original -> base -> export) et le v2 de référence.
# Clé : (feuille, cellule, aspect comparé). Aucune valeur exportée ne dépend de la date du jour :
# « Jours restants », « Aujourd'hui » et les compteurs sont des formules (comparées en texte).
ALLOWED_DIFFERENCES = {
    ("Suivi", "B5", "value"): "statut « ? » de l'original stocké null (décision du propriétaire, "
    "D-001), exporté vide ; la mise en forme « À qualifier » s'applique toujours",
    ("Suivi", "Q2", "hyperlink"): "plusieurs URL dans Link(s) ; l'original portait un lien hypertexte "
    "vers la 2e, la base ne garde que la liste : l'export pointe vers la 1re",
    ("Suivi", "O24", "hyperlink"): "« Offre trouvée sur » = une URL en texte, lien hypertexte d'origine "
    "avec « / » final ; la source est du texte, le lien est le texte lui-même",
}


def _conn(settings):
    return db.connect(db.init_db(settings.data_dir))


def _offers(conn) -> list[dict]:
    return [repository.row_to_offer(r, 14) for r in repository.list_rows(conn)]


def _comparable(offers: list[dict]) -> list[dict]:
    drop = {"created_at", "updated_at", "days_left", "follow_up_suggested"}
    return [{k: v for k, v in o.items() if k not in drop} for o in offers]


def _style(c) -> tuple:
    return (repr(c.font), repr(c.fill), repr(c.border), repr(c.alignment), c.number_format)


def _cell_differences(ref_wb, out_wb) -> set[tuple[str, str, str]]:
    found = set()
    for name in ref_wb.sheetnames:
        a, b = ref_wb[name], out_wb[name]
        rows, cols = max(a.max_row, b.max_row), max(a.max_column, b.max_column)
        for r in range(1, rows + 1):
            for ci in range(1, cols + 1):
                x, y = a.cell(r, ci), b.cell(r, ci)
                if x.value != y.value or type(x.value) is not type(y.value):
                    found.add((name, x.coordinate, "value"))
                hx = (x.hyperlink.target, x.hyperlink.location) if x.hyperlink else None
                hy = (y.hyperlink.target, y.hyperlink.location) if y.hyperlink else None
                if hx != hy:
                    found.add((name, x.coordinate, "hyperlink"))
                if _style(x) != _style(y):
                    found.add((name, x.coordinate, "style"))
    return found


def _structure(wb) -> dict:
    """Tout ce qui n'est pas une cellule : tableau, validations, formats conditionnels, graphique…"""
    out: dict = {"sheets": wb.sheetnames, "names": {}}
    for n, dn in wb.defined_names.items():
        out["names"][n] = dn.attr_text
    for ws in wb.worksheets:
        s = out[ws.title] = {}
        s["tables"] = [
            (
                t.name,
                t.ref,
                [
                    (c.name, c.calculatedColumnFormula.attr_text if c.calculatedColumnFormula else None)
                    for c in t.tableColumns
                ],
            )
            for t in ws.tables.values()
        ]
        s["validations"] = [
            (d.type, d.formula1, str(d.sqref), d.allow_blank, d.showErrorMessage, d.errorTitle, d.error)
            for d in ws.data_validations.dataValidation
        ]
        s["conditional"] = [
            (str(cf.sqref), [(r.type, r.formula, r.stopIfTrue, repr(r.dxf)) for r in cf.rules])
            for cf in ws.conditional_formatting
        ]
        s["charts"] = [
            (type(ch).__name__, ch.type, ch.anchor._from.col, ch.anchor._from.row, len(ch.series))
            for ch in ws._charts
        ]
        s["chart_series"] = [
            [
                (
                    se.tx.strRef.f if se.tx and se.tx.strRef else None,
                    se.cat.strRef.f if se.cat and se.cat.strRef else None,
                    se.cat.numRef.f if se.cat and se.cat.numRef else None,
                    se.val.numRef.f if se.val and se.val.numRef else None,
                    [(p.idx, repr(p.graphicalProperties)) for p in se.dPt],
                )
                for se in ch.series
            ]
            for ch in ws._charts
        ]
        s["freeze"] = ws.freeze_panes
        s["widths"] = {k: v.width for k, v in ws.column_dimensions.items()}
        s["heights"] = {k: v.height for k, v in ws.row_dimensions.items() if v.height is not None}
        s["grid"] = ws.sheet_view.showGridLines
        s["tab"] = repr(ws.sheet_properties.tabColor)
        s["print"] = (ws.page_setup.orientation, ws.page_setup.fitToWidth, ws.page_setup.fitToHeight)
    return out


# --- 1) original -> base -> export == v2 -----------------------------------------------------------


@needs_reference
def test_export_matches_v2_cell_by_cell(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        conn = _conn(settings)
        try:
            result = excel_import.import_workbook(conn, ORIGINAL)
        finally:
            conn.close()
        assert result.format == "original" and len(result.imported) == 25
        resp = client.get("/api/export.xlsx", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    out = openpyxl.load_workbook(io.BytesIO(resp.content))
    ref = openpyxl.load_workbook(V2)

    differences = _cell_differences(ref, out)
    assert differences == set(ALLOWED_DIFFERENCES), sorted(differences ^ set(ALLOWED_DIFFERENCES))
    assert ref["Suivi"]["B5"].value == "?" and out["Suivi"]["B5"].value is None

    ref_s, out_s = _structure(ref), _structure(out)
    assert out_s == ref_s
    # garde-fous explicites sur ce qui fait la v2
    assert out_s["Suivi"]["tables"][0][:2] == ("Offres", "A1:Q26")
    assert out_s["Synthèse"]["charts"] == [("BarChart", "bar", 4, 7, 1)]
    series = out_s["Synthèse"]["chart_series"][0][0]
    assert series[2:4] == ("'Synthèse'!$A$10:$A$16", "'Synthèse'!$B$10:$B$16") and len(series[4]) == 7
    assert out["Suivi"]["D2"].value.startswith("=IF(AND(ISNUMBER(Offres[[#This Row],[Deadline]])")
    assert out["Synthèse"]["B5"].value == '=COUNTIF(Offres[Candidature envoyée],"☑")'
    assert out.calculation.fullCalcOnLoad is True


@needs_reference
def test_import_original_values(tmp_path):
    # Classeur réel du propriétaire : structure et comptes seulement, jamais son contenu.
    conn = _conn(make_settings(tmp_path))
    try:
        excel_import.import_workbook(conn, ORIGINAL)
        offers = {o["id"]: o for o in _offers(conn)}
    finally:
        conn.close()
    assert sorted(offers) == list(range(1, 26))
    assert all(o["title"] for o in offers.values())
    assert all(o["status"] is None or o["status"] in STATUSES for o in offers.values())
    assert all(o["department"] is None or o["department"] in geo.DEPARTMENT_CODES for o in offers.values())
    assert all(len(o["country"]) == 2 and o["country"].isupper() for o in offers.values())
    assert all(o["applied_at"] is None or isinstance(o["applied_at"], datetime) for o in offers.values())
    assert all(o["deadline"] is None or isinstance(o["deadline"], date) for o in offers.values())
    assert all(o["application_sent"] == (o["applied_at"] is not None) for o in offers.values())
    assert not any(o["follow_up_done"] for o in offers.values())
    assert sum(o["status"] is None for o in offers.values()) == 2
    assert sum(o["department"] is not None for o in offers.values()) >= 10


def test_import_original_fixture_values(tmp_path):
    """Classeur au format d'origine inventé : statuts, dates texte, notes de deadline, liens, géo."""
    rows = [
        _row(**{"Id (Dossier associé)": 1, "Intitulé": "Analyste données", "Entrerprise": "Acme",
                "Localisation": "Paris", "Status": "En cours", "Date Candidature": "03/02/2026 à 09h30",
                "DeadLine": datetime(2026, 2, 20),
                "Link(s)": "https://a.example/1\nhttps://b.example/2\nhttps://c.example/3"}),
        _row(**{"Id (Dossier associé)": 2, "Intitulé": "Ingénieur logiciel", "Entrerprise": "Globex",
                "Localisation": "Vélizy-Villacoublay (78)", "Status": "Rejeté", "DeadLine": "?"}),
        _row(**{"Id (Dossier associé)": 3, "Intitulé": "Chef de projet", "Entrerprise": "Initech ",
                "Localisation": "Lille", "Status": "Entretien", "Date Candidature": "14/03/2026 ) 10h05",
                "DeadLine": "/"}),
        _row(**{"Id (Dossier associé)": 4, "Intitulé": "Chargé d'études", "Localisation": "Île-de-France",
                "Status": "?"}),
        _row(**{"Id (Dossier associé)": 5, "Intitulé": "Doctorat", "Status": "À postuler",
                "Localisation": "Mons Belgique - Lille France"}),
        _row(**{"Id (Dossier associé)": 6, "Intitulé": "Consultant", "Localisation": "Quelque part"}),
        _row(**{"Id (Dossier associé)": 7, "Intitulé": "Chercheur", "Localisation": "Genève"}),
    ]
    conn = _conn(make_settings(tmp_path))
    try:
        result = excel_import.import_workbook(conn, _original_workbook(rows))
        offers = {o["id"]: o for o in _offers(conn)}
    finally:
        conn.close()
    assert result.format == "original" and sorted(offers) == list(range(1, 8))
    assert offers[4]["status"] is None  # « ? »
    assert offers[6]["status"] is None  # vide
    assert offers[1]["applied_at"] == datetime(2026, 2, 3, 9, 30)
    assert offers[3]["applied_at"] == datetime(2026, 3, 14, 10, 5)  # « 14/03/2026 ) 10h05 »
    assert offers[1]["deadline"] == date(2026, 2, 20) and offers[1]["deadline_note"] is None
    assert offers[2]["deadline"] is None and offers[2]["deadline_note"] == "?"
    assert offers[3]["deadline"] is None and offers[3]["deadline_note"] == "/"
    assert offers[6]["deadline"] is None and offers[6]["deadline_note"] is None
    assert offers[1]["application_sent"] and not offers[4]["application_sent"]
    assert not any(o["follow_up_done"] for o in offers.values())
    assert len(offers[1]["links"]) == 3
    assert offers[3]["company"] == "Initech "  # texte conservé tel quel
    expected = {1: ("75", "FR"), 2: ("78", "FR"), 3: ("59", "FR"), 5: ("59", "FR"),
                4: (None, "FR"), 6: (None, "FR"), 7: (None, "CH")}
    assert {i: (o["department"], o["country"]) for i, o in offers.items()} == expected


# --- 3) v2 et original donnent la même base --------------------------------------------------------


@needs_reference
def test_import_v2_equals_import_original(tmp_path):
    results = []
    for i, source in enumerate((ORIGINAL, V2)):
        conn = _conn(make_settings(tmp_path / str(i)))
        try:
            fmt = excel_import.import_workbook(conn, source).format
            results.append((fmt, _comparable(_offers(conn))))
        finally:
            conn.close()
    assert [r[0] for r in results] == ["original", "v2"]
    assert results[0][1] == results[1][1]


@needs_reference
def test_export_reimport_is_stable(tmp_path):
    conn = _conn(make_settings(tmp_path / "a"))
    try:
        excel_import.import_workbook(conn, ORIGINAL)
        first = _offers(conn)
    finally:
        conn.close()
    data = excel_export.workbook_bytes(first, 14)
    conn = _conn(make_settings(tmp_path / "b"))
    try:
        excel_import.import_workbook(conn, io.BytesIO(data))
        assert _comparable(_offers(conn)) == _comparable(first)
    finally:
        conn.close()


# --- 2) 2A / 01 : base -> API -> export -> import ----------------------------------------------------


def test_department_codes_round_trip_as_strings(tmp_path):
    settings = make_settings(tmp_path / "src")
    payloads = [
        # « Porto-Vecchio » n'est pas dans la table de mots-clés, « Lyon » donnerait 69 :
        # le département ne peut venir que de l'export lui-même.
        {"id": 7, "title": "Corse du Sud", "location": "Porto-Vecchio", "department": "2A"},
        {"id": 8, "title": "Ain", "location": "Lyon", "department": "01"},
        # sans département en base : l'export le garde absent (pas de déduction depuis « (01) »)
        {"id": 9, "title": "Bourg", "location": "Bourg-en-Bresse (01)"},
        {"id": 10, "title": "Genève", "location": "Genève", "country": "CH"},
    ]
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        for p in payloads:
            assert client.post("/api/offers", json=p).status_code == 201, p
        resp = client.get("/api/export.xlsx")
    assert resp.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    regions = [wb["Suivi"].cell(r, excel_export.NAME["Région"]).value for r in range(2, 6)]
    assert regions == ["Corse", "Auvergne-Rhône-Alpes", None, "Hors France"]

    target = make_settings(tmp_path / "dst")
    conn = _conn(target)
    try:
        excel_import.import_workbook(conn, io.BytesIO(resp.content))
        stored = conn.execute(
            "SELECT id, department, typeof(department) AS t FROM offers ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [(r["id"], r["department"]) for r in stored] == [(7, "2A"), (8, "01"), (9, None), (10, None)]
    assert all(r["t"] in ("text", "null") for r in stored)
    with TestClient(create_app(target)) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        got = {o["id"]: o for o in client.get("/api/offers").json()}
    assert got[7]["department"] == "2A" and got[7]["region"] == "Corse"
    assert got[8]["department"] == "01" and got[8]["region"] == "Auvergne-Rhône-Alpes"
    assert got[9]["department"] is None and got[9]["region"] is None
    assert got[10]["country"] == "CH" and got[10]["region"] == "Hors France"


def _reparse(offers: list[dict], edits: dict[tuple[int, str], object] | None = None) -> list[dict]:
    """Export -> modifications de cellules « dans Excel » -> relecture : données par ligne."""
    wb = excel_export.build_workbook(offers, 14)
    for (row, header), value in (edits or {}).items():
        wb["Suivi"].cell(row, excel_export.NAME[header]).value = value
    buf = io.BytesIO()
    wb.save(buf)
    parsed, _, fmt = excel_import.read_workbook(io.BytesIO(buf.getvalue()))
    assert fmt == "v2"
    return [p.data for p in parsed]


def _geo_offer(offer_id: int, location: str | None, department: str | None, country: str = "FR") -> dict:
    return {
        "id": offer_id, "title": "x", "location": location, "department": department,
        "country": country, "links": [],
    }


def test_region_edited_in_excel_is_not_contradicted():
    # Région « Bretagne » saisie, Localisation Lyon (69, Auvergne-Rhône-Alpes) : pas de 69 imposé.
    got = _reparse([_geo_offer(3, "Lyon", "01")], {(2, "Région"): "Bretagne"})[0]
    assert (got["department"], got["country"]) == (None, "FR")
    # Région cohérente avec la localisation : le département déduit est gardé.
    got = _reparse([_geo_offer(3, "Lyon", "01")], {(2, "Région"): "Auvergne-Rhône-Alpes"})[0]
    assert (got["department"], got["country"]) == ("01", "FR")  # propriété geo reprise telle quelle
    edits = {(2, "Région"): "Auvergne-Rhône-Alpes", (2, "Localisation"): "Lyon 3e"}
    got = _reparse([_geo_offer(3, "Lyon", "01")], edits)[0]
    assert (got["department"], got["country"]) == ("69", "FR")
    # « Hors France » + localisation française : ni département ni pays déduits (France par défaut).
    got = _reparse([_geo_offer(3, "Paris", "75")], {(2, "Région"): "Hors France"})[0]
    assert (got["department"], got["country"]) == (None, "FR")
    # « Hors France » + localisation étrangère : le pays déduit est gardé.
    edits = {(2, "Région"): "Hors France", (2, "Localisation"): "Berlin"}
    got = _reparse([_geo_offer(3, "Paris", "75")], edits)[0]
    assert (got["department"], got["country"]) == (None, "DE")


def test_geo_properties_reused_only_if_region_and_location_unchanged():
    # Localisation modifiée, Région inchangée (même région) : déduction, pas l'ancien 75.
    got = _reparse([_geo_offer(1, "Paris", "75")], {(2, "Localisation"): "Versailles"})[0]
    assert got["department"] == "78"
    # Rien de modifié : la propriété est reprise (département non déductible de la localisation).
    got = _reparse([_geo_offer(1, "Quelque part", "2A")])[0]
    assert got["department"] == "2A"
    # Numéros de dossier échangés dans Excel (mêmes régions) : chaque offre garde son département.
    offers = [_geo_offer(1, "Paris", "75"), _geo_offer(2, "Versailles", "78")]
    got = _reparse(offers, {(2, "Id (Dossier associé)"): 2, (3, "Id (Dossier associé)"): 1})
    assert [(g["location"], g["department"]) for g in got] == [("Paris", "75"), ("Versailles", "78")]


def test_unchanged_region_keeps_stored_geo_for_unrecognised_location():
    # « Hors France » inchangée, localisation non reconnue : le pays étranger stocké est gardé.
    got = _reparse([_geo_offer(1, "Berlin", None, "DE")], {(2, "Localisation"): "Remote EU"})[0]
    assert (got["location"], got["department"], got["country"]) == ("Remote EU", None, "DE")
    # Un pays étranger déduit de la nouvelle localisation l'emporte sur le pays stocké.
    got = _reparse([_geo_offer(1, "Berlin", None, "DE")], {(2, "Localisation"): "Genève"})[0]
    assert (got["department"], got["country"]) == (None, "CH")
    # Région française inchangée, lieu non reconnu : le département stocké (dans la Région) est gardé.
    got = _reparse([_geo_offer(1, "Ajaccio", "2A")], {(2, "Localisation"): "Porto-Vecchio"})[0]
    assert (got["department"], got["country"]) == ("2A", "FR")
    # Région modifiée : le département stocké (autre région) n'est pas repris.
    edits = {(2, "Région"): "Bretagne", (2, "Localisation"): "Porto-Vecchio"}
    got = _reparse([_geo_offer(1, "Ajaccio", "2A")], edits)[0]
    assert (got["department"], got["country"]) == (None, "FR")
    # « Hors France » saisie sur une offre française : pas de reprise du stocké (France, sans dép.).
    edits = {(2, "Région"): "Hors France", (2, "Localisation"): "Remote EU"}
    got = _reparse([_geo_offer(1, "Paris", "75")], edits)[0]
    assert (got["department"], got["country"]) == (None, "FR")


def test_import_parsed_maps_unknown_column_to_import_error(tmp_path):
    conn = _conn(make_settings(tmp_path))
    try:
        bad = excel_import.ParsedOffer(row=2, offer_id=None, data={"title": "x", "nope": 1})
        with pytest.raises(excel_import.XlsxImportError, match="rien n'a été importé"):
            excel_import.import_parsed(conn, [bad], "Suivi", "v2")
        assert repository.list_rows(conn) == []
    finally:
        conn.close()


def test_deadline_cells():
    offers = [_geo_offer(i, None, None) for i in (1, 2, 3, 4)]
    got = _reparse(
        offers,
        {
            (2, "Deadline"): 46000,  # numéro de série Excel au format Standard
            (3, "Deadline"): "fin juin",  # texte libre : conservé en note (règle documentée)
            (4, "Deadline"): "/",
            (5, "Deadline"): "15/07/2026",
        },
    )
    assert (got[0]["deadline"], got[0]["deadline_note"]) == (date(2025, 12, 9), None)
    assert (got[1]["deadline"], got[1]["deadline_note"]) == (None, "fin juin")
    assert (got[2]["deadline"], got[2]["deadline_note"]) == (None, "/")
    assert (got[3]["deadline"], got[3]["deadline_note"]) == (date(2026, 7, 15), None)
    with pytest.raises(excel_import.XlsxImportError, match="Deadline"):
        _reparse([_geo_offer(1, None, None)], {(2, "Deadline"): -3})


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Vélizy-Villacoublay (78)", ("78", "FR")),
        ("(78)", ("78", "FR")),
        ("Paris", ("75", "FR")),
        ("Paris 11e - Métro Bastille", ("75", "FR")),
        ("Velizy", ("78", "FR")),
        ("Créteil", ("94", "FR")),
        ("Toulouse", ("31", "FR")),
        ("Bordeaux", ("33", "FR")),
        ("Strasbourg", ("67", "FR")),
        ("Grenoble", ("38", "FR")),
        ("Lyon (69)", ("69", "FR")),
        ("Nancy", ("54", "FR")),
        ("Ajaccio", ("2A", "FR")),
        ("Bastia", ("2B", "FR")),
        ("Bourg-en-Bresse (01)", ("01", "FR")),
        ("Mons Belgique - Lille France", ("59", "FR")),
        ("Berlin", (None, "DE")),
        ("Genève", (None, "CH")),
        ("Île-de-France", (None, "FR")),  # région entière : département ambigu
        ("Quelque part", (None, "FR")),
        ("", (None, "FR")),
        (None, (None, "FR")),
    ],
)
def test_infer_location(location, expected):
    assert excel_import.infer_location(location) == expected


def test_parse_date_str():
    assert excel_import.parse_date_str("03/02/2026 à 09h30") == datetime(2026, 2, 3, 9, 30)
    assert excel_import.parse_date_str("14/03/2026 ) 10h05") == datetime(2026, 3, 14, 10, 5)
    assert excel_import.parse_date_str(" 01/02/2026 ") == datetime(2026, 2, 1)
    assert excel_import.parse_date_str("bientôt") is None


# --- Lecture des cellules ----------------------------------------------------------------------------


def _original_workbook(rows: list[list]) -> io.BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Feuil1"
    ws.append([c[0] for c in excel_export.COLS if c[0]])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _row(**values) -> list:
    headers = [c[0] for c in excel_export.COLS if c[0]]
    return [values.get(h) for h in headers]


def test_original_cells_read_as_text_and_empty_as_none(tmp_path):
    buf = _original_workbook(
        [
            _row(
                **{
                    "Id (Dossier associé)": 3,
                    "Intitulé": "Poste",
                    "Type de contrat": 12,
                    "Status": "?",
                    "DeadLine": "  ",
                    "Date Candidature": "05/10/2026",
                    "Link(s)": "",
                    "Description": "",
                }
            ),
            _row(),  # ligne vide : ignorée
            _row(
                **{
                    "Intitulé": "Sans id",
                    "DeadLine": datetime(2026, 12, 1),
                    "Status": "En cours",
                    "Link(s)": "https://a.example/x\nhttps://b.example/y",
                }
            ),
        ]
    )
    parsed, sheet, fmt = excel_import.read_workbook(buf)
    assert (sheet, fmt) == ("Feuil1", "original")
    assert len(parsed) == 2
    first, second = parsed[0].data, parsed[1].data
    assert parsed[0].offer_id == 3 and parsed[1].offer_id is None
    assert first["contract_type"] == "12" and first["status"] is None
    assert first["deadline"] is None and first["deadline_note"] is None
    assert first["description"] is None and first["links"] == []
    assert first["applied_at"] == datetime(2026, 10, 5) and first["application_sent"] is True
    assert second["deadline"] == date(2026, 12, 1) and second["application_sent"] is False
    assert second["links"] == ["https://a.example/x", "https://b.example/y"]


def test_invalid_rows_are_reported_and_nothing_is_written(tmp_path):
    buf = _original_workbook(
        [
            _row(**{"Id (Dossier associé)": 1, "Intitulé": "ok"}),
            _row(**{"Id (Dossier associé)": 2, "Intitulé": "x", "Status": "Inconnu"}),
            _row(**{"Id (Dossier associé)": 3, "Intitulé": "y", "Date Candidature": "demain"}),
            _row(**{"Id (Dossier associé)": 1, "Intitulé": "doublon"}),
        ]
    )
    conn = _conn(make_settings(tmp_path))
    try:
        with pytest.raises(excel_import.XlsxImportError) as info:
            excel_import.import_workbook(conn, buf)
        assert [e.split(" :")[0] for e in info.value.errors] == ["Ligne 3", "Ligne 4", "Ligne 5"]
        assert repository.list_rows(conn) == []
    finally:
        conn.close()


def test_existing_ids_need_replace(tmp_path):
    conn = _conn(make_settings(tmp_path))
    try:
        repository.create_offer(conn, {"title": "déjà là"}, offer_id=1)
        repository.create_offer(conn, {"title": "autre"}, offer_id=50)
        buf = _original_workbook([_row(**{"Id (Dossier associé)": 1, "Intitulé": "nouveau"})])
        with pytest.raises(excel_import.XlsxImportError, match="--replace"):
            excel_import.import_workbook(conn, buf)
        buf.seek(0)
        result = excel_import.import_workbook(conn, buf, replace=True)
        assert result.replaced == 2 and result.imported == [1]
        assert [o["title"] for o in _offers(conn)] == ["nouveau"]
    finally:
        conn.close()


# --- Export ------------------------------------------------------------------------------------------


def test_export_requires_auth_and_sets_headers(client, anon):
    assert anon.get("/api/export.xlsx").status_code == 401
    resp = client.get("/api/export.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == excel_export.XLSX_MEDIA_TYPE
    expected = f'attachment; filename="suivi-offres-{date.today().isoformat()}.xlsx"'
    assert resp.headers["content-disposition"] == expected
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["Suivi", "Synthèse", "Listes"]
    # base vide : le tableau garde une ligne de données vide (exigence d'Excel), réimportable
    assert wb["Suivi"].tables["Offres"].ref == "A1:Q2"
    assert excel_import.read_workbook(io.BytesIO(resp.content))[0] == []


def test_export_cells_from_db_values():
    offers = [
        {
            "id": 1,
            "title": "A",
            "status": "En cours",
            "deadline": date(2026, 5, 4),
            "deadline_note": "?",
            "applied_at": datetime(2026, 4, 1, 9, 30),
            "application_sent": True,
            "follow_up_done": True,
            "department": "13",
            "country": "FR",
            "links": ["https://x.example/1", "https://x.example/2"],
            "source": "https://jobs.example",
        },
        {
            "id": 2,
            "title": "B",
            "status": None,
            "deadline": None,
            "deadline_note": "/",
            "applied_at": None,
            "application_sent": False,
            "follow_up_done": False,
            "department": None,
            "country": "FR",
            "links": [],
        },
    ]
    ws = excel_export.build_workbook(offers, 21)["Suivi"]
    col = excel_export.NAME
    assert ws.cell(2, col["Deadline"]).value == datetime(2026, 5, 4)  # la date l'emporte sur la note
    assert ws.cell(2, col["Deadline"]).number_format == "dd/mm/yyyy"
    assert ws.cell(3, col["Deadline"]).value == "/"
    assert ws.cell(2, col["Date Candidature"]).number_format == "dd/mm/yyyy hh:mm"
    assert ws.cell(2, col["Candidature envoyée"]).value == "☑"
    assert ws.cell(2, col["Relance faite"]).value == "☑"
    assert ws.cell(3, col["Relance faite"]).value == "☐"
    assert ws.cell(2, col["Région"]).value == "Provence-Alpes-Côte d'Azur"
    assert ws.cell(3, col["Région"]).value is None and ws.cell(3, col["Statut"]).value is None
    assert ws.cell(2, col["Link(s)"]).value == "https://x.example/1 https://x.example/2"
    assert ws.cell(2, col["Link(s)"]).hyperlink.target == "https://x.example/1"
    assert ws.cell(2, col["Offre trouvée sur"]).hyperlink.target == "https://jobs.example"
    assert ws.parent["Listes"]["D2"].value == 21


# --- CLI ---------------------------------------------------------------------------------------------


def test_cli_import_and_replace(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    file = tmp_path / "suivi.xlsx"
    file.write_bytes(
        _original_workbook(
            [_row(**{"Id (Dossier associé)": 4, "Intitulé": "Poste", "Localisation": "Ajaccio"})]
        ).getvalue()
    )
    assert import_xlsx.main([str(file)]) == 0
    assert "1 offre(s) importée(s)" in capsys.readouterr().out
    assert import_xlsx.main([str(file)]) == 1  # id déjà présent
    assert "--replace" in capsys.readouterr().err
    assert import_xlsx.main([str(file), "--replace"]) == 0
    assert list((data_dir / "backups").glob("before-import-*.db"))
    conn = db.connect(db.db_path(data_dir))
    try:
        rows = repository.list_rows(conn)
    finally:
        conn.close()
    assert [(r["id"], r["department"]) for r in rows] == [(4, "2A")]
    assert import_xlsx.main([str(tmp_path / "absent.xlsx")]) == 2


# --- Atomicité de l'import ---------------------------------------------------------------------------


def _ids_titles(conn) -> list[tuple[int, str]]:
    return [(r["id"], r["title"]) for r in repository.list_rows(conn)]


def test_mixed_ids_replace_imports_explicit_ids_first(tmp_path):
    """Revue excel-io-1 (atom.py) : ligne sans id avant une ligne d'id 4, --replace."""
    conn = _conn(make_settings(tmp_path))
    try:
        for i in (1, 2, 3):
            repository.create_offer(conn, {"title": f"old{i}"}, offer_id=i)
        buf = _original_workbook(
            [_row(**{"Intitulé": "noid"}), _row(**{"Id (Dossier associé)": 4, "Intitulé": "four"})]
        )
        result = excel_import.import_workbook(conn, buf, replace=True)
        assert result.replaced == 3 and result.imported == [5, 4]  # ordre du fichier
        assert _ids_titles(conn) == [(4, "four"), (5, "noid")]
    finally:
        conn.close()


def test_cli_mixed_ids_without_replace(tmp_path, monkeypatch, capsys):
    """Revue excel-io-1 (cli2.py) : base vide, [sans id, id 1], sans --replace."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    file = tmp_path / "f.xlsx"
    file.write_bytes(
        _original_workbook(
            [_row(**{"Intitulé": "noid"}), _row(**{"Id (Dossier associé)": 1, "Intitulé": "one"})]
        ).getvalue()
    )
    assert import_xlsx.main([str(file)]) == 0
    conn = db.connect(db.db_path(data_dir))
    try:
        assert _ids_titles(conn) == [(1, "one"), (2, "noid")]
    finally:
        conn.close()


def test_import_offers_rolls_back_everything_on_error(tmp_path):
    conn = _conn(make_settings(tmp_path))
    try:
        for i in (1, 2, 3):
            repository.create_offer(conn, {"title": f"old{i}"}, offer_id=i)
        before = _ids_titles(conn)
        # échec au dernier INSERT (pays invalide pour la contrainte CHECK), après DELETE et 1er INSERT
        bad = [(7, {"title": "ok"}), (None, {"title": "ko", "country": "FRA"})]
        with pytest.raises(sqlite3.IntegrityError):
            repository.import_offers(conn, bad, replace=True)
        assert _ids_titles(conn) == before and not conn.in_transaction
        with pytest.raises(repository.OfferExists):  # id déjà en base, sans replace
            repository.import_offers(conn, [(9, {"title": "a"}), (2, {"title": "b"})])
        with pytest.raises(repository.OfferExists):  # id en double
            repository.import_offers(conn, [(9, {"title": "a"}), (9, {"title": "b"})], replace=True)
        with pytest.raises(ValueError):
            repository.import_offers(conn, [(0, {"title": "a"})], replace=True)
        assert _ids_titles(conn) == before
        # un import réussi sur la même connexion fonctionne toujours
        ids, replaced = repository.import_offers(conn, [(None, {"title": "n"}), (8, {"title": "huit"})])
        assert replaced == 0 and ids == [9, 8]
    finally:
        conn.close()


def test_repository_errors_become_clean_cli_errors(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    file = tmp_path / "f.xlsx"
    file.write_bytes(_original_workbook([_row(**{"Id (Dossier associé)": 1, "Intitulé": "one"})]).getvalue())

    def clash(*args, **kwargs):  # course : l'id 1 est pris entre la vérification et l'écriture
        raise repository.OfferExists(1)

    monkeypatch.setattr(repository, "import_offers", clash)
    assert import_xlsx.main([str(file), "--replace"]) == 1
    err = capsys.readouterr().err
    assert "Import refusé" in err and "Numéro de dossier 1 déjà utilisé" in err
    with pytest.raises(excel_import.XlsxImportError, match="rien n'a été importé"):
        conn = _conn(make_settings(tmp_path / "other"))
        try:
            excel_import.import_workbook(conn, file)
        finally:
            conn.close()
