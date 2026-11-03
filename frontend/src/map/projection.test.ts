import { describe, expect, it } from "vitest";
import { loadFeatures } from "../geo";
import { DEFAULT_SIZE, makeProjection, zonePaths } from "./projection";

/** Points (x, y) d'un attribut d SVG produit par d3.geoPath (commandes M, L, Z). */
function pathPoints(d: string): [number, number][] {
  const numbers = (d.match(/-?\d+(?:\.\d+)?(?:e-?\d+)?/g) ?? []).map(Number);
  const points: [number, number][] = [];
  for (let i = 0; i + 1 < numbers.length; i += 2) points.push([numbers[i]!, numbers[i + 1]!]);
  return points;
}

const { width, height } = DEFAULT_SIZE;

describe("projection de la carte", () => {
  it("est la conique conforme demandée (parallèles 44/49, rotation -3, centre [0, 46.5])", () => {
    const projection = makeProjection(loadFeatures("regions"), width, height);
    // d3 stocke les angles en radians : comparaison à 1e-9 près.
    const close = (actual: number[], expected: number[]) => {
      expect(actual).toHaveLength(expected.length);
      actual.forEach((v, i) => expect(v).toBeCloseTo(expected[i]!, 9));
    };
    close(projection.parallels(), [44, 49]);
    close(projection.rotate(), [-3, 0, 0]);
    close(projection.center(), [0, 46.5]);
  });

  it("fitSize : la métropole remplit la largeur ou la hauteur du conteneur", () => {
    for (const [w, h] of [
      [width, height],
      [320, 310],
    ] as const) {
      const features = loadFeatures("departements");
      const paths = zonePaths(features, makeProjection(features, w, h));
      const points = [...paths.values()].flatMap(pathPoints);
      const xs = points.map((p) => p[0]);
      const ys = points.map((p) => p[1]);
      const spanX = Math.max(...xs) - Math.min(...xs);
      const spanY = Math.max(...ys) - Math.min(...ys);
      expect(Math.min(...xs)).toBeGreaterThanOrEqual(-0.1);
      expect(Math.min(...ys)).toBeGreaterThanOrEqual(-0.1);
      expect(Math.max(...xs)).toBeLessThanOrEqual(w + 0.1);
      expect(Math.max(...ys)).toBeLessThanOrEqual(h + 0.1);
      expect(Math.max(spanX / w, spanY / h)).toBeGreaterThan(0.99);
    }
  });

  it("dessine la Corse (2A, 2B, région 94) dans la même projection, dans le viewBox, au sud-est", () => {
    const deps = loadFeatures("departements");
    const projection = makeProjection(deps, width, height);
    const paths = zonePaths(deps, projection);
    for (const code of ["2A", "2B"]) {
      const points = pathPoints(paths.get(code) ?? "");
      expect(points.length).toBeGreaterThan(10);
      for (const [x, y] of points) {
        expect(x).toBeGreaterThanOrEqual(0);
        expect(x).toBeLessThanOrEqual(width);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(y).toBeLessThanOrEqual(height);
      }
    }
    // Ajaccio (8.74 E, 41.92 N) tombe dans la boîte projetée de la Corse-du-Sud.
    const ajaccio = projection([8.738, 41.919])!;
    const corseDuSud = pathPoints(paths.get("2A") ?? "");
    expect(ajaccio[0]).toBeGreaterThan(Math.min(...corseDuSud.map((p) => p[0])));
    expect(ajaccio[0]).toBeLessThan(Math.max(...corseDuSud.map((p) => p[0])));
    expect(ajaccio[1]).toBeGreaterThan(Math.min(...corseDuSud.map((p) => p[1])));
    expect(ajaccio[1]).toBeLessThan(Math.max(...corseDuSud.map((p) => p[1])));
    // Pas d'encart : la Corse est au sud-est de Paris, dans le dernier quart à droite.
    const paris = projection([2.35, 48.86])!;
    expect(ajaccio[0]).toBeGreaterThan(paris[0]);
    expect(ajaccio[1]).toBeGreaterThan(paris[1]);
    expect(ajaccio[0]).toBeGreaterThan(width * 0.75);

    const regions = loadFeatures("regions");
    const regionPaths = zonePaths(regions, makeProjection(regions, width, height));
    expect(pathPoints(regionPaths.get("94") ?? "").length).toBeGreaterThan(10);
  });

  it("ne modifie pas le tableau partagé de loadFeatures()", () => {
    const features = loadFeatures("regions");
    const before = features.map((f) => f.properties.code);
    makeProjection(features, width, height);
    expect(loadFeatures("regions")).toBe(features);
    expect(features.map((f) => f.properties.code)).toEqual(before);
  });
});
