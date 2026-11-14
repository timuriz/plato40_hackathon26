import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: { '/api': 'http://127.0.0.1:8090' },
  },
  build: {
    outDir: 'dist', emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/three/')) return 'three-core';
          if (/node_modules\/(?:@react-three|@react-spring|@use-gesture|three-stdlib|three-mesh-bvh|troika-|camera-controls|maath|meshline|suspend-react|zustand|tunnel-rat|glsl-noise)/.test(id)) return 'r3f-vendor';
        },
      },
    },
  },
});
