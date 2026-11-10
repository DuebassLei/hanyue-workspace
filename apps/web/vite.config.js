import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // tiptap 核心单独切块，避免污染主入口
          'vendor-tiptap': [
            '@tiptap/vue-3',
            '@tiptap/core',
            '@tiptap/starter-kit',
            '@tiptap/extension-highlight',
            '@tiptap/extension-image',
            '@tiptap/extension-table',
            '@tiptap/extension-table-row',
            '@tiptap/extension-table-cell',
            '@tiptap/extension-table-header',
            '@tiptap/extension-bubble-menu',
            '@tiptap/extension-placeholder',
            'prosemirror-markdown',
            'prosemirror-model',
            'prosemirror-state',
            'prosemirror-view',
            'prosemirror-transform',
          ],
        },
      },
    },
  },
})
