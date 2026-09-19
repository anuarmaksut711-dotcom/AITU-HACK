import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import type { ClientRequest, IncomingMessage } from 'node:http'

// Compose exposes Aimeet through web; port 8000 belongs to the container network.
const apiTarget = process.env.AIMEET_API_TARGET ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/livekit': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
        configure(proxy) {
          const forwardLocalOrigin = (proxyReq: ClientRequest, req: IncomingMessage) => {
            const origin = req.headers.origin
            // Translate only requests from this local dev server. Preserve foreign
            // origins and cross-site metadata so the API can still reject them.
            if (origin === `http://${req.headers.host}` &&
                /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin) &&
                req.headers['sec-fetch-site'] !== 'cross-site') {
              proxyReq.setHeader('Origin', new URL(apiTarget).origin)
            }
          }
          proxy.on('proxyReq', forwardLocalOrigin)
          proxy.on('proxyReqWs', forwardLocalOrigin)
        },
      },
    },
  },
  build: { sourcemap: false },
})
