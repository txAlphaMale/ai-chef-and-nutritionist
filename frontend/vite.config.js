import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL || "http://localhost:8095",
        changeOrigin: true,
      },
      // /health lives outside /api (see backend/app/main.py) but needs
      // the same dev-time proxy -- see src/api.js's backendOrigin.
      "/health": {
        target: process.env.VITE_BACKEND_URL || "http://localhost:8095",
        changeOrigin: true,
      },
    },
  },
});
