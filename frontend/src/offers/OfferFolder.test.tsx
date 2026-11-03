// Dossier Nextcloud dans le panneau de détail : bouton « Ouvrir le dossier » actif ou désactivé
// selon /api/meta, liste des fichiers, dossier absent avec « Créer le dossier », erreurs 409/502/503,
// chargement et clavier. fetch simulé par route (GET/POST /api/offers/{id}/folder).

import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/token";
import type { Folder, Offer } from "../api/types";
import { jsonResponse, META } from "../test/fetchMock";
import { NEXTCLOUD_NOTE } from "./folder";
import { OfferFiltersProvider } from "./filters";
import { useOfferFolder, type FolderState } from "./OfferFolder";
import { OffersView } from "./OffersView";
import { makeOffer } from "./testData";

const OFFERS: Offer[] = [
  makeOffer({ id: 1, title: "Ingénieur", company: "Thales", status: "À postuler" }),
  makeOffer({ id: 2, title: "Chef de projet", status: "En cours" }),
];

const NC = "http://nc.local";
const DIR = "/Candidatures/01%20-%20Ing%C3%A9nieur%20Thales";
const FOLDER: Folder = {
  name: "01 - Ingénieur Thales",
  url: `${NC}/apps/files/files/100?dir=${DIR}`,
  files: [
    { name: "lettre.pdf", is_dir: false, size: 1536, modified: "2026-10-01T10:15:00", url: `${NC}/apps/files/files/102?dir=${DIR}` },
    { name: "CV", is_dir: true, size: null, modified: "2026-09-30T08:00:00", url: `${NC}/apps/files/files/101?dir=${DIR}/CV` },
  ],
};

interface Call {
  method: string;
  path: string;
}

type Handler = (call: Call) => Response | Promise<Response> | undefined;

function routeFetch(meta: Record<string, unknown>, handler: Handler = () => undefined) {
  const calls: Call[] = [];
  const fn = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input), "http://test");
    const call = { method: init?.method ?? "GET", path: url.pathname };
    calls.push(call);
    const custom = await handler(call);
    if (custom) return custom;
    if (url.pathname === "/api/meta") return jsonResponse(200, meta);
    if (url.pathname === "/api/offers" && call.method === "GET") return jsonResponse(200, OFFERS);
    return jsonResponse(404, { detail: "inconnu" });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const ENABLED = { ...META, nextcloud_enabled: true, nextcloud_error: null };
const folderCalls = (calls: Call[]) => calls.filter((c) => c.path.endsWith("/folder"));

function deferred() {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((r) => (resolve = r));
  return { promise, resolve };
}

async function openDetail(user: ReturnType<typeof userEvent.setup>, id: number) {
  render(
    <OfferFiltersProvider>
      <OffersView />
    </OfferFiltersProvider>,
  );
  await screen.findByText("2 offres");
  await user.click(screen.getByRole("button", { name: `Détail de l'offre n° ${id}` }));
  return screen.getByRole("dialog", { name: new RegExp(`N° ${id}`) });
}

beforeEach(() => {
  setToken("jeton-test");
});

describe("« Ouvrir le dossier » désactivé", () => {
  it("lien Nextcloud non activé : bouton désactivé avec infobulle, aucun appel /folder, pas de section", async () => {
    const user = userEvent.setup();
    const calls = routeFetch({ ...META, nextcloud_enabled: false, nextcloud_error: null });
    const panel = await openDetail(user, 1);
    const button = within(panel).getByRole("button", { name: "Ouvrir le dossier" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", NEXTCLOUD_NOTE);
    expect(button).toHaveAttribute("aria-description", NEXTCLOUD_NOTE);
    expect(within(panel).queryByRole("heading", { name: "Dossier Nextcloud" })).not.toBeInTheDocument();
    expect(folderCalls(calls)).toHaveLength(0);
  });

  it("réglage invalide : l'infobulle donne l'erreur de configuration", async () => {
    const user = userEvent.setup();
    routeFetch({ ...META, nextcloud_enabled: false, nextcloud_error: "NEXTCLOUD_URL manquant." });
    const panel = await openDetail(user, 1);
    const button = within(panel).getByRole("button", { name: "Ouvrir le dossier" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Nextcloud mal configuré : NEXTCLOUD_URL manquant.");
  });
});

describe("dossier trouvé", () => {
  it("chargement, puis lien dans un nouvel onglet et liste des fichiers (nom, taille, date, liens)", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    const calls = routeFetch(ENABLED, (c) => (c.path === "/api/offers/1/folder" ? pending.promise : undefined));
    const panel = await openDetail(user, 1);

    // Pendant la recherche : bouton désactivé, message de chargement annoncé.
    const waiting = within(panel).getByRole("button", { name: "Ouvrir le dossier" });
    expect(waiting).toBeDisabled();
    expect(waiting).toHaveAttribute("title", "Recherche du dossier dans Nextcloud…");
    expect(within(panel).getByRole("status")).toHaveTextContent("Chargement du dossier…");
    expect(within(panel).getByRole("region", { name: "Dossier Nextcloud" })).toHaveAttribute("aria-busy", "true");

    pending.resolve(jsonResponse(200, FOLDER));
    const open = await within(panel).findByRole("link", { name: "Ouvrir le dossier (nouvel onglet)" });
    // Un vrai lien ouvert par le navigateur : jamais bloqué comme fenêtre surgissante.
    expect(open).toHaveAttribute("href", FOLDER.url);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open.getAttribute("rel")).toContain("noopener");
    expect(folderCalls(calls)).toEqual([{ method: "GET", path: "/api/offers/1/folder" }]);

    expect(within(panel).getByRole("link", { name: FOLDER.name })).toHaveAttribute("href", FOLDER.url);
    const table = within(panel).getByRole("table", { name: `Contenu du dossier ${FOLDER.name}` });
    const rows = within(table).getAllByRole("row").slice(1);
    // Sous-dossiers d'abord.
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent)).toEqual(["CV/ (dossier)", "lettre.pdf"]);
    const [dir, file] = rows as [HTMLElement, HTMLElement];
    expect(within(dir).getByRole("link", { name: "CV/" })).toHaveAttribute("href", FOLDER.files[1]!.url);
    expect(within(dir).getByText("—")).toBeInTheDocument();
    expect(within(dir).getByText("30/09/2026 08:00")).toBeInTheDocument();
    const fileLink = within(file).getByRole("link", { name: "lettre.pdf" });
    expect(fileLink).toHaveAttribute("href", `${NC}/apps/files/files/102?dir=${DIR}&openfile=true`);
    expect(fileLink).toHaveAttribute("target", "_blank");
    expect(within(file).getByText("1,5 Ko")).toBeInTheDocument();
    expect(within(file).getByText("01/10/2026 10:15")).toBeInTheDocument();
    expect(within(panel).getByRole("region", { name: "Dossier Nextcloud" })).toHaveAttribute("aria-busy", "false");
  });

  it("dossier vide : message « Dossier vide. »", async () => {
    const user = userEvent.setup();
    routeFetch(ENABLED, (c) => (c.path === "/api/offers/1/folder" ? jsonResponse(200, { ...FOLDER, files: [] }) : undefined));
    const panel = await openDetail(user, 1);
    expect(await within(panel).findByText("Dossier vide.")).toBeInTheDocument();
    expect(within(panel).queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("dossier absent", () => {
  const missing = () =>
    jsonResponse(404, {
      detail: "Aucun dossier Nextcloud pour l'offre n° 1 dans « /Candidatures » (nom attendu commençant par son numéro, ex. « 01 - … »).",
      code: "folder_not_found",
    });

  it("« Créer le dossier » au clavier : POST, dossier affiché, lien actif, focus sur la section", async () => {
    const user = userEvent.setup();
    const calls = routeFetch(ENABLED, (c) => {
      if (c.path !== "/api/offers/1/folder") return undefined;
      return c.method === "POST"
        ? jsonResponse(201, { ...FOLDER, files: FOLDER.files.slice(1), created: true })
        : missing();
    });
    const panel = await openDetail(user, 1);
    expect(await within(panel).findByText("Aucun dossier Nextcloud pour cette offre.")).toBeInTheDocument();
    expect(within(panel).getByText(/nom attendu commençant par son numéro/)).toBeInTheDocument();
    const button = within(panel).getByRole("button", { name: "Ouvrir le dossier" });
    expect(button).toBeDisabled();
    expect(button.getAttribute("title")).toContain("Créer le dossier");
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Réessayer" })).not.toBeInTheDocument();

    const create = within(panel).getByRole("button", { name: "Créer le dossier" });
    create.focus();
    await user.keyboard("{Enter}");

    expect(await within(panel).findByText(`Dossier créé : ${FOLDER.name}`)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Ouvrir le dossier (nouvel onglet)" })).toHaveAttribute("href", FOLDER.url);
    expect(within(panel).getByRole("link", { name: "CV/" })).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Créer le dossier" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("heading", { name: "Dossier Nextcloud" })).toHaveFocus();
    expect(folderCalls(calls)).toEqual([
      { method: "GET", path: "/api/offers/1/folder" },
      { method: "POST", path: "/api/offers/1/folder" },
    ]);
  });

  it("création en cours : bouton désactivé « Création du dossier… » ; dossier existant (200) annoncé", async () => {
    const user = userEvent.setup();
    const pending = deferred();
    const calls = routeFetch(ENABLED, (c) => {
      if (c.path !== "/api/offers/1/folder") return undefined;
      return c.method === "POST" ? pending.promise : missing();
    });
    const panel = await openDetail(user, 1);
    await user.click(await within(panel).findByRole("button", { name: "Créer le dossier" }));
    // aria-disabled (pas disabled) : le bouton garde le focus ; un second clic n'envoie rien.
    const busy = within(panel).getByRole("button", { name: "Création du dossier…" });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).toHaveFocus();
    await user.click(busy);
    expect(folderCalls(calls).filter((c) => c.method === "POST")).toHaveLength(1);
    pending.resolve(jsonResponse(200, { ...FOLDER, created: false }));
    expect(await within(panel).findByText(`Dossier existant retrouvé : ${FOLDER.name}`)).toBeInTheDocument();
  });

  it("échec de la création (503) : message clair, le bouton reste disponible et garde le focus", async () => {
    const user = userEvent.setup();
    routeFetch(ENABLED, (c) => {
      if (c.path !== "/api/offers/1/folder") return undefined;
      return c.method === "POST"
        ? jsonResponse(503, { detail: "Nextcloud injoignable : délai dépassé.", code: "nextcloud_unreachable" })
        : missing();
    });
    const panel = await openDetail(user, 1);
    const create = await within(panel).findByRole("button", { name: "Créer le dossier" });
    create.focus();
    await user.keyboard("{Enter}");
    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent("Création impossible. Nextcloud ne répond pas. Nextcloud injoignable : délai dépassé.");
    const again = within(panel).getByRole("button", { name: "Créer le dossier" });
    expect(again).toBeEnabled();
    expect(again).not.toHaveAttribute("aria-disabled", "true");
    // Le focus ne part pas au titre de la section : le bouton reste sous le doigt pour réessayer.
    await waitFor(() => expect(again).toHaveFocus());
    expect(within(panel).getByRole("heading", { name: "Dossier Nextcloud" })).not.toHaveFocus();
  });
});

describe("erreurs du dossier", () => {
  it("409 dossier ambigu : alerte avec le détail ; « Réessayer » relance la recherche", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    const calls = routeFetch(ENABLED, (c) => {
      if (c.path !== "/api/offers/1/folder") return undefined;
      attempts += 1;
      return attempts === 1
        ? jsonResponse(409, { detail: "Plusieurs dossiers pour l'offre n° 1 : « 01 - A », « 1 - B ».", code: "folder_ambiguous" })
        : jsonResponse(200, FOLDER);
    });
    const panel = await openDetail(user, 1);
    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent("Plusieurs dossiers portent ce numéro : renommez-en un dans Nextcloud.");
    expect(alert).toHaveTextContent("« 01 - A », « 1 - B »");
    expect(within(panel).queryByRole("button", { name: "Créer le dossier" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Ouvrir le dossier" })).toBeDisabled();

    await user.click(within(panel).getByRole("button", { name: "Réessayer" }));
    expect(await within(panel).findByRole("link", { name: "Ouvrir le dossier (nouvel onglet)" })).toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
    expect(folderCalls(calls)).toHaveLength(2);
  });

  it.each([
    [503, "nextcloud_unreachable", "Nextcloud ne répond pas."],
    [503, "nextcloud_config", "Le lien Nextcloud n'est pas utilisable."],
    [502, "nextcloud_auth", "Nextcloud refuse l'accès."],
    [502, "nextcloud_root_missing", "Dossier racine introuvable dans Nextcloud."],
    [502, "nextcloud_error", "Dossier indisponible."],
  ])("%i %s : message clair avec le détail du service", async (status, code, title) => {
    const user = userEvent.setup();
    routeFetch(ENABLED, (c) =>
      c.path === "/api/offers/1/folder" ? jsonResponse(status, { detail: "Détail du service.", code }) : undefined,
    );
    const panel = await openDetail(user, 1);
    const alert = await within(panel).findByRole("alert");
    expect(alert).toHaveTextContent(`${title} Détail du service.`);
    expect(within(panel).queryByRole("button", { name: "Créer le dossier" })).not.toBeInTheDocument();
  });
});

describe("réponses tardives (useOfferFolder)", () => {
  const SECOND: Folder = { ...FOLDER, name: "02 - Chef de projet", url: `${NC}/apps/files/files/200?dir=/Candidatures/02` };
  const folderName = (state: FolderState) => (state.kind === "ready" ? state.folder.name : state.kind);

  it("changement d'offre : le dossier de l'offre précédente n'est jamais montré pour la nouvelle", async () => {
    const second = deferred();
    routeFetch(ENABLED, (c) => {
      if (c.path === "/api/offers/1/folder") return jsonResponse(200, FOLDER);
      if (c.path === "/api/offers/2/folder") return second.promise;
      return undefined;
    });
    const { result, rerender } = renderHook(({ id }) => useOfferFolder(id, true), { initialProps: { id: 1 } });
    await waitFor(() => expect(folderName(result.current.state)).toBe(FOLDER.name));
    rerender({ id: 2 });
    expect(result.current.state.kind).toBe("loading");
    second.resolve(jsonResponse(200, SECOND));
    await waitFor(() => expect(folderName(result.current.state)).toBe(SECOND.name));
  });

  it("GET : la réponse de l'offre 1 arrivée après le passage à l'offre 2 ne s'affiche pas", async () => {
    const late = deferred();
    routeFetch(ENABLED, (c) => {
      if (c.path === "/api/offers/1/folder") return late.promise;
      if (c.path === "/api/offers/2/folder") return jsonResponse(200, SECOND);
      return undefined;
    });
    const { result, rerender } = renderHook(({ id }) => useOfferFolder(id, true), { initialProps: { id: 1 } });
    expect(result.current.state.kind).toBe("loading");
    rerender({ id: 2 });
    await waitFor(() => expect(folderName(result.current.state)).toBe(SECOND.name));
    late.resolve(jsonResponse(200, FOLDER));
    await act(async () => {
      await late.promise;
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(folderName(result.current.state)).toBe(SECOND.name);
  });

  it("POST : la création lancée sur l'offre 1 et finie après le passage à l'offre 2 ne s'affiche pas", async () => {
    const late = deferred();
    const calls = routeFetch(ENABLED, (c) => {
      if (c.path === "/api/offers/1/folder") {
        return c.method === "POST" ? late.promise : jsonResponse(404, { detail: "absent", code: "folder_not_found" });
      }
      if (c.path === "/api/offers/2/folder") return jsonResponse(200, SECOND);
      return undefined;
    });
    const { result, rerender } = renderHook(({ id }) => useOfferFolder(id, true), { initialProps: { id: 1 } });
    await waitFor(() => expect(result.current.state.kind).toBe("error"));
    let created!: Promise<boolean>;
    act(() => {
      created = result.current.create();
    });
    expect(result.current.creating).toBe(true);
    rerender({ id: 2 });
    await waitFor(() => expect(folderName(result.current.state)).toBe(SECOND.name));
    expect(result.current.creating).toBe(false);
    late.resolve(jsonResponse(201, { ...FOLDER, created: true }));
    await act(async () => {
      expect(await created).toBe(true);
    });
    expect(folderName(result.current.state)).toBe(SECOND.name);
    expect(folderCalls(calls)).toContainEqual({ method: "POST", path: "/api/offers/1/folder" });
  });
});

describe("nouvelle offre", () => {
  it("le message de création dit ce qu'il en est du dossier Nextcloud", async () => {
    const user = userEvent.setup();
    routeFetch(ENABLED, (c) =>
      c.path === "/api/offers" && c.method === "POST"
        ? jsonResponse(201, {
            ...makeOffer({ id: 6, title: "Data" }),
            folder: { status: "error", detail: "Nextcloud injoignable : délai dépassé." },
          })
        : undefined,
    );
    render(
      <OfferFiltersProvider>
        <OffersView />
      </OfferFiltersProvider>,
    );
    await screen.findByText("2 offres");
    await user.click(screen.getByRole("button", { name: "Nouvelle offre" }));
    const dialog = screen.getByRole("dialog", { name: "Nouvelle offre" });
    await user.type(within(dialog).getByLabelText("Intitulé"), "Data");
    await user.click(within(dialog).getByRole("button", { name: "Créer l'offre" }));
    expect(
      await screen.findByText("Offre n° 6 créée. Dossier Nextcloud non créé : Nextcloud injoignable : délai dépassé."),
    ).toBeInTheDocument();
  });
});
