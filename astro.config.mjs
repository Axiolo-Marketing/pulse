import { defineConfig } from "astro/config";

// GitHub Pages deploys at https://tomdigati.github.io/pulse/
// site + base must match for asset paths to resolve correctly.
export default defineConfig({
  site: "https://tomdigati.github.io",
  base: "/pulse",
  output: "static",
  trailingSlash: "ignore",
});
