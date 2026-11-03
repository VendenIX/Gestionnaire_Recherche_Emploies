import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Stats } from "../api/types";
import { OfferFiltersProvider } from "../offers/filters";
import { makeOffer } from "../offers/testData";
import { jsonResponse } from "../test/fetchMock";
import { SyntheseView, share } from "./SyntheseView";

const OFFERS = [
  makeOffer({ id: 1, department: "2A", status: "En cours", application_sent: true }),
  makeOffer({ id: 2, department: "75", status: "Entretien", application_sent: true }),
  makeOffer({ id: 3, department: null, country: "BE", status: null }),
  makeOffer({ id: 4, department: "69", status: "À postuler" }),
];

const STATS: Stats = {
  total: 4,
  by_status: { "À postuler": 1, "En cours": 1, Entretien: 1, "Offre reçue": 0, Rejeté: 0, Abandon: 0, "À qualifier": 1 },
  by_region: {},
  deadlines_soon: [
    makeOffer({ id: 4, title: "Dev Lyon", company: "Acme", department: "69", deadline: "2026-11-05", days_left: 3 }),
  ],
};

function stubApi(routes: Record<string, () => Response>) {
  const fn = vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    const route = Object.keys(routes).find((path) => url === path || url.startsWith(`${path}?`));
    return route ? routes[route]!() : jsonResponse(404, { detail: "inconnu" });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderView() {
  return render(
    <OfferFiltersProvider>
      <SyntheseView />
    </OfferFiltersProvider>,
  );
}

describe("page Synthèse", () => {
  it("affiche les compteurs par statut avec les couleurs de statut", async () => {
    stubApi({ "/api/stats": () => jsonResponse(200, STATS), "/api/offers": () => jsonResponse(200, OFFERS) });
    renderView();
    const tiles = await screen.findByLabelText("Compteurs");
    const tile = (label: string) => within(tiles).getByText(label, { selector: "dt" }).parentElement!;
    expect(tile("Offres suivies")).toHaveTextContent("4");
    expect(tile("En cours")).toHaveTextContent("1");
    expect(tile("En cours")).toHaveClass("status-en-cours");
    expect(tile("À qualifier")).toHaveClass("status-a-qualifier");
    expect(tile("Rejeté")).toHaveTextContent("0");
    expect(tile("Deadlines ≤ 7 jours")).toHaveTextContent("1");
    // Candidatures envoyées : comptées sur la liste des offres (chargée à part).
    expect(await within(tiles).findByText("2", { selector: "dd" })).toBeInTheDocument();
    expect(tile("Candidatures envoyées")).toHaveTextContent("2");
  });

  it("affiche la répartition par statut (nombre et part) et les deadlines à 7 jours", async () => {
    stubApi({ "/api/stats": () => jsonResponse(200, STATS), "/api/offers": () => jsonResponse(200, OFFERS) });
    renderView();
    const section = (await screen.findByRole("heading", { name: "Répartition par statut" })).closest("section")!;
    const rows = within(section).getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector(".bar-label")?.textContent)).toEqual([
      "À postuler",
      "En cours",
      "Entretien",
      "Offre reçue",
      "Rejeté",
      "Abandon",
      "À qualifier",
    ]);
    expect(rows[1]).toHaveTextContent("1 (25 %)");
    expect(rows[1]!.querySelector(".bar-fill")).toHaveStyle({ width: "25%" });
    expect(rows[3]).toHaveTextContent("0 (0 %)");

    const deadlines = screen.getByRole("heading", { name: "Deadlines dans les 7 jours" }).closest("section")!;
    expect(deadlines).toHaveTextContent("05/11/2026 · dans 3 j");
    expect(deadlines).toHaveTextContent("N° 4 · Dev Lyon — Acme");
  });

  it("affiche la carte avec les offres chargées (Hors France compté)", async () => {
    stubApi({ "/api/stats": () => jsonResponse(200, STATS), "/api/offers": () => jsonResponse(200, OFFERS) });
    renderView();
    expect(await screen.findByRole("button", { name: /^Corse : 1 offre$/ })).toBeInTheDocument();
    expect(screen.getByTestId("count-hors-france")).toHaveTextContent("1");
  });

  it("prend les libellés Hors France / Sans département de /api/meta", async () => {
    const meta = { regions: ["Corse", "Étranger"], no_department_label: "Lieu inconnu", statuses: [], status_unset_label: "À qualifier", departments: [], follow_up_days: 7 };
    stubApi({
      "/api/stats": () => jsonResponse(200, STATS),
      "/api/offers": () => jsonResponse(200, OFFERS),
      "/api/meta": () => jsonResponse(200, meta),
    });
    renderView();
    expect(await screen.findByRole("button", { name: /^Étranger/ })).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /^Lieu inconnu/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Hors France/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("garde les libellés par défaut si /api/meta échoue, sans alerte", async () => {
    stubApi({
      "/api/stats": () => jsonResponse(200, STATS),
      "/api/offers": () => jsonResponse(200, OFFERS),
      "/api/meta": () => jsonResponse(500, { detail: "Meta indisponible" }),
    });
    renderView();
    expect(await screen.findByRole("button", { name: /^Hors France/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Sans département/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("affiche l'erreur de chargement", async () => {
    stubApi({ "/api/stats": () => jsonResponse(500, { detail: "Base indisponible" }), "/api/offers": () => jsonResponse(200, []) });
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("Base indisponible");
  });

  it("share", () => {
    expect(share(1, 3)).toBe(33);
    expect(share(0, 0)).toBe(0);
  });
});
