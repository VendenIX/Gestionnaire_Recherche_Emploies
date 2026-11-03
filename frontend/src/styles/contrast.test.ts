// Contraste WCAG AA (4.5:1, texte normal) des couleurs de texte sur leurs fonds, calculé depuis
// les variables CSS réelles. Les fonds de statut restent ceux de l'Excel v2 (vérifiés ici) : pour
// le contraste, seule la couleur du texte peut changer.
// Fichiers lus sur le disque : vitest tourne avec `css: false` (un import `?raw` serait vide).
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (relative: string) => readFileSync(new URL(relative, import.meta.url), "utf8");
const baseCss = read("../styles.css");
const offersCss = read("./offers.css");

const AA = 4.5;

function variables(...sources: string[]): Map<string, string> {
  const vars = new Map<string, string>();
  for (const css of sources) {
    for (const m of css.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)) vars.set(m[1]!, m[2]!.toLowerCase());
  }
  return vars;
}

const VARS = variables(baseCss, offersCss);

it("lit bien les feuilles de style", () => {
  expect(VARS.size).toBeGreaterThan(30);
});

function color(name: string): string {
  const value = name.startsWith("#") ? name : VARS.get(name);
  if (!value) throw new Error(`variable CSS absente : --${name}`);
  return value;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const STATUSES = ["a-postuler", "en-cours", "entretien", "offre-recue", "rejete", "abandon", "a-qualifier"];
const DEADLINES = ["today", "3", "7", "later"];

/** Fonds de l'Excel v2 (reference/build_excel_v2.py, PAL) : ne pas les changer pour le contraste. */
const EXCEL_STATUS_BG: Record<string, string> = {
  "a-postuler": "#fef3c7",
  "en-cours": "#dbeafe",
  entretien: "#ede9fe",
  "offre-recue": "#d1fae5",
  rejete: "#fee2e2",
  abandon: "#e5e7eb",
  "a-qualifier": "#ffedd5",
};

describe("contraste AA des couleurs de texte", () => {
  it("calcul de référence : noir sur blanc 21:1, gris 777 sur blanc ≈ 4.48:1", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });

  it("les fonds de statut sont ceux de l'Excel v2", () => {
    for (const s of STATUSES) expect(color(`status-${s}-bg`)).toBe(EXCEL_STATUS_BG[s]);
  });

  const pairs: [string, string][] = [
    ...STATUSES.map((s): [string, string] => [`status-${s}-fg`, `status-${s}-bg`]),
    ...DEADLINES.map((d): [string, string] => [`deadline-${d}-fg`, `deadline-${d}-bg`]),
    ["deadline-overdue-apply-fg", "deadline-overdue-apply-bg"],
    ["follow-up-fg", "follow-up-bg"],
    ["row-closed-fg", "row-closed-bg"],
    ["ink", "background"],
    ["ink", "surface"],
    ["accent", "surface"],
    ["accent", "background"],
    ["accent-ink", "accent"],
    ["danger", "surface"],
    ["danger", "background"],
    ["#ffffff", "head-bg"],
    ["#ffffff", "head-quick-bg"],
    ["#ffffff", "ink"],
    // Texte atténué (.muted) sur tous les fonds clairs où il apparaît.
    ...["background", "surface", "row-closed-bg", "grid", ...STATUSES.map((s) => `status-${s}-bg`)].map(
      (bg): [string, string] => ["muted", bg],
    ),
    // Info-bulle de la carte (map.css) : texte blanc et gris clair sur l'encre.
    ["#d1d5db", "ink"],
  ];

  it.each(pairs)("%s sur %s ≥ 4.5:1", (fg, bg) => {
    expect(contrast(color(fg), color(bg))).toBeGreaterThanOrEqual(AA);
  });
});
