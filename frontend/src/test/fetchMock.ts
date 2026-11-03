import { vi } from "vitest";

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Remplace fetch par un mock qui renvoie les réponses données, dans l'ordre. */
export function mockFetch(...responses: Response[]) {
  const fn = vi.fn<typeof fetch>();
  for (const r of responses) fn.mockResolvedValueOnce(r);
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function headerOf(fn: ReturnType<typeof mockFetch>, call: number, name: string): string | null {
  const init = fn.mock.calls[call]?.[1];
  return new Headers(init?.headers).get(name);
}

export const META = {
  statuses: ["À postuler", "En cours", "Entretien", "Offre reçue", "Rejeté", "Abandon"],
  status_unset_label: "À qualifier",
  regions: ["Corse"],
  out_of_france_label: "Hors France",
  no_department_label: "Sans département",
  departments: [{ code: "2A", name: "Corse-du-Sud", region: "Corse" }],
  follow_up_days: 7,
};
