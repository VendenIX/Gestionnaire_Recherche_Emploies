// Logique pure du formulaire d'offre (création et modification) : valeurs du formulaire,
// validation en français, corps des requêtes et erreurs du service par champ.
//
// Règles : un champ non renseigné reste vide (null), aucune valeur par défaut sauf le pays (FR) ;
// un code de département reste une chaîne (« 01 », « 2A ») et n'est envoyé qu'en France (D-001) ;
// en modification, seuls les champs changés par l'utilisateur sont envoyés (un champ modifié en
// ligne et encore en vol n'est donc jamais écrasé par une ancienne valeur du formulaire).

import { ApiError } from "../api/client";
import { isStatus, type Offer, type OfferCreate, type Status } from "../api/types";
import { departmentByCode } from "../geo";

export const FRANCE = "FR";

/** Suggestions du type de contrat (saisie libre permise). */
export const CONTRACT_SUGGESTIONS = ["CDI", "CDD", "Stage", "Alternance", "Freelance", "Intérim", "VIE"] as const;

export const TEXT_FIELDS = [
  "title",
  "contract_type",
  "company",
  "location",
  "main_issue",
  "rejection_reason",
  "source",
  "deadline_note",
  "description",
] as const;
export type TextField = (typeof TEXT_FIELDS)[number];

export interface FormValues extends Record<TextField, string> {
  department: string;
  country: string;
  /** "" = sans statut (« À qualifier »). */
  status: Status | "";
  /** Date AAAA-MM-JJ ou "". */
  applied_at: string;
  deadline: string;
  application_sent: boolean;
  follow_up_done: boolean;
  links: string[];
}

export type FormField = keyof FormValues;
/** Messages d'erreur par champ ; « form » : erreur générale. */
export type FormErrors = Partial<Record<FormField | "form", string>>;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const COUNTRY_RE = /^[A-Z]{2}$/;

export function emptyForm(): FormValues {
  const text = Object.fromEntries(TEXT_FIELDS.map((f) => [f, ""])) as Record<TextField, string>;
  return {
    ...text,
    department: "",
    country: FRANCE,
    status: "",
    applied_at: "",
    deadline: "",
    application_sent: false,
    follow_up_done: false,
    links: [],
  };
}

/**
 * Valeurs du formulaire de modification. Un département absent du référentiel (impossible : le
 * service les valide) donnerait "" : il n'est alors ni affiché ni renvoyé, et ne bloque pas
 * l'enregistrement des autres champs.
 */
export function formFromOffer(offer: Offer): FormValues {
  const text = Object.fromEntries(TEXT_FIELDS.map((f) => [f, offer[f] ?? ""])) as Record<TextField, string>;
  const department = offer.department && departmentByCode(offer.department) ? offer.department : "";
  return {
    ...text,
    department,
    country: offer.country || FRANCE,
    status: offer.status ?? "",
    applied_at: offer.applied_at ? offer.applied_at.slice(0, 10) : "",
    deadline: offer.deadline ?? "",
    application_sent: offer.application_sent,
    follow_up_done: offer.follow_up_done,
    links: [...offer.links],
  };
}

export function normalizeCountry(value: string): string {
  const code = value.trim().toUpperCase();
  return code === "" ? FRANCE : code;
}

export function isFrance(values: Pick<FormValues, "country">): boolean {
  return normalizeCountry(values.country) === FRANCE;
}

function cleanText(value: string): string | null {
  const text = value.trim();
  return text === "" ? null : text;
}

function cleanLinks(links: string[]): string[] {
  return links.map((l) => l.trim()).filter((l) => l !== "");
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.host !== "" && !/\s/.test(value);
  } catch {
    return false;
  }
}

export function validateForm(values: FormValues): FormErrors {
  const errors: FormErrors = {};
  const country = normalizeCountry(values.country);
  if (!COUNTRY_RE.test(country)) {
    errors.country = "Le pays doit être un code ISO de 2 lettres (ex. FR, CH, BE).";
  }
  if (country === FRANCE && values.department !== "" && !departmentByCode(values.department)) {
    errors.department = `Département inconnu : « ${values.department} ».`;
  }
  for (const field of ["deadline", "applied_at"] as const) {
    if (values[field] !== "" && !DATE_RE.test(values[field])) {
      errors[field] = "Date invalide (format AAAA-MM-JJ attendu).";
    }
  }
  const bad = cleanLinks(values.links).findIndex((l) => !isHttpUrl(l));
  if (bad >= 0) {
    errors.links = `Lien n° ${bad + 1} invalide : une adresse http:// ou https:// est attendue.`;
  }
  return errors;
}

function departmentOf(values: FormValues): string | null {
  return isFrance(values) && values.department !== "" ? values.department : null;
}

function appliedAtOf(date: string): string | null {
  return date === "" ? null : `${date}T00:00:00`;
}

/** Corps de POST /api/offers : uniquement les champs renseignés, plus le pays. */
export function toCreatePayload(values: FormValues): OfferCreate {
  const payload: OfferCreate = {};
  for (const field of TEXT_FIELDS) {
    const text = cleanText(values[field]);
    if (text !== null) payload[field] = text;
  }
  payload.country = normalizeCountry(values.country);
  const department = departmentOf(values);
  if (department !== null) payload.department = department;
  if (isStatus(values.status)) payload.status = values.status;
  if (values.applied_at !== "") payload.applied_at = appliedAtOf(values.applied_at);
  if (values.deadline !== "") payload.deadline = values.deadline;
  if (values.application_sent) payload.application_sent = true;
  if (values.follow_up_done) payload.follow_up_done = true;
  const links = cleanLinks(values.links);
  if (links.length > 0) payload.links = links;
  return payload;
}

/**
 * Champs changés par rapport aux valeurs initiales du formulaire (celles de l'offre affichée à
 * son ouverture) ; seuls ceux-là partent dans le PATCH.
 */
export function toChanges(values: FormValues, initial: FormValues): Partial<Offer> {
  const changes: Partial<Offer> = {};
  for (const field of TEXT_FIELDS) {
    const next = cleanText(values[field]);
    if (next !== cleanText(initial[field])) changes[field] = next;
  }
  const country = normalizeCountry(values.country);
  if (country !== normalizeCountry(initial.country)) changes.country = country;
  const department = departmentOf(values);
  if (department !== departmentOf(initial)) changes.department = department;
  if (values.status !== initial.status) changes.status = isStatus(values.status) ? values.status : null;
  // Date de candidature : inchangée, l'heure d'origine est conservée (rien n'est envoyé).
  if (values.applied_at !== initial.applied_at) changes.applied_at = appliedAtOf(values.applied_at);
  if (values.deadline !== initial.deadline) changes.deadline = values.deadline === "" ? null : values.deadline;
  if (values.application_sent !== initial.application_sent) changes.application_sent = values.application_sent;
  if (values.follow_up_done !== initial.follow_up_done) changes.follow_up_done = values.follow_up_done;
  const links = cleanLinks(values.links);
  if (JSON.stringify(links) !== JSON.stringify(cleanLinks(initial.links))) changes.links = links;
  return changes;
}

const FORM_FIELDS = new Set<string>([
  ...TEXT_FIELDS,
  "department",
  "country",
  "status",
  "applied_at",
  "deadline",
  "application_sent",
  "follow_up_done",
  "links",
]);

/** Erreurs du service rattachées aux champs du formulaire ; le reste va dans « form ». */
export function errorsFromApi(error: unknown): FormErrors {
  if (!(error instanceof ApiError)) return { form: "Erreur inattendue." };
  const errors: FormErrors = {};
  const other: string[] = [];
  for (const item of error.errors) {
    const name = item.field.split(".")[0] ?? "";
    if (FORM_FIELDS.has(name)) {
      const key = name as FormField;
      errors[key] = errors[key] ? `${errors[key]} ${item.message}` : item.message;
    } else {
      other.push(item.field ? `${item.field} : ${item.message}` : item.message);
    }
  }
  if (error.errors.length === 0) errors.form = error.message;
  else if (other.length > 0) errors.form = other.join(" ; ");
  else errors.form = "Certains champs sont invalides : voir les messages ci-dessous.";
  return errors;
}
