import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { setToken } from "../api/token";
import type { Meta, Offer } from "../api/types";
import { jsonResponse } from "../test/fetchMock";
import { OfferFiltersProvider, useOfferFilters } from "./filters";
import { OffersView } from "./OffersView";
import { makeOffer } from "./testData";

const NOW = new Date(2026, 10, 2, 12, 0, 0); // 2 novembre 2026

const META: Meta = {
  statuses: ["À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon"],
  status_unset_label: "À qualifier",
  regions: ["Corse", "Nouvelle-Aquitaine", "Hors France"],
  no_department_label: "Sans département",
  departments: [
    { code: "19", name: "Corrèze", region: "Nouvelle-Aquitaine" },
    { code: "2A", name: "Corse-du-Sud", region: "Corse" },
    { code: "2B", name: "Haute-Corse", region: "Corse" },
    { code: "21", name: "Côte-d'Or", region: "Bourgogne-Franche-Comté" },
  ],
  follow_up_days: 7,
};

const OFFERS: Offer[] = [
  makeOffer({ id: 1, title: "Ingénieur", company: "Thales", department: "21", status: "À postuler", deadline: "2026-11-05" }),
  makeOffer({ id: 2, department: "2B", region: "Corse", status: "Rejeté", deadline: "2026-11-02" }),
  makeOffer({ id: 3, department: "19", status: null, deadline: null, deadline_note: "?" }),
  makeOffer({
    id: 4,
    department: "2A",
    region: "Corse",
    status: "En cours",
    applied_at: "2026-10-20T09:00:00",
    application_sent: true,
  }),
  makeOffer({ id: 5, status: "En cours", applied_at: "2026-10-30T09:00:00" }),
];

interface Call {
  method: string;
  path: string;
  params: URLSearchParams;
  body: unknown;
  auth: string | null;
}

/** fetch simulé par route : /api/meta, GET /api/offers, PATCH /api/offers/:id, /api/export.xlsx. */
function routeFetch(options: { patch?: (id: number, body: Record<string, unknown>) => Response } = {}) {
  const calls: Call[] = [];
  const fn = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    calls.push({
      method,
      path: url.pathname,
      params: url.searchParams,
      body,
      auth: new Headers(init?.headers).get("Authorization"),
    });
    if (url.pathname === "/api/meta") return jsonResponse(200, META);
    if (url.pathname === "/api/export.xlsx") {
      return new Response(new Blob(["xlsx"]), { status: 200 });
    }
    if (url.pathname === "/api/offers" && method === "GET") return jsonResponse(200, OFFERS);
    const m = /^\/api\/offers\/(\d+)$/.exec(url.pathname);
    if (m && method === "PATCH") {
      const id = Number(m[1]);
      if (options.patch) return options.patch(id, body ?? {});
      const offer = OFFERS.find((o) => o.id === id);
      return jsonResponse(200, { ...offer, ...body });
    }
    return jsonResponse(404, { detail: "inconnu" });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const offerGets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.path === "/api/offers");

function renderView(extra?: React.ReactNode) {
  return render(
    <OfferFiltersProvider>
      {extra}
      <OffersView />
    </OfferFiltersProvider>,
  );
}

async function loaded() {
  await screen.findByText("5 offres");
  // Les menus de filtres sont remplis par /api/meta.
  await screen.findByRole("option", { name: "Corse" });
}

function rowOf(id: number): HTMLElement {
  const cell = screen.getAllByRole("cell").find((c) => c.classList.contains("cell-id") && c.textContent === String(id));
  if (!cell) throw new Error(`ligne ${id} introuvable`);
  return cell.closest("tr") as HTMLElement;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  setToken("jeton-test");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("tableau des offres : affichage", () => {
  it("Statut, Deadline et Jours restants sont les trois premières colonnes", async () => {
    routeFetch();
    renderView();
    await loaded();
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.replace(/[▲▼↕]/g, "").trim());
    expect(headers.slice(0, 3)).toEqual(["Statut", "Deadline", "Jours restants"]);
  });

  it("jours restants, urgence, note de deadline, À qualifier et lignes estompées", async () => {
    routeFetch();
    renderView();
    await loaded();

    const r1 = rowOf(1);
    const days1 = r1.querySelector(".cell-days");
    expect(days1).toHaveTextContent("dans 3 j");
    expect(days1).toHaveClass("deadline-soon");
    expect(r1.querySelector(".cell-deadline")).toHaveTextContent("05/11/2026");
    expect(within(r1).getByLabelText("Statut de l'offre n° 1")).toHaveClass("status-a-postuler");

    // Rejeté : ligne estompée, pas de jours restants même si la deadline est aujourd'hui.
    const r2 = rowOf(2);
    expect(r2).toHaveClass("row-closed");
    expect(r2.querySelector(".cell-days")).toHaveTextContent("");
    expect(rowOf(1)).not.toHaveClass("row-closed");

    // Sans statut : « À qualifier » ; sans deadline : la note est affichée.
    const r3 = rowOf(3);
    expect(within(r3).getByLabelText("Statut de l'offre n° 3")).toHaveDisplayValue("À qualifier");
    expect(within(r3).getByLabelText("Statut de l'offre n° 3")).toHaveClass("status-a-qualifier");
    expect(r3.querySelector(".cell-deadline")).toHaveTextContent("?");
  });

  it("badge de relance suggérée selon follow_up_days de /api/meta", async () => {
    routeFetch();
    renderView();
    await loaded();
    // Offre 4 : candidature il y a 13 jours (> 7) ; offre 5 : il y a 3 jours.
    expect(within(rowOf(4)).getByText("Relance suggérée")).toBeInTheDocument();
    expect(within(rowOf(5)).queryByText("Relance suggérée")).not.toBeInTheDocument();
    expect(within(rowOf(1)).queryByText("Relance suggérée")).not.toBeInTheDocument();
  });
});

describe("tableau des offres : filtres", () => {
  it("statut, région, département et texte sont envoyés à GET /api/offers", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    expect(offerGets(calls)[0]?.params.toString()).toBe("");

    await user.selectOptions(screen.getByLabelText("Statut"), "À qualifier");
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.get("status")).toBe("À qualifier"));

    await user.selectOptions(screen.getByLabelText("Région"), "Corse");
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.get("region")).toBe("Corse"));

    // Le menu des départements suit la région choisie, dans l'ordre du référentiel.
    const depOptions = within(screen.getByLabelText("Département"))
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(depOptions).toEqual(["", "2A", "2B"]);
    await user.selectOptions(screen.getByLabelText("Département"), "2A");
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.get("department")).toBe("2A"));

    await user.type(screen.getByLabelText("Recherche"), "thales");
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.get("q")).toBe("thales"));
    const last = offerGets(calls).at(-1)?.params;
    expect(Object.fromEntries(last ?? [])).toEqual({
      status: "À qualifier",
      region: "Corse",
      department: "2A",
      q: "thales",
    });
    // Recherche différée : pas un appel par lettre.
    expect(offerGets(calls).filter((c) => c.params.has("q")).length).toBeLessThan(3);

    await user.click(screen.getByRole("button", { name: "Effacer les filtres" }));
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.toString()).toBe(""));
  });

  it("sans région choisie : tous les départements dans l'ordre 19 < 2A < 2B < 21", async () => {
    routeFetch();
    renderView();
    await loaded();
    const values = within(screen.getByLabelText("Département"))
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["", "19", "2A", "2B", "21"]);
  });

  it("les filtres peuvent être réglés de l'extérieur (carte de la Synthèse)", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    function MapStub() {
      const { setFilters } = useOfferFilters();
      return (
        <button type="button" onClick={() => setFilters({ region: "Corse" })}>
          Carte : Corse
        </button>
      );
    }
    renderView(<MapStub />);
    await loaded();
    await user.click(screen.getByRole("button", { name: "Carte : Corse" }));
    await waitFor(() => expect(offerGets(calls).at(-1)?.params.toString()).toBe("region=Corse"));
    expect(screen.getByLabelText("Région")).toHaveDisplayValue("Corse");
  });
});

describe("tableau des offres : tri", () => {
  it("tri par département dans l'ordre du référentiel, au clavier, avec aria-sort", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    const ids = () =>
      screen
        .getAllByRole("cell")
        .filter((c) => c.classList.contains("cell-id"))
        .map((c) => c.textContent);
    expect(ids()).toEqual(["1", "2", "3", "4", "5"]);
    expect(screen.getByRole("columnheader", { name: /N°/ })).toHaveAttribute("aria-sort", "ascending");

    const button = screen.getByRole("button", { name: /Département \/ Région/ });
    button.focus();
    await user.keyboard("{Enter}");
    // 19 (3) < 2A (4) < 2B (2) < 21 (1), puis sans département (5).
    expect(ids()).toEqual(["3", "4", "2", "1", "5"]);
    const header = screen.getByRole("columnheader", { name: /Département/ });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("columnheader", { name: /N°/ })).not.toHaveAttribute("aria-sort");

    await user.click(button);
    expect(ids()).toEqual(["1", "2", "4", "3", "5"]);
    expect(header).toHaveAttribute("aria-sort", "descending");
  });

  it("tri par jours restants", async () => {
    const user = userEvent.setup();
    routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: /Jours restants/ }));
    const first = screen.getAllByRole("cell").find((c) => c.classList.contains("cell-id"));
    expect(first).toHaveTextContent("1");
  });
});

describe("tableau des offres : édition en ligne", () => {
  it("le statut envoie un PATCH du seul champ modifié", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    await user.selectOptions(screen.getByLabelText("Statut de l'offre n° 1"), "Entretien");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.path).toBe("/api/offers/1");
    expect(patch?.body).toEqual({ status: "Entretien" });
    expect(patch?.auth).toBe("Bearer jeton-test");
    expect(screen.getByLabelText("Statut de l'offre n° 1")).toHaveDisplayValue("Entretien");
  });

  it("un champ texte : Entrée enregistre, valeur vide -> null ; Échap annule", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Modifier : Entreprise de l'offre n° 1" }));
    const input = screen.getByLabelText("Entreprise de l'offre n° 1");
    await user.clear(input);
    await user.type(input, "Acme{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ company: "Acme" });
    expect(await screen.findByRole("button", { name: "Modifier : Entreprise de l'offre n° 1" })).toHaveTextContent(
      "Acme",
    );

    await user.click(screen.getByRole("button", { name: "Modifier : Lieu de l'offre n° 1" }));
    await user.type(screen.getByLabelText("Lieu de l'offre n° 1"), "Lyon{Escape}");
    await user.click(screen.getByRole("button", { name: "Modifier : Intitulé de l'offre n° 1" }));
    await user.clear(screen.getByLabelText("Intitulé de l'offre n° 1"));
    await user.tab();
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toEqual({ title: null });
  });

  it("la deadline et les cases à cocher envoient leur champ seul", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    renderView();
    await loaded();
    await user.click(screen.getByLabelText("Relance faite (offre n° 4)"));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ follow_up_done: true });
    // Relance faite : le badge disparaît.
    await waitFor(() => expect(within(rowOf(4)).queryByText("Relance suggérée")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Modifier : Deadline de l'offre n° 3" }));
    const date = screen.getByLabelText("Deadline de l'offre n° 3");
    await user.type(date, "2026-11-10");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toEqual({ deadline: "2026-11-10" });
    expect(rowOf(3).querySelector(".cell-days")).toHaveTextContent("dans 8 j");
  });

  it("refus du serveur : retour à l'ancienne valeur et message en français", async () => {
    const user = userEvent.setup();
    routeFetch({
      patch: () => jsonResponse(422, { detail: "statut inconnu", errors: [{ field: "status", message: "x" }] }),
    });
    renderView();
    await loaded();
    await user.selectOptions(screen.getByLabelText("Statut de l'offre n° 1"), "Abandon");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Modification non enregistrée (offre n° 1, Statut) : statut inconnu");
    expect(screen.getByLabelText("Statut de l'offre n° 1")).toHaveDisplayValue("À postuler");
    expect(rowOf(1)).not.toHaveClass("row-closed");

    await user.click(screen.getByLabelText("Candidature envoyée (offre n° 4)"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("offre n° 4, Candidature envoyée"));
    expect(screen.getByLabelText("Candidature envoyée (offre n° 4)")).toBeChecked();
  });

  it("service injoignable : retour arrière aussi", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    const real = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input, init) =>
        init?.method === "PATCH" ? Promise.reject(new TypeError("Failed to fetch")) : real(input, init),
      ),
    );
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Modifier : Entreprise de l'offre n° 1" }));
    await user.type(screen.getByLabelText("Entreprise de l'offre n° 1"), " SA{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Service injoignable");
    expect(screen.getByRole("button", { name: "Modifier : Entreprise de l'offre n° 1" })).toHaveTextContent(/^Thales$/);
    expect(calls.length).toBeGreaterThan(0);
  });
});

describe("tableau des offres : modifications concurrentes", () => {
  /** Service simulé avec état : PATCH appliqué à l'arrivée ; `hold` retient la réponse (ou l'échec). */
  function statefulServer(hold: (body: Record<string, unknown>, n: number) => "ok" | "hold" | "hold-fail") {
    const state = { offer: makeOffer({ id: 1, title: "Ing", company: "Thales", status: "À postuler" }) };
    const releases: (() => void)[] = [];
    let n = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async (input, init) => {
        const url = new URL(String(input), "http://test");
        if (url.pathname === "/api/meta") return jsonResponse(200, META);
        if (init?.method === "PATCH") {
          const body = JSON.parse(String(init.body)) as Record<string, unknown>;
          const mode = hold(body, n++);
          if (mode === "hold-fail") {
            await new Promise<void>((resolve) => releases.push(resolve));
            return jsonResponse(422, { detail: "refus" });
          }
          state.offer = { ...state.offer, ...body };
          const snapshot = { ...state.offer };
          if (mode === "hold") await new Promise<void>((resolve) => releases.push(resolve));
          return jsonResponse(200, snapshot);
        }
        return jsonResponse(200, [state.offer]);
      }),
    );
    return { state, release: () => releases.shift()?.() };
  }

  const companyButton = () => screen.getByRole("button", { name: "Modifier : Entreprise de l'offre n° 1" });
  const statusSelect = () => screen.getByLabelText("Statut de l'offre n° 1") as HTMLSelectElement;
  const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 20)));

  it("une réponse ancienne n'écrase pas une modification plus récente d'un autre champ", async () => {
    const user = userEvent.setup();
    const server = statefulServer((body) => ("status" in body ? "hold" : "ok"));
    renderView();
    await screen.findByText("1 offre");
    await user.selectOptions(statusSelect(), "Entretien");
    await user.click(companyButton());
    await user.clear(screen.getByLabelText("Entreprise de l'offre n° 1"));
    await user.type(screen.getByLabelText("Entreprise de l'offre n° 1"), "Acme{Enter}");
    await waitFor(() => expect(companyButton()).toHaveTextContent(/^Acme$/));
    await settle();
    server.release(); // réponse du PATCH statut, prise avant celui de l'entreprise
    await settle();
    expect(server.state.offer.company).toBe("Acme");
    expect(server.state.offer.status).toBe("Entretien");
    expect(companyButton()).toHaveTextContent(/^Acme$/);
    expect(statusSelect().value).toBe("Entretien");
  });

  it("un échec ancien ne revient pas en arrière par-dessus une modification enregistrée", async () => {
    const user = userEvent.setup();
    const server = statefulServer((_body, n) => (n === 0 ? "hold-fail" : "ok"));
    renderView();
    await screen.findByText("1 offre");
    await user.selectOptions(statusSelect(), "Entretien");
    await user.selectOptions(statusSelect(), "En cours");
    await settle();
    server.release(); // le premier PATCH échoue après le succès du second
    await settle();
    expect(server.state.offer.status).toBe("En cours");
    expect(statusSelect().value).toBe("En cours");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("le dernier échec revient à la dernière valeur enregistrée", async () => {
    const user = userEvent.setup();
    const server = statefulServer((_body, n) => (n === 1 ? "hold-fail" : "ok"));
    renderView();
    await screen.findByText("1 offre");
    await user.selectOptions(statusSelect(), "Entretien");
    await waitFor(() => expect(server.state.offer.status).toBe("Entretien"));
    await user.selectOptions(statusSelect(), "En cours");
    await settle();
    server.release();
    expect(await screen.findByRole("alert")).toHaveTextContent("offre n° 1, Statut");
    expect(server.state.offer.status).toBe("Entretien");
    expect(statusSelect().value).toBe("Entretien");
  });
});

describe("tableau des offres : export", () => {
  it("« Exporter en Excel » télécharge /api/export.xlsx avec le jeton", async () => {
    const user = userEvent.setup();
    const calls = routeFetch();
    // jsdom ne fournit pas ces fonctions : on les crée le temps du test, puis on les retire.
    const hadCreate = "createObjectURL" in URL;
    const hadRevoke = "revokeObjectURL" in URL;
    if (!hadCreate) URL.createObjectURL = () => "";
    if (!hadRevoke) URL.revokeObjectURL = () => undefined;
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    onTestFinished(() => {
      createObjectURL.mockRestore();
      revokeObjectURL.mockRestore();
      const url = URL as unknown as Record<string, unknown>;
      if (!hadCreate) delete url.createObjectURL;
      if (!hadRevoke) delete url.revokeObjectURL;
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("suivi-offres-2026-11-02.xlsx");
      expect(this.href).toBe("blob:test");
    });
    renderView();
    await loaded();

    await user.click(screen.getByRole("button", { name: "Exporter en Excel" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const exportCall = calls.find((c) => c.path === "/api/export.xlsx");
    expect(exportCall?.auth).toBe("Bearer jeton-test");
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:test");
  });

  it("erreur d'export affichée en français", async () => {
    const user = userEvent.setup();
    routeFetch();
    const real = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input, init) =>
        String(input).includes("export") ? Promise.resolve(jsonResponse(500, null)) : real(input, init),
      ),
    );
    renderView();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Exporter en Excel" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Export impossible : Erreur du serveur (HTTP 500).");
  });
});
