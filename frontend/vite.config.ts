import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { cloudflare } from '@cloudflare/vite-plugin'
import { sites } from '@openai/sites-vite-plugin'
import path from 'path'

// Docker 内代理目标需指向 backend service, 本机直跑则用 localhost
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig(({ command }) => ({
  // Cloudflare/Sites plugins require the workerd binary and are only needed
  // for the production artifact. Keeping them out of `vite` dev mode avoids
  // crashing the local Docker development server on unsupported hosts.
  plugins: [react(), ...(command === 'build' ? [sites(), cloudflare({ configPath: './wrangler.json' })] : [])],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': apiTarget }
  }
}))
