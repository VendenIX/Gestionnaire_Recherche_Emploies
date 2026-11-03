// Formulaire d'offre : « Nouvelle offre » et modification complète (mêmes champs).
// Menus déroulants (statut, département dans l'ordre du référentiel), suggestions de contrat,
// vraies cases à cocher, champs date, liste de liens modifiable, messages en français à côté des
// champs (validation locale puis erreurs 422 du service).
// OfferFormDialog : le formulaire dans une fenêtre ; la fermer (Échap, Annuler, ×) avec des
// modifications non enregistrées demande confirmation (fenêtre dans la page, pas window.confirm).

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { STATUS_UNSET, STATUSES, isStatus } from "../api/types";
import { DEPARTMENTS } from "../geo";
import { Dialog } from "./Dialog";
import {
  CONTRACT_SUGGESTIONS,
  FRANCE,
  errorsFromApi,
  isFrance,
  toChanges,
  validateForm,
  type FormErrors,
  type FormField,
  type FormValues,
  type TextField,
} from "./form";

export interface OfferFormProps {
  initial: FormValues;
  submitLabel: string;
  /** Envoie le formulaire ; une erreur levée (ApiError) est affichée à côté des champs. */
  onSubmit: (values: FormValues) => Promise<void>;
  onCancel: () => void;
  /** Appelé quand le formulaire devient différent (ou de nouveau identique) des valeurs initiales. */
  onDirtyChange?: (dirty: boolean) => void;
  /** Appelé quand l'envoi commence et quand sa réponse (succès ou erreur) est arrivée. */
  onSubmittingChange?: (submitting: boolean) => void;
}

const LABELS: Record<FormField, string> = {
  title: "Intitulé",
  company: "Entreprise",
  contract_type: "Type de contrat",
  location: "Lieu (ville)",
  country: "Pays (code ISO)",
  department: "Département",
  status: "Statut",
  source: "Source",
  applied_at: "Date de candidature",
  deadline: "Date limite",
  deadline_note: "Note sur la date limite",
  main_issue: "Enjeu principal",
  rejection_reason: "Motif de refus",
  description: "Description",
  links: "Liens",
  application_sent: "Candidature envoyée",
  follow_up_done: "Relance faite",
};

/** Ordre de focalisation du premier champ en erreur. */
const FIELD_ORDER: FormField[] = [
  "title",
  "company",
  "contract_type",
  "location",
  "country",
  "department",
  "status",
  "source",
  "applied_at",
  "deadline",
  "deadline_note",
  "main_issue",
  "rejection_reason",
  "description",
  "links",
  "application_sent",
  "follow_up_done",
];

export function OfferForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
  onDirtyChange,
  onSubmittingChange,
}: OfferFormProps) {
  const [values, setValues] = useState<FormValues>(initial);
  // Modifié = au moins un champ qui partirait dans la requête (mêmes règles que toChanges).
  const dirty = Object.keys(toChanges(values, initial)).length > 0;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  const [errors, setErrors] = useState<FormErrors>({});
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    onSubmittingChange?.(submitting);
  }, [submitting, onSubmittingChange]);
  const prefix = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const id = (field: string) => `${prefix}-${field}`;
  const errorId = (field: string) => `${prefix}-${field}-error`;

  const set = <K extends FormField>(field: K, value: FormValues[K]) => {
    setValues((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => (prev[field] ? { ...prev, [field]: undefined } : prev));
  };

  // À l'étranger, le département est désactivé et jamais envoyé (la région devient « Hors
  // France ») ; il n'est pas effacé du formulaire : revenir à FR le retrouve.
  const setCountry = (country: string) => {
    setValues((prev) => ({ ...prev, country }));
    setErrors((prev) => ({ ...prev, country: undefined, department: undefined }));
  };

  const focusFirstError = (found: FormErrors) => {
    const field = FIELD_ORDER.find((f) => found[f]);
    const target = field
      ? formRef.current?.querySelector<HTMLElement>(`[data-field="${field}"]`)
      : formRef.current?.querySelector<HTMLElement>(".form-error-summary");
    target?.focus();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    const local = validateForm(values);
    if (Object.keys(local).length > 0) {
      setErrors({ ...local, form: "Le formulaire contient des erreurs : voir les messages à côté des champs." });
      requestAnimationFrame(() => focusFirstError(local));
      return;
    }
    setErrors({});
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (error) {
      const found = errorsFromApi(error);
      setErrors(found);
      requestAnimationFrame(() => focusFirstError(found));
    } finally {
      setSubmitting(false);
    }
  };

  const describedBy = (field: FormField, hint?: string) =>
    [errors[field] ? errorId(field) : null, hint ?? null].filter(Boolean).join(" ") || undefined;

  const errorOf = (field: FormField) =>
    errors[field] ? (
      <p id={errorId(field)} className="field-error">
        {errors[field]}
      </p>
    ) : null;

  const field = (name: FormField, control: ReactNode, wide = false) => (
    <div className={`form-field${wide ? " form-field-wide" : ""}`}>
      <label htmlFor={id(name)}>{LABELS[name]}</label>
      {control}
      {errorOf(name)}
    </div>
  );

  const text = (name: TextField, options: { autoFocus?: boolean; list?: string; wide?: boolean } = {}) =>
    field(
      name,
      <input
        id={id(name)}
        data-field={name}
        data-autofocus={options.autoFocus ? "" : undefined}
        type="text"
        list={options.list}
        value={values[name]}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(name)}
        onChange={(e) => set(name, e.target.value)}
      />,
      options.wide,
    );

  const area = (name: TextField, rows = 3) =>
    field(
      name,
      <textarea
        id={id(name)}
        data-field={name}
        rows={rows}
        value={values[name]}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(name)}
        onChange={(e) => set(name, e.target.value)}
      />,
      true,
    );

  const date = (name: "applied_at" | "deadline") =>
    field(
      name,
      <input
        id={id(name)}
        data-field={name}
        type="date"
        value={values[name]}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={describedBy(name)}
        onChange={(e) => set(name, e.target.value)}
      />,
    );

  const check = (name: "application_sent" | "follow_up_done") => (
    <div className="form-check">
      <input
        id={id(name)}
        data-field={name}
        type="checkbox"
        checked={values[name]}
        onChange={(e) => set(name, e.target.checked)}
      />
      <label htmlFor={id(name)}>{LABELS[name]}</label>
      {errorOf(name)}
    </div>
  );

  const abroad = !isFrance(values);
  const departmentHint = id("department-hint");

  return (
    <form ref={formRef} className="offer-form" noValidate onSubmit={submit} aria-busy={submitting || undefined}>
      {errors.form ? (
        <p role="alert" tabIndex={-1} className="error form-error-summary">
          {errors.form}
        </p>
      ) : null}

      <div className="form-grid">
        {text("title", { autoFocus: true, wide: true })}
        {text("company")}
        {text("contract_type", { list: id("contracts") })}
        <datalist id={id("contracts")}>
          {CONTRACT_SUGGESTIONS.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        {text("location")}
        {field(
          "country",
          <input
            id={id("country")}
            data-field="country"
            type="text"
            maxLength={2}
            autoCapitalize="characters"
            className="input-country"
            value={values.country}
            placeholder={FRANCE}
            aria-invalid={errors.country ? true : undefined}
            aria-describedby={describedBy("country")}
            onChange={(e) => setCountry(e.target.value.toUpperCase())}
          />,
        )}
        {field(
          "department",
          <>
            <select
              id={id("department")}
              data-field="department"
              value={abroad ? "" : values.department}
              disabled={abroad}
              aria-invalid={errors.department ? true : undefined}
              aria-describedby={describedBy("department", abroad ? departmentHint : undefined)}
              onChange={(e) => set("department", e.target.value)}
            >
              <option value="">Non renseigné</option>
              {DEPARTMENTS.map((d) => (
                <option key={d.code} value={d.code}>
                  {d.code} - {d.name}
                </option>
              ))}
            </select>
            {abroad ? (
              <p id={departmentHint} className="field-hint">
                Pas de département hors de France.
              </p>
            ) : null}
          </>,
        )}
        {field(
          "status",
          <select
            id={id("status")}
            data-field="status"
            value={values.status}
            aria-invalid={errors.status ? true : undefined}
            aria-describedby={describedBy("status")}
            onChange={(e) => set("status", isStatus(e.target.value) ? e.target.value : "")}
          >
            <option value="">{STATUS_UNSET} (sans statut)</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>,
        )}
        {text("source")}
        {date("applied_at")}
        {date("deadline")}
        {text("deadline_note")}
        {area("main_issue", 2)}
        {area("rejection_reason", 2)}
        {area("description", 4)}

        <fieldset className="form-field form-field-wide form-links">
          <legend>{LABELS.links}</legend>
          {values.links.length === 0 ? <p className="muted">Aucun lien.</p> : null}
          <ul>
            {values.links.map((link, i) => (
              <li key={i}>
                <input
                  type="url"
                  data-field={i === 0 ? "links" : undefined}
                  aria-label={`Lien n° ${i + 1}`}
                  value={link}
                  placeholder="https://"
                  aria-invalid={errors.links ? true : undefined}
                  aria-describedby={describedBy("links")}
                  onChange={(e) => set("links", values.links.map((l, j) => (j === i ? e.target.value : l)))}
                />
                <button
                  type="button"
                  className="button ghost"
                  aria-label={`Retirer le lien n° ${i + 1}`}
                  onClick={() => set("links", values.links.filter((_, j) => j !== i))}
                >
                  Retirer
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="button ghost" onClick={() => set("links", [...values.links, ""])}>
            Ajouter un lien
          </button>
          {errorOf("links")}
        </fieldset>

        <div className="form-field form-field-wide form-checks">
          {check("application_sent")}
          {check("follow_up_done")}
        </div>
      </div>

      <div className="form-actions">
        <button type="button" className="button ghost" onClick={onCancel} disabled={submitting}>
          Annuler
        </button>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Enregistrement…" : submitLabel}
        </button>
      </div>
    </form>
  );
}

export interface OfferFormDialogProps {
  title: string;
  initial: FormValues;
  submitLabel: string;
  /** Envoie le formulaire ; en cas de succès, l'appelant ferme la fenêtre (sans confirmation). */
  onSubmit: (values: FormValues) => Promise<void>;
  /** Fermeture demandée par l'utilisateur, après confirmation s'il y a des modifications. */
  onClose: () => void;
}

export const DISCARD_TITLE = "Abandonner les modifications ?";

/** Formulaire d'offre dans une fenêtre ; confirmation avant d'abandonner une saisie. */
export function OfferFormDialog({ title, initial, submitLabel, onSubmit, onClose }: OfferFormDialogProps) {
  const [dirty, setDirty] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // Requête POST/PATCH en vol : elle s'appliquera quand même, la fenêtre ne peut donc pas être
  // fermée (Échap, ×, Annuler) avant sa réponse ; sinon « Abandonner » mentirait.
  const [submitting, setSubmitting] = useState(false);
  const requestClose = () => {
    if (submitting) return;
    if (dirty) setConfirming(true);
    else onClose();
  };
  return (
    <Dialog title={title} onClose={requestClose} className="offer-form-dialog">
      <OfferForm
        initial={initial}
        submitLabel={submitLabel}
        onSubmit={onSubmit}
        onCancel={requestClose}
        onDirtyChange={setDirty}
        onSubmittingChange={setSubmitting}
      />
      {confirming ? (
        <Dialog role="alertdialog" title={DISCARD_TITLE} onClose={() => setConfirming(false)} className="confirm-dialog">
          <p>Les modifications saisies dans ce formulaire seront perdues.</p>
          <div className="form-actions">
            <button type="button" className="button ghost" data-autofocus="" onClick={() => setConfirming(false)}>
              Continuer la saisie
            </button>
            <button type="button" className="button danger" onClick={onClose}>
              Abandonner
            </button>
          </div>
        </Dialog>
      ) : null}
    </Dialog>
  );
}
