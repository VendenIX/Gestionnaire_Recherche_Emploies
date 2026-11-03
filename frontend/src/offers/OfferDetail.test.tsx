// Formulaire « Nouvelle offre », panneau de détail, modification et suppression, contre un
// fetch simulé par route (POST, PATCH, DELETE /api/offers).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/token";
import type { Offer } from "../api/types";
import { jsonResponse, META } from "../test/fetchMock";
import { NEXTCLOUD_NOTE } from "./OfferDetail";
import { OfferFiltersProvider } from "./filters";
import { OffersView } from "./OffersView";
import { makeOffer } from "./testData";

const OFFERS: Offer[] = [
  makeOffer({
    id: 1,
    title: "Ingénieur",
    company: "Thales",
    department: "01",
    region: "Auvergne-Rhône-Alpes",
    status: "À postuler",
    links: ["https://jobs.example/1"],
    created_at: "2026-10-01T10:15:00",
    updated_at: "2026-10-03T08:00:00",
  }),
  makeOffer({ id: 2, title: "Chef de projet", department: "2A", region: "Corse", status: "En cours" }),
];

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

type Handler = (call: Call) => Response | Promise<Response> | undefined;

/** fetch simulé : /api/meta, GET /api/offers ; `handler` répond en premier s'il renvoie une réponse. */
function routeFetch(handler: Handler = () => undefined) {
  const calls: Call[] = [];
  let offers = [...OFFERS];
  const fn = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    const call = { method, path: url.pathname, body };
    calls.push(call);
    const custom = await handler(call);
    if (custom) return custom;
    if (url.pathname === "/api/meta") return jsonResponse(200, META);
    if (url.pathname === "/api/offers" && method === "GET") return jsonResponse(200, offers);
    if (url.pathname === "/api/offers" && method === "POST") {
      const created = makeOffer({ id: 6, title: null, status: null, ...(body as Partial<Offer>) });
      offers = [...offers, created];
      return jsonResponse(201, created);
    }
    const m = /^\/api\/offers\/(\d+)$/.exec(url.pathname);
    if (m) {
      const id = Number(m[1]);
      const offer = offers.find((o) => o.id === id);
      if (method === "PATCH") return jsonResponse(200, { ...offer, ...body });
      if (method === "DELETE") {
        offers = offers.filter((o) => o.id !== id);
        return new Response(null, { status: 204 });
      }
    }
    return jsonResponse(404, { detail: "inconnu" });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function deferred() {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => (resolve = r));
  return { promise, resolve };
}

function renderView() {
  return render(
    <OfferFiltersProvider>
      <OffersView />
    </OfferFiltersProvider>,
  );
}

async function loaded() {
  await screen.findByText("2 offres");
}

const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

async function openDetail(user: ReturnType<typeof userEvent.setup>, id: number) {
  await user.click(screen.getByRole("button", { name: `Détail de l'offre n° ${id}` }));
  return screen.getByRole("dialog", { name: new RegExp(`N° ${id}`) });
}

beforeEach(() => {
  setToken("jeton-test");
});

describe("Nouvelle offre", () => {
  it("crée une offre : seuls les champs saisis et le pays sont envoyés, « 2A » reste une chaîne", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();

    const opener = screen.getByRole("button", { name: "Nouvelle offre" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const title = within(dialog).getByLabelText("Intitulé");
    expect(title).toHaveFocus();
    // Valeurs par défaut : vides, sauf le pays.
    expect(within(dialog).getByLabelText("Pays (code ISO)")).toHaveValue("FR");
    expect(within(dialog).getByLabelText("Statut")).toHaveValue("");
    expect(within(dialog).getByLabelText("Candidature envoyée")).not.toBeChecked();
    // Département : ordre du référentiel, libellé « code - nom ».
    const deps = within(within(dialog).getByLabelText("Département"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(deps.slice(0, 2)).toEqual(["Non renseigné", "01 - Ain"]);
    expect(deps.indexOf("2A - Corse-du-Sud")).toBe(deps.indexOf("19 - Corrèze") + 1);
    expect(deps.indexOf("21 - Côte-d'Or")).toBe(deps.indexOf("2B - Haute-Corse") + 1);

    await user.type(title, "Ingénieur systèmes");
    await user.type(within(dialog).getByLabelText("Entreprise"), "Thales");
    await user.selectOptions(within(dialog).getByLabelText("Département"), "2A");
    await user.selectOptions(within(dialog).getByLabelText("Statut"), "À postuler");
    await user.click(within(dialog).getByLabelText("Candidature envoyée"));
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un lien" }));
    await user.type(within(dialog).getByLabelText("Lien n° 1"), "https://jobs.example/new");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Offre n° 6 créée.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([
      {
        method: "POST",
        path: "/api/offers",
        body: {
          title: "Ingénieur systèmes",
          company: "Thales",
          country: "FR",
          department: "2A",
          status: "À postuler",
          application_sent: true,
          links: ["https://jobs.example/new"],
        },
      },
    ]);
    expect(opener).toHaveFocus();
    // La liste est rechargée et montre la nouvelle offre.
    expect(await screen.findByText("3 offres")).toBeInTheDocument();
  });

  it("département désactivé à l'étranger et jamais envoyé", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const department = within(dialog).getByLabelText("Département");
    await user.selectOptions(department, "01");
    const country = within(dialog).getByLabelText("Pays (code ISO)");
    await user.clear(country);
    await user.type(country, "ch");
    expect(country).toHaveValue("CH");
    expect(department).toBeDisabled();
    // Le choix « 01 » n'est plus affiché à l'étranger (gardé pour un retour en France).
    expect(department).toHaveValue("");
    expect(within(dialog).getByText("Pas de département hors de France.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Lieu (ville)"), "Genève");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));
    await screen.findByRole("status");
    expect(writes(calls)[0]?.body).toEqual({ location: "Genève", country: "CH" });
  });

  it("validation locale en français (pas d'appel) puis erreurs 422 du service à côté du champ", async () => {
    const user = userEvent.setup();
    const calls = routeFetch((c) =>
      c.method === "POST"
        ? jsonResponse(422, {
            detail: "Données invalides : links : Lien refusé par le service.",
            errors: [{ field: "links", message: "Lien refusé par le service." }],
          })
        : undefined,
    );
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const country = within(dialog).getByLabelText("Pays (code ISO)");
    await user.clear(country);
    await user.type(country, "1");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));
    expect(within(dialog).getByText(/code ISO de 2 lettres/)).toBeInTheDocument();
    expect(country).toHaveAttribute("aria-invalid", "true");
    expect(country).toHaveAccessibleDescription(/code ISO de 2 lettres/);
    await waitFor(() => expect(country).toHaveFocus());
    expect(writes(calls)).toEqual([]);

    await user.clear(country);
    await user.click(within(dialog).getByRole("button", { name: "Ajouter un lien" }));
    await user.type(within(dialog).getByLabelText("Lien n° 1"), "https://x.example");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));
    expect(await within(dialog).findByText("Lien refusé par le service.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Lien n° 1")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("dialog", { name: "Nouvelle offre" })).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(1);
  });

  it("piège le focus (Tab, Maj+Tab) ; Échap ferme et rend le focus", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    const opener = screen.getByRole("button", { name: "Nouvelle offre" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const close = within(dialog).getByRole("button", { name: "Fermer" });
    const submit = within(dialog).getByRole("button", { name: "Créer l'offre" });
    submit.focus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(submit).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe("panneau de détail", () => {
  it("montre tous les champs, la région, des liens sûrs, les dates et « Ouvrir le dossier » désactivé", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    expect(panel).toHaveAttribute("aria-modal", "true");
    expect(within(panel).getByText("01 - Ain")).toBeInTheDocument();
    expect(within(panel).getByText("Auvergne-Rhône-Alpes")).toBeInTheDocument();
    expect(within(panel).getByText("Thales")).toBeInTheDocument();
    const link = within(panel).getByRole("link", { name: "https://jobs.example/1" });
    expect(link).toHaveAttribute("href", "https://jobs.example/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(within(panel).getByText("01/10/2026 10:15")).toBeInTheDocument();
    expect(within(panel).getByText("03/10/2026 08:00")).toBeInTheDocument();
    const folder = within(panel).getByRole("button", { name: "Ouvrir le dossier" });
    expect(folder).toBeDisabled();
    expect(folder).toHaveAttribute("title", NEXTCLOUD_NOTE);
    expect(folder).toHaveAttribute("aria-description", NEXTCLOUD_NOTE);
    expect(NEXTCLOUD_NOTE).toBe("Lien Nextcloud non activé (NEXTCLOUD_WEBDAV)");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Détail de l'offre n° 1" })).toHaveFocus();
  });

  it("« 2A » s'affiche tel quel", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 2);
    expect(within(panel).getByText("2A - Corse-du-Sud")).toBeInTheDocument();
  });

  it("statut modifié dans le panneau : PATCH du seul champ, tableau à jour", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toEqual({ method: "PATCH", path: "/api/offers/1", body: { status: "Entretien" } });
    await user.keyboard("{Escape}");
    const row = screen.getByRole("button", { name: "Détail de l'offre n° 1" }).closest("tr") as HTMLElement;
    expect(within(row).getByLabelText("Statut de l'offre n° 1")).toHaveValue("Entretien");
  });

  it("modifier : formulaire prérempli, PATCH des seuls champs changés, focus rendu", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    const edit = within(panel).getByRole("button", { name: "Modifier" });
    await user.click(edit);
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    expect(within(form).getByLabelText("Intitulé")).toHaveValue("Ingénieur");
    expect(within(form).getByLabelText("Département")).toHaveValue("01");
    expect(within(form).getByLabelText("Lien n° 1")).toHaveValue("https://jobs.example/1");
    const company = within(form).getByLabelText("Entreprise");
    await user.clear(company);
    await user.type(company, "Acme");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument());
    expect(writes(calls)).toEqual([{ method: "PATCH", path: "/api/offers/1", body: { company: "Acme" } }]);
    expect(within(panel).getByText("Acme")).toBeInTheDocument();
    expect(edit).toHaveFocus();
  });

  it("le formulaire n'écrase pas un statut modifié en ligne encore en vol", async () => {
    const user = userEvent.setup();
    const statusReply = deferred();
    const calls = routeFetch((c) =>
      c.method === "PATCH" && c.body && "status" in c.body ? statusReply.promise : undefined,
    );
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    await user.click(within(panel).getByRole("button", { name: "Modifier" }));
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    // Le formulaire s'ouvre sur la valeur affichée (optimiste).
    expect(within(form).getByLabelText("Statut")).toHaveValue("Entretien");
    await user.type(within(form).getByLabelText("Lieu (ville)"), "Lyon");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument());

    // Réponse du formulaire arrivée d'abord : le service ne connaît pas encore le nouveau statut.
    expect(writes(calls).map((c) => c.body)).toEqual([{ status: "Entretien" }, { location: "Lyon" }]);
    const status = within(panel).getByLabelText("Statut de l'offre n° 1");
    expect(status).toHaveValue("Entretien");
    expect(within(panel).getByText("Lyon")).toBeInTheDocument();

    statusReply.resolve(jsonResponse(200, { ...OFFERS[0], status: "Entretien" }));
    await waitFor(() => expect(within(panel).getAllByText("Entretien").length).toBeGreaterThan(0));
    expect(status).toHaveValue("Entretien");
    expect(within(panel).getByText("Lyon")).toBeInTheDocument();
  });

  it("échec du formulaire : erreur affichée, valeurs revenues dans le panneau", async () => {
    const user = userEvent.setup();
    routeFetch((c) =>
      c.method === "PATCH"
        ? jsonResponse(422, { detail: "x", errors: [{ field: "company", message: "Entreprise refusée." }] })
        : undefined,
    );
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.click(within(panel).getByRole("button", { name: "Modifier" }));
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    const company = within(form).getByLabelText("Entreprise");
    await user.clear(company);
    await user.type(company, "Acme");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    expect(await within(form).findByText("Entreprise refusée.")).toBeInTheDocument();
    expect(company).toHaveValue("Acme");
    expect(within(panel).getByText("Thales")).toBeInTheDocument();
  });
});

describe("panneau de détail : erreurs et instantané du formulaire", () => {
  it("échec d'un champ rapide (422) : valeur revenue, erreur dans le panneau, fermable", async () => {
    const user = userEvent.setup();
    routeFetch((c) =>
      c.method === "PATCH"
        ? jsonResponse(422, { detail: "Statut refusé.", errors: [{ field: "status", message: "Statut refusé." }] })
        : undefined,
    );
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    const status = within(panel).getByLabelText("Statut de l'offre n° 1");
    await user.selectOptions(status, "Entretien");
    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent("Modification non enregistrée (offre n° 1, Statut)");
    expect(status).toHaveValue("À postuler");
    // Une seule alerte, dans la fenêtre modale (pas derrière, dans le tableau).
    expect(screen.getAllByRole("alert")).toEqual([alert]);
    await user.click(within(alert).getByRole("button", { name: "Fermer le message" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(panel).toBeInTheDocument();
  });

  it("modification en ligne refusée pendant que le formulaire est ouvert : le formulaire ne la renvoie pas", async () => {
    const user = userEvent.setup();
    const statusReply = deferred();
    const calls = routeFetch((c) =>
      c.method === "PATCH" && c.body && "status" in c.body ? statusReply.promise : undefined,
    );
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    await user.click(within(panel).getByRole("button", { name: "Modifier" }));
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    expect(within(form).getByLabelText("Statut")).toHaveValue("Entretien");

    statusReply.resolve(
      jsonResponse(422, { detail: "Statut refusé.", errors: [{ field: "status", message: "Statut refusé." }] }),
    );
    expect(await within(panel).findByRole("alert")).toHaveTextContent("offre n° 1, Statut");
    // Instantané : le formulaire garde la valeur de son ouverture.
    expect(within(form).getByLabelText("Statut")).toHaveValue("Entretien");

    await user.type(within(form).getByLabelText("Lieu (ville)"), "Lyon");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument());
    // Seul le champ touché dans le formulaire part : le statut refusé n'est pas renvoyé en silence.
    expect(writes(calls).map((c) => c.body)).toEqual([{ status: "Entretien" }, { location: "Lyon" }]);
    expect(within(panel).getByLabelText("Statut de l'offre n° 1")).toHaveValue("À postuler");
    expect(within(panel).getByText("Lyon")).toBeInTheDocument();
  });
});

describe("abandon d'une saisie", () => {
  it("Nouvelle offre : rien saisi, Annuler ferme sans question", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Nouvelle offre : saisie en cours, Échap / Annuler / × demandent confirmation dans la page", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm");
    const calls = routeFetch();
    renderView();
    await loaded();
    const opener = screen.getByRole("button", { name: "Nouvelle offre" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const title = within(dialog).getByLabelText("Intitulé");
    await user.type(title, "Ingénieur");

    await user.keyboard("{Escape}");
    let ask = screen.getByRole("alertdialog", { name: "Abandonner les modifications ?" });
    expect(within(ask).getByRole("button", { name: "Continuer la saisie" })).toHaveFocus();
    await user.click(within(ask).getByRole("button", { name: "Continuer la saisie" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(title).toHaveValue("Ingénieur");
    expect(title).toHaveFocus();

    // Échap dans la confirmation : retour à la saisie, le formulaire reste ouvert.
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    ask = screen.getByRole("alertdialog", { name: "Abandonner les modifications ?" });
    expect(within(ask).getByRole("button", { name: "Continuer la saisie" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Nouvelle offre" })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Fermer" }));
    ask = screen.getByRole("alertdialog", { name: "Abandonner les modifications ?" });
    await user.click(within(ask).getByRole("button", { name: "Abandonner" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(writes(calls)).toEqual([]);
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("Nouvelle offre : revenir aux valeurs initiales ne demande plus rien", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    const title = within(dialog).getByLabelText("Intitulé");
    await user.type(title, "X");
    await user.clear(title);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("modification : sans changement Annuler ferme ; avec changement, Abandonner garde l'offre intacte", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    const edit = within(panel).getByRole("button", { name: "Modifier" });
    await user.click(edit);
    let form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    await user.click(within(form).getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument();

    await user.click(edit);
    form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    const company = within(form).getByLabelText("Entreprise");
    await user.clear(company);
    await user.type(company, "Acme");
    await user.keyboard("{Escape}");
    const ask = screen.getByRole("alertdialog", { name: "Abandonner les modifications ?" });
    await user.click(within(ask).getByRole("button", { name: "Abandonner" }));
    expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: /N° 1/ })).toBeInTheDocument();
    expect(within(panel).getByText("Thales")).toBeInTheDocument();
    expect(edit).toHaveFocus();
    expect(writes(calls)).toEqual([]);
  });
});

describe("suppression", () => {
  it("demande confirmation dans la page (pas window.confirm), Échap annule, puis supprime", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm");
    const calls = routeFetch();
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    const del = within(panel).getByRole("button", { name: "Supprimer" });
    await user.click(del);
    let confirm = screen.getByRole("alertdialog", { name: "Supprimer l'offre n° 1 ?" });
    expect(within(confirm).getByRole("button", { name: "Annuler" })).toHaveFocus();

    // Échap ferme la confirmation seule ; rien n'est supprimé.
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: /N° 1/ })).toBeInTheDocument();
    expect(del).toHaveFocus();
    expect(writes(calls)).toEqual([]);

    await user.click(del);
    confirm = screen.getByRole("alertdialog", { name: "Supprimer l'offre n° 1 ?" });
    await user.click(within(confirm).getByRole("button", { name: "Supprimer définitivement" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Offre n° 1 supprimée.");
    expect(writes(calls)).toEqual([{ method: "DELETE", path: "/api/offers/1", body: null }]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Détail de l'offre n° 1" })).not.toBeInTheDocument();
    expect(screen.getByText("1 offre")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tableau des offres" })).toHaveFocus();
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("échec de la suppression : message dans la confirmation, offre conservée", async () => {
    const user = userEvent.setup();
    routeFetch((c) => (c.method === "DELETE" ? jsonResponse(404, { detail: "Offre introuvable." }) : undefined));
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.click(within(panel).getByRole("button", { name: "Supprimer" }));
    const confirm = screen.getByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Supprimer définitivement" }));
    expect(await within(confirm).findByRole("alert")).toHaveTextContent("Offre introuvable.");
    expect(screen.getByText("2 offres")).toBeInTheDocument();
  });
});

describe("envoi en cours : la fenêtre ne se ferme pas avant la réponse", () => {
  it("Nouvelle offre : Échap, × et Annuler sont sans effet pendant le POST, puis la réponse ferme", async () => {
    const user = userEvent.setup();
    const reply = deferred();
    const calls = routeFetch((c) => (c.method === "POST" ? reply.promise : undefined));
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    await user.type(within(dialog).getByLabelText("Intitulé"), "Ingénieur");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));

    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Fermer" }));
    expect(within(dialog).getByRole("button", { name: "Annuler" })).toBeDisabled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Nouvelle offre" })).toBe(dialog);

    reply.resolve(jsonResponse(201, makeOffer({ id: 6, title: "Ingénieur" })));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Offre n° 6 créée.")).toBeInTheDocument();
  });

  it("Modifier : Échap pendant le PATCH ne propose pas d'abandonner ; après un refus, on peut fermer", async () => {
    const user = userEvent.setup();
    const reply = deferred();
    routeFetch((c) => (c.method === "PATCH" ? reply.promise : undefined));
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.click(within(panel).getByRole("button", { name: "Modifier" }));
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    const company = within(form).getByLabelText("Entreprise");
    await user.clear(company);
    await user.type(company, "Acme");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Modifier l'offre n° 1" })).toBe(form);

    reply.resolve(jsonResponse(422, { detail: "x", errors: [{ field: "company", message: "Entreprise refusée." }] }));
    expect(await within(form).findByText("Entreprise refusée.")).toBeInTheDocument();
    // Réponse reçue : la fermeture redevient possible (avec confirmation, la saisie est modifiée).
    await user.click(within(form).getByRole("button", { name: "Annuler" }));
    expect(screen.getByRole("alertdialog", { name: "Abandonner les modifications ?" })).toBeInTheDocument();
  });
});

describe("où s'affiche une erreur de modification", () => {
  it("ouvrir le Détail efface une erreur affichée dans le tableau", async () => {
    const user = userEvent.setup();
    routeFetch((c) => (c.method === "PATCH" ? jsonResponse(422, { detail: "Statut refusé." }) : undefined));
    renderView();
    await loaded();
    await user.selectOptions(screen.getByLabelText("Statut de l'offre n° 1"), "Entretien");
    expect(await screen.findByRole("alert")).toHaveTextContent("offre n° 1, Statut");
    const panel = await openDetail(user, 2);
    expect(panel).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("enregistrer le formulaire efface l'erreur d'un champ rapide du panneau", async () => {
    const user = userEvent.setup();
    routeFetch((c) =>
      c.method === "PATCH" && c.body && "status" in c.body ? jsonResponse(422, { detail: "Statut refusé." }) : undefined,
    );
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    expect(await within(panel).findByRole("alert")).toHaveTextContent("offre n° 1, Statut");
    await user.click(within(panel).getByRole("button", { name: "Modifier" }));
    const form = screen.getByRole("dialog", { name: "Modifier l'offre n° 1" });
    await user.type(within(form).getByLabelText("Lieu (ville)"), "Lyon");
    await user.click(within(form).getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Modifier/ })).not.toBeInTheDocument());
    expect(within(panel).getByText("Lyon")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("fermer le panneau garde l'erreur d'une modification encore en vol : elle s'affiche dans le tableau", async () => {
    const user = userEvent.setup();
    const reply = deferred();
    routeFetch((c) => (c.method === "PATCH" ? reply.promise : undefined));
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    await user.click(within(panel).getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    reply.resolve(jsonResponse(422, { detail: "Statut refusé." }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Modification non enregistrée (offre n° 1, Statut) : Statut refusé.");
    expect(alert.closest('[role="dialog"]')).toBeNull();
    expect(screen.getByLabelText("Statut de l'offre n° 1")).toHaveDisplayValue("À postuler");
  });

  it("fermer le panneau garde l'erreur déjà affichée : elle passe dans le tableau", async () => {
    const user = userEvent.setup();
    routeFetch((c) => (c.method === "PATCH" ? jsonResponse(422, { detail: "Statut refusé." }) : undefined));
    renderView();
    await loaded();
    const panel = await openDetail(user, 1);
    await user.selectOptions(within(panel).getByLabelText("Statut de l'offre n° 1"), "Entretien");
    expect(await within(panel).findByRole("alert")).toHaveTextContent("offre n° 1, Statut");
    await user.click(within(panel).getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Modification non enregistrée (offre n° 1, Statut) : Statut refusé.");
  });
});
