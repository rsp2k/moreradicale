// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

// https://astro.build/config
export default defineConfig({
  // Build static files into the Python package's web data directory.
  // After `npm run build`, the Python wheel can ship the new UI.
  outDir: "../moreradicale/web/internal_data",
  // Don't emit a build manifest into the package
  build: {
    inlineStylesheets: "auto",
    assets: "_assets",
  },
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
  },
  // Per global rules
  telemetry: false,
  devToolbar: { enabled: false },
  // Astro served by Python; no base path
  base: "/.web/",
  trailingSlash: "always",
});
