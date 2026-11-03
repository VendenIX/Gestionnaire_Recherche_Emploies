// Page Synthèse : compteurs (comme l'onglet Synthèse de l'Excel v2, mêmes couleurs de statut),
// répartition par statut (barres SVG/CSS accessibles, sans bibliothèque de graphiques),
// deadlines à 7 jours (GET /api/stats) et carte de France (offres de GET /api/offers).

import { useEffect, useMemo, useState } from "react";
import { ApiError, getMeta, getStats, listOffers } from "../api/client";
import { STATUS_UNSET, STATUSES, type Meta, type Offer, type Stats, type Status } from "../api/types";
import { mapLabelsOf } from "../map/counts";
import { FranceMap } from "../map/FranceMap";
import { daysLeftLabel, formatDate, urgency, urgencyClass } from "../offers/logic";
import { statusClass } from "../status";
import "../styles/synthese.css";

/** Ordre de la répartition : les 6 statuts puis « À qualifier » (comme l'Excel). */
export const DISTRIBUTION_ORDER: readonly (Status | null)[] = [...STATUSES, null];

interface Loaded {
  stats: Stats | null;
  offers: Offer[] | null;
  error: string | null;
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "Erreur inattendue.";
}

/** Part en pourcentage entier (0 si total nul). */
export function share(count: number, total: number): number {
  return total > 0 ? Math.round((count / total) * 100) : 0;
}

function countOf(stats: Stats, status: Status | null): number {
  return stats.by_status[status ?? STATUS_UNSET] ?? 0;
}

function Tile({ label, value, className = "" }: { label: string; value: number | string; className?: string }) {
  return (
    <div className={`tile ${className}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function SyntheseView() {
  const [loaded, setLoaded] = useState<Loaded>({ stats: null, offers: null, error: null });
  const [meta, setMeta] = useState<Meta | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // Un seul message d'erreur, celui du premier appel qui échoue : stats et offres viennent du
    // même service, leurs erreurs ont presque toujours la même cause (jeton, service arrêté) et
    // deux alertes identiques n'apprennent rien de plus. La partie chargée reste affichée.
    const fail = (error: unknown) => {
      if (controller.signal.aborted) return;
      setLoaded((prev) => ({ ...prev, error: prev.error ?? messageOf(error) }));
    };
    getStats(controller.signal).then((stats) => setLoaded((prev) => ({ ...prev, stats })), fail);
    listOffers({}, controller.signal).then((offers) => setLoaded((prev) => ({ ...prev, offers })), fail);
    // Meta ne sert qu'aux libellés « Hors France » / « Sans département » de la carte : en cas
    // d'échec, la carte garde ses libellés par défaut, sans alerte (comme le tableau).
    getMeta({ signal: controller.signal }).then(setMeta, () => undefined);
    return () => controller.abort();
  }, []);

  const { stats, offers, error } = loaded;
  const mapLabels = useMemo(() => mapLabelsOf(meta), [meta]);
  const sent = offers ? offers.filter((o) => o.application_sent).length : null;

  return (
    <section aria-labelledby="synthese-title" className="synthese">
      <h2 id="synthese-title">Synthèse</h2>
      {error ? (
        <p role="alert" className="synthese-error">
          Chargement impossible : {error}
        </p>
      ) : null}
      {!stats && !error ? <p className="muted">Chargement…</p> : null}

      {stats ? (
        <>
          <dl className="tiles" aria-label="Compteurs">
            <Tile label="Offres suivies" value={stats.total} className="tile-neutral" />
            <Tile label="Candidatures envoyées" value={sent ?? "…"} className="tile-neutral" />
            {DISTRIBUTION_ORDER.map((s) => (
              <Tile key={s ?? STATUS_UNSET} label={s ?? STATUS_UNSET} value={countOf(stats, s)} className={statusClass(s)} />
            ))}
            <Tile label="Deadlines ≤ 7 jours" value={stats.deadlines_soon.length} className="tile-deadline" />
          </dl>

          <div className="synthese-grid">
            <section className="card" aria-labelledby="repartition-title">
              <h3 id="repartition-title">Répartition par statut</h3>
              <ul className="bars">
                {DISTRIBUTION_ORDER.map((s) => {
                  const label = s ?? STATUS_UNSET;
                  const count = countOf(stats, s);
                  const pct = share(count, stats.total);
                  return (
                    <li key={label} className="bar-row">
                      <span className={`status ${statusClass(s)} bar-label`}>{label}</span>
                      <span className="bar-track" aria-hidden="true">
                        <span className={`bar-fill bar-${statusClass(s)}`} style={{ width: `${pct}%` }} />
                      </span>
                      <span className="bar-value">
                        {count} <span className="muted">({pct} %)</span>
                      </span>
                    </li>
                  );
                })}
              </ul>
              <p className="bars-total">
                Total : <strong>{stats.total}</strong>
              </p>
            </section>

            <section className="card" aria-labelledby="deadlines-title">
              <h3 id="deadlines-title">Deadlines dans les 7 jours</h3>
              {stats.deadlines_soon.length === 0 ? (
                <p className="muted">Aucune deadline dans les 7 prochains jours.</p>
              ) : (
                <ul className="deadlines">
                  {stats.deadlines_soon.map((o) => (
                    <li key={o.id}>
                      <span className={`deadline-chip ${urgencyClass(urgency(o.days_left, o.status))}`}>
                        {formatDate(o.deadline)}
                        {o.days_left !== null ? ` · ${daysLeftLabel(o.days_left)}` : ""}
                      </span>
                      <span>
                        N° {o.id} · {o.title || "Sans intitulé"}
                        {o.company ? <span className="muted"> — {o.company}</span> : null}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      ) : null}

      {offers ? <FranceMap offers={offers} labels={mapLabels} /> : null}
    </section>
  );
}
