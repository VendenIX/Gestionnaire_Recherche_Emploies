// Dossier Nextcloud d'une offre dans le panneau de détail (phase 3, D-003).
//
// Le dossier est cherché dès l'ouverture du panneau (GET /api/offers/{id}/folder) : « Ouvrir le
// dossier » devient alors un vrai lien (<a target="_blank">) vers son adresse, qu'aucun
// navigateur ne bloque comme fenêtre surgissante (pas de window.open après un appel réseau).
// Sans lien Nextcloud actif (/api/meta), le bouton reste désactivé avec une infobulle et rien
// n'est demandé au service.

import { useCallback, useEffect, useRef, useState } from "react";
import { createFolder, getFolder } from "../api/client";
import type { Folder } from "../api/types";
import { entryLink, folderProblem, formatModified, formatSize, sortEntries, type FolderProblem } from "./folder";

export type FolderState =
  | { kind: "disabled" }
  | { kind: "loading" }
  | { kind: "ready"; folder: Folder; notice: string | null }
  | { kind: "error"; problem: FolderProblem };

export interface OfferFolderApi {
  state: FolderState;
  creating: boolean;
  /** Erreur de la dernière création (« Créer le dossier »), affichée sous le message. */
  createError: FolderProblem | null;
  reload: () => void;
  /** Crée le dossier ; vrai si la création a réussi (l'erreur est rangée dans createError). */
  create: () => Promise<boolean>;
}

/**
 * Charge le dossier de l'offre tant que le lien Nextcloud est actif ; création à la demande.
 * Chaque résultat est rangé sous la clé de sa demande (offre et rechargement) : une réponse
 * arrivée après un changement d'offre ou un « Réessayer » ne s'affiche jamais.
 */
export function useOfferFolder(offerId: number, enabled: boolean): OfferFolderApi {
  const [reloads, setReloads] = useState(0);
  const key = `${offerId}:${reloads}`;
  const [loaded, setLoaded] = useState<{ key: string; state: FolderState } | null>(null);
  const [creatingKey, setCreatingKey] = useState<string | null>(null);
  const [createFailure, setCreateFailure] = useState<{ key: string; problem: FolderProblem } | null>(null);
  // Clé affichée en ce moment : une création finie après un changement d'offre n'écrase pas le
  // dossier déjà chargé pour la nouvelle offre.
  const currentKey = useRef(key);
  useEffect(() => {
    currentKey.current = key;
  }, [key]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    getFolder(offerId, controller.signal).then(
      (folder) => {
        if (controller.signal.aborted) return;
        setLoaded({ key, state: { kind: "ready", folder, notice: null } });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({ key, state: { kind: "error", problem: folderProblem(error) } });
      },
    );
    return () => controller.abort();
  }, [offerId, enabled, key]);

  const reload = useCallback(() => setReloads((n) => n + 1), []);

  const create = useCallback(async () => {
    const at = key;
    setCreatingKey(at);
    setCreateFailure(null);
    try {
      const folder = await createFolder(offerId);
      const notice =
        folder.created === false ? `Dossier existant retrouvé : ${folder.name}` : `Dossier créé : ${folder.name}`;
      if (currentKey.current === at) setLoaded({ key: at, state: { kind: "ready", folder, notice } });
      return true;
    } catch (error) {
      if (currentKey.current === at) setCreateFailure({ key: at, problem: folderProblem(error) });
      return false;
    } finally {
      setCreatingKey((k) => (k === at ? null : k));
    }
  }, [offerId, key]);

  const state: FolderState = !enabled
    ? { kind: "disabled" }
    : loaded?.key === key
      ? loaded.state
      : { kind: "loading" };
  return {
    state,
    creating: enabled && creatingKey === key,
    createError: createFailure?.key === key ? createFailure.problem : null,
    reload,
    create,
  };
}

/** Bouton « Ouvrir le dossier » : lien vers Nextcloud une fois le dossier trouvé, sinon désactivé. */
export function OpenFolderButton({ state, disabledNote }: { state: FolderState; disabledNote: string }) {
  if (state.kind === "ready") {
    return (
      <a className="button ghost" href={state.folder.url} target="_blank" rel="noopener noreferrer">
        Ouvrir le dossier{" "}
        <span className="visually-hidden">(nouvel onglet)</span>
      </a>
    );
  }
  const note =
    state.kind === "disabled"
      ? disabledNote
      : state.kind === "loading"
        ? "Recherche du dossier dans Nextcloud…"
        : state.problem.missing
          ? "Aucun dossier pour cette offre : « Créer le dossier » plus bas."
          : "Dossier indisponible : voir le message plus bas.";
  return (
    <span className="tooltip-wrap" title={note}>
      <button type="button" className="button ghost" disabled title={note} aria-description={note}>
        Ouvrir le dossier
      </button>
    </span>
  );
}

/** Section « Dossier Nextcloud » du panneau : fichiers, création, messages d'erreur. */
export function FolderSection({ api, offerId }: { api: OfferFolderApi; offerId: number }) {
  const { state, creating, createError } = api;
  const heading = useRef<HTMLHeadingElement>(null);
  const headingId = `dossier-offre-${offerId}`;

  const onCreate = async () => {
    if (creating) return;
    // Réussite : le bouton « Créer le dossier » disparaît, le focus va au titre de la section.
    // Échec : le bouton reste là et garde le focus pour réessayer (aria-disabled pendant la
    // création, pas disabled : un bouton désactivé perdrait le focus clavier).
    if (await api.create()) heading.current?.focus();
  };

  if (state.kind === "disabled") return null;
  return (
    <section className="detail-folder" aria-labelledby={headingId} aria-busy={state.kind === "loading" || creating}>
      <h3 id={headingId} ref={heading} tabIndex={-1}>
        Dossier Nextcloud
      </h3>
      {state.kind === "loading" ? (
        <p role="status" className="muted">
          Chargement du dossier…
        </p>
      ) : null}
      {state.kind === "ready" ? <FolderContents folder={state.folder} notice={state.notice} /> : null}
      {state.kind === "error" ? (
        <div className="folder-problem">
          <p role={state.problem.missing ? "status" : "alert"} className={state.problem.missing ? undefined : "error"}>
            <strong>{state.problem.title}</strong> {state.problem.missing ? null : state.problem.detail}
          </p>
          {state.problem.missing ? (
            <p className="muted folder-detail">{state.problem.detail}</p>
          ) : null}
          {createError ? (
            <p role="alert" className="error">
              <strong>Création impossible.</strong> {createError.title} {createError.detail}
            </p>
          ) : null}
          <div className="detail-actions">
            {state.problem.missing ? (
              <button
                type="button"
                className="button primary"
                onClick={() => void onCreate()}
                aria-disabled={creating}
              >
                {creating ? "Création du dossier…" : "Créer le dossier"}
              </button>
            ) : null}
            {state.problem.retry ? (
              <button type="button" className="button ghost" onClick={api.reload}>
                Réessayer
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function FolderContents({ folder, notice }: { folder: Folder; notice: string | null }) {
  const entries = sortEntries(folder.files);
  return (
    <>
      {notice ? (
        <p role="status" className="folder-notice">
          {notice}
        </p>
      ) : null}
      <p className="folder-name">
        <a href={folder.url} target="_blank" rel="noopener noreferrer">
          {folder.name}
        </a>
      </p>
      {entries.length === 0 ? (
        <p className="muted">Dossier vide.</p>
      ) : (
        <div className="folder-files-wrap">
          <table className="folder-files">
            <caption className="visually-hidden">Contenu du dossier {folder.name}</caption>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Taille</th>
                <th scope="col">Modifié le</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={`${entry.is_dir ? "d" : "f"}-${entry.name}`}>
                  <td>
                    <a href={entryLink(entry)} target="_blank" rel="noopener noreferrer">
                      {entry.is_dir ? `${entry.name}/` : entry.name}
                    </a>
                    {entry.is_dir ? (
                      <>
                        {" "}
                        <span className="visually-hidden">(dossier)</span>
                      </>
                    ) : null}
                  </td>
                  <td className="folder-size">{entry.is_dir ? <span className="muted">—</span> : formatSize(entry.size)}</td>
                  <td>{formatModified(entry.modified)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
