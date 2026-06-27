// Vitest global setup: adds jest-dom matchers (toBeInTheDocument, etc.) and
// resets the DOM between tests.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
