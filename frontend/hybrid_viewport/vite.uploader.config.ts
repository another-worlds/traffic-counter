import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// Uploader-only build — outputs to dist/uploader/
// Setting root to the uploader/ directory means Vite treats index.html there
// as the entry and emits it to outDir/index.html (not outDir/uploader/index.html).
// Streamlit declares_component(path="dist/uploader/") serves files from that root;
// with base: './' all asset hrefs become ./assets/*.js — within the served path.
export default defineConfig({
  plugins: [react()],
  root: resolve(__dirname, 'uploader'),
  base: './',
  build: {
    outDir: resolve(__dirname, 'dist/uploader'),
    emptyOutDir: true,
  },
});
