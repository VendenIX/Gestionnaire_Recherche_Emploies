import { describe, expect, it } from "vitest";
import {
  daysLeft,
  daysLeftLabel,
  departmentRanker,
  followUpSuggested,
  formatDate,
  sortOffers,
  urgency,
  urgencyClass,
} from "./logic";
import { makeOffer } from "./testData";

// 2 novembre 2026, 15 h (heure locale).
const NOW = new Date(2026, 10, 2, 15, 0, 0);

describe("jours restants", () => {
  it.each([
    ["2026-11-02", 0],
    ["2026-11-03", 1],
    ["2026-11-30", 28],
    ["2026-10-31", -2],
    ["2027-03-31", 149], // traverse le changement d'heure
  ])("deadline %s -> %i", (deadline, expected) => {
    expect(daysLeft({ deadline, status: "En cours" }, NOW)).toBe(expected);
  });

  it("vide sans deadline, et pour Rejeté / Abandon (comme la formule Excel)", () => {
    expect(daysLeft({ deadline: null, status: "À postuler" }, NOW)).toBeNull();
    expect(daysLeft({ deadline: "2026-11-05", status: "Rejeté" }, NOW)).toBeNull();
    expect(daysLeft({ deadline: "2026-11-05", status: "Abandon" }, NOW)).toBeNull();
    expect(daysLeft({ deadline: "2026-11-05", status: null }, NOW)).toBe(3);
  });

  it("libellé au format de l'Excel v2", () => {
    expect(daysLeftLabel(0)).toBe("Aujourd'hui");
    expect(daysLeftLabel(5)).toBe("dans 5 j");
    expect(daysLeftLabel(-3)).toBe("il y a 3 j");
    expect(daysLeftLabel(null)).toBe("");
  });

  it("formate la date en JJ/MM/AAAA", () => {
    expect(formatDate("2026-11-30")).toBe("30/11/2026");
    expect(formatDate(null)).toBe("");
  });
});

describe("urgence (seuils de l'Excel v2)", () => {
  it.each([
    [0, "En cours", "today"],
    [1, "En cours", "soon"],
    [3, "En cours", "soon"],
    [4, "En cours", "week"],
    [7, "En cours", "week"],
    [8, "En cours", "later"],
    [-1, "À postuler", "overdue-apply"],
    [-1, "En cours", "overdue"],
    [-1, null, "overdue"],
  ] as const)("%i jours, statut %s -> %s", (days, status, expected) => {
    expect(urgency(days, status)).toBe(expected);
  });

  it("aucune urgence sans jours restants", () => {
    expect(urgency(null, "À postuler")).toBeNull();
    expect(urgencyClass(null)).toBe("");
    expect(urgencyClass("today")).toBe("deadline-today");
  });
});

describe("relance suggérée", () => {
  const base = { status: "En cours" as const, follow_up_done: false, applied_at: "2026-10-18T15:00:00" };

  it("En cours, non relancée, candidature de plus de N jours", () => {
    expect(followUpSuggested(base, 14, NOW)).toBe(true); // 15 jours
    expect(followUpSuggested(base, 15, NOW)).toBe(false); // exactement 15 jours : pas « plus de »
    expect(followUpSuggested(base, 20, NOW)).toBe(false);
  });

  it("jamais si relance faite, autre statut ou sans date de candidature", () => {
    expect(followUpSuggested({ ...base, follow_up_done: true }, 14, NOW)).toBe(false);
    expect(followUpSuggested({ ...base, status: "Entretien" }, 14, NOW)).toBe(false);
    expect(followUpSuggested({ ...base, status: null }, 14, NOW)).toBe(false);
    expect(followUpSuggested({ ...base, applied_at: null }, 14, NOW)).toBe(false);
  });
});

describe("tri", () => {
  const order = ["01", "18", "19", "2A", "2B", "21", "75"];

  it("département : ordre du référentiel, jamais numérique (19 < 2A < 2B < 21)", () => {
    const offers = ["21", "2B", null, "19", "2A", "01"].map((department, i) => makeOffer({ id: i + 1, department }));
    const asc = sortOffers(offers, { key: "department", dir: "asc" }, { departmentOrder: order });
    expect(asc.map((o) => o.department)).toEqual(["01", "19", "2A", "2B", "21", null]);
    const desc = sortOffers(offers, { key: "department", dir: "desc" }, { departmentOrder: order });
    expect(desc.map((o) => o.department)).toEqual(["21", "2B", "2A", "19", "01", null]);
    const rank = departmentRanker(order);
    expect(rank("2A")).toBeLessThan(rank("21") ?? 0);
  });

  it("statut dans l'ordre de la liste, « À qualifier » en dernier", () => {
    const offers = [
      makeOffer({ id: 1, status: "Rejeté" }),
      makeOffer({ id: 2, status: null }),
      makeOffer({ id: 3, status: "À postuler" }),
      makeOffer({ id: 4, status: "Entretien" }),
    ];
    const ids = sortOffers(offers, { key: "status", dir: "asc" }, { departmentOrder: [] }).map((o) => o.id);
    expect(ids).toEqual([3, 4, 1, 2]);
  });

  it("deadline et jours restants : vides en fin de liste dans les deux sens", () => {
    const offers = [
      makeOffer({ id: 1, deadline: "2026-12-01" }),
      makeOffer({ id: 2, deadline: null }),
      makeOffer({ id: 3, deadline: "2026-11-05" }),
      makeOffer({ id: 4, deadline: "2026-11-04", status: "Rejeté" }),
    ];
    const ctx = { departmentOrder: [], now: NOW };
    expect(sortOffers(offers, { key: "deadline", dir: "asc" }, ctx).map((o) => o.id)).toEqual([4, 3, 1, 2]);
    expect(sortOffers(offers, { key: "days", dir: "asc" }, ctx).map((o) => o.id)).toEqual([3, 1, 2, 4]);
    expect(sortOffers(offers, { key: "days", dir: "desc" }, ctx).map((o) => o.id)).toEqual([1, 3, 2, 4]);
  });

  it("texte : ordre alphabétique français sans tenir compte des accents ni de la casse", () => {
    const offers = [
      makeOffer({ id: 1, company: "Zodiac" }),
      makeOffer({ id: 2, company: "édf" }),
      makeOffer({ id: 3, company: "Acme" }),
      makeOffer({ id: 4, company: null }),
    ];
    const ids = sortOffers(offers, { key: "company", dir: "asc" }, { departmentOrder: [] }).map((o) => o.id);
    expect(ids).toEqual([3, 2, 1, 4]);
  });
});
