import { describe, expect, it } from "vitest";
import { placeTooltip, TOOLTIP_GAP, TOOLTIP_MARGIN } from "./tooltip";

// Carte étroite (mobile 375 px : cadre d'environ 320 px) et info-bulle de 16rem (256 px).
const FRAME = { width: 320, height: 309 };
const TIP = { width: 256, height: 90 };

function inside(box: { left: number; top: number }, frame = FRAME, tip = TIP) {
  expect(box.left).toBeGreaterThanOrEqual(TOOLTIP_MARGIN);
  expect(box.left + tip.width).toBeLessThanOrEqual(frame.width - TOOLTIP_MARGIN);
  expect(box.top).toBeGreaterThanOrEqual(TOOLTIP_MARGIN);
  expect(box.top + tip.height).toBeLessThanOrEqual(frame.height - TOOLTIP_MARGIN);
}

describe("placeTooltip", () => {
  it("au milieu : centrée au-dessus du point", () => {
    const box = placeTooltip([160, 200], FRAME, { width: 100, height: 40 });
    expect(box).toEqual({ left: 110, top: 200 - TOOLTIP_GAP - 40, below: false });
  });

  it("bord gauche (Finistère) : collée à la marge gauche", () => {
    const box = placeTooltip([8, 150], FRAME, TIP);
    expect(box.left).toBe(TOOLTIP_MARGIN);
    inside(box);
  });

  it("bord droit (Corse, Alsace) : collée à la marge droite", () => {
    const box = placeTooltip([312, 250], FRAME, TIP);
    expect(box.left).toBe(FRAME.width - TIP.width - TOOLTIP_MARGIN);
    inside(box);
  });

  it("en haut (Nord) : passée dessous", () => {
    const box = placeTooltip([200, 20], FRAME, TIP);
    expect(box.below).toBe(true);
    expect(box.top).toBe(20 + TOOLTIP_GAP);
    inside(box);
  });

  it("ni au-dessus ni dessous : reste dans le cadre en bas", () => {
    const box = placeTooltip([160, 60], { width: 320, height: 150 }, TIP);
    expect(box.below).toBe(true);
    inside(box, { width: 320, height: 150 });
  });

  it("chaque point du cadre donne une info-bulle dans le cadre", () => {
    for (let x = 0; x <= FRAME.width; x += 16) {
      for (let y = 0; y <= FRAME.height; y += 16) inside(placeTooltip([x, y], FRAME, TIP));
    }
  });
});
