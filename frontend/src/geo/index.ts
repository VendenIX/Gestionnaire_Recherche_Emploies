// Fonds de carte embarqués (aucun appel réseau) et référentiel des départements.
//
// - data/france.topo.json : une topologie, deux objets (« departements », « regions »), générée
//   par scripts/build-geo.mjs depuis gregoiredavid/france-geojson (commit figé, licence et
//   source : README « Fonds de carte »). Les régions sont la fusion des départements par
//   region_code du référentiel : les deux couches partagent leurs arcs, les frontières
//   coïncident exactement à la superposition (utile pour mesh() / contours).
// - data/departements.json : copie de <repo>/data/departements.json faite par le même script.
//   On copie plutôt qu'importer ../../../data parce que l'étape Node du Dockerfile ne reçoit
//   que frontend/ ; un test vérifie que la copie reste identique à l'original (D-001).
//
// Règle D-001 : un code de département ou de région est toujours une chaîne (« 2A », « 01 »),
// jamais un nombre, et l'ordre est celui du référentiel (jamais un tri numérique).
//
// Outre-mer (971 à 976) : hors périmètre ; pour l'ajouter plus tard, générer d'autres couches
// (une topologie par encart) et étendre GeoLayer, sans changer la forme des features.

import type { Feature, FeatureCollection, MultiPolygon, Polygon } from "geojson";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import franceTopo from "./data/france.topo.json";
import referentialJson from "./data/departements.json";

export type GeoLayer = "regions" | "departements";

export interface ZoneProperties {
  /** Code INSEE, toujours une chaîne (« 2A », « 01 », « 84 »). */
  code: string;
  nom: string;
}

export type ZoneFeature = Feature<Polygon | MultiPolygon, ZoneProperties>;

export interface Department {
  code: string;
  name: string;
  region_code: string;
  region_name: string;
  overseas: boolean;
}

export interface Region {
  code: string;
  name: string;
}

export type FranceTopology = Topology<Record<GeoLayer, GeometryCollection<ZoneProperties>>>;

/** Topologie brute (métropole), pour topojson-client mesh() si besoin de frontières seules. */
export const FRANCE_TOPOLOGY: FranceTopology = franceTopo as unknown as FranceTopology;

const cache = new Map<GeoLayer, readonly ZoneFeature[]>();

/** Features GeoJSON d'une couche (métropole) ; départements dans l'ordre du fichier source,
 * régions dans l'ordre du référentiel. Le tableau est mis en cache et partagé entre tous les
 * appelants (même référence à chaque appel, utile aux useMemo) : il est en lecture seule, le
 * copier avant de le trier ou de le modifier. */
export function loadFeatures(layer: GeoLayer): readonly ZoneFeature[] {
  const cached = cache.get(layer);
  if (cached) return cached;
  const object = FRANCE_TOPOLOGY.objects[layer];
  if (!object) throw new Error(`Couche absente du TopoJSON : ${layer}`);
  const collection = feature(FRANCE_TOPOLOGY, object) as FeatureCollection<Polygon | MultiPolygon, ZoneProperties>;
  const features = collection.features.map((f) => ({
    ...f,
    properties: { code: String(f.properties.code), nom: String(f.properties.nom) },
  }));
  cache.set(layer, features);
  return features;
}

/** Référentiel des départements (ordre officiel : 2A et 2B entre 19 et 21). */
export const DEPARTMENTS: readonly Department[] = referentialJson as Department[];

const DEPARTMENT_BY_CODE = new Map(DEPARTMENTS.map((d) => [d.code, d]));

/** Régions dans l'ordre de première apparition dans le référentiel. */
export const REGIONS: readonly Region[] = (() => {
  const seen = new Map<string, Region>();
  for (const d of DEPARTMENTS) {
    if (!seen.has(d.region_code)) seen.set(d.region_code, { code: d.region_code, name: d.region_name });
  }
  return [...seen.values()];
})();

/** Département du référentiel, ou undefined si le code est inconnu (ex. « 20 »). */
export function departmentByCode(code: string): Department | undefined {
  return DEPARTMENT_BY_CODE.get(code);
}

/** Code de la région d'un département, ou undefined si le département est inconnu. */
export function regionCodeForDepartment(code: string): string | undefined {
  return DEPARTMENT_BY_CODE.get(code)?.region_code;
}

/** Codes des départements d'une région, dans l'ordre du référentiel. */
export function departmentsOfRegion(regionCode: string): string[] {
  return DEPARTMENTS.filter((d) => d.region_code === regionCode).map((d) => d.code);
}
