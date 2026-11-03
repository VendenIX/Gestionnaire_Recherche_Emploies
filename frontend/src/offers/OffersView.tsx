// Tableau principal des offres : Statut, Deadline et Jours restants d'abord, filtres rapides
// (envoyés à GET /api/offers), tri par en-tête, édition en ligne (PATCH du seul champ modifié,
// retour arrière en cas d'erreur), relance suggérée et export Excel. Ouvre aussi le formulaire
// « Nouvelle offre » et le panneau de détail (modification complète, suppression) : toutes les
// modifications, du tableau comme du panneau, passent par le même suivi (EditTracker).

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, createOffer, deleteOffer, exportXlsx, getMeta, listOffers, updateOffer } from "../api/client";
import { STATUS_UNSET, STATUSES, type Meta, type Offer, type OfferUpdate } from "../api/types";
import "../styles/offers.css";
import "../styles/dialogs.css";
import { CheckboxCell, EditableCell, StatusSelect } from "./cells";
import { EditTracker } from "./edits";
import { emptyForm, toCreatePayload, type FormValues } from "./form";
import { folderOutcomeNote, nextcloudDisabledNote, nextcloudEnabled } from "./folder";
import { OfferDetail } from "./OfferDetail";
import { OfferFormDialog } from "./OfferForm";
import { cleanFilters, useOfferFilters, type TableFilters } from "./filters";
import {
  DEFAULT_FOLLOW_UP_DAYS,
  daysLeft,
  daysLeftLabel,
  departmentOrderOf,
  followUpSuggested,
  formatDate,
  isClosed,
  sortOffers,
  urgency,
  urgencyClass,
  type SortKey,
  type SortState,
} from "./logic";

/** Délai avant d'envoyer la recherche texte au serveur. */
export const TEXT_DEBOUNCE_MS = 300;

type EditableField = keyof OfferUpdate & keyof Offer;

const COLUMNS: { key: SortKey | null; label: string; className?: string }[] = [
  { key: "status", label: "Statut", className: "col-quick" },
  { key: "deadline", label: "Deadline", className: "col-quick" },
  { key: "days", label: "Jours restants", className: "col-quick" },
  { key: "id", label: "N°" },
  { key: "title", label: "Intitulé" },
  { key: "company", label: "Entreprise" },
  { key: "contract_type", label: "Contrat" },
  { key: "location", label: "Lieu" },
  { key: "department", label: "Département / Région" },
  { key: null, label: "Candidature envoyée", className: "col-check" },
  { key: null, label: "Relance faite", className: "col-check" },
];

const FIELD_LABELS: Partial<Record<EditableField, string>> = {
  status: "Statut",
  deadline: "Deadline",
  title: "Intitulé",
  company: "Entreprise",
  contract_type: "Contrat",
  location: "Lieu",
  application_sent: "Candidature envoyée",
  follow_up_done: "Relance faite",
};

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Erreur inattendue.";
}

function todayIso(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Télécharge un Blob sous le nom donné (lien temporaire). */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

interface LoadResult {
  key: string;
  offers: Offer[];
  error: string | null;
}

export function OffersView() {
  const { filters, updateFilter, resetFilters, setFilters } = useOfferFilters();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [result, setResult] = useState<LoadResult | null>(null);
  const [reloads, setReloads] = useState(0);
  const [sort, setSort] = useState<SortState>({ key: "id", dir: "asc" });
  const [editError, setEditError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Recherche texte différée : la saisie met à jour le filtre tout de suite, l'appel attend.
  const [debouncedQ, setDebouncedQ] = useState(filters.q ?? "");
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQ(filters.q ?? ""), TEXT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  useEffect(() => {
    const controller = new AbortController();
    getMeta({ signal: controller.signal }).then(setMeta, () => undefined);
    return () => controller.abort();
  }, []);

  const queryKey = JSON.stringify(
    cleanFilters({ status: filters.status, region: filters.region, department: filters.department, q: debouncedQ.trim() }),
  );
  const loadKey = `${queryKey}#${reloads}`;

  useEffect(() => {
    const controller = new AbortController();
    const query = JSON.parse(queryKey) as TableFilters;
    listOffers(query, controller.signal).then(
      (offers) => setResult({ key: loadKey, offers, error: null }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setResult({ key: loadKey, offers: [], error: messageOf(error) });
      },
    );
    return () => controller.abort();
  }, [queryKey, loadKey]);

  const loading = result?.key !== loadKey;
  const followUpDays = meta?.follow_up_days ?? DEFAULT_FOLLOW_UP_DAYS;
  const departmentOrder = useMemo(() => departmentOrderOf(meta), [meta]);
  const rows = useMemo(
    () => sortOffers(result?.offers ?? [], sort, { departmentOrder }),
    [result, sort, departmentOrder],
  );

  const replaceOffer = useCallback((id: number, update: (offer: Offer) => Offer) => {
    setResult((prev) => (prev ? { ...prev, offers: prev.offers.map((o) => (o.id === id ? update(o) : o)) } : prev));
  }, []);

  // Modifications concurrentes : une réponse ancienne n'écrase pas une modification plus récente.
  const [tracker] = useState(() => new EditTracker());

  /** Enregistre un seul champ : affichage immédiat, PATCH, retour arrière si refus. */
  const saveField = useCallback(
    async <K extends EditableField>(offer: Offer, field: K, value: Offer[K]) => {
      if (offer[field] === value) return;
      setEditError(null);
      const { seq, apply } = tracker.start(offer, field, value);
      replaceOffer(offer.id, apply);
      try {
        const saved = await updateOffer(offer.id, { [field]: value } as OfferUpdate);
        replaceOffer(offer.id, tracker.succeed(offer.id, field, seq, saved));
      } catch (error) {
        const latest = tracker.isLatest(offer.id, field, seq);
        replaceOffer(offer.id, tracker.fail(offer.id, field, seq));
        if (latest) {
          const label = FIELD_LABELS[field] ?? field;
          setEditError(`Modification non enregistrée (offre n° ${offer.id}, ${label}) : ${messageOf(error)}`);
        }
      }
    },
    [replaceOffer, tracker],
  );

  /**
   * Enregistre plusieurs champs en un PATCH (formulaire de modification). Même suivi que
   * saveField : seuls les champs changés sont envoyés, une modification en ligne en vol sur un
   * autre champ garde sa valeur, et la plus récente l'emporte sur un même champ. L'erreur est
   * relevée pour que le formulaire l'affiche à côté des champs.
   */
  const saveChanges = useCallback(
    async (offer: Offer, changes: Partial<Offer>) => {
      const names = Object.keys(changes) as (keyof Offer)[];
      if (names.length === 0) return;
      setEditError(null);
      const { seq, apply } = tracker.startMany(offer, changes);
      replaceOffer(offer.id, apply);
      try {
        const saved = await updateOffer(offer.id, changes as OfferUpdate);
        replaceOffer(offer.id, tracker.succeedMany(offer.id, names, seq, saved));
      } catch (error) {
        replaceOffer(offer.id, tracker.failMany(offer.id, names, seq));
        throw error;
      }
    },
    [replaceOffer, tracker],
  );

  const onCreate = async (values: FormValues) => {
    const created = await createOffer(toCreatePayload(values));
    setCreating(false);
    setNotice(`Offre n° ${created.id} créée.${folderOutcomeNote(created.folder)}`);
    setReloads((n) => n + 1);
  };

  const onDelete = async (offer: Offer) => {
    await deleteOffer(offer.id);
    setResult((prev) => (prev ? { ...prev, offers: prev.offers.filter((o) => o.id !== offer.id) } : prev));
    setDetailId(null);
    setNotice(`Offre n° ${offer.id} supprimée.`);
  };

  const detailOffer = detailId === null ? null : (result?.offers.find((o) => o.id === detailId) ?? null);
  const tableHeading = () => document.getElementById("tableau-title");

  const onExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await exportXlsx();
      downloadBlob(blob, `suivi-offres-${todayIso()}.xlsx`);
    } catch (error) {
      setExportError(`Export impossible : ${messageOf(error)}`);
    } finally {
      setExporting(false);
    }
  };

  const toggleSort = (key: SortKey) =>
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  const regions = meta ? [...meta.regions, meta.no_department_label] : [];
  const departments = (meta?.departments ?? []).filter(
    (d) => !filters.region || d.region === filters.region,
  );

  const onRegionChange = (region: string) => {
    const dep = meta?.departments.find((d) => d.code === filters.department);
    const keepDepartment = !region || (dep !== undefined && dep.region === region);
    setFilters({ ...filters, region, department: keepDepartment ? filters.department : undefined });
  };

  const hasFilters = Object.keys(filters).length > 0;
  const now = new Date();

  return (
    <section aria-labelledby="tableau-title" className="card offers">
      <div className="offers-head">
        <h2 id="tableau-title" tabIndex={-1}>
          Tableau des offres
        </h2>
        <div className="offers-actions">
          <button
            type="button"
            className="button primary"
            onClick={() => {
              setNotice(null);
              setCreating(true);
            }}
          >
            Nouvelle offre
          </button>
          <button type="button" className="button ghost" onClick={() => setReloads((n) => n + 1)}>
            Actualiser
          </button>
          <button type="button" className="button ghost" onClick={onExport} disabled={exporting}>
            {exporting ? "Export en cours…" : "Exporter en Excel"}
          </button>
        </div>
      </div>

      <form className="filters" role="search" aria-label="Filtres des offres" onSubmit={(e) => e.preventDefault()}>
        <label>
          <span>Statut</span>
          <select value={filters.status ?? ""} onChange={(e) => updateFilter("status", e.target.value as TableFilters["status"])}>
            <option value="">Tous</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
            <option value={STATUS_UNSET}>{STATUS_UNSET}</option>
          </select>
        </label>
        <label>
          <span>Région</span>
          <select value={filters.region ?? ""} onChange={(e) => onRegionChange(e.target.value)}>
            <option value="">Toutes</option>
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Département</span>
          <select value={filters.department ?? ""} onChange={(e) => updateFilter("department", e.target.value)}>
            <option value="">Tous</option>
            {departments.map((d) => (
              <option key={d.code} value={d.code}>
                {d.code} — {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-text">
          <span>Recherche</span>
          <input
            type="search"
            value={filters.q ?? ""}
            placeholder="Intitulé, entreprise, lieu…"
            onChange={(e) => updateFilter("q", e.target.value)}
          />
        </label>
        {hasFilters ? (
          <button type="button" className="button ghost" onClick={resetFilters}>
            Effacer les filtres
          </button>
        ) : null}
      </form>

      {notice ? (
        <p role="status" className="notice">
          {notice}
        </p>
      ) : null}
      {exportError ? (
        <p role="alert" className="error">
          {exportError}
        </p>
      ) : null}
      {/* Panneau ouvert : l'erreur s'affiche dans le panneau (fenêtre modale), pas derrière. */}
      {editError && !detailOffer ? (
        <p role="alert" className="error edit-error">
          {editError}{" "}
          <button type="button" className="link-button" onClick={() => setEditError(null)}>
            Fermer
          </button>
        </p>
      ) : null}
      {result?.error && !loading ? (
        <p role="alert" className="error">
          Impossible de charger les offres : {result.error}
        </p>
      ) : null}

      <p className="muted offers-count" aria-live="polite">
        {loading ? "Chargement…" : `${rows.length} offre${rows.length > 1 ? "s" : ""}`}
      </p>

      {/* Zone défilante (mobile) : focalisable pour défiler au clavier, nommée pour les lecteurs d'écran. */}
      <div className="table-scroll" role="region" aria-label="Liste des offres" tabIndex={0}>
        <table className="offers-table">
          <thead>
            <tr>
              {COLUMNS.map((col) => {
                const sorted = col.key !== null && sort.key === col.key;
                return (
                  <th
                    key={col.label}
                    scope="col"
                    className={col.className}
                    aria-sort={sorted ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {col.key === null ? (
                      col.label
                    ) : (
                      <button type="button" className="sort-button" onClick={() => toggleSort(col.key as SortKey)}>
                        {col.label}
                        <span aria-hidden="true" className="sort-mark">
                          {sorted ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                        </span>
                      </button>
                    )}
                  </th>
                );
              })}
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((offer) => {
              const days = daysLeft(offer, now);
              const level = urgencyClass(urgency(days, offer.status));
              const suggested = followUpSuggested(offer, followUpDays, now);
              const n = `offre n° ${offer.id}`;
              return (
                <tr key={offer.id} className={isClosed(offer.status) ? "row-closed" : undefined}>
                  <td className="cell-status">
                    <StatusSelect
                      value={offer.status}
                      label={`Statut de l'${n}`}
                      onChange={(v) => void saveField(offer, "status", v)}
                    />
                  </td>
                  <td className={`cell-deadline ${level}`}>
                    <EditableCell
                      type="date"
                      value={offer.deadline}
                      label={`Deadline de l'${n}`}
                      display={
                        offer.deadline ? (
                          formatDate(offer.deadline)
                        ) : (
                          <span className="muted" title="Note de date limite">
                            {offer.deadline_note || "—"}
                          </span>
                        )
                      }
                      onSave={(v) => void saveField(offer, "deadline", v)}
                    />
                  </td>
                  <td className={`cell-days ${level}`}>{daysLeftLabel(days)}</td>
                  <td className="cell-id">{offer.id}</td>
                  {(["title", "company", "contract_type", "location"] as const).map((field) => (
                    <td key={field} className={`cell-${field}`}>
                      <EditableCell
                        value={offer[field]}
                        label={`${FIELD_LABELS[field]} de l'${n}`}
                        onSave={(v) => void saveField(offer, field, v)}
                      />
                    </td>
                  ))}
                  <td className="cell-geo">
                    {offer.department ? <strong>{offer.department}</strong> : null}
                    {offer.department && offer.region ? " · " : null}
                    {offer.region ?? (offer.country !== "FR" ? offer.country : null)}
                    {offer.country !== "FR" && offer.region ? ` (${offer.country})` : null}
                  </td>
                  <td className="cell-check">
                    <CheckboxCell
                      checked={offer.application_sent}
                      label={`Candidature envoyée (${n})`}
                      onChange={(v) => void saveField(offer, "application_sent", v)}
                    />
                  </td>
                  <td className="cell-check">
                    <CheckboxCell
                      checked={offer.follow_up_done}
                      label={`Relance faite (${n})`}
                      onChange={(v) => void saveField(offer, "follow_up_done", v)}
                    />
                    {suggested ? (
                      <span
                        className="badge-follow-up"
                        title={`Candidature de plus de ${followUpDays} jours sans relance`}
                      >
                        Relance suggérée
                      </span>
                    ) : null}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      aria-label={`Détail de l'${n}`}
                      onClick={() => {
                        setNotice(null);
                        setEditError(null);
                        setDetailId(offer.id);
                      }}
                    >
                      Détail
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!loading && !result?.error && rows.length === 0 ? (
        <p className="muted">{hasFilters ? "Aucune offre ne correspond aux filtres." : "Aucune offre."}</p>
      ) : null}

      {detailOffer ? (
        <OfferDetail
          offer={detailOffer}
          followUpDays={followUpDays}
          onClose={() => setDetailId(null)}
          onSaveField={(offer, field, value) => void saveField(offer, field, value)}
          editError={editError}
          onDismissError={() => setEditError(null)}
          onSaveChanges={saveChanges}
          onDelete={onDelete}
          fallbackFocus={tableHeading}
          nextcloudEnabled={nextcloudEnabled(meta)}
          nextcloudNote={nextcloudDisabledNote(meta)}
        />
      ) : null}
      {creating ? (
        <NewOfferDialog onCreate={onCreate} onClose={() => setCreating(false)} />
      ) : null}
    </section>
  );
}

function NewOfferDialog({ onCreate, onClose }: { onCreate: (values: FormValues) => Promise<void>; onClose: () => void }) {
  const [initial] = useState(emptyForm);
  return (
    <OfferFormDialog
      title="Nouvelle offre"
      initial={initial}
      submitLabel="Créer l'offre"
      onSubmit={onCreate}
      onClose={onClose}
    />
  );
}
