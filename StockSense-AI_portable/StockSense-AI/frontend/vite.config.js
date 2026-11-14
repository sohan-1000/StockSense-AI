import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the FastAPI backend so the browser never needs CORS in development.
// Default is port 8000; set VITE_API_TARGET (e.g. http://127.0.0.1:8001) if the backend runs elsewhere.
const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": apiTarget } },
});
