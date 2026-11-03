// Cellules modifiables en ligne : un clic (ou Entrée) ouvre le champ, Entrée ou la sortie du champ
// enregistre, Échap annule. Chaque contrôle porte un libellé accessible avec le numéro de l'offre.

import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { STATUS_UNSET, STATUSES, isStatus, type Status } from "../api/types";
import { statusClass } from "../status";

interface StatusSelectProps {
  value: Status | null;
  label: string;
  onChange: (value: Status | null) => void;
}

export function StatusSelect({ value, label, onChange }: StatusSelectProps) {
  return (
    <select
      className={`status status-select ${statusClass(value)}`}
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => onChange(isStatus(e.target.value) ? e.target.value : null)}
    >
      <option value="">{STATUS_UNSET}</option>
      {STATUSES.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </select>
  );
}

interface CheckboxProps {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}

export function CheckboxCell({ checked, label, onChange }: CheckboxProps) {
  return (
    <input
      type="checkbox"
      className="cell-checkbox"
      aria-label={label}
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

interface EditableProps {
  value: string | null;
  /** Libellé accessible du champ et du bouton (ex. « Intitulé de l'offre n° 3 »). */
  label: string;
  type?: "text" | "date";
  /** Contenu affiché hors édition ; par défaut la valeur. */
  display?: ReactNode;
  onSave: (value: string | null) => void;
}

/** Texte ou date modifiable ; une valeur vide enregistre null. */
export function EditableCell({ value, label, type = "text", display, onSave }: EditableProps) {
  const [draft, setDraft] = useState<string | null>(null);
  // Évite un double enregistrement (Entrée puis perte de focus du champ retiré).
  const closed = useRef(false);

  if (draft === null) {
    return (
      <button
        type="button"
        className="cell-edit"
        aria-label={`Modifier : ${label}`}
        title="Modifier"
        onClick={() => {
          closed.current = false;
          setDraft(value ?? "");
        }}
      >
        {display ?? value ?? ""}
        {!value && display === undefined ? <span className="muted">—</span> : null}
      </button>
    );
  }

  const commit = () => {
    if (closed.current) return;
    closed.current = true;
    const next = type === "text" ? draft.trim() : draft;
    setDraft(null);
    const normalized = next === "" ? null : next;
    if (normalized !== value) onSave(normalized);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closed.current = true;
      setDraft(null);
    }
  };

  return (
    <input
      type={type}
      className="cell-input"
      aria-label={label}
      value={draft}
      autoFocus
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={onKeyDown}
    />
  );
}
