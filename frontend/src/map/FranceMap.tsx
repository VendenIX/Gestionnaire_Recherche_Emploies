// Carte interactive de la France métropolitaine (régions ou départements), colorée par nombre
// d'offres. React dessine le SVG ; d3-geo calcule la projection et les tracés, d3-scale les
// couleurs : D3 ne touche jamais au DOM.
//
// Comportement : bascule Régions / Départements ; filtre de statut identique au menu de la
// Synthèse Excel (« Toutes » ou un statut) ; info-bulle au survol et au focus ; clic, Entrée ou
// Espace sur une zone = filtre du tableau (région ou département, plus le statut choisi) puis
// passage au tableau ; compteurs « Hors France » et « Sans département » à côté ; tableau des
// comptes en alternative texte. Clavier : la carte est un seul arrêt de tabulation (« roving
// tabindex », sinon 96 arrêts en mode Départements) ; les flèches vont à la zone voisine,
// Origine / Fin à la première / dernière zone du référentiel (voir navigation.ts).

import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { geoPath } from "d3-geo";
import { scaleLinear } from "d3-scale";
import { mesh } from "topojson-client";
import { STATUSES, type Offer, type Status } from "../api/types";
import { FRANCE_TOPOLOGY, loadFeatures, type GeoLayer } from "../geo";
import { useOfferFilters, type TableFilters } from "../offers/filters";
import { viewHref } from "../useHashView";
import "../styles/map.css";
import {
  ALL_STATUSES,
  DEFAULT_MAP_LABELS,
  countByZone,
  offersLabel,
  zonesOf,
  type MapLabels,
  type MapStatusFilter,
  type ZoneInfo,
} from "./counts";
import { isNavigationKey, nextZone } from "./navigation";
import { DEFAULT_SIZE, MAP_ASPECT, makeProjection, zoneCentroids, zonePaths } from "./projection";
import { placeTooltip } from "./tooltip";

/** Zones sans offre (grisées). */
export const EMPTY_FILL = "#e5e7eb";
/** Dégradé du moins au plus d'offres (bleus de l'accent, contraste suffisant avec le gris). */
export const FILL_RANGE: [string, string] = ["#bfdbfe", "#1e3a8a"];
/** Nombre d'offres listées dans l'info-bulle. */
export const TOOLTIP_MAX_OFFERS = 5;

const LAYER_LABELS: Record<GeoLayer, string> = { regions: "Régions", departements: "Départements" };

/** Règle les filtres du tableau puis l'affiche. */
export function useOpenTable(): (filters: TableFilters) => void {
  const { setFilters } = useOfferFilters();
  return (filters: TableFilters) => {
    setFilters(filters);
    window.location.hash = viewHref("tableau");
  };
}

function offerLine(offer: Offer): string {
  const title = offer.title?.trim() || "Sans intitulé";
  return `N° ${offer.id} · ${title}${offer.company ? ` (${offer.company})` : ""}`;
}

function useContainerSize(ref: RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState<{ width: number; height: number }>(DEFAULT_SIZE);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      if (width <= 0) return;
      const height = Math.round(width * MAP_ASPECT);
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

interface Active {
  code: string;
}

export function FranceMap({
  offers,
  labels = DEFAULT_MAP_LABELS,
}: {
  offers: readonly Offer[];
  /** Libellés « Hors France » / « Sans département » (de /api/meta, voir mapLabelsOf). */
  labels?: MapLabels;
}) {
  const { resetFilters } = useOfferFilters();
  const openTable = useOpenTable();
  const [layer, setLayer] = useState<GeoLayer>("regions");
  const [status, setStatus] = useState<MapStatusFilter>(ALL_STATUSES);
  const [active, setActive] = useState<Active | null>(null);
  // Zone qui porte l'unique arrêt de tabulation de la carte (la dernière focalisée).
  const [rover, setRover] = useState<string | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const { width, height } = useContainerSize(canvasRef);
  const tooltipId = useId();
  const titleId = useId();

  const features = loadFeatures(layer);
  const zones = useMemo(() => zonesOf(layer), [layer]);
  const counts = useMemo(() => countByZone(offers, layer, status), [offers, layer, status]);
  const projection = useMemo(() => makeProjection(features, width, height), [features, width, height]);
  const paths = useMemo(() => zonePaths(features, projection), [features, projection]);
  const centroids = useMemo(() => zoneCentroids(features, projection), [features, projection]);
  const regionBorders = useMemo(() => {
    if (layer !== "departements") return null;
    const borders = mesh(FRANCE_TOPOLOGY, FRANCE_TOPOLOGY.objects.regions, (a, b) => a !== b);
    return geoPath(projection).digits(1)(borders);
  }, [layer, projection]);
  const color = useMemo(
    () => scaleLinear<string>().domain([1, Math.max(counts.max, 2)]).range(FILL_RANGE).clamp(true),
    [counts.max],
  );

  const statusFilter: TableFilters = status === ALL_STATUSES ? {} : { status };
  const selectZone = (zone: ZoneInfo) => {
    if (layer === "regions") openTable({ ...statusFilter, region: zone.name });
    else openTable({ ...statusFilter, region: zone.regionName, department: zone.code });
  };
  const selectRegion = (region: string) => openTable({ ...statusFilter, region });

  const zoneOrder = useMemo(() => zones.map((z) => z.code), [zones]);
  const tabStop = rover !== null && zoneOrder.includes(rover) ? rover : zoneOrder[0];

  const onZoneKey = (event: KeyboardEvent, zone: ZoneInfo) => {
    if (event.key === "Enter" || event.key === " ") {
      // Espace : sans preventDefault, le navigateur ferait défiler la page (un <path> n'est pas
      // un vrai bouton) ; Entrée : évite un éventuel clic synthétique en double.
      event.preventDefault();
      selectZone(zone);
      return;
    }
    if (!isNavigationKey(event.key)) return;
    // Flèches : pas de défilement de la page, même sans voisine dans cette direction.
    event.preventDefault();
    const target = nextZone(zone.code, event.key, zoneOrder, centroids);
    if (!target) return;
    canvasRef.current?.querySelector<SVGPathElement>(`path.zone[data-code="${target}"]`)?.focus();
  };

  // Échap ferme l'info-bulle, qu'elle vienne du focus ou du survol (WCAG 1.4.13 : contenu au
  // survol masquable sans déplacer le pointeur). Le survol ne donne pas le focus : écoute sur le
  // document tant qu'une info-bulle est ouverte.
  const tooltipOpen = active !== null;
  useEffect(() => {
    if (!tooltipOpen) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setActive(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [tooltipOpen]);

  const reset = () => {
    setLayer("regions");
    setStatus(ALL_STATUSES);
    setActive(null);
    setRover(null);
    resetFilters();
  };

  const activeZone = active ? zones.find((z) => z.code === active.code) : undefined;
  const activeOffers = activeZone ? (counts.byZone.get(activeZone.code) ?? []) : [];
  const anchor = activeZone ? centroids.get(activeZone.code) : undefined;
  const statusSuffix = status === ALL_STATUSES ? "" : ` « ${status} »`;

  // Position de l'info-bulle, mesurée avant affichage : elle reste dans le cadre de la carte
  // (décalée près des bords, passée dessous en haut). Écrite dans le DOM, sans nouveau rendu.
  useLayoutEffect(() => {
    const tip = tooltipRef.current;
    const canvas = canvasRef.current;
    if (!tip || !canvas || !anchor) return;
    // Pixels réels du cadre (le viewBox peut différer tant que ResizeObserver n'a pas mesuré).
    const frameWidth = canvas.clientWidth || width;
    const scale = frameWidth / width;
    const box = placeTooltip(
      [anchor[0] * scale, anchor[1] * scale],
      { width: frameWidth, height: height * scale },
      { width: tip.offsetWidth, height: tip.offsetHeight },
    );
    tip.style.left = `${Math.round(box.left)}px`;
    tip.style.top = `${Math.round(box.top)}px`;
    tip.classList.toggle("below", box.below);
  });

  return (
    <section className="card france-map" aria-labelledby={titleId}>
      <div className="map-head">
        <h3 id={titleId}>Carte des offres par {layer === "regions" ? "région" : "département"}</h3>
        <div className="map-controls">
          <div className="map-toggle" role="group" aria-label="Découpage de la carte">
            {(Object.keys(LAYER_LABELS) as GeoLayer[]).map((l) => (
              <button
                key={l}
                type="button"
                className="button"
                aria-pressed={layer === l}
                onClick={() => {
                  setLayer(l);
                  setActive(null);
                  setRover(null);
                }}
              >
                {LAYER_LABELS[l]}
              </button>
            ))}
          </div>
          <label className="map-status">
            Afficher
            <select value={status} onChange={(e) => setStatus(e.target.value as MapStatusFilter)}>
              <option value={ALL_STATUSES}>Toutes</option>
              {STATUSES.map((s: Status) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="button ghost" onClick={reset}>
            Réinitialiser
          </button>
        </div>
      </div>

      <div className="map-body">
        <div className="map-canvas" ref={canvasRef}>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            width="100%"
            role="group"
            aria-label={`Carte de France, offres${statusSuffix} par ${layer === "regions" ? "région" : "département"}`}
            onMouseLeave={() => setActive(null)}
          >
            <g className="zones">
              {zones.map((zone) => {
                const count = counts.byZone.get(zone.code)?.length ?? 0;
                return (
                  <path
                    key={zone.code}
                    d={paths.get(zone.code) ?? ""}
                    data-code={zone.code}
                    data-count={count}
                    className={count === 0 ? "zone zone-empty" : "zone"}
                    fill={count === 0 ? EMPTY_FILL : color(count)}
                    role="button"
                    tabIndex={zone.code === tabStop ? 0 : -1}
                    aria-label={`${zone.name} : ${offersLabel(count)}${statusSuffix}`}
                    aria-describedby={active?.code === zone.code ? tooltipId : undefined}
                    onClick={() => selectZone(zone)}
                    onKeyDown={(e) => onZoneKey(e, zone)}
                    onMouseEnter={() => setActive({ code: zone.code })}
                    onFocus={() => {
                      setActive({ code: zone.code });
                      setRover(zone.code);
                    }}
                    onBlur={() => setActive((prev) => (prev?.code === zone.code ? null : prev))}
                  />
                );
              })}
            </g>
            {regionBorders ? <path className="region-borders" d={regionBorders} aria-hidden="true" /> : null}
            {activeZone ? (
              <path className="zone-highlight" d={paths.get(activeZone.code) ?? ""} aria-hidden="true" />
            ) : null}
          </svg>
          {activeZone && anchor ? (
            <div
              id={tooltipId}
              role="tooltip"
              ref={tooltipRef}
              className="map-tooltip"
            >
              <strong>{activeZone.name}</strong>
              <span>
                {offersLabel(activeOffers.length)}
                {statusSuffix}
              </span>
              {activeOffers.length > 0 ? (
                <ul>
                  {activeOffers.slice(0, TOOLTIP_MAX_OFFERS).map((o) => (
                    <li key={o.id}>{offerLine(o)}</li>
                  ))}
                  {activeOffers.length > TOOLTIP_MAX_OFFERS ? (
                    <li className="muted">et {activeOffers.length - TOOLTIP_MAX_OFFERS} autre(s)</li>
                  ) : null}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="map-side" aria-label="Offres hors de la carte et légende">
          <button type="button" className="map-counter" onClick={() => selectRegion(labels.horsFrance)}>
            <span className="map-counter-label">{labels.horsFrance}</span>
            <span className="map-counter-value" data-testid="count-hors-france">
              {counts.horsFrance.length}
            </span>
          </button>
          <button type="button" className="map-counter" onClick={() => selectRegion(labels.noDepartment)}>
            <span className="map-counter-label">{labels.noDepartment}</span>
            <span className="map-counter-value" data-testid="count-sans-departement">
              {counts.sansDepartement.length}
            </span>
          </button>
          {counts.autres.length > 0 ? (
            <div className="map-counter static">
              <span className="map-counter-label">Autres (hors référentiel)</span>
              <span className="map-counter-value" data-testid="count-autres">
                {counts.autres.length}
              </span>
            </div>
          ) : null}
          <div className="map-legend" aria-hidden="true">
            <span>
              <i style={{ background: EMPTY_FILL }} /> Aucune offre
            </span>
            <span>
              <i style={{ background: color(1) }} /> 1
            </span>
            {counts.max > 1 ? (
              <span>
                <i style={{ background: color(counts.max) }} /> {counts.max}
              </span>
            ) : null}
          </div>
          <p className="muted map-hint">
            Cliquez sur une zone (ou Entrée) pour filtrer le tableau ; au clavier, les flèches passent
            d'une zone à sa voisine.
          </p>
        </aside>
      </div>

      <details className="map-table">
        <summary>Tableau des comptes ({LAYER_LABELS[layer].toLowerCase()})</summary>
        <table>
          <caption className="map-caption">
            Offres{statusSuffix} par {layer === "regions" ? "région" : "département"}
          </caption>
          <thead>
            <tr>
              <th scope="col">{layer === "regions" ? "Région" : "Département"}</th>
              <th scope="col">Offres</th>
            </tr>
          </thead>
          <tbody>
            {zones.map((zone) => (
              <tr key={zone.code}>
                <th scope="row">{zone.name}</th>
                <td>{counts.byZone.get(zone.code)?.length ?? 0}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{labels.horsFrance}</th>
              <td>{counts.horsFrance.length}</td>
            </tr>
            <tr>
              <th scope="row">{labels.noDepartment}</th>
              <td>{counts.sansDepartement.length}</td>
            </tr>
            {counts.autres.length > 0 ? (
              <tr>
                <th scope="row">Autres (hors référentiel)</th>
                <td>{counts.autres.length}</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </details>
    </section>
  );
}
