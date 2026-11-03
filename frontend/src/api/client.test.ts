import { describe, expect, it } from "vitest";
import { headerOf, jsonResponse, META, mockFetch } from "../test/fetchMock";
import { ApiError, checkToken, createFolder, deleteOffer, getFolder, getMeta, listOffers, updateOffer } from "./client";
import { getLastClearReason, getToken, setToken, TOKEN_STORAGE_KEY } from "./token";

describe("client API", () => {
  it("ajoute l'en-tête Authorization: Bearer avec le jeton mémorisé", async () => {
    setToken("secret-123");
    const fetchFn = mockFetch(jsonResponse(200, META));
    const meta = await getMeta();
    expect(meta.departments[0]?.code).toBe("2A");
    expect(fetchFn.mock.calls[0]?.[0]).toBe("/api/meta");
    expect(headerOf(fetchFn, 0, "Authorization")).toBe("Bearer secret-123");
  });

  it("n'envoie pas d'en-tête Authorization sans jeton", async () => {
    const fetchFn = mockFetch(jsonResponse(200, META));
    await getMeta();
    expect(headerOf(fetchFn, 0, "Authorization")).toBeNull();
  });

  it("construit la requête des filtres et garde les codes de département en texte", async () => {
    setToken("t");
    const fetchFn = mockFetch(jsonResponse(200, []));
    await listOffers({ status: "À qualifier", department: "01", q: "", region: undefined });
    const url = String(fetchFn.mock.calls[0]?.[0]);
    expect(url.startsWith("/api/offers?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("status")).toBe("À qualifier");
    expect(params.get("department")).toBe("01");
    expect(params.has("q")).toBe(false);
    expect(params.has("region")).toBe(false);
  });

  it("envoie un corps JSON pour PATCH", async () => {
    setToken("t");
    const fetchFn = mockFetch(jsonResponse(200, { id: 3 }));
    await updateOffer(3, { status: "Rejeté", department: "2A" });
    const init = fetchFn.mock.calls[0]?.[1];
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ status: "Rejeté", department: "2A" });
    expect(headerOf(fetchFn, 0, "Content-Type")).toBe("application/json");
  });

  it("accepte une réponse 204 vide", async () => {
    setToken("t");
    mockFetch(new Response(null, { status: 204 }));
    await expect(deleteOffer(4)).resolves.toBeUndefined();
  });

  it("lève une ApiError avec le message français du backend (detail) et les erreurs par champ", async () => {
    setToken("t");
    mockFetch(
      jsonResponse(422, {
        detail: "Données invalides : department : département inconnu",
        errors: [{ field: "department", message: "département inconnu" }],
      }),
    );
    const error = await updateOffer(1, { department: "20" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.message).toBe("Données invalides : department : département inconnu");
    expect(apiError.errors).toEqual([{ field: "department", message: "département inconnu" }]);
    expect(getToken()).toBe("t");
  });

  it("dossier : GET puis POST /api/offers/{id}/folder ; le code stable de l'erreur est lu", async () => {
    setToken("t");
    const folder = { name: "01 - X", url: "http://nc/apps/files/files/1?dir=/d/01%20-%20X", files: [], created: true };
    const fn = mockFetch(
      jsonResponse(404, { detail: "Aucun dossier.", code: "folder_not_found" }),
      jsonResponse(201, folder),
    );
    const error = await getFolder(1).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("folder_not_found");
    expect((error as ApiError).message).toBe("Aucun dossier.");
    await expect(createFolder(1)).resolves.toEqual(folder);
    expect(fn.mock.calls[0]?.[0]).toBe("/api/offers/1/folder");
    expect(fn.mock.calls[1]?.[0]).toBe("/api/offers/1/folder");
    expect(fn.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(new ApiError(500, "x").code).toBeNull();
  });

  it("garde le message 404 du backend", async () => {
    setToken("t");
    mockFetch(jsonResponse(404, { detail: "Offre introuvable : 99" }));
    await expect(updateOffer(99, {})).rejects.toThrow("Offre introuvable : 99");
  });

  it("donne un message de repli si le corps n'est pas du JSON", async () => {
    setToken("t");
    mockFetch(new Response("Internal Server Error", { status: 500 }));
    await expect(getMeta()).rejects.toThrow("Erreur du serveur (HTTP 500).");
  });

  it("signale un service injoignable (status 0)", async () => {
    setToken("t");
    const fn = mockFetch();
    fn.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const error = (await getMeta().catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/injoignable/);
  });

  it("un 401 efface le jeton mémorisé de localStorage", async () => {
    setToken("ancien");
    mockFetch(jsonResponse(401, { detail: "Authentification requise : en-tête absent ou invalide." }));
    const error = (await getMeta().catch((e: unknown) => e)) as ApiError;
    expect(error.isUnauthorized).toBe(true);
    expect(error.message).toBe("Authentification requise : en-tête absent ou invalide.");
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(getToken()).toBeNull();
    expect(getLastClearReason()).toBe("unauthorized");
  });

  it("aucune raison d'effacement ne fuit d'un test à l'autre (test suivant le 401)", () => {
    expect(getLastClearReason()).toBeNull();
  });

  it("un 401 tardif d'une requête partie avec un ancien jeton n'efface pas le nouveau jeton", async () => {
    setToken("ancien");
    let answer: (r: Response) => void = () => {};
    const fn = mockFetch();
    fn.mockReturnValueOnce(new Promise<Response>((resolve) => (answer = resolve)));
    const pending = getMeta().catch((e: unknown) => e);
    setToken("nouveau");
    answer(jsonResponse(401, { detail: "refusé" }));
    const error = (await pending) as ApiError;
    expect(error.isUnauthorized).toBe(true);
    expect(headerOf(fn, 0, "Authorization")).toBe("Bearer ancien");
    expect(getToken()).toBe("nouveau");
    expect(getLastClearReason()).toBeNull();
  });

  it("checkToken utilise le jeton fourni et ne touche pas au jeton mémorisé sur 401", async () => {
    setToken("garde");
    const fetchFn = mockFetch(jsonResponse(401, { detail: "refusé" }));
    await expect(checkToken("candidat")).rejects.toBeInstanceOf(ApiError);
    expect(headerOf(fetchFn, 0, "Authorization")).toBe("Bearer candidat");
    expect(getToken()).toBe("garde");
  });
});
