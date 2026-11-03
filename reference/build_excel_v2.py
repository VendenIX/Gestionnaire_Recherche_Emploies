"""Reconstruit le suivi d'offres d'emploi (lecture de l'original, écriture d'un nouveau classeur).

Usage: python3 -I build.py <original.xlsx> <sortie.xlsx>
"""
import re
import sys
import math
from datetime import datetime

import openpyxl
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableFormula, TableStyleInfo

SRC, DST = sys.argv[1], sys.argv[2]

# ---------------------------------------------------------------- lecture
src = openpyxl.load_workbook(SRC)
ws0 = src.worksheets[0]
hdr0 = [c.value for c in ws0[1]]
rows0 = []
for r in range(2, ws0.max_row + 1):
    rec = {}
    for ci, h in enumerate(hdr0, start=1):
        c = ws0.cell(r, ci)
        link = None
        if c.hyperlink is not None:
            link = c.hyperlink.target or ""
            if c.hyperlink.location:
                link += "#" + c.hyperlink.location
        rec[h] = {"v": c.value, "link": link}
    rows0.append(rec)

# --------------------------------------------------------------- schéma
# (clé dans l'original ou None si nouvelle colonne, intitulé, largeur)
COLS = [
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
]
NAME = {c[1]: i for i, c in enumerate(COLS, start=1)}
L = {k: openpyxl.utils.get_column_letter(v) for k, v in NAME.items()}
assert set(hdr0) == {c[0] for c in COLS if c[0]}, "colonne d'origine non reprise"

STATUTS = ["À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon"]
CK_ON, CK_OFF = "☑", "☐"

# palette (fond, texte)
PAL = {
    "À postuler": ("FEF3C7", "92400E"),
    "En cours": ("DBEAFE", "1E40AF"),
    "Entretien": ("EDE9FE", "5B21B6"),
    "Offre reçue": ("D1FAE5", "065F46"),
    "Rejeté": ("FEE2E2", "991B1B"),
    "Abandon": ("E5E7EB", "4B5563"),
    "À qualifier": ("FFEDD5", "9A3412"),
}
FONT = "Arial"
INK, MUTED, GRID = "1F2937", "6B7280", "E5E7EB"

# ----------------------------------------------------------- nettoyage dates
def parse_date_str(s):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4}).*?(\d{1,2})h(\d{2})", s)
    if m:
        d, mo, y, h, mi = map(int, m.groups())
        return datetime(y, mo, d, h, mi)
    m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{4})\s*", s)
    if m:
        d, mo, y = map(int, m.groups())
        return datetime(y, mo, d)
    return None


URL = re.compile(r"https?://\S+")

# --------------------------------------------------------------- classeur
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Suivi"
ws_s = wb.create_sheet("Synthèse")
ws_l = wb.create_sheet("Listes")

thin = Side(style="thin", color=GRID)
cell_border = Border(bottom=thin)

# ------------------------------------------------------------------ Listes
ws_l["A1"] = "Statuts"
for i, s in enumerate(STATUTS, start=2):
    ws_l.cell(i, 1, s)
ws_l["C1"] = "Paramètres"
ws_l["C2"] = "Délai de relance (jours)"
ws_l["D2"] = 14
ws_l["E2"] = ("Valeur par défaut choisie à la création (modifiable) : un « En cours » dont la candidature "
              "date de plus de ce nombre de jours, sans relance cochée, est surligné dans la colonne Relance faite.")
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
ws_l.column_dimensions["A"].width = 18
ws_l.column_dimensions["B"].width = 3
ws_l.column_dimensions["C"].width = 26
ws_l.column_dimensions["D"].width = 10
ws_l.column_dimensions["E"].width = 70
ws_l.row_dimensions[2].height = 38
ws_l.sheet_properties.tabColor = "9CA3AF"
wb.defined_names["Statuts"] = DefinedName("Statuts", attr_text="Listes!$A$2:$A$7")
wb.defined_names["DelaiRelance"] = DefinedName("DelaiRelance", attr_text="Listes!$D$2")

# --------------------------------------------- Table de correspondance localisation -> région
# Un mot-clé est cherché dans la colonne Localisation (sans tenir compte des majuscules, mais accents compris).
# Si plusieurs mots-clés correspondent, le plus BAS de la liste l'emporte : l'étranger est donc listé en premier.
FOREIGN = ("Allemagne|Germany|Berlin|Munich|Heidelberg|Belgique|Bruxelles|Suisse|Genève|Lausanne|Zurich|"
           "Luxembourg|Royaume-Uni|Londres|London|Irlande|Dublin|Pays-Bas|Amsterdam|Espagne|Barcelone|Madrid|Italie|"
           "Milan|Danemark|Copenhague|Suède|Stockholm|Canada|Montréal|Québec|États-Unis|Etats-Unis|Singapour|Japon|"
           "Tokyo|Chine|Maroc|Tunisie|Israël")
DEPTS = {
    "Île-de-France": "75 77 78 91 92 93 94 95",
    "Hauts-de-France": "02 59 60 62 80",
    "Normandie": "14 27 50 61 76",
    "Bretagne": "22 29 35 56",
    "Pays de la Loire": "44 49 53 72 85",
    "Centre-Val de Loire": "18 28 36 37 41 45",
    "Grand Est": "08 10 51 52 54 55 57 67 68 88",
    "Bourgogne-Franche-Comté": "21 25 39 58 70 71 89 90",
    "Nouvelle-Aquitaine": "16 17 19 23 24 33 40 47 64 79 86 87",
    "Auvergne-Rhône-Alpes": "01 03 07 15 26 38 42 43 63 69 73 74",
    "Occitanie": "09 11 12 30 31 32 34 46 48 65 66 81 82",
    "Provence-Alpes-Côte d'Azur": "04 05 06 13 83 84",
    "Corse": "2A 2B",
}
PLACES = {
    "Île-de-France": ("Île-de-France|Ile-de-France|Paris|Vélizy|Velizy|Saclay|Palaiseau|Orsay|Gif-sur-Yvette|"
                      "Bures-sur-Yvette|Massy|Boulogne-Billancourt|Issy-les-Moulineaux|Nanterre|La Défense|La Defense|"
                      "Courbevoie|Puteaux|Montrouge|Évry|Evry|Villejuif|Créteil|Creteil|Saint-Denis|Montreuil|"
                      "Versailles|Guyancourt|Rueil-Malmaison|Levallois|Clichy|Ivry-sur-Seine|Kremlin-Bicêtre|"
                      "Le Plessis-Robinson|Châtillon|Malakoff|Vincennes|Cergy|Marne-la-Vallée|Champs-sur-Marne|"
                      "Meudon|Suresnes|Neuilly-sur-Seine|Saint-Ouen|Aubervilliers|Pantin|Bagneux|Cachan|"
                      "Fontenay-aux-Roses|Fontenay-sous-Bois|Colombes|Asnières|Gennevilliers|Argenteuil|Antony|"
                      "Rungis|Saint-Quentin-en-Yvelines"),
    "Hauts-de-France": ("Hauts-de-France|Lille|Villeneuve-d'Ascq|Roubaix|Tourcoing|Amiens|Compiègne|Dunkerque|"
                        "Valenciennes|Arras|Calais|Beauvais|Boulogne-sur-Mer"),
    "Normandie": "Normandie|Rouen|Caen|Le Havre|Cherbourg|Évreux|Alençon",
    "Bretagne": "Bretagne|Rennes|Brest|Lorient|Vannes|Quimper|Saint-Brieuc|Lannion|Saint-Malo",
    "Pays de la Loire": "Pays de la Loire|Nantes|Angers|Le Mans|Saint-Nazaire|La Roche-sur-Yon|Cholet",
    "Centre-Val de Loire": "Centre-Val de Loire|Orléans|Orleans|Tours|Bourges|Blois|Chartres|Châteauroux",
    "Grand Est": ("Grand Est|Strasbourg|Nancy|Metz|Reims|Mulhouse|Colmar|Illkirch|Troyes|Charleville-Mézières|"
                  "Épinal|Vandoeuvre|Thionville|Châlons"),
    "Bourgogne-Franche-Comté": ("Bourgogne-Franche-Comté|Dijon|Besançon|Besancon|Belfort|Montbéliard|Auxerre|"
                                "Chalon-sur-Saône|Mâcon|Nevers"),
    "Nouvelle-Aquitaine": ("Nouvelle-Aquitaine|Bordeaux|Poitiers|Limoges|La Rochelle|Pessac|Talence|Mérignac|"
                           "Merignac|Bayonne|Biarritz|Anglet|Angoulême|Niort|Périgueux"),
    "Auvergne-Rhône-Alpes": ("Auvergne-Rhône-Alpes|Lyon|Villeurbanne|Grenoble|Saint-Étienne|Saint-Etienne|"
                             "Clermont-Ferrand|Annecy|Chambéry|Chambery|Valence|Meylan|Échirolles|Echirolles|"
                             "Vénissieux|Venissieux|Saint-Martin-d'Hères|Montbonnot|Aubière"),
    "Occitanie": ("Occitanie|Toulouse|Montpellier|Nîmes|Nimes|Perpignan|Béziers|Beziers|Tarbes|Carcassonne|"
                  "Castres|Montauban|Labège|Labege|Blagnac|Colomiers|Narbonne"),
    "Provence-Alpes-Côte d'Azur": ("Provence-Alpes-Côte d'Azur|PACA|Côte d'Azur|Marseille|Nice|Aix-en-Provence|"
                                   "Aix-Marseille|Sophia Antipolis|Valbonne|Antibes|Cannes|Toulon|Avignon"),
    "Corse": "Corse|Ajaccio|Bastia",
}
VILLES = [(k, "Hors France") for k in FOREIGN.split("|")]
for reg in PLACES:  # ordre = priorité croissante (le dernier gagne)
    VILLES += [(f"({d})", reg) for d in DEPTS[reg].split()]
    VILLES += [(k, reg) for k in PLACES[reg].split("|")]
assert set(DEPTS) == set(PLACES)
assert sum(len(v.split()) for v in DEPTS.values()) == 96

REGIONS = ["Auvergne-Rhône-Alpes", "Bourgogne-Franche-Comté", "Bretagne", "Centre-Val de Loire", "Corse",
           "Grand Est", "Hauts-de-France", "Île-de-France", "Normandie", "Nouvelle-Aquitaine", "Occitanie",
           "Pays de la Loire", "Provence-Alpes-Côte d'Azur", "Hors France"]
assert set(REGIONS) == set(PLACES) | {"Hors France"}


def guess_region(loc):
    """Pré-remplissage à la création uniquement (le dernier mot clé trouvé l'emporte : l'étranger est testé en premier,
    pour que « Belgique - Lille » soit rangé en France). Rien n'est déduit ensuite : la région est ensuite saisie."""
    if loc is None or not str(loc).strip():
        return None
    low = str(loc).lower()
    found = None
    for k, reg in VILLES:
        if k.lower() in low:
            found = reg
    return found


ws_l["G1"] = "Régions (menu déroulant)"
ws_l["G1"].font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
ws_l["G1"].fill = PatternFill("solid", start_color=INK)
for i, reg in enumerate(REGIONS, start=2):
    ws_l.cell(i, 7, reg).font = Font(name=FONT, size=10)
ws_l.cell(len(REGIONS) + 3, 7,
          "« Hors France » = offre située à l'étranger. Ces noms alimentent la carte de la Synthèse : ne pas les renommer."
          ).font = Font(name=FONT, size=9, italic=True, color=MUTED)
ws_l.column_dimensions["F"].width = 3
ws_l.column_dimensions["G"].width = 30
wb.defined_names["Regions"] = DefinedName("Regions", attr_text=f"Listes!$G$2:$G${len(REGIONS) + 1}")

# ------------------------------------------------------------------- Suivi
for ci, (_, h, w) in enumerate(COLS, start=1):
    c = ws.cell(1, ci, h)
    quick = h in ("Statut", "Deadline", "Jours restants")
    c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
    c.fill = PatternFill("solid", start_color="1D4ED8" if quick else INK)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w
ws.row_dimensions[1].height = 34

WRAP_COLS = {"Intitulé", "Entreprise", "Localisation", "Région", "Problème majeur", "Motif rejet si rejet",
              "Description", "Type de contrat"}
CENTER_COLS = {"Id (Dossier associé)", "Statut", "Deadline", "Jours restants", "Candidature envoyée",
               "Date Candidature", "Relance faite", "Type de contrat"}
URL_COLS = {"Offre trouvée sur", "Link(s)"}
widths = {h: w for _, h, w in COLS}


def est_lines(text, width):
    per_line = max(1, int(width * 1.15))
    return sum(max(1, math.ceil(len(p) / per_line)) for p in str(text).split("\n"))


for ri, rec in enumerate(rows0, start=2):
    max_lines = 1
    for src_key, h, w in COLS:
        ci = NAME[h]
        c = ws.cell(ri, ci)
        c.font = Font(name=FONT, size=10, color=INK)
        c.border = cell_border
        c.alignment = Alignment(
            vertical="center",
            horizontal="center" if h in CENTER_COLS else "left",
            wrap_text=(h in WRAP_COLS or h in URL_COLS),
        )
        if src_key is None:
            continue
        v = rec[src_key]["v"]
        link = rec[src_key]["link"]
        if h == "Date Candidature" and isinstance(v, str):
            parsed = parse_date_str(v)
            assert parsed is not None, v
            v = parsed
        if v is not None:
            c.value = v
        if isinstance(v, datetime):
            has_time = (v.hour, v.minute) != (0, 0)
            c.number_format = "dd/mm/yyyy hh:mm" if has_time else "dd/mm/yyyy"
        if h in URL_COLS and v is not None:
            if link is None and isinstance(v, str) and URL.fullmatch(v.strip()):
                link = v.strip()
            if link:
                c.hyperlink = link
                c.font = Font(name=FONT, size=9, color="1D4ED8", underline="single")
        if h in WRAP_COLS and v is not None:
            max_lines = max(max_lines, est_lines(v, widths[h]))
    # --- colonnes ajoutées
    # Jours restants (formule de colonne calculée de la table)
    # cases à cocher : « envoyée » déduit de la présence d'une date de candidature
    sent = rec["Date Candidature"]["v"] is not None
    for h, val in (("Candidature envoyée", CK_ON if sent else CK_OFF), ("Relance faite", CK_OFF)):
        c = ws.cell(ri, NAME[h], val)
        c.font = Font(name="Segoe UI Symbol", size=13, color=INK)
        c.alignment = Alignment(horizontal="center", vertical="center")
    st = ws.cell(ri, NAME["Statut"])
    st.font = Font(name=FONT, size=10, bold=True, color=INK)
    ws.row_dimensions[ri].height = max(30, min(13.5 * max_lines + 8, 62))
    ws.cell(ri, NAME["Id (Dossier associé)"]).font = Font(name=FONT, size=10, color=MUTED)

last = 1 + len(rows0)
LAST_CF = 300  # formats / validations déjà prêts pour les futures lignes

# Jours restants : formule structurée (colonne calculée)
ST_REF = "Offres[[#This Row],[Statut]]"
DL_REF = "Offres[[#This Row],[Deadline]]"
formula_body = (f'IF(AND(ISNUMBER({DL_REF}),{ST_REF}<>"Rejeté",{ST_REF}<>"Abandon"),{DL_REF}-TODAY(),"")')
jr = NAME["Jours restants"]
for ri in range(2, last + 1):
    c = ws.cell(ri, jr, "=" + formula_body)
    c.number_format = '"dans "0" j";"il y a "0" j";"Aujourd\'hui"'
    c.font = Font(name=FONT, size=10, bold=True, color=INK)

# Région : choisie par menu déroulant. Pré-remplie à la création d'après la colonne Localisation (modifiable) ;
# une localisation sans indication (vide) laisse la région vide.
rg_col = NAME["Région"]
n_prefill = 0
for ri, rec in enumerate(rows0, start=2):
    reg = guess_region(rec["Localisation"]["v"])
    if reg:
        ws.cell(ri, rg_col, reg)
        n_prefill += 1

tab = Table(displayName="Offres", ref=f"A1:{openpyxl.utils.get_column_letter(len(COLS))}{last}")
tab.tableStyleInfo = TableStyleInfo(name=None, showFirstColumn=False, showLastColumn=False,
                                    showRowStripes=False, showColumnStripes=False)
ws.add_table(tab)
tab._initialise_columns()
for col, (_, h, _) in zip(tab.tableColumns, COLS):
    col.name = h
tab.tableColumns[jr - 1].calculatedColumnFormula = TableFormula(attr_text=formula_body)

# Mise en page
ws.freeze_panes = ws.cell(2, NAME["Intitulé"] + 1)  # Id, Statut, Deadline, Jours, Intitulé restent visibles
ws.sheet_view.showGridLines = False
ws.sheet_properties.tabColor = "1D4ED8"
ws.page_setup.orientation = "landscape"
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
ws.print_title_rows = "1:1"

# Validations (listes déroulantes)
dv_st = DataValidation(type="list", formula1="Statuts", allow_blank=True, showErrorMessage=True,
                       errorTitle="Statut inconnu", error="Choisis un statut dans la liste (onglet Listes).")
dv_st.add(f"{L['Statut']}2:{L['Statut']}{LAST_CF}")
dv_ck = DataValidation(type="list", formula1=f'"{CK_OFF},{CK_ON}"', allow_blank=True, showErrorMessage=True,
                       errorTitle="Case à cocher", error=f"Choisis {CK_OFF} ou {CK_ON}.")
dv_ck.add(f"{L['Candidature envoyée']}2:{L['Candidature envoyée']}{LAST_CF}")
dv_ck.add(f"{L['Relance faite']}2:{L['Relance faite']}{LAST_CF}")
dv_reg = DataValidation(type="list", formula1="Regions", allow_blank=True, showErrorMessage=True,
                        errorTitle="Région inconnue", error="Choisis une région dans la liste (« Hors France » pour l'étranger).")
dv_reg.add(f"{L['Région']}2:{L['Région']}{LAST_CF}")
ws.add_data_validation(dv_st)
ws.add_data_validation(dv_ck)
ws.add_data_validation(dv_reg)

# ---------------------------------------------------- mise en forme conditionnelle
def fill(c):
    return PatternFill(fill_type="solid", start_color=c, end_color=c)


B, C, D = L["Statut"], L["Deadline"], L["Jours restants"]
E_, I_, J_, K_ = L["Intitulé"], L["Candidature envoyée"], L["Date Candidature"], L["Relance faite"]
lastcol = openpyxl.utils.get_column_letter(len(COLS))
rg = lambda col: f"{col}2:{col}{LAST_CF}"

# 1) cellule Statut : une couleur par statut
for s, (bg, fg) in PAL.items():
    if s == "À qualifier":
        f = f'AND(${E_}2<>"",ISNA(MATCH(${B}2,Statuts,0)))'
    else:
        f = f'${B}2="{s}"'
    ws.conditional_formatting.add(rg(B), FormulaRule(formula=[f], font=Font(color=fg, bold=True), fill=fill(bg)))

# 2) Deadline + Jours restants : urgence (seulement si l'offre est vivante, cf. colonne Jours restants)
n = f"ISNUMBER(${D}2)"
rules = [
    (f"AND({n},${D}2=0)", Font(color="FFFFFF", bold=True), fill("DC2626"), False),
    (f"AND({n},${D}2>=1,${D}2<=3)", Font(color="C2410C", bold=True), fill("FFEDD5"), False),
    (f"AND({n},${D}2>=4,${D}2<=7)", Font(color="854D0E", bold=True), fill("FEF9C3"), False),
    (f"AND({n},${D}2>7)", Font(color="166534", bold=True), fill("DCFCE7"), False),
    (f'AND({n},${D}2<0,${B}2="À postuler")', Font(color="991B1B", bold=True), fill("FEE2E2"), True),
    (f"AND({n},${D}2<0)", Font(color=MUTED, italic=True), None, False),
]
for f, font, fl, stop in rules:
    ws.conditional_formatting.add(f"{C}2:{D}{LAST_CF}",
                                  FormulaRule(formula=[f], font=font, fill=fl, stopIfTrue=stop) if fl
                                  else FormulaRule(formula=[f], font=font))

# 3) cases à cocher — la relance suggérée passe en premier (et stoppe) pour primer sur le gris de ☐
ws.conditional_formatting.add(
    rg(K_),
    FormulaRule(formula=[f'AND(${B}2="En cours",${K_}2="{CK_OFF}",ISNUMBER(${J_}2),TODAY()-${J_}2>=DelaiRelance)'],
                font=Font(color="92400E", bold=True), fill=fill("FDE68A"), stopIfTrue=True))
for col in (I_, K_):
    ws.conditional_formatting.add(rg(col), FormulaRule(formula=[f'${col}2="{CK_ON}"'], font=Font(color="15803D")))
    ws.conditional_formatting.add(rg(col), FormulaRule(formula=[f'${col}2="{CK_OFF}"'], font=Font(color="9CA3AF")))


# 4) lignes mortes (Rejeté / Abandon) : tout le reste de la ligne s'estompe
ws.conditional_formatting.add(f"A2:{lastcol}{LAST_CF}",
                              FormulaRule(formula=[f'OR(${B}2="Rejeté",${B}2="Abandon")'],
                                          font=Font(color="9CA3AF"), fill=fill("F9FAFB")))

# ----------------------------------------------------------------- Synthèse
S = ws_s
S.sheet_view.showGridLines = False
S.sheet_properties.tabColor = "0F766E"
for col in "ABCDEFG":
    S.column_dimensions[col].width = 19
S["A1"] = "Synthèse de ma recherche"
S["A1"].font = Font(name=FONT, size=18, bold=True, color=INK)
S["A2"] = "Aujourd'hui :"
S["A2"].font = Font(name=FONT, size=10, color=MUTED)
S["B2"] = "=TODAY()"
S["B2"].number_format = "dd/mm/yyyy"
S["B2"].font = Font(name=FONT, size=10, color=MUTED, bold=True)
S["B2"].alignment = Alignment(horizontal="left")

tiles = [
    ("Offres suivies", "=B17", INK, "F3F4F6"),
    ("Candidatures envoyées", f'=COUNTIF(Offres[Candidature envoyée],"{CK_ON}")', INK, "F3F4F6"),
    ("En cours", '=COUNTIF(Offres[Statut],"En cours")', *PAL["En cours"][::-1]),
    ("Entretiens", '=COUNTIF(Offres[Statut],"Entretien")', *PAL["Entretien"][::-1]),
    ("Rejetées", '=COUNTIF(Offres[Statut],"Rejeté")', *PAL["Rejeté"][::-1]),
    ("Deadlines ≤ 7 jours", '=COUNTIFS(Offres[Jours restants],">=0",Offres[Jours restants],"<=7")', "C2410C", "FFEDD5"),
    ("À qualifier", "=B16", *PAL["À qualifier"][::-1]),
]
box = Border(left=Side(style="thin", color="FFFFFF"), right=Side(style="thin", color="FFFFFF"))
for i, (lab, f, fg, bg) in enumerate(tiles):
    col = openpyxl.utils.get_column_letter(i + 1)
    a, b = S[f"{col}4"], S[f"{col}5"]
    a.value, b.value = lab, f
    a.font = Font(name=FONT, size=9, color=fg if bg != "F3F4F6" else MUTED, bold=True)
    b.font = Font(name=FONT, size=24, bold=True, color=fg)
    for x in (a, b):
        x.fill = fill(bg)
        x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        x.border = box
    S.row_dimensions[4].height = 28
    S.row_dimensions[5].height = 44
S.row_dimensions[3].height = 10

# Répartition par statut
S["A8"] = "Répartition par statut"
S["A8"].font = Font(name=FONT, size=12, bold=True, color=INK)
for ci, h in enumerate(["Statut", "Nombre", "Part"], start=1):
    c = S.cell(9, ci, h)
    c.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
    c.fill = fill(INK)
    c.alignment = Alignment(horizontal="center")
order = STATUTS + ["À qualifier"]
# lignes 10..15 : 6 statuts ; ligne 16 : à qualifier ; ligne 17 : total
for i, s in enumerate(order):
    r = 10 + i
    S.cell(r, 1, s)
    if s == "À qualifier":
        S.cell(r, 2, "=B17-SUM(B10:B15)")
    else:
        S.cell(r, 2, f'=COUNTIF(Offres[Statut],"{s}")')
    S.cell(r, 3, f"=IF($B$17=0,0,B{r}/$B$17)")
    bg, fg = PAL[s]
    S.cell(r, 1).fill = fill(bg)
    S.cell(r, 1).font = Font(name=FONT, size=10, bold=True, color=fg)
    for ci in (2, 3):
        S.cell(r, ci).font = Font(name=FONT, size=10, color=INK)
        S.cell(r, ci).alignment = Alignment(horizontal="center")
    S.cell(r, 3).number_format = "0%"
    for ci in (1, 2, 3):
        S.cell(r, ci).border = cell_border
S["A17"], S["B17"] = "Total", "=COUNTA(Offres[Intitulé])"
S["C17"] = "=IF(B17=0,0,SUM(C10:C16))"
S["C17"].number_format = "0%"
for ci in (1, 2, 3):
    c = S.cell(17, ci)
    c.font = Font(name=FONT, size=10, bold=True, color=INK)
    c.border = Border(top=Side(style="thin", color=INK))
    if ci > 1:
        c.alignment = Alignment(horizontal="center")

# Graphique
ch = BarChart()
ch.type = "bar"
ch.style = 10
ch.title = None
ch.legend = None
ch.y_axis.delete = True
ch.x_axis.scaling.orientation = "maxMin"
ch.x_axis.delete = False
ch.y_axis.majorGridlines = None
data = Reference(S, min_col=2, min_row=9, max_row=16)
cats = Reference(S, min_col=1, min_row=10, max_row=16)
ch.add_data(data, titles_from_data=True)
ch.set_categories(cats)
ser = ch.series[0]
ser.graphicalProperties.solidFill = "1D4ED8"
for idx, s in enumerate(order):
    pt = DataPoint(idx=idx)
    pt.graphicalProperties.solidFill = PAL[s][1] if s not in ("Abandon",) else "6B7280"
    ser.dPt.append(pt)
ser.dLbls = DataLabelList()
ser.dLbls.showVal = True
ser.dLbls.showSerName = False
ser.dLbls.showCatName = False
ser.dLbls.showLegendKey = False
ch.gapWidth = 60
ch.height, ch.width = 7.2, 11.5
S.add_chart(ch, "E8")

# Légende
S["A20"] = "Légende"
S["A20"].font = Font(name=FONT, size=12, bold=True, color=INK)
legend = [
    ("Aujourd'hui", "DC2626", "FFFFFF", "Deadline = aujourd'hui (offre non rejetée / non abandonnée)"),
    ("1 à 3 jours", "FFEDD5", "C2410C", "Deadline imminente"),
    ("4 à 7 jours", "FEF9C3", "854D0E", "Deadline proche"),
    ("Plus de 7 jours", "DCFCE7", "166534", "Il reste du temps"),
    ("Manquée", "FEE2E2", "991B1B", "Deadline passée alors que le statut est « À postuler »"),
    ("Relance suggérée", "FDE68A", "92400E", "« En cours » sans relance cochée après le délai réglé dans l'onglet Listes"),
    ("Ligne grisée", "F9FAFB", "9CA3AF", "Offre Rejeté ou Abandon : elle s'efface pour laisser voir le reste"),
]
for i, (lab, bg, fg, txt) in enumerate(legend):
    r = 21 + i
    a = S.cell(r, 1, lab)
    a.fill, a.font = fill(bg), Font(name=FONT, size=10, bold=True, color=fg)
    a.alignment = Alignment(horizontal="center")
    t = S.cell(r, 2, txt)
    t.font = Font(name=FONT, size=10, color=INK)
S["A29"] = "Ajouter une offre"
S["A29"].font = Font(name=FONT, size=12, bold=True, color=INK)
S["A30"] = ("Écris directement sous la dernière ligne du tableau de l'onglet Suivi : listes déroulantes, couleurs, "
            "filtres et calcul des jours restants se prolongent automatiquement.")
S["A30"].font = Font(name=FONT, size=10, color=INK)
S["A31"] = "Cases à cocher : clique la cellule puis la flèche pour choisir ☐ ou ☑."
S["A31"].font = Font(name=FONT, size=10, color=INK)

# ------------------------------------------------- Carte de France (une case par région)
S["A34"] = "Carte des offres par région"
S["A34"].font = Font(name=FONT, size=12, bold=True, color=INK)
S["A35"] = "Afficher :"
S["A35"].font = Font(name=FONT, size=10, color=MUTED)
S["A35"].alignment = Alignment(horizontal="right", vertical="center")
S["B35"] = "Toutes"
S["B35"].font = Font(name=FONT, size=11, bold=True, color="0000FF")
S["B35"].fill = fill("FEF9C3")
S["B35"].alignment = Alignment(horizontal="center", vertical="center")
S["B35"].border = Border(*(Side(style="thin", color="CA8A04"),) * 4)
S["C35"] = "← choisis un statut (En cours, Rejeté…) pour voir où il se trouve sur la carte"
S["C35"].font = Font(name=FONT, size=9, italic=True, color=MUTED)
S["C35"].alignment = Alignment(vertical="center")
S.row_dimensions[35].height = 22
S.row_dimensions[36].height = 8
dv_map = DataValidation(type="list", formula1='"Toutes,' + ",".join(STATUTS) + '"', allow_blank=False,
                        showErrorMessage=True, errorTitle="Choix inconnu",
                        error="Choisis « Toutes » ou un statut de la liste.")
S.add_data_validation(dv_map)
dv_map.add("B35")

M0 = 37  # première ligne de la carte ; chaque case = 1 ligne de nom + 1 ligne de nombre
TILES = [  # (ligne, colonne, région) — disposition schématique, de haut en bas et d'ouest en est
    (0, 1, "Normandie"), (0, 2, "Hauts-de-France"), (0, 3, "Grand Est"),
    (1, 0, "Bretagne"), (1, 1, "Pays de la Loire"), (1, 2, "Île-de-France"), (1, 3, "Bourgogne-Franche-Comté"),
    (2, 1, "Nouvelle-Aquitaine"), (2, 2, "Centre-Val de Loire"), (2, 3, "Auvergne-Rhône-Alpes"),
    (3, 2, "Occitanie"), (3, 3, "Provence-Alpes-Côte d'Azur"), (3, 4, "Corse"),
]
assert {t[2] for t in TILES} == set(PLACES)
white = Side(style="medium", color="FFFFFF")
sel = '$B$35'
for rr in range(4):
    S.row_dimensions[M0 + 2 * rr].height = 30
    S.row_dimensions[M0 + 2 * rr + 1].height = 38
for r, c, name in TILES:
    nm, ct = S.cell(M0 + 2 * r, c + 1), S.cell(M0 + 2 * r + 1, c + 1)
    nm.value = name
    ct.value = (f"=IF({sel}=\"Toutes\",COUNTIF(Offres[Région],{nm.coordinate}),"
                f"COUNTIFS(Offres[Région],{nm.coordinate},Offres[Statut],{sel}))")
    nm.font = Font(name=FONT, size=9, bold=True, color=INK)
    nm.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ct.font = Font(name=FONT, size=22, bold=True, color=INK)
    ct.alignment = Alignment(horizontal="center", vertical="center")
    for x, bd in ((nm, Border(left=white, right=white, top=white)), (ct, Border(left=white, right=white, bottom=white))):
        x.fill = fill("F3F4F6")
        x.border = bd
MAP = f"A{M0}:E{M0 + 7}"
v = f"IF(ISNUMBER(A{M0}),A{M0},IF(ISNUMBER(A{M0 + 1}),A{M0 + 1},-1))"
mx = f"MAX($A${M0}:$E${M0 + 7})"
shades = [  # (condition, fond, texte)
    (f"{v}=0", "F3F4F6", "9CA3AF"),
    (f"AND({v}>0,{v}<=0.25*{mx})", "DBEAFE", "1E3A8A"),
    (f"AND({v}>0.25*{mx},{v}<=0.5*{mx})", "93C5FD", "1E3A8A"),
    (f"AND({v}>0.5*{mx},{v}<=0.75*{mx})", "2563EB", "FFFFFF"),
    (f"{v}>0.75*{mx}", "1E40AF", "FFFFFF"),
]
for cond, bg, fg in shades:
    S.conditional_formatting.add(MAP, FormulaRule(formula=[cond], font=Font(color=fg), fill=fill(bg), stopIfTrue=True))

# Hors carte
OFF = M0 + 9  # ligne des libellés
off_items = [
    ("Hors France", 'Offres[Région],"Hors France"'),
    ("Sans région", 'Offres[Région],""'),
]
for i, (lab, crit) in enumerate(off_items):
    a, b = S.cell(OFF, i + 1, lab), S.cell(OFF + 1, i + 1)
    b.value = f'=IF({sel}="Toutes",COUNTIF({crit}),COUNTIFS({crit},Offres[Statut],{sel}))'
    a.font = Font(name=FONT, size=9, bold=True, color=MUTED)
    b.font = Font(name=FONT, size=16, bold=True, color=INK)
a, b = S.cell(OFF, 3, "Total affiché"), S.cell(OFF + 1, 3)
b.value = f"=SUM(A{M0}:E{M0 + 7})+SUM(A{OFF + 1}:B{OFF + 1})"
a.font = Font(name=FONT, size=9, bold=True, color=MUTED)
b.font = Font(name=FONT, size=16, bold=True, color=INK)
for ci in range(1, 4):
    for x in (S.cell(OFF, ci), S.cell(OFF + 1, ci)):
        x.fill = fill("F3F4F6")
        x.alignment = Alignment(horizontal="center", vertical="center")
        x.border = Border(left=white, right=white)
S.row_dimensions[M0 + 8].height = 8
S.row_dimensions[OFF].height = 20
S.row_dimensions[OFF + 1].height = 30
notes_map = [
    "Carte schématique : une case par région, placée approximativement. Plus la case est foncée, plus il y a d'offres.",
    "La région se choisit dans la colonne Région du tableau Suivi (menu déroulant, pré-remplie d'après ta localisation).",
]
for i, t in enumerate(notes_map):
    cc = S.cell(OFF + 3 + i, 1, t)
    cc.font = Font(name=FONT, size=9, italic=True, color=MUTED)

for sh in (S, ws_l):
    sh.sheet_properties.pageSetUpPr = openpyxl.worksheet.properties.PageSetupProperties(fitToPage=True)
    sh.page_setup.fitToWidth = 1
S.page_setup.orientation = "portrait"
S.page_setup.fitToHeight = 1
ws_l.page_setup.orientation = "landscape"
ws_l.page_setup.fitToHeight = 0

wb.calculation.fullCalcOnLoad = True
wb.active = 0
wb.save(DST)
print("ok", DST, "lignes:", len(rows0))
