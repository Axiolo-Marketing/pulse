import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

// No `site` or `base` configured. The production deploy serves Pulse from
// the domain root behind nginx (see deploy/roles/nginx-site/), and the local
// dev server runs at http://localhost:4321/. If a canonical production URL
// is finalized later (pulse.axiolo.com is under consideration), set
// `site: "https://<the-domain>"` here so Astro can build absolute URLs into
// sitemap.xml and Open Graph tags.
//
// React + Tailwind v4 power the v2 UI (see ~/.claude/plans/yes-i-want-to-kind-pearl.md).
// Output stays `static`: v2 ships as React islands (`client:only="react"`) hydrated
// on the client, so nginx keeps serving pre-built files with no Node runtime.
export default defineConfig({
  output: "static",
  trailingSlash: "ignore",
  // Astro 7 changed the `compressHTML` default from `true` to `'jsx'`, which
  // strips whitespace (incl. newlines) between text and an adjacent inline
  // element. Our legal pages (terms/privacy) are authored as plain HTML with
  // inline `<a>` links wrapped onto their own lines, so the new default would
  // run words into links (e.g. "promptly at<a>info@axiolo.com"). `true`
  // restores the pre-7 single-space collapse that this prose relies on.
  // (React/JSX islands are unaffected by `'jsx'`, but `true` is correct for our
  // HTML-authored `.astro` prose.)
  compressHTML: true,
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
  },
});
