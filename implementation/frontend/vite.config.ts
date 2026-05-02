/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api → backend so the frontend can use relative URLs
// (UI-SPEC.md: no router; one origin).
//
// Target is env-driven so the same config works in two contexts:
//   • inside docker-compose, BACKEND_URL=http://backend:8000 (service DNS),
//   • host-side `npm run dev`, default falls back to http://localhost:8000.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // tsc -b in `npm run build` emits compiled .js next to .tsx — restrict
    // test discovery to TypeScript sources so we don't double-run.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
