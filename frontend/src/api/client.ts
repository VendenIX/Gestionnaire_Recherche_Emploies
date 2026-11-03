// Client HTTP typé de l'API (/api). Ajoute l'en-tête Authorization: Bearer, lit les messages
// d'erreur français du backend (champ "detail") et lève une ApiError typée.
// Un 401 efface le jeton mémorisé : l'application revient à l'écran du jeton.

import { clearToken, getToken } from "./token";
import type {
  FieldError,
  Folder,
  Meta,
  Offer,
  OfferCreate,
  OfferCreated,
  OfferFilters,
  OfferUpdate,
  Stats,
} from "./types";

export const API_BASE = "/api";

export class ApiError extends Error {
  /** Code HTTP ; 0 si le service est injoignable. */
  readonly status: number;
  /** Erreurs par champ (réponses 422). */
  readonly errors: FieldError[];
  /** Code stable de l'erreur (champ "code" du corps, ex. "folder_not_found"), sinon null. */
  readonly code: string | null;

  constructor(status: number, message: string, errors: FieldError[] = [], code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errors = errors;
    this.code = code;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | undefined>;
  /** Jeton à utiliser à la place du jeton mémorisé (vérification à la connexion). */
  token?: string;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, string | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") params.set(key, value);
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseFieldErrors(value: unknown): FieldError[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(isRecord)
    .map((item) => ({ field: String(item.field ?? ""), message: String(item.message ?? item.msg ?? "") }));
}

/** Message lisible tiré du corps d'erreur du backend ({"detail": "..."}). */
export function errorMessage(status: number, body: unknown): string {
  if (isRecord(body)) {
    const detail = body.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => (isRecord(d) && typeof d.msg === "string" ? d.msg : typeof d === "string" ? d : ""))
        .filter(Boolean);
      if (parts.length) return parts.join(" ; ");
    }
  }
  if (status === 401) return "Jeton d'accès absent ou invalide.";
  if (status === 404) return "Ressource introuvable.";
  if (status >= 500) return `Erreur du serveur (HTTP ${status}).`;
  return `Erreur HTTP ${status}.`;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/** Envoie une requête à l'API ; renvoie la réponse brute si elle est réussie. */
export async function apiRequest(path: string, options: RequestOptions = {}): Promise<Response> {
  const token = options.token ?? getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Service injoignable : vérifiez que le serveur est démarré et le réseau.");
  }

  if (!response.ok) {
    const payload = await readBody(response);
    if (response.status === 401 && options.token === undefined && token !== null && getToken() === token) {
      // Jeton mémorisé refusé (changé côté serveur) : on l'oublie, l'écran du jeton réapparaît.
      // Seulement s'il est encore celui de la requête : un 401 tardif ne doit pas effacer un jeton
      // saisi entre-temps.
      clearToken("unauthorized");
    }
    const errors = isRecord(payload) ? parseFieldErrors(payload.errors) : [];
    const code = isRecord(payload) && typeof payload.code === "string" ? payload.code : null;
    throw new ApiError(response.status, errorMessage(response.status, payload), errors, code);
  }
  return response;
}

/** Requête JSON typée. */
export async function apiJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await apiRequest(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// --- Points d'accès ------------------------------------------------------------------------

export function getMeta(options: Pick<RequestOptions, "token" | "signal"> = {}): Promise<Meta> {
  return apiJson<Meta>("/meta", options);
}

/** Vérifie un jeton par un appel authentifié, sans le mémoriser. */
export async function checkToken(token: string): Promise<Meta> {
  return getMeta({ token });
}

export function getStats(signal?: AbortSignal): Promise<Stats> {
  return apiJson<Stats>("/stats", { signal });
}

export function listOffers(filters: OfferFilters = {}, signal?: AbortSignal): Promise<Offer[]> {
  return apiJson<Offer[]>("/offers", { query: { ...filters }, signal });
}

export function getOffer(id: number, signal?: AbortSignal): Promise<Offer> {
  return apiJson<Offer>(`/offers/${id}`, { signal });
}

export function createOffer(offer: OfferCreate): Promise<OfferCreated> {
  return apiJson<OfferCreated>("/offers", { method: "POST", body: offer });
}

export function updateOffer(id: number, changes: OfferUpdate): Promise<Offer> {
  return apiJson<Offer>(`/offers/${id}`, { method: "PATCH", body: changes });
}

export async function deleteOffer(id: number): Promise<void> {
  await apiRequest(`/offers/${id}`, { method: "DELETE" });
}

/** Dossier Nextcloud de l'offre et son contenu ; 404 code folder_not_found s'il n'existe pas. */
export function getFolder(id: number, signal?: AbortSignal): Promise<Folder> {
  return apiJson<Folder>(`/offers/${id}/folder`, { signal });
}

/** Crée le dossier de l'offre (201) ou renvoie celui qui existe déjà (200, created: false). */
export function createFolder(id: number): Promise<Folder> {
  return apiJson<Folder>(`/offers/${id}/folder`, { method: "POST" });
}

/** Classeur Excel v2 (GET /api/export.xlsx) ; authentifié, donc téléchargé en Blob. */
export async function exportXlsx(): Promise<Blob> {
  const response = await apiRequest("/export.xlsx");
  return response.blob();
}
