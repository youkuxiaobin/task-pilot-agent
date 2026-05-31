import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/agent/web/',
  plugins: [vue()],
  server: {
    proxy: {
      '/agent': 'http://127.0.0.1:9010',
      '/file': 'http://127.0.0.1:9010',
      '/aggre_mcp_market': 'http://127.0.0.1:9010',
    },
  },
})
