import { useId, useState, type FormEvent } from "react";
import { ApiError, checkToken } from "./api/client";
import { setToken } from "./api/token";

/** Caractères ASCII visibles uniquement (valeur d'en-tête Authorization sûre). */
const VALID_TOKEN = /^[\x21-\x7E]+$/;

/** Écran de connexion : demande le jeton une fois, le vérifie, puis le mémorise. */
export function TokenScreen({ notice }: { notice?: string }) {
  const inputId = useId();
  const errorId = useId();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = value.trim();
    if (!token) {
      setError("Saisissez le jeton d'accès.");
      return;
    }
    if (!VALID_TOKEN.test(token)) {
      // Un en-tête HTTP n'accepte pas ces caractères : fetch échouerait avant tout envoi.
      setError("Le jeton contient des caractères non valides.");
      return;
    }
    setChecking(true);
    setError(null);
    try {
      await checkToken(token);
      setToken(token);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Jeton refusé par le serveur. Vérifiez la valeur de API_TOKEN.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Erreur inattendue lors de la vérification du jeton.");
      }
      setChecking(false);
    }
  }

  return (
    <main className="token-screen">
      <form className="card token-card" onSubmit={onSubmit} noValidate>
        <h1>Suivi des offres</h1>
        <p>
          Saisissez le jeton d'accès du service (variable <code>API_TOKEN</code>). Il est vérifié puis
          mémorisé dans ce navigateur.
        </p>
        {notice ? <p className="notice">{notice}</p> : null}
        <label htmlFor={inputId}>Jeton d'accès</label>
        <input
          id={inputId}
          type="password"
          autoComplete="current-password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          disabled={checking}
          autoFocus
        />
        {error ? (
          <p id={errorId} className="error" role="alert">
            {error}
          </p>
        ) : null}
        <button type="submit" className="button primary" disabled={checking}>
          {checking ? "Vérification…" : "Se connecter"}
        </button>
      </form>
    </main>
  );
}
