// Panneau de détail d'une offre (panneau latéral sur portable, plein écran sur mobile) : tous les
// champs, région calculée, liens cliquables, dates de création et de modification ; actions
// Modifier (formulaire complet), Supprimer (confirmation dans la page) et « Ouvrir le dossier »
// (lien Nextcloud par WebDAV, phase 3 : désactivé avec une infobulle s'il n'est pas activé ; la
// section « Dossier Nextcloud » liste alors les fichiers ou propose de créer le dossier, voir
// OfferFolder.tsx).
//
// Toute modification passe par le suivi des modifications de OffersView (EditTracker) : les champs
// rapides (statut, cases à cocher) par onSaveField, le formulaire par onSaveChanges (un PATCH des
// seuls champs changés). L'échec d'un champ rapide (editError) s'affiche dans le panneau, près des
// champs rapides : le panneau est modal, le tableau derrière n'est pas lisible.

import { useState, type ReactNode } from "react";
import type { Offer } from "../api/types";
import { departmentByCode } from "../geo";
import { statusLabel } from "../status";
import { CheckboxCell, StatusSelect } from "./cells";
import { Dialog } from "./Dialog";
import { NEXTCLOUD_NOTE } from "./folder";
import { formFromOffer, toChanges } from "./form";
import { daysLeft, daysLeftLabel, followUpSuggested, formatDate } from "./logic";
import { FolderSection, OpenFolderButton, useOfferFolder } from "./OfferFolder";
import { OfferFormDialog } from "./OfferForm";

export { NEXTCLOUD_NOTE };

export type QuickField = "status" | "application_sent" | "follow_up_done";

export interface OfferDetailProps {
  offer: Offer;
  followUpDays: number;
  onClose: () => void;
  /** Modification d'un seul champ (même chemin que l'édition en ligne du tableau). */
  onSaveField: <K extends QuickField>(offer: Offer, field: K, value: Offer[K]) => void;
  /** Erreur de la dernière modification d'un champ (onSaveField), avec sa fermeture. */
  editError?: string | null;
  onDismissError?: () => void;
  /** Modification de plusieurs champs (formulaire) ; lève l'erreur du service en cas d'échec. */
  onSaveChanges: (offer: Offer, changes: Partial<Offer>) => Promise<void>;
  /** Suppression confirmée ; lève l'erreur du service en cas d'échec. */
  onDelete: (offer: Offer) => Promise<void>;
  fallbackFocus?: () => HTMLElement | null;
  /** Lien Nextcloud actif (/api/meta nextcloud_enabled) : le dossier est cherché à l'ouverture. */
  nextcloudEnabled?: boolean;
  /** Infobulle du bouton « Ouvrir le dossier » quand le lien n'est pas actif. */
  nextcloudNote?: string;
}

/** AAAA-MM-JJTHH:MM… -> JJ/MM/AAAA HH:MM (00:00 omis). */
export function formatDateTime(value: string | null): string {
  if (!value) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(value);
  if (!m) return value;
  const day = `${m[3]}/${m[2]}/${m[1]}`;
  return m[4] && `${m[4]}:${m[5]}` !== "00:00" ? `${day} ${m[4]}:${m[5]}` : day;
}

function Item({ label, children }: { label: string; children: ReactNode }) {
  const empty = children === null || children === undefined || children === "";
  return (
    <div className="detail-item">
      <dt>{label}</dt>
      <dd>{empty ? <span className="muted">—</span> : children}</dd>
    </div>
  );
}

export function OfferDetail({
  offer,
  followUpDays,
  onClose,
  onSaveField,
  editError,
  onDismissError,
  onSaveChanges,
  onDelete,
  fallbackFocus,
  nextcloudEnabled = false,
  nextcloudNote = NEXTCLOUD_NOTE,
}: OfferDetailProps) {
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const folder = useOfferFolder(offer.id, nextcloudEnabled);
  const n = `offre n° ${offer.id}`;
  const days = daysLeft(offer);
  const department = offer.department ? departmentByCode(offer.department) : undefined;

  return (
    <Dialog
      variant="panel"
      className="offer-detail"
      title={
        <>
          <span className="detail-number">N° {offer.id}</span> {offer.title || <span className="muted">Sans intitulé</span>}
        </>
      }
      onClose={onClose}
      fallbackFocus={fallbackFocus}
    >
      <div className="detail-actions">
        <button type="button" className="button primary" onClick={() => setMode("edit")}>
          Modifier
        </button>
        <button type="button" className="button danger" onClick={() => setMode("delete")}>
          Supprimer
        </button>
        <OpenFolderButton state={folder.state} disabledNote={nextcloudNote} />
      </div>

      <div className="detail-quick">
        <label>
          <span>Statut</span>
          <StatusSelect
            value={offer.status}
            label={`Statut de l'${n}`}
            onChange={(v) => onSaveField(offer, "status", v)}
          />
        </label>
        <label className="detail-check">
          <CheckboxCell
            checked={offer.application_sent}
            label={`Candidature envoyée (${n})`}
            onChange={(v) => onSaveField(offer, "application_sent", v)}
          />
          <span aria-hidden="true">Candidature envoyée</span>
        </label>
        <label className="detail-check">
          <CheckboxCell
            checked={offer.follow_up_done}
            label={`Relance faite (${n})`}
            onChange={(v) => onSaveField(offer, "follow_up_done", v)}
          />
          <span aria-hidden="true">Relance faite</span>
        </label>
        {followUpSuggested(offer, followUpDays) ? <span className="badge-follow-up">Relance suggérée</span> : null}
      </div>
      {editError ? (
        <p role="alert" className="error edit-error">
          {editError}{" "}
          <button type="button" className="link-button" onClick={onDismissError}>
            Fermer le message
          </button>
        </p>
      ) : null}

      <dl className="detail-list">
        <Item label="Statut">{statusLabel(offer.status)}</Item>
        <Item label="Date limite">
          {offer.deadline ? `${formatDate(offer.deadline)}${days !== null ? ` (${daysLeftLabel(days)})` : ""}` : null}
        </Item>
        <Item label="Note sur la date limite">{offer.deadline_note}</Item>
        <Item label="Entreprise">{offer.company}</Item>
        <Item label="Type de contrat">{offer.contract_type}</Item>
        <Item label="Lieu">{offer.location}</Item>
        <Item label="Département">
          {offer.department ? (department ? `${offer.department} - ${department.name}` : offer.department) : null}
        </Item>
        <Item label="Région">{offer.region}</Item>
        <Item label="Pays">{offer.country}</Item>
        <Item label="Source">{offer.source}</Item>
        <Item label="Date de candidature">{formatDateTime(offer.applied_at)}</Item>
        <Item label="Candidature envoyée">{offer.application_sent ? "Oui" : "Non"}</Item>
        <Item label="Relance faite">{offer.follow_up_done ? "Oui" : "Non"}</Item>
        <Item label="Enjeu principal">{offer.main_issue}</Item>
        <Item label="Motif de refus">{offer.rejection_reason}</Item>
        <Item label="Description">
          {offer.description ? <span className="detail-text">{offer.description}</span> : null}
        </Item>
        <Item label="Liens">
          {offer.links.length > 0 ? (
            <ul className="detail-links">
              {offer.links.map((link, i) => (
                <li key={`${i}-${link}`}>
                  <a href={link} target="_blank" rel="noopener noreferrer">
                    {link}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
        </Item>
        <Item label="Créée le">{formatDateTime(offer.created_at)}</Item>
        <Item label="Modifiée le">{formatDateTime(offer.updated_at)}</Item>
      </dl>

      <FolderSection api={folder} offerId={offer.id} />

      {mode === "edit" ? (
        <EditOfferDialog offer={offer} onSave={onSaveChanges} onClose={() => setMode("view")} />
      ) : null}
      {mode === "delete" ? (
        <DeleteDialog offer={offer} onDelete={onDelete} onClose={() => setMode("view")} />
      ) : null}
    </Dialog>
  );
}

function EditOfferDialog({
  offer,
  onSave,
  onClose,
}: {
  offer: Offer;
  onSave: OfferDetailProps["onSaveChanges"];
  onClose: () => void;
}) {
  // Instantané pris à l'ouverture (valeurs affichées, optimistes comprises) : seuls les champs que
  // l'utilisateur change dans le formulaire partent dans le PATCH, comparés à cet instantané et
  // non à l'offre courante. Choix voulu : si une modification en ligne en vol échoue pendant que
  // le formulaire est ouvert, le formulaire garde la valeur optimiste à l'écran mais ne la renvoie
  // pas (le champ n'a pas été touché) ; l'offre garde la valeur revenue et l'erreur s'affiche dans
  // le panneau. Comparer à formFromOffer(offer) renverrait en silence la modification refusée.
  const [initial] = useState(() => formFromOffer(offer));
  return (
    <OfferFormDialog
      title={`Modifier l'offre n° ${offer.id}`}
      initial={initial}
      submitLabel="Enregistrer"
      onClose={onClose}
      onSubmit={async (values) => {
        const changes = toChanges(values, initial);
        if (Object.keys(changes).length > 0) await onSave(offer, changes);
        onClose();
      }}
    />
  );
}

function DeleteDialog({
  offer,
  onDelete,
  onClose,
}: {
  offer: Offer;
  onDelete: OfferDetailProps["onDelete"];
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onDelete(offer);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erreur inattendue.");
      setBusy(false);
    }
  };
  return (
    <Dialog role="alertdialog" title={`Supprimer l'offre n° ${offer.id} ?`} onClose={onClose} className="confirm-dialog">
      <p>
        L'offre « {offer.title || "sans intitulé"} »{offer.company ? ` (${offer.company})` : ""} sera supprimée
        définitivement. Son numéro ne sera pas réutilisé.
      </p>
      {error ? (
        <p role="alert" className="error">
          Suppression impossible : {error}
        </p>
      ) : null}
      <div className="form-actions">
        <button type="button" className="button ghost" onClick={onClose} data-autofocus="">
          Annuler
        </button>
        <button type="button" className="button danger" onClick={confirm} disabled={busy}>
          {busy ? "Suppression…" : "Supprimer définitivement"}
        </button>
      </div>
    </Dialog>
  );
}
