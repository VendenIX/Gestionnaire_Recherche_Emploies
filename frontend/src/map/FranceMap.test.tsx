import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Offer } from "../api/types";
import { OfferFiltersProvider, useOfferFilters, type TableFilters } from "../offers/filters";
import { makeOffer } from "../offers/testData";
import { EMPTY_FILL, FranceMap } from "./FranceMap";
import { DEFAULT_SIZE } from "./projection";

const OFFERS = [
  makeOffer({ id: 1, title: "Ingénieur Ajaccio", company: "Corsica Tech", department: "2A", status: "En cours" }),
  makeOffer({ id: 2, title: "Data Bastia", department: "2B", status: "À postuler" }),
  makeOffer({ id: 3, title: "Dev Lyon", department: "69", status: "En cours" }),
  makeOffer({ id: 4, title: "Genève", department: null, country: "CH", status: "Rejeté" }),
  makeOffer({ id: 5, title: "Lausanne", department: null, country: "CH", status: "En cours" }),
  makeOffer({ id: 6, title: "Télétravail", department: null, status: "À postuler" }),
  ...[10, 11, 12, 13, 14, 15].map((id) => makeOffer({ id, title: `Paris ${id}`, department: "75" })),
];

/** Filtres courants du tableau, rendus dans le DOM pour le test. */
function Probe() {
  return <output data-testid="table-filters">{JSON.stringify(useOfferFilters().filters)}</output>;
}

const tableFilters = (): TableFilters => JSON.parse(screen.getByTestId("table-filters").textContent || "{}") as TableFilters;

function renderMap(initial: TableFilters = {}, offers: readonly Offer[] = OFFERS, props: Partial<Parameters<typeof FranceMap>[0]> = {}) {
  window.location.hash = "#/synthese";
  return render(
    <OfferFiltersProvider initial={initial}>
      <FranceMap offers={offers} {...props} />
      <Probe />
    </OfferFiltersProvider>,
  );
}

const zone = (name: RegExp) => screen.getByRole("button", { name });

describe("carte de France", () => {
  it("dessine les 13 régions, focalisables, avec un aria-label (nom et nombre d'offres)", () => {
    const { container } = renderMap();
    const zones = container.querySelectorAll("path.zone");
    expect(zones).toHaveLength(13);
    zones.forEach((p, i) => {
      // Un seul arrêt de tabulation (la première zone), les autres focalisables par les flèches.
      expect(p).toHaveAttribute("tabindex", i === 0 ? "0" : "-1");
      expect(p.getAttribute("d")).toMatch(/^M/);
    });
    expect(zone(/^Corse : 2 offres$/)).toHaveAttribute("data-code", "94");
    expect(zone(/^Auvergne-Rhône-Alpes : 1 offre$/)).toBeInTheDocument();
    expect(zone(/^Île-de-France : 6 offres$/)).toBeInTheDocument();
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("viewBox", `0 0 ${DEFAULT_SIZE.width} ${DEFAULT_SIZE.height}`);
  });

  it("grise les zones sans offre et colore les autres", () => {
    renderMap();
    const bretagne = zone(/^Bretagne : aucune offre$/);
    expect(bretagne).toHaveAttribute("fill", EMPTY_FILL);
    expect(bretagne).toHaveClass("zone-empty");
    const idf = zone(/^Île-de-France/);
    const corse = zone(/^Corse/);
    expect(idf.getAttribute("fill")).not.toBe(EMPTY_FILL);
    expect(corse.getAttribute("fill")).not.toBe(EMPTY_FILL);
    // Plus d'offres -> autre teinte.
    expect(idf.getAttribute("fill")).not.toBe(corse.getAttribute("fill"));
  });

  it("affiche les compteurs Hors France et Sans département", () => {
    renderMap();
    expect(screen.getByTestId("count-hors-france")).toHaveTextContent("2");
    expect(screen.getByTestId("count-sans-departement")).toHaveTextContent("1");
  });

  it("bascule en départements : 96 zones, 2A et 2B séparés, frontières régionales", async () => {
    const user = userEvent.setup();
    const { container } = renderMap();
    await user.click(screen.getByRole("button", { name: "Départements" }));
    expect(screen.getByRole("button", { name: "Départements" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelectorAll("path.zone")).toHaveLength(96);
    expect(zone(/^Corse-du-Sud \(2A\) : 1 offre$/)).toHaveAttribute("data-code", "2A");
    expect(zone(/^Haute-Corse \(2B\) : 1 offre$/)).toHaveAttribute("data-code", "2B");
    expect(container.querySelector("path.region-borders")?.getAttribute("d")).toMatch(/^M/);
    expect(screen.getByRole("heading", { name: "Carte des offres par département" })).toBeInTheDocument();
  });

  it("filtre par statut (Toutes ou un statut, comme l'Excel) y compris les compteurs", async () => {
    const user = userEvent.setup();
    renderMap();
    const select = screen.getByLabelText("Afficher");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Toutes",
      "À postuler",
      "En cours",
      "Entretien",
      "Offre reçue",
      "Rejeté",
      "Abandon",
    ]);
    await user.selectOptions(select, "En cours");
    expect(zone(/^Corse : 1 offre « En cours »$/)).toBeInTheDocument();
    expect(zone(/^Île-de-France : aucune offre « En cours »$/)).toHaveAttribute("fill", EMPTY_FILL);
    expect(screen.getByTestId("count-hors-france")).toHaveTextContent("1");
    expect(screen.getByTestId("count-sans-departement")).toHaveTextContent("0");
  });

  it("montre une info-bulle au survol et au focus : nom, nombre, liste courte", async () => {
    const user = userEvent.setup();
    renderMap();
    await user.hover(zone(/^Île-de-France/));
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Île-de-France");
    expect(tooltip).toHaveTextContent("6 offres");
    expect(within(tooltip).getAllByRole("listitem")).toHaveLength(6);
    expect(tooltip).toHaveTextContent("N° 10 · Paris 10");
    expect(tooltip).toHaveTextContent("et 1 autre(s)");
    expect(tooltip).not.toHaveTextContent("Paris 15");

    fireEvent.focus(zone(/^Corse/));
    const corse = screen.getByRole("tooltip");
    expect(corse).toHaveTextContent("Corse");
    expect(corse).toHaveTextContent("N° 1 · Ingénieur Ajaccio (Corsica Tech)");
    expect(zone(/^Corse/)).toHaveAttribute("aria-describedby", corse.id);
  });

  it("clic sur une région : filtre région du tableau et passage au tableau", async () => {
    const user = userEvent.setup();
    renderMap({ q: "ancien" });
    await user.click(zone(/^Corse/));
    expect(tableFilters()).toEqual({ region: "Corse" });
    expect(window.location.hash).toBe("#/tableau");
  });

  it("clic sur un département avec un statut choisi : filtres région, département et statut", async () => {
    const user = userEvent.setup();
    renderMap();
    await user.click(screen.getByRole("button", { name: "Départements" }));
    await user.selectOptions(screen.getByLabelText("Afficher"), "En cours");
    await user.click(zone(/^Corse-du-Sud/));
    expect(tableFilters()).toEqual({ region: "Corse", department: "2A", status: "En cours" });
    expect(window.location.hash).toBe("#/tableau");
  });

  it("clavier : Tab atteint les zones, Entrée et Espace activent le filtre", async () => {
    const user = userEvent.setup();
    renderMap();
    zone(/^Corse/).focus();
    await user.keyboard("{Enter}");
    expect(tableFilters()).toEqual({ region: "Corse" });

    window.location.hash = "#/synthese";
    zone(/^Bretagne/).focus();
    await user.keyboard(" ");
    expect(tableFilters()).toEqual({ region: "Bretagne" });
    expect(window.location.hash).toBe("#/tableau");

    // Tab depuis le bouton Réinitialiser : la carte n'a qu'un arrêt, la dernière zone visitée.
    screen.getByRole("button", { name: "Réinitialiser" }).focus();
    await user.tab();
    expect(document.activeElement).toHaveAttribute("data-code", "53");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Bretagne");
  });

  it("les compteurs Hors France et Sans département filtrent aussi le tableau", async () => {
    const user = userEvent.setup();
    renderMap();
    await user.click(screen.getByRole("button", { name: /^Hors France/ }));
    expect(tableFilters()).toEqual({ region: "Hors France" });
    await user.click(screen.getByRole("button", { name: /^Sans département/ }));
    expect(tableFilters()).toEqual({ region: "Sans département" });
  });

  it("Réinitialiser remet Régions, Toutes et vide les filtres du tableau", async () => {
    const user = userEvent.setup();
    const { container } = renderMap({ region: "Corse", status: "Rejeté" });
    await user.click(screen.getByRole("button", { name: "Départements" }));
    await user.selectOptions(screen.getByLabelText("Afficher"), "Rejeté");
    await user.click(screen.getByRole("button", { name: "Réinitialiser" }));
    expect(screen.getByLabelText("Afficher")).toHaveValue("Toutes");
    expect(screen.getByRole("button", { name: "Régions" })).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelectorAll("path.zone")).toHaveLength(13);
    expect(tableFilters()).toEqual({});
    expect(window.location.hash).toBe("#/synthese");
  });

  it("propose un tableau des comptes en alternative texte", async () => {
    const user = userEvent.setup();
    renderMap();
    await user.click(screen.getByText(/Tableau des comptes/));
    const table = screen.getByRole("table");
    const row = (name: string) => within(table).getByRole("rowheader", { name }).closest("tr")!;
    expect(row("Corse")).toHaveTextContent("2");
    expect(row("Bretagne")).toHaveTextContent("0");
    expect(row("Hors France")).toHaveTextContent("2");
    expect(row("Sans département")).toHaveTextContent("1");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 13 + 2);
  });

  it("ferme l'info-bulle : perte du focus, Échap (focus et survol), sortie du SVG", async () => {
    const user = userEvent.setup();
    const { container } = renderMap();
    const svg = container.querySelector("svg")!;

    fireEvent.focus(zone(/^Corse/));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Corse");
    fireEvent.blur(zone(/^Corse/));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    act(() => zone(/^Corse/).focus());
    expect(screen.getByRole("tooltip")).toHaveTextContent("Corse");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(window.location.hash).toBe("#/synthese");
    act(() => zone(/^Corse/).blur());

    // Survol seul (le focus reste ailleurs) : Échap ferme aussi (WCAG 1.4.13).
    await user.hover(zone(/^Île-de-France/));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Île-de-France");
    expect(document.activeElement).toBe(document.body);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.hover(zone(/^Bretagne/));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Bretagne");
    fireEvent.mouseLeave(svg);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("compte à part un département hors référentiel (971, France) : compteur et ligne du tableau", async () => {
    const user = userEvent.setup();
    renderMap({}, [...OFFERS, makeOffer({ id: 20, title: "Pointe-à-Pitre", department: "971", country: "FR" })]);
    expect(screen.getByTestId("count-autres")).toHaveTextContent("1");
    expect(screen.getByText("Autres (hors référentiel)", { selector: ".map-counter-label" })).toBeInTheDocument();
    // Ni Hors France ni Sans département.
    expect(screen.getByTestId("count-hors-france")).toHaveTextContent("2");
    expect(screen.getByTestId("count-sans-departement")).toHaveTextContent("1");
    await user.click(screen.getByText(/Tableau des comptes/));
    const table = screen.getByRole("table");
    const row = within(table).getByRole("rowheader", { name: "Autres (hors référentiel)" }).closest("tr")!;
    expect(row).toHaveTextContent("1");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 13 + 3);
  });

  it("sans offre hors référentiel, pas de compteur Autres", () => {
    renderMap();
    expect(screen.queryByTestId("count-autres")).not.toBeInTheDocument();
  });

  it("utilise les libellés fournis (meta) pour les compteurs, le tableau et le filtre", async () => {
    const user = userEvent.setup();
    renderMap({}, OFFERS, { labels: { horsFrance: "Étranger", noDepartment: "Lieu inconnu" } });
    expect(screen.queryByRole("button", { name: /^Hors France/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Étranger/ }));
    expect(tableFilters()).toEqual({ region: "Étranger" });
    await user.click(screen.getByRole("button", { name: /^Lieu inconnu/ }));
    expect(tableFilters()).toEqual({ region: "Lieu inconnu" });
    await user.click(screen.getByText(/Tableau des comptes/));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("rowheader", { name: "Étranger" }).closest("tr")).toHaveTextContent("2");
    expect(within(table).getByRole("rowheader", { name: "Lieu inconnu" }).closest("tr")).toHaveTextContent("1");
  });

  it("suit la taille du conteneur (ResizeObserver) et se déconnecte au démontage", () => {
    const observers: { callback: ResizeObserverCallback; observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }[] = [];
    class FakeResizeObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
      constructor(public callback: ResizeObserverCallback) {
        observers.push(this);
      }
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const { container, unmount } = renderMap();
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("viewBox", `0 0 ${DEFAULT_SIZE.width} ${DEFAULT_SIZE.height}`);
    expect(observers).toHaveLength(1);
    const observer = observers[0]!;
    expect(observer.observe).toHaveBeenCalledWith(container.querySelector(".map-canvas"));
    const before = zone(/^Corse/).getAttribute("d");

    const fire = (width: number) =>
      act(() => {
        observer.callback([{ contentRect: { width } } as ResizeObserverEntry], observer as unknown as ResizeObserver);
      });
    fire(320);
    expect(svg).toHaveAttribute("viewBox", "0 0 320 309");
    const after = zone(/^Corse/).getAttribute("d");
    expect(after).toMatch(/^M/);
    expect(after).not.toBe(before);

    // Largeur nulle (conteneur masqué) : taille conservée.
    fire(0);
    expect(svg).toHaveAttribute("viewBox", "0 0 320 309");

    expect(observer.disconnect).not.toHaveBeenCalled();
    unmount();
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
  });
});

describe("carte de France : clavier (roving tabindex)", () => {
  const focusedCode = () => document.activeElement?.getAttribute("data-code") ?? null;

  it("Départements : un seul arrêt de tabulation, flèches entre voisins, Origine / Fin", async () => {
    const user = userEvent.setup();
    const { container } = renderMap();
    await user.click(screen.getByRole("button", { name: "Départements" }));
    const stops = () => [...container.querySelectorAll('path.zone[tabindex="0"]')].map((p) => p.getAttribute("data-code"));
    expect(stops()).toEqual(["01"]);
    expect(container.querySelectorAll('path.zone[tabindex="-1"]')).toHaveLength(95);

    // Tab entre dans la carte puis en sort aussitôt (pas 96 arrêts).
    screen.getByRole("button", { name: "Réinitialiser" }).focus();
    await user.tab();
    expect(focusedCode()).toBe("01");
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Hors France/ }));

    // Flèches : Lyon (69) -> voisin à l'est, au nord, etc. ; le focus et l'info-bulle suivent.
    act(() => (container.querySelector('path.zone[data-code="69"]') as SVGPathElement).focus());
    await user.keyboard("{ArrowRight}");
    const east = focusedCode();
    expect(east).not.toBe("69");
    expect(east).not.toBeNull();
    expect(stops()).toEqual([east]);
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      container.querySelector(`path.zone[data-code="${east}"]`)!.getAttribute("aria-label")!.split(" : ")[0]!,
    );
    await user.keyboard("{Home}");
    expect(focusedCode()).toBe("01");
    await user.keyboard("{End}");
    expect(focusedCode()).toBe("95");

    // Maj+Tab hors de la carte puis Tab : retour sur la dernière zone visitée.
    await user.tab({ shift: true });
    expect(focusedCode()).toBeNull();
    await user.tab();
    expect(focusedCode()).toBe("95");
    // Entrée sur la zone atteinte au clavier filtre le tableau.
    await user.keyboard("{Enter}");
    expect(tableFilters()).toEqual({ region: "Île-de-France", department: "95" });
  });

  it("Régions : Finistère vers l'est puis retour ; changer de découpage remet l'arrêt sur la première zone", async () => {
    const user = userEvent.setup();
    const { container } = renderMap();
    act(() => zone(/^Bretagne/).focus());
    await user.keyboard("{ArrowLeft}");
    // Rien plus à l'ouest que la Bretagne : le focus reste.
    expect(focusedCode()).toBe("53");
    await user.keyboard("{ArrowRight}");
    expect(focusedCode()).not.toBe("53");
    await user.click(screen.getByRole("button", { name: "Départements" }));
    await user.click(screen.getByRole("button", { name: "Régions" }));
    expect(container.querySelector('path.zone[tabindex="0"]')).toHaveAttribute("data-code", "84");
  });

  it("une flèche ne fait pas défiler la page (preventDefault), une autre touche si", () => {
    renderMap();
    const corse = zone(/^Corse/);
    act(() => corse.focus());
    expect(fireEvent.keyDown(corse, { key: "ArrowDown" })).toBe(false);
    expect(fireEvent.keyDown(corse, { key: "a" })).toBe(true);
  });
});

describe("carte de France : info-bulle dans le cadre", () => {
  function stubTooltipSize(width: number, height: number) {
    const size = (dim: number) =>
      function (this: HTMLElement) {
        return this.getAttribute("role") === "tooltip" ? dim : 0;
      };
    const w = vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(size(width));
    const h = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(size(height));
    return () => {
      w.mockRestore();
      h.mockRestore();
    };
  }

  const px = (value: string) => Number(value.replace("px", ""));

  it("décalée près des bords gauche et droit, passée dessous en haut, jamais hors du cadre", async () => {
    const restore = stubTooltipSize(200, 80);
    try {
      const user = userEvent.setup();
      renderMap();
      const { width, height } = DEFAULT_SIZE;
      const check = () => {
        const tip = screen.getByRole("tooltip");
        const left = px(tip.style.left);
        const top = px(tip.style.top);
        expect(left).toBeGreaterThanOrEqual(4);
        expect(left + 200).toBeLessThanOrEqual(width - 4);
        expect(top).toBeGreaterThanOrEqual(4);
        expect(top + 80).toBeLessThanOrEqual(height - 4);
        return { tip, left, top };
      };
      await user.hover(zone(/^Bretagne/));
      expect(check().left).toBe(4);
      await user.hover(zone(/^Corse/));
      expect(check().left).toBe(width - 200 - 4);
      const north = (await user.hover(zone(/^Hauts-de-France/)), check());
      expect(north.tip).toHaveClass("below");
      await user.hover(zone(/^Occitanie/));
      expect(check().tip).not.toHaveClass("below");
    } finally {
      restore();
    }
  });
});

describe("carte de France : écouteur Échap du document", () => {
  it("est retiré à la fermeture de l'info-bulle et au démontage", async () => {
    const user = userEvent.setup();
    const add = vi.spyOn(document, "addEventListener");
    const remove = vi.spyOn(document, "removeEventListener");
    try {
      const { unmount } = renderMap();
      const keydownAdds = () => add.mock.calls.filter(([type]) => type === "keydown").map(([, fn]) => fn);
      const keydownRemoves = () => remove.mock.calls.filter(([type]) => type === "keydown").map(([, fn]) => fn);
      const before = keydownAdds().length;

      await user.hover(zone(/^Bretagne/));
      const first = keydownAdds()[before];
      expect(first).toBeTypeOf("function");
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
      expect(keydownRemoves()).toContain(first);

      await user.hover(zone(/^Corse/));
      const second = keydownAdds().at(-1);
      expect(second).not.toBe(first);
      expect(keydownRemoves()).not.toContain(second);
      unmount();
      expect(keydownRemoves()).toContain(second);
    } finally {
      add.mockRestore();
      remove.mockRestore();
    }
  });
});
