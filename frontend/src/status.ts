import { STATUS_UNSET, type Status } from "./api/types";

// Classe CSS d'un statut ; les couleurs (variables --status-*) viennent de styles.css,
// copiées de reference/build_excel_v2.py (PAL).
const STATUS_CLASS: Record<Status, string> = {
  "À postuler": "status-a-postuler",
  "En cours": "status-en-cours",
  Entretien: "status-entretien",
  "Offre reçue": "status-offre-recue",
  Rejeté: "status-rejete",
  Abandon: "status-abandon",
};

export function statusClass(status: Status | null): string {
  return status === null ? "status-a-qualifier" : STATUS_CLASS[status];
}

export function statusLabel(status: Status | null): string {
  return status ?? STATUS_UNSET;
}
