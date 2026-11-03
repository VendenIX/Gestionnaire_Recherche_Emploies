// Comptes d'offres par zone de la carte (région ou département), calculés côté client à partir
// de la liste des offres. Règle D-001 : un code de département reste une chaîne (« 2A », « 01 »).
//
// - pays différent de FR -> « Hors France » (jamais rattaché à une zone, même avec un département) ;
// - France sans département -> « Sans département » ;
// - France avec un département absent du référentiel métropolitain (outre-mer, plus tard) ->
//   « Autres » : compté à part pour ne rien perdre, jamais rattaché à une zone au hasard.

import type { Meta, Offer, Status } from "../api/types";
import { DEPARTMENTS, REGIONS, departmentByCode, type GeoLayer } from "../geo";

/** Libellés du backend (geo.HORS_FRANCE, schemas.NO_DEPARTMENT) : valeurs du filtre « région ».
 * Copies de secours seulement : la carte lit les libellés de GET /api/meta (`mapLabelsOf`). */
export const HORS_FRANCE = "Hors France";
export const NO_DEPARTMENT = "Sans département";

/** Libellés (et valeurs du filtre « région ») des compteurs à côté de la carte. */
export interface MapLabels {
  horsFrance: string;
  noDepartment: string;
}

export const DEFAULT_MAP_LABELS: MapLabels = { horsFrance: HORS_FRANCE, noDepartment: NO_DEPARTMENT };

/**
 * Libellés tirés de /api/meta : `out_of_france_label` et `no_department_label`.
 * Backend antérieur sans `out_of_france_label` : l'entrée de `regions` qui n'est pas une région
 * métropolitaine (quel que soit son rang), sinon la constante. Sans meta (pas encore chargée ou
 * en échec) : les constantes ci-dessus.
 */
export function mapLabelsOf(
  meta: Pick<Meta, "regions" | "no_department_label" | "out_of_france_label"> | null | undefined,
): MapLabels {
  if (!meta) return DEFAULT_MAP_LABELS;
  const horsFrance =
    meta.out_of_france_label ||
    meta.regions.find((name) => name && !REGIONS.some((r) => r.name === name)) ||
    HORS_FRANCE;
  return { horsFrance, noDepartment: meta.no_department_label || NO_DEPARTMENT };
}

/** Filtre de statut de la carte, identique au menu de la Synthèse Excel : « Toutes » ou un statut. */
export const ALL_STATUSES = "Toutes";
export type MapStatusFilter = Status | typeof ALL_STATUSES;

export interface ZoneCounts {
  /** Offres par code de zone (code région ou code département). */
  byZone: Map<string, Offer[]>;
  horsFrance: Offer[];
  sansDepartement: Offer[];
  /** Département inconnu du référentiel métropolitain (ex. outre-mer). */
  autres: Offer[];
  /** Plus grand compte d'une zone (0 si aucune). */
  max: number;
}

export function matchesStatus(offer: Offer, status: MapStatusFilter): boolean {
  return status === ALL_STATUSES || offer.status === status;
}

/** Répartit les offres (filtrées par statut) entre les zones de la couche choisie. */
export function countByZone(offers: readonly Offer[], layer: GeoLayer, status: MapStatusFilter = ALL_STATUSES): ZoneCounts {
  const byZone = new Map<string, Offer[]>();
  const horsFrance: Offer[] = [];
  const sansDepartement: Offer[] = [];
  const autres: Offer[] = [];
  for (const offer of offers) {
    if (!matchesStatus(offer, status)) continue;
    const country = (offer.country || "FR").toUpperCase();
    if (country !== "FR") {
      horsFrance.push(offer);
      continue;
    }
    if (offer.department === null || offer.department === "") {
      sansDepartement.push(offer);
      continue;
    }
    const department = departmentByCode(String(offer.department));
    if (!department) {
      autres.push(offer);
      continue;
    }
    const code = layer === "regions" ? department.region_code : department.code;
    const list = byZone.get(code);
    if (list) list.push(offer);
    else byZone.set(code, [offer]);
  }
  let max = 0;
  for (const list of byZone.values()) max = Math.max(max, list.length);
  return { byZone, horsFrance, sansDepartement, autres, max };
}

export interface ZoneInfo {
  code: string;
  name: string;
  /** Pour un département : nom de sa région (filtre du tableau). */
  regionName?: string;
}

const REGION_NAME = new Map(REGIONS.map((r) => [r.code, r.name]));

/** Zones d'une couche dans l'ordre du référentiel (ordre du clavier et du tableau des comptes). */
export function zonesOf(layer: GeoLayer): ZoneInfo[] {
  if (layer === "regions") return REGIONS.map((r) => ({ code: r.code, name: r.name }));
  return DEPARTMENTS.map((d) => ({ code: d.code, name: `${d.name} (${d.code})`, regionName: d.region_name }));
}

export function regionName(code: string): string | undefined {
  return REGION_NAME.get(code);
}

/** « 1 offre », « 3 offres », « aucune offre ». */
export function offersLabel(count: number): string {
  if (count === 0) return "aucune offre";
  return count === 1 ? "1 offre" : `${count} offres`;
}
