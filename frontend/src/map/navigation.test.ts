import { describe, expect, it } from "vitest";
import { loadFeatures, type GeoLayer } from "../geo";
import { isNavigationKey, nextZone, type Point } from "./navigation";
import { DEFAULT_SIZE, makeProjection, zoneCentroids } from "./projection";

const ARROWS = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"];

function layout(layer: GeoLayer, width: number, height: number) {
  const features = loadFeatures(layer);
  const centroids = zoneCentroids(features, makeProjection(features, width, height));
  return { order: features.map((f) => f.properties.code), centroids };
}

/** Zones atteintes depuis `start` par les seules flèches. */
function reachable(start: string, order: readonly string[], centroids: ReadonlyMap<string, Point>) {
  const seen = new Set([start]);
  const queue = [start];
  while (queue.length > 0) {
    const code = queue.shift()!;
    for (const key of ARROWS) {
      const next = nextZone(code, key, order, centroids);
      if (next && !seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return seen;
}

describe("nextZone", () => {
  const centroids = new Map<string, Point>([
    ["a", [0, 0]],
    ["b", [10, 1]],
    ["c", [10, 30]],
    ["d", [0, 10]],
  ]);
  const order = ["a", "b", "c", "d"];

  it("flèche : la plus proche dans la direction, l'écart latéral comptant double", () => {
    expect(nextZone("a", "ArrowRight", order, centroids)).toBe("b");
    expect(nextZone("a", "ArrowDown", order, centroids)).toBe("d");
    expect(nextZone("b", "ArrowLeft", order, centroids)).toBe("a");
    expect(nextZone("d", "ArrowUp", order, centroids)).toBe("a");
    expect(nextZone("a", "ArrowLeft", order, centroids)).toBeUndefined();
  });

  it("pondération figée : écart latéral x2, cône |latéral| <= 2 x avancée", () => {
    const pick = (points: Record<string, Point>, key = "ArrowRight") =>
      nextZone("o", key, ["o", ...Object.keys(points)], new Map<string, Point>([["o", [0, 0]], ...Object.entries(points)]));
    // Score = avancée + 2 x latéral. p : 10 + 0 = 10. q (6, 2.1) : 6 + 4.2 = 10.2 -> p ;
    // un poids latéral < 1.9 donnerait q.
    expect(pick({ p: [10, 0], q: [6, 2.1] })).toBe("p");
    // q (6, 1.9) : 6 + 3.8 = 9.8 -> q ; un poids latéral > 2.1 donnerait p.
    expect(pick({ p: [10, 0], q: [6, 1.9] })).toBe("q");
    // Même chose dans les autres directions (latéral signé des deux côtés).
    expect(pick({ p: [0, -10], q: [-2.1, -6] }, "ArrowUp")).toBe("p");
    expect(pick({ p: [0, -10], q: [-1.9, -6] }, "ArrowUp")).toBe("q");
    expect(pick({ p: [-10, 0], q: [-6, 2.1] }, "ArrowLeft")).toBe("p");
    expect(pick({ p: [0, 10], q: [1.9, 6] }, "ArrowDown")).toBe("q");
    // Cône : latéral <= 2 x avancée (environ ±63°), bord inclus ; au-delà, aucune zone.
    expect(pick({ r: [5, 10] })).toBe("r");
    expect(pick({ r: [5, -9.9] })).toBe("r");
    expect(pick({ r: [5, 10.1] })).toBeUndefined();
    expect(pick({ r: [0, 5] })).toBeUndefined();
    expect(pick({ r: [-1, 0] })).toBeUndefined();
  });

  it("Origine / Fin : première et dernière zone ; autre touche : rien", () => {
    expect(nextZone("c", "Home", order, centroids)).toBe("a");
    expect(nextZone("a", "End", order, centroids)).toBe("d");
    expect(nextZone("a", "Tab", order, centroids)).toBeUndefined();
    expect(isNavigationKey("ArrowUp")).toBe(true);
    expect(isNavigationKey("Enter")).toBe(false);
  });

  for (const layer of ["regions", "departements"] as const) {
    for (const [width, height] of [
      [DEFAULT_SIZE.width, DEFAULT_SIZE.height],
      [320, 309],
    ] as const) {
      it(`${layer} ${width} px : toute zone est joignable aux flèches depuis toute autre`, () => {
        const { order, centroids } = layout(layer, width, height);
        expect(order).toHaveLength(layer === "regions" ? 13 : 96);
        for (const start of order) expect(reachable(start, order, centroids).size).toBe(order.length);
      });
    }
  }

  it("départements : la Corse se rejoint depuis les Alpes-Maritimes et inversement", () => {
    const { order, centroids } = layout("departements", DEFAULT_SIZE.width, DEFAULT_SIZE.height);
    const fromNice = ARROWS.map((k) => nextZone("06", k, order, centroids));
    expect(fromNice.some((c) => c === "2A" || c === "2B")).toBe(true);
    const fromCorse = ARROWS.map((k) => nextZone("2B", k, order, centroids));
    expect(fromCorse.some((c) => c === "06" || c === "83" || c === "2A")).toBe(true);
  });
});
