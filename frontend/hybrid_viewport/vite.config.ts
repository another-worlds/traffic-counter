import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// Viewport-only build — outputs to dist/
// The uploader has its own config (vite.uploader.config.ts) so each bundle
// gets its own self-contained dist tree (assets sibling to index.html),
// which is required for Streamlit's component static-file server.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, 'index.html'),
    },
  },
});
