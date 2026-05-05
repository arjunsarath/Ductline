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
  // Resolve TypeScript sources before .js so any stray emit next to a
  // .tsx (e.g. from a pre-noEmit ``tsc -b`` run) doesn't shadow the real
  // source the dev server should serve. ``tsconfig.json`` has ``noEmit:true``,
  // so this is belt-and-braces against future regressions.
  resolve: {
    extensions: [".mts", ".ts", ".tsx", ".mjs", ".js", ".jsx", ".json"],
  },
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
    // Restrict test discovery to TypeScript sources — same belt-and-braces
    // intent as ``resolve.extensions`` above.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
