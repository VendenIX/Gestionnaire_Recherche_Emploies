import type { Offer } from "../api/types";

/** Offre complète pour les tests ; les champs donnés remplacent les valeurs par défaut. */
export function makeOffer(fields: Partial<Offer> & { id: number }): Offer {
  return {
    title: `Offre ${fields.id}`,
    contract_type: null,
    company: null,
    location: null,
    department: null,
    country: "FR",
    region: null,
    status: "À postuler",
    main_issue: null,
    rejection_reason: null,
    source: null,
    applied_at: null,
    application_sent: false,
    follow_up_done: false,
    deadline: null,
    deadline_note: null,
    description: null,
    links: [],
    created_at: "2026-10-01T10:00:00",
    updated_at: "2026-10-01T10:00:00",
    days_left: null,
    follow_up_suggested: false,
    ...fields,
  };
}
