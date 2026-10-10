/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: "../backend/qrm_hunter/web", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:7070",
      "/ws": { target: "ws://127.0.0.1:7070", ws: true },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
