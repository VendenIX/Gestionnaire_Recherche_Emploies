// Projection de la carte : conique conforme de Lambert (parallèles 44 et 49, rotation -3,
// centre [0, 46.5]), ajustée au conteneur par fitSize. La Corse est dans la même projection
// (aucun encart). d3-geo ne sert qu'à calculer les tracés : React dessine le SVG.
//
// Outre-mer (971 à 976) : plus tard, un encart = une autre projection + un autre <g>, ajouté à
// côté de celle-ci ; la projection métropolitaine ne change pas.

import type { FeatureCollection, MultiPolygon, Polygon } from "geojson";
import { geoConicConformal, geoPath, type GeoConicProjection, type GeoProjection } from "d3-geo";
import type { ZoneFeature } from "../geo";

export const PROJECTION_PARALLELS: [number, number] = [44, 49];
export const PROJECTION_ROTATE: [number, number] = [-3, 0];
export const PROJECTION_CENTER: [number, number] = [0, 46.5];

/** Taille par défaut avant la première mesure (et sous jsdom, sans ResizeObserver). */
export const DEFAULT_SIZE = { width: 600, height: 580 } as const;
/** Hauteur / largeur de la carte (la métropole est à peu près carrée). */
export const MAP_ASPECT = DEFAULT_SIZE.height / DEFAULT_SIZE.width;

export function makeProjection(features: readonly ZoneFeature[], width: number, height: number): GeoConicProjection {
  // Nouvel objet collection : le tableau partagé de loadFeatures() n'est jamais modifié.
  const collection: FeatureCollection<Polygon | MultiPolygon> = { type: "FeatureCollection", features: [...features] };
  return geoConicConformal()
    .parallels(PROJECTION_PARALLELS)
    .rotate(PROJECTION_ROTATE)
    .center(PROJECTION_CENTER)
    .fitSize([width, height], collection);
}

/** Tracés SVG (attribut d) par code de zone. */
export function zonePaths(features: readonly ZoneFeature[], projection: GeoProjection): Map<string, string> {
  const path = geoPath(projection).digits(1);
  const out = new Map<string, string>();
  for (const f of features) out.set(f.properties.code, path(f) ?? "");
  return out;
}

/** Point d'ancrage de l'info-bulle : centroïde projeté de la zone. */
export function zoneCentroids(features: readonly ZoneFeature[], projection: GeoProjection): Map<string, [number, number]> {
  const path = geoPath(projection);
  const out = new Map<string, [number, number]>();
  for (const f of features) out.set(f.properties.code, path.centroid(f));
  return out;
}
