import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev: the Python engine serves /api on 8770; Vite proxies to it (SSE included).
// Build: `vite build` → web/dist, which the Python server serves at /.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8770', changeOrigin: true, ws: false },
      '/runs': { target: 'http://127.0.0.1:8770', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
