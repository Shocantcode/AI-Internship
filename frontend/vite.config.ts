import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/health': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/mcp': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/api/forecast': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/api/datasets': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
})
