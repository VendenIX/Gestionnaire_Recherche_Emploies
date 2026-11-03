import { describe, expect, expectTypeOf, it } from "vitest";
import { isStatus, STATUSES, type Department, type Offer, type OfferFields, type Status } from "./types";

describe("types de l'API (D-001)", () => {
  it("département et pays sont des chaînes, jamais des nombres", () => {
    expectTypeOf<Offer["department"]>().toEqualTypeOf<string | null>();
    expectTypeOf<Offer["country"]>().toEqualTypeOf<string>();
    expectTypeOf<Department["code"]>().toEqualTypeOf<string>();
    expectTypeOf<NonNullable<OfferFields["department"]>>().toEqualTypeOf<string>();
    expectTypeOf<NonNullable<OfferFields["country"]>>().toEqualTypeOf<string>();
    expectTypeOf<Offer["department"]>().not.toMatchTypeOf<number>();
  });

  it("le statut est null ou l'un des 6 statuts", () => {
    expectTypeOf<Offer["status"]>().toEqualTypeOf<Status | null>();
    expectTypeOf<Status>().toEqualTypeOf<
      "À postuler" | "En cours" | "Entretien" | "Offre reçue" | "Rejeté" | "Abandon"
    >();
    expect(STATUSES).toHaveLength(6);
    expect(isStatus("Rejeté")).toBe(true);
    expect(isStatus("À qualifier")).toBe(false);
    expect(isStatus(null)).toBe(false);
  });
});
