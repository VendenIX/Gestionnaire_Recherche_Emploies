import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import type { FolderEntry } from "../api/types";
import {
  entryLink,
  folderOutcomeNote,
  folderProblem,
  formatModified,
  formatSize,
  NEXTCLOUD_NOTE,
  nextcloudDisabledNote,
  nextcloudEnabled,
  sortEntries,
} from "./folder";
import { META } from "../test/fetchMock";

const entry = (over: Partial<FolderEntry>): FolderEntry => ({
  name: "a.pdf",
  is_dir: false,
  size: 10,
  modified: null,
  url: "http://nc/apps/files/files/7?dir=/Candidatures/01%20-%20X",
  ...over,
});

describe("activation", () => {
  it("actif seulement si meta.nextcloud_enabled vaut true", () => {
    expect(nextcloudEnabled(null)).toBe(false);
    expect(nextcloudEnabled({ ...META, statuses: [] })).toBe(false);
    expect(nextcloudEnabled({ ...META, statuses: [], nextcloud_enabled: true })).toBe(true);
  });

  it("infobulle : erreur de configuration si elle existe, sinon la note par défaut", () => {
    expect(nextcloudDisabledNote(null)).toBe(NEXTCLOUD_NOTE);
    expect(nextcloudDisabledNote({ ...META, statuses: [], nextcloud_error: "X manquant." })).toBe(
      "Nextcloud mal configuré : X manquant.",
    );
  });
});

describe("formatSize", () => {
  it.each([
    [null, ""],
    [0, "0 o"],
    [1023, "1023 o"],
    [1536, "1,5 Ko"],
    [10 * 1024, "10 Ko"],
    [5 * 1024 * 1024, "5 Mo"],
    [1.25 * 1024 ** 3, "1,3 Go"],
  ])("%s -> %s", (size, text) => {
    expect(formatSize(size)).toBe(text);
  });
});

describe("formatModified", () => {
  it("UTC -> heure locale du fuseau", () => {
    expect(formatModified("2026-10-01T10:15:00Z", "Europe/Paris")).toBe("01/10/2026 12:15");
    expect(formatModified("2026-12-01T00:05:00+00:00", "Europe/Paris")).toBe("01/12/2026 01:05");
  });

  it("vide ou illisible", () => {
    expect(formatModified(null)).toBe("");
    expect(formatModified("hier")).toBe("hier");
  });
});

describe("entryLink", () => {
  it("fichier : openfile=true ajouté pour l'ouvrir dans Nextcloud", () => {
    expect(entryLink(entry({}))).toBe("http://nc/apps/files/files/7?dir=/Candidatures/01%20-%20X&openfile=true");
  });

  it("dossier, lien sans identifiant ou déjà ouvrant : inchangé", () => {
    const dir = entry({ is_dir: true });
    expect(entryLink(dir)).toBe(dir.url);
    const noId = entry({ url: "http://nc/apps/files/files?dir=/Candidatures/01" });
    expect(entryLink(noId)).toBe(noId.url);
    const already = entry({ url: "http://nc/apps/files/files/7?dir=/x&openfile=true" });
    expect(entryLink(already)).toBe(already.url);
    expect(entryLink(entry({ url: "pas une url" }))).toBe("pas une url");
  });
});

it("sortEntries : sous-dossiers d'abord, puis ordre alphabétique sans casse", () => {
  const sorted = sortEntries([
    entry({ name: "b.pdf" }),
    entry({ name: "Lettre", is_dir: true }),
    entry({ name: "A.pdf" }),
    entry({ name: "CV", is_dir: true }),
  ]);
  expect(sorted.map((e) => e.name)).toEqual(["CV", "Lettre", "A.pdf", "b.pdf"]);
});

describe("folderProblem", () => {
  it("dossier absent : création proposée, pas de nouvelle tentative", () => {
    const p = folderProblem(new ApiError(404, "Aucun dossier.", [], "folder_not_found"));
    expect(p).toEqual({ title: "Aucun dossier Nextcloud pour cette offre.", detail: "Aucun dossier.", missing: true, retry: false });
  });

  it("409 et 503 sans code connu : messages génériques avec nouvelle tentative", () => {
    expect(folderProblem(new ApiError(409, "x")).title).toBe("Dossier ambigu.");
    expect(folderProblem(new ApiError(503, "x")).title).toBe("Nextcloud indisponible.");
    expect(folderProblem(new ApiError(0, "Service injoignable.")).retry).toBe(true);
    expect(folderProblem(new Error("boum")).detail).toBe("boum");
  });
});

describe("folderOutcomeNote", () => {
  it("issue de la création du dossier avec l'offre", () => {
    expect(folderOutcomeNote(undefined)).toBe("");
    expect(folderOutcomeNote({ status: "disabled" })).toBe("");
    expect(folderOutcomeNote({ status: "created", name: "06 - Data" })).toBe(" Dossier Nextcloud créé : 06 - Data.");
    expect(folderOutcomeNote({ status: "existing", name: "06 - Data" })).toBe(" Dossier Nextcloud existant : 06 - Data.");
    expect(folderOutcomeNote({ status: "error", detail: "Délai dépassé." })).toBe(" Dossier Nextcloud non créé : Délai dépassé.");
    expect(folderOutcomeNote({ status: "error" })).toBe(" Dossier Nextcloud non créé.");
    expect(folderOutcomeNote({ status: "error", detail: "Délai dépassé" })).toBe(" Dossier Nextcloud non créé : Délai dépassé.");
  });
});
