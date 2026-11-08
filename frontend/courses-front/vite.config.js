import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8080,
    proxy: {
      '/courses': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      '/admin': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      '/join': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      '/j': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => {
          if (path.startsWith('/api/v1/admin') || path.startsWith('/api/v1/courses')) {
            return path.replace(/^\/api\/v1/, '');
          }
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
