import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, API calls go to the FastAPI backend; in prod FastAPI serves this build.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
