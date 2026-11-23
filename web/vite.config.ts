import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the RAGOps API at /ui (see ragops/server/app.py). `npm run dev` proxies API calls to :8000.
export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  server: { proxy: { "/v1": "http://localhost:8000", "/health": "http://localhost:8000" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 900 },
});
