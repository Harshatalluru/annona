import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

// UI is served by the runner's FastAPI on http://127.0.0.1:7070.
// `npm run dev` is for local frontend iteration only.
export default defineConfig({
  plugins: [react()],
  // The window's own version, from package.json — never typed by hand again.
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  build: {
    outDir: "dist",
  },
  server: {
    port: 5173,
  },
});
