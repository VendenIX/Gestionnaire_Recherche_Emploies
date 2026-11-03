// Largeur du tableau des offres à 1366 px (portable). jsdom ne calcule pas la mise en page : la
// mesure réelle a été faite dans Chrome sans interface (p2-leftovers : à 1366 px, avant
// 1307 px de tableau pour 1283 px disponibles, après 1283 = 1283, largeur minimale 1232 px).
// Ce test fige la cause : les en-têtes longs doivent pouvoir passer sur deux lignes, et aucune
// colonne n'est masquée pour gagner de la place.
// Fichier lu sur le disque : vitest tourne avec `css: false` (un import `?raw` serait vide).
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

// Chemin passé par une variable : Vite réécrit `new URL("<littéral>", import.meta.url)` en URL
// d'actif (non `file:`), ce que readFileSync refuse.
const read = (relative: string) => readFileSync(new URL(relative, import.meta.url), "utf8");
const css = read("./offers.css");

/** Corps de la première règle dont le sélecteur est exactement `selector` (hors @media). */
function baseRule(selector: string): string {
  const topLevel = css.split(/@media[^{]*\{/)[0]!;
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = topLevel.match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`));
  expect(m, `règle ${selector}`).not.toBeNull();
  return m![1]!;
}

it("en-têtes du tableau : retour à la ligne permis hors mobile (tient à 1366 px)", () => {
  expect(baseRule(".offers-table thead th")).toMatch(/white-space:\s*normal/);
  expect(baseRule(".offers-table thead th")).not.toMatch(/nowrap/);
});

it("aucune colonne du tableau n'est masquée", () => {
  expect(css).not.toMatch(/\.offers-table[^{]*(th|td)[^{]*\{[^}]*display:\s*none/);
  expect(css).not.toMatch(/\.offers-table[^{]*(th|td)[^{]*\{[^}]*visibility:\s*hidden/);
});
