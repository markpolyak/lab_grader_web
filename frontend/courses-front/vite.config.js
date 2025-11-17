import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => {
          // Убираем /api/v1 для admin маршрутов (как делает Caddy в production)
          if (path.startsWith('/api/v1/admin')) {
            return path.replace(/^\/api\/v1/, '');
          }
          // Убираем /api для остальных маршрутов
          if (
            path.startsWith('/api/courses') ||
            path.startsWith('/api/groups') ||
            path.startsWith('/api/labs')
          ) {
            return path.replace(/^\/api/, '');
          }
          return path;
        },
      },
    },
  },
});
