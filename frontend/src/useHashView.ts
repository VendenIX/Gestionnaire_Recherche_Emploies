import { useSyncExternalStore } from "react";

// Navigation minimale par le fragment d'URL (#/tableau, #/synthese) : pas de bibliothèque de routage.
export const VIEWS = ["tableau", "synthese"] as const;
export type View = (typeof VIEWS)[number];

function readView(): View {
  const name = window.location.hash.replace(/^#\/?/, "");
  return (VIEWS as readonly string[]).includes(name) ? (name as View) : "tableau";
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

export function useHashView(): View {
  return useSyncExternalStore(subscribe, readView, () => "tableau");
}

export function viewHref(view: View): string {
  return `#/${view}`;
}
