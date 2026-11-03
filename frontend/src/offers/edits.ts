/**
 * Suivi des modifications en ligne concurrentes (plusieurs PATCH en vol sur une même offre).
 *
 * Chaque modification reçoit un numéro de séquence croissant. Pour chaque champ on garde :
 * - la dernière valeur confirmée par le service (et le numéro de la requête qui l'a confirmée) ;
 * - les modifications encore en vol (valeur optimiste).
 * La valeur affichée d'un champ est celle de plus grand numéro parmi la valeur confirmée et les
 * modifications en vol : une réponse ancienne n'écrase jamais une modification plus récente, et un
 * échec ne revient en arrière que jusqu'à la dernière valeur encore valable.
 */
import type { Offer } from "../api/types";

type Field = keyof Offer;
export type Updater = (offer: Offer) => Offer;

interface Entry {
  seq: number;
  value: unknown;
}

interface FieldState {
  confirmed: Entry;
  pending: Entry[];
}

interface OfferState {
  fields: Map<Field, FieldState>;
  /** Numéro de la réponse complète la plus récente appliquée à l'offre. */
  appliedSeq: number;
}

export class EditTracker {
  private seq = 0;
  private offers = new Map<number, OfferState>();

  private offerState(id: number): OfferState {
    let state = this.offers.get(id);
    if (!state) {
      state = { fields: new Map(), appliedSeq: 0 };
      this.offers.set(id, state);
    }
    return state;
  }

  private displayed(field: FieldState): unknown {
    let best = field.confirmed;
    for (const entry of field.pending) if (entry.seq > best.seq) best = entry;
    return best.value;
  }

  /**
   * Oublie les champs touchés qui n'ont plus de modification en vol, puis l'offre si plus aucun
   * champ n'est suivi : une valeur confirmée ne doit pas survivre à un rechargement (scénarios K, L).
   */
  private cleanup(id: number, state: OfferState, touched: readonly (readonly [Field, FieldState | undefined])[]) {
    for (const [name, field] of touched) if (field && field.pending.length === 0) state.fields.delete(name);
    if (state.fields.size === 0) this.offers.delete(id);
  }

  /** Valeurs affichées des champs suivis d'une offre (instantané). */
  private overrides(state: OfferState): Partial<Offer> {
    const values: Record<string, unknown> = {};
    for (const [name, field] of state.fields) values[name] = this.displayed(field);
    return values as Partial<Offer>;
  }

  /**
   * Démarre une modification d'un champ. Renvoie son numéro et la mise à jour (pure, rejouable)
   * à appliquer à l'offre affichée.
   */
  start<K extends Field>(offer: Offer, name: K, value: Offer[K]): { seq: number; apply: Updater } {
    return this.startMany(offer, { [name]: value } as Partial<Offer>);
  }

  /**
   * Démarre une modification de plusieurs champs envoyés dans un même PATCH (formulaire de
   * modification) : un seul numéro pour tous les champs, chacun suivi comme une modification en
   * ligne. Les champs absents de `changes` ne sont pas touchés : une modification en ligne encore
   * en vol sur un autre champ garde sa valeur.
   */
  startMany(offer: Offer, changes: Partial<Offer>): { seq: number; apply: Updater } {
    const seq = ++this.seq;
    const state = this.offerState(offer.id);
    for (const name of Object.keys(changes) as Field[]) {
      let field = state.fields.get(name);
      if (!field) {
        field = { confirmed: { seq: 0, value: offer[name] }, pending: [] };
        state.fields.set(name, field);
      }
      field.pending.push({ seq, value: changes[name] });
    }
    const values = this.overrides(state);
    return { seq, apply: (current) => ({ ...current, ...values }) };
  }

  /** Réponse du service : fusionne sans écraser les modifications plus récentes ou encore en vol. */
  succeed(id: number, name: Field, seq: number, saved: Offer): Updater {
    return this.succeedMany(id, [name], seq, saved);
  }

  /** Réponse du service à une modification de plusieurs champs (même numéro). */
  succeedMany(id: number, names: readonly Field[], seq: number, saved: Offer): Updater {
    const state = this.offerState(id);
    const touched: [Field, FieldState | undefined][] = names.map((name) => [name, state.fields.get(name)]);
    for (const [name, field] of touched) {
      if (!field) continue;
      field.pending = field.pending.filter((e) => e.seq !== seq);
      if (seq > field.confirmed.seq) field.confirmed = { seq, value: saved[name] };
    }
    // Réponse la plus récente reçue : l'état du service fait foi, sauf champs encore modifiés.
    // Réponse dépassée (une réponse plus récente est déjà affichée) : seuls les champs suivis bougent.
    const fresh = seq > state.appliedSeq;
    if (fresh) state.appliedSeq = seq;
    const values = this.overrides(state);
    this.cleanup(id, state, touched);
    return (current) => ({ ...(fresh ? saved : current), ...values });
  }

  /**
   * Échec : retire la modification ; l'affichage revient à la dernière valeur encore valable
   * (valeur confirmée ou modification plus récente).
   */
  fail(id: number, name: Field, seq: number): Updater {
    return this.failMany(id, [name], seq);
  }

  /** Échec d'une modification de plusieurs champs : chaque champ revient comme pour fail(). */
  failMany(id: number, names: readonly Field[], seq: number): Updater {
    const state = this.offers.get(id);
    if (!state) return (current) => current;
    const values: Record<string, unknown> = {};
    const touched: [Field, FieldState | undefined][] = names.map((name) => [name, state.fields.get(name)]);
    for (const [name, field] of touched) {
      if (!field) continue;
      field.pending = field.pending.filter((e) => e.seq !== seq);
      values[name] = this.displayed(field);
    }
    this.cleanup(id, state, touched);
    return (current) => ({ ...current, ...(values as Partial<Offer>) });
  }

  /** Vrai si aucune modification plus récente de ce champ n'a été lancée depuis `seq`. */
  isLatest(id: number, name: Field, seq: number): boolean {
    const field = this.offers.get(id)?.fields.get(name);
    if (!field) return true;
    return field.confirmed.seq <= seq && field.pending.every((e) => e.seq <= seq);
  }
}
