// Fenêtre de dialogue dans la page (pas de window.confirm) : focus placé dans la fenêtre à
// l'ouverture, Tab et Maj+Tab restent à l'intérieur, Échap ferme, le focus revient à l'élément
// qui l'avait avant l'ouverture. Les dialogues peuvent s'imbriquer (confirmation dans le panneau) :
// le plus intérieur traite la touche et l'empêche de remonter.

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
}

export interface DialogProps {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** « panel » : panneau latéral (plein écran sur mobile) ; « modal » : fenêtre centrée. */
  variant?: "panel" | "modal";
  /** alertdialog pour une confirmation. */
  role?: "dialog" | "alertdialog";
  className?: string;
  /** Élément à focaliser si celui d'avant l'ouverture a disparu (ex. ligne supprimée). */
  fallbackFocus?: () => HTMLElement | null;
}

export function Dialog({
  title,
  onClose,
  children,
  variant = "modal",
  role = "dialog",
  className,
  fallbackFocus,
}: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // Élément actif à l'ouverture (premier rendu seulement).
  const [opener] = useState<Element | null>(() => document.activeElement);
  const fallback = useRef(fallbackFocus);
  useEffect(() => {
    fallback.current = fallbackFocus;
  });

  useEffect(() => {
    const root = ref.current;
    if (root) {
      const preferred = root.querySelector<HTMLElement>("[data-autofocus]");
      (preferred ?? focusables(root)[0] ?? root).focus();
    }
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
      else fallback.current?.()?.focus();
    };
  }, [opener]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !ref.current) return;
    e.stopPropagation();
    const items = focusables(ref.current);
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === ref.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div className={`dialog-backdrop dialog-backdrop-${variant}`} onMouseDown={(e) => {
        // Clic hors du panneau : il se ferme ; une fenêtre (formulaire, confirmation) reste ouverte.
        if (variant === "panel" && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`dialog dialog-${variant}${className ? ` ${className}` : ""}`}
        onKeyDown={onKeyDown}
      >
        <div className="dialog-head">
          <h2 id={titleId} className="dialog-title">
            {title}
          </h2>
          <button type="button" className="button ghost dialog-close" onClick={onClose} aria-label="Fermer">
            ×
          </button>
        </div>
        <div className="dialog-body">{children}</div>
      </div>
    </div>
  );
}
