import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        // Main viewport: dist/index.html
        viewport: resolve(__dirname, 'index.html'),
        // Uploader: dist/uploader/index.html — Streamlit bridge points to dist/uploader/
        uploader: resolve(__dirname, 'uploader/index.html'),
      },
    },
  },
});
