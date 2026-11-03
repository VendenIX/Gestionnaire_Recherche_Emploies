import { describe, expect, it } from "vitest";
import franceTopo from "./data/france.topo.json";
import {
  type Department,
  DEPARTMENTS,
  FRANCE_TOPOLOGY,
  REGIONS,
  departmentByCode,
  departmentsOfRegion,
  loadFeatures,
  regionCodeForDepartment,
} from "./index";

// Référentiel d'origine (D-001), hors de frontend/ : pas d'import statique, sinon `tsc -b`
// échoue dans l'étape Node du Dockerfile qui ne reçoit que frontend/. Le glob est résolu par
// Vite (vitest) ; dans le dépôt complet il doit trouver exactement un fichier.
// vitest doit donc tourner depuis le dépôt complet (data/ à côté de frontend/) : dans une copie
// de frontend/ seule, le glob ne trouve rien et les tests du référentiel échouent (voulu). Ne pas
// « corriger » le repli `?? []` ci-dessous : il sert seulement à typer et à garder `tsc -b` vert.
const originals = import.meta.glob<Department[]>("../../../data/departements.json", {
  eager: true,
  import: "default",
});
const originalMatches = Object.values(originals);
const original: Department[] = originalMatches[0] ?? [];

const sorted = (codes: Iterable<string>) => [...codes].sort();

describe("référentiel embarqué", () => {
  it("est identique à data/departements.json", () => {
    expect(Object.keys(originals)).toEqual(["../../../data/departements.json"]);
    expect(originalMatches).toHaveLength(1);
    expect(DEPARTMENTS).toEqual(original);
  });

  it("contient exactement 96 départements métropolitains, dont 01, 2A et 2B, sans 20", () => {
    const codes = DEPARTMENTS.map((d) => d.code);
    expect(codes).toHaveLength(96);
    expect(new Set(codes).size).toBe(96);
    expect(codes).toEqual(expect.arrayContaining(["01", "2A", "2B", "95"]));
    expect(codes).not.toContain("20");
    expect(DEPARTMENTS.every((d) => !d.overseas)).toBe(true);
    // ordre officiel : 2A et 2B entre 19 et 21
    expect(codes.slice(codes.indexOf("19"), codes.indexOf("19") + 4)).toEqual(["19", "2A", "2B", "21"]);
  });

  it("dérive la région d'un département", () => {
    expect(REGIONS).toHaveLength(13);
    expect(regionCodeForDepartment("2A")).toBe("94");
    expect(regionCodeForDepartment("01")).toBe("84");
    expect(regionCodeForDepartment("78")).toBe("11");
    expect(regionCodeForDepartment("20")).toBeUndefined();
    expect(departmentByCode("2B")?.name).toBe("Haute-Corse");
    expect(departmentsOfRegion("94")).toEqual(["2A", "2B"]);
  });
});

describe("fonds de carte TopoJSON", () => {
  it("les départements ont exactement les codes du référentiel", () => {
    const features = loadFeatures("departements");
    const codes = features.map((f) => f.properties.code);
    expect(features).toHaveLength(96);
    expect(sorted(codes)).toEqual(sorted(original.map((d) => d.code)));
    expect(codes).toEqual(expect.arrayContaining(["01", "2A", "2B"]));
    expect(codes).not.toContain("20");
  });

  it("les 13 régions ont exactement les codes du référentiel", () => {
    const features = loadFeatures("regions");
    expect(features).toHaveLength(13);
    expect(sorted(features.map((f) => f.properties.code))).toEqual(
      sorted(new Set(original.map((d) => d.region_code))),
    );
  });

  it("les codes sont des chaînes, dans les fichiers comme dans les features", () => {
    expect(Object.keys(franceTopo.objects).sort()).toEqual(["departements", "regions"]);
    for (const object of Object.values(franceTopo.objects)) {
      for (const g of object.geometries) {
        expect(typeof g.properties.code).toBe("string");
        expect(Object.keys(g.properties).sort()).toEqual(["code", "nom"]);
      }
    }
    for (const f of [...loadFeatures("departements"), ...loadFeatures("regions")]) {
      expect(typeof f.properties.code).toBe("string");
      expect(f.properties.nom).not.toBe("");
      expect(["Polygon", "MultiPolygon"]).toContain(f.geometry.type);
    }
  });

  it("les noms des 13 régions correspondent au référentiel (region_name)", () => {
    const names = new Map(original.map((d) => [d.region_code, d.region_name]));
    const features = loadFeatures("regions");
    expect(features.map((f) => [f.properties.code, f.properties.nom])).toEqual(
      REGIONS.map((r) => [r.code, r.name]),
    );
    const mismatches = features.filter((f) => names.get(f.properties.code) !== f.properties.nom);
    expect(mismatches).toEqual([]);
    expect(new Set(features.map((f) => f.properties.nom)).size).toBe(13);
  });

  it("régions et départements partagent les arcs (frontières superposables)", () => {
    // index d'arc absolu (~i désigne l'arc i parcouru à l'envers)
    const arcsOf = (layer: "departements" | "regions") =>
      new Set(
        FRANCE_TOPOLOGY.objects[layer].geometries
          .flatMap((g) => ("arcs" in g ? [g.arcs].flat(3) : []))
          .map((i) => (i < 0 ? ~i : i)),
      );
    const deptArcs = arcsOf("departements");
    const regionArcs = arcsOf("regions");
    expect(regionArcs.size).toBeGreaterThan(0);
    expect([...regionArcs].filter((i) => !deptArcs.has(i))).toEqual([]);
    expect(deptArcs.size).toBe(FRANCE_TOPOLOGY.arcs.length);
  });

  it("les noms des départements correspondent au référentiel", () => {
    const names = new Map(original.map((d) => [d.code, d.name]));
    const mismatches = loadFeatures("departements").filter((f) => names.get(f.properties.code) !== f.properties.nom);
    expect(mismatches.map((f) => `${f.properties.code} ${f.properties.nom}`)).toEqual([]);
  });
});
