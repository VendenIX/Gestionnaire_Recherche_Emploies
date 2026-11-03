// Jeton d'API mémorisé dans localStorage (décision du propriétaire, 2026-11-02).
// Petit magasin observable, lu par React avec useSyncExternalStore.

export const TOKEN_STORAGE_KEY = "jobs-tracker.api-token";

type Listener = () => void;
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) listener();
}

export function getToken(): string | null {
  try {
    const value = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    return value ? value : null;
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  lastClearReason = null;
  notify();
}

/** Pourquoi le jeton a été effacé en dernier (message de l'écran du jeton). */
export type ClearReason = "logout" | "unauthorized";
let lastClearReason: ClearReason | null = null;

export function getLastClearReason(): ClearReason | null {
  return lastClearReason;
}

/** Réservé aux tests : remet à zéro l'état du module. */
export function resetTokenStateForTests(): void {
  lastClearReason = null;
}

export function clearToken(reason: ClearReason = "logout"): void {
  lastClearReason = reason;
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } finally {
    notify();
  }
}

export function subscribeToken(listener: Listener): () => void {
  listeners.add(listener);
  // Un autre onglet qui se connecte ou se déconnecte.
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === TOKEN_STORAGE_KEY) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}
