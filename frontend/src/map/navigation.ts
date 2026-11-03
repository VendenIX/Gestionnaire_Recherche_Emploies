// Navigation au clavier entre les zones de la carte (« roving tabindex ») : la carte n'est qu'un
// arrêt de tabulation ; les flèches vont à la zone voisine dans la direction choisie (d'après les
// centroïdes projetés), Origine / Fin à la première / dernière zone du référentiel.

export type Point = readonly [number, number];

const DIRECTIONS: Record<string, Point> = {
  ArrowRight: [1, 0],
  ArrowLeft: [-1, 0],
  ArrowDown: [0, 1],
  ArrowUp: [0, -1],
};

/** Touches gérées par nextZone (les autres laissent faire le navigateur). */
export function isNavigationKey(key: string): boolean {
  return key in DIRECTIONS || key === "Home" || key === "End";
}

/**
 * Zone atteinte depuis `from` avec la touche `key`, ou undefined (pas de voisine dans cette
 * direction, ou touche non gérée). Flèche : zone la plus proche dans un cône d'environ ±63° autour
 * de la direction, l'écart latéral comptant double ; ainsi chaque zone reste joignable.
 */
export function nextZone(
  from: string,
  key: string,
  order: readonly string[],
  centroids: ReadonlyMap<string, Point>,
): string | undefined {
  if (key === "Home") return order[0];
  if (key === "End") return order[order.length - 1];
  const dir = DIRECTIONS[key];
  const origin = centroids.get(from);
  if (!dir || !origin) return undefined;
  let best: string | undefined;
  let bestScore = Infinity;
  for (const code of order) {
    if (code === from) continue;
    const c = centroids.get(code);
    if (!c || !Number.isFinite(c[0]) || !Number.isFinite(c[1])) continue;
    const dx = c[0] - origin[0];
    const dy = c[1] - origin[1];
    const along = dx * dir[0] + dy * dir[1];
    const across = Math.abs(dx * dir[1] - dy * dir[0]);
    if (along <= 0 || across > 2 * along) continue;
    const score = along + 2 * across;
    if (score < bestScore) {
      bestScore = score;
      best = code;
    }
  }
  return best;
}
