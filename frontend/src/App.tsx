import { clearToken, getLastClearReason } from "./api/token";
import { TokenScreen } from "./TokenScreen";
import { useHashView, viewHref, type View } from "./useHashView";
import { useToken } from "./useToken";
import { OfferFiltersProvider } from "./offers/filters";
import { OffersView } from "./offers/OffersView";
import { SyntheseView } from "./synthese/SyntheseView";

const NAV: { view: View; label: string }[] = [
  { view: "tableau", label: "Tableau" },
  { view: "synthese", label: "Synthèse" },
];

const MAIN_ID = "contenu";

const NOTICES = {
  logout: "Vous êtes déconnecté.",
  unauthorized: "Le serveur a refusé le jeton mémorisé : saisissez-le à nouveau.",
} as const;

export function App() {
  const token = useToken();
  const view = useHashView();

  if (token === null) {
    const reason = getLastClearReason();
    return <TokenScreen notice={reason ? NOTICES[reason] : undefined} />;
  }

  // Filtres du tableau au niveau de l'application : la Synthèse (carte) peut les régler.
  return (
    <OfferFiltersProvider>
      <div className="app">
        {/* Lien d'évitement : le fragment sert à la navigation (#/tableau), d'où le focus posé à la main. */}
        <a
          className="skip-link"
          href={`#${MAIN_ID}`}
          onClick={(e) => {
            e.preventDefault();
            document.getElementById(MAIN_ID)?.focus();
          }}
        >
          Aller au contenu principal
        </a>
        <header className="app-header">
          <h1 className="brand">Suivi des offres</h1>
          <nav aria-label="Navigation principale" className="main-nav">
            {NAV.map((item) => (
              <a
                key={item.view}
                href={viewHref(item.view)}
                className="nav-link"
                aria-current={view === item.view ? "page" : undefined}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <button type="button" className="button ghost" onClick={() => clearToken("logout")}>
            Se déconnecter
          </button>
        </header>
        <main className="app-main" id={MAIN_ID} tabIndex={-1}>
          {view === "synthese" ? <SyntheseView /> : <OffersView />}
        </main>
      </div>
    </OfferFiltersProvider>
  );
}
