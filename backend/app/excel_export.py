"""Export de la base vers un classeur Excel identique au suivi v2 (reference/build_excel_v2.py).

``build_workbook(offers, follow_up_days)`` reproduit les onglets Suivi / Synthèse / Listes : mêmes
colonnes dans le même ordre, tableau "Offres", listes déroulantes, cases ☐/☑, couleurs de statut,
urgence des deadlines, lignes Rejeté/Abandon estompées, "Jours restants" en formule de colonne,
compteurs, graphique par statut et carte de France en cases avec menu de statut.

Différences de contenu avec le script v2 (les données viennent de la base, pas de l'Excel d'origine) :

- Région = dérivée de department/country (geo.region_for), jamais devinée à l'export ;
- Deadline = date si ``deadline``, sinon ``deadline_note`` ("?" ou "/"), sinon vide ;
- Link(s) = liens joints par une espace ; lien hypertexte vers le premier lien ;
- "Offre trouvée sur" : lien hypertexte si le texte est une URL seule (comme v2) ;
- Candidature envoyée / Relance faite = application_sent / follow_up_done ;
- Listes!D2 (délai de relance) = ``follow_up_days`` de la configuration.

Le département et le pays de chaque offre sont aussi écrits en propriétés personnalisées du
document (invisibles, ``geo.<id>`` = "2A|FR|Ajaccio", avec la localisation exportée) : à la
réimportation ils sont repris tels quels seulement si les colonnes Région ET Localisation de la ligne
n'ont pas été modifiées dans Excel (sinon le département est déduit de la localisation).
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import FormulaRule
from openpyxl.packaging.custom import StringProperty
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.table import Table, TableFormula, TableStyleInfo

from . import geo
from .schemas import STATUS_UNSET, STATUSES

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# (intitulé dans l'Excel d'origine ou None si colonne ajoutée par v2, intitulé v2, largeur)
COLS: tuple[tuple[str | None, str, int], ...] = (
    ("Id (Dossier associé)", "Id (Dossier associé)", 11),
    ("Status", "Statut", 13),
    ("DeadLine", "Deadline", 13),
    (None, "Jours restants", 14),
    ("Intitulé", "Intitulé", 46),
    ("Entrerprise", "Entreprise", 26),
    ("Type de contrat", "Type de contrat", 14),
    ("Localisation", "Localisation", 20),
    (None, "Région", 24),
    (None, "Candidature envoyée", 13),
    ("Date Candidature", "Date Candidature", 16),
    (None, "Relance faite", 10),
    ("Problème majeur", "Problème majeur", 34),
    ("Motif rejet si rejet", "Motif rejet si rejet", 36),
    ("Offre trouvée sur", "Offre trouvée sur", 26),
    ("Description", "Description", 44),
    ("Link(s)", "Link(s)", 34),
)
NAME = {c[1]: i for i, c in enumerate(COLS, start=1)}
L = {k: get_column_letter(v) for k, v in NAME.items()}

STATUTS = list(STATUSES)
CK_ON, CK_OFF = "☑", "☐"
GEO_PROP_PREFIX = "geo."

# palette (fond, texte)
PAL = {
    "À postuler": ("FEF3C7", "92400E"),
    "En cours": ("DBEAFE", "1E40AF"),
    "Entretien": ("EDE9FE", "5B21B6"),
    "Offre reçue": ("D1FAE5", "065F46"),
    "Rejeté": ("FEE2E2", "991B1B"),
    "Abandon": ("E5E7EB", "4B5563"),
    STATUS_UNSET: ("FFEDD5", "9A3412"),
}
FONT = "Arial"
INK, MUTED, GRID = "1F2937", "6B7280", "E5E7EB"
URL = re.compile(r"https?://\S+")
LAST_CF = 300  # formats / validations déjà prêts pour les futures lignes

REGIONS = list(geo.REGIONS)
# Cases de la carte : (ligne, colonne, région), de haut en bas et d'ouest en est.
TILES = [
    (0, 1, "Normandie"),
    (0, 2, "Hauts-de-France"),
    (0, 3, "Grand Est"),
    (1, 0, "Bretagne"),
    (1, 1, "Pays de la Loire"),
    (1, 2, "Île-de-France"),
    (1, 3, "Bourgogne-Franche-Comté"),
    (2, 1, "Nouvelle-Aquitaine"),
    (2, 2, "Centre-Val de Loire"),
    (2, 3, "Auvergne-Rhône-Alpes"),
    (3, 2, "Occitanie"),
    (3, 3, "Provence-Alpes-Côte d'Azur"),
    (3, 4, "Corse"),
]

WRAP_COLS = {
    "Intitulé",
    "Entreprise",
    "Localisation",
    "Région",
    "Problème majeur",
    "Motif rejet si rejet",
    "Description",
    "Type de contrat",
}
CENTER_COLS = {
    "Id (Dossier associé)",
    "Statut",
    "Deadline",
    "Jours restants",
    "Candidature envoyée",
    "Date Candidature",
    "Relance faite",
    "Type de contrat",
}
URL_COLS = {"Offre trouvée sur", "Link(s)"}
WIDTHS = {h: w for _, h, w in COLS}

# Colonne v2 -> champ de l'offre (colonnes de texte recopiées telles quelles).
TEXT_COLUMNS = {
    "Intitulé": "title",
    "Entreprise": "company",
    "Type de contrat": "contract_type",
    "Localisation": "location",
    "Problème majeur": "main_issue",
    "Motif rejet si rejet": "rejection_reason",
    "Offre trouvée sur": "source",
    "Description": "description",
}


def _fill(color: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=color, end_color=color)


def _est_lines(text: object, width: int) -> int:
    per_line = max(1, int(width * 1.15))
    return sum(max(1, math.ceil(len(p) / per_line)) for p in str(text).split("\n"))


def _as_datetime(value: Any) -> datetime | None:
    """date/datetime ou texte ISO -> datetime naïf (Excel n'a pas de fuseau)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raise TypeError(f"Date inattendue : {value!r}")


def _cell_values(offer: Mapping[str, Any]) -> dict[str, Any]:
    """Valeurs des colonnes v2 pour une offre (None = cellule vide)."""
    values: dict[str, Any] = {h: offer.get(f) for h, f in TEXT_COLUMNS.items()}
    values["Id (Dossier associé)"] = offer.get("id")
    values["Statut"] = offer.get("status")
    deadline = _as_datetime(offer.get("deadline"))
    values["Deadline"] = deadline if deadline is not None else (offer.get("deadline_note") or None)
    values["Date Candidature"] = _as_datetime(offer.get("applied_at"))
    values["Région"] = geo.region_for(offer.get("department"), offer.get("country"))
    links = list(offer.get("links") or [])
    values["Link(s)"] = " ".join(links) if links else None
    for h in TEXT_COLUMNS:
        if values[h] == "":
            values[h] = None
    return values


def _write_listes(wb: Workbook, ws_l: Any, follow_up_days: int) -> None:
    ws_l["A1"] = "Statuts"
    for i, s in enumerate(STATUTS, start=2):
        ws_l.cell(i, 1, s)
    ws_l["C1"] = "Paramètres"
    ws_l["C2"] = "Délai de relance (jours)"
    ws_l["D2"] = follow_up_days
    ws_l["E2"] = (
        "Valeur par défaut choisie à la création (modifiable) : un « En cours » dont la candidature "
        "date de plus de ce nombre de jours, sans relance cochée, est surligné dans la colonne Relance faite."
    )
    for c in ("A1", "C1"):
        ws_l[c].font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        ws_l[c].fill = PatternFill("solid", start_color=INK)
    for r in range(2, 8):
        ws_l.cell(r, 1).font = Font(name=FONT, size=10)
    ws_l["C2"].font = Font(name=FONT, size=10)
    ws_l["D2"].font = Font(name=FONT, size=10, color="0000FF", bold=True)
    ws_l["D2"].fill = PatternFill("solid", start_color="FFFF00")
    ws_l["D2"].alignment = Alignment(horizontal="center")
    ws_l["E2"].font = Font(name=FONT, size=9, italic=True, color=MUTED)
    ws_l["E2"].alignment = Alignment(wrap_text=True, vertical="top")
    for col, width in (("A", 18), ("B", 3), ("C", 26), ("D", 10), ("E", 70)):
        ws_l.column_dimensions[col].width = width
    ws_l.row_dimensions[2].height = 38
    ws_l.sheet_properties.tabColor = "9CA3AF"
    wb.defined_names["Statuts"] = DefinedName("Statuts", attr_text="Listes!$A$2:$A$7")
    wb.defined_names["DelaiRelance"] = DefinedName("DelaiRelance", attr_text="Listes!$D$2")

    ws_l["G1"] = "Régions (menu déroulant)"
    ws_l["G1"].font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
    ws_l["G1"].fill = PatternFill("solid", start_color=INK)
    for i, reg in enumerate(REGIONS, start=2):
        ws_l.cell(i, 7, reg).font = Font(name=FONT, size=10)
    ws_l.cell(
        len(REGIONS) + 3,
        7,
        "« Hors France » = offre située à l'étranger. Ces noms alimentent la carte de la Synthèse : "
        "ne pas les renommer.",
    ).font = Font(name=FONT, size=9, italic=True, color=MUTED)
    ws_l.column_dimensions["F"].width = 3
    ws_l.column_dimensions["G"].width = 30
    wb.defined_names["Regions"] = DefinedName("Regions", attr_text=f"Listes!$G$2:$G${len(REGIONS) + 1}")


def _write_suivi(ws: Any, offers: list[Mapping[str, Any]]) -> None:
    thin = Side(style="thin", color=GRID)
    cell_border = Border(bottom=thin)
    for ci, (_, h, w) in enumerate(COLS, start=1):
        c = ws.cell(1, ci, h)
        quick = h in ("Statut", "Deadline", "Jours restants")
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", start_color="1D4ED8" if quick else INK)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 34

    for ri, offer in enumerate(offers, start=2):
        values = _cell_values(offer)
        max_lines = 1
        for _, h, _ in COLS:
            c = ws.cell(ri, NAME[h])
            c.font = Font(name=FONT, size=10, color=INK)
            c.border = cell_border
            c.alignment = Alignment(
                vertical="center",
                horizontal="center" if h in CENTER_COLS else "left",
                wrap_text=(h in WRAP_COLS or h in URL_COLS),
            )
            if h not in values:
                continue
            v = values[h]
            if v is not None:
                c.value = v
            if isinstance(v, datetime):
                has_time = (v.hour, v.minute) != (0, 0)
                c.number_format = "dd/mm/yyyy hh:mm" if has_time else "dd/mm/yyyy"
            if h in URL_COLS and isinstance(v, str):
                link = None
                if h == "Link(s)":
                    link = offer["links"][0]
                elif URL.fullmatch(v.strip()):
                    link = v.strip()
                if link:
                    c.hyperlink = link
                    c.font = Font(name=FONT, size=9, color="1D4ED8", underline="single")
            if h in WRAP_COLS and v is not None:
                max_lines = max(max_lines, _est_lines(v, WIDTHS[h]))
        for h, on in (
            ("Candidature envoyée", offer.get("application_sent")),
            ("Relance faite", offer.get("follow_up_done")),
        ):
            c = ws.cell(ri, NAME[h], CK_ON if on else CK_OFF)
            c.font = Font(name="Segoe UI Symbol", size=13, color=INK)
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(ri, NAME["Statut"]).font = Font(name=FONT, size=10, bold=True, color=INK)
        ws.row_dimensions[ri].height = max(30, min(13.5 * max_lines + 8, 62))
        ws.cell(ri, NAME["Id (Dossier associé)"]).font = Font(name=FONT, size=10, color=MUTED)

    # Un tableau Excel doit avoir au moins une ligne de données : ligne vide si aucune offre.
    last = 1 + max(1, len(offers))
    last_cf = max(LAST_CF, last)

    st_ref = "Offres[[#This Row],[Statut]]"
    dl_ref = "Offres[[#This Row],[Deadline]]"
    formula_body = f'IF(AND(ISNUMBER({dl_ref}),{st_ref}<>"Rejeté",{st_ref}<>"Abandon"),{dl_ref}-TODAY(),"")'
    jr = NAME["Jours restants"]
    for ri in range(2, last + 1):
        c = ws.cell(ri, jr, "=" + formula_body)
        c.number_format = '"dans "0" j";"il y a "0" j";"Aujourd\'hui"'
        c.font = Font(name=FONT, size=10, bold=True, color=INK)

    tab = Table(displayName="Offres", ref=f"A1:{get_column_letter(len(COLS))}{last}")
    tab.tableStyleInfo = TableStyleInfo(
        name=None, showFirstColumn=False, showLastColumn=False, showRowStripes=False, showColumnStripes=False
    )
    ws.add_table(tab)
    tab._initialise_columns()
    for col, (_, h, _) in zip(tab.tableColumns, COLS, strict=True):
        col.name = h
    tab.tableColumns[jr - 1].calculatedColumnFormula = TableFormula(attr_text=formula_body)

    ws.freeze_panes = ws.cell(2, NAME["Intitulé"] + 1)
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "1D4ED8"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_title_rows = "1:1"

    def rg(col: str) -> str:
        return f"{col}2:{col}{last_cf}"

    dv_st = DataValidation(
        type="list",
        formula1="Statuts",
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Statut inconnu",
        error="Choisis un statut dans la liste (onglet Listes).",
    )
    dv_st.add(rg(L["Statut"]))
    dv_ck = DataValidation(
        type="list",
        formula1=f'"{CK_OFF},{CK_ON}"',
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Case à cocher",
        error=f"Choisis {CK_OFF} ou {CK_ON}.",
    )
    dv_ck.add(rg(L["Candidature envoyée"]))
    dv_ck.add(rg(L["Relance faite"]))
    dv_reg = DataValidation(
        type="list",
        formula1="Regions",
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Région inconnue",
        error="Choisis une région dans la liste (« Hors France » pour l'étranger).",
    )
    dv_reg.add(rg(L["Région"]))
    ws.add_data_validation(dv_st)
    ws.add_data_validation(dv_ck)
    ws.add_data_validation(dv_reg)

    b, c_, d = L["Statut"], L["Deadline"], L["Jours restants"]
    e_, i_, j_, k_ = L["Intitulé"], L["Candidature envoyée"], L["Date Candidature"], L["Relance faite"]
    lastcol = get_column_letter(len(COLS))

    # 1) cellule Statut : une couleur par statut
    for s, (bg, fg) in PAL.items():
        if s == STATUS_UNSET:
            f = f'AND(${e_}2<>"",ISNA(MATCH(${b}2,Statuts,0)))'
        else:
            f = f'${b}2="{s}"'
        ws.conditional_formatting.add(
            rg(b), FormulaRule(formula=[f], font=Font(color=fg, bold=True), fill=_fill(bg))
        )

    # 2) Deadline + Jours restants : urgence (seulement si l'offre est vivante)
    n = f"ISNUMBER(${d}2)"
    rules = [
        (f"AND({n},${d}2=0)", Font(color="FFFFFF", bold=True), _fill("DC2626"), False),
        (f"AND({n},${d}2>=1,${d}2<=3)", Font(color="C2410C", bold=True), _fill("FFEDD5"), False),
        (f"AND({n},${d}2>=4,${d}2<=7)", Font(color="854D0E", bold=True), _fill("FEF9C3"), False),
        (f"AND({n},${d}2>7)", Font(color="166534", bold=True), _fill("DCFCE7"), False),
        (f'AND({n},${d}2<0,${b}2="À postuler")', Font(color="991B1B", bold=True), _fill("FEE2E2"), True),
        (f"AND({n},${d}2<0)", Font(color=MUTED, italic=True), None, False),
    ]
    for f, font, fl, stop in rules:
        rule = (
            FormulaRule(formula=[f], font=font, fill=fl, stopIfTrue=stop)
            if fl
            else FormulaRule(formula=[f], font=font)
        )
        ws.conditional_formatting.add(f"{c_}2:{d}{last_cf}", rule)

    # 3) cases à cocher : la relance suggérée passe en premier (et stoppe)
    ws.conditional_formatting.add(
        rg(k_),
        FormulaRule(
            formula=[
                f'AND(${b}2="En cours",${k_}2="{CK_OFF}",ISNUMBER(${j_}2),TODAY()-${j_}2>=DelaiRelance)'
            ],
            font=Font(color="92400E", bold=True),
            fill=_fill("FDE68A"),
            stopIfTrue=True,
        ),
    )
    for col in (i_, k_):
        ws.conditional_formatting.add(
            rg(col), FormulaRule(formula=[f'${col}2="{CK_ON}"'], font=Font(color="15803D"))
        )
        ws.conditional_formatting.add(
            rg(col), FormulaRule(formula=[f'${col}2="{CK_OFF}"'], font=Font(color="9CA3AF"))
        )

    # 4) lignes mortes (Rejeté / Abandon) : tout le reste de la ligne s'estompe
    ws.conditional_formatting.add(
        f"A2:{lastcol}{last_cf}",
        FormulaRule(
            formula=[f'OR(${b}2="Rejeté",${b}2="Abandon")'], font=Font(color="9CA3AF"), fill=_fill("F9FAFB")
        ),
    )


def _write_synthese(s: Any) -> None:
    thin = Side(style="thin", color=GRID)
    cell_border = Border(bottom=thin)
    s.sheet_view.showGridLines = False
    s.sheet_properties.tabColor = "0F766E"
    for col in "ABCDEFG":
        s.column_dimensions[col].width = 19
    s["A1"] = "Synthèse de ma recherche"
    s["A1"].font = Font(name=FONT, size=18, bold=True, color=INK)
    s["A2"] = "Aujourd'hui :"
    s["A2"].font = Font(name=FONT, size=10, color=MUTED)
    s["B2"] = "=TODAY()"
    s["B2"].number_format = "dd/mm/yyyy"
    s["B2"].font = Font(name=FONT, size=10, color=MUTED, bold=True)
    s["B2"].alignment = Alignment(horizontal="left")

    tiles = [
        ("Offres suivies", "=B17", INK, "F3F4F6"),
        ("Candidatures envoyées", f'=COUNTIF(Offres[Candidature envoyée],"{CK_ON}")', INK, "F3F4F6"),
        ("En cours", '=COUNTIF(Offres[Statut],"En cours")', *PAL["En cours"][::-1]),
        ("Entretiens", '=COUNTIF(Offres[Statut],"Entretien")', *PAL["Entretien"][::-1]),
        ("Rejetées", '=COUNTIF(Offres[Statut],"Rejeté")', *PAL["Rejeté"][::-1]),
        (
            "Deadlines ≤ 7 jours",
            '=COUNTIFS(Offres[Jours restants],">=0",Offres[Jours restants],"<=7")',
            "C2410C",
            "FFEDD5",
        ),
        (STATUS_UNSET, "=B16", *PAL[STATUS_UNSET][::-1]),
    ]
    box = Border(left=Side(style="thin", color="FFFFFF"), right=Side(style="thin", color="FFFFFF"))
    for i, (lab, f, fg, bg) in enumerate(tiles):
        col = get_column_letter(i + 1)
        a, b = s[f"{col}4"], s[f"{col}5"]
        a.value, b.value = lab, f
        a.font = Font(name=FONT, size=9, color=fg if bg != "F3F4F6" else MUTED, bold=True)
        b.font = Font(name=FONT, size=24, bold=True, color=fg)
        for x in (a, b):
            x.fill = _fill(bg)
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            x.border = box
        s.row_dimensions[4].height = 28
        s.row_dimensions[5].height = 44
    s.row_dimensions[3].height = 10

    # Répartition par statut : lignes 10..15 (6 statuts), 16 (à qualifier), 17 (total)
    s["A8"] = "Répartition par statut"
    s["A8"].font = Font(name=FONT, size=12, bold=True, color=INK)
    for ci, h in enumerate(["Statut", "Nombre", "Part"], start=1):
        c = s.cell(9, ci, h)
        c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        c.fill = _fill(INK)
        c.alignment = Alignment(horizontal="center")
    order = STATUTS + [STATUS_UNSET]
    for i, st in enumerate(order):
        r = 10 + i
        s.cell(r, 1, st)
        if st == STATUS_UNSET:
            s.cell(r, 2, "=B17-SUM(B10:B15)")
        else:
            s.cell(r, 2, f'=COUNTIF(Offres[Statut],"{st}")')
        s.cell(r, 3, f"=IF($B$17=0,0,B{r}/$B$17)")
        bg, fg = PAL[st]
        s.cell(r, 1).fill = _fill(bg)
        s.cell(r, 1).font = Font(name=FONT, size=10, bold=True, color=fg)
        for ci in (2, 3):
            s.cell(r, ci).font = Font(name=FONT, size=10, color=INK)
            s.cell(r, ci).alignment = Alignment(horizontal="center")
        s.cell(r, 3).number_format = "0%"
        for ci in (1, 2, 3):
            s.cell(r, ci).border = cell_border
    s["A17"], s["B17"] = "Total", "=COUNTA(Offres[Intitulé])"
    s["C17"] = "=IF(B17=0,0,SUM(C10:C16))"
    s["C17"].number_format = "0%"
    for ci in (1, 2, 3):
        c = s.cell(17, ci)
        c.font = Font(name=FONT, size=10, bold=True, color=INK)
        c.border = Border(top=Side(style="thin", color=INK))
        if ci > 1:
            c.alignment = Alignment(horizontal="center")

    # Graphique par statut
    ch = BarChart()
    ch.type = "bar"
    ch.style = 10
    ch.title = None
    ch.legend = None
    ch.y_axis.delete = True
    ch.x_axis.scaling.orientation = "maxMin"
    ch.x_axis.delete = False
    ch.y_axis.majorGridlines = None
    ch.add_data(Reference(s, min_col=2, min_row=9, max_row=16), titles_from_data=True)
    ch.set_categories(Reference(s, min_col=1, min_row=10, max_row=16))
    ser = ch.series[0]
    ser.graphicalProperties.solidFill = "1D4ED8"
    for idx, st in enumerate(order):
        pt = DataPoint(idx=idx)
        pt.graphicalProperties.solidFill = PAL[st][1] if st != "Abandon" else "6B7280"
        ser.dPt.append(pt)
    ser.dLbls = DataLabelList()
    ser.dLbls.showVal = True
    ser.dLbls.showSerName = False
    ser.dLbls.showCatName = False
    ser.dLbls.showLegendKey = False
    ch.gapWidth = 60
    ch.height, ch.width = 7.2, 11.5
    s.add_chart(ch, "E8")

    # Légende
    s["A20"] = "Légende"
    s["A20"].font = Font(name=FONT, size=12, bold=True, color=INK)
    legend = [
        ("Aujourd'hui", "DC2626", "FFFFFF", "Deadline = aujourd'hui (offre non rejetée / non abandonnée)"),
        ("1 à 3 jours", "FFEDD5", "C2410C", "Deadline imminente"),
        ("4 à 7 jours", "FEF9C3", "854D0E", "Deadline proche"),
        ("Plus de 7 jours", "DCFCE7", "166534", "Il reste du temps"),
        ("Manquée", "FEE2E2", "991B1B", "Deadline passée alors que le statut est « À postuler »"),
        (
            "Relance suggérée",
            "FDE68A",
            "92400E",
            "« En cours » sans relance cochée après le délai réglé dans l'onglet Listes",
        ),
        (
            "Ligne grisée",
            "F9FAFB",
            "9CA3AF",
            "Offre Rejeté ou Abandon : elle s'efface pour laisser voir le reste",
        ),
    ]
    for i, (lab, bg, fg, txt) in enumerate(legend):
        r = 21 + i
        a = s.cell(r, 1, lab)
        a.fill, a.font = _fill(bg), Font(name=FONT, size=10, bold=True, color=fg)
        a.alignment = Alignment(horizontal="center")
        s.cell(r, 2, txt).font = Font(name=FONT, size=10, color=INK)
    s["A29"] = "Ajouter une offre"
    s["A29"].font = Font(name=FONT, size=12, bold=True, color=INK)
    s["A30"] = (
        "Écris directement sous la dernière ligne du tableau de l'onglet Suivi : "
        "listes déroulantes, couleurs, filtres et calcul des jours restants se prolongent automatiquement."
    )
    s["A30"].font = Font(name=FONT, size=10, color=INK)
    s["A31"] = "Cases à cocher : clique la cellule puis la flèche pour choisir ☐ ou ☑."
    s["A31"].font = Font(name=FONT, size=10, color=INK)

    # Carte de France (une case par région)
    s["A34"] = "Carte des offres par région"
    s["A34"].font = Font(name=FONT, size=12, bold=True, color=INK)
    s["A35"] = "Afficher :"
    s["A35"].font = Font(name=FONT, size=10, color=MUTED)
    s["A35"].alignment = Alignment(horizontal="right", vertical="center")
    s["B35"] = "Toutes"
    s["B35"].font = Font(name=FONT, size=11, bold=True, color="0000FF")
    s["B35"].fill = _fill("FEF9C3")
    s["B35"].alignment = Alignment(horizontal="center", vertical="center")
    s["B35"].border = Border(*(Side(style="thin", color="CA8A04"),) * 4)
    s["C35"] = "← choisis un statut (En cours, Rejeté…) pour voir où il se trouve sur la carte"
    s["C35"].font = Font(name=FONT, size=9, italic=True, color=MUTED)
    s["C35"].alignment = Alignment(vertical="center")
    s.row_dimensions[35].height = 22
    s.row_dimensions[36].height = 8
    dv_map = DataValidation(
        type="list",
        formula1='"Toutes,' + ",".join(STATUTS) + '"',
        allow_blank=False,
        showErrorMessage=True,
        errorTitle="Choix inconnu",
        error="Choisis « Toutes » ou un statut de la liste.",
    )
    s.add_data_validation(dv_map)
    dv_map.add("B35")

    m0 = 37  # première ligne de la carte ; chaque case = 1 ligne de nom + 1 ligne de nombre
    white = Side(style="medium", color="FFFFFF")
    sel = "$B$35"
    for rr in range(4):
        s.row_dimensions[m0 + 2 * rr].height = 30
        s.row_dimensions[m0 + 2 * rr + 1].height = 38
    for r, c, name in TILES:
        nm, ct = s.cell(m0 + 2 * r, c + 1), s.cell(m0 + 2 * r + 1, c + 1)
        nm.value = name
        ct.value = (
            f'=IF({sel}="Toutes",COUNTIF(Offres[Région],{nm.coordinate}),'
            f"COUNTIFS(Offres[Région],{nm.coordinate},Offres[Statut],{sel}))"
        )
        nm.font = Font(name=FONT, size=9, bold=True, color=INK)
        nm.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ct.font = Font(name=FONT, size=22, bold=True, color=INK)
        ct.alignment = Alignment(horizontal="center", vertical="center")
        for x, bd in (
            (nm, Border(left=white, right=white, top=white)),
            (ct, Border(left=white, right=white, bottom=white)),
        ):
            x.fill = _fill("F3F4F6")
            x.border = bd
    map_range = f"A{m0}:E{m0 + 7}"
    v = f"IF(ISNUMBER(A{m0}),A{m0},IF(ISNUMBER(A{m0 + 1}),A{m0 + 1},-1))"
    mx = f"MAX($A${m0}:$E${m0 + 7})"
    shades = [
        (f"{v}=0", "F3F4F6", "9CA3AF"),
        (f"AND({v}>0,{v}<=0.25*{mx})", "DBEAFE", "1E3A8A"),
        (f"AND({v}>0.25*{mx},{v}<=0.5*{mx})", "93C5FD", "1E3A8A"),
        (f"AND({v}>0.5*{mx},{v}<=0.75*{mx})", "2563EB", "FFFFFF"),
        (f"{v}>0.75*{mx}", "1E40AF", "FFFFFF"),
    ]
    for cond, bg, fg in shades:
        s.conditional_formatting.add(
            map_range, FormulaRule(formula=[cond], font=Font(color=fg), fill=_fill(bg), stopIfTrue=True)
        )

    off = m0 + 9  # ligne des libellés hors carte
    off_items = [
        (geo.HORS_FRANCE, f'Offres[Région],"{geo.HORS_FRANCE}"'),
        ("Sans région", 'Offres[Région],""'),
    ]
    for i, (lab, crit) in enumerate(off_items):
        a, b = s.cell(off, i + 1, lab), s.cell(off + 1, i + 1)
        b.value = f'=IF({sel}="Toutes",COUNTIF({crit}),COUNTIFS({crit},Offres[Statut],{sel}))'
        a.font = Font(name=FONT, size=9, bold=True, color=MUTED)
        b.font = Font(name=FONT, size=16, bold=True, color=INK)
    a, b = s.cell(off, 3, "Total affiché"), s.cell(off + 1, 3)
    b.value = f"=SUM(A{m0}:E{m0 + 7})+SUM(A{off + 1}:B{off + 1})"
    a.font = Font(name=FONT, size=9, bold=True, color=MUTED)
    b.font = Font(name=FONT, size=16, bold=True, color=INK)
    for ci in range(1, 4):
        for x in (s.cell(off, ci), s.cell(off + 1, ci)):
            x.fill = _fill("F3F4F6")
            x.alignment = Alignment(horizontal="center", vertical="center")
            x.border = Border(left=white, right=white)
    s.row_dimensions[m0 + 8].height = 8
    s.row_dimensions[off].height = 20
    s.row_dimensions[off + 1].height = 30
    notes_map = [
        "Carte schématique : une case par région, placée approximativement. Plus la case est foncée, "
        "plus il y a d'offres.",
        "La région se choisit dans la colonne Région du tableau Suivi (menu déroulant, pré-remplie d'après "
        "ta localisation).",
    ]
    for i, t in enumerate(notes_map):
        s.cell(off + 3 + i, 1, t).font = Font(name=FONT, size=9, italic=True, color=MUTED)


def build_workbook(offers: Iterable[Mapping[str, Any]], follow_up_days: int) -> Workbook:
    """Classeur v2 à partir des offres (dictionnaires OfferOut ou équivalents), dans l'ordre donné."""
    offers = list(offers)
    wb = Workbook()
    ws = wb.active
    ws.title = "Suivi"
    ws_s = wb.create_sheet("Synthèse")
    ws_l = wb.create_sheet("Listes")

    _write_listes(wb, ws_l, follow_up_days)
    _write_suivi(ws, offers)
    _write_synthese(ws_s)

    for sh in (ws_s, ws_l):
        sh.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        sh.page_setup.fitToWidth = 1
    ws_s.page_setup.orientation = "portrait"
    ws_s.page_setup.fitToHeight = 1
    ws_l.page_setup.orientation = "landscape"
    ws_l.page_setup.fitToHeight = 0

    # Département et pays exacts (la colonne Région seule ne permet pas de les retrouver).
    for offer in offers:
        if offer.get("id") is None:
            continue
        department = offer.get("department") or ""
        country = offer.get("country") or geo.FRANCE
        location = offer.get("location") or ""
        wb.custom_doc_props.append(
            StringProperty(name=f"{GEO_PROP_PREFIX}{offer['id']}", value=f"{department}|{country}|{location}")
        )

    wb.calculation.fullCalcOnLoad = True
    wb.active = 0
    return wb


def workbook_bytes(offers: Iterable[Mapping[str, Any]], follow_up_days: int) -> bytes:
    """Classeur v2 sérialisé (.xlsx)."""
    buffer = io.BytesIO()
    build_workbook(offers, follow_up_days).save(buffer)
    return buffer.getvalue()
