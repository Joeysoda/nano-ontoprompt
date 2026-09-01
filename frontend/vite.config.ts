import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

// Docker 内代理目标需指向 backend service, 本机直跑则用 localhost
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig(async ({ command }) => {
  // Cloudflare/Sites plugins require the workerd binary and are only needed
  // for the production artifact. Keeping them out of `vite` dev mode avoids
  // crashing the local Docker development server on unsupported hosts.
  const plugins: any[] = [react()]
  if (command === 'build' && fs.existsSync(path.resolve(__dirname, './wrangler.json'))) {
    // These hosting integrations are optional in a local checkout. If their
    // packages are installed, retain the production integration; otherwise a
    // normal Vite build should still validate and bundle the application.
    try {
      // @ts-ignore optional package, supplied only by the Sites runtime
      const { sites } = await import('@openai/sites-vite-plugin')
      // @ts-ignore optional package, supplied only by the Sites runtime
      const { cloudflare } = await import('@cloudflare/vite-plugin')
      plugins.push(sites(), cloudflare({ configPath: './wrangler.json' }))
    } catch {
      console.warn('Optional Sites/Cloudflare Vite plugins are not installed; building the local web bundle.')
    }
  }
  return {
    plugins,
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    server: { host: true, port: 5173, proxy: { '/api': apiTarget } }
  }
})
