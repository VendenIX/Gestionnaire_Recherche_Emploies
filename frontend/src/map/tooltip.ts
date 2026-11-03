// Placement de l'info-bulle de la carte : au-dessus du centroïde de la zone, centrée, mais
// toujours dans le cadre de la carte (décalée près des bords gauche / droit, passée dessous
// quand la place manque en haut). Tout est en pixels du conteneur (= unités du viewBox).

/** Écart entre le centroïde et l'info-bulle, et marge minimale avec les bords du cadre. */
export const TOOLTIP_GAP = 10;
export const TOOLTIP_MARGIN = 4;

export interface TooltipBox {
  left: number;
  top: number;
  /** Vrai si l'info-bulle est sous le point (pas assez de place au-dessus). */
  below: boolean;
}

export function placeTooltip(
  anchor: readonly [number, number],
  frame: { width: number; height: number },
  tip: { width: number; height: number },
): TooltipBox {
  const [x, y] = anchor;
  const maxLeft = Math.max(TOOLTIP_MARGIN, frame.width - tip.width - TOOLTIP_MARGIN);
  const left = Math.min(Math.max(x - tip.width / 2, TOOLTIP_MARGIN), maxLeft);
  const above = y - TOOLTIP_GAP - tip.height;
  const below = above < TOOLTIP_MARGIN;
  // Dessous non plus, l'info-bulle reste dans le cadre (quitte à recouvrir le point).
  const maxTop = Math.max(TOOLTIP_MARGIN, frame.height - tip.height - TOOLTIP_MARGIN);
  const top = below ? Math.min(y + TOOLTIP_GAP, maxTop) : above;
  return { left, top, below };
}
