import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { emptyForm, errorsFromApi, formFromOffer, toChanges, toCreatePayload, validateForm } from "./form";
import { makeOffer } from "./testData";

describe("formulaire : corps de création", () => {
  it("un formulaire vide n'envoie que le pays (aucune autre valeur par défaut)", () => {
    expect(toCreatePayload(emptyForm())).toEqual({ country: "FR" });
  });

  it("les codes de département restent des chaînes (« 01 », « 2A »)", () => {
    for (const code of ["01", "2A"]) {
      const payload = toCreatePayload({ ...emptyForm(), department: code });
      expect(payload.department).toBe(code);
      expect(typeof payload.department).toBe("string");
    }
  });

  it("à l'étranger, le département n'est jamais envoyé ; pays en majuscules", () => {
    const payload = toCreatePayload({ ...emptyForm(), country: "ch", department: "01" });
    expect(payload).toEqual({ country: "CH" });
  });

  it("textes rognés, vides omis, liens vides retirés, cases cochées seulement si vraies", () => {
    const payload = toCreatePayload({
      ...emptyForm(),
      title: "  Ingénieur  ",
      company: "   ",
      status: "À postuler",
      applied_at: "2026-10-20",
      deadline: "2026-11-30",
      application_sent: true,
      links: [" https://a.example/x ", ""],
    });
    expect(payload).toEqual({
      title: "Ingénieur",
      country: "FR",
      status: "À postuler",
      applied_at: "2026-10-20T00:00:00",
      deadline: "2026-11-30",
      application_sent: true,
      links: ["https://a.example/x"],
    });
  });
});

describe("formulaire : modification (seuls les champs changés)", () => {
  const offer = makeOffer({
    id: 3,
    company: "Thales",
    department: "2A",
    applied_at: "2026-10-20T09:30:00",
    links: ["https://a.example"],
  });

  it("rien de changé : aucun champ envoyé (l'heure de candidature est conservée)", () => {
    const initial = formFromOffer(offer);
    expect(toChanges(initial, initial)).toEqual({});
    expect(initial.applied_at).toBe("2026-10-20");
  });

  it("n'envoie que les champs modifiés ; vider un champ envoie null", () => {
    const initial = formFromOffer(offer);
    const values = { ...initial, company: "", title: "Nouveau", follow_up_done: true };
    expect(toChanges(values, initial)).toEqual({ company: null, title: "Nouveau", follow_up_done: true });
  });

  it("passer à l'étranger efface le département ; revenir en France le retrouve", () => {
    const initial = formFromOffer(offer);
    expect(toChanges({ ...initial, country: "BE" }, initial)).toEqual({ country: "BE", department: null });
    expect(toChanges({ ...initial, country: "be" }, initial).department).toBeNull();
    expect(toChanges({ ...initial, country: "FR" }, initial)).toEqual({});
  });

  it("département hors référentiel (impossible côté service) : vide, jamais renvoyé ni bloquant", () => {
    const initial = formFromOffer({ ...offer, department: "20" });
    expect(initial.department).toBe("");
    expect(validateForm(initial)).toEqual({});
    expect(toChanges({ ...initial, company: "Acme" }, initial)).toEqual({ company: "Acme" });
  });

  it("liens : la liste entière est envoyée quand elle change", () => {
    const initial = formFromOffer(offer);
    expect(toChanges({ ...initial, links: [...initial.links, "https://b.example"] }, initial)).toEqual({
      links: ["https://a.example", "https://b.example"],
    });
  });
});

describe("formulaire : validation et erreurs du service", () => {
  it("messages en français pour le pays, le département et les liens", () => {
    const errors = validateForm({ ...emptyForm(), country: "F1", links: ["https://ok.example", "ftp://x"] });
    expect(errors.country).toMatch(/code ISO de 2 lettres/);
    expect(errors.links).toMatch(/Lien n° 2 invalide/);
    expect(validateForm({ ...emptyForm(), department: "20" }).department).toMatch(/Département inconnu/);
    expect(validateForm(emptyForm())).toEqual({});
  });

  it("rattache les erreurs 422 aux champs, le reste en erreur générale", () => {
    const err = new ApiError(422, "Données invalides", [
      { field: "links", message: "Lien invalide." },
      { field: "department", message: "Département inconnu." },
    ]);
    const found = errorsFromApi(err);
    expect(found.links).toBe("Lien invalide.");
    expect(found.department).toBe("Département inconnu.");
    expect(found.form).toMatch(/champs sont invalides/);
    // Noms pointés (élément d'une liste) : rattachés au champ, messages cumulés.
    const dotted = errorsFromApi(
      new ApiError(422, "x", [
        { field: "links.0", message: "Lien n° 1 invalide." },
        { field: "links.2", message: "Lien n° 3 invalide." },
      ]),
    );
    expect(dotted.links).toBe("Lien n° 1 invalide. Lien n° 3 invalide.");
    expect(dotted.form).toMatch(/champs sont invalides/);
    expect(errorsFromApi(new ApiError(0, "Service injoignable"))).toEqual({ form: "Service injoignable" });
    expect(errorsFromApi(new ApiError(422, "x", [{ field: "corps", message: "JSON invalide." }])).form).toBe(
      "corps : JSON invalide.",
    );
  });
});
