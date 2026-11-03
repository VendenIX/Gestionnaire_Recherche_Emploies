// Dossier Nextcloud d'une offre (phase 3, D-003) : règles pures d'affichage, sans React.
// Taille lisible, date de modification en heure locale, lien d'un fichier, et lecture des erreurs
// {detail, code} des routes /api/offers/{id}/folder.

import { ApiError } from "../api/client";
import type { FolderEntry, FolderOutcome, Meta } from "../api/types";

/** Infobulle du bouton « Ouvrir le dossier » quand le lien Nextcloud n'est pas activé. */
export const NEXTCLOUD_NOTE = "Lien Nextcloud non activé (NEXTCLOUD_WEBDAV)";

/** Le lien Nextcloud est utilisable d'après /api/meta (absent ou meta pas encore lue : non). */
export function nextcloudEnabled(meta: Meta | null | undefined): boolean {
  return meta?.nextcloud_enabled === true;
}

/** Raison affichée sur le bouton désactivé : réglage invalide, ou lien non activé. */
export function nextcloudDisabledNote(meta: Meta | null | undefined): string {
  const error = meta?.nextcloud_error;
  return error ? `Nextcloud mal configuré : ${error}` : NEXTCLOUD_NOTE;
}

/** 1536 -> « 1,5 Ko » ; null (dossier ou taille inconnue) -> « ». Unités en base 1024. */
export function formatSize(size: number | null): string {
  if (size === null || !Number.isFinite(size) || size < 0) return "";
  if (size < 1024) return `${size} o`;
  const units = ["Ko", "Mo", "Go", "To"];
  let value = size / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  const text = value >= 10 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, "");
  return `${text.replace(".", ",")} ${units[i]}`;
}

/**
 * Date de modification (ISO, en UTC dans les réponses du service) -> « JJ/MM/AAAA HH:MM » à l'heure
 * locale du navigateur (ou du fuseau donné, pour les tests). Valeur illisible : rendue telle quelle.
 */
export function formatModified(value: string | null, timeZone?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("day")}/${get("month")}/${get("year")} ${get("hour")}:${get("minute")}`;
}

/**
 * Lien d'un élément du dossier. Le service donne, pour un fichier, le lien de son dossier avec
 * l'identifiant du fichier (/apps/files/files/<id>?dir=…), qui le sélectionne seulement sur un
 * Nextcloud récent : on ajoute openfile=true pour l'ouvrir (ignoré par les versions qui ne le
 * connaissent pas). Un sous-dossier garde son lien.
 */
export function entryLink(entry: FolderEntry): string {
  if (entry.is_dir) return entry.url;
  let url: URL;
  try {
    url = new URL(entry.url);
  } catch {
    return entry.url;
  }
  if (!/\/apps\/files\/files\/[^/]+$/.test(url.pathname) || url.searchParams.has("openfile")) return entry.url;
  // Ajout en fin de chaîne : le paramètre dir garde l'encodage choisi par le service.
  const [base, hash = ""] = entry.url.split("#", 2) as [string, string?];
  return `${base}${url.search ? "&" : "?"}openfile=true${hash ? `#${hash}` : ""}`;
}

/** Tri d'affichage : sous-dossiers d'abord, puis noms dans l'ordre alphabétique français. */
export function sortEntries(entries: FolderEntry[]): FolderEntry[] {
  return [...entries].sort(
    (a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name, "fr", { sensitivity: "base" }),
  );
}

export interface FolderProblem {
  /** Phrase courte en tête du message. */
  title: string;
  /** Détail en français renvoyé par le service (ou message réseau). */
  detail: string;
  /** Le dossier n'existe pas : proposer « Créer le dossier ». */
  missing: boolean;
  /** Une nouvelle tentative a un sens (Nextcloud injoignable, erreur passagère, ambiguïté réglée). */
  retry: boolean;
}

/** Erreur d'une route /folder -> message affiché dans le panneau. */
export function folderProblem(error: unknown): FolderProblem {
  if (!(error instanceof ApiError)) {
    const detail = error instanceof Error ? error.message : "Erreur inattendue.";
    return { title: "Dossier indisponible.", detail, missing: false, retry: true };
  }
  const detail = error.message;
  switch (error.code) {
    case "folder_not_found":
      return { title: "Aucun dossier Nextcloud pour cette offre.", detail, missing: true, retry: false };
    case "folder_ambiguous":
      return {
        title: "Plusieurs dossiers portent ce numéro : renommez-en un dans Nextcloud.",
        detail,
        missing: false,
        retry: true,
      };
    case "nextcloud_disabled":
    case "nextcloud_config":
      return { title: "Le lien Nextcloud n'est pas utilisable.", detail, missing: false, retry: false };
    case "nextcloud_unreachable":
      return { title: "Nextcloud ne répond pas.", detail, missing: false, retry: true };
    case "nextcloud_auth":
      return { title: "Nextcloud refuse l'accès.", detail, missing: false, retry: true };
    case "nextcloud_root_missing":
      return { title: "Dossier racine introuvable dans Nextcloud.", detail, missing: false, retry: true };
    case "offer_not_found":
      return { title: "Offre introuvable.", detail, missing: false, retry: false };
    default:
      break;
  }
  if (error.status === 409) return { title: "Dossier ambigu.", detail, missing: false, retry: true };
  if (error.status === 503) return { title: "Nextcloud indisponible.", detail, missing: false, retry: true };
  return { title: "Dossier indisponible.", detail, missing: false, retry: true };
}

/** Suite du message « Offre n° N créée. » selon l'issue de la création de son dossier. */
export function folderOutcomeNote(outcome: FolderOutcome | undefined): string {
  if (!outcome) return "";
  switch (outcome.status) {
    case "created":
      return ` Dossier Nextcloud créé : ${outcome.name ?? ""}.`.replace(" : .", ".");
    case "existing":
      return ` Dossier Nextcloud existant : ${outcome.name ?? ""}.`.replace(" : .", ".");
    case "error":
      if (!outcome.detail) return " Dossier Nextcloud non créé.";
      return ` Dossier Nextcloud non créé : ${outcome.detail}${/[.!?…]$/.test(outcome.detail) ? "" : "."}`;
    default:
      return "";
  }
}
