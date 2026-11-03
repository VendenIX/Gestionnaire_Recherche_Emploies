import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { getMeta } from "./api/client";
import { getToken, setToken, TOKEN_STORAGE_KEY } from "./api/token";
import { headerOf, jsonResponse, META, mockFetch } from "./test/fetchMock";
import { makeOffer } from "./offers/testData";

describe("écran du jeton", () => {
  it("demande le jeton, le vérifie par un appel authentifié puis le mémorise", async () => {
    const user = userEvent.setup();
    const fetchFn = mockFetch(jsonResponse(200, META));
    render(<App />);

    expect(screen.getByRole("heading", { name: "Suivi des offres" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Jeton d'accès"), "  bon-jeton  ");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByRole("button", { name: "Se déconnecter" })).toBeInTheDocument();
    // Un seul appel de vérification ; les suivants sont le chargement du tableau (meta + offres).
    expect(fetchFn.mock.calls[0]?.[0]).toBe("/api/meta");
    expect(fetchFn.mock.calls.slice(1).map((c) => c[0]).sort()).toEqual(["/api/meta", "/api/offers"]);
    expect(headerOf(fetchFn, 0, "Authorization")).toBe("Bearer bon-jeton");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe("bon-jeton");
    expect(screen.getByRole("navigation", { name: "Navigation principale" })).toBeInTheDocument();
  });

  it("refuse un jeton invalide sans le mémoriser", async () => {
    const user = userEvent.setup();
    mockFetch(jsonResponse(401, { detail: "Authentification requise" }));
    render(<App />);

    await user.type(screen.getByLabelText("Jeton d'accès"), "mauvais");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Jeton refusé");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(screen.queryByRole("button", { name: "Se déconnecter" })).not.toBeInTheDocument();
  });

  it("refuse un jeton vide sans appeler l'API", async () => {
    const user = userEvent.setup();
    const fetchFn = mockFetch();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Se connecter" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Saisissez le jeton");
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each(["jeton-é", "prix€", "“jeton”", "deux mots"])(
    "refuse le jeton « %s » aux caractères non valides sans appeler l'API",
    async (token) => {
      const user = userEvent.setup();
      const fetchFn = mockFetch();
      render(<App />);
      await user.type(screen.getByLabelText("Jeton d'accès"), token);
      await user.click(screen.getByRole("button", { name: "Se connecter" }));
      expect(screen.getByRole("alert")).toHaveTextContent("Le jeton contient des caractères non valides.");
      expect(fetchFn).not.toHaveBeenCalled();
      expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    },
  );

  it("affiche l'erreur du service injoignable", async () => {
    const user = userEvent.setup();
    const fn = mockFetch();
    fn.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<App />);
    await user.type(screen.getByLabelText("Jeton d'accès"), "x");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Service injoignable");
  });
});

describe("application connectée", () => {
  it("ouvre directement l'application si un jeton est mémorisé", () => {
    setToken("memo");
    render(<App />);
    expect(screen.getByRole("button", { name: "Se déconnecter" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Jeton d'accès")).not.toBeInTheDocument();
  });

  it("« Se déconnecter » efface le jeton de localStorage et réaffiche l'écran du jeton", async () => {
    const user = userEvent.setup();
    setToken("memo");
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Se déconnecter" }));
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(getToken()).toBeNull();
    expect(screen.getByLabelText("Jeton d'accès")).toBeInTheDocument();
    expect(screen.getByText("Vous êtes déconnecté.")).toBeInTheDocument();
  });

  it("un 401 sur un appel ultérieur efface le jeton et réaffiche l'écran du jeton", async () => {
    setToken("perime");
    render(<App />);
    expect(screen.getByRole("button", { name: "Se déconnecter" })).toBeInTheDocument();

    mockFetch(jsonResponse(401, { detail: "Authentification requise" }));
    await act(async () => {
      await getMeta().catch(() => undefined);
    });

    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(screen.getByLabelText("Jeton d'accès")).toBeInTheDocument();
    expect(screen.getByText(/refusé le jeton mémorisé/)).toBeInTheDocument();
  });

  it("repères et lien d'évitement : Tab le montre en premier, il donne le focus au contenu sans changer de vue", async () => {
    const user = userEvent.setup();
    setToken("memo");
    window.location.hash = "#/synthese";
    render(<App />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Suivi des offres" })).toBeInTheDocument();
    const main = screen.getByRole("main");
    await user.tab();
    const skip = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(skip).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(main).toHaveFocus();
    expect(window.location.hash).toBe("#/synthese");
    expect(await screen.findByRole("heading", { name: "Synthèse" })).toBeInTheDocument();
  });

  it("navigue entre Tableau et Synthèse par le fragment d'URL", async () => {
    const user = userEvent.setup();
    setToken("memo");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Tableau des offres" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tableau" })).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("link", { name: "Synthèse" }));
    expect(await screen.findByRole("heading", { name: "Synthèse" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Synthèse" })).toHaveAttribute("aria-current", "page");
  });

  it("un clic sur la carte de la Synthèse ouvre le tableau filtré sur la région", async () => {
    const user = userEvent.setup();
    const offers = [makeOffer({ id: 1, department: "2A", region: "Corse" })];
    const fn = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/meta") return jsonResponse(200, META);
      if (url === "/api/stats")
        return jsonResponse(200, { total: 1, by_status: { "À postuler": 1 }, by_region: {}, deadlines_soon: [] });
      if (url.startsWith("/api/offers")) return jsonResponse(200, offers);
      return jsonResponse(404, { detail: "inconnu" });
    });
    vi.stubGlobal("fetch", fn);
    setToken("memo");
    window.location.hash = "#/synthese";
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /^Corse : 1 offre$/ }));
    expect(await screen.findByRole("heading", { name: "Tableau des offres" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/tableau");
    expect(screen.getByLabelText("Région")).toHaveValue("Corse");
    expect(fn.mock.calls.map((c) => String(c[0]))).toContain("/api/offers?region=Corse");
  });
});
