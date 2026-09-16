import { defineConfig } from "vite";

// Local-only dashboard. The dev server binds to 127.0.0.1 and proxies API calls to the
// local backend, so the browser only ever talks to this machine.
export default defineConfig({
  base: "/dashboard/",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
    fs: { allow: [".."] }, // sample_data/ lives one level up
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.js"],
  },
});
