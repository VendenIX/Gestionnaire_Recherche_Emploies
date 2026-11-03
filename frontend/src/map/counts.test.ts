import { describe, expect, it } from "vitest";
import { makeOffer } from "../offers/testData";
import { REGIONS } from "../geo";
import { ALL_STATUSES, DEFAULT_MAP_LABELS, HORS_FRANCE, NO_DEPARTMENT, countByZone, mapLabelsOf, offersLabel, zonesOf } from "./counts";

const OFFERS = [
  makeOffer({ id: 1, department: "2A", status: "En cours" }),
  makeOffer({ id: 2, department: "2B", status: "À postuler" }),
  makeOffer({ id: 3, department: "01", status: "En cours" }),
  makeOffer({ id: 4, department: "69", status: "Rejeté" }),
  makeOffer({ id: 5, department: "75", status: null }),
  makeOffer({ id: 6, department: null, country: "CH", status: "En cours" }),
  // Pays étranger : jamais rattaché à une zone, même avec un département renseigné.
  makeOffer({ id: 7, department: "74", country: "CH" }),
  makeOffer({ id: 8, department: null, country: "FR" }),
  makeOffer({ id: 9, department: "971", country: "FR" }),
];

const ids = (list: { id: number }[] | undefined) => (list ?? []).map((o) => o.id);

describe("countByZone", () => {
  it("compte par département en gardant 2A et 2B distincts (codes texte)", () => {
    const counts = countByZone(OFFERS, "departements");
    expect(ids(counts.byZone.get("2A"))).toEqual([1]);
    expect(ids(counts.byZone.get("2B"))).toEqual([2]);
    expect(ids(counts.byZone.get("01"))).toEqual([3]);
    expect(counts.byZone.has("1")).toBe(false);
    expect(counts.byZone.has("20")).toBe(false);
    expect(counts.max).toBe(1);
  });

  it("compte par région : 2A + 2B -> Corse (94), 01 + 69 -> Auvergne-Rhône-Alpes (84)", () => {
    const counts = countByZone(OFFERS, "regions");
    expect(ids(counts.byZone.get("94"))).toEqual([1, 2]);
    expect(ids(counts.byZone.get("84"))).toEqual([3, 4]);
    expect(ids(counts.byZone.get("11"))).toEqual([5]);
    expect(counts.max).toBe(2);
  });

  it("met l'étranger dans Hors France et la France sans département dans Sans département", () => {
    const counts = countByZone(OFFERS, "regions");
    expect(ids(counts.horsFrance)).toEqual([6, 7]);
    expect(ids(counts.sansDepartement)).toEqual([8]);
    // Département hors référentiel métropolitain (outre-mer, plus tard) : compté à part.
    expect(ids(counts.autres)).toEqual([9]);
    const total =
      [...counts.byZone.values()].reduce((n, l) => n + l.length, 0) +
      counts.horsFrance.length +
      counts.sansDepartement.length +
      counts.autres.length;
    expect(total).toBe(OFFERS.length);
  });

  it("filtre par statut comme le menu de la Synthèse Excel", () => {
    const counts = countByZone(OFFERS, "regions", "En cours");
    expect(ids(counts.byZone.get("94"))).toEqual([1]);
    expect(ids(counts.byZone.get("84"))).toEqual([3]);
    expect(counts.byZone.has("11")).toBe(false);
    expect(ids(counts.horsFrance)).toEqual([6]);
    expect(counts.sansDepartement).toEqual([]);
    expect(countByZone(OFFERS, "regions", ALL_STATUSES).byZone.get("11")).toHaveLength(1);
  });
});

describe("zonesOf", () => {
  it("donne les 13 régions et les 96 départements dans l'ordre du référentiel", () => {
    expect(zonesOf("regions").map((z) => z.code)).toEqual(REGIONS.map((r) => r.code));
    const deps = zonesOf("departements").map((z) => z.code);
    expect(deps).toHaveLength(96);
    expect(deps.slice(deps.indexOf("19"), deps.indexOf("19") + 4)).toEqual(["19", "2A", "2B", "21"]);
  });
});

it("offersLabel", () => {
  expect(offersLabel(0)).toBe("aucune offre");
  expect(offersLabel(1)).toBe("1 offre");
  expect(offersLabel(4)).toBe("4 offres");
});

it("mapLabelsOf : libellés de /api/meta, constantes seulement sans meta", () => {
  expect(mapLabelsOf(null)).toEqual({ horsFrance: HORS_FRANCE, noDepartment: NO_DEPARTMENT });
  expect(mapLabelsOf(undefined)).toBe(DEFAULT_MAP_LABELS);
  const names = REGIONS.map((r) => r.name);
  // Champ dédié : il l'emporte, quel que soit l'ordre de regions.
  expect(
    mapLabelsOf({
      regions: ["Étranger", ...[...names].reverse()],
      out_of_france_label: "Étranger",
      no_department_label: "Lieu inconnu",
    }),
  ).toEqual({ horsFrance: "Étranger", noDepartment: "Lieu inconnu" });
  // Champ dédié même si regions ne le contient pas ou le met au milieu.
  expect(
    mapLabelsOf({ regions: [...names.slice(0, 1), "Hors France", ...names.slice(1)], out_of_france_label: "Ailleurs", no_department_label: "" }),
  ).toEqual({ horsFrance: "Ailleurs", noDepartment: NO_DEPARTMENT });
  // Backend antérieur (pas de champ dédié) : l'entrée non métropolitaine, à n'importe quel rang.
  expect(mapLabelsOf({ regions: [...names, "Étranger"], no_department_label: "Lieu inconnu" })).toEqual({
    horsFrance: "Étranger",
    noDepartment: "Lieu inconnu",
  });
  expect(mapLabelsOf({ regions: ["Étranger", ...names], no_department_label: "Lieu inconnu" }).horsFrance).toBe("Étranger");
  expect(mapLabelsOf({ regions: [...names.slice(0, 3), "Étranger", ...names.slice(3)], no_department_label: "" }).horsFrance).toBe(
    "Étranger",
  );
  // Aucune entrée non métropolitaine : constante de secours.
  expect(mapLabelsOf({ regions: names, no_department_label: "" })).toEqual(DEFAULT_MAP_LABELS);
  expect(mapLabelsOf({ regions: [], no_department_label: "Sans dép." })).toEqual({
    horsFrance: HORS_FRANCE,
    noDepartment: "Sans dép.",
  });
});
