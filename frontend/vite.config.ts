import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies /api to the FastAPI backend so the browser makes
// same-origin requests (no CORS dance during development).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    // jsdom for the App-level component tests (ADR-0028); the pure lib/ tests don't
    // depend on the environment.
    environment: "jsdom",
    globals: true,
    clearMocks: true,
    setupFiles: ["src/test/setup.ts"],
  },
});
