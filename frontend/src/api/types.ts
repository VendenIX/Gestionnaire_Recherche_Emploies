// Types miroirs des schémas du backend (backend/app/schemas.py).
// Règle D-001 : un code de département ou de pays est TOUJOURS une chaîne ("01", "2A", "FR"),
// jamais un nombre.

export const STATUSES = ["À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon"] as const;

/** Un des 6 statuts valides. */
export type Status = (typeof STATUSES)[number];

/** Libellé d'affichage d'une offre sans statut (status === null). */
export const STATUS_UNSET = "À qualifier";
export type StatusUnset = typeof STATUS_UNSET;

/** Code de département du référentiel : "01", "2A", "75"… (jamais un nombre). */
export type DepartmentCode = string;
/** Code pays ISO à 2 lettres en majuscules : "FR", "CH"… */
export type CountryCode = string;
/** Date AAAA-MM-JJ. */
export type IsoDate = string;
/** Date et heure ISO 8601. */
export type IsoDateTime = string;

/** Offre renvoyée par l'API (OfferOut), avec les champs calculés à la lecture. */
export interface Offer {
  id: number;
  title: string | null;
  contract_type: string | null;
  company: string | null;
  location: string | null;
  department: DepartmentCode | null;
  country: CountryCode;
  region: string | null;
  status: Status | null;
  main_issue: string | null;
  rejection_reason: string | null;
  source: string | null;
  applied_at: IsoDateTime | null;
  application_sent: boolean;
  follow_up_done: boolean;
  deadline: IsoDate | null;
  deadline_note: string | null;
  description: string | null;
  links: string[];
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  days_left: number | null;
  follow_up_suggested: boolean;
}

/** Champs modifiables d'une offre (_OfferFields) ; la région n'est jamais envoyée. */
export interface OfferFields {
  title?: string | null;
  contract_type?: string | null;
  company?: string | null;
  location?: string | null;
  department?: DepartmentCode | null;
  country?: CountryCode | null;
  status?: Status | null;
  main_issue?: string | null;
  rejection_reason?: string | null;
  source?: string | null;
  applied_at?: IsoDateTime | null;
  application_sent?: boolean;
  follow_up_done?: boolean;
  deadline?: IsoDate | null;
  deadline_note?: string | null;
  description?: string | null;
  links?: string[];
}

/** Création (POST /api/offers) : numéro de dossier explicite permis. */
export interface OfferCreate extends OfferFields {
  id?: number;
}

/** Mise à jour partielle (PATCH) : seuls les champs fournis changent ; null efface. */
export type OfferUpdate = OfferFields;

export interface Department {
  code: DepartmentCode;
  name: string;
  region: string;
}

/** GET /api/meta */
export interface Meta {
  statuses: Status[];
  status_unset_label: string;
  regions: string[];
  /** Libellé « Hors France » (région des offres à l'étranger). Absent sur un backend antérieur. */
  out_of_france_label?: string;
  no_department_label: string;
  departments: Department[];
  follow_up_days: number;
  /** Lien Nextcloud par WebDAV actif (NEXTCLOUD_WEBDAV et réglages complets). Absent : inactif. */
  nextcloud_enabled?: boolean;
  /** Réglage Nextcloud manquant ou invalide (WebDAV demandé mais inutilisable), sinon null. */
  nextcloud_error?: string | null;
}

/** Élément du dossier Nextcloud d'une offre (fichier ou sous-dossier). */
export interface FolderEntry {
  name: string;
  is_dir: boolean;
  /** Taille en octets ; null pour un dossier ou si Nextcloud ne la donne pas. */
  size: number | null;
  modified: IsoDateTime | null;
  /** Lien vers l'interface web Nextcloud. */
  url: string;
}

/** GET / POST /api/offers/{id}/folder */
export interface Folder {
  name: string;
  url: string;
  files: FolderEntry[];
  /** POST : true si le dossier vient d'être créé, false s'il existait. */
  created?: boolean;
}

/** Issue de la création automatique du dossier avec l'offre (POST /api/offers). */
export interface FolderOutcome {
  status: "disabled" | "created" | "existing" | "error";
  name?: string | null;
  url?: string | null;
  detail?: string | null;
}

/** Réponse de POST /api/offers : l'offre et, sur un backend récent, l'issue de son dossier. */
export interface OfferCreated extends Offer {
  folder?: FolderOutcome;
}

/** GET /api/stats ; les clés de by_status incluent le libellé « À qualifier ». */
export interface Stats {
  total: number;
  by_status: Record<string, number>;
  by_region: Record<string, number>;
  deadlines_soon: Offer[];
}

/** Filtres de GET /api/offers. */
export interface OfferFilters {
  status?: Status | StatusUnset;
  department?: DepartmentCode;
  region?: string;
  q?: string;
  order?: "asc" | "desc";
}

/** Détail d'une erreur de validation (422). */
export interface FieldError {
  field: string;
  message: string;
}

export function isStatus(value: unknown): value is Status {
  return typeof value === "string" && (STATUSES as readonly string[]).includes(value);
}
