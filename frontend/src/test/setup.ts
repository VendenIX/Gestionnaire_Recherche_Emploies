import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { resetTokenStateForTests } from "../api/token";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  resetTokenStateForTests();
  window.location.hash = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
