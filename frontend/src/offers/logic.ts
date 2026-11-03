// Règles calculées côté client, jamais stockées : jours restants, urgence de la date
// limite, relance suggérée, tri. Mêmes règles que le classeur Excel v2 exporté.

import { STATUSES, type Meta, type Offer, type Status } from "../api/types";

const DAY_MS = 86_400_000;

/** Délai de relance par défaut si /api/meta ne le donne pas. */
export const DEFAULT_FOLLOW_UP_DAYS = 14;

/** Date AAAA-MM-JJ -> minuit UTC (ms), ou null si illisible. */
function isoDateToUtc(value: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return null;
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function todayUtc(now: Date): number {
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
}

function wallClock(d: Date): number {
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds());
}

/** Offre « morte » : Rejeté ou Abandon (ligne estompée, pas de jours restants). */
export function isClosed(status: Status | null): boolean {
  return status === "Rejeté" || status === "Abandon";
}

/**
 * Jours restants avant la date limite (négatif si dépassée). Comme la formule de l'Excel v2 :
 * vide sans date limite ou si l'offre est Rejetée / Abandonnée.
 */
export function daysLeft(offer: Pick<Offer, "deadline" | "status">, now: Date = new Date()): number | null {
  if (!offer.deadline || isClosed(offer.status)) return null;
  const deadline = isoDateToUtc(offer.deadline);
  if (deadline === null) return null;
  return Math.round((deadline - todayUtc(now)) / DAY_MS);
}

/** Libellé des jours restants, format de nombre de l'Excel v2. */
export function daysLeftLabel(days: number | null): string {
  if (days === null) return "";
  if (days === 0) return "Aujourd'hui";
  return days > 0 ? `dans ${days} j` : `il y a ${-days} j`;
}

export type Urgency = "today" | "soon" | "week" | "later" | "overdue-apply" | "overdue";

/**
 * Urgence (mise en forme conditionnelle n° 2 de l'Excel v2) : 0 -> aujourd'hui (rouge),
 * 1-3 j (orange), 4-7 j (jaune), > 7 j (vert), dépassée et « À postuler » (rouge pâle),
 * dépassée sinon (gris italique).
 */
export function urgency(days: number | null, status: Status | null): Urgency | null {
  if (days === null) return null;
  if (days === 0) return "today";
  if (days >= 1 && days <= 3) return "soon";
  if (days >= 4 && days <= 7) return "week";
  if (days > 7) return "later";
  return status === "À postuler" ? "overdue-apply" : "overdue";
}

/** Classe CSS d'urgence (couleurs dans styles/offers.css et styles.css). */
export function urgencyClass(level: Urgency | null): string {
  return level ? `deadline-${level}` : "";
}

/**
 * Relance suggérée : statut « En cours », relance non faite, candidature datée de plus de
 * `followUpDays` jours (même règle que le backend, repository.follow_up_suggested).
 */
export function followUpSuggested(
  offer: Pick<Offer, "status" | "follow_up_done" | "applied_at">,
  followUpDays: number,
  now: Date = new Date(),
): boolean {
  if (offer.status !== "En cours" || offer.follow_up_done || !offer.applied_at) return false;
  const applied = Date.parse(offer.applied_at);
  if (Number.isNaN(applied)) return false;
  // Écart en heure locale « murale », comme le backend (datetime naïves) : un changement d'heure
  // ne décale pas la relance d'une heure.
  return wallClock(now) - wallClock(new Date(applied)) > followUpDays * DAY_MS;
}

/** AAAA-MM-JJ -> JJ/MM/AAAA. */
export function formatDate(value: string | null): string {
  if (!value) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value;
}

// --- Tri ------------------------------------------------------------------------------------

export type SortKey =
  | "status"
  | "deadline"
  | "days"
  | "id"
  | "title"
  | "company"
  | "contract_type"
  | "location"
  | "department";

export type SortDir = "asc" | "desc";

export interface SortState {
  key: SortKey;
  dir: SortDir;
}

export interface SortContext {
  /** Codes de département dans l'ordre du référentiel (jamais un tri numérique, D-001). */
  departmentOrder: readonly string[];
  now?: Date;
}

const collator = new Intl.Collator("fr", { sensitivity: "base", numeric: true });

/** Rang d'un département dans le référentiel : 19 < 2A < 2B < 21. Inconnu après les connus. */
export function departmentRanker(order: readonly string[]): (code: string | null) => number | null {
  const index = new Map(order.map((code, i) => [code, i]));
  return (code) => (code === null ? null : (index.get(code) ?? order.length));
}

export function departmentOrderOf(meta: Pick<Meta, "departments"> | null): string[] {
  return meta ? meta.departments.map((d) => d.code) : [];
}

type SortValue = number | string | null;

function sortValue(offer: Offer, key: SortKey, rank: (code: string | null) => number | null, now: Date): SortValue {
  switch (key) {
    case "status":
      return offer.status === null ? STATUSES.length : STATUSES.indexOf(offer.status);
    case "deadline":
      return offer.deadline;
    case "days":
      return daysLeft(offer, now);
    case "id":
      return offer.id;
    case "department":
      return rank(offer.department);
    default:
      return offer[key] && offer[key].trim() ? offer[key] : null;
  }
}

/** Trie sans modifier la liste ; les valeurs vides restent en fin de liste dans les deux sens. */
export function sortOffers(offers: readonly Offer[], sort: SortState, context: SortContext): Offer[] {
  const rank = departmentRanker(context.departmentOrder);
  const now = context.now ?? new Date();
  const sign = sort.dir === "asc" ? 1 : -1;
  const keyed = offers.map((offer) => ({ offer, value: sortValue(offer, sort.key, rank, now) }));
  keyed.sort((a, b) => {
    if (a.value === null || b.value === null) {
      if (a.value === b.value) return a.offer.id - b.offer.id;
      return a.value === null ? 1 : -1;
    }
    const cmp =
      typeof a.value === "number" && typeof b.value === "number"
        ? a.value - b.value
        : collator.compare(String(a.value), String(b.value));
    return cmp !== 0 ? sign * cmp : a.offer.id - b.offer.id;
  });
  return keyed.map((k) => k.offer);
}
