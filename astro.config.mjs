import { defineConfig } from "astro/config";

// No `site` or `base` configured. The production deploy serves Pulse from
// the domain root behind nginx (see deploy/roles/nginx-site/), and the local
// dev server runs at http://localhost:4321/. If a canonical production URL
// is finalized later (pulse.axiolo.com is under consideration), set
// `site: "https://<the-domain>"` here so Astro can build absolute URLs into
// sitemap.xml and Open Graph tags.
export default defineConfig({
  output: "static",
  trailingSlash: "ignore",
});
