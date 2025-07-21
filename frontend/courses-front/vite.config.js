import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => {
          if (path.startsWith('/api/admin')) {
            return path;
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
