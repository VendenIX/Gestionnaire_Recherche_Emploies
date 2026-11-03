// Filtres du tableau, partagés par l'application (contexte React) : la carte de la Synthèse peut
// les régler (ex. setFilters({ region: "Corse" }) puis aller sur #/tableau).

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { OfferFilters } from "../api/types";

/** Filtres rapides : statut (dont « À qualifier »), région, département, texte. */
export type TableFilters = Pick<OfferFilters, "status" | "region" | "department" | "q">;

export interface OfferFiltersApi {
  filters: TableFilters;
  /** Remplace tous les filtres. */
  setFilters: (filters: TableFilters) => void;
  /** Modifie un filtre ; une valeur vide le retire. */
  updateFilter: <K extends keyof TableFilters>(key: K, value: TableFilters[K] | "" | undefined) => void;
  resetFilters: () => void;
}

/** Retire les filtres vides (undefined ou chaîne vide). */
export function cleanFilters(filters: TableFilters): TableFilters {
  const out: TableFilters = {};
  for (const [key, value] of Object.entries(filters) as [keyof TableFilters, string | undefined][]) {
    if (value !== undefined && value !== "") (out as Record<string, string>)[key] = value;
  }
  return out;
}

const OfferFiltersContext = createContext<OfferFiltersApi | null>(null);

export function OfferFiltersProvider({ children, initial = {} }: { children: ReactNode; initial?: TableFilters }) {
  const [filters, setRaw] = useState<TableFilters>(() => cleanFilters(initial));

  const setFilters = useCallback((next: TableFilters) => setRaw(cleanFilters(next)), []);
  const updateFilter = useCallback<OfferFiltersApi["updateFilter"]>(
    (key, value) => setRaw((prev) => cleanFilters({ ...prev, [key]: value })),
    [],
  );
  const resetFilters = useCallback(() => setRaw({}), []);

  const api = useMemo(
    () => ({ filters, setFilters, updateFilter, resetFilters }),
    [filters, setFilters, updateFilter, resetFilters],
  );
  return <OfferFiltersContext.Provider value={api}>{children}</OfferFiltersContext.Provider>;
}

/** Filtres du tableau ; à utiliser sous <OfferFiltersProvider>. */
export function useOfferFilters(): OfferFiltersApi {
  const api = useContext(OfferFiltersContext);
  if (!api) throw new Error("useOfferFilters doit être utilisé sous <OfferFiltersProvider>.");
  return api;
}
