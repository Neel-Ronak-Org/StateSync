import path from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  build: {
    // The production bundle must run under a CSP with no 'unsafe-eval'
    // (nginx.conf). Vite's output does not use eval; source maps stay off so the
    // served bundle is exactly what the CSP checks.
    sourcemap: false,
  },
})
