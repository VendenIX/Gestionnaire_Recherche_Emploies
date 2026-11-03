import { describe, expect, it } from "vitest";
import { EditTracker } from "./edits";
import { makeOffer } from "./testData";

describe("EditTracker : modifications concurrentes d'un champ", () => {
  const base = makeOffer({ id: 1, company: "Thales", status: "À postuler" });
  it("A: fresh reply of other field carries pre-edit value of a pending field (server applies later)", () => {
    const t = new EditTracker(); let ui = base;
    const s = t.start(ui, "status", "Entretien"); ui = s.apply(ui);
    const c = t.start(ui, "company", "Initech"); ui = c.apply(ui);
    ui = t.succeed(1, "company", c.seq, { ...base, company: "Initech" })(ui); // server had not applied status yet
    expect(ui.status).toBe("Entretien");
    ui = t.succeed(1, "status", s.seq, { ...base, company: "Initech", status: "Entretien" })(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Initech" });
  });
  it("B: same field, replies out of order (seq2 then seq1)", () => {
    const t = new EditTracker(); let ui = base;
    const a = t.start(ui, "status", "Entretien"); ui = a.apply(ui);
    const b = t.start(ui, "status", "En cours"); ui = b.apply(ui);
    ui = t.succeed(1, "status", b.seq, { ...base, status: "En cours" })(ui);
    expect(ui.status).toBe("En cours");
    ui = t.succeed(1, "status", a.seq, { ...base, status: "Entretien" })(ui);
    expect(ui.status).toBe("En cours");
  });
  it("C: older edit fails while newer still pending", () => {
    const t = new EditTracker(); let ui = base;
    const a = t.start(ui, "status", "Entretien"); ui = a.apply(ui);
    const b = t.start(ui, "status", "En cours"); ui = b.apply(ui);
    ui = t.fail(1, "status", a.seq)(ui);
    expect(ui.status).toBe("En cours");
    expect(t.isLatest(1, "status", a.seq)).toBe(false);
  });
  it("D: newer edit succeeds, older pending stays hidden", () => {
    const t = new EditTracker(); let ui = base;
    const a = t.start(ui, "status", "Entretien"); ui = a.apply(ui);
    const b = t.start(ui, "status", "En cours"); ui = b.apply(ui);
    ui = t.succeed(1, "status", b.seq, { ...base, status: "En cours" })(ui);
    ui = t.fail(1, "status", a.seq)(ui);
    expect(ui.status).toBe("En cours");
  });
  it("E: both fail -> original; replays are pure (StrictMode)", () => {
    const t = new EditTracker(); let ui = base;
    const a = t.start(ui, "status", "Entretien"); ui = a.apply(a.apply(ui));
    const b = t.start(ui, "status", "En cours"); ui = b.apply(ui);
    const fb = t.fail(1, "status", b.seq); ui = fb(fb(ui));
    expect(ui.status).toBe("Entretien");
    ui = t.fail(1, "status", a.seq)(ui);
    expect(ui.status).toBe("À postuler");
  });
  it("F: reload while in flight (list shows pre-edit), then reply", () => {
    const t = new EditTracker(); let ui = base;
    const a = t.start(ui, "status", "Entretien"); ui = a.apply(ui);
    expect(ui.status).toBe("Entretien");
    ui = { ...base }; // reload result
    ui = t.succeed(1, "status", a.seq, { ...base, status: "Entretien" })(ui);
    expect(ui.status).toBe("Entretien");
  });
});

describe("EditTracker : formulaire de modification (plusieurs champs, un seul PATCH)", () => {
  const base = makeOffer({ id: 1, company: "Thales", status: "À postuler", location: "Lyon" });

  it("G: le formulaire ne touche pas un champ modifié en ligne encore en vol", () => {
    const t = new EditTracker(); let ui = base;
    const s = t.start(ui, "status", "Entretien"); ui = s.apply(ui);
    // Formulaire ouvert après l'édition en ligne : il n'envoie que les champs changés.
    const f = t.startMany(ui, { company: "Initech", location: "Rennes" }); ui = f.apply(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Initech", location: "Rennes" });
    // La réponse du formulaire arrive avant celle du statut (le service n'a pas encore le statut).
    ui = t.succeedMany(1, ["company", "location"], f.seq, { ...base, company: "Initech", location: "Rennes" })(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Initech", location: "Rennes" });
    ui = t.succeed(1, "status", s.seq, { ...base, status: "Entretien" })(ui);
    // Réponse dépassée : seul le statut bouge, les champs du formulaire restent.
    expect(ui).toMatchObject({ status: "Entretien", company: "Initech", location: "Rennes" });
  });

  it("H: le formulaire, plus récent, l'emporte sur une édition en ligne plus ancienne du même champ", () => {
    const t = new EditTracker(); let ui = base;
    const s = t.start(ui, "status", "Entretien"); ui = s.apply(ui);
    const f = t.startMany(ui, { status: "Rejeté", company: "Initech" }); ui = f.apply(ui);
    ui = t.succeedMany(1, ["status", "company"], f.seq, { ...base, status: "Rejeté", company: "Initech" })(ui);
    // L'édition en ligne, encore en vol, est dépassée (son échec n'afficherait pas d'erreur).
    expect(t.isLatest(1, "status", s.seq)).toBe(false);
    ui = t.succeed(1, "status", s.seq, { ...base, status: "Entretien" })(ui);
    expect(ui).toMatchObject({ status: "Rejeté", company: "Initech" });
  });

  it("I: échec du formulaire : chaque champ revient, l'édition en ligne en vol reste affichée", () => {
    const t = new EditTracker(); let ui = base;
    const s = t.start(ui, "status", "Entretien"); ui = s.apply(ui);
    const f = t.startMany(ui, { status: "Rejeté", company: "Initech" }); ui = f.apply(ui);
    const fail = t.failMany(1, ["status", "company"], f.seq);
    ui = fail(fail(ui)); // rejouable (StrictMode)
    expect(ui).toMatchObject({ status: "Entretien", company: "Thales" });
    ui = t.succeed(1, "status", s.seq, { ...base, status: "Entretien" })(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Thales" });
  });

  it("K: un champ confirmé n'est plus suivi : il ne revient pas sur une valeur rechargée depuis", () => {
    const t = new EditTracker(); let ui = base;
    const f = t.startMany(ui, { status: "Rejeté", company: "Initech" }); ui = f.apply(ui);
    ui = t.succeedMany(1, ["status", "company"], f.seq, { ...base, status: "Rejeté", company: "Initech" })(ui);
    // Rechargement : le service a changé le statut et l'entreprise depuis (autre client, CLI).
    ui = { ...ui, status: "Entretien", company: "Safran" };
    const s = t.start(ui, "location", "Paris"); ui = s.apply(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Safran", location: "Paris" });
    ui = t.fail(1, "location", s.seq)(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Safran", location: "Lyon" });
  });

  it("L: comme K, mais le formulaire échoue (failMany) puis une édition en ligne échoue (fail)", () => {
    const t = new EditTracker(); let ui = base;
    const f = t.startMany(ui, { status: "Rejeté", company: "Initech" }); ui = f.apply(ui);
    ui = t.failMany(1, ["status", "company"], f.seq)(ui);
    expect(ui).toMatchObject({ status: "À postuler", company: "Thales" });
    // Rechargement : le service a changé le statut et l'entreprise depuis (autre client, CLI).
    ui = { ...ui, status: "Entretien", company: "Safran" };
    const s = t.start(ui, "location", "Paris"); ui = s.apply(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Safran", location: "Paris" });
    ui = t.fail(1, "location", s.seq)(ui);
    expect(ui).toMatchObject({ status: "Entretien", company: "Safran", location: "Lyon" });
    // Plus rien n'est suivi : une nouvelle édition part de la valeur rechargée.
    const c = t.start(ui, "company", "Globex");
    expect(t.fail(1, "company", c.seq)(c.apply(ui))).toMatchObject({ status: "Entretien", company: "Safran" });
  });

  it("J: une édition en ligne lancée après le formulaire l'emporte sur sa réponse", () => {
    const t = new EditTracker(); let ui = base;
    const f = t.startMany(ui, { status: "Rejeté", company: "Initech" }); ui = f.apply(ui);
    const s = t.start(ui, "status", "Abandon"); ui = s.apply(ui);
    ui = t.succeedMany(1, ["status", "company"], f.seq, { ...base, status: "Rejeté", company: "Initech" })(ui);
    expect(ui).toMatchObject({ status: "Abandon", company: "Initech" });
    ui = t.succeed(1, "status", s.seq, { ...base, status: "Abandon", company: "Initech" })(ui);
    expect(ui).toMatchObject({ status: "Abandon", company: "Initech" });
  });
});
