import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend is on-premise and binds to localhost; the dev server proxies to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.NUMENOR_API ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
