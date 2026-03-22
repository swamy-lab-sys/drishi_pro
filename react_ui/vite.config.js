import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Flask backend runs on 8000; Vite proxies all /api and /ws requests.
// This means no CORS needed — same-origin from browser perspective.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Use relative base in production so assets resolve correctly from /react/
  // In dev, base stays '/' so localhost:5173/ works normally
  base: command === 'build' ? './' : '/',
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    // Production build goes into Flask's static folder so Flask can serve it
    outDir: '../web/static/react',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Deterministic filenames for Flask template tags
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
}))
