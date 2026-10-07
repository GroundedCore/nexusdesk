import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// This config runs in Node, but tsconfig intentionally exposes only the browser
// types, so `process` is declared locally instead of pulling in @types/node and
// leaking Node globals into src/.
declare const process: { env: Record<string, string | undefined> };

// Port 8000 is frequently taken on developer machines (for example by the
// C-Lodop print control on Windows), so the backend target is overridable.
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': apiTarget, '/openapi': apiTarget },
  },
});
