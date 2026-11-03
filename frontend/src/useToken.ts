import { useSyncExternalStore } from "react";
import { getToken, subscribeToken } from "./api/token";

/** Jeton mémorisé courant ; se met à jour à la connexion, à la déconnexion et sur un 401. */
export function useToken(): string | null {
  return useSyncExternalStore(subscribeToken, getToken, () => null);
}
