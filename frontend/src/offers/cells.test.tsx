import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EditableCell } from "./cells";

/**
 * Entrée (ou Échap) puis perte de focus dans le même tour d'événements : le champ n'est pas
 * encore retiré quand le blur arrive, la garde doit empêcher un second enregistrement.
 */
function keyThenBlur(input: HTMLElement, key: string) {
  act(() => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    input.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    input.dispatchEvent(new FocusEvent("blur"));
  });
}

async function openEditor(onSave: (v: string | null) => void) {
  const user = userEvent.setup();
  render(<EditableCell value="Thales" label="Entreprise" onSave={onSave} />);
  await user.click(screen.getByRole("button", { name: "Modifier : Entreprise" }));
  const input = screen.getByLabelText("Entreprise");
  await user.clear(input);
  await user.type(input, "Acme");
  return input;
}

describe("EditableCell : un seul enregistrement par édition", () => {
  it("Entrée puis perte de focus -> exactement 1 enregistrement", async () => {
    const onSave = vi.fn();
    const input = await openEditor(onSave);
    keyThenBlur(input, "Enter");
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith("Acme");
    expect(screen.getByRole("button", { name: "Modifier : Entreprise" })).toBeInTheDocument();
  });

  it("Échap puis perte de focus -> aucun enregistrement", async () => {
    const onSave = vi.fn();
    const input = await openEditor(onSave);
    keyThenBlur(input, "Escape");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("perte de focus seule -> 1 enregistrement", async () => {
    const onSave = vi.fn();
    const user = userEvent.setup();
    await openEditor(onSave);
    await user.tab();
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
